from __future__ import annotations

from magi_agent.observability.models import ActivityEvent
from magi_agent.observability.store import ActivityStore


def test_record_and_list(tmp_path):
    store = ActivityStore(tmp_path / "obs.db")
    rid = store.record_event(ActivityEvent(kind="tool_start", tool_name="read",
                                           session_id="s1", payload={"path": "a"}))
    assert rid >= 1
    rows = store.list_events()
    assert len(rows) == 1
    assert rows[0]["kind"] == "tool_start"
    assert rows[0]["payload"] == {"path": "a"}
    assert rows[0]["id"] == rid
    store.close()


def test_filters_and_since_id(tmp_path):
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    id2 = store.record_event(ActivityEvent(kind="tool_end", session_id="s1"))
    store.record_event(ActivityEvent(kind="tool_start", session_id="s2"))
    assert {r["kind"] for r in store.list_events(session_id="s1")} == {"tool_start", "tool_end"}
    assert [r["kind"] for r in store.list_events(kind="tool_start")] == ["tool_start", "tool_start"]
    after = store.list_events(since_id=id2)
    assert all(r["id"] > id2 for r in after)
    store.close()


def test_prune_by_max_events(tmp_path):
    store = ActivityStore(tmp_path / "obs.db")
    for _ in range(10):
        store.record_event(ActivityEvent(kind="x"))
    removed = store.prune(max_events=4)
    assert removed == 6
    assert store.count_events() == 4
    store.close()


def test_record_after_close_returns_negative(tmp_path):
    store = ActivityStore(tmp_path / "obs.db")
    store.close()
    assert store.record_event(ActivityEvent(kind="x")) == -1
    assert store.list_events() == []


def test_prune_by_retention(tmp_path):
    import time as _t
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(kind="old", ts=_t.time() - 10 * 86400))
    store.record_event(ActivityEvent(kind="new"))
    removed = store.prune(retention_days=7)
    assert removed == 1
    assert [r["kind"] for r in store.list_events()] == ["new"]
    store.close()


def test_list_sessions_groups_by_session(tmp_path):
    import time as _t
    store = ActivityStore(tmp_path / "obs.db")
    # s1: 2 events, one tool_start
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1", ts=_t.time() - 2))
    store.record_event(ActivityEvent(kind="tool_end", session_id="s1", ts=_t.time() - 1))
    # s2: 1 event, no tool_start — recorded last so it has latest last_active
    store.record_event(ActivityEvent(kind="message", session_id="s2", ts=_t.time()))

    rows = store.list_sessions()
    assert len(rows) == 2

    # s2 was last active, so it should appear first
    assert rows[0]["id"] == "s2"
    assert rows[1]["id"] == "s1"

    # verify s1 aggregates
    s1 = next(r for r in rows if r["id"] == "s1")
    assert s1["event_count"] == 2
    assert s1["tool_count"] == 1

    # verify s2 aggregates
    s2 = next(r for r in rows if r["id"] == "s2")
    assert s2["event_count"] == 1
    assert s2["tool_count"] == 0

    store.close()


def test_list_sessions_empty_when_closed(tmp_path):
    store = ActivityStore(tmp_path / "obs.db")
    store.close()
    assert store.list_sessions() == []


def test_latest_event_with_kind_like(tmp_path):
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(kind="task_board", session_id="s1"))
    for _ in range(5):
        store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    store.record_event(ActivityEvent(kind="task_board", session_id="s1"))
    latest = store.latest_event_with_kind_like("board")
    assert latest is not None and latest["kind"] == "task_board"
    # the SECOND board event (highest id) is returned
    assert latest["id"] == store.list_events(kind="task_board", limit=10)[-1]["id"]
    assert store.latest_event_with_kind_like("nope") is None
    store.close()
