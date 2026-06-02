from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PYTHON_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_ROOT.parents[2]
MATRIX_PATH = PYTHON_ROOT / "tests/fixtures/parity/e2e_harness_parity_matrix.json"
PLAN_PATHS = (
    REPO_ROOT
    / "docs/superpowers/plans/2026-05-23-python-adk-claude-code-harness-parity-execution-plan.md",
    REPO_ROOT
    / "docs/superpowers/plans/2026-05-23-python-adk-research-harness-claude-code-parity.md",
    REPO_ROOT
    / "docs/superpowers/plans/2026-05-23-python-adk-claude-code-general-automation-harness.md",
)

REQUIRED_ROW_IDS = (
    "combined_matrix",
    "request_shape_snapshot",
    "local_adk_turn_runner",
    "tool_schema_output_artifact_store",
    "toolhost_kernel_scheduler",
    "local_read_search_source_projection",
    "event_transcript_projection",
    "approval_pause_resume",
    "read_ledger_workspace_mutation_safety",
    "coding_read_before_edit_mutation_recipe",
    "coding_evidence_gate",
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
    "checkpoint_context_compaction",
    "research_benchmark_eval_capture",
    "final_integration_readiness",
    "deployment_canary_activation_blocker",
)
REQUIRED_FIELDS = {
    "id",
    "capability",
    "requestedBy",
    "latestMainCoveredRefs",
    "missingImplementation",
    "owningLayer",
    "adkPrimitive",
    "prSliceAssignment",
    "dependencies",
    "activationGate",
    "status",
    "defaultOff",
    "trafficAttached",
    "notes",
}
REQUESTERS = {"coding", "research", "general_automation"}
OWNING_LAYERS = {
    "Core substrate",
    "Coding recipe/harness/plugin",
    "Research recipe/harness/plugin",
    "General automation plugin/harness",
    "Provider boundary",
    "Tests/docs only",
    "Mixed",
}
STATUSES = {"already_covered", "partially_covered", "missing", "activation_blocked"}
FORBIDDEN_PLACEHOLDERS = ("unknown", "tbd", "later")
STALE_REFS = (
    "magi_agent/tools/execution_kernel.py",
    "magi_agent/tools/local_read.py",
    "magi_agent/tools/local_workspace.py",
    "magi_agent/providers/",
)
EXPECTED_PR_ASSIGNMENTS = {
    "combined_matrix": "PR1",
    "request_shape_snapshot": "PR2",
    "local_adk_turn_runner": "PR2",
    "tool_schema_output_artifact_store": "PR3",
    "toolhost_kernel_scheduler": "PR4",
    "local_read_search_source_projection": "PR5",
    "event_transcript_projection": "PR6",
    "approval_pause_resume": "PR7",
    "read_ledger_workspace_mutation_safety": "PR8",
    "coding_read_before_edit_mutation_recipe": "PR9",
    "coding_evidence_gate": "PR10",
    "shell_testrun_bash_safe_subset": "PR11",
    "csv_spreadsheet_backoffice": "PR12",
    "research_routing_agent_materialization": "PR13",
    "research_citation_final_gate": "PR14",
    "web_search_fetch_provider_boundary": "PR15",
    "knowledge_browser_source_boundary": "PR16",
    "mcp_toolsearch_adapter": "PR17",
    "child_runner_evidence_envelope": "PR18",
    "coding_subagent_recipe": "PR19",
    "parallel_research_child_runner": "PR20",
    "checkpoint_context_compaction": "PR21",
    "research_benchmark_eval_capture": "PR22",
    "final_integration_readiness": "PR23",
    "deployment_canary_activation_blocker": "PR23",
}
EXPECTED_OWNING_LAYERS = {
    "combined_matrix": "Tests/docs only",
    "request_shape_snapshot": "Core substrate",
    "local_adk_turn_runner": "Core substrate",
    "tool_schema_output_artifact_store": "Core substrate",
    "toolhost_kernel_scheduler": "Core substrate",
    "local_read_search_source_projection": "Core substrate",
    "event_transcript_projection": "Core substrate",
    "approval_pause_resume": "Core substrate",
    "read_ledger_workspace_mutation_safety": "Core substrate",
    "coding_read_before_edit_mutation_recipe": "Coding recipe/harness/plugin",
    "coding_evidence_gate": "Coding recipe/harness/plugin",
    "shell_testrun_bash_safe_subset": "General automation plugin/harness",
    "csv_spreadsheet_backoffice": "General automation plugin/harness",
    "research_routing_agent_materialization": "Research recipe/harness/plugin",
    "research_citation_final_gate": "Research recipe/harness/plugin",
    "web_search_fetch_provider_boundary": "Provider boundary",
    "knowledge_browser_source_boundary": "Provider boundary",
    "mcp_toolsearch_adapter": "Provider boundary",
    "child_runner_evidence_envelope": "Core substrate",
    "coding_subagent_recipe": "Coding recipe/harness/plugin",
    "parallel_research_child_runner": "Research recipe/harness/plugin",
    "checkpoint_context_compaction": "Core substrate",
    "research_benchmark_eval_capture": "Research recipe/harness/plugin",
    "final_integration_readiness": "Mixed",
    "deployment_canary_activation_blocker": "Mixed",
}


def _load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text())


def _row_ref_path(ref: object) -> str | None:
    if isinstance(ref, str):
        return ref
    if isinstance(ref, dict) and isinstance(ref.get("path"), str):
        return ref["path"]
    return None


def _row_ref_state(ref: object) -> str:
    if isinstance(ref, str):
        return "existing"
    if isinstance(ref, dict) and isinstance(ref.get("state"), str):
        return ref["state"]
    return "existing"


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for nested in value.values():
            result.extend(_strings(nested))
        return result
    if isinstance(value, list):
        result = []
        for nested in value:
            result.extend(_strings(nested))
        return result
    return []


def test_required_rows_exist_exactly_once() -> None:
    matrix = _load_matrix()
    row_ids = [row["id"] for row in matrix["rows"]]

    assert row_ids == list(REQUIRED_ROW_IDS)
    assert len(row_ids) == len(set(row_ids))


def test_rows_have_required_fields_and_valid_enums() -> None:
    for row in _load_matrix()["rows"]:
        assert set(row) >= REQUIRED_FIELDS
        assert row["owningLayer"] in OWNING_LAYERS
        assert row["status"] in STATUSES
        assert row["prSliceAssignment"] in {f"PR{number}" for number in range(1, 24)}
        assert isinstance(row["requestedBy"], list)
        assert row["requestedBy"]
        assert set(row["requestedBy"]) <= REQUESTERS
        assert isinstance(row["latestMainCoveredRefs"], list)
        assert isinstance(row["missingImplementation"], list)
        assert isinstance(row["dependencies"], list)


def test_requesters_include_shared_rows_and_all_rows_are_default_off() -> None:
    rows = _load_matrix()["rows"]

    assert any(len(row["requestedBy"]) > 1 for row in rows)
    assert all(row["defaultOff"] is True for row in rows)
    assert all(row["trafficAttached"] is False for row in rows)


def test_pr1_through_pr22_rows_are_no_longer_status_missing() -> None:
    for row in _load_matrix()["rows"]:
        if row["prSliceAssignment"] != "PR23":
            assert row["status"] != "missing", row["id"]


def test_matrix_contains_no_placeholder_status_or_field_values() -> None:
    for text in _strings(_load_matrix()):
        lowered = text.lower()
        assert not any(marker in lowered for marker in FORBIDDEN_PLACEHOLDERS), text


def test_existing_refs_exist_and_planned_missing_refs_do_not_exist() -> None:
    for row in _load_matrix()["rows"]:
        for ref in row["latestMainCoveredRefs"]:
            path = _row_ref_path(ref)
            assert path is not None
            if _row_ref_state(ref) == "existing":
                assert (PYTHON_ROOT / path).exists(), (row["id"], path)
            else:
                raise AssertionError(f"latestMainCoveredRefs must only list existing refs: {row['id']}")

        for ref in row["missingImplementation"]:
            path = _row_ref_path(ref)
            assert path is not None
            assert _row_ref_state(ref) == "planned_missing"
            assert not (PYTHON_ROOT / path).exists(), (row["id"], path)


def test_stale_paths_are_remapped_not_canonical() -> None:
    matrix = _load_matrix()
    for row in matrix["rows"]:
        for ref in row["latestMainCoveredRefs"]:
            path = _row_ref_path(ref)
            assert path is not None
            assert not any(stale in path for stale in STALE_REFS), (row["id"], path)

    for plan_path in PLAN_PATHS:
        text = plan_path.read_text()
        if any(stale in text for stale in STALE_REFS):
            top = "\n".join(text.splitlines()[:18])
            assert "Consolidated PR1 reconciliation note" in top
            assert "magi_agent/tools/kernel.py" in top
            assert "magi_agent/gates/gate1a_readonly_tools.py" in top
            assert "provider_boundary.py" in top


def test_core_rows_do_not_own_domain_specific_behavior() -> None:
    forbidden_core_phrases = (
        "coding-specific",
        "research-specific",
        "claude code-specific",
        "general automation-specific",
    )

    for row in _load_matrix()["rows"]:
        if row["owningLayer"] == "Core substrate":
            owned_text = " ".join(
                str(row[field]) for field in ("capability", "adkPrimitive", "notes")
            ).lower()
            assert not any(phrase in owned_text for phrase in forbidden_core_phrases), row["id"]


def test_pr22_research_benchmark_capture_row_is_implemented_but_activation_blocked() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    row = rows["research_benchmark_eval_capture"]
    refs = {_row_ref_path(ref) for ref in row["latestMainCoveredRefs"]}

    assert row["status"] == "activation_blocked"
    assert row["missingImplementation"] == []
    assert row["defaultOff"] is True
    assert row["trafficAttached"] is False
    assert "magi_agent/shadow/research_runner_capture.py" in refs
    assert "tests/test_research_runner_capture.py" in refs
    assert "tests/test_e2e_harness_pr22_research_benchmark_eval_capture.py" in refs


def test_pr21_checkpoint_context_compaction_row_is_activation_blocked_default_off() -> None:
    row = next(
        item for item in _load_matrix()["rows"] if item["id"] == "checkpoint_context_compaction"
    )
    refs = {_row_ref_path(ref) for ref in row["latestMainCoveredRefs"]}

    assert row["status"] == "activation_blocked"
    assert row["defaultOff"] is True
    assert row["trafficAttached"] is False
    assert row["missingImplementation"] == []
    assert "production replay remains blocked" in row["activationGate"]
    assert "tests/test_e2e_harness_pr21_checkpoint_context_compaction.py" in refs
    assert "magi_agent/runtime/context_lifecycle.py" in refs
    assert "magi_agent/runtime/query_state.py" in refs
    assert "production" in row["notes"]


def test_pr_slice_assignments_match_consolidated_order() -> None:
    for row in _load_matrix()["rows"]:
        assert row["prSliceAssignment"] == EXPECTED_PR_ASSIGNMENTS[row["id"]]

    assert _load_matrix()["rows"][0]["owningLayer"] == "Tests/docs only"


def test_missing_paths_and_notes_match_pr_slice_assignment() -> None:
    for row in _load_matrix()["rows"]:
        expected = row["prSliceAssignment"]

        for ref in row["missingImplementation"]:
            path = _row_ref_path(ref)
            assert path is not None
            match = re.search(r"/pr(\d{2})_", path)
            assert match is not None, (row["id"], path)
            assert f"PR{int(match.group(1))}" == expected, (row["id"], path, expected)

        for pr_label in re.findall(r"\bPR\d+\b", row["notes"]):
            assert pr_label == expected, (row["id"], row["notes"], expected)


def test_domain_specific_rows_keep_recipe_or_provider_ownership() -> None:
    for row in _load_matrix()["rows"]:
        assert row["owningLayer"] == EXPECTED_OWNING_LAYERS[row["id"]]


def test_deployment_canary_activation_row_is_blocked_without_adk_routing() -> None:
    row = _load_matrix()["rows"][-1]

    assert row["id"] == "deployment_canary_activation_blocker"
    assert row["status"] == "activation_blocked"
    assert "ADK-owned routing" not in row["adkPrimitive"]


def test_pr23_final_integration_rows_are_metadata_only_activation_blocked() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    final_row = rows["final_integration_readiness"]
    deployment_row = rows["deployment_canary_activation_blocker"]
    expected_refs = {
        "magi_agent/harness/e2e_readiness.py",
        "tests/test_e2e_harness_pr23_final_integration_readiness.py",
    }

    for row in (final_row, deployment_row):
        refs = {_row_ref_path(ref) for ref in row["latestMainCoveredRefs"]}
        assert row["status"] == "activation_blocked"
        assert row["missingImplementation"] == []
        assert row["defaultOff"] is True
        assert row["trafficAttached"] is False
        assert expected_refs <= refs

    assert "metadata only" in final_row["activationGate"]
    assert "no activation" in final_row["activationGate"]
    assert final_row["dependencies"] == [
        "coding_evidence_gate",
        "research_benchmark_eval_capture",
        "csv_spreadsheet_backoffice",
    ]
    assert deployment_row["dependencies"] == ["final_integration_readiness"]
    assert deployment_row["activationGate"] == (
        "deployment/routing/secrets/model/live traffic blocked"
    )
    assert "user-visible parity" in deployment_row["notes"]


def test_parallel_research_child_runner_row_stays_recipe_owned_and_activation_blocked() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    core_row = rows["child_runner_evidence_envelope"]
    research_row = rows["parallel_research_child_runner"]

    assert research_row["owningLayer"] == "Research recipe/harness/plugin"
    assert research_row["status"] == "activation_blocked"
    assert research_row["defaultOff"] is True
    assert research_row["trafficAttached"] is False
    assert "live ADK Runner activation blocked" in research_row["adkPrimitive"]
    assert "magi_agent/recipes/research_child_runner.py" not in {
        ref["path"] for ref in core_row["latestMainCoveredRefs"]
    }
    assert "magi_agent/recipes/research_child_runner.py" in {
        ref["path"] for ref in research_row["latestMainCoveredRefs"]
    }
