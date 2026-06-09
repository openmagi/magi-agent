"""Tests for GET /v1/app/customize endpoint."""
from __future__ import annotations

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

_TOKEN = "test-gateway-token"


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=_TOKEN,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def test_customize_requires_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))
    # No token header — must get 401
    res = client.get("/v1/app/customize")
    assert res.status_code == 401


def test_customize_returns_catalog_and_overrides(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))
    res = client.get("/v1/app/customize", headers={"x-gateway-token": _TOKEN})
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"catalog", "overrides"}
    assert set(body["catalog"].keys()) == {"verification", "tools"}
    assert set(body["catalog"]["verification"].keys()) == {
        "recipes",
        "harnessPresets",
        "hooks",
    }
    # No customize.json → tools overrides default to empty dict
    assert body["overrides"]["tools"] == {}
