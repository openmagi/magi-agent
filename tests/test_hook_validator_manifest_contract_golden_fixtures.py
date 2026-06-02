from __future__ import annotations

import json
from pathlib import Path


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "hook_validator_manifest"
    / "policy_matrix.json"
)

EXPECTED_CASE_ORDER = (
    "adk_callback_mapping_all_points",
    "blocking_fail_closed_timeout",
    "blocking_fail_open_timeout",
    "non_blocking_observer_failure",
    "permission_ask_boundary",
    "if_rule_tool_filter_metadata",
    "if_rule_filtered_permission_no_control_request",
    "malformed_if_rule_skip_metadata",
    "protected_hook_manifest_downgrade",
    "external_hook_manifest_validation",
    "verifier_hard_safety_vs_audit",
)

ATTACHMENT_FLAGS = (
    "adkRunnerInvoked",
    "liveCallbackAttached",
    "liveHookBusExecuted",
    "liveValidatorExecuted",
    "toolHostDispatched",
    "routeOrApiAttached",
    "dashboardAttached",
    "deployOrCanaryAttached",
    "productionStorageWritten",
    "evidenceBlockEnabled",
)


def _expected_adk_mapping() -> dict[str, str]:
    from magi_agent.adk_bridge.callback_adapter import ADK_CALLBACK_HOOK_POINTS

    return {
        callback_name: hook_point.value
        for callback_name, hook_point in ADK_CALLBACK_HOOK_POINTS.items()
    }


def _load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _case_map(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    cases = payload["cases"]
    assert isinstance(cases, list)
    case_ids = [str(case["caseId"]) for case in cases]
    assert len(case_ids) == len(set(case_ids)), "hook fixture caseIds must be unique"
    return {str(case["caseId"]): case for case in cases}


def _assert_all_attachment_flags_false(container: dict[str, object]) -> None:
    flags = container["attachmentFlags"]
    assert isinstance(flags, dict)
    assert tuple(flags) == ATTACHMENT_FLAGS
    assert set(flags.values()) == {False}


def _assert_concrete_hook_point(value: object) -> None:
    from magi_agent.hooks.manifest import HookPoint

    assert value in {hook_point.value for hook_point in HookPoint}
    assert value != "custom"


def _assert_no_adk_callback_plugin_or_eval_execution(case: dict[str, object]) -> None:
    assert case["handlerExecuted"] is False
    assert case["liveHookBusExecuted"] is False
    assert case["adkCallbackExecuted"] is False
    assert case["adkPluginExecuted"] is False
    assert case["adkEvalExecuted"] is False


def test_hook_validator_manifest_fixture_covers_default_off_local_metadata_matrix() -> None:
    payload = _load_fixture()
    cases = _case_map(payload)

    assert payload["schemaVersion"] == "hookValidatorManifestFixture.v1"
    assert payload["fixtureId"] == "hook_validator_manifest_matrix_0001"
    assert payload["sourceRuntime"] == "typescript-core-agent"
    assert payload["recordingMode"] == "local_diagnostic_fixture"
    assert payload["localDiagnostic"] is True
    assert payload["metadataOnly"] is True
    assert payload["defaultOff"] is True
    assert tuple(cases) == EXPECTED_CASE_ORDER
    _assert_all_attachment_flags_false(payload)

    adk = cases["adk_callback_mapping_all_points"]
    assert adk["adkCallbackMapping"] == _expected_adk_mapping()
    assert adk["callbackProjectionStatus"] == "metadata_only"
    assert adk["nonContinueBoundary"] == "blocked_by_adapter_boundary"
    assert adk["blocking"] is True
    assert adk["failOpen"] is False
    assert adk["failClosed"] is True
    assert adk["timeoutMs"] == 5000

    fail_closed = cases["blocking_fail_closed_timeout"]
    assert fail_closed["hookPoint"] == "beforeToolUse"
    assert fail_closed["blocking"] is True
    assert fail_closed["failOpen"] is False
    assert fail_closed["failClosed"] is True
    assert fail_closed["timeoutMs"] == 1000
    assert fail_closed["timeoutBudgetOwner"] == "OpenMagi HookBus metadata"

    fail_open = cases["blocking_fail_open_timeout"]
    assert fail_open["hookPoint"] == "beforeLLMCall"
    assert fail_open["blocking"] is True
    assert fail_open["failOpen"] is True
    assert fail_open["failClosed"] is False
    assert fail_open["timeoutMs"] == 12000

    observer = cases["non_blocking_observer_failure"]
    assert observer["hookPoint"] == "afterToolUse"
    assert observer["blocking"] is False
    assert observer["failOpen"] is True
    assert observer["failClosed"] is False
    assert observer["observerFailureBehavior"] == "telemetry_failed_open"

    permission = cases["permission_ask_boundary"]
    assert permission["hookPoint"] == "beforeToolUse"
    assert permission["permissionDecision"] == "ask"
    assert permission["controlBoundaryOwner"] == "OpenMagi ControlRequest"
    assert permission["requiresControlRequest"] is True
    assert permission["liveAdkConfirmation"] is False

    if_filter = cases["if_rule_tool_filter_metadata"]
    assert if_filter["category"] == "if_rule_applicability_metadata"
    assert if_filter["hookPoint"] == "beforeToolUse"
    assert if_filter["hookName"] == "gitCommandObserver"
    assert if_filter["if"] == "Bash(git *)"
    assert if_filter["applicabilityStatus"] == "metadata_recorded_not_evaluated"
    assert if_filter["tsRuleSyntax"] == "Bash(<glob>)"
    assert if_filter["toolName"] == "Bash"
    assert if_filter["toolInputPattern"] == "git *"
    assert if_filter["requiresControlRequest"] is False
    assert if_filter["askUserInvoked"] is False
    assert if_filter["liveAdkConfirmation"] is False
    _assert_no_adk_callback_plugin_or_eval_execution(if_filter)

    filtered_permission = cases["if_rule_filtered_permission_no_control_request"]
    assert filtered_permission["category"] == "if_rule_applicability_metadata"
    assert filtered_permission["hookPoint"] == "beforeToolUse"
    assert filtered_permission["hookName"] == "gitPermissionGate"
    assert filtered_permission["if"] == "Bash(git *)"
    assert filtered_permission["applicabilityStatus"] == "filtered_out_by_if_metadata"
    assert filtered_permission["permissionDecision"] == "not_reached_filtered_by_if"
    assert filtered_permission["requiresControlRequest"] is False
    assert filtered_permission["controlRequestCreated"] is False
    assert filtered_permission["askUserInvoked"] is False
    assert filtered_permission["liveAdkConfirmation"] is False
    assert "controlBoundaryOwner" not in filtered_permission
    assert "controlRequestId" not in filtered_permission
    _assert_no_adk_callback_plugin_or_eval_execution(filtered_permission)

    malformed_if = cases["malformed_if_rule_skip_metadata"]
    assert malformed_if["category"] == "if_rule_applicability_metadata"
    assert malformed_if["hookPoint"] == "beforeToolUse"
    assert malformed_if["hookName"] == "emptyIfRuleObserver"
    assert malformed_if["if"] == ""
    assert malformed_if["applicabilityStatus"] == "skipped_malformed_if_rule"
    assert malformed_if["warningPosture"] == "warn_once_metadata"
    assert malformed_if["ifRuleMalformed"] is True
    assert malformed_if["ifRuleEvaluated"] is False
    assert malformed_if["requiresControlRequest"] is False
    assert malformed_if["askUserInvoked"] is False
    assert malformed_if["liveAdkConfirmation"] is False
    _assert_no_adk_callback_plugin_or_eval_execution(malformed_if)

    protected = cases["protected_hook_manifest_downgrade"]
    assert protected["protected"] is True
    assert protected["securityCritical"] is True
    assert protected["hardSafety"] is True
    assert protected["priority"] == 0
    assert protected["attemptedPriority"] == 100
    assert protected["blocking"] is True
    assert protected["attemptedBlocking"] is False
    assert protected["failOpen"] is False
    assert protected["attemptedFailOpen"] is True
    assert protected["failClosed"] is True
    assert protected["timeoutMs"] == 1000
    assert protected["attemptedTimeoutMs"] == 30000
    assert protected["optOut"] is False
    assert protected["attemptedOptOut"] is True
    assert protected["nonOptOut"] is True

    external = cases["external_hook_manifest_validation"]
    assert external["category"] == "external_manifest_validation"
    assert external["source"]["kind"] == "custom-plugin"
    assert external["source"]["package"] == "example.local.hooks"
    assert external["source"]["entrypoint"] == "artifact_observer"
    assert external["hookPoint"] == "onArtifactCreated"
    _assert_concrete_hook_point(external["hookPoint"])
    assert external["customPoint"] == "afterArtifactCreated"
    assert external["sourceMetadataValid"] is True
    assert external["loaderExecuted"] is False

    verifier = cases["verifier_hard_safety_vs_audit"]
    assert verifier["hardSafetyVerifier"] == {
        "verifierId": "security-policy-hard-safety",
        "defaultEnabled": True,
        "disabled": False,
        "blocking": True,
        "failOpen": False,
        "failClosed": True,
        "metadataOnly": True,
    }
    assert verifier["auditValidator"] == {
        "verifierId": "dev-coding-verification-audit",
        "defaultEnabled": False,
        "disabled": True,
        "blocking": False,
        "failOpen": True,
        "failClosed": False,
        "metadataOnly": True,
    }

    for case in cases.values():
        assert case["metadataOnly"] is True
        assert case["defaultOff"] is True
        _assert_all_attachment_flags_false(case)


def test_hook_validator_manifest_fixture_has_no_live_runtime_or_production_flags() -> None:
    payload = _load_fixture()
    dumped = json.dumps(payload, sort_keys=True)

    forbidden_fragments = (
        '"adkRunnerInvoked": true',
        '"liveCallbackAttached": true',
        '"liveHookBusExecuted": true',
        '"liveValidatorExecuted": true',
        '"handlerExecuted": true',
        '"adkCallbackExecuted": true',
        '"adkPluginExecuted": true',
        '"adkEvalExecuted": true',
        '"toolHostDispatched": true',
        '"askUserInvoked": true',
        '"routeOrApiAttached": true',
        '"dashboardAttached": true',
        '"deployOrCanaryAttached": true',
        '"productionStorageWritten": true',
        '"evidenceBlockEnabled": true',
        "Runner.run",
        "ToolHost.execute",
        "/api/",
        "vercel",
        "canary",
    )
    for fragment in forbidden_fragments:
        assert fragment not in dumped


def test_hook_validator_manifest_case_map_rejects_duplicate_case_ids_before_projection() -> None:
    payload = _load_fixture()
    cases = payload["cases"]
    assert isinstance(cases, list)
    duplicate = dict(cases[1])
    duplicate["caseId"] = cases[0]["caseId"]
    cases.append(duplicate)

    try:
        _case_map(payload)
    except AssertionError:
        return
    raise AssertionError("duplicate caseIds must be rejected before _case_map projection")
