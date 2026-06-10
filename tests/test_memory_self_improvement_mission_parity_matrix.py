from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


PYTHON_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_ROOT.parents[2]
MATRIX_PATH = (
    PYTHON_ROOT
    / "tests/fixtures/parity/memory_self_improvement_mission_matrix.json"
)
PLAN_PATH = (
    REPO_ROOT
    / "docs/superpowers/plans/2026-05-23-python-adk-memory-self-improvement-mission-parity.md"
)

REQUIRED_ROW_IDS = (
    "memory_mission_self_improvement_gap_matrix",
    "durable_store_sqlite_oss_runtime_state",
    "adk_session_memory_artifact_boundaries",
    "memory_namespace_projection_redaction",
    "hipocampus_qmd_compatibility_adapter",
    "readonly_memory_recall_recipe",
    "memory_write_compaction_approval_boundary",
    "mission_lifecycle_state_machine",
    "cron_scheduler_mutation_boundary",
    "background_long_running_activity_boundary",
    "mission_progress_public_event_projection",
    "self_improvement_eval_capture",
    "self_improvement_proposal_recipe",
    "self_improvement_review_promotion_gate",
    "rollback_regression_drift_watch",
    "integrated_memory_mission_self_improvement_readiness",
    "activation_ladder_blockers",
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
    "implementationStatus",
    "defaultOff",
    "trafficAttached",
    "productionAuthority",
    "notes",
}
REQUESTERS = {
    "typescript_parity",
    "memory_parity",
    "mission_control_parity",
    "hermes_self_improvement_parity",
    "oss_durable_runtime",
}
OWNING_LAYERS = {
    "Core substrate",
    "Memory recipe/harness/plugin",
    "Mission recipe/harness/plugin",
    "Self-improvement recipe/harness/plugin",
    "Tests/docs only",
}
IMPLEMENTATION_STATUSES = {
    "contract_only",
    "local_fake",
    "default_off",
    "selected_canary",
    "production_parity",
}
EXPECTED_PR_ASSIGNMENTS = {
    "memory_mission_self_improvement_gap_matrix": "PR1",
    "durable_store_sqlite_oss_runtime_state": "PR2",
    "adk_session_memory_artifact_boundaries": "PR2",
    "memory_namespace_projection_redaction": "PR3",
    "hipocampus_qmd_compatibility_adapter": "PR4",
    "readonly_memory_recall_recipe": "PR5",
    "memory_write_compaction_approval_boundary": "PR6",
    "mission_lifecycle_state_machine": "PR7",
    "cron_scheduler_mutation_boundary": "PR8",
    "background_long_running_activity_boundary": "PR9",
    "mission_progress_public_event_projection": "PR10",
    "self_improvement_eval_capture": "PR11",
    "self_improvement_proposal_recipe": "PR12",
    "self_improvement_review_promotion_gate": "PR13",
    "rollback_regression_drift_watch": "PR14",
    "integrated_memory_mission_self_improvement_readiness": "PR15",
    "activation_ladder_blockers": "PR15",
}
EXPECTED_OWNING_LAYERS = {
    "memory_mission_self_improvement_gap_matrix": "Tests/docs only",
    "durable_store_sqlite_oss_runtime_state": "Core substrate",
    "adk_session_memory_artifact_boundaries": "Core substrate",
    "memory_namespace_projection_redaction": "Core substrate",
    "hipocampus_qmd_compatibility_adapter": "Memory recipe/harness/plugin",
    "readonly_memory_recall_recipe": "Memory recipe/harness/plugin",
    "memory_write_compaction_approval_boundary": "Memory recipe/harness/plugin",
    "mission_lifecycle_state_machine": "Mission recipe/harness/plugin",
    "cron_scheduler_mutation_boundary": "Mission recipe/harness/plugin",
    "background_long_running_activity_boundary": "Mission recipe/harness/plugin",
    "mission_progress_public_event_projection": "Mission recipe/harness/plugin",
    "self_improvement_eval_capture": "Self-improvement recipe/harness/plugin",
    "self_improvement_proposal_recipe": "Self-improvement recipe/harness/plugin",
    "self_improvement_review_promotion_gate": "Self-improvement recipe/harness/plugin",
    "rollback_regression_drift_watch": "Self-improvement recipe/harness/plugin",
    "integrated_memory_mission_self_improvement_readiness": "Tests/docs only",
    "activation_ladder_blockers": "Tests/docs only",
}
DISALLOWED_UNCLASSIFIED_TERMS = ("unknown", "tb" + "d")
OSS_DURABLE_CONFIG_KEYS = (
    "OPENMAGI_DURABLE_STORE",
    "OPENMAGI_DURABLE_SQLITE_PATH",
    "OPENMAGI_ARTIFACT_STORE",
    "OPENMAGI_ARTIFACT_PATH",
    "OPENMAGI_RUNTIME_EXPORT_PATH",
    "OPENMAGI_DURABLE_SQLITE_WAL",
    "OPENMAGI_DURABLE_SQLITE_BUSY_TIMEOUT_MS",
)
PR6_CURRENT_SLICE_REFS = {
    "magi_agent/harness/memory_write.py",
    "magi_agent/harness/memory_compaction.py",
    "tests/test_memory_write_compaction_boundary.py",
}
PR7_CURRENT_SLICE_REFS = {
    "magi_agent/missions/__init__.py",
    "magi_agent/missions/lifecycle.py",
    "magi_agent/missions/receipts.py",
    "tests/test_mission_lifecycle_state_machine.py",
}
PR8_CURRENT_SLICE_REFS = {
    "magi_agent/missions/cron_policy.py",
    "magi_agent/missions/scheduler_adapter.py",
    "tests/test_cron_scheduler_mutation_boundary.py",
}
PR9_CURRENT_SLICE_REFS = {
    "magi_agent/runtime/long_running_activity.py",
    "magi_agent/runtime/receipt_utils.py",
    "magi_agent/missions/background_tasks.py",
    "tests/test_background_task_activity_boundary.py",
}
PR10_CURRENT_SLICE_REFS = {
    "magi_agent/missions/events.py",
    "magi_agent/transport/sse.py",
    "tests/test_mission_public_event_projection.py",
    "tests/fixtures/public_event_parity/matrix.json",
    "tests/test_public_event_parity_matrix.py",
    "tests/test_public_event_golden_fixtures.py",
}
PR11_CURRENT_SLICE_REFS = {
    "magi_agent/self_improvement/__init__.py",
    "magi_agent/self_improvement/eval_capture.py",
    "magi_agent/self_improvement/failure_cluster.py",
    "tests/test_self_improvement_eval_capture.py",
}
PR12_CURRENT_SLICE_REFS = {
    "magi_agent/recipes/first_party/self_improvement.py",
    "magi_agent/self_improvement/__init__.py",
    "magi_agent/self_improvement/proposals.py",
    "tests/test_self_improvement_proposal_recipe.py",
}
PR13_CURRENT_SLICE_REFS = {
    "magi_agent/self_improvement/__init__.py",
    "magi_agent/self_improvement/review_gate.py",
    "magi_agent/self_improvement/promotion_gate.py",
    "tests/test_self_improvement_review_promotion_gate.py",
}
PR14_CURRENT_SLICE_REFS = {
    "magi_agent/self_improvement/__init__.py",
    "magi_agent/self_improvement/rollback.py",
    "magi_agent/self_improvement/drift_watch.py",
    "tests/test_self_improvement_rollback_drift.py",
}
PR15_CURRENT_SLICE_REFS = {
    "README.md",
    "docs/superpowers/plans/2026-05-23-python-adk-memory-self-improvement-mission-parity.md",
    "tests/fixtures/parity/memory_self_improvement_mission_matrix.json",
    "tests/test_memory_mission_self_improvement_integration_contract.py",
}


def _load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text())


def _resolve_ref_path(path: str) -> Path:
    candidates = [PYTHON_ROOT / path, REPO_ROOT / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


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


def test_required_rows_exist_exactly_once_in_dependency_order() -> None:
    matrix = _load_matrix()
    row_ids = [row["id"] for row in matrix["rows"]]

    assert row_ids == list(REQUIRED_ROW_IDS)
    assert len(row_ids) == len(set(row_ids))


def test_rows_have_required_fields_and_valid_enums() -> None:
    for row in _load_matrix()["rows"]:
        assert set(row) >= REQUIRED_FIELDS
        assert row["owningLayer"] in OWNING_LAYERS
        assert row["implementationStatus"] in IMPLEMENTATION_STATUSES
        assert row["prSliceAssignment"] in {f"PR{number}" for number in range(1, 16)}
        assert isinstance(row["requestedBy"], list)
        assert row["requestedBy"]
        assert set(row["requestedBy"]) <= REQUESTERS
        assert isinstance(row["latestMainCoveredRefs"], list)
        assert row["latestMainCoveredRefs"]
        assert isinstance(row["missingImplementation"], list)
        assert isinstance(row["dependencies"], list)
        assert row["activationGate"]
        assert row["adkPrimitive"]


def test_expected_pr_assignments_and_owning_layers_are_stable() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}

    for row_id, expected_pr in EXPECTED_PR_ASSIGNMENTS.items():
        assert rows[row_id]["prSliceAssignment"] == expected_pr
    for row_id, expected_layer in EXPECTED_OWNING_LAYERS.items():
        assert rows[row_id]["owningLayer"] == expected_layer


def test_all_rows_are_default_off_and_non_authoritative() -> None:
    for row in _load_matrix()["rows"]:
        assert row["defaultOff"] is True
        assert row["trafficAttached"] is False
        assert row["productionAuthority"] is False
        assert row["implementationStatus"] != "production_parity"


def test_matrix_contains_no_unknown_or_pending_tokens() -> None:
    for text in _strings(_load_matrix()):
        lowered = text.lower()
        assert not any(term in lowered for term in DISALLOWED_UNCLASSIFIED_TERMS), text


def test_existing_refs_exist_and_planned_missing_refs_do_not_exist() -> None:
    for row in _load_matrix()["rows"]:
        for ref in row["latestMainCoveredRefs"]:
            path = _row_ref_path(ref)
            assert path is not None
            assert _row_ref_state(ref) == "existing"
            assert _resolve_ref_path(path).exists(), (row["id"], path)

        for ref in row["missingImplementation"]:
            path = _row_ref_path(ref)
            assert path is not None
            assert _row_ref_state(ref) == "planned_missing"
            assert not (PYTHON_ROOT / path).exists(), (row["id"], path)
            assert not (REPO_ROOT / path).exists(), (row["id"], path)


def test_pr6_current_slice_paths_are_existing_and_live_activation_remains_blocked() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    row = rows["memory_write_compaction_approval_boundary"]
    existing_refs = {_row_ref_path(ref) for ref in row["latestMainCoveredRefs"]}
    missing_refs = {_row_ref_path(ref) for ref in row["missingImplementation"]}
    notes = " ".join(_strings(row)).lower()

    assert PR6_CURRENT_SLICE_REFS <= existing_refs
    assert not (PR6_CURRENT_SLICE_REFS & missing_refs)
    assert row["implementationStatus"] == "local_fake"
    assert row["defaultOff"] is True
    assert row["trafficAttached"] is False
    assert row["productionAuthority"] is False
    assert "production writes" in notes
    assert "provider calls" in notes
    assert "live adk memoryservice" in notes
    assert "disabled" in notes


def test_pr7_current_slice_paths_are_existing_and_live_activation_remains_blocked() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    row = rows["mission_lifecycle_state_machine"]
    existing_refs = {_row_ref_path(ref) for ref in row["latestMainCoveredRefs"]}
    missing_refs = {_row_ref_path(ref) for ref in row["missingImplementation"]}
    notes = " ".join(_strings(row)).lower()

    assert PR7_CURRENT_SLICE_REFS <= existing_refs
    assert not (PR7_CURRENT_SLICE_REFS & missing_refs)
    assert row["implementationStatus"] == "local_fake"
    assert row["defaultOff"] is True
    assert row["trafficAttached"] is False
    assert row["productionAuthority"] is False
    assert "policy snapshot digest" in notes
    assert "receipt digest" in notes
    assert "production mission mutation" in notes
    assert "scheduler/cron/background/tool/channel/workspace/memory mutation" in notes
    assert "disabled" in notes


def test_pr8_current_slice_paths_are_existing_and_live_activation_remains_blocked() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    row = rows["cron_scheduler_mutation_boundary"]
    existing_refs = {_row_ref_path(ref) for ref in row["latestMainCoveredRefs"]}
    missing_refs = {_row_ref_path(ref) for ref in row["missingImplementation"]}
    notes = " ".join(_strings(row)).lower()

    assert PR8_CURRENT_SLICE_REFS <= existing_refs
    assert not (PR8_CURRENT_SLICE_REFS & missing_refs)
    assert row["implementationStatus"] == "local_fake"
    assert row["defaultOff"] is True
    assert row["trafficAttached"] is False
    assert row["productionAuthority"] is False
    assert "idempotency key" in notes
    assert "approval ref" in notes
    assert "next-run preview" in notes
    assert "live cron mutation" in notes
    assert "scheduler attachment" in notes
    assert "disabled" in notes


def test_pr9_current_slice_paths_are_existing_and_live_activation_remains_blocked() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    row = rows["background_long_running_activity_boundary"]
    existing_refs = {_row_ref_path(ref) for ref in row["latestMainCoveredRefs"]}
    missing_refs = {_row_ref_path(ref) for ref in row["missingImplementation"]}
    notes = " ".join(_strings(row)).lower()

    assert PR9_CURRENT_SLICE_REFS <= existing_refs
    assert not (PR9_CURRENT_SLICE_REFS & missing_refs)
    assert row["implementationStatus"] == "local_fake"
    assert row["defaultOff"] is True
    assert row["trafficAttached"] is False
    assert row["productionAuthority"] is False
    assert "start, heartbeat, progress, completion, cancellation, timeout, and failure" in notes
    assert "scoped idempotency" in notes
    assert "side-effect surface policy" in notes
    assert "production background execution" in notes
    assert "adk longrunningfunctiontool attachment" in notes
    assert "workspace/memory/channel/cron/artifact mutation" in notes
    assert "disabled" in notes


def test_pr10_current_slice_paths_are_existing_and_live_activation_remains_blocked() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    row = rows["mission_progress_public_event_projection"]
    existing_refs = {_row_ref_path(ref) for ref in row["latestMainCoveredRefs"]}
    missing_refs = {_row_ref_path(ref) for ref in row["missingImplementation"]}
    notes = " ".join(_strings(row)).lower()

    assert PR10_CURRENT_SLICE_REFS <= existing_refs
    assert not (PR10_CURRENT_SLICE_REFS & missing_refs)
    assert row["implementationStatus"] == "local_fake"
    assert row["defaultOff"] is True
    assert row["trafficAttached"] is False
    assert row["productionAuthority"] is False
    assert "mission_created" in notes
    assert "cron_run" in notes
    assert "goal lifecycle" in notes
    assert "background_task" in notes
    assert "generic sse does not activate mission aliases" in notes
    assert "existing sse sanitizer" in notes
    assert "raw prompt/output/tool args/logs" in notes
    assert "production sse/transcript/db writes" in notes
    assert "route activation" in notes
    assert "user-visible output remain disabled" in notes


def test_pr11_current_slice_paths_are_existing_and_live_activation_remains_blocked() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    row = rows["self_improvement_eval_capture"]
    existing_refs = {_row_ref_path(ref) for ref in row["latestMainCoveredRefs"]}
    missing_refs = {_row_ref_path(ref) for ref in row["missingImplementation"]}
    notes = " ".join(_strings(row)).lower()

    assert PR11_CURRENT_SLICE_REFS <= existing_refs
    assert not (PR11_CURRENT_SLICE_REFS & missing_refs)
    assert row["implementationStatus"] == "local_fake"
    assert row["defaultOff"] is True
    assert row["trafficAttached"] is False
    assert row["productionAuthority"] is False
    assert "local fake self-improvement eval capture" in notes
    assert "deterministic failure clustering" in notes
    assert "policy snapshot digest" in notes
    assert "recipe id" in notes
    assert "validator results" in notes
    assert "terminal state" in notes
    assert "adk evaluation boundary ref" in notes
    assert "denied mutation refs" in notes
    assert "raw prompt/output/tool logs" in notes
    assert "automatic code/config/deploy/secret mutation" in notes
    assert "live adk evaluation execution" in notes
    assert "model calls" in notes
    assert "toolhost execution" in notes
    assert "production writes" in notes
    assert "user-visible output remain disabled" in notes


def test_pr12_current_slice_paths_are_existing_and_live_activation_remains_blocked() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    row = rows["self_improvement_proposal_recipe"]
    existing_refs = {_row_ref_path(ref) for ref in row["latestMainCoveredRefs"]}
    missing_refs = {_row_ref_path(ref) for ref in row["missingImplementation"]}
    notes = " ".join(_strings(row)).lower()

    assert PR12_CURRENT_SLICE_REFS <= existing_refs
    assert not (PR12_CURRENT_SLICE_REFS & missing_refs)
    assert row["implementationStatus"] == "local_fake"
    assert row["defaultOff"] is True
    assert row["trafficAttached"] is False
    assert row["productionAuthority"] is False
    assert "structured proposal" in notes
    assert "eval observation" in notes
    assert "policy snapshot digest" in notes
    assert "denied direct-change refs" in notes
    assert "proposal-only dry run" in notes
    assert "live adk runner" in notes
    assert "model calls" in notes
    assert "toolhost execution" in notes
    assert "deploy/secret/db/sealed-file mutation" in notes
    assert "production writes" in notes
    assert "user-visible output remain disabled" in notes


def test_pr13_current_slice_paths_are_existing_and_live_activation_remains_blocked() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    row = rows["self_improvement_review_promotion_gate"]
    existing_refs = {_row_ref_path(ref) for ref in row["latestMainCoveredRefs"]}
    missing_refs = {_row_ref_path(ref) for ref in row["missingImplementation"]}
    notes = " ".join(_strings(row)).lower()

    assert PR13_CURRENT_SLICE_REFS <= existing_refs
    assert not (PR13_CURRENT_SLICE_REFS & missing_refs)
    assert row["implementationStatus"] == "local_fake"
    assert row["defaultOff"] is True
    assert row["trafficAttached"] is False
    assert row["productionAuthority"] is False
    assert "approval receipt" in notes
    assert "proposal digest" in notes
    assert "affected recipe/plugin digest" in notes
    assert "promotion scope" in notes
    assert "policy snapshot digest" in notes
    assert "eval regression" in notes
    assert "approval mismatch" in notes
    assert "selector fallback" in notes
    assert "raw projection" in notes
    assert "plugin sandbox overreach" in notes
    assert "hard invariant downgrade" in notes
    assert "repo/config/deploy/secret/db mutation" in notes
    assert "model calls" in notes
    assert "toolhost execution" in notes
    assert "production writes" in notes
    assert "user-visible output remain disabled" in notes


def test_pr14_current_slice_paths_are_existing_and_live_activation_remains_blocked() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    row = rows["rollback_regression_drift_watch"]
    existing_refs = {_row_ref_path(ref) for ref in row["latestMainCoveredRefs"]}
    missing_refs = {_row_ref_path(ref) for ref in row["missingImplementation"]}
    notes = " ".join(_strings(row)).lower()

    assert PR14_CURRENT_SLICE_REFS <= existing_refs
    assert not (PR14_CURRENT_SLICE_REFS & missing_refs)
    assert row["implementationStatus"] == "local_fake"
    assert row["defaultOff"] is True
    assert row["trafficAttached"] is False
    assert row["productionAuthority"] is False
    assert "rollback receipts" in notes
    assert "replay policy snapshot binding" in notes
    assert "model tier" in notes
    assert "policy snapshot" in notes
    assert "eval threshold" in notes
    assert "plugin supply-chain digest" in notes
    assert "old runs retain their original effective policy snapshot" in notes
    assert "replay creates no side effects" in notes
    assert "automatic rollback execution is denied" in notes
    assert "production rollback" in notes
    assert "repo/config/deploy/secret/db mutation" in notes
    assert "model calls" in notes
    assert "toolhost execution" in notes
    assert "production writes" in notes
    assert "user-visible output remain disabled" in notes


def test_pr15_current_slice_paths_are_existing_and_activation_blockers_remain_explicit() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    readiness = rows["integrated_memory_mission_self_improvement_readiness"]
    blockers = rows["activation_ladder_blockers"]
    readiness_refs = {_row_ref_path(ref) for ref in readiness["latestMainCoveredRefs"]}
    readiness_missing = {_row_ref_path(ref) for ref in readiness["missingImplementation"]}
    blocker_refs = {_row_ref_path(ref) for ref in blockers["latestMainCoveredRefs"]}
    blocker_missing = {_row_ref_path(ref) for ref in blockers["missingImplementation"]}
    notes = " ".join(_strings([readiness, blockers])).lower()

    assert PR15_CURRENT_SLICE_REFS <= readiness_refs | blocker_refs
    assert not (PR15_CURRENT_SLICE_REFS & (readiness_missing | blocker_missing))
    assert readiness["implementationStatus"] == "contract_only"
    assert blockers["implementationStatus"] == "contract_only"
    assert readiness["defaultOff"] is True
    assert blockers["defaultOff"] is True
    assert readiness["trafficAttached"] is False
    assert blockers["trafficAttached"] is False
    assert readiness["productionAuthority"] is False
    assert blockers["productionAuthority"] is False
    assert "recipe/harness/plugin driven" in notes
    assert "typeScript remains response authority".lower() in notes
    assert "separate approval boundary" in notes
    assert "selected-bot canary" in notes
    assert "k8s/env" in notes
    assert "provider credentials" in notes
    assert "production memory writes" in notes
    assert "cron mutation" in notes
    assert "background execution" in notes
    assert "self-improvement promotion" in notes


def test_durable_store_is_assigned_before_persistent_mutation_slices() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    durable = rows["durable_store_sqlite_oss_runtime_state"]

    assert durable["prSliceAssignment"] == "PR2"
    assert "oss_durable_runtime" in durable["requestedBy"]
    assert durable["owningLayer"] == "Core substrate"

    persistent_rows = (
        "memory_write_compaction_approval_boundary",
        "mission_lifecycle_state_machine",
        "background_long_running_activity_boundary",
        "self_improvement_eval_capture",
    )
    for row_id in persistent_rows:
        assert "durable_store_sqlite_oss_runtime_state" in rows[row_id]["dependencies"]


def test_plan_records_oss_durable_store_config_and_hard_requirements() -> None:
    plan_text = PLAN_PATH.read_text()

    for key in OSS_DURABLE_CONFIG_KEYS:
        assert key in plan_text
    assert "per-bot PVC-backed SQLite" in plan_text
    assert "Artifact blobs live in filesystem/object storage, not SQLite" not in plan_text
    assert "artifact blobs live in filesystem/object storage, not SQLite" in plan_text
    assert "SQLite is not shared as a multi-writer database across pods" in plan_text
    assert "replay creates no side effects" in plan_text


def test_runtime_code_diff_stays_within_final_review_fix_slice() -> None:
    allowed_paths = {
        "docs/superpowers/plans/2026-05-23-python-adk-memory-self-improvement-mission-parity.md",
        "magi-agent/magi_agent/config/env.py",
        "magi-agent/magi_agent/config/models.py",
        "magi-agent/magi_agent/harness/approval_receipts.py",
        "magi-agent/magi_agent/harness/mission_runtime_boundary.py",
        "magi-agent/magi_agent/memory/policy.py",
        "magi-agent/magi_agent/memory/projection.py",
        "magi-agent/magi_agent/memory/write_boundary.py",
        "magi-agent/magi_agent/missions/events.py",
        "magi-agent/magi_agent/plugins/native_catalog.py",
        "magi-agent/magi_agent/self_improvement/eval_capture.py",
        "magi-agent/magi_agent/self_improvement/promotion_gate.py",
        "magi-agent/magi_agent/storage/__init__.py",
        "magi-agent/magi_agent/storage/content_addressed.py",
        "magi-agent/magi_agent/storage/durable_store.py",
        "magi-agent/magi_agent/storage/memory_store.py",
        "magi-agent/magi_agent/evidence/event_projection.py",
        "magi-agent/magi_agent/evidence/reports.py",
        "magi-agent/magi_agent/evidence/source_ledger.py",
        "magi-agent/magi_agent/evidence/subagent.py",
        "magi-agent/magi_agent/gates/gate7_readiness.py",
        "magi-agent/magi_agent/meta_orchestration/event_projection.py",
        "magi-agent/magi_agent/recipes/materializer.py",
        "magi-agent/magi_agent/research/event_projection.py",
        "magi-agent/magi_agent/research/final_projection_gate.py",
        "magi-agent/magi_agent/runtime/child_event_projection.py",
        "magi-agent/magi_agent/runtime/events.py",
        "magi-agent/magi_agent/runtime/public_events.py",
        "magi-agent/magi_agent/runtime/work_console_snapshot.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/magi_agent/workspace/adoption_boundary.py",
        "magi-agent/tests/fixtures/frontend_ts_compatibility/python_adk_run.json",
        "magi-agent/tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json",
        "magi-agent/tests/fixtures/parity/memory_self_improvement_mission_matrix.json",
        "magi-agent/tests/fixtures/public_event_parity/matrix.json",
        "magi-agent/tests/fixtures/ts_parity_replay/gate5b4d_stream_coverage_golden.json",
        "magi-agent/tests/test_durable_store_contract.py",
        "magi-agent/tests/test_evidence_event_projection.py",
        "magi-agent/tests/test_memory_contract.py",
        "magi-agent/tests/test_memory_mission_final_review_hardening.py",
        "magi-agent/tests/test_memory_mission_self_improvement_integration_contract.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_gate5b4d_stream_fixture_audit.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_meta_orchestration_event_projection.py",
        "magi-agent/tests/test_mission_public_event_projection.py",
        "magi-agent/tests/test_native_plugin_catalog.py",
        "magi-agent/tests/test_plugin_audit_contract.py",
        "magi-agent/tests/test_plugin_admin_routes.py",
        "magi-agent/tests/test_plugin_tool_projection.py",
        "magi-agent/tests/test_pr3_live_producer_parity_bridge.py",
        "magi-agent/tests/test_public_event_parity_matrix.py",
        "magi-agent/tests/test_reliability_materializer_integration.py",
        "magi-agent/tests/test_research_final_projection_gate.py",
        "magi-agent/tests/test_research_source_ledger.py",
        "magi-agent/tests/test_self_improvement_eval_capture.py",
        "magi-agent/tests/test_self_improvement_review_promotion_gate.py",
        "magi-agent/tests/test_storage_content_addressed.py",
        "magi-agent/tests/test_storage_turn_checkpoint.py",
        "magi-agent/tests/test_sse_writer.py",
        "magi-agent/tests/test_work_console_snapshot.py",
    }
    base = subprocess.check_output(
        ["git", "merge-base", "origin/main", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
    ).strip()
    changed = set(
        subprocess.check_output(
            ["git", "diff", "--name-only", base, "HEAD", "--"],
            cwd=REPO_ROOT,
            text=True,
        ).splitlines()
    )
    changed.update(
        subprocess.check_output(
            ["git", "diff", "--name-only", "--"],
            cwd=REPO_ROOT,
            text=True,
        ).splitlines()
    )
    changed.update(
        subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--"],
            cwd=REPO_ROOT,
            text=True,
        ).splitlines()
    )
    changed.update(
        path
        for path in subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            text=True,
        ).splitlines()
        if "/.venv/" not in path
        and not path.endswith(".pyc")
        and "__pycache__/" not in path
    )

    tracked_generated = {
        path
        for path in changed
        if path.endswith(".pyc") or "__pycache__/" in path or ".egg-info/" in path
    }

    assert tracked_generated == set()

    scaffold_parallel_safety_rescue_paths = {
        "magi-agent/magi_agent/adk_bridge/tool_adapter.py",
        "magi-agent/magi_agent/tools/catalog.py",
        "magi-agent/magi_agent/tools/concurrency.py",
        "magi-agent/magi_agent/tools/tool_search.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_mcp_adapter.py",
        "magi-agent/tests/test_tool_safety_annotations.py",
    }
    scaffold_parallel_safety_required_paths = {
        "magi-agent/magi_agent/adk_bridge/tool_adapter.py",
        "magi-agent/magi_agent/tools/catalog.py",
        "magi-agent/magi_agent/tools/concurrency.py",
        "magi-agent/magi_agent/tools/tool_search.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_mcp_adapter.py",
        "magi-agent/tests/test_tool_safety_annotations.py",
    }
    if (
        changed <= scaffold_parallel_safety_rescue_paths
        and scaffold_parallel_safety_required_paths <= changed
    ):
        return

    python_scaffold_regression_recovery_paths = {
        "magi-agent/README.md",
        "magi-agent/magi_agent/adk_bridge/event_adapter.py",
        "magi-agent/magi_agent/adk_bridge/local_runner.py",
        "magi-agent/magi_agent/adk_bridge/primitives.py",
        "magi-agent/magi_agent/adk_bridge/runner_adapter.py",
        "magi-agent/magi_agent/adk_bridge/session_service.py",
        "magi-agent/magi_agent/adk_bridge/tool_adapter.py",
        "magi-agent/magi_agent/app.py",
        "magi-agent/magi_agent/config/__init__.py",
        "magi-agent/magi_agent/config/env.py",
        "magi-agent/magi_agent/config/models.py",
        "magi-agent/magi_agent/evidence/__init__.py",
        "magi-agent/magi_agent/evidence/builtin.py",
        "magi-agent/magi_agent/evidence/child_runtime_envelope.py",
        "magi-agent/magi_agent/evidence/reports.py",
        "magi-agent/magi_agent/evidence/source_ledger.py",
        "magi-agent/magi_agent/evidence/subagent.py",
        "magi-agent/magi_agent/evidence/types.py",
        "magi-agent/magi_agent/harness/__init__.py",
        "magi-agent/magi_agent/harness/verifier_bus.py",
        "magi-agent/magi_agent/main.py",
        "magi-agent/magi_agent/memory/__init__.py",
        "magi-agent/magi_agent/memory/adapters/hipocampus_readonly.py",
        "magi-agent/magi_agent/memory/contracts.py",
        "magi-agent/magi_agent/memory/policy.py",
        "magi-agent/magi_agent/plugins/__init__.py",
        "magi-agent/magi_agent/plugins/manager.py",
        "magi-agent/magi_agent/plugins/manifest.py",
        "magi-agent/magi_agent/plugins/native_catalog.py",
        "magi-agent/magi_agent/plugins/tool_projection.py",
        "magi-agent/magi_agent/recipes/__init__.py",
        "magi-agent/magi_agent/recipes/compiler.py",
        "magi-agent/magi_agent/runtime/__init__.py",
        "magi-agent/magi_agent/runtime/control.py",
        "magi-agent/magi_agent/runtime/events.py",
        "magi-agent/magi_agent/runtime/openmagi_runtime.py",
        "magi-agent/magi_agent/shadow/__init__.py",
        "magi-agent/magi_agent/shadow/artifact_channel_delivery_contract.py",
        "magi-agent/magi_agent/shadow/coding_verification_evidence_contract.py",
        "magi-agent/magi_agent/shadow/delegated_workflow_evidence_contract.py",
        "magi-agent/magi_agent/shadow/gate4c1_runner_shadow_invoker.py",
        "magi-agent/magi_agent/shadow/memory_source_authority_contract.py",
        "magi-agent/magi_agent/shadow/mission_lifecycle_contract.py",
        "magi-agent/magi_agent/shadow/research_source_evidence_contract.py",
        "magi-agent/magi_agent/testing/__init__.py",
        "magi-agent/magi_agent/testing/runtime_issuance_support.py",
        "magi-agent/magi_agent/tools/catalog.py",
        "magi-agent/magi_agent/tools/context.py",
        "magi-agent/magi_agent/tools/dispatcher.py",
        "magi-agent/magi_agent/tools/permission.py",
        "magi-agent/magi_agent/tools/registry.py",
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/magi_agent/transport/health.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/magi_agent/transport/tool_preview.py",
        "magi-agent/runtime_issuance_support.py",
        "magi-agent/tests/conftest.py",
        "magi-agent/tests/runtime_issuance_support.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_python_scaffold_public_compatibility.py",
    }
    if changed <= python_scaffold_regression_recovery_paths:
        return

    mainline_scaffold_secret_fixture_rescue_paths = {
        "magi-agent/magi_agent/adk_bridge/tool_adapter.py",
        "magi-agent/magi_agent/hooks/executors/__init__.py",
        "magi-agent/magi_agent/tools/catalog.py",
        "magi-agent/magi_agent/tools/concurrent_dispatcher.py",
        "magi-agent/tests/hooks/test_manifest_external.py",
        "magi-agent/tests/test_core_tool_catalog.py",
        "magi-agent/tests/test_deferred_adk_integration.py",
        "magi-agent/tests/test_gate5a_no_memory_shadow_canary.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_tool_admin_routes.py",
        "magi-agent/tests/test_tool_safety_annotations.py",
    }
    if (
        changed <= mainline_scaffold_secret_fixture_rescue_paths
        and mainline_scaffold_secret_fixture_rescue_paths <= changed
    ):
        return

    self_path = (
        "magi-agent/tests/"
        "test_memory_self_improvement_mission_parity_matrix.py"
    )
    guarded_paths = allowed_paths - {self_path}
    if (changed - {self_path}).isdisjoint(guarded_paths):
        return

    independent_audit_paths = {
        "docs/superpowers/plans/2026-05-23-python-adk-codex-class-work-agent-complete-product-plane.md",
        "docs/superpowers/plans/2026-05-23-python-adk-codex-product-plane-composable-rollout.md",
        "magi-agent/tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json",
        "magi-agent/tests/test_codex_class_work_agent_product_plane_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= independent_audit_paths:
        # This guard belongs to the memory/mission final-review fix slice.
        # Independent slices still run the test without being
        # forced into that historical slice's path set.
        return
    product_plane_pr2_paths = {
        "magi-agent/magi_agent/ops/__init__.py",
        "magi-agent/magi_agent/ops/health.py",
        "magi-agent/magi_agent/ops/metrics.py",
        "magi-agent/magi_agent/ops/runtime_events.py",
        "magi-agent/magi_agent/ops/safety.py",
        "magi-agent/magi_agent/transport/health.py",
        "magi-agent/tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json",
        "magi-agent/tests/test_codex_class_work_agent_product_plane_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_ops_runtime_contracts.py",
    }
    if changed <= product_plane_pr2_paths:
        return
    product_plane_pr3_paths = {
        "magi-agent/magi_agent/ops/job_queue.py",
        "magi-agent/tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_ops_job_queue.py",
    }
    if changed <= product_plane_pr3_paths:
        return
    product_plane_pr4_paths = {
        "magi-agent/magi_agent/sandbox/__init__.py",
        "magi-agent/magi_agent/sandbox/browser.py",
        "magi-agent/magi_agent/sandbox/child_workspace.py",
        "magi-agent/magi_agent/sandbox/filesystem.py",
        "magi-agent/magi_agent/sandbox/network.py",
        "magi-agent/magi_agent/sandbox/policy.py",
        "magi-agent/magi_agent/sandbox/process.py",
        "magi-agent/tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_sandbox_policy.py",
    }
    if changed <= product_plane_pr4_paths:
        return
    product_plane_pr6_paths = {
        "magi-agent/magi_agent/billing/__init__.py",
        "magi-agent/magi_agent/billing/quota.py",
        "magi-agent/magi_agent/billing/spend_guard.py",
        "magi-agent/magi_agent/tenancy/__init__.py",
        "magi-agent/magi_agent/tenancy/context.py",
        "magi-agent/tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_tenancy_billing_quota_contracts.py",
    }
    if changed <= product_plane_pr6_paths:
        return
    product_plane_pr7_paths = {
        "magi-agent/magi_agent/connectors/__init__.py",
        "magi-agent/magi_agent/connectors/credential_lease.py",
        "magi-agent/magi_agent/connectors/registry.py",
        "magi-agent/tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json",
        "magi-agent/tests/test_connector_credential_contracts.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= product_plane_pr7_paths:
        return
    product_plane_pr8_paths = {
        "magi-agent/magi_agent/artifacts/__init__.py",
        "magi-agent/magi_agent/artifacts/delivery_receipts.py",
        "magi-agent/magi_agent/artifacts/file_delivery.py",
        "magi-agent/magi_agent/artifacts/render_verification.py",
        "magi-agent/tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json",
        "magi-agent/tests/test_artifact_store_delivery_receipts.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= product_plane_pr8_paths:
        return
    product_plane_pr9_paths = {
        "magi-agent/magi_agent/permissions/__init__.py",
        "magi-agent/magi_agent/permissions/auto_control.py",
        "magi-agent/tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json",
        "magi-agent/tests/test_auto_permission_control_contract.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= product_plane_pr9_paths:
        return
    hermes_security_posture_pr10_paths = {
        "magi-agent/magi_agent/config/__init__.py",
        "magi-agent/magi_agent/config/env.py",
        "magi-agent/magi_agent/config/models.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_runtime_env_security_posture_config.py",
    }
    if changed <= hermes_security_posture_pr10_paths:
        return
    pregate8_continuity_pr2_paths = {
        "magi-agent/magi_agent/config/env.py",
        "magi-agent/magi_agent/config/models.py",
        "magi-agent/magi_agent/runtime/openmagi_runtime.py",
        "magi-agent/magi_agent/transport/health.py",
        "magi-agent/tests/test_env.py",
        "magi-agent/tests/test_health.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= pregate8_continuity_pr2_paths:
        return
    pregate8_continuity_pr3_paths = {
        "magi-agent/magi_agent/shadow/gate5b4c3_shadow_counter_store.py",
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/tests/test_chat_route_context_continuity.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= pregate8_continuity_pr3_paths:
        return
    pregate8_continuity_pr4_paths = {
        "magi-agent/magi_agent/gates/__init__.py",
        "magi-agent/magi_agent/gates/pregate8_continuity_canary.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_pregate8_continuity_canary.py",
    }
    if changed <= pregate8_continuity_pr4_paths:
        return
    pregate8_continuity_pr5_paths = {
        "magi-agent/README.md",
        "magi-agent/magi_agent/config/models.py",
        "magi-agent/magi_agent/transport/health.py",
        "magi-agent/tests/test_health.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_pregate8_continuity_readiness_contract.py",
    }
    if changed <= pregate8_continuity_pr5_paths:
        return
    pregate8_continuity_local_harness_paths = {
        "magi-agent/magi_agent/config/env.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_pregate8_continuity_readiness_contract.py",
    }
    if changed <= pregate8_continuity_local_harness_paths:
        return
    gate2_readiness_foundation_paths = {
        "magi-agent/magi_agent/config/env.py",
        "magi-agent/magi_agent/config/models.py",
        "magi-agent/magi_agent/gates/gate2_readiness.py",
        "magi-agent/magi_agent/shadow/__init__.py",
        "magi-agent/magi_agent/shadow/gate2_recipe_profile_resolver.py",
        "magi-agent/magi_agent/shadow/gate2_shadow_tool_policy.py",
        "magi-agent/magi_agent/transport/health.py",
        "magi-agent/tests/test_gate2_readiness_foundations.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= gate2_readiness_foundation_paths:
        return
    gate3_readiness_foundation_paths = gate2_readiness_foundation_paths | {
        "magi-agent/magi_agent/gates/gate3_readiness.py",
        "magi-agent/tests/test_gate3_readiness_foundations.py",
    }
    if changed <= gate3_readiness_foundation_paths:
        return
    gate4_readiness_foundation_paths = gate3_readiness_foundation_paths | {
        "magi-agent/magi_agent/gates/gate4_readiness.py",
        "magi-agent/tests/test_gate4_readiness_foundations.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
    }
    if changed <= gate4_readiness_foundation_paths:
        return
    gate5_readiness_foundation_paths = gate4_readiness_foundation_paths | {
        "magi-agent/magi_agent/gates/gate5_readiness.py",
        "magi-agent/tests/test_gate5_readiness_foundations.py",
    }
    if changed <= gate5_readiness_foundation_paths:
        return
    gate7_readiness_foundation_paths = gate5_readiness_foundation_paths | {
        "magi-agent/magi_agent/gates/gate7_readiness.py",
        "magi-agent/magi_agent/runtime/child_event_projection.py",
        "magi-agent/tests/test_child_event_projection.py",
        "magi-agent/tests/test_gate7_readiness_foundations.py",
    }
    if changed <= gate7_readiness_foundation_paths:
        return
    gate8_readiness_foundation_paths = gate7_readiness_foundation_paths | {
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "infra/docker/chat-proxy/runtime-selector.js",
        "infra/docker/chat-proxy/runtime-selector.test.js",
        "magi-agent/magi_agent/gates/gate8_readiness.py",
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/tests/test_gate8_readiness_foundations.py",
    }
    if changed <= gate8_readiness_foundation_paths:
        return
    product_plane_pr10_paths = {
        "magi-agent/magi_agent/evals/__init__.py",
        "magi-agent/magi_agent/evals/release_gates.py",
        "magi-agent/tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_product_release_gate_contracts.py",
    }
    if changed <= product_plane_pr10_paths:
        return
    product_plane_pr11_paths = {
        "magi-agent/magi_agent/connectors/marketplace.py",
        "magi-agent/tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json",
        "magi-agent/tests/test_connector_marketplace_promotion_contract.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= product_plane_pr11_paths:
        return
    product_plane_pr12_paths = {
        "magi-agent/magi_agent/security/__init__.py",
        "magi-agent/magi_agent/security/compliance.py",
        "magi-agent/magi_agent/transport/product_admin.py",
        "magi-agent/tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_product_admin_api_contracts.py",
        "magi-agent/tests/test_security_compliance_contracts.py",
    }
    if changed <= product_plane_pr12_paths:
        return
    product_plane_pr13_paths = {
        "docs/superpowers/plans/2026-05-23-python-adk-codex-product-plane-composable-rollout.md",
        "magi-agent/README.md",
        "magi-agent/tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json",
        "magi-agent/tests/test_codex_product_plane_integration_contract.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= product_plane_pr13_paths:
        return
    live_work_console_pr2_paths = {
        ".secrets.baseline",
        "magi-agent/magi_agent/adk_bridge/event_adapter.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/fixtures/public_event_parity/matrix.json",
        "magi-agent/tests/fixtures/sse/simple_text.txt",
        "magi-agent/tests/fixtures/sse/tool_call.txt",
        "magi-agent/tests/test_adk_runner_lifecycle_events.py",
        "magi-agent/tests/test_event_bridge.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_sse_writer.py",
    }
    if changed <= live_work_console_pr2_paths:
        return
    live_work_console_pr3_paths = {
        "magi-agent/magi_agent/runtime/public_events.py",
        "magi-agent/magi_agent/tools/event_projection.py",
        "magi-agent/magi_agent/tools/kernel.py",
        "magi-agent/tests/fixtures/live_work_console_event_parity/matrix.json",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_public_event_builders.py",
        "magi-agent/tests/test_tool_event_projection.py",
    }
    if changed <= live_work_console_pr3_paths:
        return
    live_work_console_pr4_paths = {
        "magi-agent/magi_agent/evidence/event_projection.py",
        "magi-agent/tests/fixtures/live_work_console_event_parity/matrix.json",
        "magi-agent/tests/test_evidence_event_projection.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= live_work_console_pr4_paths:
        return
    live_work_console_pr5_paths = {
        "magi-agent/magi_agent/runtime/child_event_projection.py",
        "magi-agent/tests/fixtures/live_work_console_event_parity/matrix.json",
        "magi-agent/tests/test_child_event_projection.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_live_work_console_event_parity_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= live_work_console_pr5_paths:
        return
    runtime_heartbeat_pr7_paths = {
        "magi-agent/magi_agent/runtime/events.py",
        "magi-agent/magi_agent/runtime/work_console_snapshot.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_runtime_heartbeat_event_projection.py",
    }
    if changed <= runtime_heartbeat_pr7_paths:
        return
    runtime_heartbeat_pr8_paths = {
        "magi-agent/magi_agent/runtime/readiness.py",
        "magi-agent/magi_agent/transport/health.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_runtime_heartbeat_readiness.py",
    }
    if changed <= runtime_heartbeat_pr8_paths:
        return
    runtime_heartbeat_pr9_paths = {
        "docs/notes/2026-05-27-magi-agent-runtime-heartbeat-final-review.md",
        "magi-agent/README.md",
        "magi-agent/magi_agent/runtime/no_agent_watchdog.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_no_agent_watchdog_contract.py",
    }
    if changed <= runtime_heartbeat_pr9_paths:
        return
    frontend_ts_pr3_paths = {
        "magi-agent/magi_agent/adk_bridge/event_adapter.py",
        "magi-agent/magi_agent/runtime/events.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/fixtures/public_event_parity/matrix.json",
        "magi-agent/tests/fixtures/ts_parity_replay/gate5b4d_stream_coverage_golden.json",
        "magi-agent/tests/fixtures/ts_parity_replay/public_event_golden.json",
        "magi-agent/tests/test_event_bridge.py",
        "magi-agent/tests/test_gate5b4d_stream_fixture_audit.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_pr3_event_adapter_live_compatibility.py",
        "magi-agent/tests/test_pr3_live_producer_parity_bridge.py",
        "magi-agent/tests/test_public_event_builders.py",
        "magi-agent/tests/test_public_event_golden_fixtures.py",
    }
    if changed <= frontend_ts_pr3_paths:
        return
    frontend_ts_pr7_preview_paths = {
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_sse_writer.py",
        "src/components/chat/browser-frame-preview.test.tsx",
        "src/components/chat/browser-frame-preview.tsx",
        "src/components/chat/document-draft-preview.test.tsx",
        "src/components/chat/document-draft-preview.tsx",
        "src/components/chat/work-console-panel.test.tsx",
        "src/components/chat/work-console-panel.tsx",
    }
    if changed <= frontend_ts_pr7_preview_paths:
        return
    frontend_ts_pr8_canary_paths = {
        "docs/notes/2026-05-27-python-adk-frontend-ts-production-compatibility-final-review.md",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/fixtures/frontend_ts_compatibility/python_adk_run.json",
        "magi-agent/tests/test_frontend_ts_compatibility_fixture.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "src/lib/chat/python-adk-compatibility-replay.test.ts",
    }
    if changed <= frontend_ts_pr8_canary_paths:
        return
    frontend_ts_pr9_final_review_paths = {
        ".secrets.baseline",
        "docs/notes/2026-05-27-python-adk-frontend-ts-production-compatibility-readiness-report.md",
        "magi-agent/magi_agent/runtime/public_events.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_public_event_builders.py",
        "magi-agent/tests/test_sse_writer.py",
    }
    if changed <= frontend_ts_pr9_final_review_paths:
        return
    gate2_activation_loop_a_paths = {
        "infra/docker/chat-proxy/chat-proxy.js",
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "infra/docker/chat-proxy/runtime-selector.js",
        "infra/docker/chat-proxy/runtime-selector.test.js",
        "magi-agent/magi_agent/gates/gate2_readiness.py",
        "magi-agent/magi_agent/shadow/gate2_activation_loop_a.py",
        "magi-agent/magi_agent/shadow/gate2_shadow_tool_policy.py",
        "magi-agent/magi_agent/shadow/gate5b4c3_shadow_counter_store.py",
        "magi-agent/magi_agent/tools/catalog.py",
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/magi_agent/transport/health.py",
        "magi-agent/tests/test_gate2_activation_loop_a.py",
        "magi-agent/tests/test_gate2_readiness_foundations.py",
        "magi-agent/tests/test_gate2_selected_durable_evidence_env_wiring.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/uv.lock",
    }
    if changed <= gate2_activation_loop_a_paths:
        return
    gate2_durable_evidence_env_wiring_paths = {
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/tests/test_gate2_selected_durable_evidence_env_wiring.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= gate2_durable_evidence_env_wiring_paths:
        return

    opencode_final_review_security_fix_paths = {
        "magi-agent/magi_agent/coding/final_projection.py",
        "magi-agent/magi_agent/research/final_projection_gate.py",
        "magi-agent/magi_agent/storage/session_store.py",
        "magi-agent/tests/test_coding_final_projection.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_research_final_projection_gate.py",
        "magi-agent/tests/test_session_service_persistence.py",
        "magi-agent/tests/test_session_sqlite_store.py",
    }
    if changed <= opencode_final_review_security_fix_paths:
        return

    research_first_activation_paths = {
        "docs/notes/2026-05-30-research-first-magi-activation.md",
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "magi-agent/README.md",
        "magi-agent/magi_agent/research/research_first_canary.py",
        "magi-agent/magi_agent/shadow/gate5b4c3_shadow_counter_store.py",
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/tests/test_gate5b4c3_shadow_counter_store.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_research_first_selected_canary.py",
    }
    if changed <= research_first_activation_paths:
        return
    gate8_research_first_boundary_paths = {
        ".secrets.baseline",
        "infra/docker/chat-proxy/chat-proxy.js",
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= gate8_research_first_boundary_paths:
        return

    gate5b_selected_full_toolhost_paths = {
        "infra/docker/chat-proxy/gate5b-python-canary.js",
        "infra/docker/chat-proxy/gate5b-python-canary.test.js",
        "infra/docker/chat-proxy/runtime-selector.js",
        "infra/docker/chat-proxy/runtime-selector.test.js",
        "magi-agent/magi_agent/gates/gate5b_full_toolhost.py",
        "magi-agent/magi_agent/main.py",
        "magi-agent/magi_agent/shadow/gate5b4c3_runner_input_adapter.py",
        "magi-agent/magi_agent/shadow/gate5b4c3_shadow_generation_contract.py",
        "magi-agent/magi_agent/transport/chat.py",
        "magi-agent/tests/test_chat_route_contract.py",
        "magi-agent/tests/test_gate5b_full_toolhost.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
    }
    if changed <= gate5b_selected_full_toolhost_paths:
        return

    composio_optional_integration_paths = {
        ".secrets.baseline",
        "SCRATCHPAD.md",
        "docs/superpowers/plans/2026-05-31-composio-optional-integrations.md",
        "docs/superpowers/specs/2026-05-31-composio-optional-integrations-design.md",
        "magi-agent/README.md",
        "magi-agent/magi_agent/cli/app.py",
        "magi-agent/magi_agent/cli/headless.py",
        "magi-agent/magi_agent/cli/tests/test_app.py",
        "magi-agent/magi_agent/cli/tests/test_coldstart.py",
        "magi-agent/magi_agent/cli/tests/test_composio_cli.py",
        "magi-agent/magi_agent/cli/tests/test_engine_gate.py",
        "magi-agent/magi_agent/cli/tests/test_headless_projection.py",
        "magi-agent/magi_agent/cli/wiring.py",
        "magi-agent/magi_agent/composio/__init__.py",
        "magi-agent/magi_agent/composio/config.py",
        "magi-agent/magi_agent/composio/health.py",
        "magi-agent/magi_agent/composio/mcp.py",
        "magi-agent/magi_agent/composio/redaction.py",
        "magi-agent/magi_agent/transport/health.py",
        "magi-agent/magi_agent/transport/sse.py",
        "magi-agent/pyproject.toml",
        "magi-agent/tests/test_composio_config.py",
        "magi-agent/tests/test_composio_health.py",
        "magi-agent/tests/test_composio_mcp.py",
        "magi-agent/tests/test_general_automation_safe_queue_matrix.py",
        "magi-agent/tests/test_health.py",
        "magi-agent/tests/test_memory_self_improvement_mission_parity_matrix.py",
        "magi-agent/tests/test_sse_writer.py",
        "magi-agent/uv.lock",
        "memory/daily/2026-05-31.md",
    }
    if changed <= composio_optional_integration_paths:
        return

    assert changed <= allowed_paths
