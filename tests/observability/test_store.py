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


# ---------------------------------------------------------------------------
# list_sessions — new enrichment fields (Task 5)
# ---------------------------------------------------------------------------

from magi_agent.observability.store import _derive_session_label, _label_from_session_id


# --- pure helper: _label_from_session_id ---

def test_label_from_session_id_numeric_suffix():
    """last segment numeric -> '{name} #{n}'"""
    assert _label_from_session_id("agent:main:app:demo:32") == "demo #32"


def test_label_from_session_id_two_segments():
    assert _label_from_session_id("session:work") == "session:work"


def test_label_from_session_id_single_segment():
    assert _label_from_session_id("s1") == "s1"


def test_label_from_session_id_numeric_only():
    assert _label_from_session_id("42") == "42"


# --- pure helper: _derive_session_label tier priority ---

def test_derive_label_tier1_prefers_summary():
    label = _derive_session_label("sid", "Fix the login bug", ["Read", "Bash"])
    assert label == "Fix the login bug"


def test_derive_label_tier1_whitespace_stripped():
    label = _derive_session_label("sid", "  trimmed  ", [])
    assert label == "trimmed"


def test_derive_label_tier1_empty_summary_falls_to_tier2():
    label = _derive_session_label("sid", "   ", ["Read", "Bash"])
    assert label == "Read, Bash"


def test_derive_label_tier1_none_summary_falls_to_tier2():
    label = _derive_session_label("sid", None, ["EditFile"])
    assert label == "EditFile"


def test_derive_label_tier2_tools_joined():
    label = _derive_session_label("sid", None, ["Read", "Bash", "Edit"])
    assert label == "Read, Bash, Edit"


def test_derive_label_tier2_caps_at_5_with_more_indicator():
    tools = ["T1", "T2", "T3", "T4", "T5", "T6", "T7"]
    label = _derive_session_label("sid", None, tools)
    assert "T1" in label
    assert "+2 more" in label
    assert "T6" not in label


def test_derive_label_tier2_no_tools_falls_to_tier3():
    label = _derive_session_label("agent:work:task:42", None, [])
    assert label == "task #42"


def test_derive_label_tier3_id_parse():
    label = _derive_session_label("agent:main:app:demo:32", None, [])
    assert label == "demo #32"


# --- list_sessions integration: new fields present ---

def test_list_sessions_new_fields_present(tmp_path):
    """All new fields are present on every row."""
    import time as _t
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(kind="turn_start", session_id="s1",
                                     summary="hello", ts=_t.time()))
    rows = store.list_sessions()
    assert len(rows) == 1
    row = rows[0]
    assert "label" in row
    assert "kind_breakdown" in row
    assert "error_count" in row
    assert "rule_check_count" in row
    store.close()


def test_list_sessions_existing_fields_unchanged(tmp_path):
    """Existing fields (id, event_count, tool_count, started_at, last_active) are present and correct."""
    import time as _t
    store = ActivityStore(tmp_path / "obs.db")
    t0 = _t.time()
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1", ts=t0))
    store.record_event(ActivityEvent(kind="tool_end",   session_id="s1", ts=t0 + 1))
    rows = store.list_sessions()
    assert len(rows) == 1
    s = rows[0]
    assert s["id"] == "s1"
    assert s["event_count"] == 2
    assert s["tool_count"] == 1
    assert abs(s["started_at"] - t0) < 0.01
    assert abs(s["last_active"] - (t0 + 1)) < 0.01
    store.close()


def test_list_sessions_label_tier1(tmp_path):
    """Tier-1: label = first turn_start summary (non-empty)."""
    import time as _t
    store = ActivityStore(tmp_path / "obs.db")
    t0 = _t.time()
    store.record_event(ActivityEvent(kind="turn_start", session_id="s1",
                                     summary="Fix the login bug", ts=t0))
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1",
                                     tool_name="Read", ts=t0 + 1))
    rows = store.list_sessions()
    s = next(r for r in rows if r["id"] == "s1")
    assert s["label"] == "Fix the login bug"
    store.close()


def test_list_sessions_label_tier1_uses_first_turn_start(tmp_path):
    """Tier-1: when multiple turn_start events exist, uses the EARLIEST one."""
    import time as _t
    store = ActivityStore(tmp_path / "obs.db")
    t0 = _t.time()
    store.record_event(ActivityEvent(kind="turn_start", session_id="s1",
                                     summary="First goal", ts=t0))
    store.record_event(ActivityEvent(kind="turn_start", session_id="s1",
                                     summary="Second goal", ts=t0 + 5))
    rows = store.list_sessions()
    s = next(r for r in rows if r["id"] == "s1")
    assert s["label"] == "First goal"
    store.close()


def test_list_sessions_label_tier2_tool_names(tmp_path):
    """Tier-2: no turn_start summary -> label = distinct tool names by first use."""
    import time as _t
    store = ActivityStore(tmp_path / "obs.db")
    t0 = _t.time()
    # turn_start with empty summary -> falls to tier2
    store.record_event(ActivityEvent(kind="turn_start", session_id="s1",
                                     summary="  ", ts=t0))
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1",
                                     tool_name="Read", ts=t0 + 1))
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1",
                                     tool_name="Bash", ts=t0 + 2))
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1",
                                     tool_name="Read", ts=t0 + 3))  # duplicate, not added
    rows = store.list_sessions()
    s = next(r for r in rows if r["id"] == "s1")
    assert s["label"] == "Read, Bash"
    store.close()


def test_list_sessions_label_tier3_session_id_parse(tmp_path):
    """Tier-3: no summary, no tools -> label parsed from session_id."""
    import time as _t
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(kind="message", session_id="agent:main:app:demo:32",
                                     ts=_t.time()))
    rows = store.list_sessions()
    s = rows[0]
    assert s["label"] == "demo #32"
    store.close()


def test_list_sessions_kind_breakdown(tmp_path):
    """kind_breakdown is a dict mapping kind -> count for the session."""
    import time as _t
    store = ActivityStore(tmp_path / "obs.db")
    t0 = _t.time()
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1", ts=t0))
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1", ts=t0 + 1))
    store.record_event(ActivityEvent(kind="tool_end",   session_id="s1", ts=t0 + 2))
    store.record_event(ActivityEvent(kind="message",    session_id="s2", ts=t0 + 3))
    rows = store.list_sessions()
    s1 = next(r for r in rows if r["id"] == "s1")
    s2 = next(r for r in rows if r["id"] == "s2")
    assert s1["kind_breakdown"] == {"tool_start": 2, "tool_end": 1}
    assert s2["kind_breakdown"] == {"message": 1}
    store.close()


def test_list_sessions_error_count(tmp_path):
    """error_count = events with kind='error' or kind='aborted'."""
    import time as _t
    store = ActivityStore(tmp_path / "obs.db")
    t0 = _t.time()
    store.record_event(ActivityEvent(kind="error",      session_id="s1", ts=t0))
    store.record_event(ActivityEvent(kind="aborted",    session_id="s1", ts=t0 + 1))
    store.record_event(ActivityEvent(kind="tool_start", status="error", session_id="s1", ts=t0 + 2))
    rows = store.list_sessions()
    s = rows[0]
    # Only kind='error'/'aborted' counted; tool_start with status='error' excluded
    assert s["error_count"] == 2
    store.close()


def test_list_sessions_rule_check_count(tmp_path):
    """rule_check_count = events with kind='rule_check'."""
    import time as _t
    store = ActivityStore(tmp_path / "obs.db")
    t0 = _t.time()
    store.record_event(ActivityEvent(kind="rule_check", session_id="s1", ts=t0))
    store.record_event(ActivityEvent(kind="rule_check", session_id="s1", ts=t0 + 1))
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1", ts=t0 + 2))
    rows = store.list_sessions()
    s = rows[0]
    assert s["rule_check_count"] == 2
    store.close()


def test_list_sessions_zero_error_and_rule_check_when_none(tmp_path):
    """Sessions with no error/rule_check events show 0 for those counts."""
    import time as _t
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1", ts=_t.time()))
    rows = store.list_sessions()
    s = rows[0]
    assert s["error_count"] == 0
    assert s["rule_check_count"] == 0
    store.close()


def test_list_sessions_tier1_tiebreak_lowest_id_wins(tmp_path):
    """Tier-1 tie-break: two turn_start events with identical ts -> lower-id row's
    summary wins (deterministic, not last-write-wins nondeterminism)."""
    import time as _t
    store = ActivityStore(tmp_path / "obs.db")
    shared_ts = _t.time()
    # Insert low-id first, then high-id — both share the same ts.
    id_low = store.record_event(ActivityEvent(
        kind="turn_start", session_id="s1",
        summary="first goal (low id)", ts=shared_ts,
    ))
    id_high = store.record_event(ActivityEvent(
        kind="turn_start", session_id="s1",
        summary="second goal (high id)", ts=shared_ts,
    ))
    # Sanity: insert order respected by SQLite autoincrement.
    assert id_low < id_high

    rows = store.list_sessions()
    s = next(r for r in rows if r["id"] == "s1")
    # The lower-id row is the stable, deterministic winner.
    assert s["label"] == "first goal (low id)"

    # Calling again must return the same label (no nondeterminism across calls).
    rows2 = store.list_sessions()
    s2 = next(r for r in rows2 if r["id"] == "s1")
    assert s2["label"] == s["label"]

    store.close()


# ---------------------------------------------------------------------------
# Task-7: kind_breakdown() — global GROUP BY kind counts
# ---------------------------------------------------------------------------

def test_kind_breakdown_empty(tmp_path):
    """kind_breakdown on an empty store returns {}."""
    store = ActivityStore(tmp_path / "obs.db")
    assert store.kind_breakdown() == {}
    store.close()


def test_kind_breakdown_counts_by_kind(tmp_path):
    """kind_breakdown returns correct per-kind counts across all sessions."""
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1"))
    store.record_event(ActivityEvent(kind="tool_end",   session_id="s1"))
    store.record_event(ActivityEvent(kind="rule_check", session_id="s2"))
    store.record_event(ActivityEvent(kind="error",      session_id="s2"))
    breakdown = store.kind_breakdown()
    assert breakdown["tool_start"] == 2
    assert breakdown["tool_end"] == 1
    assert breakdown["rule_check"] == 1
    assert breakdown["error"] == 1
    assert set(breakdown.keys()) == {"tool_start", "tool_end", "rule_check", "error"}
    store.close()


def test_kind_breakdown_closed_returns_empty(tmp_path):
    """kind_breakdown on a closed store returns {} (fail-open)."""
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(kind="tool_start"))
    store.close()
    assert store.kind_breakdown() == {}


# ---------------------------------------------------------------------------
# Task-8: has_evidence filter on list_events
# ---------------------------------------------------------------------------

def _sha256_ref() -> str:
    """Canonical valid sha256 evidenceRef fixture."""
    return "sha256:" + "a" * 64


def test_has_evidence_includes_rule_check_with_evidence_ref(tmp_path):
    """rule_check with evidenceRef present and non-empty -> included."""
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(
        kind="rule_check",
        payload={"type": "rule_check", "ruleId": "verifier:sha256:abc", "verdict": "ok",
                 "evidenceRef": _sha256_ref()},
    ))
    rows = store.list_events(has_evidence=True)
    assert len(rows) == 1
    assert rows[0]["kind"] == "rule_check"
    store.close()


def test_has_evidence_excludes_rule_check_with_empty_evidence_ref(tmp_path):
    """rule_check with evidenceRef present but empty string -> excluded."""
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(
        kind="rule_check",
        payload={"type": "rule_check", "ruleId": "verifier:sha256:abc", "verdict": "pending",
                 "evidenceRef": ""},
    ))
    rows = store.list_events(has_evidence=True)
    assert rows == []
    store.close()


def test_has_evidence_excludes_rule_check_with_no_evidence_ref_and_zero_matched(tmp_path):
    """rule_check with no evidenceRef and detail.matched_evidence=0 -> excluded."""
    store = ActivityStore(tmp_path / "obs.db")
    # dict-form detail with matched_evidence=0
    store.record_event(ActivityEvent(
        kind="rule_check",
        payload={"type": "rule_check", "ruleId": "evidence:sha256:abc", "verdict": "pending",
                 "detail": {"matched_evidence": 0}},
    ))
    rows = store.list_events(has_evidence=True)
    assert rows == []
    store.close()


def test_has_evidence_includes_rule_check_with_detail_matched_evidence_gt_zero(tmp_path):
    """rule_check with detail.matched_evidence > 0 (dict form) -> included."""
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(
        kind="rule_check",
        payload={"type": "rule_check", "ruleId": "evidence:sha256:abc", "verdict": "ok",
                 "detail": {"matched_evidence": 3}},
    ))
    rows = store.list_events(has_evidence=True)
    assert len(rows) == 1
    store.close()


def test_has_evidence_includes_rule_check_with_detail_string_matched_gt_zero(tmp_path):
    """rule_check with real project_evidence_verdict_rule_event string detail -> included.

    Actual runtime emission from project_evidence_verdict_rule_event:
    "evidence verdict state=pass: matched=3 missing=0 failures=0 enforcement=hard"
    The brief describes this as detail.matched_evidence; in the current codebase
    detail is a plain string, not a nested dict. Filter handles both forms.
    """
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(
        kind="rule_check",
        payload={"type": "rule_check", "ruleId": "evidence:sha256:abc", "verdict": "ok",
                 "detail": "evidence verdict state=pass: matched=3 missing=0 failures=0 enforcement=hard"},
    ))
    rows = store.list_events(has_evidence=True)
    assert len(rows) == 1
    store.close()


def test_has_evidence_excludes_rule_check_with_detail_string_matched_zero(tmp_path):
    """rule_check with detail string matched=0 -> excluded."""
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(
        kind="rule_check",
        payload={"type": "rule_check", "ruleId": "evidence:sha256:abc", "verdict": "pending",
                 "detail": "evidence verdict state=audit: matched=0 missing=1 failures=0 enforcement=audit"},
    ))
    rows = store.list_events(has_evidence=True)
    assert rows == []
    store.close()


def test_has_evidence_excludes_non_rule_check_even_with_evidence_ref(tmp_path):
    """Non-rule_check event is excluded even if its payload mentions evidenceRef."""
    store = ActivityStore(tmp_path / "obs.db")
    # tool_start with evidenceRef in payload — should NOT be returned
    store.record_event(ActivityEvent(
        kind="tool_start",
        payload={"evidenceRef": _sha256_ref()},
    ))
    rows = store.list_events(has_evidence=True)
    assert rows == []
    store.close()


def test_has_evidence_excludes_rule_check_with_no_payload(tmp_path):
    """rule_check with empty/absent payload -> excluded (no evidence signals)."""
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(kind="rule_check"))
    rows = store.list_events(has_evidence=True)
    assert rows == []
    store.close()


def test_has_evidence_false_does_not_change_default_behavior(tmp_path):
    """has_evidence=False (default) -> unchanged behavior, all kinds returned."""
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(kind="tool_start"))
    store.record_event(ActivityEvent(kind="rule_check", payload={"evidenceRef": _sha256_ref()}))
    store.record_event(ActivityEvent(kind="message"))
    rows_default = store.list_events()
    rows_false = store.list_events(has_evidence=False)
    assert rows_default == rows_false
    assert len(rows_false) == 3
    store.close()


def test_has_evidence_combined_with_session_id(tmp_path):
    """has_evidence=True AND session_id -> AND semantics: only matching session."""
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(
        kind="rule_check", session_id="s1",
        payload={"evidenceRef": _sha256_ref()},
    ))
    store.record_event(ActivityEvent(
        kind="rule_check", session_id="s2",
        payload={"evidenceRef": _sha256_ref()},
    ))
    rows = store.list_events(has_evidence=True, session_id="s1")
    assert len(rows) == 1
    assert rows[0]["session_id"] == "s1"
    store.close()


def test_has_evidence_no_false_positive_for_evidenceref_in_string_value(tmp_path):
    """Payload that has evidenceRef as a string value (not a key) -> excluded.

    Verifies the Python post-filter catches what LIKE alone would falsely include.
    """
    store = ActivityStore(tmp_path / "obs.db")
    # evidenceRef appears as a string VALUE, not a key — empty-string value
    store.record_event(ActivityEvent(
        kind="rule_check",
        payload={"ruleId": "verifier:sha256:abc", "note": "no evidenceRef was set", "evidenceRef": ""},
    ))
    rows = store.list_events(has_evidence=True)
    assert rows == []
    store.close()


# ---------------------------------------------------------------------------
# Task-8 review fixes
# ---------------------------------------------------------------------------

from magi_agent.observability.store import _payload_has_evidence


# Finding 1 — SQL LIMIT before Python post-filter
def test_has_evidence_limit_counts_evidence_rows_not_total_rule_check_rows(tmp_path):
    """SQL LIMIT must NOT gate the Python evidence post-filter.

    Seed 8 rule_check rows: the first 4 have no evidence, rows 5-8 have
    evidenceRef.  With limit=2, has_evidence=True should return exactly 2
    evidence rows (rows 5 and 6 by insertion order) — not 0 (which the old
    code would produce by limiting to the first 4 non-evidence rows before
    post-filtering).
    """
    store = ActivityStore(tmp_path / "obs.db")
    # 4 non-evidence rule_check rows come first
    for i in range(4):
        store.record_event(ActivityEvent(
            kind="rule_check",
            payload={"ruleId": f"r{i}", "evidenceRef": ""},
        ))
    # 4 evidence-bearing rule_check rows follow
    evidence_ids = []
    for i in range(4):
        eid = store.record_event(ActivityEvent(
            kind="rule_check",
            payload={"ruleId": f"ev{i}", "evidenceRef": _sha256_ref()},
        ))
        evidence_ids.append(eid)

    rows = store.list_events(has_evidence=True, limit=2)
    # Must get exactly 2 evidence rows (not 0 or 1)
    assert len(rows) == 2
    # Both returned rows must be evidence-bearing
    assert all(r["payload"].get("evidenceRef") == _sha256_ref() for r in rows)
    # Order is ASC by id: the two EARLIEST evidence rows (ev0 and ev1)
    assert rows[0]["id"] == evidence_ids[0]
    assert rows[1]["id"] == evidence_ids[1]
    store.close()


# Finding 2 — non-numeric matched_evidence must not raise
def test_payload_has_evidence_non_numeric_matched_evidence_returns_false():
    """dict detail with non-numeric matched_evidence must not raise; returns False."""
    # String "n/a" should not raise ValueError
    assert _payload_has_evidence({"detail": {"matched_evidence": "n/a"}}) is False
    # Nested object should not raise
    assert _payload_has_evidence({"detail": {"matched_evidence": {"count": 3}}}) is False
    # None value should not raise
    assert _payload_has_evidence({"detail": {"matched_evidence": None}}) is False


def test_payload_has_evidence_numeric_matched_evidence_still_works():
    """Numeric matched_evidence path remains correct after isinstance guard."""
    assert _payload_has_evidence({"detail": {"matched_evidence": 3}}) is True
    assert _payload_has_evidence({"detail": {"matched_evidence": 0}}) is False
    assert _payload_has_evidence({"detail": {"matched_evidence": 1.5}}) is True


# Finding 3 — empty-payload guard
def test_payload_has_evidence_empty_dict_returns_false():
    """_payload_has_evidence({}) is self-contained and returns False."""
    assert _payload_has_evidence({}) is False


def test_payload_has_evidence_none_returns_false():
    """_payload_has_evidence(None) returns False (explicit guard at top)."""
    # type: ignore[arg-type]
    assert _payload_has_evidence(None) is False  # type: ignore[arg-type]


def test_has_evidence_no_payload_row_excluded_via_store(tmp_path):
    """rule_check with no payload (stored as None) -> excluded (confirming store path)."""
    store = ActivityStore(tmp_path / "obs.db")
    store.record_event(ActivityEvent(kind="rule_check"))  # no payload
    rows = store.list_events(has_evidence=True)
    assert rows == []
    store.close()
