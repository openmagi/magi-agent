from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.harness.self_debug import (
    ActionFingerprintInput,
    ChangedConditionMetadata,
    FailureSafetyMetadata,
    RetryDecisionMetadata,
    RetryFailureMetadata,
    RetryStateMetadata,
    action_fingerprint,
    build_retry_state,
    decide_retry_metadata,
    self_debug_policy_defaults,
    stable_failure_class_catalog,
)


EXPECTED_FAILURE_CLASSES = (
    "tool_input_validation",
    "permission_denial",
    "transient_network",
    "context_overflow",
    "empty_or_truncated_model_response",
    "unknown_tool_call",
    "loop_detector_warning",
    "test_or_command_failure",
    "verifier_block",
    "delivery_failure",
)


def _action(**overrides: object) -> ActionFingerprintInput:
    payload: dict[str, object] = {
        "actionType": "tool_call",
        "toolName": "Bash",
        "observableInput": {"command": "pytest", "api_key": "sk-secret-one"},
        "evidenceTarget": "unit-tests",
        "permissionState": "granted",
        "modelPolicy": "default",
    }
    payload.update(overrides)
    return ActionFingerprintInput.model_validate(payload)


def _failure(**overrides: object) -> RetryFailureMetadata:
    payload: dict[str, object] = {
        "failureClass": "test_or_command_failure",
        "errorMessage": "pytest failed with Authorization: Bearer live-token",
        "structuredFields": {
            "exitCode": 1,
            "stderr": "api_key=sk-secret-value Authorization: Bearer live-token",
        },
    }
    payload.update(overrides)
    return RetryFailureMetadata.model_validate(payload)


def _state(**overrides: object) -> RetryStateMetadata:
    payload: dict[str, object] = {
        "sessionId": "session-1",
        "turnId": "turn-1",
        "runOn": "main",
        "agentRole": "coding",
        "spawnDepth": 0,
        "failure": _failure(),
        "failedActionFingerprint": action_fingerprint(_action()),
        "evidenceRefs": ("evidence:1",),
        "requiredNextAction": "collect failing test output before retrying",
        "attemptsRemaining": 1,
        "strategy": "different_tool",
        "changedCondition": ChangedConditionMetadata(changedPlan=True),
    }
    payload.update(overrides)
    return RetryStateMetadata.model_validate(payload)


def test_stable_failure_catalog_is_complete_read_only_and_default_off() -> None:
    catalog = stable_failure_class_catalog()
    defaults = self_debug_policy_defaults()

    assert catalog == EXPECTED_FAILURE_CLASSES
    assert isinstance(catalog, tuple)
    assert defaults.default_enabled is False
    assert defaults.traffic_attached is False
    assert defaults.retry_attached is False

    with pytest.raises(ValidationError):
        defaults.default_enabled = True  # type: ignore[misc]


def test_action_fingerprints_are_deterministic_and_ignore_secret_raw_values() -> None:
    first = _action(observableInput={"command": "curl", "api_key": "sk-secret-one"})
    second = _action(observableInput={"api_key": "sk-secret-two", "command": "curl"})

    assert action_fingerprint(first) == action_fingerprint(second)
    assert "sk-secret" not in first.public_fingerprint_material()
    assert "sk-secret" not in second.public_fingerprint_material()


def test_malformed_hidden_reasoning_action_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="hidden reasoning"):
        _action(observableInput={"command": "pytest", "hidden_reasoning": "private chain"})

    with pytest.raises(ValidationError):
        ActionFingerprintInput.model_validate(
            {
                "actionType": "tool_call",
                "toolName": "Bash",
                "observableInput": {"command": "pytest"},
                "rawHiddenThoughts": "not observable",
            }
        )


def test_retry_state_preserves_session_run_role_depth_and_metadata() -> None:
    state = _state(
        runOn="child",
        agentRole="research",
        spawnDepth=2,
        evidenceRefs=("evidence:1", "artifact:2"),
    )
    dumped = state.model_dump(by_alias=True)

    assert dumped["sessionId"] == "session-1"
    assert dumped["turnId"] == "turn-1"
    assert dumped["runOn"] == "child"
    assert dumped["agentRole"] == "research"
    assert dumped["spawnDepth"] == 2
    assert dumped["failure"]["failureClass"] == "test_or_command_failure"
    assert dumped["attemptsRemaining"] == 1
    assert dumped["requiredNextAction"] == "collect failing test output before retrying"
    assert dumped["strategy"] == "different_tool"
    assert dumped["evidenceRefs"] == ("evidence:1", "artifact:2")


def test_repeated_identical_failed_action_without_changed_condition_blocks_retry_metadata() -> None:
    state = _state(
        attemptsRemaining=2,
        strategy="same_tool",
        changedCondition=ChangedConditionMetadata(),
    )

    decision = decide_retry_metadata(state, next_action=_action())

    assert decision.decision == "block_retry"
    assert decision.retry_attached is False
    assert decision.blocking_attached is False
    assert "changed condition" in decision.reason


def test_changed_dimensions_allow_retry_and_are_recorded() -> None:
    state = _state(
        attemptsRemaining=1,
        strategy="different_tool",
        changedCondition=ChangedConditionMetadata(changedTool=True, changedEvidenceTarget=True),
    )

    decision = decide_retry_metadata(
        state,
        next_action=_action(toolName="Python", evidenceTarget="traceback"),
    )

    assert decision.decision == "allow_retry"
    assert decision.changed_condition.changed_dimensions == (
        "tool",
        "evidence_target",
    )


@pytest.mark.parametrize("failure_class", ("permission_denial", "verifier_block"))
def test_permission_denial_and_verifier_block_preserve_fail_closed_safety(
    failure_class: str,
) -> None:
    state = _state(
        failure=_failure(failureClass=failure_class),
        safety=FailureSafetyMetadata(
            hardSafety=True,
            approvalRequired=True,
            optional=False,
            failOpen=False,
            optOutAllowed=False,
        ),
    )

    assert state.safety.hard_safety is True
    assert state.safety.approval_required is True
    assert state.safety.fail_open is False
    assert state.safety.opt_out_allowed is False

    with pytest.raises(ValidationError):
        _state(
            failure=_failure(failureClass=failure_class),
            safety={"hardSafety": True, "optional": False, "failOpen": True},
        )


@pytest.mark.parametrize("failure_class", ("permission_denial", "verifier_block"))
@pytest.mark.parametrize(
    "safety",
    (
        {
            "hardSafety": False,
            "approvalRequired": True,
            "optional": False,
            "failOpen": False,
            "optOutAllowed": False,
        },
        {
            "hardSafety": False,
            "approvalRequired": False,
            "optional": False,
            "failOpen": False,
            "optOutAllowed": False,
        },
        {
            "hardSafety": True,
            "approvalRequired": True,
            "optional": True,
            "failOpen": False,
            "optOutAllowed": False,
        },
    ),
)
def test_permission_denial_and_verifier_block_reject_incomplete_hard_safety_metadata(
    failure_class: str,
    safety: dict[str, bool],
) -> None:
    with pytest.raises(ValidationError, match="hard-safety approval-required non-optional"):
        _state(
            failure=_failure(failureClass=failure_class),
            safety=safety,
        )


def test_transient_network_bounded_retry_and_exhausted_report_metadata() -> None:
    retry_state = build_retry_state(
        sessionId="session-1",
        turnId="turn-1",
        runOn="main",
        agentRole="general",
        spawnDepth=0,
        failure=_failure(failureClass="transient_network"),
        failedAction=_action(),
        attemptsRemaining=1,
        evidenceRefs=("net:timeout",),
    )
    retry_decision = decide_retry_metadata(retry_state, next_action=_action())

    assert retry_state.strategy == "same_tool"
    assert retry_decision.decision == "allow_retry"
    assert retry_decision.report_kind is None

    exhausted_state = build_retry_state(
        sessionId="session-1",
        turnId="turn-1",
        runOn="main",
        agentRole="general",
        spawnDepth=0,
        failure=_failure(failureClass="transient_network"),
        failedAction=_action(),
        attemptsRemaining=0,
    )
    exhausted_decision = decide_retry_metadata(exhausted_state, next_action=_action())

    assert exhausted_decision.decision == "report_failure"
    assert exhausted_decision.report_kind == "partial_failure_report"


def test_public_previews_and_reports_redact_and_truncate() -> None:
    state = _state(
        failure=_failure(
            errorMessage=(
                "Authorization: Bearer live-token api_key=sk-secret-value "
                + ("x" * 500)
            ),
            structuredFields={
                "env": "OPENAI_API_KEY=sk-secret-value",
                "github": "ghp_secretvalue",
            },
        )
    )
    decision = RetryDecisionMetadata(
        retryState=state,
        decision="block_retry",
        reason="Authorization: Bearer live-token api_key=sk-secret-value " + ("x" * 500),
    )
    dumped = json.dumps(decision.model_dump(by_alias=True), sort_keys=True)

    assert "live-token" not in dumped
    assert "sk-secret-value" not in dumped
    assert "ghp_secretvalue" not in dumped
    assert len(state.failure.public_error_preview) <= 400
    assert len(decision.public_report_preview) <= 400


def test_model_copy_rejects_attachment_flags_and_non_json_metadata() -> None:
    state = _state()

    assert state.traffic_attached is False
    assert state.execution_attached is False
    assert state.runner_attached is False
    assert state.route_attached is False
    assert state.retry_attached is False
    assert state.blocking_attached is False
    assert state.canary_attached is False

    for flag in (
        "trafficAttached",
        "executionAttached",
        "runnerAttached",
        "routeAttached",
        "retryAttached",
        "blockingAttached",
        "canaryAttached",
        "traffic_attached",
    ):
        with pytest.raises(ValidationError):
            state.model_copy(update={flag: True})

    with pytest.raises(ValidationError):
        _state(metadata={"bad": {object()}})

    with pytest.raises(ValidationError):
        state.model_copy(update={"metadata": {"bad": {1, 2, 3}}})


def test_nested_metadata_is_defensively_immutable() -> None:
    state = _state(metadata={"nested": {"value": "kept"}, "items": ["one"]})

    with pytest.raises(TypeError):
        state.metadata["nested"]["value"] = "mutated"  # type: ignore[index]

    with pytest.raises(TypeError):
        state.metadata["items"][0] = "mutated"  # type: ignore[index]


def test_self_debug_import_stays_adk_runner_runtime_and_route_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.harness.self_debug")
assert hasattr(module, "RetryStateMetadata")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"self_debug import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
