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

Durable channel history (F1b)
-----------------------------
Optionally the pump also writes the durable :class:`ChannelMessageStore` at the
turn seams (user row at pump start, assistant row at pump finish) so multi-window
history sync survives a process restart. The store is resolved via a caller-
supplied ``store_accessor`` closure (keeps this module import-light); every store
call is fail-soft so a store fault never breaks the stream. When the durable
params are omitted the pump is byte-identical to the pre-F1b behavior.

Both the LOCAL streaming branch and the local-serve governed gate5b branch route
through this module (F1). The gate5b branch supplies a ``cancel`` event that the
gate5b driver's heartbeat loop honors, so the idle-abort watchdog can stop a
genuinely stuck gate5b turn just as it does for the LOCAL branch.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Callable

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
    user_message: str | None = None,
    channel: str | None = None,
    store_accessor: Callable[[], object | None] | None = None,
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
    user_message / channel / store_accessor:
        F1b durable channel-history params (all default None = byte-identical to
        the pre-F1b pump). ``store_accessor`` is a zero-arg closure returning a
        :class:`~magi_agent.storage.channel_message_store.ChannelMessageStore`
        (or None); it is called lazily so the pump stays import-light. When a
        store is available and ``user_message`` is non-empty, a ``role=user`` row
        is appended at pump start (crash durability); at pump finish, a
        ``role=assistant`` row is appended from ``reducer.content`` (skipped when
        empty), flagged ``incomplete`` with the terminal reason on error/aborted.
        Every store call is fail-soft: a store fault never breaks the stream.
    """
    turn_store = store if store is not None else LOCAL_TURN_STORE
    reducer = LocalSnapshotReducer(session_id=session_id, turn_id=turn_id)
    turn_store.begin(session_id, reducer)

    # F1b: resolve the durable channel-message store once (fail-soft). None when
    # no accessor was supplied, the flag is OFF, or resolution raised.
    _channel_store: object | None = None
    if store_accessor is not None:
        try:
            _channel_store = store_accessor()
        except Exception:  # noqa: BLE001 -- a store fault must never break the stream
            _channel_store = None

    # Persist the user message NOW (before consuming frames) so even a hard-crash
    # turn leaves a durable user row. Best-effort; never raises.
    if _channel_store is not None and user_message:
        try:
            await _channel_store.append_message(  # type: ignore[attr-defined]
                message_id=uuid.uuid4().hex,
                session_id=session_id,
                role="user",
                content=user_message,
                channel=channel or "",
                turn_id=turn_id,
            )
        except Exception:  # noqa: BLE001, S110 -- best-effort; never break the turn
            pass

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
            # F1b: persist the assistant message at turn end (best-effort). Runs
            # after ``finish`` so the in-memory snapshot is settled. Skipped when
            # the assistant produced no text (empty turns deliver nothing,
            # mirroring the existing honesty rule).
            _assistant_content = reducer.content
            _reducer_terminal = reducer.terminal
            if _channel_store is not None and _assistant_content:
                _incomplete = _reducer_terminal in ("error", "aborted")
                try:
                    await _channel_store.append_message(  # type: ignore[attr-defined]
                        message_id=uuid.uuid4().hex,
                        session_id=session_id,
                        role="assistant",
                        content=_assistant_content,
                        channel=channel or "",
                        turn_id=turn_id,
                        incomplete=_incomplete,
                        terminal=_reducer_terminal if _incomplete else None,
                    )
                except Exception:  # noqa: BLE001, S110 -- best-effort; never break the turn
                    pass
            # F3: emit the assembled assistant message to the session transcript.
            # Lazy import + fail-open; a no-op when the transcript sink is inactive
            # (MAGI_SESSION_TRANSCRIPT_ENABLED default-OFF). Honors the
            # "assembled message record carries the final body" contract.
            if _assistant_content:
                try:
                    from magi_agent.observability.transcript import (  # noqa: PLC0415
                        emit_transcript_record,
                    )

                    emit_transcript_record(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": _assistant_content,
                            "terminal": _reducer_terminal or "completed",
                        },
                        session_id,
                        turn_id,
                    )
                except Exception:  # noqa: BLE001, S110 -- transcript must never break a turn
                    pass
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
