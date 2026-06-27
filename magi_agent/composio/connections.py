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

    def authorize(self, *, user_id: str, toolkit: str) -> object: ...


class _ConnectedAccountsAPI(Protocol):
    def get(self, connection_id: str) -> object: ...

    def list(self, **kwargs: object) -> object: ...

    def delete(self, connection_id: str) -> object: ...


class _ConnectionsClient(Protocol):
    toolkits: _ToolkitsAPI
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
    """Start an OAuth connection; returns the redirect URL the user must visit."""

    request = client.toolkits.authorize(user_id=entity_id, toolkit=toolkit)
    return {
        "connection_id": _attr(request, "id"),
        "status": _attr(request, "status"),
        "redirect_url": _attr(request, "redirect_url"),
    }


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
