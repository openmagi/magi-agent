"""Tests for magi_agent.transport.streaming_chat — SSE frame serializer."""
from __future__ import annotations

import json

import pytest

from magi_agent.runtime.events import RuntimeEvent
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.transport.streaming_chat import sse_frames_for


# ---------------------------------------------------------------------------
# Primary acceptance test (TDD step 1)
# ---------------------------------------------------------------------------

def test_sse_frames_for_events_and_terminal():
    events = [
        RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "hi"}, turn_id="t1"),
        RuntimeEvent(type="tool", payload={"type": "tool_start", "id": "c1", "name": "Bash"}, turn_id="t1"),
    ]
    terminal = EngineResult(terminal=Terminal.completed, usage={"input_tokens": 1}, turn_id="t1", session_id="s1")
    frames = list(sse_frames_for(iter(events), terminal))
    text = b"".join(frames).decode()
    assert "event: agent\n" in text
    assert "text_delta" in text
    assert "\"turn_id\": \"t1\"" in text or "\"turn_id\":\"t1\"" in text
    assert "turn_result" in text
    assert text.rstrip().endswith("data: [DONE]")


# ---------------------------------------------------------------------------
# Additional focused test (a): suppressed event (thinking_delta) is skipped
# ---------------------------------------------------------------------------

def test_thinking_delta_events_are_skipped():
    """Events whose sanitized payload is None (e.g. thinking_delta) must be dropped."""
    events = [
        RuntimeEvent(
            type="token",
            payload={"type": "thinking_delta", "thinking": "internal chain-of-thought"},
            turn_id="t2",
        ),
        RuntimeEvent(
            type="token",
            payload={"type": "text_delta", "delta": "hello"},
            turn_id="t2",
        ),
    ]
    terminal = EngineResult(terminal=Terminal.completed, turn_id="t2")
    frames = list(sse_frames_for(iter(events), terminal))
    text = b"".join(frames).decode()

    # The thinking event must not appear
    assert "thinking_delta" not in text
    assert "internal chain-of-thought" not in text

    # The visible text_delta must still appear
    assert "text_delta" in text


# ---------------------------------------------------------------------------
# Additional focused test (b): Terminal.aborted + error field serialization
# ---------------------------------------------------------------------------

def test_aborted_terminal_with_error_serialized():
    """Terminal.aborted + error='cancelled' must appear in the turn_result frame."""
    events: list[RuntimeEvent] = []
    terminal = EngineResult(
        terminal=Terminal.aborted,
        error="cancelled",
        session_id="sess-abc",
        turn_id="t3",
    )
    frames = list(sse_frames_for(iter(events), terminal))
    text = b"".join(frames).decode()

    # Find the turn_result data line and parse it
    turn_result_line = next(
        (line for line in text.splitlines() if "turn_result" in line and line.startswith("data:")),
        None,
    )
    assert turn_result_line is not None, "No turn_result data line found"

    payload = json.loads(turn_result_line[len("data:"):].strip())
    assert payload["type"] == "turn_result"
    assert payload["terminal"] == "aborted"
    assert payload["error"] == "cancelled"
    assert payload["session_id"] == "sess-abc"
    assert payload["turn_id"] == "t3"

    # Must still end with [DONE]
    assert text.rstrip().endswith("data: [DONE]")
