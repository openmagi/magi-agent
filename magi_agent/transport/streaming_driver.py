"""Async driver that turns one agent turn into a live SSE byte stream.

This is the concurrency-critical core of the streaming-chat surface. It runs the
engine's :meth:`run_turn_stream` in a dedicated PRODUCER task whose output (and
any in-stream tool-permission ``control_request`` events emitted by the prompt
sink) funnel through a single :class:`asyncio.Queue`; the CONSUMER (this async
generator's body) drains that queue and frames each item as SSE bytes.

Why a separate producer task
----------------------------
When a tool fires under ``permission_mode="default"``, ``run_turn_stream`` is
internally *awaiting the gate decision*. Meanwhile the gate's sink (via
``QueueFrameWriter``) has ALREADY put a ``control_request`` ``RuntimeEvent`` on
the SAME queue. The consumer must be free to drain and emit that
``control_request`` to the client so the client can answer (a sibling route
calls ``sink.deliver(...)``). If we iterated the engine generator INLINE in the
consumer, the consumer would be blocked inside the engine awaiting the gate while
the gate awaits the client — a deadlock. The producer/consumer split breaks it.

Termination guarantee
---------------------
The stream ALWAYS ends with a ``turn_result`` frame followed by ``data: [DONE]``
— on the happy path, on producer error, and on cancellation — so the client
never hangs. The producer's ``finally`` always enqueues the ``_END`` sentinel,
and the consumer always emits the terminal frames after seeing it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.transport.active_turn import ActiveTurn, ActiveTurnTable
from magi_agent.transport.streaming_chat import frame_for_event, frame_for_terminal

if TYPE_CHECKING:
    from magi_agent.cli.permissions import HeadlessSink

__all__ = ["drive_streaming_chat"]


# Module-level sentinel marking "producer is done; no more queue items".
# A unique object() so it can never collide with a real RuntimeEvent.
_END = object()


async def drive_streaming_chat(
    engine: object,
    gate: object,
    turn_input: dict,
    *,
    cancel: asyncio.Event,
    queue: "asyncio.Queue[object]",
    sink: "HeadlessSink",
    registry: ActiveTurnTable,
    session_id: str,
    turn_id: str,
) -> AsyncIterator[bytes]:
    """Drive one turn and yield its SSE byte frames, interleaving control events.

    Parameters
    ----------
    engine:
        Object exposing ``run_turn_stream(runtime, turn_input, *, cancel, gate)``
        as an async generator (the final yielded item is an ``EngineResult``).
    gate:
        The permission gate threaded into the engine (may be ``None``).
    turn_input:
        Turn input dict (``{"prompt", "session_id", "turn_id", ...}``).
    cancel:
        Cooperative-cancel event shared with the engine and the interrupt route.
    queue:
        The shared queue. The engine's events flow here via the producer task;
        in-stream ``control_request`` events flow here via the prompt sink's
        ``QueueFrameWriter``.
    sink:
        The streaming prompt sink, stored on the registry so the control-response
        route can ``sink.deliver(...)`` a parked ask.
    registry:
        The :class:`ActiveTurnTable` to register this turn in (and remove from on
        teardown).
    session_id, turn_id:
        Identity of this turn.

    Yields
    ------
    bytes
        SSE frame chunks: zero or more ``event: agent`` frames, then a
        ``turn_result`` frame, then ``data: [DONE]``.
    """
    registry.register(
        ActiveTurn(
            session_id=session_id,
            turn_id=turn_id,
            cancel=cancel,
            sink=sink,
        )
    )

    # Mutable holder so the producer task can hand the terminal back to the
    # consumer (an async generator cannot ``return`` a value).
    terminal_holder: dict[str, EngineResult] = {}

    async def _produce() -> None:
        try:
            async for item in engine.run_turn_stream(  # type: ignore[attr-defined]
                None,
                turn_input,
                cancel=cancel,
                gate=gate,
            ):
                if isinstance(item, EngineResult):
                    # Terminal — store it; do NOT enqueue (the consumer reads it
                    # from the holder after _END).
                    terminal_holder["result"] = item
                else:
                    # A RuntimeEvent — engine output OR a control_request that the
                    # sink put on this same queue while the engine awaited the gate.
                    await queue.put(item)
        except asyncio.CancelledError:
            # Producer task cancelled (consumer/generator teardown). Let the
            # finally enqueue _END so the consumer (if still draining) terminates.
            raise
        except Exception as exc:  # noqa: BLE001 - surface ANY engine fault as a terminal
            terminal_holder["result"] = EngineResult(
                terminal=Terminal.error,
                error=str(exc) or exc.__class__.__name__,
                session_id=session_id,
                turn_id=turn_id,
            )
        finally:
            # ALWAYS signal end-of-stream so the consumer never hangs.
            await queue.put(_END)

    producer = asyncio.ensure_future(_produce())

    try:
        while True:
            item = await queue.get()
            if item is _END:
                break
            # Every non-sentinel item is a RuntimeEvent (engine event or a
            # control_request from the sink).
            assert isinstance(item, RuntimeEvent)
            frame = frame_for_event(item)
            if frame is not None:
                yield frame

        terminal = terminal_holder.get("result")
        if terminal is None:
            # Producer ended without a terminal (shouldn't normally happen).
            terminal = EngineResult(
                terminal=Terminal.error,
                error="no_result",
                session_id=session_id,
                turn_id=turn_id,
            )
        for chunk in frame_for_terminal(terminal):
            yield chunk
    finally:
        # If the CLIENT disconnected / the generator was closed early, request a
        # cooperative cancel so the producer stops promptly, then await it.
        cancel.set()
        producer.cancel()
        try:
            await producer
        except asyncio.CancelledError:
            pass
        registry.unregister(session_id, turn_id)
