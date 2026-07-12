"""Composio connection management used by the dashboard Integrations tab.

Thin, side-effect-free wrappers over the Composio SDK (``composio==0.13.1``).
The SDK client is always injected so tests run without network or the optional
``composio`` package. The HTTP layer resolves the API key from the local vault
and builds a real client via :func:`build_connections_client`.
"""

from __future__ import annotations

from typing import Any, Protocol


class _ToolkitsAPI(Protocol):
    def list(self, **kwargs: object) -> object: ...


class _AuthConfigsAPI(Protocol):
    def list(self, **kwargs: object) -> object: ...


class _ConnectedAccountsAPI(Protocol):
    def get(self, connection_id: str) -> object: ...

    def list(self, **kwargs: object) -> object: ...

    def delete(self, connection_id: str) -> object: ...

    def link(self, *, user_id: str, auth_config_id: str) -> object: ...


class _ConnectionsClient(Protocol):
    toolkits: _ToolkitsAPI
    auth_configs: _AuthConfigsAPI
    connected_accounts: _ConnectedAccountsAPI


def list_catalog(
    client: _ConnectionsClient,
    *,
    category: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    managed_only: bool = True,
) -> dict[str, Any]:
    """Return a normalized page of available toolkits.

    ``managed_only`` restricts to Composio-managed auth (one-click connect,
    no user-supplied OAuth app required).
    """

    kwargs: dict[str, object] = {}
    if managed_only:
        kwargs["managed_by"] = "composio"
    if category is not None:
        kwargs["category"] = category
    if cursor is not None:
        kwargs["cursor"] = cursor
    if limit is not None:
        kwargs["limit"] = limit

    response = client.toolkits.list(**kwargs)
    items = [_normalize_toolkit(item) for item in _iter_items(response)]
    return {"items": items, "next_cursor": _attr(response, "next_cursor")}


def initiate_connection(
    client: _ConnectionsClient,
    *,
    entity_id: str,
    toolkit: str,
) -> dict[str, Any]:
    """Start an OAuth connection; returns the redirect URL the user must visit.

    Composio retired the managed-OAuth ``toolkits.authorize`` endpoint (400
    ``ConnectedAccount_BadRequest`` / ``ComposioLegacyConnectedAccountsEndpoint
    RetiredError``). The supported v3 flow is: resolve the toolkit's auth config
    (``auth_configs.list(toolkit_slug=...)``) then ``connected_accounts.link(
    user_id, auth_config_id)`` → redirect URL. For Composio-managed toolkits an
    auth config already exists; we prefer the managed one when several are
    returned.
    """

    auth_config_id = _resolve_auth_config_id(client, toolkit)
    request = client.connected_accounts.link(
        user_id=entity_id, auth_config_id=auth_config_id
    )
    return {
        "connection_id": _attr(request, "id"),
        "status": _attr(request, "status"),
        "redirect_url": _attr(request, "redirect_url"),
    }


def _resolve_auth_config_id(client: _ConnectionsClient, toolkit: str) -> str:
    """Return the auth config id to link against for ``toolkit``.

    Prefers a Composio-managed config (one-click, no user-supplied OAuth app);
    falls back to the first config returned. Raises when the toolkit has no auth
    config (e.g. a non-managed toolkit that needs its OAuth app configured on
    the Composio dashboard first).
    """

    response = client.auth_configs.list(toolkit_slug=toolkit)
    items = _iter_items(response)
    if not items:
        raise ValueError(f"no Composio auth config available for toolkit {toolkit!r}")
    chosen = next(
        (item for item in items if _attr(item, "is_composio_managed")),
        items[0],
    )
    auth_config_id = _attr(chosen, "id")
    if not auth_config_id:
        raise ValueError(f"Composio auth config for toolkit {toolkit!r} has no id")
    return str(auth_config_id)


def connection_status(
    client: _ConnectionsClient,
    *,
    connection_id: str,
) -> dict[str, Any]:
    """Non-blocking poll of a connection's status."""

    account = client.connected_accounts.get(connection_id)
    return {
        "connection_id": connection_id,
        "status": _attr(account, "status"),
        "toolkit": _attr(account, "toolkit"),
    }


def list_connections(
    client: _ConnectionsClient,
    *,
    entity_id: str,
) -> list[dict[str, Any]]:
    """List the entity's existing connected accounts."""

    response = client.connected_accounts.list(user_ids=[entity_id])
    return [_normalize_connection(item) for item in _iter_items(response)]


def delete_connection(client: _ConnectionsClient, *, connection_id: str) -> None:
    """Disconnect a connected account."""

    client.connected_accounts.delete(connection_id)


def mint_session(
    client: Any,
    *,
    entity_id: str,
    toolkits: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Mint a Composio MCP session for ``entity_id`` using the master client.

    Approach A: the caller (a self-hosted runtime brokering through us) connects
    its ADK toolset DIRECTLY to the returned Composio MCP URL — we mint the
    session with the platform master key and then step out of the tool-call
    path. The session is scoped to the caller's entity via Composio's
    ``user_id`` so connected accounts never cross tenants.
    """

    create_kwargs: dict[str, object] = {"user_id": entity_id}
    if toolkits:
        create_kwargs["toolkits"] = list(toolkits)
    session = client.create(**create_kwargs)
    mcp = _attr(session, "mcp")
    return {
        "mcp_url": _attr(mcp, "url"),
        "headers": _attr(mcp, "headers"),
    }


def build_connections_client(api_key: str) -> _ConnectionsClient:
    """Build a real Composio client. Raises ImportError if the extra is absent."""

    from composio import Composio

    return Composio(api_key=api_key)


def _iter_items(response: object) -> list[Any]:
    items = _attr(response, "items")
    if items is None:
        return list(response) if isinstance(response, (list, tuple)) else []
    return list(items)


def _normalize_toolkit(item: object) -> dict[str, Any]:
    meta = _attr(item, "meta")
    return {
        "slug": _attr(item, "slug"),
        "name": _attr(item, "name"),
        "logo": _attr(meta, "logo"),
        "categories": [_category_name(c) for c in _attr(meta, "categories") or []],
    }


def _normalize_connection(item: object) -> dict[str, Any]:
    return {
        "connection_id": _attr(item, "id"),
        "toolkit": _attr(item, "toolkit"),
        "status": _attr(item, "status"),
    }


def _category_name(category: object) -> Any:
    if isinstance(category, str):
        return category
    return _attr(category, "name") or _attr(category, "slug") or str(category)


def _attr(obj: object, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
