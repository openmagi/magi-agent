"""Tests for GET /v1/app/customize and PATCH /v1/app/customize/tools/{name} endpoints."""
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


# Alias for tests that need to accept a tmp_path argument (unused, kept for compat)
def _build_runtime(tmp_path=None, *, gateway_token: str = _TOKEN) -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=gateway_token,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def _client(tmp_path=None) -> TestClient:
    """Unauthenticated test client (no gateway token header)."""
    return TestClient(create_app(_build_runtime(tmp_path)))


def test_patch_tool_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)  # no token header
    resp = client.patch("/v1/app/customize/tools/web_fetch", json={"enabled": False})
    assert resp.status_code == 401


def test_patch_tool_persists_and_applies(tmp_path, monkeypatch):
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    runtime = _build_runtime(tmp_path, gateway_token=_TOKEN)
    # pick a real tool name from the runtime registry
    tool_name = runtime.tool_registry.list_all()[0].name
    client = TestClient(create_app(runtime))
    client.headers.update({"x-gateway-token": _TOKEN})

    resp = client.patch(f"/v1/app/customize/tools/{tool_name}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["overrides"]["tools"][tool_name] is False
    # persisted to disk
    import json
    assert json.loads(cfile.read_text())["tools"][tool_name] is False
    # applied live
    assert runtime.tool_registry.resolve_registration(tool_name).enabled is False


def test_patch_tool_bad_body(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.patch("/v1/app/customize/tools/web_fetch", json={"nope": 1})
    assert resp.status_code == 400


def test_customize_requires_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))
    # No token header — must get 401
    res = client.get("/v1/app/customize")
    assert res.status_code == 401


def test_patch_tool_unknown_name_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.patch("/v1/app/customize/tools/__definitely_not_a_tool__", json={"enabled": False})
    assert resp.status_code == 404
    # nothing persisted
    import os
    cfile = tmp_path / "customize.json"
    assert not cfile.exists() or "__definitely_not_a_tool__" not in cfile.read_text()


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
