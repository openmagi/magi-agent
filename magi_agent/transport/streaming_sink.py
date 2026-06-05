"""SSE streaming-chat seam for tool-permission approval requests.

This module provides the public seam the SSE chat route (a later task) uses to
receive tool-permission ``control_request`` events as :class:`RuntimeEvent`
objects on an :class:`asyncio.Queue`.

The design adapts the existing :class:`~magi_agent.cli.permissions.HeadlessSink`
rather than reimplementing it.  :class:`QueueFrameWriter` satisfies the
:class:`~magi_agent.cli.permissions.FrameWriter` protocol and translates a
``ControlRequestFrame`` into a ``control`` :class:`RuntimeEvent` placed on the
caller's queue.  The factory :func:`build_streaming_prompt_sink` wires the two
together into a fully-configured :class:`HeadlessSink`.

Usage (SSE route)::

    queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    sink = build_streaming_prompt_sink(queue, turn_id=turn_id)
    rt = build_headless_runtime(prompt_sink=sink)
    # … run the turn …
    # Each tool-permission ask produces one RuntimeEvent on *queue*.
    # Send it to the client; on receipt of the response call sink.deliver().
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from magi_agent.cli.permissions import HeadlessSink, PermissionMode
from magi_agent.runtime.events import RuntimeEvent

if TYPE_CHECKING:
    pass  # nothing extra needed at runtime

__all__ = [
    "QueueFrameWriter",
    "build_streaming_prompt_sink",
]


class QueueFrameWriter:
    """FrameWriter that converts a ControlRequestFrame into a RuntimeEvent.

    Satisfies the :class:`~magi_agent.cli.permissions.FrameWriter` protocol:
    ``async def write(self, frame: object) -> None``.

    On each call the writer inspects ``frame`` for ``request_id`` and
    ``request`` attributes (the fields emitted by
    :class:`~magi_agent.cli.permissions.HeadlessSink`) and puts a
    ``RuntimeEvent(type="control", ...)`` on the injected queue.  Unknown frame
    shapes are silently dropped so the writer never raises on stale or
    malformed data.

    Parameters
    ----------
    queue:
        Destination queue.  The SSE route reads events from this queue and
        forwards them to the browser as ``control_request`` SSE frames.
    turn_id:
        Optional turn identifier attached to every emitted
        :class:`RuntimeEvent` so the client can correlate the approval
        request with the active turn.
    """

    def __init__(
        self,
        queue: asyncio.Queue[RuntimeEvent],
        *,
        turn_id: str | None = None,
    ) -> None:
        self._queue = queue
        self._turn_id = turn_id

    async def write(self, frame: object) -> None:
        """Translate *frame* into a ``control`` RuntimeEvent and enqueue it."""
        request = getattr(frame, "request", None) or {}
        payload: dict[str, object] = {
            "type": "control_request",
            "request_id": getattr(frame, "request_id", None),
            "tool_name": request.get("tool_name"),
            "arguments": request.get("arguments") or {},
            "reason": request.get("reason"),
        }
        event = RuntimeEvent(type="control", payload=payload, turn_id=self._turn_id)
        await self._queue.put(event)


def build_streaming_prompt_sink(
    queue: asyncio.Queue[RuntimeEvent],
    *,
    permission_mode: PermissionMode = "default",
    turn_id: str | None = None,
) -> HeadlessSink:
    """Build a :class:`HeadlessSink` that enqueues control-request events.

    This is the public seam the SSE route wires into
    :func:`~magi_agent.cli.wiring.build_headless_runtime` via the
    ``prompt_sink`` parameter.

    Parameters
    ----------
    queue:
        Queue to receive :class:`RuntimeEvent` objects of type ``"control"``.
    permission_mode:
        ``"default"`` | ``"acceptEdits"`` | ``"bypassPermissions"``.
        Forwarded to :class:`HeadlessSink` unchanged.
    turn_id:
        Optional turn identifier attached to emitted events.

    Returns
    -------
    HeadlessSink
        A configured sink.  Call :meth:`~HeadlessSink.deliver` with a
        :class:`~magi_agent.cli.protocol.ControlResponse` to resolve a
        pending ask; call :meth:`~HeadlessSink.close` to fail-close all
        pending asks (e.g. on stream teardown).
    """
    writer = QueueFrameWriter(queue, turn_id=turn_id)
    return HeadlessSink(writer, permission_mode=permission_mode)
