"""Tests for the agent-mode CRUD endpoints under /v1/app/modes."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

_TOKEN = "test-gateway-token"

_MODE = {
    "displayName": "Coding",
    "systemPrompt": "Be a careful engineer.",
    "toolDelta": {"exclude": ["WebSearch"], "include": []},
    "scopedPolicyIds": [],
}


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


@pytest.fixture(autouse=True)
def _no_builtins(monkeypatch: pytest.MonkeyPatch) -> None:
    # User-store CRUD route tests; isolate from the default-ON built-in posture
    # modes (covered by tests/test_builtin_modes.py).
    monkeypatch.setenv("MAGI_CUSTOMIZE_BUILTIN_MODES_ENABLED", "0")


def _authed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


def test_modes_requires_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))  # no token header
    assert client.get("/v1/app/modes").status_code == 401


def test_list_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.get("/v1/app/modes")
    assert resp.status_code == 200
    assert resp.json() == {"modes": [], "activeMode": None}


def test_upsert_and_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.put("/v1/app/modes/coding", json=_MODE)
    assert resp.status_code == 200
    assert resp.json()["mode"]["id"] == "coding"
    listing = client.get("/v1/app/modes").json()
    assert [m["id"] for m in listing["modes"]] == ["coding"]
    assert listing["modes"][0]["toolDelta"]["exclude"] == ["WebSearch"]


def test_upsert_invalid_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.put("/v1/app/modes/coding", json={"displayName": "   "})  # empty name
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_mode"


def test_set_active_and_reflect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    client.put("/v1/app/modes/coding", json=_MODE)
    resp = client.post("/v1/app/modes/active", json={"modeId": "coding"})
    assert resp.status_code == 200 and resp.json()["activeMode"] == "coding"
    assert client.get("/v1/app/modes").json()["activeMode"] == "coding"


def test_set_active_unknown_is_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.post("/v1/app/modes/active", json={"modeId": "nope"})
    assert resp.status_code == 404


def test_set_active_null_clears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    client.put("/v1/app/modes/coding", json=_MODE)
    client.post("/v1/app/modes/active", json={"modeId": "coding"})
    resp = client.post("/v1/app/modes/active", json={"modeId": None})
    assert resp.status_code == 200 and resp.json()["activeMode"] is None


def test_delete_clears_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    client.put("/v1/app/modes/coding", json=_MODE)
    client.post("/v1/app/modes/active", json={"modeId": "coding"})
    resp = client.delete("/v1/app/modes/coding")
    assert resp.status_code == 200
    body = resp.json()
    assert body["modes"] == [] and body["activeMode"] is None


def test_put_bad_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.put(
        "/v1/app/modes/coding",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


# PR-P5.1: built-in posture modes are read-only. The guards are flag-independent
# (the builtin- prefix is reserved regardless of the autouse _no_builtins), so
# both routes must 400 rather than mutate/500.
def test_delete_builtin_mode_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.delete("/v1/app/modes/builtin-coding")
    assert resp.status_code == 400
    assert resp.json()["error"] == "delete_rejected"


def test_upsert_builtin_mode_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.put("/v1/app/modes/builtin-coding", json={"displayName": "X"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "upsert_rejected"
