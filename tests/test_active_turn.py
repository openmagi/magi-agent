"""Tests for magi_agent.transport.active_turn — the in-flight turn registry.

These guard the registry's ``register`` / ``get`` / ``unregister`` contract, in
particular the ``turn_id`` guard on ``unregister`` that prevents evicting a NEWER
turn that has already replaced an older one under the same session.
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.transport.active_turn import (
    ActiveTurn,
    ActiveTurnClaim,
    ActiveTurnExists,
    ActiveTurnTable,
)


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


# ---------------------------------------------------------------------------
# B-1 — claim semantics keyed by (session_id, turn_id)
# ---------------------------------------------------------------------------
def test_try_register_returns_claim_and_does_not_clobber_duplicate() -> None:
    """Two registers for the SAME (session_id, turn_id) must not overwrite."""
    table = ActiveTurnTable()
    first = _make_turn("sess-dup", "turn-1")
    claim = table.try_register(first)
    assert isinstance(claim, ActiveTurnClaim)
    assert claim.session_id == "sess-dup"
    assert claim.turn_id == "turn-1"
    assert claim.owner_id  # a fresh, non-empty owner id

    # A second register for the same (session, turn) is refused.
    second = _make_turn("sess-dup", "turn-1")
    assert table.try_register(second) is None
    # The ORIGINAL turn is still the stored one (no last-writer-wins clobber).
    assert table.get("sess-dup", "turn-1") is first


def test_two_turn_ids_one_session_both_registrable_and_ambiguous() -> None:
    table = ActiveTurnTable()
    t1 = _make_turn("sess-multi", "turn-a")
    t2 = _make_turn("sess-multi", "turn-b")
    assert table.try_register(t1) is not None
    assert table.try_register(t2) is not None

    assert table.get("sess-multi", "turn-a") is t1
    assert table.get("sess-multi", "turn-b") is t2
    # Session-only resolution is ambiguous when two turns are live.
    assert table.get_single("sess-multi") == "ambiguous"


def test_get_single_returns_lone_turn_or_none() -> None:
    table = ActiveTurnTable()
    assert table.get_single("nobody") is None
    turn = _make_turn("sess-one", "turn-1")
    table.try_register(turn)
    assert table.get_single("sess-one") is turn


def test_register_compat_shim_raises_on_duplicate() -> None:
    table = ActiveTurnTable()
    turn = _make_turn("sess-shim", "turn-1")
    table.register(turn)
    with pytest.raises(ActiveTurnExists):
        table.register(_make_turn("sess-shim", "turn-1"))
    # The original survives.
    assert table.get("sess-shim", "turn-1") is turn


def test_stale_claim_unregister_does_not_remove_newer_turn() -> None:
    """A stale owner releasing must not evict a newer turn under a different id."""
    table = ActiveTurnTable()
    older = _make_turn("sess-stale", "turn-1")
    older_claim = table.try_register(older)
    assert older_claim is not None
    # Older finishes and a newer turn registers under a DIFFERENT turn_id.
    table.unregister(older_claim)
    newer = _make_turn("sess-stale", "turn-2")
    table.try_register(newer)
    # Re-running the stale release must be a no-op (different turn_id).
    table.unregister(older_claim)
    assert table.get("sess-stale", "turn-2") is newer


def test_unregister_with_mismatched_owner_is_noop() -> None:
    """A claim whose owner_id does not match the stored turn must not evict."""
    table = ActiveTurnTable()
    turn = _make_turn("sess-owner", "turn-1")
    table.try_register(turn)
    forged = ActiveTurnClaim(
        session_id="sess-owner", turn_id="turn-1", owner_id="not-the-real-owner"
    )
    table.unregister(forged)
    assert table.get("sess-owner", "turn-1") is turn


def test_unregister_tuple_form_still_supported() -> None:
    """Back-compat: (session_id, turn_id) positional unregister still works."""
    table = ActiveTurnTable()
    turn = _make_turn("sess-tuple", "turn-1")
    table.try_register(turn)
    table.unregister("sess-tuple", "turn-1")
    assert table.get("sess-tuple", "turn-1") is None


def test_table_is_keyed_by_two_tuple() -> None:
    """Regression guard: the table must NOT collapse to session-only keying."""
    table = ActiveTurnTable()
    table.try_register(_make_turn("sess-key", "turn-1"))
    table.try_register(_make_turn("sess-key", "turn-2"))
    # Two distinct turns under one session => two entries (not 1).
    assert len(table._turns) == 2
    # And the keys are 2-tuples.
    assert all(isinstance(k, tuple) and len(k) == 2 for k in table._turns)
