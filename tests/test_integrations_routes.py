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

    def authorize(self, *, user_id: str, toolkit: str) -> dict[str, object]:
        return {"id": "conn_1", "status": "INITIATED", "redirect_url": "https://auth/x"}


class _FakeConnectedAccounts:
    def get(self, connection_id: str) -> dict[str, object]:
        return {"id": connection_id, "toolkit": "gmail", "status": "ACTIVE"}

    def list(self, **kwargs: object) -> dict[str, object]:
        return {"items": [{"id": "conn_1", "toolkit": "gmail", "status": "ACTIVE"}]}

    def delete(self, connection_id: str) -> dict[str, object]:
        return {"deleted": True}


class _FakeComposioClient:
    toolkits = _FakeToolkits()
    connected_accounts = _FakeConnectedAccounts()


def _client(
    monkeypatch,
    tmp_path,
    *,
    composio_client_provider=None,
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
