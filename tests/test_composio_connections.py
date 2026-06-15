from __future__ import annotations

import pytest


class _FakeMeta:
    def __init__(self, logo: str | None, categories: list[object]) -> None:
        self.logo = logo
        self.categories = categories


class _FakeCategory:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeToolkitItem:
    def __init__(
        self,
        slug: str,
        name: str,
        logo: str | None = None,
        categories: list[object] | None = None,
    ) -> None:
        self.slug = slug
        self.name = name
        self.meta = _FakeMeta(logo, categories or [])


class _FakeToolkitListResponse:
    def __init__(self, items: list[object], next_cursor: str | None = None) -> None:
        self.items = items
        self.next_cursor = next_cursor


class _FakeToolkits:
    def __init__(self, response: object) -> None:
        self._response = response
        self.list_calls: list[dict[str, object]] = []
        self.authorize_calls: list[dict[str, object]] = []

    def list(self, **kwargs: object) -> object:
        self.list_calls.append(dict(kwargs))
        return self._response

    def authorize(self, *, user_id: str, toolkit: str) -> object:
        self.authorize_calls.append({"user_id": user_id, "toolkit": toolkit})
        return _FakeConnectionRequest(
            id="conn_123",
            status="INITIATED",
            redirect_url="https://auth.example/redirect",
        )


class _FakeConnectionRequest:
    def __init__(self, id: str, status: str, redirect_url: str | None) -> None:
        self.id = id
        self.status = status
        self.redirect_url = redirect_url


class _FakeConnectedAccount:
    def __init__(self, id: str, toolkit: str, status: str) -> None:
        self.id = id
        self.toolkit = toolkit
        self.status = status


class _FakeConnectedAccounts:
    def __init__(self, account: object | None = None, items: list[object] | None = None) -> None:
        self._account = account
        self._items = items or []
        self.get_calls: list[str] = []
        self.list_calls: list[dict[str, object]] = []
        self.delete_calls: list[str] = []

    def get(self, connection_id: str) -> object:
        self.get_calls.append(connection_id)
        return self._account

    def list(self, **kwargs: object) -> object:
        self.list_calls.append(dict(kwargs))
        return _FakeToolkitListResponse(items=self._items)

    def delete(self, connection_id: str) -> dict[str, object]:
        self.delete_calls.append(connection_id)
        return {"deleted": True}


class _FakeClient:
    def __init__(self, toolkits: object, connected_accounts: object) -> None:
        self.toolkits = toolkits
        self.connected_accounts = connected_accounts


def test_list_catalog_filters_to_managed_and_normalizes_items() -> None:
    from magi_agent.composio.connections import list_catalog

    toolkits = _FakeToolkits(
        _FakeToolkitListResponse(
            items=[
                _FakeToolkitItem(
                    "gmail",
                    "Gmail",
                    logo="https://logo/gmail.png",
                    categories=[_FakeCategory("productivity")],
                )
            ],
            next_cursor="next_1",
        )
    )
    client = _FakeClient(toolkits, _FakeConnectedAccounts())

    page = list_catalog(client)

    assert toolkits.list_calls[0]["managed_by"] == "composio"
    assert page["next_cursor"] == "next_1"
    assert page["items"] == [
        {
            "slug": "gmail",
            "name": "Gmail",
            "logo": "https://logo/gmail.png",
            "categories": ["productivity"],
        }
    ]


def test_list_catalog_passes_query_cursor_limit_and_can_disable_managed_filter() -> None:
    from magi_agent.composio.connections import list_catalog

    toolkits = _FakeToolkits(_FakeToolkitListResponse(items=[]))
    client = _FakeClient(toolkits, _FakeConnectedAccounts())

    list_catalog(client, category="crm", cursor="c1", limit=25, managed_only=False)

    call = toolkits.list_calls[0]
    assert call["category"] == "crm"
    assert call["cursor"] == "c1"
    assert call["limit"] == 25
    assert "managed_by" not in call


def test_initiate_connection_returns_redirect_url() -> None:
    from magi_agent.composio.connections import initiate_connection

    toolkits = _FakeToolkits(_FakeToolkitListResponse(items=[]))
    client = _FakeClient(toolkits, _FakeConnectedAccounts())

    result = initiate_connection(client, entity_id="user-1", toolkit="gmail")

    assert toolkits.authorize_calls[0] == {"user_id": "user-1", "toolkit": "gmail"}
    assert result == {
        "connection_id": "conn_123",
        "status": "INITIATED",
        "redirect_url": "https://auth.example/redirect",
    }


def test_connection_status_reads_connected_account() -> None:
    from magi_agent.composio.connections import connection_status

    accounts = _FakeConnectedAccounts(
        account=_FakeConnectedAccount("conn_123", "gmail", "ACTIVE")
    )
    client = _FakeClient(_FakeToolkits(_FakeToolkitListResponse(items=[])), accounts)

    result = connection_status(client, connection_id="conn_123")

    assert accounts.get_calls == ["conn_123"]
    assert result == {"connection_id": "conn_123", "status": "ACTIVE", "toolkit": "gmail"}


def test_list_connections_normalizes_accounts() -> None:
    from magi_agent.composio.connections import list_connections

    accounts = _FakeConnectedAccounts(
        items=[_FakeConnectedAccount("conn_1", "slack", "ACTIVE")]
    )
    client = _FakeClient(_FakeToolkits(_FakeToolkitListResponse(items=[])), accounts)

    result = list_connections(client, entity_id="user-1")

    assert accounts.list_calls[0]["user_ids"] == ["user-1"]
    assert result == [{"connection_id": "conn_1", "toolkit": "slack", "status": "ACTIVE"}]


def test_delete_connection_calls_sdk() -> None:
    from magi_agent.composio.connections import delete_connection

    accounts = _FakeConnectedAccounts()
    client = _FakeClient(_FakeToolkits(_FakeToolkitListResponse(items=[])), accounts)

    delete_connection(client, connection_id="conn_9")

    assert accounts.delete_calls == ["conn_9"]


def test_build_connections_client_requires_composio_package() -> None:
    from magi_agent.composio.connections import build_connections_client

    # composio optional extra is not installed in the test env → ImportError surfaces.
    with pytest.raises(ImportError):
        build_connections_client("sk-test")
