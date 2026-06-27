"""Composio platform-broker server routes.

The other half of the ``platform`` credential mode (client side merged in the
``composio/`` + dashboard routes): these are the endpoints a self-hosted runtime
calls so its users get Composio **without their own Composio key**. We run a
deployment of this with the platform master Composio key in its env; the caller
authenticates with a free platform token and we mint Composio sessions / proxy
OAuth with the master key, scoped to the caller's entity.

Approach A (tool calls go runtime→Composio directly): ``POST .../session`` mints
a Composio MCP session for the entity and returns its URL + headers; the runtime
connects its toolset straight to Composio. OAuth (connect/status/list/delete)
and the catalog still go through here because they need the master key.

Everything network-bearing is injected (``master_client_provider``,
``token_validator``) so the routes are unit-testable without Composio or a live
token service.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from magi_agent.composio import connections as composio_connections

# token_validator(token) -> caller identity (truthy) or None to reject.
TokenValidator = Callable[[str | None], str | None]
# master_client_provider() -> a Composio client built from the master key, or None.
MasterClientProvider = Callable[[], Any | None]

_ENTITY_HEADER = "x-magi-composio-entity"
_MASTER_KEY_ENVS = ("MAGI_COMPOSIO_MASTER_KEY", "COMPOSIO_API_KEY")
_BROKER_TOKENS_ENV = "MAGI_COMPOSIO_BROKER_TOKENS"


def register_composio_broker_routes(
    app: FastAPI,
    *,
    master_client_provider: MasterClientProvider | None = None,
    token_validator: TokenValidator | None = None,
) -> None:
    provide_master = master_client_provider or _default_master_client_provider
    validate_token = token_validator or _default_token_validator

    def _auth(authorization: str | None) -> str | None:
        token = _bearer(authorization)
        return validate_token(token)

    def _entity(request: Request, fallback: str | None) -> str:
        header = request.headers.get(_ENTITY_HEADER)
        value = (header or fallback or "").strip()
        return value or "default"

    @app.post("/v1/integrations/composio/session")
    async def broker_session(
        request: Request, authorization: str | None = Header(default=None)
    ) -> JSONResponse:
        if _auth(authorization) is None:
            return _unauthorized()
        body = await _json(request)
        if isinstance(body, JSONResponse):
            return body
        client = provide_master()
        if client is None:
            return _not_configured()
        entity = _entity(request, body.get("entity_id"))
        toolkits = _toolkits(body.get("toolkits"))
        try:
            result = composio_connections.mint_session(
                client, entity_id=entity, toolkits=toolkits
            )
        except Exception:
            return _upstream_error("session")
        return JSONResponse(content=result)

    @app.get("/v1/integrations/composio/catalog")
    async def broker_catalog(
        request: Request, authorization: str | None = Header(default=None)
    ) -> JSONResponse:
        if _auth(authorization) is None:
            return _unauthorized()
        client = provide_master()
        if client is None:
            return _not_configured()
        category = request.query_params.get("category")
        cursor = request.query_params.get("cursor")
        managed_only = request.query_params.get("managed_only", "1") not in ("0", "false")
        try:
            page = composio_connections.list_catalog(
                client, category=category, cursor=cursor, managed_only=managed_only
            )
        except Exception:
            return _upstream_error("catalog")
        return JSONResponse(content=page)

    @app.post("/v1/integrations/composio/connect")
    async def broker_connect(
        request: Request, authorization: str | None = Header(default=None)
    ) -> JSONResponse:
        if _auth(authorization) is None:
            return _unauthorized()
        body = await _json(request)
        if isinstance(body, JSONResponse):
            return body
        toolkit = body.get("toolkit")
        if not isinstance(toolkit, str) or not toolkit.strip():
            return JSONResponse(status_code=400, content={"error": "toolkit_required"})
        client = provide_master()
        if client is None:
            return _not_configured()
        try:
            result = composio_connections.initiate_connection(
                client, entity_id=_entity(request, body.get("entity_id")),
                toolkit=toolkit.strip(),
            )
        except Exception:
            return _upstream_error("connect")
        return JSONResponse(content=result)

    @app.get("/v1/integrations/composio/connect/{connection_id}/status")
    async def broker_status(
        connection_id: str, request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        if _auth(authorization) is None:
            return _unauthorized()
        client = provide_master()
        if client is None:
            return _not_configured()
        try:
            result = composio_connections.connection_status(
                client, connection_id=connection_id
            )
        except Exception:
            return _upstream_error("status")
        return JSONResponse(content=result)

    @app.get("/v1/integrations/composio/connections")
    async def broker_connections(
        request: Request, authorization: str | None = Header(default=None)
    ) -> JSONResponse:
        if _auth(authorization) is None:
            return _unauthorized()
        client = provide_master()
        if client is None:
            return _not_configured()
        try:
            items = composio_connections.list_connections(
                client, entity_id=_entity(request, request.query_params.get("entity_id"))
            )
        except Exception:
            return _upstream_error("connections")
        return JSONResponse(content={"connections": items})

    @app.delete("/v1/integrations/composio/connection/{connection_id}")
    async def broker_disconnect(
        connection_id: str, request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        if _auth(authorization) is None:
            return _unauthorized()
        client = provide_master()
        if client is None:
            return _not_configured()
        try:
            composio_connections.delete_connection(client, connection_id=connection_id)
        except Exception:
            return _upstream_error("disconnect")
        return JSONResponse(content={"disconnected": connection_id})


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


def _toolkits(raw: object) -> tuple[str, ...]:
    if isinstance(raw, (list, tuple)):
        return tuple(str(t).strip() for t in raw if str(t).strip())
    if isinstance(raw, str) and raw.strip():
        return tuple(t.strip() for t in raw.split(",") if t.strip())
    return ()


def _default_token_validator(token: str | None) -> str | None:
    """Fail-closed allowlist from ``MAGI_COMPOSIO_BROKER_TOKENS`` (comma list).

    Hosted deployments front this with the real platform-token auth gateway and
    inject a validated token; the env allowlist is the OSS-self-host default.
    No tokens configured → every request is rejected.
    """
    if not token:
        return None
    allow = {t.strip() for t in (os.environ.get(_BROKER_TOKENS_ENV) or "").split(",") if t.strip()}
    return token if token in allow else None


def _default_master_client_provider() -> Any | None:
    for env in _MASTER_KEY_ENVS:
        key = (os.environ.get(env) or "").strip()
        if key:
            try:
                return composio_connections.build_connections_client(key)
            except Exception:
                return None
    return None


def _unauthorized() -> JSONResponse:
    return JSONResponse(status_code=401, content={"error": "invalid_or_missing_token"})


def _not_configured() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": "broker_master_key_unconfigured"},
    )


def _upstream_error(op: str) -> JSONResponse:
    return JSONResponse(status_code=502, content={"error": "composio_upstream_error", "op": op})


async def _json(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "invalid_body"})
    return body
