"""Dashboard "Credentials" admin routes.

Parity with the hosted Clawy agent-vault C surface:

* ``GET  /v1/admin/credentials``                 — list redacted metadata + vault status
* ``POST /v1/admin/credentials``                 — register: forward secret to the vault
  seam, persist metadata only (status ``active`` if a vault_ref was returned, else
  ``pending``), respond WITHOUT the secret
* ``POST /v1/admin/credentials/{id}/revoke``     — revoke via the seam, mark metadata revoked

All routes require a valid ``x-gateway-token`` (reusing ``transport.tools``'s helper).
Registering the routes is unconditional and inert by default: with the vault seam
OFF the secret is dropped and the credential is recorded as ``pending``.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from magi_agent.credentials_admin import store, vault_local
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
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})

        fields = _validate_body(body)
        if isinstance(fields, JSONResponse):
            return fields
        service, label, auth_scheme, secret = fields

        # Forward the secret to the vault seam, then drop it. The seam never
        # returns, logs, or raises the plaintext.
        try:
            seam_result = vault_local.register_credential(
                service=service,
                label=label,
                auth_scheme=auth_scheme,
                secret=secret,
            )
        except vault_local.VaultSeamError:
            # Secret-free error: the credential could not be stored in the vault.
            # We still record pending metadata so the operator sees the attempt.
            logger.warning("vault seam rejected credential for service=%s", service)
            seam_result = {"disabled": True}
        finally:
            # Scrub the local plaintext reference immediately.
            secret = ""  # noqa: F841

        vault_ref = seam_result.get("vault_ref") if isinstance(seam_result, dict) else None
        status = store.STATUS_ACTIVE if vault_ref else store.STATUS_PENDING

        projection = store.add_credential(
            service=service,
            label=label,
            auth_scheme=auth_scheme,
            status=status,
            vault_ref=vault_ref if isinstance(vault_ref, str) else None,
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


def _validate_body(
    body: object,
) -> tuple[str, str, str, str] | JSONResponse:
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "object_required"})
    service = body.get("service")
    label = body.get("label")
    auth_scheme = body.get("auth_scheme")
    secret = body.get("secret")
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
    return (
        str(service).strip(),
        str(label).strip(),
        str(auth_scheme).strip(),
        str(secret),
    )
