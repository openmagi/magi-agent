"""POST /v1/app/packs/{id}/state — dashboard install/remove of a pack."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

_TOKEN = "test-gateway-token"
# A real bundled first-party pack (default-enabled) discovered regardless of env.
_PACK = "openmagi.evidence-firstparty-activity"


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


def _authed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Isolate packs-state.json (sibling of config.toml) into tmp so the test
    # never writes the real ~/.magi override.
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


def _enabled_of(packs: list[dict], pack_id: str) -> bool | None:
    for p in packs:
        if p.get("packId") == pack_id:
            return p.get("enabled")
    return None


def test_requires_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    client = TestClient(create_app(_runtime()))
    assert client.post(f"/v1/app/packs/{_PACK}/state", json={"enabled": False}).status_code == 401


def test_bad_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.post(f"/v1/app/packs/{_PACK}/state", json={})
    assert resp.status_code == 400
    assert resp.json()["error"] == "enabled_bool_required"


def test_unknown_pack_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.post("/v1/app/packs/openmagi.does-not-exist/state", json={"enabled": False})
    assert resp.status_code == 404
    assert resp.json()["error"] == "unknown_pack"


def test_remove_then_install_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    # Baseline: the pack is enabled by default.
    baseline = client.get("/v1/app/packs").json()["packs"]
    assert _enabled_of(baseline, _PACK) is True

    # Remove -> inventory reflects it disabled.
    removed = client.post(f"/v1/app/packs/{_PACK}/state", json={"enabled": False})
    assert removed.status_code == 200
    assert _enabled_of(removed.json()["packs"], _PACK) is False

    # Install again -> back to enabled (first-party is always recoverable).
    installed = client.post(f"/v1/app/packs/{_PACK}/state", json={"enabled": True})
    assert installed.status_code == 200
    assert _enabled_of(installed.json()["packs"], _PACK) is True
