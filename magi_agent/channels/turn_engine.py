"""Engine-backed ``run_turn`` for the channel turn bridge (PR1.5).

The shared bridge (``channels.turn_bridge``) is pure: it takes an injected
``run_turn(session_key, inbound) -> str``.  THIS module provides the real one —
it drives one governed turn through the single ``run_governed_turn`` primitive
and extracts the final assistant text via the EXISTING
``collect_governed_child_turn`` helper (so we inherit the engine's event
contract instead of re-implementing event normalization).

Unlike the boundary modules, this is deliberately NOT import-clean: it is the
engine wrapper, so importing the runtime turn primitive here is expected.  It is
imported lazily by the gateway wiring so the import-clean boundary/watcher
modules never pull it in transitively.

Sync bridging
-------------
The gateway poll loop calls the bridge's ``on_inbound`` from a worker thread
(``asyncio.to_thread(poll_once)``), so ``make_engine_run_turn`` returns a sync
callable that drives the async turn to completion with ``asyncio.run`` (the
worker thread has no running event loop).
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Callable

from magi_agent.channels.turn_bridge import ChannelInbound, RunTurn
from magi_agent.runtime.child_governed_collector import collect_governed_child_turn
from magi_agent.runtime.governed_turn import run_governed_turn
from magi_agent.runtime.turn_context import TurnContext

# (ctx) -> async event stream.  Default is the real primitive; injected in tests.
StreamFactory = Callable[[TurnContext], AsyncGenerator[object, None]]


async def run_channel_turn_async(
    session_key: str,
    text: str,
    *,
    turn_id: str,
    memory_mode: str = "normal",
    stream_factory: StreamFactory = run_governed_turn,
) -> str:
    """Drive one governed turn for *text* and return the final assistant reply."""
    ctx = TurnContext(
        prompt=text,
        session_id=session_key,
        turn_id=turn_id,
        memory_mode=memory_mode,
    )
    summary, _evidence_refs, _status = await collect_governed_child_turn(
        stream_factory(ctx)
    )
    return summary


def make_engine_run_turn(
    *,
    memory_mode: str = "normal",
    stream_factory: StreamFactory = run_governed_turn,
) -> RunTurn:
    """Build the sync ``RunTurn`` the bridge expects, backed by the real engine."""
    import asyncio

    def run_turn(session_key: str, inbound: ChannelInbound) -> str:
        return asyncio.run(
            run_channel_turn_async(
                session_key,
                inbound.text,
                turn_id=uuid.uuid4().hex,
                memory_mode=memory_mode,
                stream_factory=stream_factory,
            )
        )

    return run_turn


__all__ = [
    "make_engine_run_turn",
    "run_channel_turn_async",
]
