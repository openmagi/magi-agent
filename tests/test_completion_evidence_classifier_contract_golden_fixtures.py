from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "completion_evidence_classifier"
    / "policy_matrix.json"
)


EXPECTED_CASE_ORDER = (
    "file_edit_is_work_not_verification",
    "bash_npm_test_is_verification_metadata_only",
    "bash_failed_npm_test_is_not_verification",
    "bash_pnpm_test_and_build_is_verification_metadata_only",
    "bash_npm_run_build_is_verification_metadata_only",
    "file_deliver_attachment_marker_is_delivery_work_only",
    "file_deliver_provider_message_receipt_is_delivery_work_only",
    "file_deliver_kb_write_receipt_is_delivery_work_only",
    "file_deliver_without_delivery_ack_is_not_work",
    "document_preview_is_document_verification",
    "web_search_is_source_verification",
    "clock_is_exactness_verification",
    "calculation_is_exactness_verification",
    "transcript_call_result_pair_preserves_tool_metadata",
)

FORBIDDEN_RUNTIME_TOKENS = (
    "/data/bots",
    "/workspace",
    "/var/lib/kubelet",
    "Bearer ",
    "ghp_",
    "sk-",
    "SUPABASE_SERVICE_ROLE_KEY",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
    "Runner.run",
    '"hookBusExecuted": true',
    '"toolHostDispatched": true',
    '"adkRunnerInvoked": true',
    '"shellExecuted": true',
    '"gitExecuted": true',
    '"testExecuted": true',
    '"fileMutated": true',
    '"routeAttached": true',
    '"apiAttached": true',
    '"dashboardAttached": true',
    '"productionStorageWritten": true',
    '"evidenceBlockEnabled": true',
    '"finalAnswerBlocked": true',
    '"userVisibleOutput": true',
    '"artifactAttached": true',
    '"attachmentWritten": true',
)

FORBIDDEN_RUNTIME_FLAG_KEYS = (
    "adkRunnerInvoked",
    "hookBusExecuted",
    "toolHostDispatched",
    "finalAnswerBlocked",
    "adkFunctionToolInvoked",
    "adkFunctionResponseAttached",
    "adkArtifactServiceCalled",
    "shellOrGitOrTestExecuted",
    "shellExecuted",
    "gitExecuted",
    "testExecuted",
    "fileMutated",
    "routeOrApiOrDashboardAttached",
    "productionStorageWritten",
    "evidenceBlockEnabled",
    "userVisibleOutputEmitted",
)


def _load_fixture() -> dict[str, Any]:
    with FIXTURE.open() as fixture_file:
        payload = json.load(fixture_file)
    assert isinstance(payload, dict)
    return payload


def _case_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = payload["cases"]
    assert isinstance(cases, list)
    mapped = {case["caseId"]: case for case in cases}
    assert len(cases) == len(mapped)
    assert len(cases) == len(EXPECTED_CASE_ORDER)
    assert tuple(mapped) == EXPECTED_CASE_ORDER
    return mapped


def _assert_metadata_only_boundary(payload: dict[str, Any]) -> None:
    assert payload["localDiagnostic"] is True
    assert payload["metadataOnly"] is True
    assert payload["noRuntimeExecution"] is True
    assert payload["noHookBusExecution"] is True
    assert payload["noToolHostDispatch"] is True
    assert payload["noFinalAnswerBlocking"] is True
    assert payload["noAdkRunner"] is True
    assert payload["noShellGitTestFileMutationRuntime"] is True
    assert payload["noRouteApiDashboard"] is True
    assert payload["noProductionStorage"] is True
    assert payload["noEvidenceBlockMode"] is True
    assert payload["noUserVisibleOutput"] is True
    assert payload["attachmentFlags"]
    assert set(payload["attachmentFlags"].values()) == {False}

    for key in FORBIDDEN_RUNTIME_FLAG_KEYS:
        assert payload.get(key) is not True

    serialized = json.dumps(payload, sort_keys=True)
    for token in FORBIDDEN_RUNTIME_TOKENS:
        assert token not in serialized


def _assert_case(
    case: dict[str, Any],
    *,
    tool_name: str,
    is_work_evidence: bool,
    is_verification_evidence: bool,
    evidence_role: str,
    verification_kind: str | None,
    delivery_ack: str | None = None,
) -> None:
    assert case["toolName"] == tool_name
    assert case["localDiagnostic"] is True
    assert case["metadataOnly"] is True
    assert case["attachedToRuntime"] is False
    assert case["isWorkEvidence"] is is_work_evidence
    assert case["isVerificationEvidence"] is is_verification_evidence
    assert case["evidenceRole"] == evidence_role
    assert case["verificationKind"] == verification_kind
    assert case["deliveryAck"] == delivery_ack


def test_completion_evidence_classifier_fixture_records_ts_same_turn_policy_matrix() -> None:
    payload = _load_fixture()
    cases = _case_map(payload)

    assert payload["fixtureId"] == "completion_evidence_classifier_matrix_0001"
    assert payload["scope"] == "ts_verification_evidence_recorded_policy"
    assert payload["adkFirstNotes"] == {
        "futureProducers": "ADK FunctionTool/FunctionResponse events only after ToolHost approval",
        "futureGate": "OpenMagi dev-coding validator/eval metadata around ADK events",
        "futureArtifactDelivery": "ADK ArtifactService plus OpenMagi receipt policy",
        "attachmentBehavior": "none",
    }
    _assert_metadata_only_boundary(payload)

    _assert_case(
        cases["file_edit_is_work_not_verification"],
        tool_name="FileEdit",
        is_work_evidence=True,
        is_verification_evidence=False,
        evidence_role="work",
        verification_kind=None,
    )

    for case_id, command in (
        ("bash_npm_test_is_verification_metadata_only", "npm test"),
        ("bash_pnpm_test_and_build_is_verification_metadata_only", "pnpm test && npm run build"),
        ("bash_npm_run_build_is_verification_metadata_only", "npm run build"),
    ):
        case = cases[case_id]
        _assert_case(
            case,
            tool_name="Bash",
            is_work_evidence=False,
            is_verification_evidence=True,
            evidence_role="verification_metadata",
            verification_kind="coding",
        )
        assert case["command"] == command

    failed_test = cases["bash_failed_npm_test_is_not_verification"]
    _assert_case(
        failed_test,
        tool_name="Bash",
        is_work_evidence=False,
        is_verification_evidence=False,
        evidence_role="ignored_metadata",
        verification_kind=None,
    )
    assert failed_test["command"] == "npm test"
    assert failed_test["inputMetadata"] == {
        "exitCode": 1,
        "commandKind": "test",
        "toolResultStatus": "error",
    }

    for case_id, delivery_ack in (
        ("file_deliver_attachment_marker_is_delivery_work_only", "attachment_marker"),
        ("file_deliver_provider_message_receipt_is_delivery_work_only", "provider_message_receipt"),
        ("file_deliver_kb_write_receipt_is_delivery_work_only", "kb_write_receipt"),
    ):
        _assert_case(
            cases[case_id],
            tool_name="FileDeliver",
            is_work_evidence=True,
            is_verification_evidence=False,
            evidence_role="delivery_work",
            verification_kind=None,
            delivery_ack=delivery_ack,
        )

    _assert_case(
        cases["file_deliver_without_delivery_ack_is_not_work"],
        tool_name="FileDeliver",
        is_work_evidence=False,
        is_verification_evidence=False,
        evidence_role="ignored_metadata",
        verification_kind=None,
    )

    for case_id, tool_name in (
        ("document_preview_is_document_verification", "DocumentPreview"),
        ("web_search_is_source_verification", "WebSearch"),
        ("clock_is_exactness_verification", "Clock"),
        ("calculation_is_exactness_verification", "Calculation"),
    ):
        expected_kind = {
            "DocumentPreview": "document",
            "WebSearch": "source",
            "Clock": "exactness",
            "Calculation": "exactness",
        }[tool_name]
        _assert_case(
            cases[case_id],
            tool_name=tool_name,
            is_work_evidence=False,
            is_verification_evidence=True,
            evidence_role="verification_metadata",
            verification_kind=expected_kind,
        )

    transcript = cases["transcript_call_result_pair_preserves_tool_metadata"]
    _assert_case(
        transcript,
        tool_name="web-search",
        is_work_evidence=False,
        is_verification_evidence=True,
        evidence_role="verification_metadata",
        verification_kind="source",
    )
    assert transcript["transcriptPair"] == {
        "callEventType": "tool_call",
        "resultEventType": "tool_result",
        "callId": "call_src_001",
        "resultForCallId": "call_src_001",
    }
    assert transcript["classifierInputMetadata"] == {
        "toolName": "web-search",
        "normalizedToolName": "WebSearch",
        "operation": "search",
        "provider": "recorded-ts-transcript",
        "queryPreview": "OpenMagi ADK artifact delivery policy",
    }
