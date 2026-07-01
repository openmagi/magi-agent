"""WS3 PR3b - pure pre-judge resolver + structured evidence verdict.

Hermetic: ``evaluate_required_evidence`` exercises the REAL ``FinalOutputGate``
for the natural ``missing`` / ``satisfied`` verdicts; the ``blocked`` ->
``unverifiable`` mapping (which only arises from a hard calc failure that an
empty ``outputText`` cannot trigger) is exercised by monkeypatching the gate so
the structured mapping is asserted in isolation. The resolver's ``pause`` branch
reuses the same monkeypatch via the module-level helper.
"""
from __future__ import annotations

import pytest

from magi_agent.runtime.goal_loop_evidence import (
    EvidenceVerdict,
    evaluate_required_evidence,
    resolve_pre_judge_outcome,
)
from magi_agent.runtime.plan_ledger import TodoItem


def _todos(*pairs: tuple[str, str]) -> tuple[TodoItem, ...]:
    return tuple(TodoItem(content=c, status=s) for c, s in pairs)  # type: ignore[arg-type]


def _source_record(ref: str = "example.com") -> dict[str, object]:
    # sourceRef must pass the gate's public-ref shape (no "/"); see
    # final_output_gate._PUBLIC_REF_RE.
    return {"type": "SourceInspection", "sourceRef": ref}


# ---------------------------------------------------------------------------
# evaluate_required_evidence (structured verdict)
# ---------------------------------------------------------------------------


class TestEvaluateRequiredEvidence:
    def test_missing_when_required_evidence_absent(self) -> None:
        assert (
            evaluate_required_evidence(("source_ledger",), (), domain="general")
            == "missing"
        )

    def test_satisfied_when_source_evidence_present(self) -> None:
        verdict = evaluate_required_evidence(
            ("source_ledger",),
            (_source_record(),),
            domain="general",
        )
        assert verdict == "satisfied"

    def test_blocked_maps_to_unverifiable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A hard calc failure (gate status "blocked") cannot be produced with an
        # empty outputText, so monkeypatch the gate to return blocked and assert
        # the structured mapping.
        import magi_agent.evidence.final_output_gate as gate_mod

        class _BlockedDecision:
            status = "blocked"
            reason_codes = ("numeric_claim_mismatch",)

        class _FakeGate:
            def __init__(self, *_a: object, **_k: object) -> None:
                pass

            def evaluate(self, *_a: object, **_k: object) -> object:
                return _BlockedDecision()

        monkeypatch.setattr(gate_mod, "FinalOutputGate", _FakeGate)
        assert (
            evaluate_required_evidence(("source_ledger",), (), domain="general")
            == "unverifiable"
        )

    def test_empty_required_is_satisfied(self) -> None:
        # Total-function safety: nothing to verify.
        assert evaluate_required_evidence((), (), domain="general") == "satisfied"


# ---------------------------------------------------------------------------
# resolve_pre_judge_outcome
# ---------------------------------------------------------------------------


class TestResolvePreJudgeOutcome:
    def test_pre_judge_done_when_all_todos_completed(self) -> None:
        outcome = resolve_pre_judge_outcome(
            required_evidence=(),
            evidence_records=(),
            ledger_snapshot=_todos(("t1", "completed"), ("t2", "completed")),
        )
        assert outcome == "done"

    def test_pre_judge_continue_when_open_todos(self) -> None:
        outcome = resolve_pre_judge_outcome(
            required_evidence=(),
            evidence_records=(),
            ledger_snapshot=_todos(("t1", "completed"), ("t2", "in_progress")),
        )
        assert outcome != "done"
        assert outcome == "continue"  # rule 3: non-empty ledger, open todos

    def test_pre_judge_defer_when_empty_ledger_no_evidence(self) -> None:
        outcome = resolve_pre_judge_outcome(
            required_evidence=(),
            evidence_records=(),
            ledger_snapshot=(),
        )
        assert outcome == "defer_to_judge"

    def test_pre_judge_evidence_required_blocks_unsupported_done(self) -> None:
        # Required evidence + zero source records -> never done.
        outcome = resolve_pre_judge_outcome(
            required_evidence=("source_ledger",),
            evidence_records=(),
            ledger_snapshot=_todos(("t1", "completed")),
        )
        assert outcome != "done"
        assert outcome == "continue"

    def test_pre_judge_evidence_missing_routes_continue(self) -> None:
        outcome = resolve_pre_judge_outcome(
            required_evidence=("source_ledger",),
            evidence_records=(),
            ledger_snapshot=(),
        )
        assert outcome == "continue"

    def test_pre_judge_evidence_satisfied_routes_done(self) -> None:
        outcome = resolve_pre_judge_outcome(
            required_evidence=("source_ledger",),
            evidence_records=(_source_record(),),
            ledger_snapshot=_todos(("t1", "completed")),
        )
        assert outcome == "done"

    def test_pre_judge_evidence_satisfied_done_ignores_open_todos(self) -> None:
        # Precedence pin: when required_evidence is DECLARED, the satisfied gate is
        # the completion contract and short-circuits to done INDEPENDENT of the
        # ledger - even with an open todo (the evidence, not the todo list, is the
        # declared contract). Locks the OR/precedence semantics for PR3c activation.
        outcome = resolve_pre_judge_outcome(
            required_evidence=("source_ledger",),
            evidence_records=(_source_record(),),
            ledger_snapshot=_todos(("t1", "completed"), ("t2", "in_progress")),
        )
        assert outcome == "done"

    def test_pre_judge_evidence_blocked_routes_pause(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import magi_agent.runtime.goal_loop_evidence as resolver_mod

        def _fake_eval(*_a: object, **_k: object) -> EvidenceVerdict:
            return "unverifiable"

        monkeypatch.setattr(resolver_mod, "evaluate_required_evidence", _fake_eval)
        outcome = resolve_pre_judge_outcome(
            required_evidence=("source_ledger",),
            evidence_records=(),
            ledger_snapshot=_todos(("t1", "completed")),
        )
        assert outcome == "pause"

    def test_all_complete_but_missing_evidence_not_done(self) -> None:
        # All todos completed does NOT override an unmet evidence requirement.
        outcome = resolve_pre_judge_outcome(
            required_evidence=("source_ledger",),
            evidence_records=(),
            ledger_snapshot=_todos(("t1", "completed"), ("t2", "completed")),
        )
        assert outcome == "continue"


# ---------------------------------------------------------------------------
# subsystem-A parity: goal_is_met derives its bool from the shared verdict
# ---------------------------------------------------------------------------


class TestGoalIsMetParity:
    def test_final_gate_status_literal_set_is_pinned(self) -> None:
        # Guard the "fail is not a real status" assumption against a future
        # Literal change (the old goal_is_met defended status in ("blocked",
        # "fail") even though "fail" is unreachable).
        from typing import get_args

        from magi_agent.evidence.final_output_gate import FinalGateStatus

        assert set(get_args(FinalGateStatus)) == {
            "passed",
            "repair_required",
            "insufficient_evidence",
            "blocked",
            "skipped",
        }

    def test_goal_is_met_true_when_satisfied(self) -> None:
        from magi_agent.runtime.goal_nudge import GoalNudge, goal_is_met

        nudge = GoalNudge(goal="g", required_evidence=("source_ledger",))
        assert goal_is_met(nudge, evidence_records=(_source_record(),)) is True

    def test_goal_is_met_false_when_missing(self) -> None:
        from magi_agent.runtime.goal_nudge import GoalNudge, goal_is_met

        nudge = GoalNudge(goal="g", required_evidence=("source_ledger",))
        assert goal_is_met(nudge, evidence_records=()) is False

    def test_goal_is_met_false_when_no_required_evidence(self) -> None:
        from magi_agent.runtime.goal_nudge import GoalNudge, goal_is_met

        nudge = GoalNudge(goal="g")
        assert goal_is_met(nudge, evidence_records=()) is False
