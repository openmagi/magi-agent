"""Contract tests for the extracted ``ActiveTurnRegistry`` module.

``ActiveTurnRegistry`` was extracted out of the retired
``runtime/runner_session_boundary.py`` into its own ADK-free module
(``runtime/active_turn_registry.py``) so the dead boundary reference stack could
be deleted without taking the one live part with it. These tests pin the
single-flight behaviour against the new import path; ``cli.engine`` is the sole
production consumer.
"""

from __future__ import annotations

import asyncio

from magi_agent.runtime.active_turn_registry import ActiveTurnRegistry


def test_try_acquire_grants_first_turn() -> None:
    registry = ActiveTurnRegistry()
    assert registry.try_acquire(session_key="s1", turn_id="t1") is True


def test_try_acquire_rejects_concurrent_turn_for_same_session() -> None:
    registry = ActiveTurnRegistry()
    assert registry.try_acquire(session_key="s1", turn_id="t1") is True
    # A second turn for the same session is rejected while the first holds the slot.
    assert registry.try_acquire(session_key="s1", turn_id="t2") is False


def test_try_acquire_allows_distinct_sessions() -> None:
    registry = ActiveTurnRegistry()
    assert registry.try_acquire(session_key="s1", turn_id="t1") is True
    assert registry.try_acquire(session_key="s2", turn_id="t2") is True


def test_release_frees_slot_for_subsequent_turn() -> None:
    registry = ActiveTurnRegistry()
    assert registry.try_acquire(session_key="s1", turn_id="t1") is True
    registry.release(session_key="s1", turn_id="t1")
    assert registry.try_acquire(session_key="s1", turn_id="t2") is True


def test_release_is_noop_for_non_owning_turn() -> None:
    registry = ActiveTurnRegistry()
    assert registry.try_acquire(session_key="s1", turn_id="t1") is True
    # A release from a turn that does not own the slot must not free it.
    registry.release(session_key="s1", turn_id="other")
    assert registry.try_acquire(session_key="s1", turn_id="t2") is False


def test_release_when_done_releases_slot_after_task_completes() -> None:
    async def _scenario() -> bool:
        registry = ActiveTurnRegistry()
        assert registry.try_acquire(session_key="s1", turn_id="t1") is True

        async def _work() -> None:
            return None

        task = asyncio.create_task(_work())
        registry.release_when_done(session_key="s1", turn_id="t1", task=task)
        await task
        # Allow the done-callback to run.
        await asyncio.sleep(0)
        return registry.try_acquire(session_key="s1", turn_id="t2")

    assert asyncio.run(_scenario()) is True


def test_single_flight_per_session() -> None:
    reg = ActiveTurnRegistry()
    assert reg.try_acquire(session_key="session-1", turn_id="turn-1") is True
    # Second concurrent turn for the same session is rejected.
    assert reg.try_acquire(session_key="session-1", turn_id="turn-2") is False
    reg.release(session_key="session-1", turn_id="turn-1")
    # Slot is free again after release.
    assert reg.try_acquire(session_key="session-1", turn_id="turn-3") is True


def test_release_with_mismatched_turn_id_is_noop() -> None:
    reg = ActiveTurnRegistry()
    assert reg.try_acquire(session_key="session-1", turn_id="turn-1") is True
    reg.release(session_key="session-1", turn_id="other-turn")
    # Holder unchanged: a new turn is still rejected.
    assert reg.try_acquire(session_key="session-1", turn_id="turn-2") is False


def test_release_when_done_frees_slot_after_task_completes() -> None:
    async def _scenario() -> bool:
        reg = ActiveTurnRegistry()
        assert reg.try_acquire(session_key="session-1", turn_id="turn-1") is True

        async def _turn() -> None:
            await asyncio.sleep(0)

        task = asyncio.create_task(_turn())
        reg.release_when_done(session_key="session-1", turn_id="turn-1", task=task)
        await task
        # Done-callbacks run via call_soon; yield once so they fire.
        await asyncio.sleep(0)
        return reg.try_acquire(session_key="session-1", turn_id="turn-2")

    assert asyncio.run(_scenario()) is True
