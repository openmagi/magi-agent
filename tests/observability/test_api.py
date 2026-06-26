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


# ---------------------------------------------------------------------------
# Task-2: new /activity query params
# ---------------------------------------------------------------------------

def _auth_get(client, path):
    return client.get(path, headers={"Authorization": "Bearer local-dev-token"})


def test_activity_default_shape_unchanged(tmp_path):
    """No new params -> response is still {"events": [...]}."""
    client, store, _ = _client(tmp_path)
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    r = _auth_get(client, "/api/observability/v1/activity")
    assert r.status_code == 200
    body = r.json()
    assert "events" in body
    assert len(body["events"]) == 1


def test_activity_exclude_kind(tmp_path):
    """exclude_kind must reach list_events and filter out the specified kind."""
    client, store, _ = _client(tmp_path)
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    store.record_event(ActivityEvent(kind="tool_end", session_id="s1"))
    r = _auth_get(client, "/api/observability/v1/activity?exclude_kind=tool_start")
    assert r.status_code == 200
    kinds = [e["kind"] for e in r.json()["events"]]
    assert kinds == ["tool_end"]


def test_activity_exclude_kind_comma(tmp_path):
    """exclude_kind with comma list excludes multiple kinds."""
    client, store, _ = _client(tmp_path)
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    store.record_event(ActivityEvent(kind="tool_end", session_id="s1"))
    store.record_event(ActivityEvent(kind="message", session_id="s1"))
    r = _auth_get(client, "/api/observability/v1/activity?exclude_kind=tool_start,tool_end")
    assert r.status_code == 200
    kinds = [e["kind"] for e in r.json()["events"]]
    assert kinds == ["message"]


def test_activity_status_filter(tmp_path):
    """status param must reach list_events and filter by event status field."""
    client, store, _ = _client(tmp_path)
    store.record_event(ActivityEvent(kind="tool_end", status="ok"))
    store.record_event(ActivityEvent(kind="tool_end", status="error"))
    r = _auth_get(client, "/api/observability/v1/activity?status=ok")
    assert r.status_code == 200
    statuses = [e["status"] for e in r.json()["events"]]
    assert statuses == ["ok"]


def test_activity_q_filter(tmp_path):
    """q param must reach list_events and filter by summary substring."""
    client, store, _ = _client(tmp_path)
    store.record_event(ActivityEvent(kind="message", summary="hello world"))
    store.record_event(ActivityEvent(kind="message", summary="goodbye world"))
    r = _auth_get(client, "/api/observability/v1/activity?q=hello")
    assert r.status_code == 200
    summaries = [e["summary"] for e in r.json()["events"]]
    assert summaries == ["hello world"]


def test_activity_before_id(tmp_path):
    """before_id must reach list_events and return only events with id < before_id."""
    client, store, _ = _client(tmp_path)
    id1 = store.record_event(ActivityEvent(kind="tool_start"))
    id2 = store.record_event(ActivityEvent(kind="tool_end"))
    store.record_event(ActivityEvent(kind="message"))
    r = _auth_get(client, f"/api/observability/v1/activity?before_id={id2}")
    assert r.status_code == 200
    ids = [e["id"] for e in r.json()["events"]]
    assert ids == [id1]


def test_activity_kind_comma(tmp_path):
    """kind accepts a comma-separated string and returns events matching any of the kinds."""
    client, store, _ = _client(tmp_path)
    store.record_event(ActivityEvent(kind="tool_start"))
    store.record_event(ActivityEvent(kind="tool_end"))
    store.record_event(ActivityEvent(kind="message"))
    r = _auth_get(client, "/api/observability/v1/activity?kind=tool_start,tool_end")
    assert r.status_code == 200
    kinds = sorted(e["kind"] for e in r.json()["events"])
    assert kinds == ["tool_end", "tool_start"]


def test_activity_before_id_invalid(tmp_path):
    """Non-integer before_id must return 422 (same validation style as since_id/limit)."""
    client, _, _ = _client(tmp_path)
    r = _auth_get(client, "/api/observability/v1/activity?before_id=notanint")
    assert r.status_code == 422


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


# ---------------------------------------------------------------------------
# Task-7: /meta — kind_breakdown and categories extensions
# ---------------------------------------------------------------------------

def test_meta_existing_fields_unchanged(tmp_path):
    """/meta still returns version, bot_id, and events (backward compat)."""
    client, store, _ = _client(tmp_path)
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    r = client.get("/api/observability/v1/meta",
                   headers={"Authorization": "Bearer local-dev-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "v1"
    assert body["bot_id"] == "bot-x"
    assert body["events"] == 1


def test_meta_includes_kind_breakdown_empty(tmp_path):
    """/meta kind_breakdown is {} when no events exist."""
    client, _, _ = _client(tmp_path)
    r = client.get("/api/observability/v1/meta",
                   headers={"Authorization": "Bearer local-dev-token"})
    assert r.status_code == 200
    body = r.json()
    assert "kind_breakdown" in body
    assert body["kind_breakdown"] == {}


def test_meta_includes_kind_breakdown_with_events(tmp_path):
    """/meta kind_breakdown reflects accurate per-kind global counts."""
    client, store, _ = _client(tmp_path)
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    store.record_event(ActivityEvent(kind="rule_check", session_id="s1"))
    r = client.get("/api/observability/v1/meta",
                   headers={"Authorization": "Bearer local-dev-token"})
    assert r.status_code == 200
    bd = r.json()["kind_breakdown"]
    assert bd["tool_start"] == 2
    assert bd["rule_check"] == 1
    assert "tool_end" not in bd


def test_meta_includes_categories(tmp_path):
    """/meta includes a 'categories' key with taxonomy payload."""
    client, _, _ = _client(tmp_path)
    r = client.get("/api/observability/v1/meta",
                   headers={"Authorization": "Bearer local-dev-token"})
    assert r.status_code == 200
    body = r.json()
    assert "categories" in body


def test_meta_categories_shape(tmp_path):
    """categories payload has 'categories' dict and 'noise_kinds' list."""
    client, _, _ = _client(tmp_path)
    r = client.get("/api/observability/v1/meta",
                   headers={"Authorization": "Bearer local-dev-token"})
    cats = r.json()["categories"]
    assert isinstance(cats, dict)
    assert "categories" in cats
    assert "noise_kinds" in cats
    assert isinstance(cats["categories"], dict)
    assert isinstance(cats["noise_kinds"], list)


def test_meta_categories_contains_real_kinds(tmp_path):
    """categories dict contains real emitted kinds, not fictional ones."""
    client, _, _ = _client(tmp_path)
    r = client.get("/api/observability/v1/meta",
                   headers={"Authorization": "Bearer local-dev-token"})
    cats = r.json()["categories"]["categories"]
    # Flatten all kinds across all categories
    all_kinds = {k for kinds in cats.values() for k in kinds}
    # Real emitted kinds must be present
    assert "tool_start" in all_kinds
    assert "tool_end" in all_kinds
    assert "rule_check" in all_kinds
    assert "turn_start" in all_kinds
    assert "error" in all_kinds
    # No fictional kinds
    assert "my_fake_kind" not in all_kinds
    assert "message" not in all_kinds  # 'message' is not a real runtime kind


def test_meta_categories_noise_identifiable(tmp_path):
    """noise_kinds list identifies the default-hidden noise set."""
    client, _, _ = _client(tmp_path)
    r = client.get("/api/observability/v1/meta",
                   headers={"Authorization": "Bearer local-dev-token"})
    noise = r.json()["categories"]["noise_kinds"]
    assert "text_delta" in noise
    assert "heartbeat" in noise
    assert "turn_phase" in noise
    assert "runtime_trace" in noise
    assert "tool_progress" in noise


def test_meta_categories_named_category_keys(tmp_path):
    """categories dict has the expected category names."""
    client, _, _ = _client(tmp_path)
    r = client.get("/api/observability/v1/meta",
                   headers={"Authorization": "Bearer local-dev-token"})
    cats = r.json()["categories"]["categories"]
    assert "lifecycle" in cats
    assert "tools" in cats
    assert "policy" in cats
    assert "errors" in cats
    assert "other" in cats
