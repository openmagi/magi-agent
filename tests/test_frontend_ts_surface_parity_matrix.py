from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "public_event_parity"
    / "frontend_ts_surface_matrix.json"
)
DOC = (
    Path(__file__).parents[4]
    / "docs"
    / "notes"
    / "2026-05-27-python-adk-frontend-ts-production-compatibility-audit.md"
)

REQUIRED_COLUMNS = {
    "frontend_surface",
    "frontend_files_tests",
    "ts_production_source_path",
    "python_adk_parser_sanitizer_support",
    "python_adk_live_producer_support",
    "chat_proxy_compatibility",
    "current_status",
    "owning_layer",
    "pr_slice_assignment",
    "activation_gate",
}

REQUIRED_SURFACES = {
    "chat_composer",
    "model_picker",
    "attachments",
    "kb_context",
    "streaming_text",
    "work_console",
    "run_inspector",
    "agent_activity_timeline",
    "active_snapshot_reconnect",
    "inject_interrupt",
    "explicit_recipe_selection",
    "recipe_pack_customize_builder",
    "product_plane_admin_runtime_services",
    "source_citation_research_evidence",
    "task_board",
    "subagent_child_background",
    "mission_cron_goal",
    "browser_frame",
    "document_draft",
    "artifact_panel",
    "control_approval",
    "heartbeat_retry_fallback",
}

ALLOWED_STATUSES = {
    "compatible",
    "contract_only",
    "default_off",
    "blocked_until_gate",
    "missing",
    "frontend_bug",
}
GAP_STATUSES = ALLOWED_STATUSES - {"compatible"}
ALLOWED_OWNING_LAYERS = {
    "Frontend",
    "Chat proxy",
    "Python ADK core",
    "Python ADK harness/recipe/plugin",
    "Tests/docs only",
}
ALLOWED_SUPPORT_STATUSES = {
    "supported",
    "contract_only",
    "default_off",
    "blocked_until_gate",
    "missing",
    "not_applicable",
}
ALLOWED_PR_SLICE_PREFIXES = (
    "PR0 Reconciliation Matrix And Gap Audit",
    "PR1 Production Recipe Selector Gating Fix",
    "PR2 Frontend Public Event Replay Compatibility Tests",
    "PR3 Python ADK Live Producer Parity Bridge",
    "PR4 Active Snapshot, Inject, Interrupt Compatibility",
    "PR5 Product-Plane And Admin Dashboard Live Projection Readiness",
    "PR6 Mission/Cron/Goal/Background Surface Compatibility",
    "PR7 Artifact, Browser Frame, And Document Draft Compatibility",
    "PR8 End-To-End Compatibility Canary Harness",
    "PR9 Final Multi-Agent Review And Readiness Report",
)
PRIVATE_FRAGMENTS = (
    "secret",
    "token",
    "authorization",
    "private key",
    "raw payload",
    "raw adk",
    "raw transcript",
)


def _load_matrix() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _rows() -> list[dict[str, Any]]:
    data = _load_matrix()
    assert data["schema_version"] == "frontendTsSurfaceParity.v1"
    assert isinstance(data["rows"], list)
    return data["rows"]


def _support_status(row: dict[str, Any], key: str) -> str:
    value = row[key]
    assert isinstance(value, dict), f"{row['frontend_surface']} {key}"
    assert isinstance(value.get("status"), str), f"{row['frontend_surface']} {key}"
    return value["status"]


def _assert_nonempty(value: Any, *, label: str) -> None:
    if isinstance(value, str):
        assert value.strip(), label
    elif isinstance(value, list):
        assert value and all(isinstance(item, str) and item.strip() for item in value), label
    elif isinstance(value, dict):
        assert value, label
    else:
        raise AssertionError(f"{label} has unsupported value type {type(value).__name__}")


def _doc_table_rows() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or cells[0] in {"frontend_surface", "---"}:
            continue
        if cells[0].startswith(":") or set(cells[0]) <= {"-"}:
            continue
        rows[cells[0]] = cells
    return rows


def _doc_table_surfaces() -> set[str]:
    return set(_doc_table_rows())


def _doc_gap_summary_rows() -> dict[str, tuple[str, str]]:
    summary_rows: dict[str, tuple[str, str]] = {}
    current_layer: str | None = None
    in_summary = False
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if line == "## Gap Summary By Owning Slice":
            in_summary = True
            continue
        if not in_summary:
            continue
        if line.startswith("## "):
            break
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith(":") and not stripped.startswith("- "):
            current_layer = stripped.removesuffix(":")
            continue
        if not stripped.startswith("- ") or current_layer is None:
            continue

        summary_label, surfaces_text = stripped[2:].split(":", 1)
        surfaces_text = surfaces_text.split(".", 1)[0]
        for surface in surfaces_text.split(","):
            surface = surface.strip()
            if surface:
                summary_rows[surface] = (current_layer, summary_label)
    return summary_rows


def _uses_plan_slice(value: str) -> bool:
    return any(value.startswith(prefix) for prefix in ALLOWED_PR_SLICE_PREFIXES)


def test_matrix_covers_required_frontend_ts_surfaces_once() -> None:
    rows = _rows()
    surfaces = [row["frontend_surface"] for row in rows]

    assert REQUIRED_SURFACES <= set(surfaces)
    assert len(surfaces) == len(set(surfaces))


def test_matrix_rows_have_required_columns_and_allowed_enums() -> None:
    for row in _rows():
        missing = REQUIRED_COLUMNS - set(row)
        assert not missing, row.get("frontend_surface", "<missing surface>")

        for column in REQUIRED_COLUMNS:
            _assert_nonempty(row[column], label=f"{row['frontend_surface']} {column}")

        assert row["current_status"] in ALLOWED_STATUSES, row["frontend_surface"]
        assert row["owning_layer"] in ALLOWED_OWNING_LAYERS, row["frontend_surface"]
        assert _uses_plan_slice(row["pr_slice_assignment"]), row["frontend_surface"]

        for key in (
            "python_adk_parser_sanitizer_support",
            "python_adk_live_producer_support",
            "chat_proxy_compatibility",
        ):
            assert _support_status(row, key) in ALLOWED_SUPPORT_STATUSES, row["frontend_surface"]


def test_gap_rows_have_owning_slice_and_activation_gate() -> None:
    for row in _rows():
        if row["current_status"] not in GAP_STATUSES:
            continue

        assert row["owning_layer"] != "Tests/docs only", row["frontend_surface"]
        assert row["pr_slice_assignment"].startswith("PR"), row["frontend_surface"]
        assert row["activation_gate"] not in {"none", "n/a", "already active"}, row["frontend_surface"]


def test_doc_table_lists_the_same_surfaces_as_fixture() -> None:
    fixture_surfaces = {row["frontend_surface"] for row in _rows()}

    assert _doc_table_surfaces() == fixture_surfaces


def test_doc_pr_slice_assignments_use_plan_slices() -> None:
    for surface, cells in _doc_table_rows().items():
        assert len(cells) == 10, surface
        assert _uses_plan_slice(cells[8]), surface


def test_doc_table_matches_fixture_status_owner_slice_and_gate() -> None:
    doc_rows = _doc_table_rows()
    for row in _rows():
        surface = row["frontend_surface"]
        doc_row = doc_rows[surface]

        assert doc_row[6] == row["current_status"], surface
        assert doc_row[7] == row["owning_layer"], surface
        assert doc_row[8] == row["pr_slice_assignment"], surface
        assert doc_row[9] == row["activation_gate"], surface


def test_gap_summary_matches_fixture_owner_and_slice_assignments() -> None:
    summary_rows = _doc_gap_summary_rows()
    for row in _rows():
        if row["current_status"] == "compatible":
            continue

        surface = row["frontend_surface"]
        summary_layer, summary_label = summary_rows[surface]
        assert summary_layer == row["owning_layer"], surface
        assert row["pr_slice_assignment"].startswith(summary_label), surface


def test_explicit_recipe_selection_records_known_frontend_bug() -> None:
    rows = {row["frontend_surface"]: row for row in _rows()}
    row = rows["explicit_recipe_selection"]

    assert row["current_status"] == "frontend_bug"
    assert row["owning_layer"] == "Frontend"
    assert row["pr_slice_assignment"].startswith(
        "PR1 Production Recipe Selector Gating Fix"
    )
    assert "hidden" in row["activation_gate"].lower()
    assert "real bot-scoped availability" in row["activation_gate"].lower()

    doc_row = _doc_table_rows()["explicit_recipe_selection"]
    assert doc_row[6] == "frontend_bug"
    assert doc_row[7] == "Frontend"
    assert doc_row[8].startswith("PR1 Production Recipe Selector Gating Fix")


def test_audit_summary_acknowledges_frontend_recipe_selector_gap() -> None:
    doc_text = DOC.read_text(encoding="utf-8")

    assert "No frontend implementation gap is assigned" not in doc_text
    assert "The only frontend implementation gap assigned here is PR1" in doc_text
    assert "explicit_recipe_selection" in doc_text


def test_compatible_rows_have_explicit_parser_live_and_chat_proxy_support() -> None:
    for row in _rows():
        support_statuses = {
            _support_status(row, "python_adk_parser_sanitizer_support"),
            _support_status(row, "python_adk_live_producer_support"),
            _support_status(row, "chat_proxy_compatibility"),
        }
        if row["current_status"] == "compatible":
            assert support_statuses == {"supported"}, row["frontend_surface"]
        else:
            assert support_statuses != {"supported"}, row["frontend_surface"]


def test_matrix_and_doc_do_not_contain_private_payload_markers() -> None:
    combined = FIXTURE.read_text(encoding="utf-8") + "\n" + DOC.read_text(encoding="utf-8")
    lowered = combined.lower()

    for fragment in PRIVATE_FRAGMENTS:
        assert fragment not in lowered
