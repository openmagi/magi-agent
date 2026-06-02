from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openmagi_core_agent.artifacts.delivery_receipts import ArtifactDeliveryAuthorityFlags
from openmagi_core_agent.artifacts.file_delivery import FileDeliveryAuthorityFlags
from openmagi_core_agent.artifacts.local_result_store import ResultStoreAuthorityFlags
from openmagi_core_agent.artifacts.output_registry_boundary import OutputArtifactAuthorityFlags
from openmagi_core_agent.artifacts.render_verification import RenderVerificationAuthorityFlags
from openmagi_core_agent.config.models import BuildInfo, RuntimeConfig
from openmagi_core_agent.connectors.credential_lease import CredentialLeaseAuthorityFlags
from openmagi_core_agent.connectors.marketplace import MarketplaceAuthorityFlags
from openmagi_core_agent.connectors.registry import ConnectorAuthorityFlags
from openmagi_core_agent.evals.release_gates import ReleaseGateAuthorityFlags
from openmagi_core_agent.ops.metrics import RuntimeOpsAttachmentFlags
from openmagi_core_agent.ops.job_queue import JobQueueAuthorityFlags
from openmagi_core_agent.permissions.auto_control import AutoPermissionAuthorityFlags
from openmagi_core_agent.runtime.openmagi_runtime import OpenMagiRuntime
from openmagi_core_agent.sandbox.policy import SandboxAuthorityFlags
from openmagi_core_agent.security.compliance import ComplianceAuthorityFlags
from openmagi_core_agent.tenancy.context import TenantRuntimeAuthorityFlags
from openmagi_core_agent.transport.product_admin import build_product_admin_contract_snapshot


PYTHON_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_ROOT.parents[2]
MATRIX_PATH = (
    PYTHON_ROOT
    / "tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json"
)
README_PATH = PYTHON_ROOT / "README.md"
PLAN_PATH = (
    REPO_ROOT
    / "docs/superpowers/plans/2026-05-23-python-adk-codex-product-plane-composable-rollout.md"
)


def _load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text())


def _rows_by_id() -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in _load_matrix()["rows"]}


def _config() -> RuntimeConfig:
    return RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
    )


def _booleans(value: object) -> list[bool]:
    if isinstance(value, bool):
        return [value]
    if isinstance(value, dict):
        result: list[bool] = []
        for nested in value.values():
            result.extend(_booleans(nested))
        return result
    if isinstance(value, list | tuple):
        result = []
        for nested in value:
            result.extend(_booleans(nested))
        return result
    return []


def _authority_payload(flag: object) -> dict[str, object]:
    projection = getattr(flag, "public_projection", None)
    if callable(projection):
        return projection()
    dump = getattr(flag, "model_dump", None)
    if callable(dump):
        return dump(by_alias=True, mode="json")
    return {
        name: getattr(flag, name)
        for name in (
            "adk_callback_attached",
            "tool_host_bypass_allowed",
            "production_policy_write",
            "frontend_admin_attached",
            "user_visible_authority",
            "route_attached",
        )
    }


def test_product_plane_integration_row_is_closed_with_explicit_followups() -> None:
    rows = _rows_by_id()
    readiness = rows["product_plane_integration_readiness"]

    assert readiness["missingImplementation"] == []
    assert readiness["implementationStatus"] == "contract_only"
    assert readiness["defaultOff"] is True
    assert readiness["trafficAttached"] is False
    assert readiness["productionAuthority"] is False
    assert readiness["frontendAdminFollowUp"] is True
    assert readiness["deploymentDbOpsFollowUp"] is True
    assert {
        "core_primitives_are_generic",
        "product_policies_external_to_core",
        "live_attachment_flags_false",
        "frontend_admin_db_k8s_secrets_deploy_activation_followups_recorded",
    } <= set(readiness["requiredProofs"])
    refs = {ref["path"] for ref in readiness["latestMainCoveredRefs"]}
    assert "tests/test_codex_product_plane_integration_contract.py" in refs
    assert "README.md" in refs

    frontend = rows["frontend_admin_product_surface_followup"]
    deployment = rows["deployment_db_ops_activation_followup"]
    assert frontend["owningLayer"] == "Frontend/admin"
    assert deployment["owningLayer"] == "Deployment/DB/ops"
    assert frontend["missingImplementation"]
    assert deployment["missingImplementation"]
    assert frontend["frontendAdminFollowUp"] is True
    assert deployment["deploymentDbOpsFollowUp"] is True
    assert "separate frontend session" in frontend["activationGate"]
    assert "separate" in deployment["activationGate"]
    assert "no implementation-session activation" in deployment["activationGate"]


def test_representative_product_plane_authority_flags_remain_false() -> None:
    flags = (
        RuntimeOpsAttachmentFlags(),
        JobQueueAuthorityFlags(),
        SandboxAuthorityFlags(),
        TenantRuntimeAuthorityFlags(),
        ConnectorAuthorityFlags(),
        CredentialLeaseAuthorityFlags(),
        MarketplaceAuthorityFlags(),
        ArtifactDeliveryAuthorityFlags(),
        FileDeliveryAuthorityFlags(),
        RenderVerificationAuthorityFlags(),
        OutputArtifactAuthorityFlags(),
        ResultStoreAuthorityFlags(),
        ReleaseGateAuthorityFlags(),
        ComplianceAuthorityFlags(),
        AutoPermissionAuthorityFlags(),
    )

    for flag in flags:
        payload = _authority_payload(flag)
        booleans = _booleans(payload)
        assert booleans, type(flag).__name__
        assert set(booleans) == {False}, (type(flag).__name__, payload)


def test_product_admin_contract_snapshot_is_readiness_only_not_activation() -> None:
    snapshot = build_product_admin_contract_snapshot(OpenMagiRuntime(config=_config()))
    encoded = json.dumps(snapshot, sort_keys=True).lower()

    assert snapshot["defaultOff"] is True
    assert snapshot["noLiveData"] is True
    assert set(snapshot["authorityFlags"].values()) == {False}
    assert set(snapshot["sections"]) == {
        "ops",
        "policy",
        "artifacts",
        "release_gates",
        "connector_registry",
        "security_compliance",
    }
    assert "raw prompt" not in encoded
    assert "raw output" not in encoded
    assert "hidden reasoning" not in encoded
    assert "authorization" not in encoded
    assert "cookie" not in encoded
    assert "/users/" not in encoded


def test_readiness_docs_keep_activation_separate_from_core_contracts() -> None:
    combined = README_PATH.read_text() + "\n" + PLAN_PATH.read_text()

    required_phrases = (
        "Product-Plane Integration Readiness",
        "default-off",
        "No frontend routes",
        "No DB migrations",
        "No Kubernetes or deployment changes",
        "No live connector credentials",
        "Python user-visible output remains disabled",
        "separate frontend/admin session",
        "separate execution/deployment approval",
        "per-bot PVC-backed SQLite",
    )
    for phrase in required_phrases:
        assert phrase in combined


def test_product_plane_pr13_does_not_touch_frontend_or_deployment_paths() -> None:
    rows = _rows_by_id()
    all_pr13_paths = {
        ref["path"]
        for row in rows.values()
        if row["prSliceAssignment"] == "PR13"
        for ref in row["latestMainCoveredRefs"] + row["missingImplementation"]
    }

    disallowed_prefixes = (
        "src/",
        "apps/",
        "supabase/",
        "infra/k8s/",
        "infra/docker/chat-proxy/",
        "infra/docker/api-proxy/",
        "infra/docker/provisioning-worker/",
    )
    for path in all_pr13_paths:
        row = next(
            row
            for row in rows.values()
            if any(
                ref["path"] == path
                for ref in row["latestMainCoveredRefs"] + row["missingImplementation"]
            )
        )
        if path.startswith(disallowed_prefixes) or path.startswith(
            "infra/planned_product_plane_activation/"
        ):
            assert row["owningLayer"] in {"Frontend/admin", "Deployment/DB/ops"}
            continue
        assert path.startswith(("README.md", "tests/", "docs/superpowers/plans/"))
