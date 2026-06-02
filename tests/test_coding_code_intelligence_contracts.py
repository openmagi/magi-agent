from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.harness.coding.code_intelligence_contracts import (
    CodeActionProjection,
    CodeIntelligenceAuthorityFlags,
    CodeIntelligenceObservation,
    CodeIntelligenceReport,
    CodeIntelligenceSpan,
    build_code_intelligence_contract,
    project_code_intelligence_report,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = PYTHON_ROOT / "tests/fixtures/parity/coding_harness_consolidated_matrix.json"


def _span(file_ref: str = "file-ref:sha256:" + "1" * 64) -> dict[str, object]:
    return {
        "fileRef": file_ref,
        "startLine": 4,
        "startColumn": 2,
        "endLine": 4,
        "endColumn": 17,
        "sourceDigest": "sha256:" + "2" * 64,
    }


def _observation(operation: str, *, status: str = "projected") -> dict[str, object]:
    return {
        "operation": operation,
        "status": status,
        "span": _span(),
        "sourceMetadata": {
            "sourceRef": "source-ref:sha256:" + "3" * 64,
            "workspaceRef": "workspace-ref:sha256:" + "4" * 64,
            "languageId": "python",
            "providerRef": "provider-ref:metadata-only",
        },
        "resultDigest": "sha256:" + "5" * 64,
        "reasonCodes": (f"{operation}_metadata_projected",),
    }


def _report_projection(**updates: object) -> dict[str, object]:
    report = build_code_intelligence_contract(
        providerAvailable=True,
        observations=tuple(
            _observation(operation)
            for operation in (
                "diagnostics",
                "definition",
                "references",
                "hover",
                "symbols",
                "rename",
                "code_action",
            )
        ),
        codeActions=(
            {
                "actionId": "code-action:sha256:" + "6" * 64,
                "titleDigest": "sha256:" + "7" * 64,
                "targetFiles": ("file-ref:sha256:" + "8" * 64,),
                "editCount": 2,
                "operationRef": "operation-ref:code-action",
            },
        ),
        diagnosticsReportDigest="sha256:" + "9" * 64,
        codeIntelligenceClaims=("claim-ref:sha256:" + "a" * 64,),
        testVerificationEvidenceRefs=(),
        **updates,
    )
    return report.public_projection()


def _rendered_projection(**updates: object) -> str:
    return json.dumps(_report_projection(**updates), sort_keys=True)


def test_all_code_intelligence_operations_require_stable_span_and_source_metadata() -> None:
    projection = _report_projection()

    assert projection["status"] == "projected"
    assert projection["success"] is True
    assert projection["operationStatuses"] == {
        "diagnostics": "projected",
        "definition": "projected",
        "references": "projected",
        "hover": "projected",
        "symbols": "projected",
        "rename": "projected",
        "code_action": "projected",
    }

    for observation in projection["observations"]:
        assert observation["span"] == {
            "fileRef": "file-ref:sha256:" + "1" * 64,
            "startLine": 4,
            "startColumn": 2,
            "endLine": 4,
            "endColumn": 17,
            "sourceDigest": "sha256:" + "2" * 64,
        }
        assert observation["sourceMetadata"] == {
            "sourceRef": "source-ref:sha256:" + "3" * 64,
            "workspaceRef": "workspace-ref:sha256:" + "4" * 64,
            "languageId": "python",
            "providerRef": "provider-ref:metadata-only",
        }
        assert observation["resultDigest"].startswith("sha256:")


def test_multiple_locations_for_same_operation_are_allowed_with_metadata() -> None:
    second_diagnostic = {
        **_observation("diagnostics"),
        "span": _span(file_ref="file-ref:sha256:" + "b" * 64),
        "resultDigest": "sha256:" + "c" * 64,
    }

    projection = build_code_intelligence_contract(
        providerAvailable=True,
        requestedOperations=("diagnostics",),
        observations=(_observation("diagnostics"), second_diagnostic),
    ).public_projection()

    assert projection["status"] == "projected"
    assert projection["operationStatuses"] == {"diagnostics": "projected"}
    assert [item["operation"] for item in projection["observations"]] == [
        "diagnostics",
        "diagnostics",
    ]
    assert projection["observations"][1]["span"]["fileRef"] == "file-ref:sha256:" + "b" * 64


def test_code_actions_project_target_files_and_edit_counts_without_raw_edits() -> None:
    projection = _report_projection()

    assert projection["codeActions"] == [
        {
            "actionId": "code-action:sha256:" + "6" * 64,
            "titleDigest": "sha256:" + "7" * 64,
            "targetFiles": ["file-ref:sha256:" + "8" * 64],
            "editCount": 2,
            "operationRef": "operation-ref:code-action",
        }
    ]
    assert "rawEdit" not in _rendered_projection()
    assert "newText" not in _rendered_projection()


def test_missing_lsp_provider_projects_provider_unavailable_not_success() -> None:
    projection = build_code_intelligence_contract(
        providerAvailable=False,
        requestedOperations=("diagnostics", "hover", "code_action"),
    ).public_projection()

    assert projection["status"] == "provider_unavailable"
    assert projection["success"] is False
    assert projection["reasonCodes"] == ["provider_unavailable"]
    assert projection["operationStatuses"] == {
        "diagnostics": "provider_unavailable",
        "hover": "provider_unavailable",
        "code_action": "provider_unavailable",
    }
    assert projection["observations"] == []
    assert projection["authorityFlags"]["lspProviderAttached"] is False
    assert projection["authorityFlags"]["lspSubprocessStarted"] is False


def test_provider_unavailable_report_cannot_carry_projected_observations_or_actions() -> None:
    base_payload = build_code_intelligence_contract(providerAvailable=False).model_dump(
        by_alias=True,
        mode="python",
    )

    with pytest.raises(ValidationError):
        CodeIntelligenceReport.model_validate(
            {
                **base_payload,
                "operationStatuses": {"code_action": "provider_unavailable"},
                "observations": (_observation("code_action"),),
                "codeActions": (
                    {
                        "actionId": "code-action:sha256:" + "1" * 64,
                        "titleDigest": "sha256:" + "2" * 64,
                        "targetFiles": ("file-ref:sha256:" + "3" * 64,),
                        "editCount": 1,
                        "operationRef": "operation-ref:code-action",
                    },
                ),
            }
        )


def test_code_intelligence_claims_do_not_satisfy_test_verification() -> None:
    projection = _report_projection()

    assert projection["codeIntelligenceClaimRefs"] == ["claim-ref:sha256:" + "a" * 64]
    assert projection["testVerificationEvidenceRefs"] == []
    assert projection["testVerificationSatisfied"] is False
    assert "test_verification_evidence_required" in projection["reasonCodes"]


def test_projection_is_default_off_local_only_no_live_no_core_and_adk_metadata_only() -> None:
    projection = _report_projection()

    assert projection["defaultOff"] is True
    assert projection["localOnly"] is True
    assert projection["liveAuthorityAllowed"] is False
    assert projection["coreTouchAllowed"] is False
    assert projection["adkPrimitiveNames"] == [
        "FunctionTool.name",
        "FunctionTool.description",
        "FunctionTool.input_schema",
        "Agent.metadata",
    ]
    assert projection["diagnosticsReportRef"].startswith("artifact:code-intelligence-diagnostics:")
    assert projection["adkArtifactServiceBoundary"] == "ArtifactService"
    assert projection["authorityFlags"] == {
        "lspProviderAttached": False,
        "lspSubprocessStarted": False,
        "subprocessStarted": False,
        "workspaceMutated": False,
        "modelProviderInvoked": False,
        "toolExecuted": False,
        "coreRuntimeTouched": False,
        "mcpOrBrowserActivated": False,
    }


@pytest.mark.parametrize(
    ("model", "payload"),
    (
        (
            CodeIntelligenceSpan,
            {
                "fileRef": "/Users/kevin/private/app.py",
                "startLine": 1,
                "startColumn": 1,
                "endLine": 1,
                "endColumn": 2,
                "sourceDigest": "sha256:" + "b" * 64,
            },
        ),
        (
            CodeIntelligenceObservation,
            {
                **_observation("hover"),
                "sourceMetadata": {
                    "sourceRef": "source-ref:sha256:" + "c" * 64,
                    "workspaceRef": "workspace-ref:sha256:" + "d" * 64,
                    "languageId": "python",
                    "providerRef": "provider-ref:/workspace/private/raw-output",
                },
            },
        ),
        (
            CodeActionProjection,
            {
                "actionId": "code-action:sha256:" + "e" * 64,
                "titleDigest": "sha256:" + "f" * 64,
                "targetFiles": ("file-ref:/workspace/private/app.py",),
                "editCount": 1,
                "operationRef": "operation-ref:code-action",
            },
        ),
    ),
)
def test_models_reject_private_paths_raw_tool_output_and_non_ref_projection_data(
    model: type[CodeIntelligenceSpan | CodeIntelligenceObservation | CodeActionProjection],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_authority_flags_cannot_be_forged_through_construct_copy_or_report_payload() -> None:
    assert CodeIntelligenceAuthorityFlags.model_construct(
        lspSubprocessStarted=True,
        toolExecuted=True,
    ).model_dump(by_alias=True) == CodeIntelligenceAuthorityFlags().model_dump(by_alias=True)

    flags = CodeIntelligenceAuthorityFlags()
    assert flags.model_copy(update={"modelProviderInvoked": True}).model_dump(by_alias=True) == (
        CodeIntelligenceAuthorityFlags().model_dump(by_alias=True)
    )

    report = CodeIntelligenceReport.model_validate(
        {
            **build_code_intelligence_contract(providerAvailable=False).model_dump(
                by_alias=True,
                mode="python",
            ),
            "authorityFlags": {
                "lspProviderAttached": True,
                "lspSubprocessStarted": True,
                "subprocessStarted": True,
                "workspaceMutated": True,
                "modelProviderInvoked": True,
                "toolExecuted": True,
                "coreRuntimeTouched": True,
                "mcpOrBrowserActivated": True,
            },
        }
    )

    assert set(report.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_direct_report_payload_cannot_project_operations_without_observations() -> None:
    base_payload = build_code_intelligence_contract(providerAvailable=False).model_dump(
        by_alias=True,
        mode="python",
    )

    with pytest.raises(ValidationError):
        CodeIntelligenceReport.model_validate(
            {
                **base_payload,
                "status": "projected",
                "success": True,
                "operationStatuses": {"diagnostics": "projected"},
                "observations": (),
                "reasonCodes": (),
            }
        )

    with pytest.raises(ValidationError):
        CodeIntelligenceReport.model_validate(
            {
                **base_payload,
                "status": "projected",
                "success": True,
                "operationStatuses": {"code_action": "projected"},
                "observations": (),
                "codeActions": (
                    {
                        "actionId": "code-action:sha256:" + "1" * 64,
                        "titleDigest": "sha256:" + "2" * 64,
                        "targetFiles": ("file-ref:sha256:" + "3" * 64,),
                        "editCount": 1,
                        "operationRef": "operation-ref:code-action",
                    },
                ),
                "reasonCodes": (),
            }
        )


def test_project_helper_rejects_raw_private_claim_refs_and_passes_with_explicit_testrun_ref() -> None:
    with pytest.raises(ValidationError):
        project_code_intelligence_report(
            providerAvailable=True,
            observations=(_observation("diagnostics"),),
            codeIntelligenceClaims=("raw output says diagnostics passed from /Users/kevin",),
        )

    projection = project_code_intelligence_report(
        providerAvailable=True,
        observations=(_observation("diagnostics"),),
        codeIntelligenceClaims=("claim-ref:sha256:" + "1" * 64,),
        testVerificationEvidenceRefs=("test-ref:sha256:" + "2" * 64,),
    ).public_projection()

    assert projection["testVerificationSatisfied"] is True
    assert projection["testVerificationEvidenceRefs"] == ["test-ref:sha256:" + "2" * 64]


def test_pr7_matrix_row_is_marked_complete_by_contract_module_and_tests() -> None:
    data = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    row = next(item for item in data["rows"] if item["id"] == "lsp_code_intelligence_contracts")

    assert row["alreadyCovered"] is True
    assert row["defaultOff"] is True
    assert row["liveAuthorityAllowed"] is False
    assert row["coreTouchAllowed"] is False
    assert row["coveredByFiles"] == [
        "magi_agent/harness/coding/code_intelligence_contracts.py",
    ]
    assert row["coveredByTests"] == [
        "tests/test_coding_code_intelligence_contracts.py",
    ]
    assert row["missingImplementation"] == ["complete"]
