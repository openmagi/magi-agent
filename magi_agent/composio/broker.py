"""HTTP client for the platform Composio broker (``platform`` credential mode).

In ``platform`` mode the runtime holds no Composio key: the magi-cp
control-plane brokers every Composio operation behind the tenant's platform
Bearer token, holding the master Composio key server-side. This module is the
thin client the dashboard "Integrations" routes use for
connect/status/list/delete/catalog when the resolved credential source is
``platform``.

The HTTP seam (:data:`BrokerTransport`) is injected so the routes stay testable
without a live broker, mirroring the Telegram/Discord ``fetch_json`` injection
pattern already used in :mod:`magi_agent.transport.integrations`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

# transport(method, url, *, token, json=None) -> parsed JSON object
BrokerTransport = Callable[..., dict[str, Any]]

_CONNECT_PATH = "/v1/integrations/composio/connect"
_CONNECTIONS_PATH = "/v1/integrations/composio/connections"
_CONNECTION_PATH = "/v1/integrations/composio/connection"
_CATALOG_PATH = "/v1/integrations/composio/catalog"
_SESSION_PATH = "/v1/integrations/composio/session"


class ComposioBrokerClient:
    """Tenant-scoped client for the platform Composio broker endpoints.

    The broker resolves the tenant from the Bearer ``token`` and scopes
    connected accounts to ``entity_id`` (the per-user/bot segment derived by
    :func:`magi_agent.composio.config.resolve_composio_config`).
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        entity_id: str,
        transport: BrokerTransport,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._entity = entity_id or "default"
        self._transport = transport

    def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        if params:
            query = "&".join(
                f"{quote(str(key))}={quote(str(value))}"
                for key, value in params.items()
                if value is not None and value != ""
            )
            if query:
                url = f"{url}?{query}"
        result = self._transport(method, url, token=self._token, json=json)
        return result if isinstance(result, dict) else {}

    def initiate(self, toolkit: str) -> dict[str, Any]:
        return self._call(
            "POST",
            _CONNECT_PATH,
            json={"toolkit": toolkit, "entity_id": self._entity},
        )

    def status(self, connection_id: str) -> dict[str, Any]:
        return self._call(
            "GET",
            f"{_CONNECT_PATH}/{quote(connection_id)}/status",
            params={"entity_id": self._entity},
        )

    def list(self) -> list[Any]:
        result = self._call(
            "GET", _CONNECTIONS_PATH, params={"entity_id": self._entity}
        )
        items = result.get("connections")
        return items if isinstance(items, list) else []

    def delete(self, connection_id: str) -> None:
        self._call(
            "DELETE",
            f"{_CONNECTION_PATH}/{quote(connection_id)}",
            params={"entity_id": self._entity},
        )

    def catalog(
        self,
        *,
        category: str | None,
        cursor: str | None,
        managed_only: bool,
    ) -> dict[str, Any]:
        return self._call(
            "GET",
            _CATALOG_PATH,
            params={
                "category": category,
                "cursor": cursor,
                "managed_only": "1" if managed_only else "0",
                "entity_id": self._entity,
            },
        )

    def session(self, *, toolkits: tuple[str, ...] = ()) -> dict[str, Any]:
        """Mint a Composio MCP session for this entity (approach A).

        Returns ``{"mcp_url", "headers"}`` for Composio's own MCP endpoint; the
        caller connects its ADK toolset DIRECTLY there (the broker steps out of
        the tool-call path after minting).
        """
        body: dict[str, Any] = {"entity_id": self._entity}
        if toolkits:
            body["toolkits"] = list(toolkits)
        return self._call("POST", _SESSION_PATH, json=body)


def default_http_transport(
    method: str,
    url: str,
    *,
    token: str,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bearer-authed httpx transport (lazy import keeps cold-start clean)."""
    import httpx

    response = httpx.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}"},
        json=json,
        timeout=30.0,
    )
    response.raise_for_status()
    if not response.content:
        return {}
    parsed = response.json()
    return parsed if isinstance(parsed, dict) else {}


def build_broker_client(
    config: Any,
    *,
    transport: BrokerTransport | None = None,
) -> ComposioBrokerClient | None:
    """Build a broker client from a resolved ``platform``-mode config, or None.

    Returns ``None`` unless the config carries a broker URL + platform token
    (i.e. ``credential_source == "platform"`` and active).
    """
    base_url = getattr(config, "platform_broker_url", None)
    token = getattr(config, "platform_token", None)
    if not base_url or not token:
        return None
    return ComposioBrokerClient(
        base_url=base_url,
        token=token,
        entity_id=getattr(config, "entity_id", None) or "default",
        transport=transport or default_http_transport,
    )
