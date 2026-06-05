"""SSE frame serializer for a stream of RuntimeEvents + a terminal EngineResult.

Converts the engine's raw event stream into Server-Sent-Events byte frames
ready to be written directly to an HTTP response body. This module is a pure
serializer — no FastAPI, no asyncio, no network I/O.

Wire format produced:

  For each RuntimeEvent (that survives sanitization):
    event: agent
    data: <json>
    <blank line>

  After all events, one terminal frame:
    event: agent
    data: {"type":"turn_result","terminal":"<value>","usage":{...},...}
    <blank line>

  Then the sentinel:
    data: [DONE]
    <blank line>

The sanitizer ``_sanitize_agent_event`` from ``magi_agent.transport.sse`` is
reused unchanged to enforce the same public-surface privacy rules that the
existing SSE writer uses.
"""

from __future__ import annotations

import json
from collections.abc import Generator, Iterator

from magi_agent.cli.contracts import EngineResult
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.transport.sse import _sanitize_agent_event

__all__ = ["sse_frames_for"]


def _frame(payload_dict: dict[str, object]) -> bytes:
    """Encode one ``event: agent`` SSE frame as UTF-8 bytes."""
    data = json.dumps(payload_dict, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return f"event: agent\ndata: {data}\n\n".encode()


def sse_frames_for(
    events_iter: Iterator[RuntimeEvent],
    terminal: EngineResult,
) -> Generator[bytes, None, None]:
    """Yield SSE byte frames for every event then a terminal turn_result frame.

    Args:
        events_iter: Iterator of :class:`~magi_agent.runtime.events.RuntimeEvent`
            objects emitted by the engine during a single turn.
        terminal: The :class:`~magi_agent.cli.contracts.EngineResult` that
            describes how the turn finished.

    Yields:
        UTF-8 encoded SSE frame bytes.  One chunk per logical frame:
        - zero or more ``event: agent`` frames (one per non-dropped event),
        - one ``event: agent`` turn_result frame,
        - one ``data: [DONE]`` frame.
    """
    for event in events_iter:
        # Start from the raw payload dict and run it through the sanitizer.
        payload: dict[str, object] = dict(event.payload)

        # Merge turn_id into the payload if not already present.
        if event.turn_id is not None and "turn_id" not in payload:
            payload["turn_id"] = event.turn_id

        safe = _sanitize_agent_event(payload)
        if safe is None:
            # Sanitizer decided this event should be dropped (e.g. thinking_delta).
            continue

        # Ensure turn_id survives sanitization (sanitizer may strip unknown keys).
        if event.turn_id is not None and "turn_id" not in safe:
            safe = {**safe, "turn_id": event.turn_id}

        yield _frame(safe)

    # Terminal frame — always emitted, never sanitized.
    turn_result: dict[str, object] = {
        "type": "turn_result",
        "terminal": terminal.terminal.value,
        "usage": terminal.usage,
        "cost_usd": terminal.cost_usd,
        "error": terminal.error,
        "session_id": terminal.session_id,
        "turn_id": terminal.turn_id,
    }
    yield _frame(turn_result)

    # SSE sentinel frame.
    yield b"data: [DONE]\n\n"
