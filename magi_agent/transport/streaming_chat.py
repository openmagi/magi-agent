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

import math
from collections.abc import Iterator, Mapping

from magi_agent.engine.contracts import EngineResult
from magi_agent.runtime.events import RuntimeEvent
# streaming_chat intentionally reuses sse's internal helpers (_json, _sanitize_agent_event)
# to stay lock-step with the SSE wire format. The redaction helpers
# (_has_private_text_marker / _redact_unbounded_public_text) are reused so the
# terminal turn_result.error is scrubbed the SAME way visible text / error events are.
from magi_agent.transport.sse import (
    _has_private_text_marker,
    _json,
    _redact_unbounded_public_text,
    _sanitize_agent_event,
)

__all__ = ["sse_frames_for", "frame_for_event", "frame_for_terminal"]


def _frame(payload_dict: dict[str, object]) -> bytes:
    """Encode one ``event: agent`` SSE frame as UTF-8 bytes."""
    return f"event: agent\ndata: {_json(payload_dict)}\n\n".encode()


def frame_for_event(event: RuntimeEvent) -> bytes | None:
    """Encode a single :class:`RuntimeEvent` into one SSE ``event: agent`` frame.

    Merges ``turn_id`` into a copy of ``event.payload``, runs the shared
    :func:`_sanitize_agent_event` sanitizer, and returns the encoded frame bytes
    — or ``None`` if the sanitizer dropped the event (e.g. ``thinking_delta``).

    This is the per-event half of :func:`sse_frames_for`, extracted so the async
    streaming driver can frame events one-at-a-time as they arrive on its queue
    (including in-stream ``control_request`` events from the prompt sink) without
    re-implementing the wire format.
    """
    # Start from the raw payload dict and run it through the sanitizer.
    payload: dict[str, object] = dict(event.payload)

    # Merge turn_id into the payload if not already present.
    if event.turn_id is not None and "turn_id" not in payload:
        payload["turn_id"] = event.turn_id

    safe = _sanitize_agent_event(payload)
    if safe is None:
        # Sanitizer decided this event should be dropped (e.g. thinking_delta).
        return None

    safe = _reconcile_missing_receipt_turn_end(safe)

    # Ensure turn_id survives sanitization (sanitizer may strip unknown keys).
    if event.turn_id is not None and "turn_id" not in safe:
        safe = {**safe, "turn_id": event.turn_id}

    return _frame(safe)


def _reconcile_missing_receipt_turn_end(
    safe: dict[str, object],
) -> dict[str, object]:
    """Treat a ``missing_runtime_receipt`` turn_end as a committed turn.

    The ADK bridge emits the final-response ``turn_end`` with ``status=committed``
    but no runtime receipt (the local OSS runner has no receipt infrastructure),
    which the event projection downgrades to ``status=aborted`` /
    ``reason="missing_runtime_receipt"``. On THIS streaming surface the authoritative
    terminal is the separate ``turn_result`` frame, so forwarding that contradictory
    ``aborted`` turn_end makes the dashboard surface a terminal error *after* it has
    already streamed a complete reply. The turn genuinely committed, so normalize it
    back to ``committed`` here (transport-surface reconciliation only — the projection
    and CLI/headless surfaces are untouched).
    """
    if (
        safe.get("type") == "turn_end"
        and safe.get("status") == "aborted"
        and safe.get("reason") == "missing_runtime_receipt"
    ):
        reconciled = {k: v for k, v in safe.items() if k != "reason"}
        reconciled["status"] = "committed"
        reconciled.setdefault("stopReason", "end_turn")
        return reconciled
    return safe


def _scrub_citations(citations: Mapping[str, object]) -> dict[str, object]:
    """Scrub the terminal ``citations`` payload for secret markers.

    This is the LOCAL dashboard surface, so ``uri`` ships unredacted by design
    (Section 8) EXCEPT when it carries a private-text marker: then the SAME
    redaction the terminal error / visible text gets applies. Only the free-text
    fields (``uri`` / ``title``) can carry a leak; structural fields
    (``sourceId`` / ``kind`` / ``trustTier`` / ``inspected`` / ``n``) are
    passed through untouched.
    """

    def _scrub_text(value: object) -> object:
        if isinstance(value, str) and value and _has_private_text_marker(value):
            return "[redacted-private]"
        return value

    scrubbed = dict(citations)
    raw_sources = scrubbed.get("sources")
    if isinstance(raw_sources, (list, tuple)):
        clean_sources: list[object] = []
        for entry in raw_sources:
            if isinstance(entry, Mapping):
                clean_entry = dict(entry)
                if "uri" in clean_entry:
                    clean_entry["uri"] = _scrub_text(clean_entry["uri"])
                if "title" in clean_entry:
                    clean_entry["title"] = _scrub_text(clean_entry["title"])
                clean_sources.append(clean_entry)
            else:
                clean_sources.append(entry)
        scrubbed["sources"] = clean_sources
    return scrubbed


def frame_for_terminal(
    terminal: EngineResult,
    *,
    citations: Mapping[str, object] | None = None,
) -> Iterator[bytes]:
    """Yield the terminal ``turn_result`` frame followed by the ``[DONE]`` sentinel.

    The ``turn_result`` frame is always emitted (never sanitized away) and guards
    non-finite floats so :func:`_json` (``allow_nan=False``) never raises
    mid-stream.

    When ``citations`` is provided (Wave 3a source-citation payload, computed by
    the caller only while ``MAGI_SOURCE_CITATION_ENABLED`` is on), it rides the
    frame under a ``citations`` key after passing through the secret-marker
    scrub. When ``citations`` is ``None`` the frame is BYTE-IDENTICAL to the
    pre-citation frame (no ``citations`` key at all, not ``null``).
    """
    # Guard non-finite floats so _json (allow_nan=False) never raises mid-stream.
    # EngineResult.cost_usd is always a float and .usage always a dict (non-Optional
    # per contracts.py), so no `is None` guard is needed — only the non-finite scrub.
    safe_cost = terminal.cost_usd if math.isfinite(terminal.cost_usd) else 0.0
    safe_usage: dict[str, object] = {
        k: (None if isinstance(v, float) and not math.isfinite(v) else v)
        for k, v in terminal.usage.items()
    }
    # Redact the terminal error the SAME way visible text / error events are: an
    # engine exception's str(exc) can leak filesystem paths/secrets. Mirror
    # _sanitize_error_event's message handling; keep None as None.
    safe_error = terminal.error
    if isinstance(safe_error, str) and safe_error:
        safe_error = (
            "[redacted-private]"
            if _has_private_text_marker(safe_error)
            else _redact_unbounded_public_text(safe_error)
        )
    turn_result: dict[str, object] = {
        "type": "turn_result",
        "terminal": terminal.terminal.value,
        "usage": safe_usage,
        "cost_usd": safe_cost,
        "error": safe_error,
        "session_id": terminal.session_id,
        "turn_id": terminal.turn_id,
    }
    if citations is not None:
        turn_result["citations"] = _scrub_citations(citations)
    yield _frame(turn_result)

    # SSE sentinel frame.
    yield b"data: [DONE]\n\n"


def sse_frames_for(
    events_iter: Iterator[RuntimeEvent],
    terminal: EngineResult,
) -> Iterator[bytes]:
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
        frame = frame_for_event(event)
        if frame is not None:
            yield frame

    yield from frame_for_terminal(terminal)
