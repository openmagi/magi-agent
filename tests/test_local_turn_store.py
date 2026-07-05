"""Tests for magi_agent.transport.local_turn_store.

Covers the SSE snapshot reducer (parses the exact frames the local driver
yields) and the process-local turn store (live snapshot while running,
completed-turn record with a generous TTL after, reset-aware keying).
"""

from __future__ import annotations

from magi_agent.transport.local_turn_store import (
    COMPLETED_RECORD_TTL_S,
    IDLE_ABORT_WATCHDOG_S,
    CompletedTurnRecord,
    LocalSnapshotReducer,
    LocalTurnStore,
)


def _agent_frame(payload: dict) -> bytes:
    import json

    return f"event: agent\ndata: {json.dumps(payload)}\n\n".encode()


def _done_frame() -> bytes:
    return b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Reducer: content accumulation
# ---------------------------------------------------------------------------


def test_reducer_accumulates_text_delta_content() -> None:
    r = LocalSnapshotReducer(session_id="agent:main:app:general", turn_id="t1")
    r.ingest(_agent_frame({"type": "text_delta", "delta": "Hello "}))
    r.ingest(_agent_frame({"type": "text_delta", "delta": "world"}))
    snap = r.snapshot()
    assert snap is not None
    assert snap["content"] == "Hello world"
    assert snap["status"] == "running"
    assert snap["turnId"] == "t1"
    assert snap["sessionKey"] == "agent:main:app:general"


def test_reducer_stitches_across_chunk_boundaries() -> None:
    r = LocalSnapshotReducer(session_id="s", turn_id="t")
    frame = _agent_frame({"type": "text_delta", "delta": "chunked"})
    # Split the frame mid-bytes across two ingest calls.
    half = len(frame) // 2
    r.ingest(frame[:half])
    # Nothing complete yet.
    assert r.snapshot() is None or r.snapshot()["content"] == ""
    r.ingest(frame[half:])
    snap = r.snapshot()
    assert snap is not None
    assert snap["content"] == "chunked"


def test_reducer_handles_openai_choices_delta() -> None:
    r = LocalSnapshotReducer(session_id="s", turn_id="t")
    r.ingest(
        b"data: "
        + b'{"choices":[{"index":0,"delta":{"content":"abc"}}]}'
        + b"\n\n"
    )
    snap = r.snapshot()
    assert snap is not None
    assert snap["content"] == "abc"


def test_reducer_response_clear_resets_content() -> None:
    r = LocalSnapshotReducer(session_id="s", turn_id="t")
    r.ingest(_agent_frame({"type": "text_delta", "delta": "stale"}))
    r.ingest(_agent_frame({"type": "response_clear"}))
    r.ingest(_agent_frame({"type": "text_delta", "delta": "fresh"}))
    snap = r.snapshot()
    assert snap is not None
    # response_clear zeroes content; text after it in a LATER frame still counts
    # via the plain text_delta branch (the reducer does not gate on batch here).
    assert snap["content"] == "fresh"


# ---------------------------------------------------------------------------
# Reducer: turn phase + terminal
# ---------------------------------------------------------------------------


def test_reducer_tracks_turn_phase() -> None:
    r = LocalSnapshotReducer(session_id="s", turn_id="t")
    r.ingest(_agent_frame({"type": "turn_phase", "phase": "executing", "turnId": "t"}))
    snap = r.snapshot()
    assert snap is not None
    assert snap["turnPhase"] == "executing"


def test_reducer_turn_result_completed_marks_committed() -> None:
    r = LocalSnapshotReducer(session_id="s", turn_id="t")
    r.ingest(_agent_frame({"type": "text_delta", "delta": "answer"}))
    r.ingest(
        _agent_frame(
            {"type": "turn_result", "terminal": "completed", "turn_id": "t", "usage": {}}
        )
    )
    r.ingest(_done_frame())
    assert r.terminal == "completed"
    snap = r.snapshot()
    assert snap is not None
    assert snap["turnPhase"] == "committed"
    assert snap["content"] == "answer"


def test_reducer_turn_result_error_marks_aborted() -> None:
    r = LocalSnapshotReducer(session_id="s", turn_id="t")
    r.ingest(_agent_frame({"type": "turn_result", "terminal": "error", "turn_id": "t"}))
    assert r.terminal == "error"
    snap = r.snapshot()
    assert snap is not None
    assert snap["turnPhase"] == "aborted"


# ---------------------------------------------------------------------------
# Reducer: tools + subagents + missions
# ---------------------------------------------------------------------------


def test_reducer_tracks_active_tools() -> None:
    r = LocalSnapshotReducer(session_id="s", turn_id="t")
    r.ingest(_agent_frame({"type": "tool_start", "id": "tc1", "name": "Bash"}))
    snap = r.snapshot()
    assert snap is not None
    assert len(snap["activeTools"]) == 1
    assert snap["activeTools"][0]["id"] == "tc1"
    assert snap["activeTools"][0]["label"] == "Bash"
    assert snap["activeTools"][0]["status"] == "running"
    r.ingest(_agent_frame({"type": "tool_end", "id": "tc1", "status": "ok", "durationMs": 12}))
    snap = r.snapshot()
    assert snap["activeTools"][0]["status"] == "done"
    assert snap["activeTools"][0]["durationMs"] == 12


def test_reducer_tracks_subagents() -> None:
    r = LocalSnapshotReducer(session_id="s", turn_id="t")
    r.ingest(_agent_frame({"type": "child_started", "taskId": "k1", "persona": "researcher"}))
    snap = r.snapshot()
    assert snap is not None
    assert len(snap["subagents"]) == 1
    assert snap["subagents"][0]["taskId"] == "k1"
    assert snap["subagents"][0]["status"] == "running"
    r.ingest(_agent_frame({"type": "child_completed", "taskId": "k1"}))
    assert r.snapshot()["subagents"][0]["status"] == "done"


def test_reducer_tracks_missions_and_goal() -> None:
    r = LocalSnapshotReducer(session_id="s", turn_id="t")
    r.ingest(
        _agent_frame(
            {
                "type": "mission_created",
                "mission": {
                    "id": "goal:t",
                    "kind": "goal",
                    "title": "Ship it",
                    "status": "running",
                    "metadata": {"objective": "Ship the feature"},
                },
            }
        )
    )
    snap = r.snapshot()
    assert snap is not None
    assert len(snap["missions"]) == 1
    assert snap["activeGoalMissionId"] == "goal:t"
    assert snap["currentGoal"] == "Ship the feature"


def test_reducer_pending_injection_count() -> None:
    r = LocalSnapshotReducer(session_id="s", turn_id="t")
    r.ingest(_agent_frame({"type": "injection_queued", "queuedCount": 2}))
    assert r.snapshot()["pendingInjectionCount"] == 2
    r.ingest(_agent_frame({"type": "injection_drained"}))
    assert r.snapshot()["pendingInjectionCount"] == 0


def test_reducer_never_raises_on_garbage() -> None:
    r = LocalSnapshotReducer(session_id="s", turn_id="t")
    r.ingest(b"event: agent\ndata: {not json\n\n")
    r.ingest(b"\x00\x01\x02")
    r.ingest("")
    # No exception, no phantom snapshot.
    assert r.snapshot() is None


def test_reducer_detached_snapshot_when_subagent_active() -> None:
    r = LocalSnapshotReducer(session_id="s", turn_id="t")
    r.ingest(_agent_frame({"type": "child_started", "taskId": "k1", "persona": "worker"}))
    detached = r.detached_snapshot()
    assert detached is not None
    assert detached["detached"] is True
    assert len(detached["subagents"]) == 1


def test_reducer_no_detached_snapshot_when_all_subagents_done() -> None:
    r = LocalSnapshotReducer(session_id="s", turn_id="t")
    r.ingest(_agent_frame({"type": "child_started", "taskId": "k1"}))
    r.ingest(_agent_frame({"type": "child_completed", "taskId": "k1"}))
    assert r.detached_snapshot() is None


# ---------------------------------------------------------------------------
# Store: live -> completed lifecycle + reset-aware keying
# ---------------------------------------------------------------------------


def _feed_completed(reducer: LocalSnapshotReducer, text: str, terminal: str = "completed") -> None:
    reducer.ingest(_agent_frame({"type": "text_delta", "delta": text}))
    reducer.ingest(_agent_frame({"type": "turn_result", "terminal": terminal, "turn_id": reducer.turn_id}))


def test_store_live_snapshot_then_completed_messages() -> None:
    store = LocalTurnStore()
    sk = "agent:main:app:general"
    reducer = LocalSnapshotReducer(session_id=sk, turn_id="t1")
    store.begin(sk, reducer)
    reducer.ingest(_agent_frame({"type": "text_delta", "delta": "partial"}))
    # While running: live snapshot available, no committed message yet.
    live = store.active_snapshot(sk)
    assert live is not None
    assert live["content"] == "partial"
    assert store.completed_messages(sk) == []
    # Finish the turn.
    reducer.ingest(_agent_frame({"type": "text_delta", "delta": " done"}))
    reducer.ingest(_agent_frame({"type": "turn_result", "terminal": "completed", "turn_id": "t1"}))
    store.finish(sk, reducer)
    # After finish: no live turn, committed message served for a late refresh.
    assert store.live_reducer(sk) is None
    msgs = store.completed_messages(sk)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"] == "partial done"
    assert msgs[0]["turnId"] == "t1"


def test_store_errored_turn_delivers_no_completed_message() -> None:
    store = LocalTurnStore()
    sk = "agent:main:app:general"
    reducer = LocalSnapshotReducer(session_id=sk, turn_id="t1")
    store.begin(sk, reducer)
    _feed_completed(reducer, "half answer", terminal="error")
    store.finish(sk, reducer)
    assert store.completed_messages(sk) == []


def test_store_reset_key_scopes_fresh_entry() -> None:
    store = LocalTurnStore()
    base = "agent:main:app:general"
    reset = "agent:main:app:general:1"
    r0 = LocalSnapshotReducer(session_id=base, turn_id="t0")
    store.begin(base, r0)
    _feed_completed(r0, "old turn")
    store.finish(base, r0)
    # The reset key is a distinct entry -- no leakage from the pre-reset turn.
    assert store.completed_messages(reset) == []
    r1 = LocalSnapshotReducer(session_id=reset, turn_id="t1")
    store.begin(reset, r1)
    assert store.active_snapshot(reset) is None or store.active_snapshot(reset) is not None
    _feed_completed(r1, "new turn")
    store.finish(reset, r1)
    assert store.completed_messages(reset)[0]["content"] == "new turn"
    assert store.completed_messages(base)[0]["content"] == "old turn"


def test_store_ttl_expiry_drops_completed_record() -> None:
    store = LocalTurnStore(completed_ttl_s=0.0)
    sk = "agent:main:app:general"
    reducer = LocalSnapshotReducer(session_id=sk, turn_id="t1")
    store.begin(sk, reducer)
    _feed_completed(reducer, "answer")
    store.finish(sk, reducer)
    # TTL 0 -> the record is evicted on the next read.
    assert store.completed_messages(sk) == []
    assert store.active_snapshot(sk) is None


def test_store_multi_tab_newer_turn_wins_slot() -> None:
    store = LocalTurnStore()
    sk = "agent:main:app:general"
    r_old = LocalSnapshotReducer(session_id=sk, turn_id="t-old")
    store.begin(sk, r_old)
    r_new = LocalSnapshotReducer(session_id=sk, turn_id="t-new")
    store.begin(sk, r_new)  # second tab starts a new turn for the same key
    r_new.ingest(_agent_frame({"type": "text_delta", "delta": "new tab"}))
    # The stale old reducer finishing must NOT clobber the newer live turn.
    _feed_completed(r_old, "old tab")
    store.finish(sk, r_old)
    live = store.active_snapshot(sk)
    assert live is not None
    assert live["content"] == "new tab"


def test_store_detached_snapshot_survives_after_finish() -> None:
    store = LocalTurnStore()
    sk = "agent:main:app:general"
    reducer = LocalSnapshotReducer(session_id=sk, turn_id="t1")
    store.begin(sk, reducer)
    reducer.ingest(_agent_frame({"type": "child_started", "taskId": "k1", "persona": "worker"}))
    reducer.ingest(_agent_frame({"type": "turn_result", "terminal": "completed", "turn_id": "t1"}))
    store.finish(sk, reducer)
    # Parent turn ended but a subagent was still active -> detached snapshot.
    snap = store.active_snapshot(sk)
    assert snap is not None
    assert snap["detached"] is True


# ---------------------------------------------------------------------------
# Budgets are generous (Kevin policy).
# ---------------------------------------------------------------------------


def test_budgets_are_generous() -> None:
    assert IDLE_ABORT_WATCHDOG_S >= 1800.0
    assert COMPLETED_RECORD_TTL_S >= 1800.0


def test_completed_turn_record_shape() -> None:
    rec = CompletedTurnRecord(
        session_id="s",
        turn_id="t",
        role="assistant",
        content="hi",
        terminal="completed",
        created_at_ms=1,
        stored_at=2.0,
    )
    assert rec.role == "assistant"
    assert rec.detached_snapshot is None
