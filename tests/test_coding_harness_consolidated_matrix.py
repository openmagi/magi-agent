from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PYTHON_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_ROOT.parents[2]
MATRIX_PATH = PYTHON_ROOT / "tests/fixtures/parity/coding_harness_consolidated_matrix.json"
OPENCODE_DELTA_MATRIX = (
    PYTHON_ROOT / "tests/fixtures/opencode_delta/harness_delta_matrix.json"
)
CONSOLIDATED_PLAN = (
    REPO_ROOT
    / "docs/superpowers/plans/2026-05-27-python-adk-coding-harness-consolidated-safe-queue-plan.md"
)
OPENCODE_DELTA_RECONCILIATION_NOTE = (
    REPO_ROOT
    / "docs/notes/2026-05-27-python-adk-coding-harness-opencode-delta-reconciliation.md"
)
FINAL_CODING_HARNESS_REVIEW_NOTE = (
    REPO_ROOT
    / "docs/notes/2026-05-27-python-adk-coding-harness-consolidated-final-review.md"
)

SOURCE_DOCUMENTS = (
    "docs/superpowers/plans/2026-05-23-python-adk-claude-code-harness-parity-execution-plan.md",
    "docs/superpowers/plans/2026-05-20-python-adk-claude-code-parity-full-master-plan.md",
    "docs/superpowers/plans/2026-05-26-python-adk-opencode-latest-harness-delta-addendum.md",
    "docs/superpowers/plans/2026-05-20-python-adk-opencode-parity-recipes.md",
    "docs/superpowers/plans/2026-05-20-python-adk-claude-code-parity-recipes.md",
    "docs/superpowers/plans/2026-05-12-coding-harness-reliability-train.md",
    "docs/superpowers/plans/2026-05-10-coding-parity-measurement-evidence.md",
)

REQUIRED_ROWS = (
    "coding_latest_main_reconciliation",
    "coding_recipe_ownership_boundaries",
    "read_before_edit_and_stale_rejection",
    "patch_apply_diff_test_checkpoint_evidence",
    "bash_safe_subset_and_shell_policy",
    "coding_subagent_roles_and_repair_loop",
    "coding_compaction_and_session_continuity",
    "lsp_code_intelligence_contracts",
    "coding_measurement_eval_and_reliability_train",
    "opencode_delta_coding_rows",
    "final_coding_harness_review",
)

ROW_FIELDS = {
    "capability",
    "sourceDocuments",
    "alreadyCovered",
    "coveredByFiles",
    "coveredByTests",
    "missingImplementation",
    "owningLayer",
    "adkPrimitive",
    "prSlice",
    "dependencies",
    "activationGate",
    "coreTouchAllowed",
    "coreGapIfBlocked",
}

FORBIDDEN_IMPLEMENTATION_PREFIXES = (
    "magi_agent/adk_bridge/",
    "magi_agent/runtime/",
    "magi_agent/transport/",
    "magi_agent/config/",
    "magi_agent/routing/",
    "infra/docker/chat-proxy/",
)
FORBIDDEN_IMPLEMENTATION_FILES = {
    "magi_agent/tools/registry.py",
    "magi_agent/tools/dispatcher.py",
    "magi_agent/tools/permission.py",
    "magi_agent/tools/result.py",
}
FORBIDDEN_IMPLEMENTATION_FRAGMENTS = (
    "infra/k8s/",
    ".env",
    "supabase/",
    "vercel",
    "provider/model/live",
)


def _load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _rows() -> dict[str, dict[str, Any]]:
    data = _load_matrix()
    assert data["schemaVersion"] == "codingHarnessConsolidatedMatrix.v1"
    rows = data["rows"]
    return {row["id"]: row for row in rows}


def _load_opencode_delta_matrix() -> dict[str, Any]:
    return json.loads(OPENCODE_DELTA_MATRIX.read_text(encoding="utf-8"))


def _assert_string_list(value: object, *, label: str) -> None:
    assert isinstance(value, list), label
    assert all(isinstance(item, str) and item.strip() for item in value), label


def test_consolidated_plan_file_is_present_and_names_source_documents() -> None:
    text = CONSOLIDATED_PLAN.read_text(encoding="utf-8")

    assert "Python ADK Coding Harness Consolidated Safe Queue Implementation Plan" in text
    assert "Do not modify generic core runtime substrate in this track." in text
    for source_doc in SOURCE_DOCUMENTS:
        assert source_doc in text
        assert (REPO_ROOT / source_doc).is_file(), source_doc


def test_historical_source_documents_are_safe_reference_stubs() -> None:
    stub_paths = (
        "docs/superpowers/plans/2026-05-20-python-adk-claude-code-parity-full-master-plan.md",
        "docs/superpowers/plans/2026-05-20-python-adk-opencode-parity-recipes.md",
        "docs/superpowers/plans/2026-05-20-python-adk-claude-code-parity-recipes.md",
    )

    for source_doc in stub_paths:
        text = (REPO_ROOT / source_doc).read_text(encoding="utf-8")

        assert "Safe Reference Stub" in text
        assert "superseded as execution material" in text
        assert "must not be followed from this PR" in text
        assert "docs/superpowers/plans/2026-05-27-python-adk-coding-harness-consolidated-safe-queue-plan.md" in text
        assert "Task " not in text
        assert "- [ ]" not in text


def test_matrix_rows_have_required_fields_and_machine_row_key() -> None:
    data = _load_matrix()
    row_ids = [row["id"] for row in data["rows"]]

    assert row_ids == list(REQUIRED_ROWS)
    for row in data["rows"]:
        assert "id" in row
        assert set(row) >= ROW_FIELDS | {"id"}
        assert "id" not in ROW_FIELDS
        assert row["sourceDocuments"]
        assert set(row["sourceDocuments"]) <= set(SOURCE_DOCUMENTS)
        assert isinstance(row["alreadyCovered"], bool)
        assert row["coreTouchAllowed"] is False
        assert isinstance(row["coreGapIfBlocked"], str)
        for field in ("coveredByFiles", "coveredByTests", "missingImplementation", "dependencies"):
            _assert_string_list(row[field], label=f"{row['id']} {field}")


def test_matrix_reconciles_exact_source_documents_and_pr_slices() -> None:
    rows = _rows()
    observed_sources = {
        source
        for row in rows.values()
        for source in row["sourceDocuments"]
    }

    assert observed_sources == set(SOURCE_DOCUMENTS)
    assert rows["coding_latest_main_reconciliation"]["prSlice"] == "PR0"
    assert rows["coding_recipe_ownership_boundaries"]["prSlice"] == "PR1"
    assert rows["read_before_edit_and_stale_rejection"]["prSlice"] == "PR2"
    assert rows["patch_apply_diff_test_checkpoint_evidence"]["prSlice"] == "PR3"
    assert rows["bash_safe_subset_and_shell_policy"]["prSlice"] == "PR4"
    assert rows["coding_subagent_roles_and_repair_loop"]["prSlice"] == "PR5"
    assert rows["coding_compaction_and_session_continuity"]["prSlice"] == "PR6"
    assert rows["lsp_code_intelligence_contracts"]["prSlice"] == "PR7"
    assert rows["coding_measurement_eval_and_reliability_train"]["prSlice"] == "PR8"
    assert rows["opencode_delta_coding_rows"]["prSlice"] == "PR9"
    assert rows["final_coding_harness_review"]["prSlice"] == "PR10"


def test_covered_rows_have_file_and_test_refs_while_missing_rows_have_targets() -> None:
    rows = _rows()

    for row in rows.values():
        if row["alreadyCovered"]:
            assert row["coveredByFiles"], row["id"]
            assert row["coveredByTests"], row["id"]
            assert row["missingImplementation"] in (["complete"], ["gap-tests-docs-only"]), row["id"]
        else:
            assert row["missingImplementation"], row["id"]
            assert row["activationGate"].startswith(row["prSlice"]), row["id"]

    assert rows["lsp_code_intelligence_contracts"]["alreadyCovered"] is True
    assert rows["lsp_code_intelligence_contracts"]["coveredByFiles"] == [
        "magi_agent/harness/coding/code_intelligence_contracts.py",
    ]
    assert rows["lsp_code_intelligence_contracts"]["coveredByTests"] == [
        "tests/test_coding_code_intelligence_contracts.py",
    ]
    assert rows["lsp_code_intelligence_contracts"]["missingImplementation"] == ["complete"]
    assert rows["final_coding_harness_review"]["alreadyCovered"] is True
    assert (
        "docs/notes/2026-05-27-python-adk-coding-harness-consolidated-final-review.md"
        in rows["final_coding_harness_review"]["coveredByFiles"]
    )
    assert rows["final_coding_harness_review"]["missingImplementation"] == [
        "complete",
    ]


def test_missing_implementation_targets_do_not_point_at_forbidden_core_paths() -> None:
    for row in _rows().values():
        for target in row["missingImplementation"]:
            if target in {"complete", "gap-tests-docs-only"}:
                continue
            assert target not in FORBIDDEN_IMPLEMENTATION_FILES, (row["id"], target)
            assert not target.startswith(FORBIDDEN_IMPLEMENTATION_PREFIXES), (row["id"], target)
            lowered = target.lower()
            assert not any(fragment in lowered for fragment in FORBIDDEN_IMPLEMENTATION_FRAGMENTS), (
                row["id"],
                target,
            )


def test_opencode_rows_delegate_to_dedicated_delta_matrix_without_duplicate_work() -> None:
    row = _rows()["opencode_delta_coding_rows"]

    assert row["alreadyCovered"] is True
    assert row["coveredByFiles"] == [
        "docs/superpowers/plans/2026-05-26-python-adk-opencode-latest-harness-delta-addendum.md",
        "magi-agent/tests/fixtures/opencode_delta/harness_delta_matrix.json",
        "docs/notes/2026-05-27-python-adk-coding-harness-opencode-delta-reconciliation.md",
    ]
    assert row["coveredByTests"] == [
        "magi-agent/tests/test_coding_harness_consolidated_matrix.py",
        "magi-agent/tests/test_opencode_delta_contract.py",
    ]
    assert row["missingImplementation"] == ["gap-tests-docs-only"]
    assert row["activationGate"] == "PR9-points-to-dedicated-opencode-delta-matrix"
    assert row["defaultOff"] is True
    assert row["liveAuthorityAllowed"] is False

    forbidden_duplicate_targets = tuple(FORBIDDEN_IMPLEMENTATION_PREFIXES) + tuple(
        FORBIDDEN_IMPLEMENTATION_FILES
    )
    for target in row["coveredByFiles"] + row["coveredByTests"] + row["missingImplementation"]:
        assert not target.startswith(forbidden_duplicate_targets), target


def test_opencode_delta_reconciliation_note_covers_every_dedicated_delta_row() -> None:
    assert OPENCODE_DELTA_RECONCILIATION_NOTE.is_file()
    note = OPENCODE_DELTA_RECONCILIATION_NOTE.read_text(encoding="utf-8")
    matrix = _load_opencode_delta_matrix()

    assert "OpenCode Delta Coding Rows Reconciliation" in note
    assert "no live activation" in note
    for status in ("covered", "missing", "delegated"):
        assert status in note

    for delta_row in matrix["rows"]:
        assert delta_row["rowId"] in note
        assert delta_row["status"] in {"covered", "missing", "delegated"}
        assert f"| `{delta_row['rowId']}` | `{delta_row['status']}` |" in note
        assert delta_row["owningLayer"] in note
        assert f"`{delta_row['activationGate']}`" in note


def test_final_coding_harness_review_row_is_complete_and_non_live() -> None:
    row = _rows()["final_coding_harness_review"]

    assert row["alreadyCovered"] is True
    assert row["missingImplementation"] == ["complete"]
    assert row["defaultOff"] is True
    assert row["liveAuthorityAllowed"] is False
    assert row["coreTouchAllowed"] is False
    assert (
        "docs/notes/2026-05-27-python-adk-coding-harness-consolidated-final-review.md"
        in row["coveredByFiles"]
    )
    assert (
        "magi-agent/tests/test_coding_harness_consolidated_matrix.py"
        in row["coveredByTests"]
    )


def test_final_coding_harness_review_note_covers_matrix_prs_and_activation_boundary() -> None:
    assert FINAL_CODING_HARNESS_REVIEW_NOTE.is_file()
    note = FINAL_CODING_HARNESS_REVIEW_NOTE.read_text(encoding="utf-8")

    for reviewer_label in (
        "Reviewer A: Core Boundary",
        "Reviewer B: Permission and Authority",
        "Reviewer C: Artifact/Snapshot/Projection Safety",
        "Reviewer D: Provider/MCP/Header Safety",
        "Reviewer E: Coding Harness Quality",
    ):
        assert reviewer_label in note

    for row_id in _rows():
        assert f"`{row_id}`" in note

    for pr_number in ("1157", "1171", "1185", "1191", "1199", "1202", "1204", "1205", "1207", "1210"):
        assert f"https://github.com/kevin-hs-sohn/magi/pull/{pr_number}" in note

    for deferred_row in (
        "provider_compatibility_fixtures",
        "lsp_lifecycle_contract",
        "provider_header_provenance_allowlist",
        "runtime_event_replay_fence",
        "todo_projection_contract",
        "client_protocol_boundary",
    ):
        assert f"`{deferred_row}`" in note

    for boundary_phrase in (
        "no deploy/live activation until separate approval",
        "no core drift",
        "no live model/tool/provider/MCP/LSP/browser/shell/workspace activation",
    ):
        assert boundary_phrase in note
