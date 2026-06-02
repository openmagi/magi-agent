from __future__ import annotations

from openmagi_core_agent.runtime.reliability_budget import (
    ReliabilityBudgetPolicy,
    ReliabilityBudgetRequest,
)


def test_sota_escalation_is_bounded_and_records_reason() -> None:
    policy = ReliabilityBudgetPolicy(maxSotaEscalations=1, maxTotalCostUsd=0.10)

    first = policy.reserve(
        ReliabilityBudgetRequest(
            phase="final_verification",
            requestedTier="sota",
            reason="validator_failed_twice",
            estimatedCostUsd=0.04,
        )
    )
    second = policy.reserve(
        ReliabilityBudgetRequest(
            phase="high_risk_review",
            requestedTier="sota",
            reason="high_risk_domain",
            estimatedCostUsd=0.04,
        )
    )

    assert first.status == "reserved"
    assert second.status == "denied"
    assert second.fallback_action in {"ask_user", "stop", "fallback_to_typescript"}


def test_cheap_standard_total_retry_and_wall_time_caps_are_enforced() -> None:
    policy = ReliabilityBudgetPolicy(
        maxCheapCalls=1,
        maxStandardCalls=1,
        maxTotalCostUsd=0.05,
        maxRetries=1,
        maxWallTimeMs=100,
    )

    cheap = policy.reserve(
        ReliabilityBudgetRequest(
            phase="intent_classification",
            requestedTier="cheap",
            reason="cheap",
            estimatedCostUsd=0.01,
        )
    )
    cheap_over = policy.reserve(
        ReliabilityBudgetRequest(
            phase="source_extraction",
            requestedTier="cheap",
            reason="cheap",
            estimatedCostUsd=0.01,
        )
    )
    retry_over = policy.reserve(
        ReliabilityBudgetRequest(
            phase="source_extraction",
            requestedTier="standard",
            reason="retry",
            estimatedCostUsd=0.01,
            retryCount=2,
        )
    )
    wall_over = policy.reserve(
        ReliabilityBudgetRequest(
            phase="source_extraction",
            requestedTier="standard",
            reason="slow",
            estimatedCostUsd=0.01,
            elapsedMs=101,
        )
    )

    assert cheap.status == "reserved"
    assert cheap_over.status == "denied"
    assert cheap_over.reason_code == "cheap_call_cap_exceeded"
    assert retry_over.reason_code == "retry_cap_exceeded"
    assert wall_over.reason_code == "wall_time_cap_exceeded"


def test_total_cost_cap_and_typescript_fallback_accounting() -> None:
    policy = ReliabilityBudgetPolicy(maxTotalCostUsd=0.05)

    reserved = policy.reserve(
        ReliabilityBudgetRequest(
            phase="source_extraction",
            requestedTier="standard",
            reason="first",
            estimatedCostUsd=0.04,
        )
    )
    denied = policy.reserve(
        ReliabilityBudgetRequest(
            phase="final_verification",
            requestedTier="standard",
            reason="second",
            estimatedCostUsd=0.02,
            fallbackToTypeScript=True,
        )
    )

    assert reserved.status == "reserved"
    assert denied.status == "denied"
    assert denied.reason_code == "total_cost_cap_exceeded"
    assert denied.fallback_action == "fallback_to_typescript"
    assert policy.ledger().fallback_to_typescript_count == 1
