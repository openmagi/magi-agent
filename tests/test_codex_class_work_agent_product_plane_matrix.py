from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PYTHON_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_ROOT.parents[2]
MATRIX_PATH = (
    PYTHON_ROOT
    / "tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json"
)
PLAN_PATHS = (
    REPO_ROOT
    / "docs/superpowers/plans/2026-05-23-python-adk-codex-product-plane-composable-rollout.md",
    REPO_ROOT
    / "docs/superpowers/plans/2026-05-23-python-adk-codex-class-work-agent-complete-product-plane.md",
)

REQUIRED_ROW_IDS = (
    "product_plane_matrix_overlap_audit",
    "runtime_ops_events_metrics",
    "durable_job_queue_lifecycle",
    "sandbox_isolation_contracts",
    "oss_durable_store_sqlite_runtime_state",
    "content_addressed_state_checkpoint_replay",
    "tenant_auth_billing_quota_spend_guard",
    "credential_broker_connector_registry",
    "artifact_store_render_delivery_receipts",
    "auto_permission_control_admin_policy",
    "evaluation_release_gate_contracts",
    "connector_marketplace_plugin_runtime_promotion",
    "product_admin_api_contract_stubs",
    "security_compliance_audit_reporting",
    "product_plane_integration_readiness",
    "frontend_admin_product_surface_followup",
    "deployment_db_ops_activation_followup",
)
REQUIRED_FIELDS = {
    "id",
    "capability",
    "requestedBySourcePlan",
    "sourcePlanAnchors",
    "latestMainCoveredRefs",
    "overlapWithQueuedLoops",
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
    "requiredProofs",
    "frontendAdminFollowUp",
    "deploymentDbOpsFollowUp",
    "notes",
}
REQUESTERS = {
    "product_plane_composable_rollout",
    "codex_class_work_agent_product_plane",
    "production_operating_plane",
    "runtime_ops_traces_metrics",
    "durable_job_queue_and_lifecycle",
    "sandbox_and_isolation",
    "durable_state",
    "oss_first_durable_runtime_storage",
    "content_addressed_state_checkpoints_replay",
    "tenant_auth_billing_quota_spend_guard",
    "credential_broker_connector_registry",
    "artifact_store_render_verification_delivery_receipts",
    "auto_permission_control",
    "policy_admin_console",
    "evaluation_release_gates",
    "connector_marketplace_plugin_runtime",
    "backend_admin_api_contract_stubs",
    "security_compliance_plane",
    "compliance_audit_observability",
    "integration_acceptance",
    "product_plane_readiness_report",
    "end_user_product_surface_plane",
    "frontend_admin_product_surface",
    "deployment_database_track",
    "infra_ops_activation",
}
OWNING_LAYERS = {
    "Core substrate",
    "Product/admin policy",
    "Plugin/connector",
    "Frontend/admin",
    "Deployment/DB/ops",
    "Tests/docs only",
}
IMPLEMENTATION_STATUSES = {
    "contract_only",
    "local_fake",
    "default_off_deployed",
    "selected_canary_passed",
    "production_parity",
}
FOLLOWUP_LAYERS = {"Frontend/admin", "Deployment/DB/ops"}
DISALLOWED_UNCLASSIFIED_TERMS = ("un" + "known", "tb" + "d", "later")
OSS_DURABLE_CONFIG_KEYS = (
    "OPENMAGI_DURABLE_STORE",
    "OPENMAGI_DURABLE_SQLITE_PATH",
    "OPENMAGI_ARTIFACT_STORE",
    "OPENMAGI_ARTIFACT_PATH",
    "OPENMAGI_RUNTIME_EXPORT_PATH",
    "OPENMAGI_DURABLE_SQLITE_WAL",
    "OPENMAGI_DURABLE_SQLITE_BUSY_TIMEOUT_MS",
)
EXPECTED_PR_ASSIGNMENTS = {
    "product_plane_matrix_overlap_audit": "PR1",
    "runtime_ops_events_metrics": "PR2",
    "durable_job_queue_lifecycle": "PR3",
    "sandbox_isolation_contracts": "PR4",
    "oss_durable_store_sqlite_runtime_state": "PR5",
    "content_addressed_state_checkpoint_replay": "PR5",
    "tenant_auth_billing_quota_spend_guard": "PR6",
    "credential_broker_connector_registry": "PR7",
    "artifact_store_render_delivery_receipts": "PR8",
    "auto_permission_control_admin_policy": "PR9",
    "evaluation_release_gate_contracts": "PR10",
    "connector_marketplace_plugin_runtime_promotion": "PR11",
    "product_admin_api_contract_stubs": "PR12",
    "security_compliance_audit_reporting": "PR12",
    "product_plane_integration_readiness": "PR13",
    "frontend_admin_product_surface_followup": "PR13",
    "deployment_db_ops_activation_followup": "PR13",
}
EXPECTED_OWNING_LAYERS = {
    "product_plane_matrix_overlap_audit": "Tests/docs only",
    "runtime_ops_events_metrics": "Core substrate",
    "durable_job_queue_lifecycle": "Core substrate",
    "sandbox_isolation_contracts": "Core substrate",
    "oss_durable_store_sqlite_runtime_state": "Core substrate",
    "content_addressed_state_checkpoint_replay": "Core substrate",
    "tenant_auth_billing_quota_spend_guard": "Core substrate",
    "credential_broker_connector_registry": "Plugin/connector",
    "artifact_store_render_delivery_receipts": "Core substrate",
    "auto_permission_control_admin_policy": "Core substrate",
    "evaluation_release_gate_contracts": "Core substrate",
    "connector_marketplace_plugin_runtime_promotion": "Plugin/connector",
    "product_admin_api_contract_stubs": "Product/admin policy",
    "security_compliance_audit_reporting": "Core substrate",
    "product_plane_integration_readiness": "Tests/docs only",
    "frontend_admin_product_surface_followup": "Frontend/admin",
    "deployment_db_ops_activation_followup": "Deployment/DB/ops",
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


def _resolve_ref_path(path: str) -> Path:
    candidates = [PYTHON_ROOT / path, REPO_ROOT / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


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
        assert row["prSliceAssignment"] in {f"PR{number}" for number in range(1, 14)}
        assert isinstance(row["requestedBySourcePlan"], list)
        assert row["requestedBySourcePlan"]
        assert set(row["requestedBySourcePlan"]) <= REQUESTERS
        assert isinstance(row["sourcePlanAnchors"], list)
        assert row["sourcePlanAnchors"]
        assert isinstance(row["latestMainCoveredRefs"], list)
        assert row["latestMainCoveredRefs"]
        assert isinstance(row["missingImplementation"], list)
        assert isinstance(row["overlapWithQueuedLoops"], list)
        assert isinstance(row["dependencies"], list)
        assert isinstance(row["requiredProofs"], list)
        assert row["requiredProofs"]
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


def test_followup_rows_are_explicitly_out_of_core_scope() -> None:
    for row in _load_matrix()["rows"]:
        if row["owningLayer"] == "Frontend/admin":
            assert row["frontendAdminFollowUp"] is True
            assert "separate frontend session" in row["activationGate"]
        elif row["owningLayer"] == "Deployment/DB/ops":
            assert row["deploymentDbOpsFollowUp"] is True
            assert "separate" in row["activationGate"]
        else:
            assert row["owningLayer"] not in FOLLOWUP_LAYERS


def test_existing_refs_exist_and_missing_refs_are_planned_missing() -> None:
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
            assert not _resolve_ref_path(path).exists(), (row["id"], path)


def test_matrix_contains_no_unclassified_or_placeholder_values() -> None:
    for text in _strings(_load_matrix()):
        lowered = text.lower()
        assert not any(marker in lowered for marker in DISALLOWED_UNCLASSIFIED_TERMS), text


def test_oss_durable_store_requirement_is_first_class_and_config_driven() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}
    row = rows["oss_durable_store_sqlite_runtime_state"]
    all_text = " ".join(_strings(row))

    assert row["owningLayer"] == "Core substrate"
    assert row["prSliceAssignment"] == "PR5"
    assert row["implementationStatus"] == "local_fake"
    assert "per-bot PVC-backed SQLite" in all_text
    assert "SQLite multi-writer across pods blocked" in row["activationGate"]
    assert "artifact_blobs_not_stored_in_sqlite" in row["requiredProofs"]
    for key in OSS_DURABLE_CONFIG_KEYS:
        assert key in all_text


def test_runtime_rows_do_not_claim_selected_canary_or_production_parity() -> None:
    for row in _load_matrix()["rows"]:
        assert row["implementationStatus"] in {"contract_only", "local_fake"}


def test_plan_documents_are_present_for_product_plane_queue() -> None:
    for path in PLAN_PATHS:
        assert path.exists(), path


def test_plan_documents_record_oss_durable_store_addendum() -> None:
    combined = "\n".join(path.read_text() for path in PLAN_PATHS)

    assert "OSS-first" in combined
    assert "SQLite must not be shared as a multi-writer database across pods" in combined
    assert "Artifact blobs" in combined or "artifact blobs" in combined
    for key in OSS_DURABLE_CONFIG_KEYS:
        assert key in combined
