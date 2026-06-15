"""Tests for magi_agent.transport.active_turn — the in-flight turn registry.

These guard the registry's ``register`` / ``get`` / ``unregister`` contract, in
particular the ``turn_id`` guard on ``unregister`` that prevents evicting a NEWER
turn that has already replaced an older one under the same session.
"""

from __future__ import annotations

import asyncio

from magi_agent.transport.active_turn import ActiveTurn, ActiveTurnTable


def _make_turn(session_id: str, turn_id: str) -> ActiveTurn:
    return ActiveTurn(
        session_id=session_id,
        turn_id=turn_id,
        cancel=asyncio.Event(),
        sink=object(),  # type: ignore[arg-type]  # sink is duck-typed; not exercised here
    )


def test_register_then_get_returns_turn() -> None:
    table = ActiveTurnTable()
    turn = _make_turn("sess-a", "turn-1")
    table.register(turn)
    assert table.get("sess-a") is turn


def test_unregister_matching_turn_id_removes() -> None:
    table = ActiveTurnTable()
    turn = _make_turn("sess-b", "turn-1")
    table.register(turn)

    table.unregister("sess-b", "turn-1")
    assert table.get("sess-b") is None


def test_unregister_different_turn_id_is_noop() -> None:
    """Guards the race: an older turn's teardown must NOT evict a newer turn."""
    table = ActiveTurnTable()
    turn = _make_turn("sess-c", "turn-2")
    table.register(turn)

    # An older turn tearing down (turn-1) must not remove the registered turn-2.
    table.unregister("sess-c", "turn-1")
    assert table.get("sess-c") is turn


def test_get_unknown_session_returns_none() -> None:
    table = ActiveTurnTable()
    assert table.get("no-such-session") is None


def test_task_field_defaults_to_none_and_round_trips() -> None:
    import asyncio as _asyncio

    table = ActiveTurnTable()
    # Default: no task threaded (cooperative streaming path).
    turn = _make_turn("sess-d", "turn-1")
    assert turn.task is None
    table.register(turn)
    assert table.get("sess-d").task is None

    async def _drive() -> None:
        sentinel = _asyncio.get_running_loop().create_future()

        async def _runner() -> None:
            await sentinel

        task = _asyncio.ensure_future(_runner())
        with_task = ActiveTurn(
            session_id="sess-e",
            turn_id="turn-2",
            cancel=_asyncio.Event(),
            sink=object(),  # type: ignore[arg-type]
            task=task,
        )
        table.register(with_task)
        assert table.get("sess-e").task is task
        task.cancel()
        try:
            await task
        except _asyncio.CancelledError:
            pass

    asyncio.run(_drive())


def test_unregister_turn_id_guard_holds_with_task_field() -> None:
    """The turn_id guard still protects a newer turn after the dataclass grew."""
    table = ActiveTurnTable()
    newer = ActiveTurn(
        session_id="sess-f",
        turn_id="turn-2",
        cancel=asyncio.Event(),
        sink=object(),  # type: ignore[arg-type]
        task=None,
    )
    table.register(newer)
    table.unregister("sess-f", "turn-1")  # stale teardown
    assert table.get("sess-f") is newer
