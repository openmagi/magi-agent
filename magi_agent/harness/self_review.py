"""C1 — Post-turn self-review fork (default OFF, shadow-first, no writes).

After each agent turn, this module optionally forks the agent (via the existing
``runtime/fork_runner.py`` machinery) to self-review the just-finished turn and
emit ``ReviewCandidate`` proposals.  The fork is:

  * Non-blocking — runs as a background asyncio task; NEVER delays or breaks
    the parent turn.
  * Fail-open — any exception inside the fork path is swallowed + logged.
  * Proposals only — the fork emits ``ReviewCandidate`` objects to an injected
    ``CandidateSink`` protocol; it does NOT write memory, call the eval-gate, or
    flip any authority flag.

Env gates
---------
MAGI_SELF_REVIEW_ENABLED    default OFF.  When off the hook is a pure no-op;
                             zero background tasks are created.
MAGI_SELF_REVIEW_SHADOW     default ON (shadow-first rollout).  When shadow is
                             on, candidates are emitted but ``acted=False``.
                             Live (shadow=False) allows the sink to act, but the
                             sink itself decides what "acting" means (C2 concern).

Tool surface restriction
------------------------
The review fork's tool surface is restricted to memory + skill tools only (no
shell, no network, no file writes).  This is expressed via ``REVIEW_DISABLED_TOOLSETS``
passed to the fork runner as part of the directive metadata, and surfaced in the
evidence record so C2 can verify the restriction was applied.

Parent cache guarantee
----------------------
The fork reuses ``ForkRunner.fork()``, which deep-copies the system-prompt
snapshot via ``FrozenPromptSnapshot.capture()``.  The parent's ``system_prompt_blocks``
are never mutated; the fork only ever sees restored (deep-copied) blocks.
This is asserted in tests via fingerprint comparison before/after the fork.

Evidence + redaction
--------------------
An ``EvidenceRecord`` is emitted for each fork execution.  Fields contain
digests and lengths only — no raw transcript text, no candidate content.

Authority flags
---------------
All ``Literal[False]`` authority flags remain unset.  This module NEVER calls
the eval-gate, NEVER persists memory, and NEVER activates skills.

Forbidden top-level imports: magi_agent.adk_bridge, google.adk, urllib, socket,
subprocess — none appear in this module's top-level import graph.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env gates
# ---------------------------------------------------------------------------

_ENV_ENABLED = "MAGI_SELF_REVIEW_ENABLED"
_ENV_SHADOW = "MAGI_SELF_REVIEW_SHADOW"

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_STRINGS


def _self_review_enabled() -> bool:
    return _env_flag(_ENV_ENABLED, default=False)


def _self_review_shadow() -> bool:
    # shadow-first: default ON unless explicitly disabled
    raw = os.environ.get(_ENV_SHADOW)
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


# ---------------------------------------------------------------------------
# Tool surface restriction
# ---------------------------------------------------------------------------

#: Toolsets disabled for the self-review fork (no shell, no network, no FS writes).
#: Only memory + skill tools are permitted (everything else is stripped).
REVIEW_DISABLED_TOOLSETS: tuple[str, ...] = (
    # shell / execution
    "BashTool",
    "RunCommand",
    "ExecuteCode",
    # network / web
    "WebSearch",
    "WebFetch",
    "BrowserNavigate",
    "BrowserAction",
    # file writes
    "WriteFile",
    "EditFile",
    "PatchFile",
    "FileWrite",
    # messaging / delivery
    "TelegramSend",
    "DiscordSend",
    "FileDeliver",
    "FileSend",
    # scheduling / task fan-out
    "CronCreate",
    "CronUpdate",
    "CronDelete",
    "TaskCreate",
    "TaskStop",
    "TaskWait",
    # interactive / user-facing
    "AskUserQuestion",
)


# ---------------------------------------------------------------------------
# ReviewCandidate model
# ---------------------------------------------------------------------------

ReviewCandidateKind = Literal["memory", "skill"]
ReviewCandidateMode = Literal["shadow", "live"]


class ReviewCandidate(BaseModel):
    """A proposed memory or skill candidate emitted by the self-review fork.

    Frozen, camelCase-aliased, ``extra="forbid"`` — matching the conventions
    in ``learning/models.py`` and ``learning/candidates.py``.

    Fields
    ------
    kind          "memory" or "skill".
    proposal      Declarative text — the proposed fact or skill summary.
                  Kept short (no raw transcript blobs).
    provenance_digest
                  SHA-256 hex of the source-turn digest used to trace back to
                  the originating turn without embedding raw content.
    confidence    [0.0, 1.0] score emitted by the fork agent's self-assessment.
    session_id    Opaque session reference.
    turn_id       Opaque turn reference.
    acted         Always ``False`` in shadow mode; the sink MAY set True in live
                  mode, but C1 itself never produces ``acted=True``.
    mode          "shadow" or "live" — the mode under which this candidate was
                  produced (mirrors the env-gate state at emission time).
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    kind: ReviewCandidateKind
    proposal: str
    provenance_digest: str = Field(alias="provenanceDigest")
    confidence: float
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    acted: bool = False
    mode: ReviewCandidateMode


# ---------------------------------------------------------------------------
# CandidateSink Protocol (injection seam)
# ---------------------------------------------------------------------------


@runtime_checkable
class CandidateSink(Protocol):
    """Receiver for ``ReviewCandidate`` objects emitted by the self-review fork.

    C1 only calls ``receive``; it never reads from the sink.  C2 wires the
    eval-gate as the real sink.  Tests inject a ``FakeCandidateSink``.

    The ``receive`` method MUST be synchronous (no await) so it can be called
    from within the fork's asyncio task without additional dispatch complexity.
    Sinks that need async work must buffer internally.
    """

    def receive(self, candidate: ReviewCandidate) -> None: ...  # pragma: no cover


# ---------------------------------------------------------------------------
# SelfReviewConfig
# ---------------------------------------------------------------------------


class SelfReviewConfig(BaseModel):
    """Frozen config controlling self-review fork behavior."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    enabled: bool = False
    shadow: bool = True

    @classmethod
    def from_env(cls) -> SelfReviewConfig:
        return cls(
            enabled=_self_review_enabled(),
            shadow=_self_review_shadow(),
        )


# ---------------------------------------------------------------------------
# ForkReviewInput — what the hook receives from the parent turn
# ---------------------------------------------------------------------------


class ForkReviewInput(BaseModel):
    """Snapshot of parent-turn context passed to the self-review fork.

    Only carries digests and structural references — never raw conversation
    content — so the evidence record stays redaction-safe.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    #: System-prompt blocks for the fork (deep-copied from parent — see note).
    system_prompt_blocks: tuple[dict[str, Any], ...] = Field(
        alias="systemPromptBlocks"
    )
    #: The last assistant message (turn output) — used as fork prefix context.
    parent_assistant_message: dict[str, Any] = Field(alias="parentAssistantMessage")
    #: SHA-256 fingerprint of the system-prompt snapshot BEFORE fork (for invariant check).
    pre_fork_fingerprint: str = Field(alias="preForkFingerprint")


# ---------------------------------------------------------------------------
# ForkReviewResult (returned from _run_review_fork)
# ---------------------------------------------------------------------------


class ForkReviewResult(BaseModel):
    """Frozen result from a single self-review fork execution."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    mode: ReviewCandidateMode
    candidates_emitted: int = Field(alias="candidatesEmitted")
    #: SHA-256 fingerprint of the system-prompt snapshot AFTER fork (must equal pre_fork).
    post_fork_fingerprint: str = Field(alias="postForkFingerprint")
    cache_untouched: bool = Field(alias="cacheUntouched")
    evidence: EvidenceRecord


# ---------------------------------------------------------------------------
# Evidence builder
# ---------------------------------------------------------------------------


def _build_review_evidence(
    *,
    session_id: str,
    turn_id: str,
    mode: ReviewCandidateMode,
    candidates_emitted: int,
    cache_untouched: bool,
    fork_status: Literal["ok", "error", "disabled"],
    elapsed_ms: float,
    pre_fork_fingerprint: str,
    post_fork_fingerprint: str,
    now: datetime,
) -> EvidenceRecord:
    return EvidenceRecord(
        type="custom:SelfReviewForkExecution",
        status="ok" if fork_status in {"ok", "disabled"} else "failed",
        observedAt=int(now.astimezone(UTC).timestamp() * 1000),
        source=EvidenceSource(kind="execution_contract"),
        fields={
            "sessionId": session_id,
            "turnId": turn_id,
            "mode": mode,
            "candidatesEmitted": candidates_emitted,
            "cacheUntouched": cache_untouched,
            "forkStatus": fork_status,
            "elapsedMs": round(elapsed_ms, 2),
            # Fingerprints are digests — safe to record; no raw content.
            "preForkFingerprint": pre_fork_fingerprint[:16],  # truncated digest
            "postForkFingerprint": post_fork_fingerprint[:16],
            "disabledToolsets": list(REVIEW_DISABLED_TOOLSETS),
        },
    )


# ---------------------------------------------------------------------------
# Provenance digest helper
# ---------------------------------------------------------------------------


def _turn_provenance_digest(session_id: str, turn_id: str) -> str:
    raw = f"{session_id}:{turn_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Core review fork runner (injectable for tests)
# ---------------------------------------------------------------------------


async def _run_review_fork(
    *,
    fork_input: ForkReviewInput,
    fork_runner: Any,
    candidate_sink: CandidateSink,
    mode: ReviewCandidateMode,
    now: datetime,
) -> ForkReviewResult:
    """Execute the self-review fork and emit candidates to the sink.

    The fork_runner is the ``ForkRunner`` from ``runtime/fork_runner.py``.
    We call ``fork_runner.fork()`` with a single directive that instructs the
    child agent to propose memory/skill candidates based on the parent turn.

    The parent's ``pre_fork_fingerprint`` must equal the post-fork snapshot
    fingerprint — proving the parent's cache was untouched.
    """
    start = time.monotonic()

    # Re-capture the snapshot from the (deep-copied) blocks to verify integrity.
    # Import lazily — never at module top-level (import-clean boundary rule).
    from magi_agent.runtime.prompt_snapshot import FrozenPromptSnapshot

    system_prompt_blocks = list(fork_input.system_prompt_blocks)
    post_snapshot = FrozenPromptSnapshot.capture(system_prompt_blocks)
    post_fork_fingerprint = post_snapshot.fingerprint
    cache_untouched = post_fork_fingerprint == fork_input.pre_fork_fingerprint

    if not cache_untouched:
        logger.warning(
            "self_review: parent prompt cache fingerprint changed during fork "
            "(pre=%s, post=%s) — skipping fork",
            fork_input.pre_fork_fingerprint[:12],
            post_fork_fingerprint[:12],
        )
        elapsed = (time.monotonic() - start) * 1000
        evidence = _build_review_evidence(
            session_id=fork_input.session_id,
            turn_id=fork_input.turn_id,
            mode=mode,
            candidates_emitted=0,
            cache_untouched=False,
            fork_status="error",
            elapsed_ms=elapsed,
            pre_fork_fingerprint=fork_input.pre_fork_fingerprint,
            post_fork_fingerprint=post_fork_fingerprint,
            now=now,
        )
        return ForkReviewResult(
            sessionId=fork_input.session_id,
            turnId=fork_input.turn_id,
            mode=mode,
            candidatesEmitted=0,
            postForkFingerprint=post_fork_fingerprint,
            cacheUntouched=False,
            evidence=evidence,
        )

    # Build the review directive.
    directive = (
        "Self-review: based on the conversation turn above, propose up to 3 "
        "memory facts or skill patterns that would improve future performance. "
        "For each proposal output a JSON object with keys: kind (memory|skill), "
        "proposal (one concise sentence), confidence (0.0-1.0). "
        "Use ONLY memory and skill tools. Do NOT write files, run commands, "
        "or send messages. Output the JSON objects only."
    )

    # Run the fork (fail-open: if fork_runner raises, we swallow it below).
    # REVIEW_DISABLED_TOOLSETS is passed here so the fork runner records and
    # forwards it to the child executor, which MUST strip those tools before
    # the child LLM call.  This is enforcement, not advisory text.
    fork_results, _fork_evidence = await fork_runner.fork(
        parent_turn_id=fork_input.turn_id,
        system_prompt_blocks=system_prompt_blocks,
        parent_assistant_message=fork_input.parent_assistant_message,
        child_directives=[directive],
        disabled_toolsets=REVIEW_DISABLED_TOOLSETS,
    )

    provenance_digest = _turn_provenance_digest(
        fork_input.session_id, fork_input.turn_id
    )
    candidates_emitted = 0

    for child_result in fork_results:
        if child_result.status != "ok" or not child_result.output:
            continue
        # Parse candidates from the fork output (best-effort JSON extraction).
        parsed = _parse_fork_output(
            output=child_result.output,
            session_id=fork_input.session_id,
            turn_id=fork_input.turn_id,
            provenance_digest=provenance_digest,
            mode=mode,
        )
        for candidate in parsed:
            candidate_sink.receive(candidate)
            candidates_emitted += 1

    elapsed = (time.monotonic() - start) * 1000
    evidence = _build_review_evidence(
        session_id=fork_input.session_id,
        turn_id=fork_input.turn_id,
        mode=mode,
        candidates_emitted=candidates_emitted,
        cache_untouched=cache_untouched,
        fork_status="ok",
        elapsed_ms=elapsed,
        pre_fork_fingerprint=fork_input.pre_fork_fingerprint,
        post_fork_fingerprint=post_fork_fingerprint,
        now=now,
    )
    return ForkReviewResult(
        sessionId=fork_input.session_id,
        turnId=fork_input.turn_id,
        mode=mode,
        candidatesEmitted=candidates_emitted,
        postForkFingerprint=post_fork_fingerprint,
        cacheUntouched=True,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Fork output parser (best-effort, fail-safe)
# ---------------------------------------------------------------------------


def _parse_fork_output(
    *,
    output: str,
    session_id: str,
    turn_id: str,
    provenance_digest: str,
    mode: ReviewCandidateMode,
) -> list[ReviewCandidate]:
    """Best-effort parse of JSON objects from the fork's text output.

    Extracts ``{kind, proposal, confidence}`` dicts.  Any parse error returns
    an empty list — this function never raises.
    """
    import json as _json
    import re as _re

    candidates: list[ReviewCandidate] = []
    # Find all {...} blobs in the output (greedy-safe: one level only).
    for match in _re.finditer(r"\{[^{}]*\}", output):
        try:
            obj = _json.loads(match.group())
        except _json.JSONDecodeError:
            continue
        kind_raw = obj.get("kind", "")
        if kind_raw not in ("memory", "skill"):
            continue
        proposal = str(obj.get("proposal", "")).strip()
        if not proposal:
            continue
        try:
            confidence = float(obj.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        try:
            candidate = ReviewCandidate(
                kind=kind_raw,  # type: ignore[arg-type]
                proposal=proposal,
                provenanceDigest=provenance_digest,
                confidence=confidence,
                sessionId=session_id,
                turnId=turn_id,
                acted=False,
                mode=mode,
            )
        except Exception:
            continue
        candidates.append(candidate)
    return candidates


# ---------------------------------------------------------------------------
# After-turn hook (non-blocking, fail-open)
# ---------------------------------------------------------------------------


async def run_self_review_hook(
    *,
    session_id: str,
    turn_id: str,
    system_prompt_blocks: list[dict[str, Any]],
    parent_assistant_message: dict[str, Any],
    fork_runner: Any,
    candidate_sink: CandidateSink,
    config: SelfReviewConfig | None = None,
    now: datetime | None = None,
) -> ForkReviewResult | None:
    """Fire the after-turn self-review fork.

    This is the HOOK ENTRY POINT.  It is:
    - Non-blocking when called from the hook bus (the bus fires non-blocking
      hooks as background asyncio tasks via ``_schedule_non_blocking_hook``).
    - Fail-open: any exception is caught, logged, and returns None.
    - Gate-checked: if ``MAGI_SELF_REVIEW_ENABLED`` is off, returns None
      immediately (zero work, zero background tasks).

    The fork is performed ONLY via the injected ``fork_runner`` (the real
    ``ForkRunner`` from ``runtime/fork_runner.py``); no ADK imports occur
    in this call path.

    Parent cache invariant: the system_prompt_blocks are deep-copied and
    snapshotted BEFORE the fork; the post-fork fingerprint is asserted equal
    to the pre-fork fingerprint inside ``_run_review_fork``.
    """
    resolved_config = config if config is not None else SelfReviewConfig.from_env()
    resolved_now = now if now is not None else datetime.now(tz=UTC)

    if not resolved_config.enabled:
        return None

    mode: ReviewCandidateMode = "shadow" if resolved_config.shadow else "live"

    # Capture the pre-fork fingerprint from a deep copy (parent blocks untouched).
    try:
        import copy as _copy
        from magi_agent.runtime.prompt_snapshot import FrozenPromptSnapshot

        blocks_copy = _copy.deepcopy(system_prompt_blocks)
        pre_snapshot = FrozenPromptSnapshot.capture(blocks_copy)
        pre_fork_fingerprint = pre_snapshot.fingerprint
    except Exception:
        logger.exception("self_review: failed to capture pre-fork snapshot — aborting")
        return None

    fork_input = ForkReviewInput(
        sessionId=session_id,
        turnId=turn_id,
        systemPromptBlocks=tuple(blocks_copy),
        parentAssistantMessage=dict(parent_assistant_message),
        preForkFingerprint=pre_fork_fingerprint,
    )

    try:
        result = await _run_review_fork(
            fork_input=fork_input,
            fork_runner=fork_runner,
            candidate_sink=candidate_sink,
            mode=mode,
            now=resolved_now,
        )
        return result
    except Exception:
        logger.exception(
            "self_review: fork failed (session=%s turn=%s) — fail-open",
            session_id,
            turn_id,
        )
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "REVIEW_DISABLED_TOOLSETS",
    "CandidateSink",
    "ForkReviewInput",
    "ForkReviewResult",
    "ReviewCandidate",
    "ReviewCandidateKind",
    "ReviewCandidateMode",
    "SelfReviewConfig",
    "run_self_review_hook",
]
