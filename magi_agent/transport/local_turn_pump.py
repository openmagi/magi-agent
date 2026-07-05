"""Detached background pump for the LOCAL streaming-chat branch.

Problem this solves
-------------------
``drive_streaming_chat`` yields SSE bytes straight into a Starlette
``StreamingResponse``. Starlette ties the async generator's lifetime to the
browser socket: when the tab refreshes (or the web client's idle watchdog aborts
the ``fetch``), the generator is closed, and ``drive_streaming_chat``'s
``finally`` block calls ``cancel.set()`` -> the turn is killed. Hosted does not
have this problem because the chat-proxy keeps the runtime turn running
independently of the browser socket.

Fix (LOCAL branch only)
-----------------------
Run ``drive_streaming_chat`` inside a detached background task (the *pump*). The
pump owns the turn and:

  * fans every SSE frame to a per-subscriber queue AND to a
    :class:`LocalSnapshotReducer` (the live snapshot the refresh endpoint reads);
  * survives subscriber disconnect -- when the browser goes away the subscriber
    generator just stops draining; the pump keeps running the turn;
  * is only ever cancelled by an idle-abort watchdog (a genuinely stuck turn
    that emits no frames for :data:`IDLE_ABORT_WATCHDOG_S`), never by disconnect;
  * on completion, records a completed-turn record in :data:`LOCAL_TURN_STORE`
    so a late refresh rehydrates the delivered text via ``channel-messages``.

The subscriber generator (returned to the ``StreamingResponse``) reads from the
per-subscriber queue and, critically, does NOT cancel the pump in its own
``finally`` -- that is the whole behavior change.

The hosted gate5b branches never call into this module; they are byte-identical.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from magi_agent.transport.local_turn_store import (
    IDLE_ABORT_WATCHDOG_S,
    LOCAL_TURN_STORE,
    LocalSnapshotReducer,
    LocalTurnStore,
)

__all__ = ["drive_detached_local_stream"]

# Sentinel put on a subscriber queue when the pump is done (no more frames).
_SUB_END = object()

# How often the watchdog wakes to check idle time. Small relative to the idle
# budget so a stuck turn is torn down within a bounded window past the budget.
_WATCHDOG_TICK_S = 5.0


async def drive_detached_local_stream(
    frame_source: AsyncIterator[bytes],
    *,
    session_id: str,
    turn_id: str | None,
    cancel: asyncio.Event,
    store: LocalTurnStore | None = None,
    idle_abort_s: float = IDLE_ABORT_WATCHDOG_S,
    watchdog_tick_s: float = _WATCHDOG_TICK_S,
) -> AsyncIterator[bytes]:
    """Wrap *frame_source* (a ``drive_streaming_chat`` byte stream) in a detached
    pump and return the SUBSCRIBER byte stream for the HTTP response.

    Parameters
    ----------
    frame_source:
        The undetached SSE byte generator (``drive_streaming_chat(...)``). The
        pump consumes it exactly once.
    session_id:
        Reset-aware session key. Keys the live/completed entry in *store* so the
        refresh endpoints find this turn.
    turn_id:
        Turn id for the snapshot record.
    cancel:
        The SAME ``asyncio.Event`` passed into ``drive_streaming_chat``. The
        watchdog ``set()``s it to cooperatively stop a stuck turn; the subscriber
        generator NEVER sets it on disconnect (the behavior change).
    store:
        Turn store (defaults to the process singleton). Injectable for tests.
    idle_abort_s / watchdog_tick_s:
        Watchdog budget + poll interval (injectable for tests).
    """
    turn_store = store if store is not None else LOCAL_TURN_STORE
    reducer = LocalSnapshotReducer(session_id=session_id, turn_id=turn_id)
    turn_store.begin(session_id, reducer)

    # Per-subscriber queue. A single subscriber (this HTTP response) at a time;
    # a second tab that opens a NEW turn gets its own pump. Multiple GET pollers
    # read the snapshot from the store, not this queue.
    sub_queue: asyncio.Queue[object] = asyncio.Queue()

    async def _pump() -> None:
        try:
            async for frame in frame_source:
                reducer.ingest(frame)
                # Best-effort fan-out to the current subscriber. If the queue
                # backpressures we still never block the turn on the browser.
                sub_queue.put_nowait(frame)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a pump fault must not wedge the store
            pass
        finally:
            turn_store.finish(session_id, reducer)
            with contextlib.suppress(Exception):
                sub_queue.put_nowait(_SUB_END)

    pump_task = asyncio.ensure_future(_pump())

    async def _watchdog() -> None:
        # Only cancels a turn that produces NO frames for the whole idle budget.
        # Browser disconnect does not affect the reducer's idle clock (the pump
        # keeps ingesting frames), so a live-but-detached turn is never killed.
        try:
            while not pump_task.done():
                await asyncio.sleep(watchdog_tick_s)
                if pump_task.done():
                    return
                if reducer.idle_seconds() >= idle_abort_s:
                    cancel.set()
                    return
        except asyncio.CancelledError:
            raise

    watchdog_task = asyncio.ensure_future(_watchdog())

    try:
        while True:
            item = await sub_queue.get()
            if item is _SUB_END:
                break
            if isinstance(item, (bytes, bytearray)):
                yield bytes(item)
    finally:
        # THE BEHAVIOR CHANGE: subscriber teardown (browser disconnect / refresh)
        # does NOT cancel the pump. The turn keeps running detached; the next
        # mount rehydrates from the store. We only stop the watchdog's own task
        # bookkeeping here (its cancel authority over the turn is untouched -- it
        # already decided based on idle time, not on this generator closing).
        if not watchdog_task.done():
            watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await watchdog_task
