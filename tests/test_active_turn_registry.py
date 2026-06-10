"""Unit tests for the extracted ActiveTurnRegistry.

The registry was previously defined inside ``runtime.runner_session_boundary``
(retired); ``cli.engine`` is its sole production consumer.
"""

from __future__ import annotations

import asyncio

from magi_agent.runtime.active_turn_registry import ActiveTurnRegistry


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
