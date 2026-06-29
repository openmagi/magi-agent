"""PR4 — Lightweight goal-nudge continuation primitive.

Provides a :class:`GoalNudge` dataclass and two pure functions
(:func:`build_nudge_message`, :func:`goal_is_met`) used by
``cli.engine.MagiEngineDriver._drive`` to implement goose-style
"keep going until the goal is met" continuation without touching
``meta_orchestration/``.

Design points
-------------
- Default OFF: ``goal_nudge=None`` → ``_drive`` behaves byte-identically to
  today. The driver checks ``goal_nudge is not None`` before any nudge logic.
- ``mode="goal"`` (default): verify-once-per-stop semantics (mirrors goose
  ``goal`` mode). A latch prevents more than one nudge per consecutive clean
  stop; the latch is reset each time a tool event fires (so a new stop is
  eligible for one nudge again).
- ``mode="grind"``: re-nudge on every clean stop until ``max_nudges`` is
  exhausted (mirrors goose ``grind`` mode).
- ``required_evidence``: when non-empty, done is evidence-backed via
  :class:`~magi_agent.evidence.final_output_gate.FinalOutputGate`. When
  empty, ``goal_is_met`` always returns ``False`` so the synthetic self-check
  turn (the nudge message itself) drives the decision.
- This module MUST NOT import from ``meta_orchestration/``.
- Import-clean: heavy evidence symbols are imported lazily inside
  ``goal_is_met`` so that importing this module stays cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable


@dataclass(frozen=True)
class GoalNudge:
    """Configuration for a single-agent goal-nudge continuation.

    Parameters
    ----------
    goal:
        Human-readable description of the objective.  Embedded verbatim in
        the synthetic nudge message so it should be concise and action-oriented.
    mode:
        ``"goal"`` (default) — verify once per consecutive clean stop (latch).
        ``"grind"`` — re-nudge every clean stop until ``max_nudges``.
    max_nudges:
        Hard cap on the total number of nudge re-invocations (anti-infinite-
        loop guard).  Default 3.  Cannot exceed the existing turn/iteration
        budget because nudge calls share the outer driver's invocation loop.
    required_evidence:
        When non-empty, done is checked via
        :class:`~magi_agent.evidence.final_output_gate.FinalOutputGate`.
        The tuple must contain evidence type tokens understood by
        ``_required_present`` (e.g. ``"source_ledger"``,
        ``"calculation_evidence"``).  When empty, ``goal_is_met`` always
        returns ``False`` (rely on the synthetic self-check turn).
    domain:
        Evidence domain forwarded to ``FinalOutputGateRequest``.  Default
        ``"general"``.

    Usage
    -----
    ``GoalNudge`` is an engine-level API only. Do NOT expose it through
    recipes or CLI flags in this PR. Use ``meta_orchestration/`` when work
    needs decomposition into spawned sub-agents with per-child acceptance.
    """

    goal: str
    mode: Literal["goal", "grind"] = "goal"
    max_nudges: int = 3
    required_evidence: tuple[str, ...] = ()
    domain: str = "general"

    def __post_init__(self) -> None:
        if self.max_nudges < 0:
            raise ValueError(
                f"GoalNudge.max_nudges must be >= 0 (got {self.max_nudges!r}). "
                "Use 0 to disable nudges."
            )


def build_nudge_message(nudge: GoalNudge) -> str:
    """Build the synthetic user message injected as the nudge re-invocation.

    ``mode="goal"`` → ask the model to check and continue if unmet (one shot).
    ``mode="grind"`` → tell the model to keep working unconditionally.
    """
    if nudge.mode == "grind":
        return (
            "Keep working. The objective is not yet complete:\n\n"
            f"**Goal:** {nudge.goal}\n\nContinue until it is fully done."
        )
    return (
        "Before finishing, check whether the following goal has been fully met:\n\n"
        f"**Goal:** {nudge.goal}\n\nIf not, continue working toward it."
    )


def goal_is_met(
    nudge: GoalNudge,
    *,
    evidence_records: "Iterable[object]",
) -> bool:
    """Return ``True`` iff the goal is considered done.

    When ``nudge.required_evidence`` is empty, always returns ``False`` —
    the nudge itself is the mechanism (the model self-checks on the synthetic
    turn).

    When ``nudge.required_evidence`` is non-empty, the bool is DERIVED from the
    single shared :func:`~magi_agent.runtime.goal_loop_evidence.evaluate_required_evidence`
    verdict (the one ``FinalOutputGate`` call site in the codebase, WS3 PR3b):
    done iff the verdict is ``"satisfied"``.  ``"missing"`` (evidence absent)
    and ``"unverifiable"`` (gate ``status == "blocked"``, a hard calc failure)
    both yield ``False``, byte-identical to the prior status/reason-code check
    (the gate returns ``"repair_required"`` for BOTH present-needs-repair and
    absent evidence, distinguished only via reason codes, so the status field
    alone is insufficient; the shared helper keys off both, exactly as before).
    """
    if not nudge.required_evidence:
        return False

    # Lazy import so the module stays cheap to import. The shared helper is the
    # one evidence-evaluation site; subsystem A's bool is derived from its
    # multi-valued verdict so the two can never drift (WS3 PR3b, section 3.2).
    from magi_agent.runtime.goal_loop_evidence import (  # noqa: PLC0415
        evaluate_required_evidence,
    )

    verdict = evaluate_required_evidence(
        nudge.required_evidence,
        tuple(evidence_records),
        domain=nudge.domain,
    )
    return verdict == "satisfied"


@dataclass(frozen=True)
class GoalNudgeEvidenceReasons:
    """WS6 PR6c: transport-safe evidence-reason fields for the nudge status.

    Pure value object surfaced on the EXISTING ``goal_nudge`` status event so a
    client can see WHY the turn continued (the evidence gate was not satisfied).
    All fields are public, redaction-safe tokens (validator names and gate
    reason codes), never user content.

    Parameters
    ----------
    missing_validators:
        The subset of ``requirement_labels`` the gate reports as still unmet,
        parsed from ``missing_required_evidence:<token>`` reason codes.
    requirement_labels:
        The full ``GoalNudge.required_evidence`` tuple the gate evaluated.
    reason_codes:
        The full reason-code tuple of the gate decision.
    """

    missing_validators: tuple[str, ...]
    requirement_labels: tuple[str, ...]
    reason_codes: tuple[str, ...]


def goal_nudge_evidence_reasons(
    nudge: GoalNudge,
    *,
    evidence_records: "Iterable[object]",
) -> GoalNudgeEvidenceReasons:
    """Project the goal_nudge evidence decision into transport-safe reason fields.

    WS6 PR6c. Re-runs the SAME :class:`~magi_agent.evidence.final_output_gate.\
FinalOutputGate` evaluation that :func:`goal_is_met` performs, over the SAME
    evidence records, then derives the ``missingValidators`` / ``requirementLabels``
    / ``reasonCodes`` used to ENRICH the existing ``goal_nudge`` status event.
    Pure and side-effect free; it does NOT alter the continue/re-invoke control
    flow (the engine still ``continue``s after emitting the enriched event).

    When ``nudge.required_evidence`` is empty the gate is not consulted (as in
    :func:`goal_is_met`, which returns ``False`` unconditionally in that case),
    so every field is empty.
    """
    requirement_labels = tuple(nudge.required_evidence)
    if not requirement_labels:
        return GoalNudgeEvidenceReasons(
            missing_validators=(),
            requirement_labels=(),
            reason_codes=(),
        )

    # Lazy import so the module stays cheap to import (mirrors ``goal_is_met``).
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
            domain=nudge.domain,
            outputText="",
            requiredEvidence=requirement_labels,
            evidenceRecords=records,
            modelTier="standard",
        )
    )
    missing_validators = tuple(
        code.split(":", 1)[1]
        for code in decision.reason_codes
        if code.startswith("missing_required_evidence:")
    )
    return GoalNudgeEvidenceReasons(
        missing_validators=missing_validators,
        requirement_labels=requirement_labels,
        reason_codes=tuple(decision.reason_codes),
    )


__all__ = [
    "GoalNudge",
    "GoalNudgeEvidenceReasons",
    "build_nudge_message",
    "goal_is_met",
    "goal_nudge_evidence_reasons",
]
