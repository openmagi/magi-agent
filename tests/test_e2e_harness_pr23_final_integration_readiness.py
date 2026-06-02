from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from openmagi_core_agent.harness.e2e_readiness import (
    DEPLOYMENT_CANARY_ACTIVATION_BLOCKERS,
    E2E_AUTHORITY_FLAG_ALIASES,
    E2E_HARNESS_REQUIRED_ROW_IDS,
    E2EReadinessAuthorityFlags,
    build_final_integration_readiness_report,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = PYTHON_ROOT / "tests/fixtures/parity/e2e_harness_parity_matrix.json"
MODULE_PATH = PYTHON_ROOT / "openmagi_core_agent/harness/e2e_readiness.py"


def _load_rows() -> list[dict[str, object]]:
    return json.loads(MATRIX_PATH.read_text())["rows"]


def _rows_by_id() -> dict[str, dict[str, object]]:
    return {str(row["id"]): row for row in _load_rows()}


def _encoded_public_projection(rows: list[dict[str, object]] | None = None) -> str:
    report = build_final_integration_readiness_report(rows or _load_rows())
    return json.dumps(report.public_projection(), sort_keys=True)


def test_pr23_readiness_report_covers_matrix_rows_and_dependency_graph() -> None:
    rows = _load_rows()
    row_ids = tuple(str(row["id"]) for row in rows)

    report = build_final_integration_readiness_report(rows)

    assert report.covered_row_ids == row_ids
    assert report.dependency_violations == ()
    assert report.readiness_metadata_complete is False
    assert report.activation_state == "activation_blocked"
    assert report.status == "activation_blocked"
    assert report.final_integration_row_id == "final_integration_readiness"
    assert report.deployment_blocker_row_id == "deployment_canary_activation_blocker"


def test_pr23_readiness_report_verifies_required_rows_without_granting_activation() -> None:
    report = build_final_integration_readiness_report(_load_rows())

    assert report.required_row_ids == E2E_HARNESS_REQUIRED_ROW_IDS[:-2]
    assert report.required_row_ids[0] == "combined_matrix"
    assert report.required_row_ids[-1] == "research_benchmark_eval_capture"
    assert "final_integration_readiness" not in report.required_row_ids
    assert report.prerequisite_missing_row_ids == ()
    assert set(report.implementation_gap_row_ids) == {
        "combined_matrix",
        "request_shape_snapshot",
        "coding_read_before_edit_mutation_recipe",
        "shell_testrun_bash_safe_subset",
        "csv_spreadsheet_backoffice",
        "research_routing_agent_materialization",
        "research_citation_final_gate",
        "web_search_fetch_provider_boundary",
        "knowledge_browser_source_boundary",
        "mcp_toolsearch_adapter",
        "child_runner_evidence_envelope",
        "coding_subagent_recipe",
        "parallel_research_child_runner",
    }
    assert report.activation_allowed is False
    assert report.traffic_attached is False
    assert report.production_authority is False


def test_pr23_readiness_report_detects_omitted_required_rows() -> None:
    rows = [
        row
        for row in _load_rows()
        if row["id"] != "knowledge_browser_source_boundary"
    ]

    report = build_final_integration_readiness_report(rows)

    assert "knowledge_browser_source_boundary" in report.required_row_ids
    assert report.readiness_metadata_complete is False
    assert report.prerequisite_missing_row_ids == ("knowledge_browser_source_boundary",)


def test_pr23_readiness_report_detects_non_empty_missing_implementation_rows() -> None:
    rows = copy.deepcopy(_load_rows())
    row = next(item for item in rows if item["id"] == "research_routing_agent_materialization")

    report = build_final_integration_readiness_report(rows)
    projection = report.public_projection()

    assert row["missingImplementation"]
    assert "research_routing_agent_materialization" in report.implementation_gap_row_ids
    assert "research_routing_agent_materialization" in projection["implementationGapRowIds"]
    assert report.readiness_metadata_complete is False


def test_pr23_rows_are_default_off_activation_blocked_and_no_longer_missing() -> None:
    rows = _rows_by_id()
    final_row = rows["final_integration_readiness"]
    deployment_row = rows["deployment_canary_activation_blocker"]

    for row in (final_row, deployment_row):
        assert row["status"] == "activation_blocked"
        assert row["defaultOff"] is True
        assert row["trafficAttached"] is False
        assert row["missingImplementation"] == []

    final_refs = {ref["path"] for ref in final_row["latestMainCoveredRefs"]}  # type: ignore[index]
    deployment_refs = {
        ref["path"] for ref in deployment_row["latestMainCoveredRefs"]  # type: ignore[index]
    }
    expected_refs = {
        "openmagi_core_agent/harness/e2e_readiness.py",
        "tests/test_e2e_harness_pr23_final_integration_readiness.py",
    }

    assert expected_refs <= final_refs
    assert expected_refs <= deployment_refs


def test_deployment_canary_blocker_lists_exact_activation_blockers() -> None:
    report = build_final_integration_readiness_report(_load_rows())
    row = _rows_by_id()["deployment_canary_activation_blocker"]

    assert report.deployment_activation_blockers == DEPLOYMENT_CANARY_ACTIVATION_BLOCKERS
    assert report.deployment_activation_blockers == (
        "deployment_not_approved",
        "routing_not_approved",
        "secrets_not_bound",
        "model_calls_not_approved",
        "live_traffic_not_attached",
    )
    assert row["status"] == "activation_blocked"
    assert row["activationGate"] == "deployment/routing/secrets/model/live traffic blocked"


def test_all_authority_and_attachment_flags_are_false() -> None:
    report = build_final_integration_readiness_report(_load_rows())
    authority = report.authority_flags.model_dump(by_alias=True)
    projection = report.public_projection()

    assert set(authority) == set(E2E_AUTHORITY_FLAG_ALIASES)
    assert set(authority.values()) == {False}
    assert projection["authorityFlags"] == authority
    assert projection["activationAllowed"] is False
    assert projection["trafficAttached"] is False
    assert projection["productionAuthority"] is False


def test_authority_flags_cannot_be_forged_with_model_copy_or_construct() -> None:
    copied = E2EReadinessAuthorityFlags().model_copy(
        update={"traffic": True, "productionAuthority": True, "model_call": True}
    )
    constructed = E2EReadinessAuthorityFlags.model_construct(
        traffic=True,
        productionAuthority=True,
        modelCall=True,
    )

    for flags in (copied, constructed):
        dumped = flags.model_dump(by_alias=True)
        assert set(dumped) == set(E2E_AUTHORITY_FLAG_ALIASES)
        assert set(dumped.values()) == {False}
        assert flags.traffic is False
        assert flags.production_authority is False
        assert flags.model_call is False


def test_public_projection_sanitizes_raw_private_inputs() -> None:
    rows = copy.deepcopy(_load_rows())
    final_row = next(row for row in rows if row["id"] == "final_integration_readiness")
    final_row["capability"] = (
        "raw /Users/kevin/private/.env Authorization Bearer sk-test "
        "cookie=session-id password=hunter2 api_key=abc auth=abc"
    )
    final_row["adkPrimitive"] = (
        "ADK SessionService with private chain-of-thought "
        "tool_output=/home/kevin/.ssh/id_rsa "
        "raw prompt=please disclose internal plan output=first second third "
        "tool args=alpha beta gamma"
    )
    final_row["notes"] = "raw secret token /workspace/private session key=abc api key=abc"
    final_row["latestMainCoveredRefs"] = [
        {"path": "openmagi_core_agent/harness/e2e_readiness.py", "state": "existing"},
        {"path": "/Users/kevin/private/raw.txt", "state": "existing"},
        {"path": "/home/sam/.ssh/id_rsa", "state": "existing"},
        {"path": "id_rsa", "state": "existing"},
    ]

    encoded = _encoded_public_projection(rows)

    assert "/Users/kevin/private" not in encoded
    assert "/workspace/private" not in encoded
    assert "Authorization" not in encoded
    assert "Bearer" not in encoded
    assert "sk-test" not in encoded
    assert "secret" not in encoded.lower()
    assert "raw" not in encoded.lower()
    assert "private" not in encoded.lower()
    assert "cookie" not in encoded.lower()
    assert "password" not in encoded.lower()
    assert "api_key" not in encoded.lower()
    assert "session_key" not in encoded.lower()
    assert "auth=abc" not in encoded.lower()
    assert "session key" not in encoded.lower()
    assert "api key" not in encoded.lower()
    assert "/home/kevin" not in encoded
    assert "/home/sam" not in encoded
    assert ".ssh" not in encoded
    assert "id_rsa" not in encoded
    assert "tool_output" not in encoded.lower()
    assert "disclose internal plan" not in encoded.lower()
    assert "first second third" not in encoded.lower()
    assert "alpha beta gamma" not in encoded.lower()


def test_public_projection_masks_malicious_row_attachment_flags_but_keeps_violations() -> None:
    rows = copy.deepcopy(_load_rows())
    target = next(row for row in rows if row["id"] == "local_adk_turn_runner")
    target["defaultOff"] = False
    target["trafficAttached"] = True

    report = build_final_integration_readiness_report(rows)
    projection = report.public_projection()
    projected_target = next(
        row for row in projection["rowSummaries"] if row["rowId"] == "local_adk_turn_runner"
    )

    assert report.default_off_violations == ("local_adk_turn_runner",)
    assert report.traffic_attachment_violations == ("local_adk_turn_runner",)
    assert report.readiness_metadata_complete is False
    assert projected_target["defaultOff"] is True
    assert projected_target["trafficAttached"] is False


def test_core_primitives_are_generic_and_domain_behavior_is_not_core_owned() -> None:
    report = build_final_integration_readiness_report(_load_rows())
    proof = report.generic_primitive_proof

    assert "local_adk_turn_runner" in proof.core_substrate_row_ids
    assert "toolhost_kernel_scheduler" in proof.core_substrate_row_ids
    assert "coding_evidence_gate" in proof.recipe_or_provider_owned_row_ids
    assert "research_benchmark_eval_capture" in proof.recipe_or_provider_owned_row_ids
    assert "csv_spreadsheet_backoffice" in proof.recipe_or_provider_owned_row_ids
    assert proof.core_owned_domain_workflow_row_ids == ()

    projected = report.public_projection()["adkPrimitiveJustifications"]
    projected_ids = {item["rowId"] for item in projected}
    assert projected_ids == set(report.covered_row_ids)
    assert all("adkPrimitive" in item for item in projected)
    assert all("owningLayer" in item for item in projected)


def test_e2e_readiness_import_boundary_stays_metadata_only() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.harness.e2e_readiness")
assert module.build_final_integration_readiness_report([])

forbidden_exact = (
    "google.adk",
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.events",
    "google.adk.tools",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.local_runner",
    "openmagi_core_agent.adk_bridge.local_toolhost",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.tools.kernel",
    "openmagi_core_agent.browser.live_provider_pack",
    "openmagi_core_agent.memory.write_boundary",
    "openmagi_core_agent.memory.adapters.hipocampus_readonly",
    "openmagi_core_agent.web_acquisition.live_provider_pack",
)
forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.routes",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.chat_proxy",
    "openmagi_core_agent.proxy",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.provisioning",
    "openmagi_core_agent.k8s",
    "openmagi_core_agent.database",
    "openmagi_core_agent.supabase",
    "openmagi_core_agent.browser",
    "openmagi_core_agent.web_acquisition",
    "openmagi_core_agent.memory",
)
loaded = [
    name
    for name in sys.modules
    if name in forbidden_exact
    or any(name.startswith(f"{exact}.") for exact in forbidden_exact)
    or any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"e2e readiness import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_e2e_readiness_source_has_no_runtime_or_provider_imports() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    forbidden_imports = (
        "google.adk",
        "openmagi_core_agent.adk_bridge.runner_adapter",
        "openmagi_core_agent.adk_bridge.local_runner",
        "openmagi_core_agent.adk_bridge.local_toolhost",
        "openmagi_core_agent.tools.dispatcher",
        "openmagi_core_agent.tools.kernel",
        "openmagi_core_agent.routes",
        "openmagi_core_agent.transport",
        "openmagi_core_agent.chat_proxy",
        "openmagi_core_agent.proxy",
        "openmagi_core_agent.deploy",
        "openmagi_core_agent.provisioning",
        "openmagi_core_agent.k8s",
        "openmagi_core_agent.browser",
        "openmagi_core_agent.web_acquisition",
        "openmagi_core_agent.memory",
        "fastapi",
        "subprocess",
        "socket",
        "httpx",
        "requests",
    )

    assert not any(forbidden in source for forbidden in forbidden_imports)
    assert "ToolHost" not in source
