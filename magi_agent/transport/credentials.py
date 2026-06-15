"""Dashboard "Credentials" admin routes.

Parity with the hosted Clawy agent-vault C surface:

* ``GET  /v1/admin/credentials``                 — list redacted metadata + vault status
* ``POST /v1/admin/credentials``                 — register: forward secret to the vault
  seam only when the vault is available, persist metadata only, respond WITHOUT
  the secret
* ``POST /v1/admin/credentials/{id}/revoke``     — revoke via the seam, mark metadata revoked

All routes require a valid ``x-gateway-token`` (reusing ``transport.tools``'s helper).
Registering the routes is unconditional and inert by default: with the vault seam
OFF registration returns ``503`` and persists nothing because no secret is
retained for later forwarding.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from magi_agent.credentials_admin import approvals_store, store, vault_local
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.storage.durable_store import DurableStoreSafetyError
from magi_agent.transport.tools import _unauthorized_response

logger = logging.getLogger(__name__)

_MAX_FIELD_LEN = 256


def register_credentials_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    @app.get("/v1/admin/credentials")
    @app.get("/api/credentials")
    async def list_credentials(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        data = store.load_credentials()
        return JSONResponse(
            content={
                "credentials": data["credentials"],
                "vault_status": vault_local.vault_status(),
            }
        )

    @app.post("/v1/admin/credentials")
    @app.post("/api/credentials")
    async def create_credential(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        current_vault_status = vault_local.vault_status()
        if not _vault_ready(current_vault_status):
            return _vault_unavailable_response(current_vault_status)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})

        fields = _validate_body(body)
        if isinstance(fields, JSONResponse):
            return fields
        service, label, auth_scheme, secret, requires_approval, host = fields

        # Forward the secret to the vault seam, then drop it. The seam never
        # returns, logs, or raises the plaintext.
        try:
            seam_result = vault_local.register_credential(
                service=service,
                label=label,
                auth_scheme=auth_scheme,
                secret=secret,
                requires_approval=requires_approval,
            )
        except vault_local.VaultSeamError:
            # Secret-free error: the credential could not be stored in the vault.
            logger.warning("vault seam rejected credential for service=%s", service)
            seam_result = {"disabled": True}
        finally:
            # Scrub the local plaintext reference immediately.
            secret = ""  # noqa: F841

        if isinstance(seam_result, dict) and seam_result.get("disabled"):
            return _vault_unavailable_response(current_vault_status)

        vault_ref = seam_result.get("vault_ref") if isinstance(seam_result, dict) else None
        if not isinstance(vault_ref, str) or not vault_ref:
            return _vault_unavailable_response(current_vault_status)
        status = store.STATUS_ACTIVE

        projection = store.add_credential(
            service=service,
            label=label,
            auth_scheme=auth_scheme,
            status=status,
            vault_ref=vault_ref,
            requires_approval=requires_approval,
            host=host,
        )

        # Exercise (do not bypass) the durable store guard with a digest-only
        # index record. This raises if the metadata were ever secret-shaped.
        try:
            vault_local.build_durable_metadata_record(
                credential_id=projection["id"],
                service=service,
                label=label,
                auth_scheme=auth_scheme,
                status=status,
                vault_ref=projection["vault_ref"],
            )
        except DurableStoreSafetyError:
            logger.warning("durable metadata record rejected for service=%s", service)

        return JSONResponse(content={"credential": projection})

    @app.post("/v1/admin/credentials/{credential_id}/revoke")
    @app.post("/api/credentials/{credential_id}/revoke")
    async def revoke_credential(credential_id: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        existing = next(
            (
                c
                for c in store.load_credentials()["credentials"]
                if c.get("id") == credential_id
            ),
            None,
        )
        if existing is None:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "message": "credential not found"},
            )
        if existing.get("vault_ref"):
            vault_local.revoke_credential(vault_ref=str(existing["vault_ref"]))
        updated = store.set_status(credential_id, store.STATUS_REVOKED)
        if updated is None:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "message": "credential not found"},
            )
        return JSONResponse(content={"credential": updated})

    @app.get("/v1/admin/credentials/approvals")
    @app.get("/api/credentials/approvals")
    async def list_approvals(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        status = request.query_params.get("status")
        approvals = approvals_store.list_approvals(status=status or None)
        return JSONResponse(content={"approvals": approvals})

    @app.post("/v1/admin/credentials/approvals/{approval_id}")
    @app.post("/api/credentials/approvals/{approval_id}")
    async def decide_approval(approval_id: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "object_required"})
        decision = body.get("decision")
        if decision not in approvals_store.DECISION_STATUSES:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_decision", "field": "decision"},
            )

        # Record the operator's decision locally first — the local store is the
        # source of truth for the dashboard regardless of the vault seam state.
        updated = approvals_store.decide_approval(approval_id, decision)
        if updated is None:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "message": "approval not found"},
            )

        # Forward the decision to the vault seam (no-op when default-OFF). Never
        # surfaces a secret — the approval record is metadata only.
        vault_local.resolve_approval(approval_id=approval_id, decision=decision)

        return JSONResponse(content={"approval": updated})


def _validate_body(
    body: object,
) -> tuple[str, str, str, str, bool, str | None] | JSONResponse:
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "object_required"})
    service = body.get("service")
    label = body.get("label")
    auth_scheme = body.get("auth_scheme")
    secret = body.get("secret")
    requires_approval_raw = body.get("requires_approval", False)
    if not isinstance(requires_approval_raw, bool):
        return JSONResponse(
            status_code=400,
            content={"error": "field_invalid", "field": "requires_approval"},
        )
    for name, value in (
        ("service", service),
        ("label", label),
        ("auth_scheme", auth_scheme),
        ("secret", secret),
    ):
        if not isinstance(value, str) or not value.strip():
            return JSONResponse(
                status_code=400,
                content={"error": "field_required", "field": name},
            )
        if len(value) > _MAX_FIELD_LEN and name != "secret":
            return JSONResponse(
                status_code=400,
                content={"error": "field_too_long", "field": name},
            )
    # Optional, non-secret target host for the local egress proxy. Validated like
    # the other string fields but allowed to be absent (→ None, resolved from the
    # service map at proxy time).
    host_raw = body.get("host")
    host: str | None = None
    if host_raw is not None:
        if not isinstance(host_raw, str) or not host_raw.strip():
            return JSONResponse(
                status_code=400,
                content={"error": "field_invalid", "field": "host"},
            )
        if len(host_raw) > _MAX_FIELD_LEN:
            return JSONResponse(
                status_code=400,
                content={"error": "field_too_long", "field": "host"},
            )
        host = host_raw.strip()
    return (
        str(service).strip(),
        str(label).strip(),
        str(auth_scheme).strip(),
        str(secret),
        bool(requires_approval_raw),
        host,
    )


def _vault_ready(status: dict[str, bool]) -> bool:
    return bool(status.get("present") and status.get("healthy"))


def _vault_unavailable_response(status: dict[str, bool]) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "vault_unavailable",
            "message": "credential registration requires an available vault",
            "vault_status": status,
        },
    )
