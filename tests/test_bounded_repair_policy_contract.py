from __future__ import annotations

from magi_agent.harness.repair_policy import RepairPlan, next_repair_action


def test_repair_plan_advances_through_named_actions() -> None:
    plan = RepairPlan.model_validate(
        {
            "planId": "research.repair",
            "maxAttempts": 3,
            "actions": [
                "removeUnsupportedClaims",
                "searchMoreSources",
                "abstain",
            ],
        }
    )

    first = next_repair_action(plan, attempt_index=0)
    second = next_repair_action(plan, attempt_index=1)
    third = next_repair_action(plan, attempt_index=2)

    assert first.action == "removeUnsupportedClaims"
    assert second.action == "searchMoreSources"
    assert third.action == "abstain"


def test_repair_plan_blocks_after_max_attempts() -> None:
    plan = RepairPlan.model_validate(
        {
            "planId": "backoffice.repair",
            "maxAttempts": 2,
            "actions": ["rerunCalculation", "askUserForPolicy"],
        }
    )

    decision = next_repair_action(plan, attempt_index=2)

    assert decision.action == "block"
    assert "repair_attempt_limit_exceeded" in decision.reason_codes


def test_empty_or_unbounded_repair_plan_is_rejected() -> None:
    try:
        RepairPlan.model_validate(
            {
                "planId": "bad",
                "maxAttempts": 99,
                "actions": [],
            }
        )
    except Exception as exc:
        assert "maxAttempts" in str(exc) or "actions" in str(exc)
    else:
        raise AssertionError("invalid repair plan must fail validation")


def test_zero_attempt_plan_blocks_immediately() -> None:
    plan = RepairPlan.model_validate(
        {
            "planId": "block.only",
            "maxAttempts": 0,
            "actions": [],
        }
    )

    decision = next_repair_action(plan, attempt_index=0)

    assert decision.action == "block"
    assert "repair_attempt_limit_exceeded" in decision.reason_codes
