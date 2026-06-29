from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_agent.transport.composio_broker import register_composio_broker_routes


class _FakeMcp:
    url = "https://mcp.composio.dev/session/minted"
    headers = {"Authorization": "Bearer composio-session", "x-composio-session": "s_1"}


class _FakeSession:
    mcp = _FakeMcp()


class _FakeToolkits:
    def list(self, **kwargs: object) -> dict[str, object]:
        return {"items": [{"slug": "gmail", "name": "Gmail"}], "next_cursor": None}

    def authorize(self, *, user_id: str, toolkit: str) -> dict[str, object]:
        return {"id": "conn_1", "status": "INITIATED", "redirect_url": "https://auth/x"}


class _FakeConnectedAccounts:
    def get(self, connection_id: str) -> dict[str, object]:
        return {"id": connection_id, "toolkit": "gmail", "status": "ACTIVE"}

    def list(self, **kwargs: object) -> dict[str, object]:
        self.last_kwargs = kwargs
        return {"items": [{"id": "conn_1", "toolkit": "gmail", "status": "ACTIVE"}]}

    def delete(self, connection_id: str) -> dict[str, object]:
        return {"deleted": True}


class _FakeMasterClient:
    def __init__(self) -> None:
        self.toolkits = _FakeToolkits()
        self.connected_accounts = _FakeConnectedAccounts()
        self.create_calls: list[dict] = []

    def create(self, **kwargs: object) -> _FakeSession:
        self.create_calls.append(dict(kwargs))
        return _FakeSession()


HDR = {"Authorization": "Bearer good-token", "X-Magi-Composio-Entity": "openmagi:user:u1:bot:b2"}


def _client(*, client=None, valid_token="good-token"):
    app = FastAPI()
    master = client if client is not None else _FakeMasterClient()
    register_composio_broker_routes(
        app,
        enabled=True,
        master_client_provider=lambda: master,
        token_validator=lambda t: t if t == valid_token else None,
    )
    return TestClient(app), master


# ── auth ──────────────────────────────────────────────────────────────


def test_session_requires_valid_bearer_token() -> None:
    c, _ = _client()
    r = c.post("/v1/integrations/composio/session", json={"entity_id": "e1"})
    assert r.status_code == 401


def test_session_rejects_wrong_token() -> None:
    c, _ = _client()
    r = c.post(
        "/v1/integrations/composio/session",
        headers={"Authorization": "Bearer nope"},
        json={"entity_id": "e1"},
    )
    assert r.status_code == 401


# ── session mint (approach A) ──────────────────────────────────────────


def test_session_mints_composio_url_scoped_to_entity() -> None:
    c, master = _client()
    r = c.post(
        "/v1/integrations/composio/session",
        headers=HDR,
        json={"toolkits": ["gmail", "googledrive"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mcp_url"] == "https://mcp.composio.dev/session/minted"
    assert body["headers"]["x-composio-session"] == "s_1"
    # Scoped to the entity from the header, with the toolkit allowlist.
    assert master.create_calls == [
        {"user_id": "openmagi:user:u1:bot:b2", "toolkits": ["gmail", "googledrive"]}
    ]


def test_session_falls_back_to_default_entity_without_header() -> None:
    c, master = _client()
    r = c.post(
        "/v1/integrations/composio/session",
        headers={"Authorization": "Bearer good-token"},
        json={},
    )
    assert r.status_code == 200
    assert master.create_calls == [{"user_id": "default"}]


def test_session_503_when_master_key_unconfigured() -> None:
    app = FastAPI()
    register_composio_broker_routes(
        app,
        enabled=True,
        master_client_provider=lambda: None,
        token_validator=lambda t: "ok",
    )
    r = TestClient(app).post(
        "/v1/integrations/composio/session",
        headers={"Authorization": "Bearer x"},
        json={},
    )
    assert r.status_code == 503
    assert r.json()["error"] == "broker_master_key_unconfigured"


# ── OAuth + catalog (broker uses master key + entity) ──────────────────


def test_catalog_returns_items() -> None:
    c, _ = _client()
    r = c.get("/v1/integrations/composio/catalog", headers=HDR)
    assert r.status_code == 200
    assert r.json()["items"][0]["slug"] == "gmail"


def test_connect_initiates_with_entity() -> None:
    c, _ = _client()
    r = c.post(
        "/v1/integrations/composio/connect", headers=HDR, json={"toolkit": "gmail"}
    )
    assert r.status_code == 200
    assert r.json()["redirect_url"] == "https://auth/x"


def test_connect_requires_toolkit() -> None:
    c, _ = _client()
    r = c.post("/v1/integrations/composio/connect", headers=HDR, json={})
    assert r.status_code == 400


def test_status_and_list_and_delete() -> None:
    c, master = _client()
    s = c.get("/v1/integrations/composio/connect/conn_1/status", headers=HDR)
    assert s.status_code == 200 and s.json()["status"] == "ACTIVE"
    li = c.get("/v1/integrations/composio/connections", headers=HDR)
    assert li.status_code == 200
    assert li.json()["connections"][0]["connection_id"] == "conn_1"
    # entity scoping reached the SDK call.
    assert master.connected_accounts.last_kwargs == {
        "user_ids": ["openmagi:user:u1:bot:b2"]
    }
    d = c.delete("/v1/integrations/composio/connection/conn_1", headers=HDR)
    assert d.status_code == 200 and d.json()["disconnected"] == "conn_1"


def test_upstream_error_maps_to_502() -> None:
    class _Boom(_FakeMasterClient):
        def create(self, **kwargs: object):
            raise RuntimeError("composio down")

    c, _ = _client(client=_Boom())
    r = c.post("/v1/integrations/composio/session", headers=HDR, json={})
    assert r.status_code == 502
    assert r.json()["op"] == "session"


# ── default-OFF gate ───────────────────────────────────────────────────


def test_disabled_by_default_registers_no_routes(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_COMPOSIO_BROKER_ENABLED", raising=False)
    app = FastAPI()
    # enabled=None → reads the (unset) flag → OFF → no routes registered.
    register_composio_broker_routes(
        app,
        master_client_provider=lambda: _FakeMasterClient(),
        token_validator=lambda t: "ok",
    )
    r = TestClient(app).post(
        "/v1/integrations/composio/session",
        headers={"Authorization": "Bearer x"},
        json={},
    )
    assert r.status_code == 404  # route does not exist when broker is off


def test_flag_enables_routes(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_COMPOSIO_BROKER_ENABLED", "1")
    app = FastAPI()
    register_composio_broker_routes(
        app,
        master_client_provider=lambda: _FakeMasterClient(),
        token_validator=lambda t: "ok" if t == "good" else None,
    )
    r = TestClient(app).post(
        "/v1/integrations/composio/session",
        headers={"Authorization": "Bearer good"},
        json={},
    )
    assert r.status_code == 200
