"""Tests for magi_agent.transport.local_turn_pump.

The pump is the behavior change: a subscriber (browser) disconnect must NOT
cancel the turn. These tests drive the async generator directly with
``asyncio`` (mirroring test_streaming_driver.py's convention).
"""

from __future__ import annotations

import asyncio
import json

from magi_agent.transport.local_turn_pump import drive_detached_local_stream
from magi_agent.transport.local_turn_store import LocalTurnStore


def _agent_frame(payload: dict) -> bytes:
    return f"event: agent\ndata: {json.dumps(payload)}\n\n".encode()


async def _source_from(frames: list[bytes], *, cancel: asyncio.Event, hold_open: asyncio.Event | None = None):
    for frame in frames:
        if cancel.is_set():
            return
        yield frame
        await asyncio.sleep(0)
    if hold_open is not None:
        # Emulate a turn that keeps running (awaiting a tool / control_request)
        # after emitting its initial frames, until cancel fires.
        while not cancel.is_set():
            await asyncio.sleep(0.01)


def test_pump_full_drain_lands_completed_record() -> None:
    async def run() -> None:
        store = LocalTurnStore()
        cancel = asyncio.Event()
        sk = "agent:main:app:general"
        frames = [
            _agent_frame({"type": "text_delta", "delta": "hello"}),
            _agent_frame({"type": "turn_result", "terminal": "completed", "turn_id": "t1"}),
            b"data: [DONE]\n\n",
        ]
        gen = drive_detached_local_stream(
            _source_from(frames, cancel=cancel),
            session_id=sk,
            turn_id="t1",
            cancel=cancel,
            store=store,
        )
        out = [chunk async for chunk in gen]
        # Subscriber saw every frame.
        joined = b"".join(out)
        assert b"hello" in joined
        assert b"[DONE]" in joined
        # And the completed record is in the store for a late refresh.
        msgs = store.completed_messages(sk)
        assert len(msgs) == 1
        assert msgs[0]["content"] == "hello"

    asyncio.run(run())


def test_pump_subscriber_disconnect_does_not_cancel_turn() -> None:
    """THE core behavior: the browser closing the SSE generator early must NOT
    stop the turn. The pump keeps running to completion and records the result."""

    async def run() -> None:
        store = LocalTurnStore()
        cancel = asyncio.Event()
        sk = "agent:main:app:general"
        # A slow source: one frame, then several more after yields.
        completed = asyncio.Event()

        async def slow_source():
            yield _agent_frame({"type": "text_delta", "delta": "part1"})
            await asyncio.sleep(0.02)
            if cancel.is_set():
                return
            yield _agent_frame({"type": "text_delta", "delta": "part2"})
            yield _agent_frame({"type": "turn_result", "terminal": "completed", "turn_id": "t1"})
            yield b"data: [DONE]\n\n"
            completed.set()

        gen = drive_detached_local_stream(
            slow_source(),
            session_id=sk,
            turn_id="t1",
            cancel=cancel,
            store=store,
        )
        # Consume exactly ONE frame then abandon the generator (browser refresh).
        first = await gen.__anext__()
        assert b"part1" in first
        await gen.aclose()  # subscriber teardown

        # The turn must NOT have been cancelled by the disconnect.
        assert not cancel.is_set()

        # Give the detached pump time to finish the turn.
        for _ in range(200):
            if completed.is_set():
                break
            await asyncio.sleep(0.01)
        assert completed.is_set()

        # Let the pump's finally run.
        for _ in range(50):
            if store.completed_messages(sk):
                break
            await asyncio.sleep(0.01)
        msgs = store.completed_messages(sk)
        assert len(msgs) == 1
        assert msgs[0]["content"] == "part1part2"

    asyncio.run(run())


def test_pump_idle_watchdog_cancels_stuck_turn() -> None:
    """A turn that emits nothing for the idle budget IS cancelled by the
    watchdog (the only sanctioned canceller)."""

    async def run() -> None:
        store = LocalTurnStore()
        cancel = asyncio.Event()
        sk = "agent:main:app:general"
        frames = [_agent_frame({"type": "text_delta", "delta": "started then stuck"})]
        gen = drive_detached_local_stream(
            _source_from(frames, cancel=cancel, hold_open=asyncio.Event()),
            session_id=sk,
            turn_id="t1",
            cancel=cancel,
            store=store,
            idle_abort_s=0.05,
            watchdog_tick_s=0.01,
        )
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
        # The watchdog fired cancel once the source went idle past the budget.
        assert cancel.is_set()
        assert any(b"started then stuck" in c for c in chunks)

    asyncio.run(run())


def test_pump_live_snapshot_readable_mid_turn() -> None:
    """While the pump runs, the store exposes the live snapshot for a refresh
    that lands mid-turn."""

    async def run() -> None:
        store = LocalTurnStore()
        cancel = asyncio.Event()
        sk = "agent:main:app:general"
        started = asyncio.Event()
        release = asyncio.Event()

        async def source():
            yield _agent_frame({"type": "text_delta", "delta": "midturn"})
            started.set()
            while not release.is_set() and not cancel.is_set():
                await asyncio.sleep(0.005)
            yield _agent_frame({"type": "turn_result", "terminal": "completed", "turn_id": "t1"})
            yield b"data: [DONE]\n\n"

        gen = drive_detached_local_stream(
            source(),
            session_id=sk,
            turn_id="t1",
            cancel=cancel,
            store=store,
        )
        first = await gen.__anext__()
        assert b"midturn" in first
        # Wait until the pump has ingested the first frame.
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(0.005)
        live = store.active_snapshot(sk)
        assert live is not None
        assert live["content"] == "midturn"
        assert live["status"] == "running"
        # Release the turn and drain.
        release.set()
        async for _ in gen:
            pass

    asyncio.run(run())


def test_pump_never_sets_cancel_on_normal_completion() -> None:
    async def run() -> None:
        store = LocalTurnStore()
        cancel = asyncio.Event()
        sk = "agent:main:app:general"
        frames = [
            _agent_frame({"type": "text_delta", "delta": "ok"}),
            b"data: [DONE]\n\n",
        ]
        gen = drive_detached_local_stream(
            _source_from(frames, cancel=cancel),
            session_id=sk,
            turn_id="t1",
            cancel=cancel,
            store=store,
        )
        async for _ in gen:
            pass
        # Normal completion does not trip the watchdog cancel.
        assert not cancel.is_set()

    asyncio.run(run())
