from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.shadow.web_acquisition_browser_provider_contract import (
    WebAcquisitionAttachmentFlags,
    WebAcquisitionBrowserProviderFixture,
    load_web_acquisition_browser_provider_fixture,
    project_web_acquisition_browser_provider_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "web_acquisition_browser_provider"


def test_web_acquisition_browser_provider_fixture_projects_default_off_contracts() -> None:
    fixture = load_web_acquisition_browser_provider_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_web_acquisition_browser_provider_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "web_acquisition_browser_provider_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.metadata_only is True
    assert projection.default_off is True
    assert projection.no_live_execution is True
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}

    assert projection.acquisition_phases == (
        "web_search",
        "fetch",
        "reader/Jina-style extraction",
        "metadata/JSON-LD extraction",
        "browser snapshot/scrape fallback",
        "source identity normalization",
        "content quality scoring",
        "retry/fallback strategy",
        "timeout/budget/domain policy",
        "redaction/public preview",
        "source/evidence ledger record creation",
        "opened/observed proof",
    )
    assert projection.browser_provider == {
        "providerId": "openmagi.browser-provider.system",
        "classification": "first_party_system_plugin_provider",
        "coreRuntime": False,
        "capabilities": (
            "browser.open",
            "browser.snapshot",
            "browser.scrape",
            "browser.click",
            "browser.fill",
            "browser.scroll",
            "browser.screenshot",
        ),
        "sessionIsolation": "ephemeral_per_turn",
        "workerBoundary": "CDP/browser-worker boundary",
        "blockedUrlClasses": ("local", "metadata", "cluster"),
        "screenshotArtifactPolicy": "sanitized_artifact_ref_only",
        "timeoutBudgetPolicy": "per_call_timeout_and_per_turn_budget",
        "approvalRequiredFor": ("forms", "downloads", "authenticated_flows"),
    }
    assert projection.case_order == (
        "wa2_source_record_digest_and_opened_proof",
        "wa4_browser_provider_fallback_metadata",
        "wa5_local_metadata_cluster_urls_blocked",
        "wa5_no_auth_bypass",
        "wa5_no_captcha_solving",
        "wa5_no_private_data_scraping",
        "wa5_parent_context_sanitized_refs_only",
        "wa5_research_recipe_dependency",
    )
    assert projection.by_decision == {"allow_metadata_only": 4, "block": 4}
    assert projection.by_category == {
        "source_record_digest_and_observation": 1,
        "browser_fallback_evidence_metadata": 1,
        "blocked_local_metadata_cluster_urls": 1,
        "no_auth_bypass": 1,
        "no_captcha_solving": 1,
        "no_private_data_scraping": 1,
        "sanitized_parent_refs_only": 1,
        "research_recipe_dependency": 1,
    }

    source_case = cases["wa2_source_record_digest_and_opened_proof"]
    assert source_case.source_records[0].method == "web.fetch"
    assert source_case.source_records[0].provider == "openmagi.web-acquisition.system"
    assert source_case.source_records[0].url == "https://docs.example.com/releases"
    assert source_case.source_records[0].content_digest == "sha256:" + "a" * 64
    assert source_case.source_records[0].proof.proof_type == "opened"
    assert projection.case_snapshots[source_case.case_id]["sourceRecords"] == (
        {
            "sourceRef": "source:web:src_1",
            "method": "web.fetch",
            "provider": "openmagi.web-acquisition.system",
            "url": "https://docs.example.com/releases",
            "normalizedUrl": "https://docs.example.com/releases",
            "contentDigest": "sha256:" + "a" * 64,
            "proofType": "opened",
            "evidenceRef": "evidence:web:src_1",
        },
    )

    browser_fallback = cases["wa4_browser_provider_fallback_metadata"]
    assert browser_fallback.raw_browser_snapshot_ref == "artifact:browser-snapshot:src_2"
    assert browser_fallback.raw_browser_snapshot_injected is False
    assert browser_fallback.raw_tool_logs_injected is False
    assert projection.case_snapshots[browser_fallback.case_id]["sourceRecordMethods"] == (
        "browser.open",
        "browser.snapshot",
        "browser.scrape",
    )
    assert projection.case_snapshots[browser_fallback.case_id]["evidenceRefs"] == (
        "evidence:browser:src_2",
    )

    blocked = projection.case_snapshots["wa5_local_metadata_cluster_urls_blocked"]
    assert blocked["decision"] == "block"
    assert blocked["blockedUrlClasses"] == ("local", "metadata", "cluster")
    assert blocked["sourceRecords"] == ()

    assert cases["wa5_no_auth_bypass"].auth_bypass_allowed is False
    assert cases["wa5_no_captcha_solving"].captcha_solving_allowed is False
    assert cases["wa5_no_private_data_scraping"].private_data_scraping_allowed is False

    parent_refs = projection.case_snapshots["wa5_parent_context_sanitized_refs_only"]
    assert parent_refs["parentOutputRefs"] == (
        "source:web:src_1",
        "evidence:browser:src_2",
    )
    assert parent_refs["rawBrowserSnapshotInjected"] is False
    assert parent_refs["rawToolLogsInjected"] is False
    assert parent_refs["parentContextRawInjection"] is False

    recipe = projection.case_snapshots["wa5_research_recipe_dependency"]
    assert recipe["recipeDependencies"] == ("web-acquisition",)
    assert recipe["citationsAddedByRecipe"] is True
    assert recipe["factGroundingAddedByRecipe"] is True
    assert recipe["parentOutputRefs"] == ("source:web:src_1", "evidence:web:src_1")

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "raw browser snapshot",
        "raw tool log",
        "raw transcript",
        "private dashboard data",
        "localhost",
        "169.254.169.254",
        "kubernetes.default.svc",
        "Bearer unsafe",
        "SUPABASE_SERVICE_ROLE_KEY",
        "adkRunnerInvoked\": true",
        "liveToolDispatched\": true",
        "networkFetched\": true",
        "browserExecuted\": true",
        "browserWorkerAttached\": true",
        "cdpSessionAttached\": true",
        "rawSnapshotInjected\": true",
        "rawToolLogInjected\": true",
        "parentContextInjected\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"browserExecuted": True}),
            id="fixture-browser-executed",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["attachmentFlags"].update(
                {"networkFetched": True}
            ),
            id="case-network-fetched",
        ),
        pytest.param(
            lambda payload: payload["browserProvider"].update({"coreRuntime": True}),
            id="browser-provider-core-runtime",
        ),
        pytest.param(
            lambda payload: payload["browserProvider"].update({"classification": "core"}),
            id="browser-provider-classified-as-core",
        ),
        pytest.param(
            lambda payload: payload["browserProvider"]["capabilities"].remove(
                "browser.screenshot"
            ),
            id="missing-screenshot-capability",
        ),
        pytest.param(
            lambda payload: payload["browserProvider"]["approvalRequiredFor"].remove(
                "forms"
            ),
            id="forms-do-not-require-approval",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["sourceRecords"][0].update(
                {"url": "http://localhost:3000/admin"}
            ),
            id="local-url-source-record",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["sourceRecords"][0].update(
                {"url": "http://169.254.169.254/latest/meta-data"}
            ),
            id="metadata-url-source-record",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["sourceRecords"][0].update(
                {"url": "https://kubernetes.default.svc/api"}
            ),
            id="cluster-url-source-record",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "operator preview saw https://10.0.0.5/admin"}
            ),
            id="private-ip-public-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "operator preview saw https://localhost:3000/admin"}
            ),
            id="localhost-public-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "operator preview saw https://127.0.0.1/admin"}
            ),
            id="loopback-public-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "operator preview saw https://169.254.169.254/latest"}
            ),
            id="metadata-ip-public-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "operator preview saw https://api.default.svc/admin"}
            ),
            id="svc-public-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "operator preview saw https://api.ns.cluster.local/admin"}
            ),
            id="cluster-local-public-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["sourceRecords"][0].update(
                {"contentDigest": "sha256:not-a-digest"}
            ),
            id="bad-source-digest",
        ),
        pytest.param(
            lambda payload: payload["cases"][1].update(
                {"rawBrowserSnapshotInjected": True}
            ),
            id="browser-fallback-raw-snapshot-injected",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["sourceRecords"][1].update(
                {"rawTranscriptIncluded": True}
            ),
            id="source-record-raw-transcript-included",
        ),
        pytest.param(
            lambda payload: payload["cases"][3].update({"authBypassAllowed": True}),
            id="auth-bypass-allowed",
        ),
        pytest.param(
            lambda payload: payload["cases"][4].update({"captchaSolvingAllowed": True}),
            id="captcha-solving-allowed",
        ),
        pytest.param(
            lambda payload: payload["cases"][5].update(
                {"privateDataScrapingAllowed": True}
            ),
            id="private-data-scraping-allowed",
        ),
        pytest.param(
            lambda payload: payload["cases"][6]["parentOutputRefs"].append(
                "raw transcript: user secret"
            ),
            id="raw-parent-output-ref",
        ),
        pytest.param(
            lambda payload: payload["cases"][7].update({"recipeDependencies": []}),
            id="research-recipe-missing-web-acquisition-dependency",
        ),
        pytest.param(
            lambda payload: payload["cases"][7].update(
                {"citationsAddedByRecipe": False}
            ),
            id="research-recipe-citations-not-separate",
        ),
        pytest.param(
            lambda payload: payload["cases"][1].update(
                {"rawSnapshotText": "raw browser snapshot text"}
            ),
            id="extra-raw-browser-snapshot-text",
        ),
    ),
)
def test_web_acquisition_browser_provider_fixture_rejects_live_and_unsafe_states(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        WebAcquisitionBrowserProviderFixture.model_validate(payload)


def test_public_metadata_allows_local_checkout_workspace_path_text() -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    payload["cases"][0]["publicPreview"] = (
        "fixture was validated from /Users/dev/workspace/clawy checkout notes"
    )

    fixture = WebAcquisitionBrowserProviderFixture.model_validate(payload)

    assert fixture.cases[0].public_preview.endswith("checkout notes")


def test_web_acquisition_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = WebAcquisitionAttachmentFlags.model_construct(
        adkRunnerInvoked=True,
        liveToolDispatched=True,
        networkFetched=True,
        browserExecuted=True,
        rawSnapshotInjected=True,
        parentContextInjected=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"browserWorkerAttached": True})


def test_web_acquisition_browser_provider_import_boundary_stays_runtime_free() -> None:
    code = """
import sys
from pathlib import Path

from openmagi_core_agent.shadow.web_acquisition_browser_provider_contract import (
    load_web_acquisition_browser_provider_fixture,
    project_web_acquisition_browser_provider_fixture,
)

fixture_root = Path('tests/fixtures/web_acquisition_browser_provider')
fixture = load_web_acquisition_browser_provider_fixture('policy_matrix.json', fixture_root=fixture_root)
project_web_acquisition_browser_provider_fixture(fixture)

forbidden = (
    'google.adk.runners',
    'google.adk.tools',
    'openmagi_core_agent.adk_bridge.local_runner',
    'openmagi_core_agent.adk_bridge.runner_adapter',
    'openmagi_core_agent.adk_bridge.tool_adapter',
    'openmagi_core_agent.tools.dispatcher',
    'openmagi_core_agent.tools.registry',
    'openmagi_core_agent.plugins.native_catalog',
    'openmagi_core_agent.recipes.compiler',
    'openmagi_core_agent.browser',
    'openmagi_core_agent.agent_browser',
    'openmagi_core_agent.browser_worker',
    'openmagi_core_agent.routes',
    'openmagi_core_agent.app',
    'openmagi_core_agent.transport.chat',
    'openmagi_core_agent.proxy',
    'openmagi_core_agent.dashboard',
    'openmagi_core_agent.db',
    'openmagi_core_agent.k8s',
    'httpx',
    'requests',
    'playwright',
    'selenium',
)
loaded = sorted(
    name
    for name in sys.modules
    for forbidden_name in forbidden
    if name == forbidden_name or name.startswith(f'{forbidden_name}.')
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
