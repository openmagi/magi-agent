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

import os
import uuid
from collections.abc import AsyncGenerator, Callable

from magi_agent.channels.turn_bridge import ChannelInbound, RunTurn
from magi_agent.runtime.child_governed_collector import collect_governed_child_turn

# PR-H: main-turn finalize-path TRACE stamp (gated on the existing
# MAGI_CHILD_RUNNER_EMPTY_DEBUG env). Logs once after the channel turn's
# stream consumption loop finishes so the operator can confirm the loop
# actually reached a terminal vs ended silently with no result text.
from magi_agent.runtime.child_runner_live import (
    _maybe_log_trace_turn_engine_stream_consumed,
)
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
    # PR-H: stamp the end of the channel turn's stream-consumption loop.
    # ``items`` here is a length proxy (summary chars) -- the consumer
    # collapses RuntimeEvent text deltas into ``summary`` before returning,
    # so we cannot report event count without re-instrumenting the
    # collector. Status doubles as ``terminal_kind`` (``completed`` /
    # ``failed`` / raised exception name).
    terminal_kind: object = None
    items = 0
    exception_cls: type | None = None
    try:
        # The missing-tool streak guard is child-runner-only; this caller does
        # not opt in (cap defaults to 0), so trip_reason is always None here.
        summary, _evidence_refs, status, _trip = await collect_governed_child_turn(
            stream_factory(ctx)
        )
        items = len(summary)
        terminal_kind = status
    except Exception as exc:  # noqa: BLE001 - re-raised; captured for trace.
        exception_cls = exc.__class__
        raise
    finally:
        _maybe_log_trace_turn_engine_stream_consumed(
            os.environ,
            turn_id=turn_id,
            items=items,
            terminal_kind=(exception_cls.__name__ if exception_cls is not None else terminal_kind),
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
