"""PR6: Bounded Coding Repair Loop tests.

Tests cover:
- One repair pass triggered by a failing test
- Max repair cap enforcement
- Missing evidence after max attempts produces ask_user/abstain
- Repair success (passing test evidence) yields project_success
- Event projection for repair decisions is public-safe
"""
from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from magi_agent.coding.repair_loop import (
    CodingRepairDecision,
    CodingRepairLoopConfig,
    CodingRepairLoopResult,
    CodingRepairLoopState,
    CodingRepairReasonCode,
    build_repair_continuation_message,
    coding_repair_loop_enabled,
    evaluate_repair_decision,
    project_repair_decision_event,
    repair_max_attempts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _failing_test_evidence() -> dict[str, object]:
    return {
        "type": "TestRun",
        "status": "failed",
        "observedAt": 1000.0,
        "source": {
            "kind": "tool_trace",
        },
        "fields": {
            "command": "pytest tests/ -q",
            "exitCode": 1,
        },
        "preview": "3 failed, 7 passed",
    }


def _passing_test_evidence() -> dict[str, object]:
    return {
        "type": "TestRun",
        "status": "ok",
        "observedAt": 2000.0,
        "source": {
            "kind": "tool_trace",
        },
        "fields": {
            "command": "pytest tests/ -q",
            "exitCode": 0,
        },
        "preview": "10 passed",
    }


def _diff_evidence() -> dict[str, object]:
    return {
        "type": "GitDiff",
        "status": "ok",
        "observedAt": 1500.0,
        "source": {
            "kind": "tool_trace",
        },
        "fields": {
            "changedFiles": ("src/foo.py",),
        },
        "preview": "1 file changed",
    }


# ---------------------------------------------------------------------------
# Test: One repair pass triggered by a failing test
# ---------------------------------------------------------------------------

class TestOneRepairPass:
    def test_failing_test_triggers_continue_repair(self) -> None:
        config = CodingRepairLoopConfig(
            enabled=True,
            maxAttempts=3,
        )
        state = CodingRepairLoopState(
            attemptCount=0,
            evidenceRefs=(),
            reasonCodes=(),
        )
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_failing_test_evidence(),
        )
        assert decision.action == "continue_repair"
        assert decision.attempt_count == 1
        assert "test_failure_detected" in decision.reason_codes

    def test_first_attempt_records_evidence_digest(self) -> None:
        config = CodingRepairLoopConfig(enabled=True, maxAttempts=3)
        state = CodingRepairLoopState(
            attemptCount=0,
            evidenceRefs=(),
            reasonCodes=(),
        )
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_failing_test_evidence(),
        )
        assert len(decision.evidence_refs) >= 1
        # Evidence refs must be sha256 digests, not raw paths
        for ref in decision.evidence_refs:
            assert ref.startswith("sha256:") or ref.startswith("evidence:")


# ---------------------------------------------------------------------------
# Test: Max repair cap enforcement
# ---------------------------------------------------------------------------

class TestMaxRepairCap:
    def test_at_max_attempts_returns_ask_user(self) -> None:
        config = CodingRepairLoopConfig(enabled=True, maxAttempts=2)
        state = CodingRepairLoopState(
            attemptCount=2,
            evidenceRefs=("evidence:attempt-1", "evidence:attempt-2"),
            reasonCodes=("test_failure_detected", "test_failure_detected"),
        )
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_failing_test_evidence(),
        )
        assert decision.action in ("ask_user", "abstain")
        assert "max_attempts_reached" in decision.reason_codes

    def test_exceeds_max_attempts_never_returns_continue_repair(self) -> None:
        config = CodingRepairLoopConfig(enabled=True, maxAttempts=1)
        state = CodingRepairLoopState(
            attemptCount=5,
            evidenceRefs=("evidence:1",) * 5,
            reasonCodes=("test_failure_detected",) * 5,
        )
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_failing_test_evidence(),
        )
        assert decision.action != "continue_repair"

    def test_max_attempts_config_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            CodingRepairLoopConfig(enabled=True, maxAttempts=100)

    def test_max_attempts_config_lower_bound(self) -> None:
        config = CodingRepairLoopConfig(enabled=True, maxAttempts=0)
        state = CodingRepairLoopState(attemptCount=0, evidenceRefs=(), reasonCodes=())
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_failing_test_evidence(),
        )
        assert decision.action in ("ask_user", "abstain")


# ---------------------------------------------------------------------------
# Test: Missing evidence after max attempts
# ---------------------------------------------------------------------------

class TestMissingEvidenceAtCap:
    def test_no_passing_test_at_cap_produces_ask_user(self) -> None:
        config = CodingRepairLoopConfig(enabled=True, maxAttempts=2)
        state = CodingRepairLoopState(
            attemptCount=2,
            evidenceRefs=("evidence:1", "evidence:2"),
            reasonCodes=("test_failure_detected", "test_failure_detected"),
        )
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=None,  # no evidence at all
        )
        assert decision.action in ("ask_user", "abstain")
        assert "max_attempts_reached" in decision.reason_codes
        assert "missing_evidence" in decision.reason_codes

    def test_no_evidence_never_claims_success(self) -> None:
        config = CodingRepairLoopConfig(enabled=True, maxAttempts=3)
        state = CodingRepairLoopState(
            attemptCount=3,
            evidenceRefs=("evidence:1", "evidence:2", "evidence:3"),
            reasonCodes=("test_failure_detected",) * 3,
        )
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_failing_test_evidence(),
        )
        assert decision.action != "project_success"


# ---------------------------------------------------------------------------
# Test: Repair success
# ---------------------------------------------------------------------------

class TestRepairSuccess:
    def test_passing_test_yields_project_success(self) -> None:
        config = CodingRepairLoopConfig(enabled=True, maxAttempts=3)
        state = CodingRepairLoopState(
            attemptCount=1,
            evidenceRefs=("evidence:attempt-1",),
            reasonCodes=("test_failure_detected",),
        )
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_passing_test_evidence(),
        )
        assert decision.action == "project_success"
        assert "test_pass_detected" in decision.reason_codes

    def test_passing_test_at_first_attempt_is_success(self) -> None:
        config = CodingRepairLoopConfig(enabled=True, maxAttempts=3)
        state = CodingRepairLoopState(
            attemptCount=0,
            evidenceRefs=(),
            reasonCodes=(),
        )
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_passing_test_evidence(),
        )
        assert decision.action == "project_success"


# ---------------------------------------------------------------------------
# Test: Default-off contract
# ---------------------------------------------------------------------------

class TestDefaultOff:
    def test_disabled_config_returns_abstain(self) -> None:
        config = CodingRepairLoopConfig(enabled=False, maxAttempts=3)
        state = CodingRepairLoopState(attemptCount=0, evidenceRefs=(), reasonCodes=())
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_failing_test_evidence(),
        )
        assert decision.action == "abstain"
        assert "repair_loop_disabled" in decision.reason_codes

    def test_default_config_is_disabled(self) -> None:
        config = CodingRepairLoopConfig()
        assert config.enabled is False


# ---------------------------------------------------------------------------
# Test: Live bounded retry enablement helpers
# ---------------------------------------------------------------------------

class TestLiveRepairHelpers:
    def test_live_retry_defaults_on_for_full_local_profile(self) -> None:
        assert coding_repair_loop_enabled({}) is True
        assert coding_repair_loop_enabled({"MAGI_RUNTIME_PROFILE": "full"}) is True

    def test_safe_profiles_keep_projection_only_behavior(self) -> None:
        assert coding_repair_loop_enabled({"MAGI_RUNTIME_PROFILE": "safe"}) is False
        assert coding_repair_loop_enabled({"MAGI_RUNTIME_PROFILE": "minimal"}) is False
        assert coding_repair_loop_enabled({"MAGI_RUNTIME_PROFILE": "off"}) is False

    def test_explicit_env_overrides_profile(self) -> None:
        assert coding_repair_loop_enabled(
            {
                "MAGI_RUNTIME_PROFILE": "safe",
                "MAGI_CODING_REPAIR_LOOP_ENABLED": "1",
            }
        ) is True
        assert coding_repair_loop_enabled(
            {
                "MAGI_RUNTIME_PROFILE": "full",
                "MAGI_CODING_REPAIR_LOOP_ENABLED": "0",
            }
        ) is False
        assert coding_repair_loop_enabled(
            {"MAGI_CODING_REPAIR_LOOP_ENABLED": "definitely"}
        ) is False

    def test_repair_max_attempts_is_bounded(self) -> None:
        assert repair_max_attempts({}) == 3
        assert repair_max_attempts({"maxAttempts": 2}) == 2
        assert repair_max_attempts({"max_attempts": 4}) == 4
        assert repair_max_attempts({"maxAttempts": 99}) == 10
        assert repair_max_attempts({"maxAttempts": -5}) == 0

    def test_repair_continuation_message_hashes_non_public_refs(self) -> None:
        message = build_repair_continuation_message(
            missing_evidence=("evidence:git-diff", "/Users/kevin/secret.patch"),
            missing_validators=("verifier:dev-coding:test-evidence",),
            attempt=1,
            max_attempts=3,
        )
        assert "evidence:git-diff" in message
        assert "verifier:dev-coding:test-evidence" in message
        assert "/Users/" not in message
        assert "secret.patch" not in message
        assert "ref:sha256:" in message


# ---------------------------------------------------------------------------
# Test: productionWorkspaceMutationAllowed is always False
# ---------------------------------------------------------------------------

class TestProductionSafety:
    def test_result_production_mutation_always_false(self) -> None:
        config = CodingRepairLoopConfig(enabled=True, maxAttempts=3)
        state = CodingRepairLoopState(attemptCount=0, evidenceRefs=(), reasonCodes=())
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_failing_test_evidence(),
        )
        result = CodingRepairLoopResult(
            decision=decision,
            state=CodingRepairLoopState(
                attemptCount=decision.attempt_count,
                evidenceRefs=decision.evidence_refs,
                reasonCodes=decision.reason_codes,
            ),
            productionWorkspaceMutationAllowed=False,
        )
        assert result.production_workspace_mutation_allowed is False

    def test_result_rejects_production_mutation_true(self) -> None:
        config = CodingRepairLoopConfig(enabled=True, maxAttempts=3)
        state = CodingRepairLoopState(attemptCount=0, evidenceRefs=(), reasonCodes=())
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_failing_test_evidence(),
        )
        with pytest.raises(ValidationError):
            CodingRepairLoopResult(
                decision=decision,
                state=CodingRepairLoopState(
                    attemptCount=decision.attempt_count,
                    evidenceRefs=decision.evidence_refs,
                    reasonCodes=decision.reason_codes,
                ),
                productionWorkspaceMutationAllowed=True,
            )


# ---------------------------------------------------------------------------
# Test: Public-safe event projection
# ---------------------------------------------------------------------------

class TestEventProjection:
    def test_projection_contains_no_raw_paths(self) -> None:
        config = CodingRepairLoopConfig(enabled=True, maxAttempts=3)
        state = CodingRepairLoopState(attemptCount=0, evidenceRefs=(), reasonCodes=())
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_failing_test_evidence(),
        )
        event = project_repair_decision_event(decision)
        event_str = str(event)
        # No raw file paths
        assert "/home/" not in event_str
        assert "/Users/" not in event_str
        assert "/workspace/" not in event_str
        # No auth tokens
        assert "Bearer" not in event_str
        assert "token" not in event_str.lower() or "auth" not in event_str.lower()

    def test_projection_includes_action_and_reason(self) -> None:
        config = CodingRepairLoopConfig(enabled=True, maxAttempts=3)
        state = CodingRepairLoopState(attemptCount=1, evidenceRefs=("evidence:1",), reasonCodes=("test_failure_detected",))
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_failing_test_evidence(),
        )
        event = project_repair_decision_event(decision)
        assert event["type"] == "coding_repair_decision"
        assert event["action"] == decision.action
        assert "reasonCodes" in event

    def test_projection_uses_digest_for_evidence(self) -> None:
        config = CodingRepairLoopConfig(enabled=True, maxAttempts=3)
        state = CodingRepairLoopState(attemptCount=0, evidenceRefs=(), reasonCodes=())
        decision = evaluate_repair_decision(
            config=config,
            state=state,
            latest_test_evidence=_failing_test_evidence(),
        )
        event = project_repair_decision_event(decision)
        for ref in event.get("evidenceRefs", ()):
            assert ref.startswith("sha256:") or ref.startswith("evidence:")


# ---------------------------------------------------------------------------
# Test: CodingRepairLoopState round-trip
# ---------------------------------------------------------------------------

class TestStateRoundTrip:
    def test_state_serialization_roundtrip(self) -> None:
        state = CodingRepairLoopState(
            attemptCount=2,
            evidenceRefs=("sha256:abc123", "evidence:def456"),
            reasonCodes=("test_failure_detected", "test_failure_detected"),
        )
        dumped = state.model_dump(by_alias=True, mode="python")
        restored = CodingRepairLoopState.model_validate(dumped)
        assert restored == state
