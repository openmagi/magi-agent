from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.office_automation_contract import (
    OfficeAutomationAttachmentFlags,
    OfficeAutomationContractFixture,
    load_office_automation_contract_fixture,
    project_office_automation_contract_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "office_automation"


def test_office_automation_contract_fixture_covers_business_pack_boundaries() -> None:
    fixture = load_office_automation_contract_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_office_automation_contract_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "office_automation_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.case_order == (
        "spreadsheet_cleanup_validation_metadata",
        "spreadsheet_reconciliation_preview",
        "spreadsheet_external_write_requires_approval",
        "browser_extract_domain_allowlisted",
        "browser_download_long_tool_metadata",
        "browser_form_submit_requires_approval_ack",
        "document_extract_fields_source_refs",
        "document_redline_no_source_mutation",
        "document_deliverable_render_required",
        "lightweight_script_scratch_metadata",
        "lightweight_script_network_write_denied",
        "office_composite_report_pack",
    )
    assert projection.by_pack == {
        "openmagi.spreadsheet-automation": 3,
        "openmagi.browser-automation": 3,
        "openmagi.document-review": 3,
        "openmagi.lightweight-scripting": 2,
        "openmagi.office-automation": 1,
    }
    assert projection.by_execution_surface == {
        "atomic_tool": 5,
        "controlled_composable": 2,
        "generated_script": 2,
        "adk_artifact_service": 2,
        "connector_tool": 1,
    }
    assert projection.by_decision == {
        "allow_metadata_only": 5,
        "approval_required": 3,
        "deny": 1,
        "dry_run_only": 2,
        "block_until_evidence": 1,
    }
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_execution is True

    cleanup = cases["spreadsheet_cleanup_validation_metadata"]
    assert cleanup.recipe_pack_id == "openmagi.spreadsheet-automation"
    assert cleanup.execution_surface == "controlled_composable"
    assert cleanup.evidence_requirements == (
        "spreadsheet.input_hash",
        "spreadsheet.row_count_consistency",
    )
    assert cleanup.tool.name == "SpreadsheetValidate"
    assert cleanup.tool.permission == "read"
    assert cleanup.tool.adk_tool_type == "FunctionTool"

    write_preview = cases["spreadsheet_external_write_requires_approval"]
    assert write_preview.decision == "approval_required"
    assert write_preview.external_write_intent is True
    assert write_preview.control_request is not None
    assert projection.control_requests[write_preview.case_id] == {
        "requestId": "office-approval:turn-office-1:spreadsheet-upload",
        "turnId": "turn-office-1",
        "toolName": "SpreadsheetUploadPreview",
        "reason": "external workbook upload requires approval",
    }

    browser_extract = cases["browser_extract_domain_allowlisted"]
    assert browser_extract.domain_allowlisted is True
    assert browser_extract.network_intent is True
    assert browser_extract.external_write_intent is False
    assert browser_extract.source_refs == ("source:browser:portal-dashboard",)

    browser_download = cases["browser_download_long_tool_metadata"]
    assert browser_download.tool.adk_tool_type == "LongRunningFunctionTool"
    assert browser_download.long_running_tool_eligible is True
    assert browser_download.unit_of_work == "long_tool_job"

    browser_submit = cases["browser_form_submit_requires_approval_ack"]
    assert browser_submit.decision == "approval_required"
    assert browser_submit.requires_external_ack is True
    assert browser_submit.external_ack_received is False

    extract_fields = cases["document_extract_fields_source_refs"]
    assert extract_fields.recipe_pack_id == "openmagi.document-review"
    assert extract_fields.source_refs == ("artifact:contract-v1#page=4",)
    assert extract_fields.evidence_requirements == ("document.source_ref_coverage",)

    render_required = cases["document_deliverable_render_required"]
    assert render_required.decision == "block_until_evidence"
    assert render_required.render_verification_required is True
    assert render_required.render_verification_passed is False
    assert render_required.delivery_claim_allowed is False

    script_metadata = cases["lightweight_script_scratch_metadata"]
    assert script_metadata.execution_surface == "generated_script"
    assert script_metadata.generated_code_metadata_only is True
    assert script_metadata.script_artifact_ref == "artifact:script-csv-transform"
    assert script_metadata.script_hash == "sha256:" + "c" * 64
    assert script_metadata.shell_or_code_execution_allowed is False

    network_denied = cases["lightweight_script_network_write_denied"]
    assert network_denied.decision == "deny"
    assert network_denied.network_write_intent is True
    assert network_denied.reason_codes == ("script_network_write_not_approved",)

    office_report = cases["office_composite_report_pack"]
    assert office_report.recipe_pack_id == "openmagi.office-automation"
    assert office_report.composed_pack_ids == (
        "openmagi.browser-automation",
        "openmagi.spreadsheet-automation",
        "openmagi.document-review",
    )
    assert office_report.artifact_refs == (
        "artifact:portal-export",
        "artifact:analysis-workbook",
        "artifact:weekly-report",
    )

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "/data/bots",
        "/workspace",
        "/var/lib/kubelet",
        "Bearer unsafe",
        "ghp_officesecret",
        "sk-office-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "private connector token",
        "hidden reasoning",
        "raw tool args",
        "adkRunnerInvoked\": true",
        "liveToolDispatched\": true",
        "shellOrCodeExecuted\": true",
        "generatedCodeExecuted\": true",
        "externalWritePerformed\": true",
        "browserSessionAttached\": true",
        "artifactWritten\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"adkRunnerInvoked": True}),
            id="fixture-runner-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["attachmentFlags"].update(
                {"liveToolDispatched": True}
            ),
            id="case-live-tool-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][4]["attachmentFlags"].update(
                {"browserSessionAttached": True}
            ),
            id="browser-session-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][2].pop("controlRequest"),
            id="external-write-without-approval",
        ),
        pytest.param(
            lambda payload: payload["cases"][3].update({"domainAllowlisted": False}),
            id="browser-network-without-domain-allowlist",
        ),
        pytest.param(
            lambda payload: payload["cases"][4].update({"unitOfWork": "mission_control"}),
            id="long-running-tool-used-for-mission-control",
        ),
        pytest.param(
            lambda payload: payload["cases"][8].update({"deliveryClaimAllowed": True}),
            id="document-delivery-claim-without-render",
        ),
        pytest.param(
            lambda payload: payload["cases"][9].update({"scriptBody": "print('run')"}),
            id="generated-script-body-extra-field",
        ),
        pytest.param(
            lambda payload: payload["cases"][10].update({"decision": "allow_metadata_only"}),
            id="script-network-write-allowed",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "/data/bots/bot-secret/report.xlsx"}
            ),
            id="unsafe-production-path-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "safe " * 120 + "sk-live-officesecret"}
            ),
            id="unsafe-secret-after-preview-truncation",
        ),
    ),
)
def test_office_automation_contract_rejects_live_flags_and_policy_bypasses(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        OfficeAutomationContractFixture.model_validate(payload)


def test_office_automation_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = OfficeAutomationAttachmentFlags.model_construct(
        adkRunnerInvoked=True,
        liveToolDispatched=True,
        shellOrCodeExecuted=True,
        externalWritePerformed=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"externalWritePerformed": True})


def test_office_automation_import_boundary_stays_runtime_free() -> None:
    code = """
import sys
from pathlib import Path

from magi_agent.shadow.office_automation_contract import (
    load_office_automation_contract_fixture,
    project_office_automation_contract_fixture,
)

fixture_root = Path('tests/fixtures/office_automation')
fixture = load_office_automation_contract_fixture('policy_matrix.json', fixture_root=fixture_root)
project_office_automation_contract_fixture(fixture)

forbidden = (
    'google.adk.runners',
    'magi_agent.adk_bridge.local_runner',
    'magi_agent.adk_bridge.runner_adapter',
    'magi_agent.adk_bridge.tool_adapter',
    'magi_agent.tools.dispatcher',
    'magi_agent.tools.registry',
    'magi_agent.plugins.agentmemory',
    'magi_agent.memory',
    'magi_agent.memory.hipocampus',
    'magi_agent.memory.qmd',
    'magi_agent.routes',
    'magi_agent.proxy',
    'magi_agent.dashboard',
    'magi_agent.db',
    'magi_agent.k8s',
    'magi_agent.telegram',
    'magi_agent.canary',
)
loaded = sorted(name for name in forbidden if name in sys.modules)
if loaded:
    raise SystemExit(f'forbidden imports loaded: {loaded}')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
