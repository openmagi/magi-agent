"""Dashboard pack-builder REST endpoints (self-host only, default-OFF, 410 gate).

These tests adapt the plan's PR4 suite to the real ``(app, runtime)`` registrar
convention (mirroring ``register_customize_routes(app, runtime)``). ``runtime`` is
unused by the FS-based dashboard routes, so the tests pass ``None``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_agent.packs.dashboard_authored import (
    DASHBOARD_PACK_DIR_NAME,
    read_sidecar,
)
from magi_agent.transport.packs_dashboard import register_dashboard_pack_routes


@pytest.fixture
def packs_root(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED", "1")
    # Ensure hosted detection is OFF (default in tests, but be explicit).
    monkeypatch.delenv("MAGI_DEPLOYMENT", raising=False)
    # The transport resolves the dashboard pack dir under the first *writable*
    # (non-bundled) pack search base; the test patches discovery to a temp dir.
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )
    return tmp_path / DASHBOARD_PACK_DIR_NAME


@pytest.fixture
def client(packs_root):  # the fixture indirectly installs env + base patch
    app = FastAPI()
    register_dashboard_pack_routes(app, runtime=None)
    return TestClient(app)


def _payload(id_: str = "ssn", **over) -> dict:
    base = {
        "id": id_,
        "label": "SSN",
        "scope": "always",
        "enabled": True,
        "trigger": {"tool": "web_fetch", "match": {"pattern": "ssn", "isRegex": False}},
        "action": "block",
    }
    base.update(over)
    return base


def test_get_empty(client):
    r = client.get("/v1/app/packs/dashboard/checks")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["checks"] == []


def test_put_then_get_round_trip(client):
    r = client.put("/v1/app/packs/dashboard/checks/ssn", json=_payload("ssn"))
    assert r.status_code == 200
    g = client.get("/v1/app/packs/dashboard/checks")
    assert any(c["id"] == "ssn" for c in g.json()["checks"])


def test_put_path_body_id_mismatch_rejected(client):
    r = client.put("/v1/app/packs/dashboard/checks/ssn", json=_payload("other"))
    assert r.status_code == 400


def test_put_invalid_regex_400(client):
    payload = _payload(
        "bad",
        trigger={"tool": "t", "match": {"pattern": "([unclosed", "isRegex": True}},
    )
    r = client.put("/v1/app/packs/dashboard/checks/bad", json=payload)
    assert r.status_code == 400


def test_put_invalid_leaves_disk_unchanged(client, packs_root):
    payload = _payload(
        "bad",
        trigger={"tool": "t", "match": {"pattern": "([unclosed", "isRegex": True}},
    )
    r = client.put("/v1/app/packs/dashboard/checks/bad", json=payload)
    assert r.status_code == 400
    # Nothing persisted — sidecar still empty.
    assert read_sidecar(packs_root) == []


def test_delete_missing_404(client):
    r = client.delete("/v1/app/packs/dashboard/checks/absent")
    assert r.status_code == 404


def test_delete_existing(client):
    client.put("/v1/app/packs/dashboard/checks/x", json=_payload("x"))
    r = client.delete("/v1/app/packs/dashboard/checks/x")
    assert r.status_code == 200
    g = client.get("/v1/app/packs/dashboard/checks")
    assert g.json()["checks"] == []


def test_flag_off_returns_410(monkeypatch):
    monkeypatch.setenv("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED", "0")
    app = FastAPI()
    register_dashboard_pack_routes(app, runtime=None)
    c = TestClient(app)
    assert c.get("/v1/app/packs/dashboard/checks").status_code == 410
    assert c.put("/v1/app/packs/dashboard/checks/x", json={}).status_code == 410
    assert c.delete("/v1/app/packs/dashboard/checks/x").status_code == 410
    assert c.get("/v1/app/packs/dashboard/menu").status_code == 410


def test_hosted_deployment_returns_410(monkeypatch, tmp_path):
    # Flag ON but hosted → still 410 (self-host only, same model as HookBus).
    monkeypatch.setenv("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED", "1")
    monkeypatch.setattr(
        "magi_agent.transport.packs_dashboard.is_hosted_deployment",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )
    app = FastAPI()
    register_dashboard_pack_routes(app, runtime=None)
    c = TestClient(app)
    assert c.get("/v1/app/packs/dashboard/checks").status_code == 410


def test_menu_returns_tool_list(client):
    r = client.get("/v1/app/packs/dashboard/menu")
    assert r.status_code == 200
    assert isinstance(r.json()["tools"], list)
