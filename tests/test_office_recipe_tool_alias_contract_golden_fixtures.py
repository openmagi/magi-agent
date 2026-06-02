from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.office_recipe_tool_alias_contract import (
    OfficeRecipeToolAliasAttachmentFlags,
    OfficeRecipeToolAliasFixture,
    load_office_recipe_tool_alias_fixture,
    project_office_recipe_tool_alias_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "office_recipe_tool_aliases"


def test_office_recipe_tool_alias_fixture_records_metadata_only_alias_parity() -> None:
    fixture = load_office_recipe_tool_alias_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_office_recipe_tool_alias_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "office_recipe_tool_alias_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.case_order == (
        "spreadsheet_read_alias_metadata",
        "spreadsheet_plan_write_alias_metadata",
        "browser_inspect_alias_metadata",
        "browser_plan_action_alias_metadata",
        "document_inspect_alias_metadata",
        "script_plan_run_alias_metadata",
    )
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_execution is True
    assert projection.abstract_tool_refs == (
        "tool:spreadsheet.read",
        "tool:spreadsheet.plan-write",
        "tool:browser.inspect",
        "tool:browser.plan-action",
        "tool:document.inspect",
        "tool:script.plan-run",
    )
    assert projection.ts_plugin_surfaces_by_ref == {
        "tool:spreadsheet.read": (
            "SpreadsheetWrite",
            "SpreadsheetValidate",
            "SpreadsheetReconcilePreview",
        ),
        "tool:spreadsheet.plan-write": ("SpreadsheetWrite", "FileDeliver", "FileSend"),
        "tool:browser.inspect": ("Browser", "SocialBrowser", "BrowserExtractSnapshot"),
        "tool:browser.plan-action": (
            "Browser",
            "SocialBrowser",
            "BrowserExtractSnapshot",
            "BrowserDownloadReport",
            "BrowserSubmitForm",
        ),
        "tool:document.inspect": (
            "DocumentWrite",
            "DocumentExtractFields",
            "DocumentRedlineSuggest",
            "DocumentDeliverableReview",
        ),
        "tool:script.plan-run": ("LightweightScriptPlan",),
    }
    assert projection.adk_first_contract == {
        "recipeRefsRemainAgentRecipeCompilerMetadata": True,
        "futureAtomicToolsMapTo": "ADK FunctionTool through OpenMagi ToolHost policy",
        "futureLongJobsMayMapTo": "LongRunningFunctionTool after approval",
        "missionsSchedulerModeledAsLongRunningFunctionTool": False,
        "importsAdkPrimitives": False,
    }

    spreadsheet_read = cases["spreadsheet_read_alias_metadata"]
    assert spreadsheet_read.abstract_tool_ref == "tool:spreadsheet.read"
    assert spreadsheet_read.metadata_only is True
    assert spreadsheet_read.executable_authority is False
    assert spreadsheet_read.live_tool_satisfied_by_ts_surface is False
    assert spreadsheet_read.reason_codes == (
        "recipe_ref_metadata_only",
        "spreadsheet_write_does_not_satisfy_live_read_validate_reconcile",
    )

    browser_plan = cases["browser_plan_action_alias_metadata"]
    assert browser_plan.external_submit_or_download_intent is True
    assert browser_plan.external_submit_or_download_attached is False
    assert browser_plan.diagnostic_metadata_surfaces == (
        "browser.extract.metadata",
        "browser.download.metadata",
        "browser.submit.metadata",
    )

    document_inspect = cases["document_inspect_alias_metadata"]
    assert document_inspect.diagnostic_metadata_surfaces == (
        "document.extraction.metadata",
        "document.redline.metadata",
        "document.render.metadata",
    )
    assert document_inspect.artifact_write_or_delivery_attached is False

    script_plan = cases["script_plan_run_alias_metadata"]
    assert script_plan.abstract_tool_ref == "tool:script.plan-run"
    assert script_plan.ts_plugin_surfaces == ("LightweightScriptPlan",)
    assert script_plan.executable_authority is False
    assert script_plan.reason_codes == (
        "recipe_ref_metadata_only",
        "script_plan_no_shell_or_scheduler_runtime",
    )

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "trafficAttached\": true",
        "executionAttached\": true",
        "toolHostDispatchAttached\": true",
        "adkRunnerInvoked\": true",
        "browserSessionAttached\": true",
        "externalSubmitAttached\": true",
        "externalDownloadAttached\": true",
        "artifactWriteAttached\": true",
        "artifactDeliveryAttached\": true",
        "connectorCallAttached\": true",
        "schedulerRuntimeAttached\": true",
        "missionRuntimeAttached\": true",
        "live_tool_dispatch",
        "ToolHost dispatcher",
        "Runner invocation",
        "/data/bots",
        "/workspace",
        "supabase://",
        "postgres://",
        "GATEWAY_TOKEN",
        "SUPABASE_SERVICE_ROLE_KEY",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"trafficAttached": True}),
            id="traffic-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["attachmentFlags"].update(
                {"executionAttached": True}
            ),
            id="case-execution-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update({"executableAuthority": True}),
            id="abstract-ref-becomes-executable",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"liveToolSatisfiedByTsSurface": True}
            ),
            id="spreadsheet-write-claims-live-read",
        ),
        pytest.param(
            lambda payload: payload["cases"][3].update(
                {"externalSubmitOrDownloadAttached": True}
            ),
            id="browser-external-action-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][4].update(
                {"artifactWriteOrDeliveryAttached": True}
            ),
            id="document-artifact-write-attached",
        ),
        pytest.param(
            lambda payload: payload["adkFirstContract"].update(
                {"importsAdkPrimitives": True}
            ),
            id="adk-imports-enabled",
        ),
        pytest.param(
            lambda payload: payload["adkFirstContract"].update(
                {"missionsSchedulerModeledAsLongRunningFunctionTool": True}
            ),
            id="mission-scheduler-long-running-tool",
        ),
    ),
)
def test_office_recipe_tool_alias_fixture_rejects_runtime_authority(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        OfficeRecipeToolAliasFixture.model_validate(payload)


@pytest.mark.parametrize(
    ("case_index", "replacement"),
    (
        pytest.param(2, ("Browser", "SocialBrowser", "BrowserExtract"), id="browser-inspect"),
        pytest.param(
            3,
            ("Browser", "SocialBrowser", "BrowserDownload", "BrowserSubmit"),
            id="browser-plan-action",
        ),
        pytest.param(
            4,
            ("DocumentWrite", "DocumentExtract", "DocumentRedline", "DocumentRender"),
            id="document-inspect",
        ),
        pytest.param(5, ("ScriptPlanRun",), id="script-plan-run"),
    ),
)
def test_office_recipe_tool_alias_fixture_rejects_adjacent_surface_name_drift(
    case_index: int,
    replacement: tuple[str, ...],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    payload["cases"][case_index]["tsPluginSurfaces"] = list(replacement)

    with pytest.raises(ValidationError, match="must record represented adjacent surfaces"):
        OfficeRecipeToolAliasFixture.model_validate(payload)


def test_office_recipe_tool_alias_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = OfficeRecipeToolAliasAttachmentFlags.model_construct(
        trafficAttached=True,
        executionAttached=True,
        adkRunnerInvoked=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"toolHostDispatchAttached": True})


def test_office_recipe_tool_alias_import_boundary_stays_metadata_only() -> None:
    code = """
import sys
from pathlib import Path

from magi_agent.shadow.office_recipe_tool_alias_contract import (
    load_office_recipe_tool_alias_fixture,
    project_office_recipe_tool_alias_fixture,
)

fixture_root = Path('tests/fixtures/office_recipe_tool_aliases')
fixture = load_office_recipe_tool_alias_fixture('policy_matrix.json', fixture_root=fixture_root)
project_office_recipe_tool_alias_fixture(fixture)

forbidden_prefixes = (
    'google.adk',
    'magi_agent.adk_bridge',
    'magi_agent.tools.dispatcher',
    'magi_agent.tools.registry',
    'magi_agent.routes',
    'magi_agent.browser',
    'magi_agent.plugins.native.browser',
    'magi_agent.memory',
    'magi_agent.channels.delivery',
    'magi_agent.db',
    'magi_agent.k8s',
    'magi_agent.canary',
)
loaded = sorted(
    name
    for name in sys.modules
    if name == forbidden_prefixes[0] or name.startswith(forbidden_prefixes)
)
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
