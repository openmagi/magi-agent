"""Tests for ChannelMessageStore (U1 backend store).

Design test coverage per section 10 of the design doc:
1. append then list returns the row with seq assigned and fields intact.
2. idempotency: same (session_id, message_id) twice appends once; second call
   returns None.
3. after_seq returns only newer rows (exclusive boundary).
4. limit returns the TAIL (latest N) in ascending order.
5. session isolation: two session_ids never see each other's rows.
6. flag OFF: channel_message_store_for returns None; corrupted db path fails
   soft to None (no raise).
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from magi_agent.storage.channel_message_store import (
    ChannelMessageStore,
    _reset_channel_message_store_singletons_for_tests,
    channel_message_store_for,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> ChannelMessageStore:
    """A fresh ChannelMessageStore backed by a temp directory."""
    return ChannelMessageStore(workspace_root=tmp_path)


@pytest.fixture(autouse=True)
def _clear_registry():
    """Reset the process-level singleton registry before and after each test."""
    _reset_channel_message_store_singletons_for_tests()
    yield
    _reset_channel_message_store_singletons_for_tests()


# ---------------------------------------------------------------------------
# Test 1: append then list returns the row with seq assigned and fields intact
# ---------------------------------------------------------------------------


def test_append_then_list_returns_row_with_fields_intact(store: ChannelMessageStore) -> None:
    seq = store.append_message_sync(
        message_id="msg-1",
        session_id="sess-a",
        role="user",
        content="Hello world",
        app_name="myapp",
        channel="general",
        turn_id="turn-1",
        created_at_ms=1760000000000,
        incomplete=False,
        terminal=None,
    )
    assert seq is not None
    assert seq >= 1

    rows = store.list_messages_sync(session_id="sess-a", app_name="myapp")
    assert len(rows) == 1
    row = rows[0]
    assert row["seq"] == seq
    assert row["message_id"] == "msg-1"
    assert row["session_id"] == "sess-a"
    assert row["role"] == "user"
    assert row["content"] == "Hello world"
    assert row["app_name"] == "myapp"
    assert row["channel"] == "general"
    assert row["turn_id"] == "turn-1"
    assert row["created_at"] == 1760000000000
    assert row["incomplete"] is False
    assert row["terminal"] is None


# ---------------------------------------------------------------------------
# Test 2: idempotency: same (session_id, message_id) appends once
# ---------------------------------------------------------------------------


def test_idempotent_append_same_message_id(store: ChannelMessageStore) -> None:
    seq1 = store.append_message_sync(
        message_id="dup-msg",
        session_id="sess-b",
        role="assistant",
        content="First write",
    )
    seq2 = store.append_message_sync(
        message_id="dup-msg",
        session_id="sess-b",
        role="assistant",
        content="Second write; should be ignored",
    )

    assert seq1 is not None
    assert seq2 is None  # deduped

    rows = store.list_messages_sync(session_id="sess-b")
    assert len(rows) == 1
    assert rows[0]["content"] == "First write"


# ---------------------------------------------------------------------------
# Test 3: after_seq is exclusive; returns only newer rows
# ---------------------------------------------------------------------------


def test_after_seq_exclusive_boundary(store: ChannelMessageStore) -> None:
    seqs = []
    for i in range(5):
        s = store.append_message_sync(
            message_id=f"msg-{i}",
            session_id="sess-c",
            role="user",
            content=f"turn {i}",
        )
        assert s is not None
        seqs.append(s)

    # after_seq = seqs[1] should return rows for seqs[2], seqs[3], seqs[4]
    pivot = seqs[1]
    rows = store.list_messages_sync(session_id="sess-c", after_seq=pivot)
    returned_seqs = [r["seq"] for r in rows]
    assert all(s > pivot for s in returned_seqs)
    assert len(rows) == 3
    # ascending order
    assert returned_seqs == sorted(returned_seqs)


# ---------------------------------------------------------------------------
# Test 4: limit returns the TAIL (latest N) in ascending order
# ---------------------------------------------------------------------------


def test_limit_returns_tail_in_ascending_order(store: ChannelMessageStore) -> None:
    for i in range(10):
        store.append_message_sync(
            message_id=f"lim-{i}",
            session_id="sess-d",
            role="user",
            content=f"msg {i}",
        )

    rows = store.list_messages_sync(session_id="sess-d", limit=3)
    assert len(rows) == 3
    # Should be the LAST 3 messages in ascending seq order
    all_rows = store.list_messages_sync(session_id="sess-d")
    expected = all_rows[-3:]
    assert [r["seq"] for r in rows] == [r["seq"] for r in expected]
    # Ascending order
    assert rows[0]["seq"] < rows[1]["seq"] < rows[2]["seq"]


# ---------------------------------------------------------------------------
# Test 5: session isolation: two session_ids never see each other's rows
# ---------------------------------------------------------------------------


def test_session_isolation(store: ChannelMessageStore) -> None:
    store.append_message_sync(
        message_id="iso-1", session_id="sess-x", role="user", content="from X"
    )
    store.append_message_sync(
        message_id="iso-2", session_id="sess-y", role="user", content="from Y"
    )

    rows_x = store.list_messages_sync(session_id="sess-x")
    rows_y = store.list_messages_sync(session_id="sess-y")

    assert len(rows_x) == 1
    assert rows_x[0]["content"] == "from X"
    assert len(rows_y) == 1
    assert rows_y[0]["content"] == "from Y"

    # Double-check: X cannot see Y's message_id
    msg_ids_x = {r["message_id"] for r in rows_x}
    msg_ids_y = {r["message_id"] for r in rows_y}
    assert msg_ids_x.isdisjoint(msg_ids_y)


# ---------------------------------------------------------------------------
# Test 6a: flag OFF: channel_message_store_for returns None
# ---------------------------------------------------------------------------


def test_channel_message_store_for_returns_none_when_flag_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_LOCAL_CHANNEL_HISTORY_ENABLED", "0")
    result = channel_message_store_for(tmp_path)
    assert result is None


# ---------------------------------------------------------------------------
# Test 6b: corrupted/unwritable db path fails soft to None (no raise)
# ---------------------------------------------------------------------------


def test_channel_message_store_for_fails_soft_on_bad_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_LOCAL_CHANNEL_HISTORY_ENABLED", "1")
    # Create a FILE where the db directory should be so mkdir fails
    blocker = tmp_path / ".openmagi"
    blocker.write_text("not a directory", encoding="utf-8")

    # Must not raise; must return None
    result = channel_message_store_for(tmp_path)
    assert result is None


# ---------------------------------------------------------------------------
# Test 6c: flag ON: channel_message_store_for returns a store
# ---------------------------------------------------------------------------


def test_channel_message_store_for_returns_store_when_flag_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_LOCAL_CHANNEL_HISTORY_ENABLED", "1")
    store = channel_message_store_for(tmp_path)
    assert store is not None
    assert isinstance(store, ChannelMessageStore)


# ---------------------------------------------------------------------------
# Test 6d: singleton: same workspace_root returns the same instance
# ---------------------------------------------------------------------------


def test_channel_message_store_for_singleton_same_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_LOCAL_CHANNEL_HISTORY_ENABLED", "1")
    s1 = channel_message_store_for(tmp_path)
    s2 = channel_message_store_for(tmp_path)
    assert s1 is s2


# ---------------------------------------------------------------------------
# Bonus: incomplete + terminal fields round-trip
# ---------------------------------------------------------------------------


def test_incomplete_and_terminal_fields_round_trip(store: ChannelMessageStore) -> None:
    store.append_message_sync(
        message_id="err-msg",
        session_id="sess-err",
        role="assistant",
        content="partial answer",
        incomplete=True,
        terminal="runner_error",
    )

    rows = store.list_messages_sync(session_id="sess-err")
    assert len(rows) == 1
    assert rows[0]["incomplete"] is True
    assert rows[0]["terminal"] == "runner_error"


# ---------------------------------------------------------------------------
# Bonus: concurrent writers (two threads, WAL + busy_timeout)
# ---------------------------------------------------------------------------


def test_concurrent_writers_do_not_lose_rows(tmp_path: Path) -> None:
    """Two threads appending to the same session must not lose rows.

    This exercises WAL + busy_timeout and mirrors design test 16's core
    assertion (the full multi-window proof lives in U3 once the endpoint is
    wired).
    """
    store = ChannelMessageStore(workspace_root=tmp_path)
    N = 20
    errors: list[Exception] = []

    def writer(prefix: str) -> None:
        for i in range(N):
            try:
                store.append_message_sync(
                    message_id=f"{prefix}-{i}",
                    session_id="shared-sess",
                    role="user",
                    content=f"{prefix} turn {i}",
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    t1 = threading.Thread(target=writer, args=("A",))
    t2 = threading.Thread(target=writer, args=("B",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Concurrent writer errors: {errors}"

    rows = store.list_messages_sync(session_id="shared-sess")
    # All 2*N unique messages must be present (dedup by distinct message_ids)
    message_ids = {r["message_id"] for r in rows}
    for prefix in ("A", "B"):
        for i in range(N):
            assert f"{prefix}-{i}" in message_ids, f"Missing {prefix}-{i}"
    # Rows are in ascending seq order
    seqs = [r["seq"] for r in rows]
    assert seqs == sorted(seqs)
