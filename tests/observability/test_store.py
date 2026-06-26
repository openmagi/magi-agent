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


# ---------------------------------------------------------------------------
# Helper: seed a store with varied rows for filter tests
# ---------------------------------------------------------------------------

def _seed_store(tmp_path):
    """Return a seeded ActivityStore with 6 rows of varied kind/status/summary."""
    store = ActivityStore(tmp_path / "obs_filter.db")
    store.record_event(ActivityEvent(kind="tool_start", status="ok",    summary="reading file",   session_id="s1"))
    store.record_event(ActivityEvent(kind="tool_end",   status="ok",    summary="done reading",   session_id="s1"))
    store.record_event(ActivityEvent(kind="tool_start", status="error", summary="write failed",   session_id="s1"))
    store.record_event(ActivityEvent(kind="message",    status="ok",    summary="user message",   session_id="s2"))
    store.record_event(ActivityEvent(kind="task_board", status="pending",summary="board snapshot",session_id="s2"))
    store.record_event(ActivityEvent(kind="tool_end",   status="error", summary="timed out",      session_id="s2"))
    return store


# ---------------------------------------------------------------------------
# Back-compat: no-new-arg call returns the same rows as before
# ---------------------------------------------------------------------------

def test_back_compat_no_new_args(tmp_path):
    store = _seed_store(tmp_path)
    rows = store.list_events()
    assert len(rows) == 6
    assert [r["kind"] for r in rows] == [
        "tool_start", "tool_end", "tool_start", "message", "task_board", "tool_end"
    ]
    store.close()


# ---------------------------------------------------------------------------
# kind: single value still produces same single-clause behavior
# ---------------------------------------------------------------------------

def test_kind_single_unchanged(tmp_path):
    store = _seed_store(tmp_path)
    rows = store.list_events(kind="tool_start")
    assert len(rows) == 2
    assert all(r["kind"] == "tool_start" for r in rows)
    store.close()


# ---------------------------------------------------------------------------
# kind: comma-separated string -> IN list
# ---------------------------------------------------------------------------

def test_kind_multi_in(tmp_path):
    store = _seed_store(tmp_path)
    rows = store.list_events(kind="tool_start,tool_end")
    assert len(rows) == 4
    assert all(r["kind"] in {"tool_start", "tool_end"} for r in rows)
    store.close()


def test_kind_multi_in_with_spaces_stripped(tmp_path):
    """Tokens in comma list must be stripped of surrounding whitespace."""
    store = _seed_store(tmp_path)
    rows = store.list_events(kind=" tool_start , tool_end ")
    assert len(rows) == 4
    store.close()


# ---------------------------------------------------------------------------
# exclude_kind: comma string -> kind NOT IN
# ---------------------------------------------------------------------------

def test_exclude_kind_single(tmp_path):
    store = _seed_store(tmp_path)
    rows = store.list_events(exclude_kind="message")
    assert len(rows) == 5
    assert all(r["kind"] != "message" for r in rows)
    store.close()


def test_exclude_kind_multi(tmp_path):
    store = _seed_store(tmp_path)
    rows = store.list_events(exclude_kind="message,task_board")
    assert len(rows) == 4
    assert all(r["kind"] not in {"message", "task_board"} for r in rows)
    store.close()


def test_kind_and_exclude_kind_combine_with_and(tmp_path):
    """kind=tool_start,tool_end AND exclude_kind=tool_end -> only tool_start rows."""
    store = _seed_store(tmp_path)
    rows = store.list_events(kind="tool_start,tool_end", exclude_kind="tool_end")
    assert len(rows) == 2
    assert all(r["kind"] == "tool_start" for r in rows)
    store.close()


# ---------------------------------------------------------------------------
# status: comma string -> status IN
# ---------------------------------------------------------------------------

def test_status_filter_single(tmp_path):
    store = _seed_store(tmp_path)
    rows = store.list_events(status="ok")
    assert len(rows) == 3
    assert all(r["status"] == "ok" for r in rows)
    store.close()


def test_status_filter_multi(tmp_path):
    store = _seed_store(tmp_path)
    rows = store.list_events(status="ok,error")
    assert len(rows) == 5
    assert all(r["status"] in {"ok", "error"} for r in rows)
    store.close()


# ---------------------------------------------------------------------------
# q: substring match on summary
# ---------------------------------------------------------------------------

def test_q_substring_match(tmp_path):
    store = _seed_store(tmp_path)
    rows = store.list_events(q="reading")
    assert len(rows) == 2
    assert all("reading" in r["summary"] for r in rows)
    store.close()


def test_q_no_match(tmp_path):
    store = _seed_store(tmp_path)
    rows = store.list_events(q="xyzzy")
    assert rows == []
    store.close()


# ---------------------------------------------------------------------------
# before_id: backward pagination (id < ?)
# ---------------------------------------------------------------------------

def test_before_id_paging(tmp_path):
    store = _seed_store(tmp_path)
    all_rows = store.list_events()
    pivot = all_rows[3]["id"]  # id of 4th row (0-indexed)
    rows = store.list_events(before_id=pivot)
    assert len(rows) == 3
    assert all(r["id"] < pivot for r in rows)
    store.close()


def test_before_id_ordering_asc(tmp_path):
    """Rows returned by before_id are still ordered ASC by id."""
    store = _seed_store(tmp_path)
    all_rows = store.list_events()
    pivot = all_rows[4]["id"]
    rows = store.list_events(before_id=pivot)
    assert rows == sorted(rows, key=lambda r: r["id"])
    store.close()


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------

def test_combined_kind_status(tmp_path):
    store = _seed_store(tmp_path)
    rows = store.list_events(kind="tool_start,tool_end", status="error")
    assert len(rows) == 2
    assert all(r["kind"] in {"tool_start", "tool_end"} and r["status"] == "error" for r in rows)
    store.close()


def test_combined_session_q(tmp_path):
    store = _seed_store(tmp_path)
    rows = store.list_events(session_id="s1", q="reading")
    assert len(rows) == 2
    assert all(r["session_id"] == "s1" and "reading" in r["summary"] for r in rows)
    store.close()


def test_combined_exclude_kind_and_before_id(tmp_path):
    store = _seed_store(tmp_path)
    all_rows = store.list_events()
    pivot = all_rows[4]["id"]
    rows = store.list_events(exclude_kind="message", before_id=pivot)
    # rows before pivot, excluding message kind
    expected = [r for r in all_rows if r["id"] < pivot and r["kind"] != "message"]
    assert [r["id"] for r in rows] == [r["id"] for r in expected]
    store.close()
