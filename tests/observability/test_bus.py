from __future__ import annotations

import asyncio

from magi_agent.observability.bus import ActivityBus


def test_subscriber_receives_live_event():
    async def run():
        bus = ActivityBus(replay=10)
        sub = bus.subscribe(channel="*")
        await bus.publish({"kind": "tool_start", "session_id": "s1"})
        ev = await asyncio.wait_for(sub.__anext__(), timeout=1.0)
        assert ev["kind"] == "tool_start"
        await sub.aclose()
    asyncio.run(run())


def test_replay_buffer_delivers_recent_on_subscribe():
    async def run():
        bus = ActivityBus(replay=10)
        await bus.publish({"kind": "a", "session_id": "s1"})
        await bus.publish({"kind": "b", "session_id": "s1"})
        sub = bus.subscribe(channel="*")
        first = await asyncio.wait_for(sub.__anext__(), timeout=1.0)
        second = await asyncio.wait_for(sub.__anext__(), timeout=1.0)
        assert [first["kind"], second["kind"]] == ["a", "b"]
        await sub.aclose()
    asyncio.run(run())


def test_channel_filtering_by_session():
    async def run():
        bus = ActivityBus(replay=10)
        sub = bus.subscribe(channel="s1")
        await bus.publish({"kind": "x", "session_id": "s2"})
        await bus.publish({"kind": "y", "session_id": "s1"})
        ev = await asyncio.wait_for(sub.__anext__(), timeout=1.0)
        assert ev["kind"] == "y"
        await sub.aclose()
    asyncio.run(run())
