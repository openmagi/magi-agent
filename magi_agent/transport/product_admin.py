from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from secrets import compare_digest
from typing import Any, TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from magi_agent.ops import default_runtime_ops_health_metadata
from magi_agent.ops.safety import require_digest, require_safe_ref

if TYPE_CHECKING:
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


_ZERO_DIGEST = "sha256:" + "0" * 64
_SECTION_ORDER = (
    "ops",
    "policy",
    "artifacts",
    "release_gates",
    "connector_registry",
    "security_compliance",
)


def register_product_admin_contract_routes(
    app: FastAPI,
    runtime: OpenMagiRuntime,
) -> None:
    @app.get("/v1/admin/product/contracts")
    async def product_admin_contracts(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        return JSONResponse(content=build_product_admin_contract_snapshot(runtime))

    @app.get("/v1/admin/product/contracts/{section}")
    async def product_admin_contract_section(section: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        snapshot = build_product_admin_contract_snapshot(runtime)
        sections = snapshot["sections"]
        if section not in sections:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "not_found",
                    "message": "product admin contract section not found",
                },
            )
        return JSONResponse(
            content={
                "schemaVersion": "openmagi.product_admin.contract_section.v1",
                "section": section,
                "contract": sections[section],
                "contractDigest": _digest_payload(
                    {
                        "schemaVersion": "openmagi.product_admin.contract_section.v1",
                        "section": section,
                        "contract": sections[section],
                    }
                ),
                "defaultOff": True,
                "liveDataAttached": False,
                "mutationRoutesAttached": False,
                "frontendRouteAttached": False,
            }
        )


def build_product_admin_contract_snapshot(runtime: OpenMagiRuntime) -> dict[str, object]:
    bot_id = require_safe_ref(runtime.config.bot_id, field_name="botId")
    policy_digest = _digest_payload(
        {
            "schemaVersion": "openmagi.product_admin.policy_contract.v1",
            "botId": bot_id,
            "authority": "default_off",
        }
    )
    sections = {
        section: _section_contract(section, policy_digest=policy_digest)
        for section in _SECTION_ORDER
    }
    snapshot: dict[str, object] = {
        "schemaVersion": "openmagi.product_admin.contract_snapshot.v1",
        "runtimeRef": f"bot:{bot_id}",
        "snapshotDigest": _digest_payload(
            {
                "schemaVersion": "openmagi.product_admin.contract_snapshot_digest.v1",
                "botId": bot_id,
                "sections": sections,
                "defaultOff": True,
            }
        ),
        "policySnapshotDigest": policy_digest,
        "sections": sections,
        "defaultOff": True,
        "noLiveData": True,
        "liveDataAttached": False,
        "mutationRoutesAttached": False,
        "frontendRouteAttached": False,
        "authorityFlags": _authority_flags(),
    }
    _assert_digest_contract(snapshot)
    return snapshot


def _section_contract(section: str, *, policy_digest: str) -> dict[str, object]:
    safe_policy_digest = require_digest(policy_digest)
    if section == "ops":
        health = default_runtime_ops_health_metadata()
        return {
            "schemaVersion": "openmagi.product_admin.ops_contract.v1",
            "section": section,
            "sectionDigest": _section_digest(section, safe_policy_digest),
            "policySnapshotDigest": safe_policy_digest,
            "healthMetadataDigest": _digest_payload(health),
            "routeAttached": False,
            "liveDataAttached": False,
            "productionStorageAttached": False,
            "mutationAllowed": False,
        }
    if section == "policy":
        return {
            "schemaVersion": "openmagi.product_admin.policy_contract.v1",
            "section": section,
            "sectionDigest": _section_digest(section, safe_policy_digest),
            "policySnapshotDigest": safe_policy_digest,
            "policyDocumentDigest": safe_policy_digest,
            "policyMutationAllowed": False,
            "mutationAllowed": False,
            "liveDataAttached": False,
        }
    if section == "artifacts":
        return {
            "schemaVersion": "openmagi.product_admin.artifact_contract.v1",
            "section": section,
            "sectionDigest": _section_digest(section, safe_policy_digest),
            "policySnapshotDigest": safe_policy_digest,
            "artifactIndexDigest": _section_digest("artifact_index", safe_policy_digest),
            "renderVerificationDigest": _section_digest("render_verification", safe_policy_digest),
            "deliveryReceiptIndexDigest": _section_digest("delivery_receipts", safe_policy_digest),
            "deliveryClaimAllowed": False,
            "liveDataAttached": False,
            "mutationAllowed": False,
        }
    if section == "release_gates":
        return {
            "schemaVersion": "openmagi.product_admin.release_gate_contract.v1",
            "section": section,
            "sectionDigest": _section_digest(section, safe_policy_digest),
            "policySnapshotDigest": safe_policy_digest,
            "promotionGateDigest": _section_digest("promotion_gate", safe_policy_digest),
            "rollbackRefDigest": _section_digest("rollback_ref", safe_policy_digest),
            "promotionAllowed": False,
            "liveDataAttached": False,
            "mutationAllowed": False,
        }
    if section == "connector_registry":
        return {
            "schemaVersion": "openmagi.product_admin.connector_registry_contract.v1",
            "section": section,
            "sectionDigest": _section_digest(section, safe_policy_digest),
            "policySnapshotDigest": safe_policy_digest,
            "connectorRegistryDigest": _section_digest("connector_registry", safe_policy_digest),
            "marketplacePromotionDigest": _section_digest(
                "marketplace_promotion",
                safe_policy_digest,
            ),
            "leaseMetadataDigest": _section_digest(
                "lease_metadata",
                safe_policy_digest,
            ),
            "credentialReadAllowed": False,
            "liveSecretReadAllowed": False,
            "pluginExecutionAllowed": False,
            "liveDataAttached": False,
            "mutationAllowed": False,
        }
    if section == "security_compliance":
        return {
            "schemaVersion": "openmagi.product_admin.security_compliance_contract.v1",
            "section": section,
            "sectionDigest": _section_digest(section, safe_policy_digest),
            "policySnapshotDigest": safe_policy_digest,
            "policyKernelDecisionDigest": _section_digest(
                "policy_kernel_decision",
                safe_policy_digest,
            ),
            "rollbackFallbackDiagnosticDigest": _section_digest(
                "rollback_fallback_diagnostic",
                safe_policy_digest,
            ),
            "complianceReportDigest": _section_digest("compliance_report", safe_policy_digest),
            "publicRouteAttached": False,
            "liveDataAttached": False,
            "mutationAllowed": False,
        }
    raise ValueError(f"unsupported product admin section: {section}")


def _section_digest(section: str, policy_digest: str) -> str:
    return _digest_payload(
        {
            "schemaVersion": "openmagi.product_admin.section_digest.v1",
            "section": require_safe_ref(section, field_name="section"),
            "policySnapshotDigest": require_digest(policy_digest),
        }
    )


def _authority_flags() -> dict[str, bool]:
    return {
        "routeAttached": False,
        "frontendRouteAttached": False,
        "productionAuthority": False,
        "liveDataAttached": False,
        "mutationAllowed": False,
        "credentialReadAllowed": False,
        "liveSecretReadAllowed": False,
        "pluginExecutionAllowed": False,
        "modelCalled": False,
        "toolHostDispatched": False,
        "networkCallAllowed": False,
        "userVisibleOutputAllowed": False,
    }


def _assert_digest_contract(value: object) -> None:
    encoded = json.dumps(value, sort_keys=True).lower()
    forbidden_fragments = (
        "raw prompt",
        "raw output",
        "hidden reasoning",
        "authorization",
        "bearer ",
        "cookie",
        "session key",
        "credential value",
        "connector token",
        "/users/",
        "/private/",
        ".env",
        "sk-",
        "ghp_",
    )
    if any(fragment in encoded for fragment in forbidden_fragments):
        raise ValueError("product admin contract projection contains forbidden material")


def _digest_payload(payload: Mapping[str, object]) -> str:
    if not payload:
        return _ZERO_DIGEST
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _unauthorized_response(
    request: Request,
    runtime: OpenMagiRuntime,
) -> JSONResponse | None:
    token = request.headers.get("x-gateway-token")
    if token is not None and compare_digest(token, runtime.config.gateway_token):
        return None
    return JSONResponse(status_code=401, content={"error": "unauthorized"})
