"""Dashboard "Integrations" admin routes.

Self-serve connection management for the OSS dashboard Integrations tab:

Composio (BYO key)
* ``PUT/DELETE /v1/admin/integrations/composio/key``  — store/clear the API key
* ``GET  /v1/admin/integrations/composio/catalog``    — searchable toolkit catalog
* ``POST /v1/admin/integrations/composio/connect``    — start an OAuth connection
* ``GET  /v1/admin/integrations/composio/connect/{id}/status`` — poll status
* ``GET  /v1/admin/integrations/composio/connections``— list connected accounts
* ``DELETE /v1/admin/integrations/composio/connection/{id}`` — disconnect

Telegram (advanced / bot-token)
* ``PUT/DELETE /v1/admin/integrations/telegram/token`` — validate (getMe) + store

Aggregate
* ``GET /v1/admin/integrations`` — non-secret status of every section

Secrets always go through the local vault seam; this module never returns,
logs, or persists plaintext. All routes require a valid ``x-gateway-token``.
Network-bearing dependencies (Composio client, Telegram HTTP) are injected so
the routes are testable without network.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from magi_agent.channels import telegram_easy
from magi_agent.channels.telegram_easy import (
    EasySessionStore,
    TelegramUserAuthPort,
)
from magi_agent.channels.channel_validate import (
    ChannelFetchJson,
    validate_discord_bot_token,
    validate_slack_bot_token,
)
from magi_agent.channels.channel_validate import (
    InvalidBotToken as ChannelInvalidBotToken,
)
from magi_agent.channels.telegram_validate import InvalidBotToken, validate_bot_token
from magi_agent.composio import connections as composio_connections
from magi_agent.composio.config import resolve_composio_config
from magi_agent.credentials_admin import store, vault_local
from magi_agent.credentials_admin.local_vault import LocalVault
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.tools import _unauthorized_response

logger = logging.getLogger(__name__)

_MAX_FIELD_LEN = 4096

ComposioClientProvider = Callable[[], Any | None]
TelegramFetchJson = Callable[[str], dict[str, Any]]
TelegramAuthPortProvider = Callable[[], TelegramUserAuthPort | None]


class _PersistError(Exception):
    """Carries an error JSONResponse out of a persist callback."""

    def __init__(self, response: JSONResponse) -> None:
        self.response = response


def register_integrations_routes(
    app: FastAPI,
    runtime: OpenMagiRuntime,
    *,
    composio_client_provider: ComposioClientProvider | None = None,
    telegram_fetch_json: TelegramFetchJson | None = None,
    telegram_auth_port_provider: TelegramAuthPortProvider | None = None,
    easy_session_store: EasySessionStore | None = None,
    now_fn: Callable[[], float] | None = None,
    discord_fetch_json: ChannelFetchJson | None = None,
    slack_fetch_json: ChannelFetchJson | None = None,
) -> None:
    provide_composio = composio_client_provider or _default_composio_client_provider
    fetch_json = telegram_fetch_json or _default_telegram_fetch_json
    discord_fetch = discord_fetch_json or _default_discord_fetch_json
    slack_fetch = slack_fetch_json or _default_slack_fetch_json
    provide_auth_port = telegram_auth_port_provider or _default_telegram_auth_port
    easy_store = easy_session_store or EasySessionStore()
    clock = now_fn or time.time

    def persist_token(token: str) -> dict[str, Any]:
        result = _persist_telegram_token(token, fetch_json)
        if isinstance(result, JSONResponse):
            raise _PersistError(result)
        return result

    # -- aggregate ----------------------------------------------------------
    @app.get("/v1/admin/integrations")
    @app.get("/api/integrations")
    async def get_integrations(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        return JSONResponse(
            content={
                "composio": _composio_status(),
                "telegram": _telegram_status(),
                "discord": _discord_status(),
                "slack": _slack_status(),
                "vault_status": vault_local.vault_status(),
            }
        )

    # -- composio: api key --------------------------------------------------
    @app.put("/v1/admin/integrations/composio/key")
    @app.put("/api/integrations/composio/key")
    async def put_composio_key(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        value = await _read_secret_field(request, "api_key")
        if isinstance(value, JSONResponse):
            return value
        result = _store_secret(
            service="composio", label="Composio API key", auth_scheme="api_key", secret=value
        )
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(content={"composio": _composio_status()})

    @app.delete("/v1/admin/integrations/composio/key")
    @app.delete("/api/integrations/composio/key")
    async def delete_composio_key(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        _revoke_service_credentials(service="composio", auth_scheme="api_key")
        return JSONResponse(content={"composio": _composio_status()})

    # -- composio: catalog + connections ------------------------------------
    @app.get("/v1/admin/integrations/composio/catalog")
    @app.get("/api/integrations/composio/catalog")
    async def composio_catalog(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        client = provide_composio()
        if client is None:
            return _composio_not_configured()
        category = request.query_params.get("category")
        cursor = request.query_params.get("cursor")
        managed_only = request.query_params.get("managed_only", "1") not in ("0", "false")
        try:
            page = composio_connections.list_catalog(
                client, category=category, cursor=cursor, managed_only=managed_only
            )
        except Exception:
            return _composio_upstream_error("catalog")
        return JSONResponse(content=page)

    @app.post("/v1/admin/integrations/composio/connect")
    @app.post("/api/integrations/composio/connect")
    async def composio_connect(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        body = await _read_json(request)
        if isinstance(body, JSONResponse):
            return body
        toolkit = body.get("toolkit")
        if not isinstance(toolkit, str) or not toolkit.strip():
            return JSONResponse(status_code=400, content={"error": "toolkit_required"})
        client = provide_composio()
        if client is None:
            return _composio_not_configured()
        try:
            result = composio_connections.initiate_connection(
                client, entity_id=_composio_entity_id(), toolkit=toolkit.strip()
            )
        except Exception:
            return _composio_upstream_error("connect")
        return JSONResponse(content=result)

    @app.get("/v1/admin/integrations/composio/connect/{connection_id}/status")
    @app.get("/api/integrations/composio/connect/{connection_id}/status")
    async def composio_connect_status(connection_id: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        client = provide_composio()
        if client is None:
            return _composio_not_configured()
        try:
            result = composio_connections.connection_status(
                client, connection_id=connection_id
            )
        except Exception:
            return _composio_upstream_error("status")
        return JSONResponse(content=result)

    @app.get("/v1/admin/integrations/composio/connections")
    @app.get("/api/integrations/composio/connections")
    async def composio_connections_list(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        client = provide_composio()
        if client is None:
            return _composio_not_configured()
        try:
            items = composio_connections.list_connections(
                client, entity_id=_composio_entity_id()
            )
        except Exception:
            return _composio_upstream_error("connections")
        return JSONResponse(content={"connections": items})

    @app.delete("/v1/admin/integrations/composio/connection/{connection_id}")
    @app.delete("/api/integrations/composio/connection/{connection_id}")
    async def composio_disconnect(connection_id: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        client = provide_composio()
        if client is None:
            return _composio_not_configured()
        try:
            composio_connections.delete_connection(client, connection_id=connection_id)
        except Exception:
            return _composio_upstream_error("disconnect")
        return JSONResponse(content={"disconnected": connection_id})

    # -- telegram: bot token (advanced) -------------------------------------
    @app.put("/v1/admin/integrations/telegram/token")
    @app.put("/api/integrations/telegram/token")
    async def put_telegram_token(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        value = await _read_secret_field(request, "token")
        if isinstance(value, JSONResponse):
            return value
        result = _persist_telegram_token(value, fetch_json)
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(content={"telegram": result})

    # -- telegram: easy setup (phone → BotFather, gated) --------------------
    @app.post("/v1/admin/integrations/telegram/easy/send-code")
    @app.post("/api/integrations/telegram/easy/send-code")
    async def telegram_easy_send_code(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        if not is_telegram_easy_enabled():
            return _easy_disabled()
        body = await _read_json(request)
        if isinstance(body, JSONResponse):
            return body
        phone = body.get("phone")
        if not isinstance(phone, str) or not phone.strip():
            return JSONResponse(status_code=400, content={"error": "phone_required"})
        port = provide_auth_port()
        if port is None:
            return _easy_disabled()
        try:
            session_id = await asyncio.to_thread(
                telegram_easy.begin_login, easy_store, port, phone.strip(), now=clock()
            )
        except Exception:
            return JSONResponse(status_code=502, content={"error": "telegram_unreachable"})
        return JSONResponse(content={"session_id": session_id})

    @app.post("/v1/admin/integrations/telegram/easy/verify-code")
    @app.post("/api/integrations/telegram/easy/verify-code")
    async def telegram_easy_verify_code(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        if not is_telegram_easy_enabled():
            return _easy_disabled()
        body = await _read_json(request)
        if isinstance(body, JSONResponse):
            return body
        session_id = body.get("session_id")
        code = body.get("code")
        if not isinstance(session_id, str) or not isinstance(code, str) or not code.strip():
            return JSONResponse(status_code=400, content={"error": "session_id_and_code_required"})
        port = provide_auth_port()
        if port is None:
            return _easy_disabled()
        try:
            needs_2fa = await asyncio.to_thread(
                telegram_easy.submit_code, easy_store, port, session_id, code.strip(), now=clock()
            )
        except telegram_easy.SessionNotFound:
            return JSONResponse(status_code=404, content={"error": "session_not_found"})
        except Exception:
            return JSONResponse(status_code=502, content={"error": "telegram_unreachable"})
        return JSONResponse(content={"needs_2fa": needs_2fa})

    @app.post("/v1/admin/integrations/telegram/easy/verify-2fa")
    @app.post("/api/integrations/telegram/easy/verify-2fa")
    async def telegram_easy_verify_2fa(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        if not is_telegram_easy_enabled():
            return _easy_disabled()
        body = await _read_json(request)
        if isinstance(body, JSONResponse):
            return body
        session_id = body.get("session_id")
        password = body.get("password")
        if not isinstance(session_id, str) or not isinstance(password, str) or not password:
            return JSONResponse(
                status_code=400, content={"error": "session_id_and_password_required"}
            )
        port = provide_auth_port()
        if port is None:
            return _easy_disabled()
        try:
            await asyncio.to_thread(
                telegram_easy.submit_password, easy_store, port, session_id, password, now=clock()
            )
        except telegram_easy.SessionNotFound:
            return JSONResponse(status_code=404, content={"error": "session_not_found"})
        except Exception:
            return JSONResponse(status_code=502, content={"error": "telegram_unreachable"})
        return JSONResponse(content={"ok": True})

    @app.post("/v1/admin/integrations/telegram/easy/create-bot")
    @app.post("/api/integrations/telegram/easy/create-bot")
    async def telegram_easy_create_bot(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        if not is_telegram_easy_enabled():
            return _easy_disabled()
        body = await _read_json(request)
        if isinstance(body, JSONResponse):
            return body
        session_id = body.get("session_id")
        bot_name = body.get("bot_name")
        if not isinstance(session_id, str) or not isinstance(bot_name, str) or not bot_name.strip():
            return JSONResponse(
                status_code=400, content={"error": "session_id_and_bot_name_required"}
            )
        port = provide_auth_port()
        if port is None:
            return _easy_disabled()
        try:
            status = await asyncio.to_thread(
                telegram_easy.finish_create_bot,
                easy_store,
                port,
                session_id,
                bot_name.strip(),
                now=clock(),
                persist=persist_token,
                username_suffixes=telegram_easy.default_username_suffixes(),
            )
        except telegram_easy.SessionNotFound:
            return JSONResponse(status_code=404, content={"error": "session_not_found"})
        except _PersistError as exc:
            return exc.response
        except telegram_easy.BotCreationFailed:
            return JSONResponse(status_code=502, content={"error": "botfather_failed"})
        except Exception:
            return JSONResponse(status_code=502, content={"error": "telegram_unreachable"})
        return JSONResponse(content={"telegram": status})

    @app.delete("/v1/admin/integrations/telegram/token")
    @app.delete("/api/integrations/telegram/token")
    async def delete_telegram_token(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        _revoke_service_credentials(service="telegram", auth_scheme="bot_token")
        return JSONResponse(content={"telegram": _telegram_status()})

    # -- discord: bot token -------------------------------------------------
    @app.put("/v1/admin/integrations/discord/token")
    @app.put("/api/integrations/discord/token")
    async def put_discord_token(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        value = await _read_secret_field(request, "token")
        if isinstance(value, JSONResponse):
            return value
        try:
            identity = validate_discord_bot_token(value, fetch_json=discord_fetch)
        except ChannelInvalidBotToken:
            return JSONResponse(status_code=400, content={"error": "invalid_bot_token"})
        except Exception:
            return JSONResponse(status_code=502, content={"error": "discord_unreachable"})
        label = identity.get("username") or "Discord bot"
        _revoke_service_credentials(service="discord", auth_scheme="bot_token")
        result = _store_secret(
            service="discord", label=label, auth_scheme="bot_token", secret=value
        )
        if isinstance(result, JSONResponse):
            return result
        return JSONResponse(content={"discord": _discord_status()})

    @app.delete("/v1/admin/integrations/discord/token")
    @app.delete("/api/integrations/discord/token")
    async def delete_discord_token(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        _revoke_service_credentials(service="discord", auth_scheme="bot_token")
        return JSONResponse(content={"discord": _discord_status()})

    # -- slack: bot token (xoxb) + app token (xapp) -------------------------
    @app.put("/v1/admin/integrations/slack/token")
    @app.put("/api/integrations/slack/token")
    async def put_slack_token(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        bot_token = await _read_secret_field(request, "bot_token")
        if isinstance(bot_token, JSONResponse):
            return bot_token
        app_token = await _read_secret_field(request, "app_token")
        if isinstance(app_token, JSONResponse):
            return app_token
        try:
            identity = validate_slack_bot_token(bot_token, fetch_json=slack_fetch)
        except ChannelInvalidBotToken:
            return JSONResponse(status_code=400, content={"error": "invalid_bot_token"})
        except Exception:
            return JSONResponse(status_code=502, content={"error": "slack_unreachable"})
        label = identity.get("team") or "Slack workspace"
        _revoke_service_credentials(service="slack", auth_scheme="bot_token")
        _revoke_service_credentials(service="slack", auth_scheme="app_token")
        bot_result = _store_secret(
            service="slack", label=label, auth_scheme="bot_token", secret=bot_token
        )
        if isinstance(bot_result, JSONResponse):
            return bot_result
        app_result = _store_secret(
            service="slack", label=label, auth_scheme="app_token", secret=app_token
        )
        if isinstance(app_result, JSONResponse):
            return app_result
        return JSONResponse(content={"slack": _slack_status()})

    @app.delete("/v1/admin/integrations/slack/token")
    @app.delete("/api/integrations/slack/token")
    async def delete_slack_token(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        _revoke_service_credentials(service="slack", auth_scheme="bot_token")
        _revoke_service_credentials(service="slack", auth_scheme="app_token")
        return JSONResponse(content={"slack": _slack_status()})


# ---------------------------------------------------------------------------
# Status projections (non-secret, no network)
# ---------------------------------------------------------------------------

def _composio_status() -> dict[str, Any]:
    # `credentialSource` lets the dashboard tell hosted (platform-brokered
    # master key) apart from self-host BYO-key, so the hosted UI can hide the
    # key-entry controls. Additive + optional on the wire for back-compat.
    config = resolve_composio_config(os.environ)
    return {
        "configured": _resolve_composio_api_key() is not None,
        "credentialSource": config.credential_source,
    }


def _telegram_status() -> dict[str, Any]:
    item = _active_credential(service="telegram", auth_scheme="bot_token")
    easy_available = is_telegram_easy_enabled()
    if item is None:
        return {"configured": False, "label": None, "easy_available": easy_available}
    return {
        "configured": True,
        "label": item.get("label") or None,
        "easy_available": easy_available,
    }


def _discord_status() -> dict[str, Any]:
    item = _active_credential(service="discord", auth_scheme="bot_token")
    if item is None:
        return {"configured": False, "label": None}
    return {"configured": True, "label": item.get("label") or None}


def _slack_status() -> dict[str, Any]:
    # Slack needs BOTH a bot token (outbound) and an app token (inbound Socket
    # Mode) to function as a channel; report configured only when both exist.
    bot = _active_credential(service="slack", auth_scheme="bot_token")
    app = _active_credential(service="slack", auth_scheme="app_token")
    if bot is None or app is None:
        return {"configured": False, "label": None}
    return {"configured": True, "label": bot.get("label") or None}


def is_telegram_easy_enabled(env: dict[str, str] | None = None) -> bool:
    """Gate for the phone-number Telegram path (default OFF).

    Requires the master flag AND operator-supplied Telegram app credentials
    (api_id / api_hash from my.telegram.org) — the phone path cannot work
    without them.
    """
    environ = os.environ if env is None else env
    flag = (environ.get("MAGI_TELEGRAM_EASY_SETUP_ENABLED") or "").strip().lower()
    if flag in ("", "0", "false", "no", "off"):
        return False
    return bool(environ.get("TELEGRAM_API_ID") and environ.get("TELEGRAM_API_HASH"))


def _persist_telegram_token(
    token: str, fetch_json: TelegramFetchJson
) -> dict[str, Any] | JSONResponse:
    """Validate (getMe) + vault-store a bot token. Convergence point for both
    the advanced (paste) and easy (BotFather) paths."""
    try:
        identity = validate_bot_token(token, fetch_json=fetch_json)
    except InvalidBotToken:
        return JSONResponse(status_code=400, content={"error": "invalid_bot_token"})
    except Exception:
        return JSONResponse(status_code=502, content={"error": "telegram_unreachable"})
    username = identity.get("username")
    label = f"@{username}" if username else "Telegram bot"
    _revoke_service_credentials(service="telegram", auth_scheme="bot_token")
    result = _store_secret(
        service="telegram", label=label, auth_scheme="bot_token", secret=token
    )
    if isinstance(result, JSONResponse):
        return result
    return _telegram_status()


def _default_telegram_auth_port() -> TelegramUserAuthPort | None:
    if not is_telegram_easy_enabled():
        return None
    try:
        from magi_agent.channels.telegram_easy_telethon import build_telethon_auth_port
    except ImportError:
        return None
    return build_telethon_auth_port(
        api_id=int(os.environ["TELEGRAM_API_ID"]),
        api_hash=os.environ["TELEGRAM_API_HASH"],
    )


# ---------------------------------------------------------------------------
# Vault helpers
# ---------------------------------------------------------------------------

def _store_secret(
    *, service: str, label: str, auth_scheme: str, secret: str
) -> dict[str, Any] | JSONResponse:
    status = vault_local.vault_status()
    if not (status.get("present") and status.get("healthy")):
        return _vault_unavailable(status)
    try:
        seam_result = vault_local.register_credential(
            service=service, label=label, auth_scheme=auth_scheme, secret=secret
        )
    except vault_local.VaultSeamError:
        logger.warning("vault seam rejected credential for service=%s", service)
        return _vault_unavailable(status)
    finally:
        secret = ""  # noqa: F841 — scrub plaintext promptly

    if not isinstance(seam_result, dict) or seam_result.get("disabled"):
        return _vault_unavailable(status)
    vault_ref = seam_result.get("vault_ref")
    if not isinstance(vault_ref, str) or not vault_ref:
        return _vault_unavailable(status)

    return store.add_credential(
        service=service,
        label=label,
        auth_scheme=auth_scheme,
        status=store.STATUS_ACTIVE,
        vault_ref=vault_ref,
        requires_approval=False,
    )


def _revoke_service_credentials(*, service: str, auth_scheme: str) -> None:
    data = store.load_credentials()
    for item in data.get("credentials", []):
        if not isinstance(item, dict):
            continue
        if (
            item.get("service") == service
            and item.get("auth_scheme") == auth_scheme
            and item.get("status") == store.STATUS_ACTIVE
        ):
            vault_ref = item.get("vault_ref")
            if isinstance(vault_ref, str) and vault_ref:
                try:
                    vault_local.revoke_credential(vault_ref=vault_ref)
                except Exception:  # noqa: BLE001 — revoke best-effort; still mark metadata
                    logger.warning("vault revoke failed for service=%s", service)
            store.set_status(str(item.get("id", "")), store.STATUS_REVOKED)


def _active_credential(*, service: str, auth_scheme: str) -> dict[str, Any] | None:
    data = store.load_credentials()
    for item in data.get("credentials", []):
        if not isinstance(item, dict):
            continue
        if (
            item.get("service") == service
            and item.get("auth_scheme") == auth_scheme
            and item.get("status") == store.STATUS_ACTIVE
        ):
            return item
    return None


def _resolve_composio_api_key() -> str | None:
    item = _active_credential(service="composio", auth_scheme="api_key")
    if item is not None:
        vault_ref = item.get("vault_ref")
        if isinstance(vault_ref, str) and vault_ref:
            secret = LocalVault().get_secret(vault_ref)
            if secret and secret.strip():
                return secret.strip()
    env_key = (os.environ.get("COMPOSIO_API_KEY") or "").strip()
    return env_key or None


def _composio_entity_id() -> str:
    config = resolve_composio_config(os.environ)
    return config.entity_id or "default"


# ---------------------------------------------------------------------------
# Default (network-bearing) dependencies
# ---------------------------------------------------------------------------

def _default_composio_client_provider() -> Any | None:
    api_key = _resolve_composio_api_key()
    if not api_key:
        return None
    return composio_connections.build_connections_client(api_key)


def _default_telegram_fetch_json(url: str) -> dict[str, Any]:
    import httpx

    response = httpx.get(url, timeout=15.0)
    return response.json()


def _default_discord_fetch_json(url: str, token: str) -> dict[str, Any]:
    import httpx

    response = httpx.get(url, headers={"Authorization": f"Bot {token}"}, timeout=15.0)
    return response.json()


def _default_slack_fetch_json(url: str, token: str) -> dict[str, Any]:
    import httpx

    response = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15.0)
    return response.json()


# ---------------------------------------------------------------------------
# Request parsing + error responses
# ---------------------------------------------------------------------------

async def _read_json(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "invalid_body"})
    return body


async def _read_secret_field(request: Request, field: str) -> str | JSONResponse:
    body = await _read_json(request)
    if isinstance(body, JSONResponse):
        return body
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        return JSONResponse(status_code=400, content={"error": f"{field}_required"})
    if len(value) > _MAX_FIELD_LEN:
        return JSONResponse(status_code=400, content={"error": f"{field}_too_long"})
    return value.strip()


def _vault_unavailable(status: dict[str, bool]) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "vault_unavailable",
            "message": "integration storage requires an available vault",
            "vault_status": status,
        },
    )


def _easy_disabled() -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "error": "telegram_easy_disabled",
            "message": "set MAGI_TELEGRAM_EASY_SETUP_ENABLED=1 plus TELEGRAM_API_ID/TELEGRAM_API_HASH",
        },
    )


def _composio_not_configured() -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"error": "composio_not_configured", "message": "set a Composio API key first"},
    )


def _composio_upstream_error(op: str) -> JSONResponse:
    logger.warning("composio %s call failed", op)
    return JSONResponse(status_code=502, content={"error": "composio_upstream_error", "op": op})
