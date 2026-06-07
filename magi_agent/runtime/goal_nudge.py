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

    When ``nudge.required_evidence`` is non-empty, delegates to
    :class:`~magi_agent.evidence.final_output_gate.FinalOutputGate` with
    ``enabled=True`` and ``localEvaluationEnabled=True``.  Returns ``True``
    when the gate decision is NOT a hard failure (``status`` not in
    ``{"blocked", "fail"}``) AND none of the decision's ``reason_codes``
    starts with ``"missing_required_evidence:"`` — because the gate returns
    ``"repair_required"`` for BOTH present and absent evidence, distinguished
    only via reason codes, so the status field alone is insufficient to
    determine done-ness.
    """
    if not nudge.required_evidence:
        return False

    # Lazy import so the module stays cheap to import.
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
            requiredEvidence=nudge.required_evidence,
            evidenceRecords=records,
            modelTier="standard",
        )
    )
    # The gate returns "repair_required" for BOTH missing and present evidence
    # (differentiating via reason_codes).  "blocked" = hard calc failure.
    # "fail" is not a real status but guard defensively.
    # Per the spec: done iff status not in the failing set ("blocked", "fail")
    # AND no missing_required_evidence reason codes (which distinguish
    # "evidence present but needs repair" from "evidence truly absent").
    if decision.status in ("blocked", "fail"):
        return False
    missing_evidence = any(
        code.startswith("missing_required_evidence:") for code in decision.reason_codes
    )
    return not missing_evidence


__all__ = [
    "GoalNudge",
    "build_nudge_message",
    "goal_is_met",
]
