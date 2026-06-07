from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_agent.observability.bus import ActivityBus
from magi_agent.observability.models import ActivityEvent
from magi_agent.observability.store import ActivityStore


def _client(tmp_path, token="local-dev-token"):
    store = ActivityStore(tmp_path / "obs.db")
    bus = ActivityBus(replay=10)
    runtime = SimpleNamespace(config=SimpleNamespace(gateway_token=token, bot_id="bot-x"))
    from magi_agent.observability.api import build_api_router
    app = FastAPI()
    app.include_router(build_api_router(store, bus, runtime))
    return TestClient(app), store, bus


def test_activity_requires_token(tmp_path):
    client, store, _ = _client(tmp_path)
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    assert client.get("/api/observability/v1/activity").status_code == 401
    ok = client.get("/api/observability/v1/activity",
                    headers={"Authorization": "Bearer local-dev-token"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["events"][0]["kind"] == "tool_start"


def test_activity_filters(tmp_path):
    client, store, _ = _client(tmp_path)
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    store.record_event(ActivityEvent(kind="tool_end", session_id="s2"))
    r = client.get("/api/observability/v1/activity?session_id=s2",
                   headers={"Authorization": "Bearer local-dev-token"})
    kinds = [e["kind"] for e in r.json()["events"]]
    assert kinds == ["tool_end"]


def test_meta(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.get("/api/observability/v1/meta",
                   headers={"Authorization": "Bearer local-dev-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "v1"
    assert body["bot_id"] == "bot-x"


def test_sessions_endpoint(tmp_path):
    client, store, _ = _client(tmp_path)
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    store.record_event(ActivityEvent(kind="tool_end", session_id="s1"))
    store.record_event(ActivityEvent(kind="message", session_id="s2"))
    r = client.get("/api/observability/v1/sessions",
                   headers={"Authorization": "Bearer local-dev-token"})
    assert r.status_code == 200
    body = r.json()
    assert "sessions" in body
    ids = [s["id"] for s in body["sessions"]]
    assert "s1" in ids and "s2" in ids
    s1 = next(s for s in body["sessions"] if s["id"] == "s1")
    assert s1["event_count"] == 2
    assert s1["tool_count"] == 1


def test_sessions_requires_auth(tmp_path):
    client, _, _ = _client(tmp_path)
    assert client.get("/api/observability/v1/sessions").status_code == 401


def test_session_events_endpoint(tmp_path):
    client, store, _ = _client(tmp_path)
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    store.record_event(ActivityEvent(kind="tool_end", session_id="s1"))
    store.record_event(ActivityEvent(kind="message", session_id="s2"))
    r = client.get("/api/observability/v1/sessions/s1/events",
                   headers={"Authorization": "Bearer local-dev-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "s1"
    assert len(body["events"]) == 2
    assert all(e["session_id"] == "s1" for e in body["events"])


def test_session_events_requires_auth(tmp_path):
    client, _, _ = _client(tmp_path)
    assert client.get("/api/observability/v1/sessions/s1/events").status_code == 401


def test_health_live_endpoint(tmp_path, monkeypatch):
    import magi_agent.transport.health as _health_mod
    monkeypatch.setattr(_health_mod, "healthz_payload", lambda _rt: {"ok": True, "status": "ready"})
    client, _, _ = _client(tmp_path)
    r = client.get("/api/observability/v1/health/live",
                   headers={"Authorization": "Bearer local-dev-token"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_health_live_requires_auth(tmp_path):
    client, _, _ = _client(tmp_path)
    assert client.get("/api/observability/v1/health/live").status_code == 401


def test_board_empty(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.get("/api/observability/v1/board",
                   headers={"Authorization": "Bearer local-dev-token"})
    assert r.status_code == 200
    assert r.json() == {"board": None}


def test_board_requires_auth(tmp_path):
    client, _, _ = _client(tmp_path)
    assert client.get("/api/observability/v1/board").status_code == 401


def test_channels_endpoint(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.get("/api/observability/v1/channels",
                   headers={"Authorization": "Bearer local-dev-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["channels"] == []
    assert "note" in body


def test_channels_requires_auth(tmp_path):
    client, _, _ = _client(tmp_path)
    assert client.get("/api/observability/v1/channels").status_code == 401


def test_missions_endpoint(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.get("/api/observability/v1/missions",
                   headers={"Authorization": "Bearer local-dev-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["missions"] == []
    assert "note" in body


def test_missions_requires_auth(tmp_path):
    client, _, _ = _client(tmp_path)
    assert client.get("/api/observability/v1/missions").status_code == 401


def test_activity_stream_sentinel_and_no_subscriber_leak(tmp_path):
    client, _store, bus = _client(tmp_path)
    with client.stream(
        "GET",
        "/api/observability/v1/activity/stream?max_events=1",
        headers={"Authorization": "Bearer local-dev-token"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        chunks = "".join(list(resp.iter_text()))
    assert "event: activity" in chunks
    assert "data:" in chunks
    assert "stream_open" in chunks
    assert chunks.endswith("\n\n")
    assert len(bus._subscribers) == 0
