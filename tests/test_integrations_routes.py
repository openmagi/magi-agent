from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.credentials_admin.store import credentials_path
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.integrations import register_integrations_routes

TOKEN = "local-token"
HEADERS = {"x-gateway-token": TOKEN}


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=TOKEN,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


class _FakeToolkits:
    def list(self, **kwargs: object) -> dict[str, object]:
        return {"items": [{"slug": "gmail", "name": "Gmail"}], "next_cursor": None}


class _FakeAuthConfigs:
    def list(self, **kwargs: object) -> dict[str, object]:
        return {"items": [{"id": "ac_1", "is_composio_managed": True}]}


class _FakeConnectedAccounts:
    def get(self, connection_id: str) -> dict[str, object]:
        return {"id": connection_id, "toolkit": "gmail", "status": "ACTIVE"}

    def list(self, **kwargs: object) -> dict[str, object]:
        # A different toolkit than the one under test (gmail), so connect takes
        # the fresh-link path rather than the already-connected short-circuit.
        return {"items": [{"id": "conn_1", "toolkit": "slack", "status": "ACTIVE"}]}

    def delete(self, connection_id: str) -> dict[str, object]:
        return {"deleted": True}

    def link(self, *, user_id: str, auth_config_id: str) -> dict[str, object]:
        return {"id": "conn_1", "status": "INITIATED", "redirect_url": "https://auth/x"}


class _FakeComposioClient:
    toolkits = _FakeToolkits()
    auth_configs = _FakeAuthConfigs()
    connected_accounts = _FakeConnectedAccounts()


class _FakeBroker:
    """Stand-in for :class:`ComposioBrokerClient` (platform credential mode)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def initiate(self, toolkit: str) -> dict[str, object]:
        self.calls.append(("initiate", toolkit))
        return {
            "id": "conn_broker",
            "status": "INITIATED",
            "redirect_url": "https://broker/auth",
        }

    def status(self, connection_id: str) -> dict[str, object]:
        self.calls.append(("status", connection_id))
        return {"id": connection_id, "status": "ACTIVE"}

    def list(self) -> list[dict[str, object]]:
        self.calls.append(("list",))
        return [{"id": "conn_broker", "toolkit": "gmail", "status": "ACTIVE"}]

    def delete(self, connection_id: str) -> None:
        self.calls.append(("delete", connection_id))

    def catalog(self, *, category, cursor, managed_only) -> dict[str, object]:
        self.calls.append(("catalog", category, cursor, managed_only))
        return {"items": [{"slug": "gmail", "name": "Gmail"}], "next_cursor": None}


def _client(
    monkeypatch,
    tmp_path,
    *,
    composio_client_provider=None,
    composio_broker_provider=None,
    telegram_fetch_json=None,
    vault: bool = True,
) -> TestClient:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    if vault:
        monkeypatch.setenv("MAGI_LOCAL_VAULT_ENABLED", "1")
        monkeypatch.setenv("MAGI_VAULT_DIR", str(tmp_path / "vault"))
    else:
        monkeypatch.delenv("MAGI_LOCAL_VAULT_ENABLED", raising=False)
    target = credentials_path()
    if target.exists():
        target.unlink()
    app = FastAPI()
    register_integrations_routes(
        app,
        _runtime(),
        composio_client_provider=composio_client_provider,
        composio_broker_provider=composio_broker_provider,
        telegram_fetch_json=telegram_fetch_json,
    )
    return TestClient(app)


def test_aggregate_requires_gateway_token(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    assert client.get("/v1/admin/integrations").status_code == 401


def test_aggregate_reports_unconfigured(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    body = client.get("/v1/admin/integrations", headers=HEADERS).json()
    assert body["composio"]["configured"] is False
    assert body["telegram"]["configured"] is False
    assert body["vault_status"]["present"] is True


def test_aggregate_reports_credential_source_missing_by_default(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.delenv("MAGI_COMPOSIO_CREDENTIAL_SOURCE", raising=False)
    client = _client(monkeypatch, tmp_path)
    composio = client.get("/v1/admin/integrations", headers=HEADERS).json()["composio"]
    # Self-host with no key configured: not hosted, so the UI keeps the BYO card.
    assert composio["credentialSource"] == "missing"


def test_aggregate_reports_hosted_credential_source(monkeypatch, tmp_path) -> None:
    # Platform-brokered: a managed master key in env + hosted source. The hosted
    # dashboard branches on this to hide the BYO-key controls.
    monkeypatch.setenv("COMPOSIO_API_KEY", "comp_master_key")
    monkeypatch.setenv("MAGI_COMPOSIO_CREDENTIAL_SOURCE", "hosted")
    client = _client(monkeypatch, tmp_path)
    composio = client.get("/v1/admin/integrations", headers=HEADERS).json()["composio"]
    assert composio["configured"] is True
    assert composio["credentialSource"] == "hosted"


def test_composio_key_round_trips_through_vault(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    put = client.put(
        "/v1/admin/integrations/composio/key",
        headers=HEADERS,
        json={"api_key": "comp-secret-123"},
    )
    assert put.status_code == 200
    assert put.json()["composio"]["configured"] is True
    agg = client.get("/v1/admin/integrations", headers=HEADERS).json()
    assert agg["composio"]["configured"] is True


def test_catalog_409_when_no_key(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, composio_client_provider=lambda: None)
    resp = client.get("/v1/admin/integrations/composio/catalog", headers=HEADERS)
    assert resp.status_code == 409
    assert resp.json()["error"] == "composio_not_configured"


def test_catalog_returns_items(monkeypatch, tmp_path) -> None:
    client = _client(
        monkeypatch, tmp_path, composio_client_provider=lambda: _FakeComposioClient()
    )
    body = client.get("/v1/admin/integrations/composio/catalog", headers=HEADERS).json()
    assert body["items"] == [
        {"slug": "gmail", "name": "Gmail", "logo": None, "categories": []}
    ]


def test_connect_returns_redirect_url(monkeypatch, tmp_path) -> None:
    client = _client(
        monkeypatch, tmp_path, composio_client_provider=lambda: _FakeComposioClient()
    )
    body = client.post(
        "/v1/admin/integrations/composio/connect",
        headers=HEADERS,
        json={"toolkit": "gmail"},
    ).json()
    assert body == {
        "connection_id": "conn_1",
        "status": "INITIATED",
        "redirect_url": "https://auth/x",
    }


def test_connect_status_polls(monkeypatch, tmp_path) -> None:
    client = _client(
        monkeypatch, tmp_path, composio_client_provider=lambda: _FakeComposioClient()
    )
    body = client.get(
        "/v1/admin/integrations/composio/connect/conn_1/status", headers=HEADERS
    ).json()
    assert body == {"connection_id": "conn_1", "status": "ACTIVE", "toolkit": "gmail"}


def test_connect_requires_toolkit(monkeypatch, tmp_path) -> None:
    client = _client(
        monkeypatch, tmp_path, composio_client_provider=lambda: _FakeComposioClient()
    )
    resp = client.post(
        "/v1/admin/integrations/composio/connect", headers=HEADERS, json={}
    )
    assert resp.status_code == 400


def test_telegram_token_validates_and_persists(monkeypatch, tmp_path) -> None:
    def fetch_json(url: str) -> dict[str, object]:
        return {"ok": True, "result": {"id": 1, "username": "my_bot", "first_name": "B"}}

    client = _client(monkeypatch, tmp_path, telegram_fetch_json=fetch_json)
    put = client.put(
        "/v1/admin/integrations/telegram/token",
        headers=HEADERS,
        json={"token": "123:ABC"},
    )
    assert put.status_code == 200
    assert put.json()["telegram"] == {
        "configured": True,
        "label": "@my_bot",
        "easy_available": False,
    }
    # delete clears it
    deleted = client.delete("/v1/admin/integrations/telegram/token", headers=HEADERS)
    assert deleted.json()["telegram"]["configured"] is False


def test_telegram_token_rejected(monkeypatch, tmp_path) -> None:
    def fetch_json(url: str) -> dict[str, object]:
        return {"ok": False, "error_code": 401}

    client = _client(monkeypatch, tmp_path, telegram_fetch_json=fetch_json)
    resp = client.put(
        "/v1/admin/integrations/telegram/token",
        headers=HEADERS,
        json={"token": "bad"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_bot_token"


def test_key_store_503_when_vault_off(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, vault=False)
    resp = client.put(
        "/v1/admin/integrations/composio/key",
        headers=HEADERS,
        json={"api_key": "x"},
    )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Platform (broker) credential mode: connect/catalog routes proxy through the
# openmagi broker instead of a local Composio client (no Composio key needed).
# ---------------------------------------------------------------------------


def test_platform_broker_connect_routes_through_broker(monkeypatch, tmp_path) -> None:
    broker = _FakeBroker()
    # No local Composio client/key configured: the local path would 409, but the
    # broker provider being present makes connect succeed via the broker.
    client = _client(
        monkeypatch,
        tmp_path,
        composio_broker_provider=lambda: broker,
    )
    resp = client.post(
        "/v1/admin/integrations/composio/connect",
        headers=HEADERS,
        json={"toolkit": "gmail"},
    )
    assert resp.status_code == 200
    assert resp.json()["redirect_url"] == "https://broker/auth"
    assert ("initiate", "gmail") in broker.calls


def test_platform_broker_catalog_routes_through_broker(monkeypatch, tmp_path) -> None:
    broker = _FakeBroker()
    client = _client(monkeypatch, tmp_path, composio_broker_provider=lambda: broker)
    resp = client.get(
        "/v1/admin/integrations/composio/catalog?category=productivity",
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["items"][0]["slug"] == "gmail"
    assert broker.calls == [("catalog", "productivity", None, True)]


def test_platform_broker_status_and_list_route_through_broker(monkeypatch, tmp_path) -> None:
    broker = _FakeBroker()
    client = _client(monkeypatch, tmp_path, composio_broker_provider=lambda: broker)
    status = client.get(
        "/v1/admin/integrations/composio/connect/conn_broker/status", headers=HEADERS
    )
    assert status.status_code == 200
    assert status.json()["status"] == "ACTIVE"
    listing = client.get(
        "/v1/admin/integrations/composio/connections", headers=HEADERS
    )
    assert listing.status_code == 200
    assert listing.json()["connections"][0]["id"] == "conn_broker"
    assert ("status", "conn_broker") in broker.calls
    assert ("list",) in broker.calls


def test_platform_broker_disconnect_routes_through_broker(monkeypatch, tmp_path) -> None:
    broker = _FakeBroker()
    client = _client(monkeypatch, tmp_path, composio_broker_provider=lambda: broker)
    resp = client.delete(
        "/v1/admin/integrations/composio/connection/conn_broker", headers=HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["disconnected"] == "conn_broker"
    assert ("delete", "conn_broker") in broker.calls


def test_no_broker_provider_keeps_local_client_path(monkeypatch, tmp_path) -> None:
    # Broker provider returns None (non-platform mode): the local Composio
    # client path is used unchanged. With a fake local client, connect works.
    client = _client(
        monkeypatch,
        tmp_path,
        composio_client_provider=lambda: _FakeComposioClient(),
        composio_broker_provider=lambda: None,
    )
    resp = client.post(
        "/v1/admin/integrations/composio/connect",
        headers=HEADERS,
        json={"toolkit": "gmail"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "INITIATED"
