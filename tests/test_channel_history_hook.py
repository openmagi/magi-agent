"""Tests for the U2 channel-history persistence hook in _local_adk_chat_sse.

Design test coverage per section 10 of the design doc:
12. one turn appends exactly two rows (user then assistant) with matching
    log_turn_id, in seq order.
13. replayed request with same turnId/userMessageId does not double-write
    (still 2 rows).
14. empty assistant output appends only the user row.
15. hook exception (store monkeypatched to raise) does not break the SSE
    stream (turn still completes, frames still yielded).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.engine.contracts import EngineResult, Terminal
from magi_agent.storage.channel_message_store import (
    ChannelMessageStore,
    _reset_channel_message_store_singletons_for_tests,
    channel_message_store_for,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
    "MAGI_PROVIDER",
    "MAGI_MODEL",
    "MAGI_GOAL_LOOP_ENABLED",
)


class _FakeEngine:
    """Minimal engine that yields one text delta then terminates cleanly."""

    def __init__(self, text: str = "Hello from assistant", error: str | None = None) -> None:
        self._text = text
        self._error = error

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):  # noqa: ANN001
        from magi_agent.runtime.events import RuntimeEvent

        if self._text:
            # Use "token" (a valid EventKind) with a "delta" key so that
            # _local_runtime_event_delta picks it up as assistant text.
            yield RuntimeEvent(type="token", payload={"delta": self._text})
        yield EngineResult(
            terminal=Terminal.completed,
            session_id="s",
            turn_id="t",
            error=self._error,
        )


async def _drain(gen) -> str:  # noqa: ANN001
    """Consume the async generator and return all SSE frames joined."""
    return "".join([chunk async for chunk in gen])


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(model="anthropic/claude", bot_id=None, user_id=None))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_store_registry():
    """Clear the process-level singleton registry before and after each test."""
    _reset_channel_message_store_singletons_for_tests()
    yield
    _reset_channel_message_store_singletons_for_tests()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Strip provider env keys and point workspace to a temp dir."""
    for name in _PROVIDER_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))


def _patch_runtime(monkeypatch: pytest.MonkeyPatch, engine: _FakeEngine | None = None) -> None:
    """Patch build_headless_runtime and routing helper (mirrors goal-mode tests)."""
    import magi_agent.cli.wiring as wiring

    if engine is None:
        engine = _FakeEngine()

    def _fake_build(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(engine=engine, gate=None)

    monkeypatch.setattr(wiring, "build_headless_runtime", _fake_build)
    monkeypatch.setattr(
        wiring, "local_runner_policy_routing_enabled_from_env", lambda: False
    )


# ---------------------------------------------------------------------------
# Test 12: one turn appends exactly two rows (user then assistant)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_turn_appends_user_and_assistant_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MAGI_LOCAL_CHANNEL_HISTORY_ENABLED", "1")
    _patch_runtime(monkeypatch)

    from magi_agent.transport import chat as chat_mod

    session_id = "agent:main:app:general"
    turn_id_val = "test-turn-abc"
    user_msg_id = "user-msg-abc"
    user_text = "What is the weather today?"

    payload = {
        "sessionId": session_id,
        "turnId": turn_id_val,
        "userMessageId": user_msg_id,
    }
    out = await _drain(chat_mod._local_adk_chat_sse(_runtime(), payload, user_text))
    assert out.rstrip().endswith("data: [DONE]")

    # Check the store
    store = channel_message_store_for(tmp_path)
    assert store is not None
    rows = store.list_messages_sync(session_id=session_id)

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}: {rows}"

    user_row = rows[0]
    assistant_row = rows[1]

    assert user_row["role"] == "user"
    assert user_row["content"] == user_text
    assert user_row["message_id"] == user_msg_id
    assert user_row["turn_id"] == turn_id_val
    assert user_row["session_id"] == session_id

    assert assistant_row["role"] == "assistant"
    assert assistant_row["content"] == "Hello from assistant"
    assert assistant_row["message_id"] == f"{turn_id_val}:assistant"
    assert assistant_row["turn_id"] == turn_id_val
    assert assistant_row["session_id"] == session_id

    # seq must be ascending (user before assistant)
    assert user_row["seq"] < assistant_row["seq"]

    # Both rows must share the same turn_id
    assert user_row["turn_id"] == assistant_row["turn_id"]


# ---------------------------------------------------------------------------
# Test 13: replayed request (same turnId/userMessageId) does not double-write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replayed_request_does_not_double_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MAGI_LOCAL_CHANNEL_HISTORY_ENABLED", "1")
    _patch_runtime(monkeypatch)

    from magi_agent.transport import chat as chat_mod

    session_id = "agent:main:app:replay-ch"
    payload = {
        "sessionId": session_id,
        "turnId": "replay-turn-1",
        "userMessageId": "replay-user-1",
    }

    # First send
    await _drain(chat_mod._local_adk_chat_sse(_runtime(), payload, "First send"))
    # Replay with identical ids
    await _drain(chat_mod._local_adk_chat_sse(_runtime(), payload, "First send"))

    store = channel_message_store_for(tmp_path)
    assert store is not None
    rows = store.list_messages_sync(session_id=session_id)

    # INSERT OR IGNORE means replayed ids are deduped: still exactly 2 rows
    assert len(rows) == 2, f"Expected 2 rows (not doubled), got {len(rows)}: {rows}"
    roles = [r["role"] for r in rows]
    assert roles == ["user", "assistant"]


# ---------------------------------------------------------------------------
# Test 14: empty assistant output appends only the user row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_assistant_appends_only_user_row(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MAGI_LOCAL_CHANNEL_HISTORY_ENABLED", "1")
    # Engine that yields no text delta
    _patch_runtime(monkeypatch, engine=_FakeEngine(text=""))

    from magi_agent.transport import chat as chat_mod

    session_id = "agent:main:app:empty-ch"
    payload = {
        "sessionId": session_id,
        "turnId": "empty-turn-1",
        "userMessageId": "empty-user-1",
    }
    out = await _drain(chat_mod._local_adk_chat_sse(_runtime(), payload, "Anything"))
    assert out.rstrip().endswith("data: [DONE]")

    store = channel_message_store_for(tmp_path)
    assert store is not None
    rows = store.list_messages_sync(session_id=session_id)

    # Only the user row; empty assistant content is not stored
    assert len(rows) == 1, f"Expected 1 row (user only), got {len(rows)}: {rows}"
    assert rows[0]["role"] == "user"


# ---------------------------------------------------------------------------
# Test 15: hook exception does NOT break the SSE stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_exception_does_not_break_sse_stream(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MAGI_LOCAL_CHANNEL_HISTORY_ENABLED", "1")
    _patch_runtime(monkeypatch)

    # Build a real store instance that raises on every append_message call
    class _BrokenStore(ChannelMessageStore):
        async def append_message(self, **kwargs: object) -> int | None:  # type: ignore[override]
            raise RuntimeError("intentional store fault")

        def append_message_sync(self, **kwargs: object) -> int | None:  # type: ignore[override]
            raise RuntimeError("intentional store fault")

    broken_store = _BrokenStore(workspace_root=tmp_path)

    import magi_agent.transport.chat_routes_local as local_mod

    monkeypatch.setattr(local_mod, "channel_message_store_for", lambda _: broken_store)

    from magi_agent.transport import chat as chat_mod

    session_id = "agent:main:app:fault-ch"
    payload = {
        "sessionId": session_id,
        "turnId": "fault-turn-1",
        "userMessageId": "fault-user-1",
    }
    out = await _drain(chat_mod._local_adk_chat_sse(_runtime(), payload, "Will fail silently"))

    # Stream must still complete successfully
    assert out.rstrip().endswith("data: [DONE]"), (
        f"SSE stream did not complete: last chars = {out[-100:]!r}"
    )
    # The text delta must still be present
    assert "Hello from assistant" in out
