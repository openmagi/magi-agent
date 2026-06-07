"""B2 — GoalJudge: goal-satisfaction judge (parse + fail-open + parse-failure budget,
shadow-first).

This module provides the judge LOGIC and a clean seam — NOT the continuation loop
(that is wired in B3).  The real model-backed judge is injected; tests use a fake.

Verdict-parsing contract
------------------------
``parse_verdict`` inspects the raw model output for ONE of two signals (in order):

1. **JSON match** — scans for an embedded JSON object containing
   ``{"satisfied": true|false}``.  The first match wins; extra fields are ignored.
   JSON-first is required so ``{"satisfied": false}`` is not misread by the token
   path (which would see the ``satisfied`` key as a bare token).

2. **Token match** — looks for the literal tokens ``NOT_SATISFIED`` or ``SATISFIED``
   (case-insensitive, whole-word boundary via \\b).  ``NOT_SATISFIED`` takes
   precedence: if both tokens appear, the answer is "not satisfied".  Tokens are
   only consulted when no JSON object is present in the text.

If neither signal is found, ``parse_verdict`` returns ``None`` (unparseable).

Fail-open policy
----------------
``apply_judge_policy`` is a pure function over (verdict-or-None, consecutive parse
failures).  When ``verdict_or_none`` is ``None`` (unparseable output OR the judge
raised), the loop CONTINUES (fail-open = "not satisfied yet, keep going").
After ``DEFAULT_JUDGE_PARSE_FAILURE_BUDGET`` CONSECUTIVE ``None`` results the policy
returns ``action="stop"`` with ``reason="parse_failure_budget_exhausted"`` so a
broken judge cannot loop forever.

Shadow mode
-----------
``JudgeDecision`` carries ``acted: bool``.  In shadow mode ``acted=False`` — the judge
ran and recorded its decision but the caller is told not to act on it.  The caller
(B3) reads ``decision.acted`` to decide whether to act.

Evidence & redaction
--------------------
``build_judge_evidence`` records the judge decision as an ``EvidenceRecord``.  Raw
goal text and raw transcript are NEVER stored — only their SHA-256 digests and the
transcript byte-length.

Authority flags
---------------
traffic_attached and execution_attached remain Literal[False] throughout — this module
only adds judge logic, not execution authority.  B5 handles live promotion.

Forbidden imports: google.adk, adk_bridge, urllib, socket, requests, http, subprocess
— none appear at top-level (kept import-clean; ADK can be referenced via TYPE_CHECKING
only if needed, and only lazily).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_JUDGE_PARSE_FAILURE_BUDGET: int = 3
"""Number of consecutive parse failures before the policy gives up and stops the loop."""

_SHADOW_ENV_VAR = "MAGI_GOAL_LOOP_JUDGE_SHADOW"

# Regex: whole-word NOT_SATISFIED / SATISFIED (case-insensitive)
_NOT_SATISFIED_RE = re.compile(r"\bNOT_SATISFIED\b", re.IGNORECASE)
_SATISFIED_RE = re.compile(r"\bSATISFIED\b", re.IGNORECASE)

# JSON extraction: look for {"satisfied": true/false} anywhere in the text.
# No IGNORECASE — the JSON key is spec-defined lowercase "satisfied".  Uppercase
# variants like {"SATISFIED": true} fall through to the token path (correct).
_JSON_SATISFIED_RE = re.compile(
    r'\{[^{}]*"satisfied"\s*:\s*(true|false)[^{}]*\}',
)


# ---------------------------------------------------------------------------
# JudgeVerdict — frozen model
# ---------------------------------------------------------------------------

_VERDICT_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
)


class JudgeVerdict(BaseModel):
    """Immutable result of a single judge invocation.

    ``raw`` stores the model's raw output for audit purposes only — it is
    NEVER written to evidence records (redacted in ``build_judge_evidence``).
    """

    model_config = _VERDICT_CONFIG

    satisfied: bool
    confidence: float | None = None
    raw: str


# ---------------------------------------------------------------------------
# JudgeDecision — caller-facing result
# ---------------------------------------------------------------------------


class JudgeDecision(BaseModel):
    """Caller-facing result of running the judge through the shadow gate.

    ``acted`` is False in shadow mode (verdict observed but not acted on).
    ``failure_count`` is the CURRENT consecutive-parse-failure count AFTER
    this invocation.  On a successful parse this equals the input
    ``consecutive_parse_failures`` (unchanged — no auto-reset).  The CALLER
    (B3) is responsible for resetting its running counter to 0 after a
    satisfied/parsed verdict is received.
    ``reason`` documents why ``acted`` has its value (for audit logs).
    """

    model_config = _VERDICT_CONFIG

    verdict: JudgeVerdict | None
    acted: bool
    failure_count: int
    reason: str


# ---------------------------------------------------------------------------
# GoalJudge Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class GoalJudge(Protocol):
    """Seam for goal-satisfaction judging.

    The real implementation wraps a small/cheap model call (injected via the
    ADK runner boundary — see ``magi_agent/adk_bridge/runner_adapter.py``).
    Tests inject a ``FakeJudge``.  This module never constructs a real model
    client.
    """

    def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
        """Return a JudgeVerdict for the given goal + recent transcript.

        Implementor obligations:
        1. The call MUST be synchronous from the caller's perspective (B3 wraps
           async calls via asyncio.run if needed — same pattern as _run_turn_sync
           in scheduler_job_execution.py).
        2. On any exception the caller is responsible for catching + incrementing
           the parse-failure budget (the judge should not swallow errors).
        3. ``raw`` must contain the model's literal output so ``parse_verdict``
           can extract the decision.
        """
        ...


# ---------------------------------------------------------------------------
# parse_verdict
# ---------------------------------------------------------------------------


def parse_verdict(raw: str) -> JudgeVerdict | None:
    """Extract a satisfied/not decision from ``raw`` model output.

    Contract (see module docstring for full spec):
    - JSON match first: ``{"satisfied": true|false}`` (first match in text wins).
    - Token match second: NOT_SATISFIED > SATISFIED (case-insensitive, whole-word).
      Tokens are only matched when NO JSON object is present in the text (so a JSON
      key like "satisfied" does not accidentally trigger the token path).
    - Returns None on unparseable output.

    Pure function — no side effects.
    """
    if not raw or not raw.strip():
        return None

    # 1. JSON-based match (takes precedence — avoids "satisfied" key being a token)
    match = _JSON_SATISFIED_RE.search(raw)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj.get("satisfied"), bool):
                return JudgeVerdict(satisfied=bool(obj["satisfied"]), raw=raw)
        except (json.JSONDecodeError, AttributeError):
            pass

    # 2. Token-based match (NOT_SATISFIED takes precedence over SATISFIED)
    has_not = bool(_NOT_SATISFIED_RE.search(raw))
    has_sat = bool(_SATISFIED_RE.search(raw))

    if has_not:
        return JudgeVerdict(satisfied=False, raw=raw)
    if has_sat:
        return JudgeVerdict(satisfied=True, raw=raw)

    return None


# ---------------------------------------------------------------------------
# apply_judge_policy — fail-open + parse-failure budget (pure function)
# ---------------------------------------------------------------------------

PolicyAction = Literal["continue", "stop"]
PolicyReason = Literal[
    "satisfied",
    "not_satisfied",
    "parse_failure_fail_open",
    "parse_failure_budget_exhausted",
]


def apply_judge_policy(
    *,
    verdict_or_none: JudgeVerdict | None,
    consecutive_parse_failures: int,
    budget: int = DEFAULT_JUDGE_PARSE_FAILURE_BUDGET,
) -> dict[str, Any]:
    """Pure policy function: decide whether the loop should continue or stop.

    Parameters
    ----------
    verdict_or_none:
        A successfully parsed JudgeVerdict, or None (unparseable / judge raised).
    consecutive_parse_failures:
        The number of consecutive None-verdict results seen so far,
        INCLUDING the current call (post-increment).  When called from
        ``run_judge``, this is ``new_failure_count`` after adding 1 for the
        current failure.  The ``>=`` check therefore fires on exactly the
        Nth consecutive failure (where N = budget).
    budget:
        Max consecutive parse failures before giving up (default = module constant).

    Returns
    -------
    dict with keys:
        ``action``: "continue" | "stop"
        ``reason``: one of PolicyReason literals (for audit)
    """
    if verdict_or_none is not None:
        # Successful parse — act on the verdict
        if verdict_or_none.satisfied:
            return {"action": "stop", "reason": "satisfied"}
        return {"action": "continue", "reason": "not_satisfied"}

    # Unparseable output (None verdict) — fail-open, but budget check first
    if consecutive_parse_failures >= budget:
        return {"action": "stop", "reason": "parse_failure_budget_exhausted"}

    return {"action": "continue", "reason": "parse_failure_fail_open"}


# ---------------------------------------------------------------------------
# build_judge_evidence — redacted EvidenceRecord
# ---------------------------------------------------------------------------


def build_judge_evidence(
    *,
    goal: str,
    transcript_excerpt: str,
    verdict: JudgeVerdict | None,
    failure_count: int,
    now: datetime | None = None,
) -> EvidenceRecord:
    """Build a redacted EvidenceRecord for audit.

    Raw goal text and raw transcript are NEVER stored.  Only their
    SHA-256 digests and the transcript byte-length are recorded.
    ``verdict.raw`` (the model's literal output) is also redacted —
    only the boolean ``satisfied`` decision is stored.
    """
    ts = now or datetime.now(UTC)
    observed_at = int(ts.astimezone(UTC).timestamp() * 1000)

    goal_digest = "sha256:" + hashlib.sha256(goal.encode()).hexdigest()
    transcript_digest = "sha256:" + hashlib.sha256(transcript_excerpt.encode()).hexdigest()
    transcript_len = len(transcript_excerpt)

    fields: dict[str, object] = {
        "goalDigest": goal_digest,
        "transcriptDigest": transcript_digest,
        "transcriptLen": transcript_len,
        "satisfied": verdict.satisfied if verdict is not None else None,
        "failureCount": failure_count,
    }

    evidence_status = "ok" if verdict is not None else "unknown"

    return EvidenceRecord(
        type="custom:GoalJudgeDecision",
        status=evidence_status,
        observedAt=observed_at,
        source=EvidenceSource(kind="verifier"),
        fields=fields,
    )


# ---------------------------------------------------------------------------
# Shadow gate helpers
# ---------------------------------------------------------------------------


def _judge_shadow_enabled() -> bool:
    """Return True if the judge should run in shadow mode (default: True)."""
    raw = os.environ.get(_SHADOW_ENV_VAR)
    if raw is None:
        return True  # shadow-first default
    clean = raw.strip().lower()
    if clean in {"0", "false"}:
        return False
    return True


def run_judge(
    judge: GoalJudge,
    *,
    goal: str,
    transcript_excerpt: str,
    consecutive_parse_failures: int,
    shadow: bool | None = None,
    now: datetime | None = None,
) -> JudgeDecision:
    """Run the judge through the shadow gate and return a JudgeDecision.

    If ``shadow`` is None, the env var ``MAGI_GOAL_LOOP_JUDGE_SHADOW`` is
    consulted (default True = shadow).

    In shadow mode ``acted`` is always False — the verdict is recorded for
    audit but the caller must NOT act on it (B3 reads this flag).

    On any exception from the judge, verdict is treated as None (unparseable)
    and failure_count is incremented in the returned decision.

    Evidence is the caller's responsibility: ``run_judge`` does NOT persist
    or attach evidence to the returned ``JudgeDecision``.  B4 owns the
    evidence bus and calls ``build_judge_evidence`` directly.
    """
    is_shadow = _judge_shadow_enabled() if shadow is None else shadow
    ts = now or datetime.now(UTC)

    verdict: JudgeVerdict | None = None
    parse_failure_added = 0

    try:
        raw_verdict = judge.judge(goal, transcript_excerpt)
        verdict = parse_verdict(raw_verdict.raw)
        if verdict is None:
            parse_failure_added = 1
    except Exception:  # noqa: BLE001
        # Judge raised — treat as unparseable (fail-open)
        parse_failure_added = 1

    new_failure_count = consecutive_parse_failures + parse_failure_added

    policy = apply_judge_policy(
        verdict_or_none=verdict,
        consecutive_parse_failures=new_failure_count,
        budget=DEFAULT_JUDGE_PARSE_FAILURE_BUDGET,
    )

    if is_shadow:
        # Shadow: record verdict but do not act
        evidence = build_judge_evidence(
            goal=goal,
            transcript_excerpt=transcript_excerpt,
            verdict=verdict,
            failure_count=new_failure_count,
            now=ts,
        )
        _ = evidence  # evidence computed for audit; NOT attached to decision — B4 calls build_judge_evidence directly
        return JudgeDecision(
            verdict=verdict,
            acted=False,
            failure_count=new_failure_count,
            reason="shadow_mode",
        )

    # Live mode
    return JudgeDecision(
        verdict=verdict,
        acted=True,
        failure_count=new_failure_count,
        reason=policy["reason"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "DEFAULT_JUDGE_PARSE_FAILURE_BUDGET",
    "GoalJudge",
    "JudgeDecision",
    "JudgeVerdict",
    "apply_judge_policy",
    "build_judge_evidence",
    "parse_verdict",
    "run_judge",
]
