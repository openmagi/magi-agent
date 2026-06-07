"""Tests for PR4 — GoalNudge runtime primitive (TDD, written first).

Tests cover:
- GoalNudge dataclass defaults and construction
- build_nudge_message text for both modes ("goal" and "grind")
- goal_is_met: False when required_evidence empty (rely on self-check)
- goal_is_met: True when required evidence is present in ledger records
- goal_is_met: False when required evidence is missing from ledger records
"""

from __future__ import annotations

import pytest

from magi_agent.runtime.goal_nudge import GoalNudge, build_nudge_message, goal_is_met


# ---------------------------------------------------------------------------
# GoalNudge dataclass
# ---------------------------------------------------------------------------


class TestGoalNudgeDefaults:
    def test_required_field_goal(self) -> None:
        nudge = GoalNudge(goal="write tests")
        assert nudge.goal == "write tests"

    def test_default_mode_is_goal(self) -> None:
        nudge = GoalNudge(goal="x")
        assert nudge.mode == "goal"

    def test_default_max_nudges(self) -> None:
        nudge = GoalNudge(goal="x")
        assert nudge.max_nudges == 3

    def test_default_required_evidence_empty(self) -> None:
        nudge = GoalNudge(goal="x")
        assert nudge.required_evidence == ()

    def test_default_domain(self) -> None:
        nudge = GoalNudge(goal="x")
        assert nudge.domain == "general"

    def test_grind_mode(self) -> None:
        nudge = GoalNudge(goal="x", mode="grind")
        assert nudge.mode == "grind"

    def test_custom_max_nudges(self) -> None:
        nudge = GoalNudge(goal="x", max_nudges=5)
        assert nudge.max_nudges == 5

    def test_custom_required_evidence(self) -> None:
        nudge = GoalNudge(goal="x", required_evidence=("source_ledger",))
        assert nudge.required_evidence == ("source_ledger",)

    def test_is_frozen(self) -> None:
        nudge = GoalNudge(goal="x")
        with pytest.raises((AttributeError, TypeError)):
            nudge.goal = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_nudge_message
# ---------------------------------------------------------------------------


class TestBuildNudgeMessage:
    def test_goal_mode_contains_goal_text(self) -> None:
        nudge = GoalNudge(goal="complete the feature", mode="goal")
        msg = build_nudge_message(nudge)
        assert "complete the feature" in msg

    def test_goal_mode_asks_to_check(self) -> None:
        nudge = GoalNudge(goal="x", mode="goal")
        msg = build_nudge_message(nudge)
        # Must contain the verification-oriented phrasing
        assert "check" in msg.lower() or "goal" in msg.lower()

    def test_grind_mode_contains_goal_text(self) -> None:
        nudge = GoalNudge(goal="write all tests", mode="grind")
        msg = build_nudge_message(nudge)
        assert "write all tests" in msg

    def test_grind_mode_asks_to_keep_working(self) -> None:
        nudge = GoalNudge(goal="x", mode="grind")
        msg = build_nudge_message(nudge)
        assert "keep working" in msg.lower() or "continue" in msg.lower()

    def test_goal_mode_message_differs_from_grind(self) -> None:
        goal_msg = build_nudge_message(GoalNudge(goal="x", mode="goal"))
        grind_msg = build_nudge_message(GoalNudge(goal="x", mode="grind"))
        assert goal_msg != grind_msg

    def test_goal_mode_exact_prefix(self) -> None:
        nudge = GoalNudge(goal="ship PR4", mode="goal")
        msg = build_nudge_message(nudge)
        assert msg.startswith("Before finishing")

    def test_grind_mode_exact_prefix(self) -> None:
        nudge = GoalNudge(goal="ship PR4", mode="grind")
        msg = build_nudge_message(nudge)
        assert msg.startswith("Keep working")


# ---------------------------------------------------------------------------
# goal_is_met
# ---------------------------------------------------------------------------


class TestGoalIsMet:
    def test_no_evidence_declared_always_false(self) -> None:
        """When required_evidence is empty, return False (rely on self-check turn)."""
        nudge = GoalNudge(goal="x", required_evidence=())
        assert goal_is_met(nudge, evidence_records=[]) is False

    def test_no_evidence_declared_with_records_still_false(self) -> None:
        """Even with records present, empty required_evidence returns False."""
        nudge = GoalNudge(goal="x", required_evidence=())
        records = [{"type": "SourceInspection", "sourceRef": "some-ref", "evidenceRef": "ev:001"}]
        assert goal_is_met(nudge, evidence_records=records) is False

    def test_required_evidence_present_returns_true(self) -> None:
        """When required_evidence is declared and the evidence is present, return True.

        FinalOutputGate.evaluate with enabled=True + localEvaluationEnabled=True checks
        required_evidence against evidenceRecords. source_ledger is satisfied when
        a SourceInspection record with a valid sourceRef is present.
        """
        nudge = GoalNudge(goal="research done", required_evidence=("source_ledger",))
        # A SourceInspection record satisfies "source_ledger"
        records = [
            {
                "type": "SourceInspection",
                "sourceRef": "web:example.com",  # must pass _is_public_ref
                "evidenceRef": "ev:0001:evidence_record",
            }
        ]
        result = goal_is_met(nudge, evidence_records=records)
        assert result is True

    def test_required_evidence_missing_returns_false(self) -> None:
        """When required evidence is declared but missing, return False."""
        nudge = GoalNudge(goal="research done", required_evidence=("source_ledger",))
        # No records → missing
        assert goal_is_met(nudge, evidence_records=[]) is False

    def test_required_evidence_wrong_type_returns_false(self) -> None:
        """Records of wrong type don't satisfy the requirement."""
        nudge = GoalNudge(goal="x", required_evidence=("source_ledger",))
        # Calculation record doesn't satisfy source_ledger
        records = [{"type": "Calculation", "evidenceRef": "ev:001"}]
        assert goal_is_met(nudge, evidence_records=records) is False

    def test_required_evidence_calculation_satisfied(self) -> None:
        """calculation_evidence is satisfied by a Calculation record."""
        nudge = GoalNudge(goal="verify numbers", required_evidence=("calculation_evidence",))
        records = [{"type": "Calculation", "evidenceRef": "ev:calc:001"}]
        result = goal_is_met(nudge, evidence_records=records)
        assert result is True
