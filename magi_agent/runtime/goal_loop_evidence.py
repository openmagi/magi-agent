"""WS3 PR3b - pure pre-judge goal-completion resolver (evidence-first).

Provides the deterministic, model-call-free decision the engine clean-break
block consults BEFORE the existing LLM goal-loop judge (subsystem B). It exists
because completion was previously the model's say-so (the Hermes #1 complaint).
Precedence: when ``required_evidence`` is declared the gate is the completion
contract (``satisfied`` -> ``done`` independent of open todos; a hard failure ->
honest ``pause``; missing evidence -> keep working, never ``done``); only when NO
evidence is declared does an all-complete durable todo ledger short-circuit to
``done``. Everything else defers to the existing judge. Ambiguity NEVER maps to
``done``.

It introduces NO new ``evaluate_goal_completion`` (that name belongs to the async
judge in ``runtime/goal_loop_judge.py``); the only function here that touches the
evidence gate is ``evaluate_required_evidence``, the single ``FinalOutputGate``
call site, shared with subsystem A's module-level ``goal_is_met``.

Pure: no I/O, no model call, no ADK import. ``evaluate_required_evidence`` lazily
imports ``FinalOutputGate`` so importing this module stays cheap (cold-start
safe).

Design: WS3 Goal/Completion + Durable Cross-Turn Todo Ledger, PR3b
(sections 3.1 / 3.2).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

GoalPreJudgeOutcome = Literal["done", "continue", "pause", "defer_to_judge"]
EvidenceVerdict = Literal["satisfied", "missing", "unverifiable"]

_DEFAULT_EVIDENCE_DOMAIN = "general"

__all__ = [
    "EvidenceVerdict",
    "GoalPreJudgeOutcome",
    "evaluate_required_evidence",
    "resolve_pre_judge_outcome",
]


def evaluate_required_evidence(
    required_evidence: tuple[str, ...],
    evidence_records: tuple[object, ...],
    *,
    domain: str = _DEFAULT_EVIDENCE_DOMAIN,
) -> EvidenceVerdict:
    """Return the structured evidence verdict for a required-evidence goal.

    The ONE ``FinalOutputGate`` evaluation site in the codebase (subsystem A's
    module-level ``goal_is_met`` derives its bool from this same call). Maps the
    gate decision to the three-way verdict the resolver needs:

    - ``"unverifiable"``: ``decision.status == "blocked"`` - a hard calculation
      failure the gate cannot get past, so completion cannot be confirmed.
    - ``"missing"``: any reason code starts ``"missing_required_evidence:"`` -
      the required evidence is simply absent, so keep working.
    - ``"satisfied"``: otherwise (status in ``passed`` / ``repair_required`` /
      ``insufficient_evidence`` / ``skipped`` with no
      ``missing_required_evidence`` reason codes).

    ``required_evidence`` empty is a total-function safety case (the resolver
    only calls this when it is non-empty): an empty tuple returns ``"satisfied"``
    (nothing to verify).
    """
    if not required_evidence:
        return "satisfied"

    # Lazy import so this module stays cheap to import (cold-start safety).
    from magi_agent.evidence.final_output_gate import (  # noqa: PLC0415
        FinalOutputGate,
        FinalOutputGateConfig,
        FinalOutputGateRequest,
    )

    records = tuple(evidence_records)
    decision = FinalOutputGate(
        FinalOutputGateConfig(enabled=True, localEvaluationEnabled=True)
    ).evaluate(
        FinalOutputGateRequest(
            domain=domain,
            outputText="",
            requiredEvidence=required_evidence,
            evidenceRecords=records,
            modelTier="standard",
        )
    )
    # ``blocked`` = hard calc failure (cannot verify). Any
    # ``missing_required_evidence:*`` reason code = evidence absent. Both are NOT
    # ``satisfied``; the status field alone is insufficient (the gate returns
    # ``repair_required`` for BOTH present-needs-repair AND absent evidence,
    # distinguished only by reason codes), so we key off both.
    if decision.status == "blocked":
        return "unverifiable"
    missing = any(
        code.startswith("missing_required_evidence:")
        for code in decision.reason_codes
    )
    if missing:
        return "missing"
    return "satisfied"


def _ledger_all_completed(ledger_snapshot: Sequence[object]) -> bool:
    """True iff the snapshot is non-empty and every todo is ``completed``."""
    return bool(ledger_snapshot) and all(
        getattr(item, "status", None) == "completed" for item in ledger_snapshot
    )


def resolve_pre_judge_outcome(
    *,
    required_evidence: tuple[str, ...],
    evidence_records: tuple[object, ...],
    ledger_snapshot: tuple[object, ...],
    domain: str = _DEFAULT_EVIDENCE_DOMAIN,
) -> GoalPreJudgeOutcome:
    """Deterministic pre-judge outcome. Ambiguity NEVER maps to ``done``.

    Rules (Design section 3.2; the ``ledger_snapshot`` is the DURABLE restored
    snapshot, not the volatile current-turn map):

    1. ``required_evidence`` non-empty -> the gate is the completion contract
       (checked FIRST, INDEPENDENT of the ledger): ``satisfied`` -> ``done``
       (even with open todos; the declared evidence is the contract); ``missing``
       -> ``continue`` (never ``done``); ``unverifiable`` (gate ``blocked``, hard
       calc failure) -> ``pause``.
    2. ``required_evidence`` EMPTY AND ``ledger_snapshot`` non-empty AND every
       todo ``completed`` -> ``done`` (the durable ledger is the contract when no
       evidence was declared).
    3. ``required_evidence`` empty AND ``ledger_snapshot`` non-empty with open
       todos -> ``continue``.
    4. Otherwise (no ledger signal, no evidence requirement) -> ``defer_to_judge``
       (let the EXISTING goal-loop judge run exactly as today).
    """
    if required_evidence:
        verdict = evaluate_required_evidence(
            required_evidence, evidence_records, domain=domain
        )
        if verdict == "satisfied":
            return "done"
        if verdict == "unverifiable":
            return "pause"
        # "missing" - the evidence is absent; keep working, never done.
        return "continue"
    if _ledger_all_completed(ledger_snapshot):
        return "done"
    if ledger_snapshot:
        # Non-empty ledger with open todos and no evidence requirement.
        return "continue"
    return "defer_to_judge"
