"""Answer Verifier — value-level verification against already-gathered evidence.

Default-OFF.  Three modes: off (no-op), audit (log only), enforce (apply correction).

Anti-overfitting firewall: this module MUST NOT import from any benchmark
scoring layer.  Any PR that adds a scorer import is a violation.

Distinct from:
  output_contract_gate.py  — shape/format discipline only, never changes value
  selective_reflection/    — reasoning approach critique, no direct value check
  final_projection_gate.py — citation integrity, no value comparison

This gate answers: "Is the final VALUE supported by what the agent already found?"

Environment variable: MAGI_ANSWER_VERIFIER_MODE
  values:
    off                             → gate disabled (default when unset)
    audit                           → observe/log only, never mutate (default when enabled)
    enforce                         → apply correction after safety guards (opt-in)
    1 | true | yes | on (truthy)    → resolved to "audit" (enable-but-safe signal)
    any other non-empty value       → resolved to "audit" (safe fallback)

Audit-first principle (P5 — GAIA learnings):
  When the verifier is enabled, the default safe mode is *audit* — it observes
  and logs mismatches but never changes the answer.  *enforce* requires an
  explicit string "enforce" because it can over-correct (backtick→grave).
  Guards remain active in enforce mode; this change only sets the safe default.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

# ---------------------------------------------------------------------------
# Env var name (single source of truth)
# ---------------------------------------------------------------------------

ANSWER_VERIFIER_MODE_ENV: str = "MAGI_ANSWER_VERIFIER_MODE"

_VERIFIER_OFF_VALUES: frozenset[str] = frozenset({"", "0", "false", "no", "off"})
_VERIFIER_ENFORCE_VALUES: frozenset[str] = frozenset({"enforce"})


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

AnswerVerifierMode = Literal["off", "audit", "enforce"]
AnswerVerifierStatus = Literal[
    "skipped",
    "confirmed",
    "mismatch_corrected",
    "mismatch_refused",
    "audit",
]
AnswerTypeHint = Literal[
    "count",
    "singular_plural",
    "entity",
    "arithmetic",
    "list",
    "ordinal",
    "unspecified",
]


# ---------------------------------------------------------------------------
# Execution posture (safety contract)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnswerVerifierExecutionPosture:
    """Safety contract for the answer verifier gate.

    default_off:            Gate is OFF unless MAGI_ANSWER_VERIFIER_MODE is set.
    local_only:             No remote service or ADK runner is attached.
    live_search_allowed:    Always False — verifier uses only already-gathered evidence.
    model_calls_allowed:    True when mode is enforce or audit.
    adk_runner_attached:    Always False.
    memory_writes_allowed:  Always False.
    channel_delivery_allowed: Always False.
    """

    default_off: Literal[True] = True
    local_only: Literal[True] = True
    live_search_allowed: Literal[False] = False
    model_calls_allowed: bool = False
    adk_runner_attached: Literal[False] = False
    memory_writes_allowed: Literal[False] = False
    channel_delivery_allowed: Literal[False] = False


# ---------------------------------------------------------------------------
# Evidence payload
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnswerVerifierEvidencePayload:
    """Evidence snippets the agent already gathered — no new search allowed.

    question:            Original question text.
    final_answer:        The answer to be verified.
    evidence_snippets:   Immutable tuple of text fragments from the agent run.
    answer_type_hint:    Hint about the answer type to guide the verifier.
    """

    question: str
    final_answer: str
    evidence_snippets: tuple[str, ...]
    answer_type_hint: AnswerTypeHint = "unspecified"


# ---------------------------------------------------------------------------
# Request / Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnswerVerifierRequest:
    """Input to evaluate_answer_verifier().

    verifier_id:      Stable identifier for audit logs (e.g. "gaia-verifier").
    mode:             "off" | "audit" | "enforce".  Default-OFF.
    question:         Original question text.
    final_answer:     The answer string produced by the agent.
    evidence_payload: Evidence gathered by the agent (no new search).
    model_provider:   Callable(prompt: str) -> str, or None.
                      If None and mode != "off", gate fails-open (skipped).
    """

    verifier_id: str
    mode: AnswerVerifierMode
    question: str
    final_answer: str
    evidence_payload: AnswerVerifierEvidencePayload
    model_provider: object | None = None


@dataclass(frozen=True)
class AnswerVerifierResult:
    """Output of evaluate_answer_verifier().

    verifier_id:        Mirrors the request verifier_id.
    mode:               Mode used for this evaluation.
    status:             Outcome classification.
    ok:                 True unless a correction was blocked by a guard.
    original_answer:    Unmodified answer from the agent.
    verified_answer:    Corrected answer (= original if no correction applied).
    correction_applied: True iff the answer was changed.
    evidence_basis:     Evidence snippet cited for any correction.
    answer_digest:      sha256 of verified_answer for audit log correlation.
    execution_posture:  Static safety contract for this run.
    """

    verifier_id: str
    mode: AnswerVerifierMode
    status: AnswerVerifierStatus
    ok: bool
    original_answer: str
    verified_answer: str
    correction_applied: bool
    evidence_basis: str
    answer_digest: str
    execution_posture: AnswerVerifierExecutionPosture


# ---------------------------------------------------------------------------
# Env-var reader (audit-first default)
# ---------------------------------------------------------------------------


def read_verifier_mode_from_env(
    env: Mapping[str, str] | None = None,
) -> AnswerVerifierMode:
    """Read ``MAGI_ANSWER_VERIFIER_MODE`` and resolve it to an :class:`AnswerVerifierMode`.

    Resolution rules (audit-first, P5):

    * Unset / empty / falsy values (``""``, ``"0"``, ``"false"``, ``"no"``,
      ``"off"``) → ``"off"`` (gate disabled).
    * Explicit ``"audit"`` (case-insensitive) → ``"audit"``.
    * Explicit ``"enforce"`` (case-insensitive) → ``"enforce"`` (opt-in only).
    * Generic truthy values (``"1"``, ``"true"``, ``"yes"``, ``"on"``) → ``"audit"``.
    * Any other non-empty value (unrecognised) → ``"audit"`` (safe fallback).

    The caller may pass an explicit ``env`` mapping for testing; defaults to
    ``os.environ``.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw = (source.get(ANSWER_VERIFIER_MODE_ENV) or "").strip().lower()

    if raw in _VERIFIER_OFF_VALUES:
        return "off"
    if raw in _VERIFIER_ENFORCE_VALUES:
        return "enforce"
    # "audit" or any truthy/unknown value → audit (safe default when enabled)
    return "audit"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _digest(text: str) -> str:
    return "sha256:" + sha256(text.encode()).hexdigest()


def _skipped_result(request: AnswerVerifierRequest) -> AnswerVerifierResult:
    return AnswerVerifierResult(
        verifier_id=request.verifier_id,
        mode=request.mode,
        status="skipped",
        ok=True,
        original_answer=request.final_answer,
        verified_answer=request.final_answer,
        correction_applied=False,
        evidence_basis="",
        answer_digest=_digest(request.final_answer),
        execution_posture=AnswerVerifierExecutionPosture(model_calls_allowed=False),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def evaluate_answer_verifier(request: AnswerVerifierRequest) -> AnswerVerifierResult:
    """Evaluate the answer verifier gate.

    mode=off  → immediately returns status=skipped (default-OFF).
    mode=audit → calls LLM, records mismatch in result, but does NOT change answer.
    mode=enforce → calls LLM; if mismatch + safety guards pass, applies correction.

    Fail-open: any exception during LLM call → skipped result with original answer.
    No new search or tool calls are permitted; only evidence_payload is used.

    Parameters
    ----------
    request:
        An AnswerVerifierRequest describing the answer to verify.

    Returns
    -------
    AnswerVerifierResult
        Never raises.
    """
    # Import here to avoid circular imports (checks module imports AnswerTypeHint from us)
    from magi_agent.research.answer_verifier_checks import (  # noqa: PLC0415
        build_verifier_prompt,
        parse_verifier_response,
        safety_guard_check,
    )

    if request.mode == "off":
        return _skipped_result(request)

    provider = request.model_provider
    if provider is None or not callable(provider):
        # Fail-open: no provider → skip
        return _skipped_result(request)

    posture = AnswerVerifierExecutionPosture(model_calls_allowed=True)

    try:
        prompt = build_verifier_prompt(
            question=request.question,
            final_answer=request.final_answer,
            answer_type_hint=request.evidence_payload.answer_type_hint,
            evidence_snippets=request.evidence_payload.evidence_snippets,
        )
        raw_response: str = provider(prompt)
    except Exception:
        # Fail-open
        return _skipped_result(request)

    verdict, corrected_value, evidence_basis = parse_verifier_response(raw_response)

    # --- audit mode: report but never change ---
    if request.mode == "audit":
        return AnswerVerifierResult(
            verifier_id=request.verifier_id,
            mode=request.mode,
            status="audit",
            ok=True,
            original_answer=request.final_answer,
            verified_answer=request.final_answer,
            correction_applied=False,
            evidence_basis=evidence_basis or "",
            answer_digest=_digest(request.final_answer),
            execution_posture=posture,
        )

    # --- enforce mode ---
    if verdict == "confirmed" or corrected_value is None:
        return AnswerVerifierResult(
            verifier_id=request.verifier_id,
            mode=request.mode,
            status="confirmed",
            ok=True,
            original_answer=request.final_answer,
            verified_answer=request.final_answer,
            correction_applied=False,
            evidence_basis="",
            answer_digest=_digest(request.final_answer),
            execution_posture=posture,
        )

    # Mismatch — apply safety guards before accepting correction
    safe = safety_guard_check(request.final_answer, corrected_value)
    if not safe:
        return AnswerVerifierResult(
            verifier_id=request.verifier_id,
            mode=request.mode,
            status="mismatch_refused",
            ok=True,
            original_answer=request.final_answer,
            verified_answer=request.final_answer,
            correction_applied=False,
            evidence_basis=evidence_basis or "",
            answer_digest=_digest(request.final_answer),
            execution_posture=posture,
        )

    # Safety guards passed — apply correction
    return AnswerVerifierResult(
        verifier_id=request.verifier_id,
        mode=request.mode,
        status="mismatch_corrected",
        ok=True,
        original_answer=request.final_answer,
        verified_answer=corrected_value,
        correction_applied=True,
        evidence_basis=evidence_basis or "",
        answer_digest=_digest(corrected_value),
        execution_posture=posture,
    )


__all__ = [
    "ANSWER_VERIFIER_MODE_ENV",
    "AnswerTypeHint",
    "AnswerVerifierEvidencePayload",
    "AnswerVerifierExecutionPosture",
    "AnswerVerifierMode",
    "AnswerVerifierRequest",
    "AnswerVerifierResult",
    "AnswerVerifierStatus",
    "evaluate_answer_verifier",
    "read_verifier_mode_from_env",
]
