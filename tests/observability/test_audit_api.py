"""Endpoint: GET /api/observability/v1/sessions/{id}/audit.

Flag-gated (default-OFF -> 404). Hermetic: explicit token + monkeypatched flag
env, real ActivityStore on tmp_path. Mirrors tests/observability/test_api.py.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_agent.observability.bus import ActivityBus
from magi_agent.observability.models import ActivityEvent
from magi_agent.observability.store import ActivityStore

_FLAG = "MAGI_CHAT_AUDIT_PANEL_ENABLED"
_AUTH = {"Authorization": "Bearer local-dev-token"}


def _client(tmp_path, token="local-dev-token"):
    store = ActivityStore(tmp_path / "obs.db")
    bus = ActivityBus(replay=10)
    runtime = SimpleNamespace(config=SimpleNamespace(gateway_token=token, bot_id="bot-x"))
    from magi_agent.observability.api import build_api_router

    app = FastAPI()
    app.include_router(build_api_router(store, bus, runtime))
    return TestClient(app), store


def test_audit_flag_off_returns_404(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(_FLAG, raising=False)
    client, store = _client(tmp_path)
    r = client.get("/api/observability/v1/sessions/s1/audit", headers=_AUTH)
    assert r.status_code == 404
    assert r.json() == {"error": "feature_disabled"}


def test_audit_requires_auth(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(_FLAG, "1")
    client, _ = _client(tmp_path)
    assert client.get("/api/observability/v1/sessions/s1/audit").status_code == 401


def test_audit_flag_on_returns_200_with_contract(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(_FLAG, "1")
    client, store = _client(tmp_path)
    store.record_event(
        ActivityEvent(
            kind="rule_check",
            session_id="s1",
            run_id="run-a",
            ts=1.0,
            payload={"verdict": "ok", "ruleId": "verifier:sha256:a"},
        )
    )
    r = client.get("/api/observability/v1/sessions/s1/audit", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["sessionId"] == "s1"
    assert "runs" in body and "sources" in body
    assert body["runs"][0]["runId"] == "run-a"
    assert body["runs"][0]["verdicts"][0]["displayLabel"] == "VERIFIED"
    assert body["sources"] == []


def test_audit_empty_session_on(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(_FLAG, "1")
    client, _ = _client(tmp_path)
    r = client.get("/api/observability/v1/sessions/none/audit", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["runs"] == []
    assert body["sources"] == []
