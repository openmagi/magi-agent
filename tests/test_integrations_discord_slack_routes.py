"""Admin token routes for the Discord + Slack channels (PR4).

Mirrors the telegram token route tests: validate (via an injected fetch_json),
vault-store, surface a non-secret status block, and revoke on delete.
"""
from __future__ import annotations

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


def _client(monkeypatch, tmp_path, *, discord_fetch_json=None, slack_fetch_json=None) -> TestClient:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAGI_LOCAL_VAULT_ENABLED", "1")
    monkeypatch.setenv("MAGI_VAULT_DIR", str(tmp_path / "vault"))
    target = credentials_path()
    if target.exists():
        target.unlink()
    app = FastAPI()
    register_integrations_routes(
        app,
        _runtime(),
        discord_fetch_json=discord_fetch_json,
        slack_fetch_json=slack_fetch_json,
    )
    return TestClient(app)


# -- Discord ---------------------------------------------------------------

def test_discord_token_validates_and_persists(monkeypatch, tmp_path) -> None:
    def fetch_json(url: str, token: str) -> dict[str, object]:
        return {"id": "9", "username": "magi_bot", "bot": True}

    client = _client(monkeypatch, tmp_path, discord_fetch_json=fetch_json)
    put = client.put(
        "/v1/admin/integrations/discord/token", headers=HEADERS, json={"token": "disc-abc"}
    )
    assert put.status_code == 200
    assert put.json()["discord"] == {"configured": True, "label": "magi_bot"}

    aggregate = client.get("/v1/admin/integrations", headers=HEADERS)
    assert aggregate.json()["discord"]["configured"] is True

    deleted = client.delete("/v1/admin/integrations/discord/token", headers=HEADERS)
    assert deleted.json()["discord"]["configured"] is False


def test_discord_token_rejected(monkeypatch, tmp_path) -> None:
    def fetch_json(url: str, token: str) -> dict[str, object]:
        return {"message": "401: Unauthorized", "code": 0}

    client = _client(monkeypatch, tmp_path, discord_fetch_json=fetch_json)
    resp = client.put(
        "/v1/admin/integrations/discord/token", headers=HEADERS, json={"token": "bad"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_bot_token"


def test_discord_requires_auth(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    resp = client.put("/v1/admin/integrations/discord/token", json={"token": "x"})
    assert resp.status_code in (401, 403)


# -- Slack -----------------------------------------------------------------

def test_slack_tokens_validate_and_persist(monkeypatch, tmp_path) -> None:
    def fetch_json(url: str, token: str) -> dict[str, object]:
        return {"ok": True, "team": "Acme", "user": "magi"}

    client = _client(monkeypatch, tmp_path, slack_fetch_json=fetch_json)
    put = client.put(
        "/v1/admin/integrations/slack/token",
        headers=HEADERS,
        json={"bot_token": "xoxb-1", "app_token": "xapp-1"},
    )
    assert put.status_code == 200
    assert put.json()["slack"] == {"configured": True, "label": "Acme"}

    aggregate = client.get("/v1/admin/integrations", headers=HEADERS)
    assert aggregate.json()["slack"]["configured"] is True

    deleted = client.delete("/v1/admin/integrations/slack/token", headers=HEADERS)
    assert deleted.json()["slack"]["configured"] is False


def test_slack_requires_both_tokens(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, slack_fetch_json=lambda u, t: {"ok": True})
    resp = client.put(
        "/v1/admin/integrations/slack/token", headers=HEADERS, json={"bot_token": "xoxb-1"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "app_token_required"


def test_slack_token_rejected(monkeypatch, tmp_path) -> None:
    def fetch_json(url: str, token: str) -> dict[str, object]:
        return {"ok": False, "error": "invalid_auth"}

    client = _client(monkeypatch, tmp_path, slack_fetch_json=fetch_json)
    resp = client.put(
        "/v1/admin/integrations/slack/token",
        headers=HEADERS,
        json={"bot_token": "bad", "app_token": "xapp-1"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_bot_token"
