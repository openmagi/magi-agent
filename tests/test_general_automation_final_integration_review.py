from __future__ import annotations

import json
from pathlib import Path


PYTHON_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = (
    PYTHON_ROOT
    / "tests"
    / "fixtures"
    / "parity"
    / "general_automation_safe_queue_matrix.json"
)


def _matrix() -> dict[str, object]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _rows() -> list[dict[str, object]]:
    rows = _matrix()["rows"]
    assert isinstance(rows, list)
    return rows


def _rows_by_id() -> dict[str, dict[str, object]]:
    return {str(row["id"]): row for row in _rows()}


def test_final_safe_queue_matrix_has_no_remaining_gap_rows() -> None:
    rows = _rows()

    assert all(row["alreadyCovered"] is True for row in rows)
    assert all(row["missingImplementation"] == [] for row in rows)
    assert all(row["coreTouchAllowed"] is False for row in rows)


def test_final_safe_queue_dependencies_are_covered_before_final_review() -> None:
    rows = _rows_by_id()
    final_row = rows["final_integration_review"]
    dependencies = tuple(str(item) for item in final_row["dependencies"])

    assert dependencies == (
        "automation_package_boundary",
        "external_directory_policy",
        "output_budget_followup_refs",
        "shell_command_path_policy",
        "general_automation_agent_presets",
        "spreadsheet_read_validate_reconcile",
        "web_acquisition_provider_contract",
        "browser_inspect_act_boundary",
        "durable_background_task_resume_contract",
        "mcp_plugin_projection_contract",
        "event_control_projection_contract",
    )
    assert all(rows[row_id]["alreadyCovered"] is True for row_id in dependencies)
    assert all(rows[row_id]["missingImplementation"] == [] for row_id in dependencies)


def test_final_safe_queue_activation_boundary_remains_closed() -> None:
    matrix = _matrix()
    rows = _rows()
    final_row = _rows_by_id()["final_integration_review"]

    assert matrix["defaultOff"] is True
    assert matrix["trafficAttached"] is False
    assert matrix["productionAuthority"] is False
    assert final_row["owningLayer"] == "Tests/docs only"
    assert final_row["adkPrimitive"] == "ADK Evaluation vocabulary and audit fixture only"
    assert final_row["activationGate"] == "default-off final audit gate"
    assert all("default-off" in str(row["activationGate"]) for row in rows)
