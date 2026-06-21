"""Tests for the serve session-end transcript buffer (runtime/active_sessions)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi_agent.runtime import active_sessions
from magi_agent.runtime.active_sessions import (
    _buffered_session_count,
    _reset_for_test,
    drain_and_extract,
    note_turn,
)

_ENABLE = "MAGI_MEMORY_SESSION_EXTRACT_ENABLED"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    for name in (_ENABLE, "MAGI_MEMORY_WRITE_ENABLED", "MAGI_MEMORY_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    _reset_for_test()
    yield
    _reset_for_test()


def test_note_turn_noop_when_gate_off() -> None:
    note_turn(session_id="s1", workspace_root="/tmp", user_text="hi", assistant_text="yo")
    assert _buffered_session_count() == 0


def test_note_turn_buffers_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENABLE, "1")
    note_turn(session_id="s1", workspace_root="/tmp", user_text="hi", assistant_text="yo")
    note_turn(session_id="s1", workspace_root="/tmp", user_text="more", assistant_text="ok")
    note_turn(session_id="s2", workspace_root="/tmp", user_text="x", assistant_text="y")
    assert _buffered_session_count() == 2


def test_note_turn_suppresses_incognito(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENABLE, "1")
    note_turn(
        session_id="s1",
        workspace_root="/tmp",
        user_text="secret",
        assistant_text="ok",
        memory_mode="incognito",
    )
    assert _buffered_session_count() == 0


def test_drain_clears_and_calls_extract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(_ENABLE, "1")
    seen: list[tuple[str, int]] = []

    async def _fake_extract(messages, *, workspace_root, model=None):  # noqa: ANN001
        seen.append((str(workspace_root), len(messages)))
        return object()

    monkeypatch.setattr(active_sessions, "run_session_extract", _fake_extract, raising=False)
    # Patch the lazily-imported symbol used inside drain via module attribute.
    import magi_agent.runtime.session_extract_runtime as sx

    monkeypatch.setattr(sx, "run_session_extract", _fake_extract, raising=True)

    note_turn(session_id="s1", workspace_root=str(tmp_path), user_text="a", assistant_text="b")
    note_turn(session_id="s2", workspace_root=str(tmp_path), user_text="c", assistant_text="d")

    drained = asyncio.run(drain_and_extract())
    assert drained == 2
    assert _buffered_session_count() == 0  # buffers cleared
    assert sorted(n for _, n in seen) == [2, 2]


def test_drain_empty_is_zero() -> None:
    assert asyncio.run(drain_and_extract()) == 0
