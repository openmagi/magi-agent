"""Tests for magi_agent.transport.streaming_chat — SSE frame serializer."""
from __future__ import annotations

import json

import pytest

from magi_agent.runtime.events import RuntimeEvent
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.transport.streaming_chat import frame_for_event, sse_frames_for


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
    assert '"turn_id":"t1"' in text
    assert "turn_result" in text
    assert text.rstrip().endswith("data: [DONE]")


# ---------------------------------------------------------------------------
# Additional focused test (a): suppressed event (thinking_delta) is skipped
# ---------------------------------------------------------------------------

def test_thinking_delta_events_are_skipped(monkeypatch: pytest.MonkeyPatch):
    """Events whose sanitized payload is None (e.g. thinking_delta) must be dropped.

    Hosted/default posture: with MAGI_STREAM_THINKING OFF the CLI SSE stream drops
    thought parts entirely. Pin the flag OFF so the local-serve overlay default
    (which enables streaming thinking on the user's own trusted machine) can't
    leak into this process and make the guard falsely fail; the ON path is
    covered by the dedicated thinking_delta streaming tests.
    """
    monkeypatch.delenv("MAGI_STREAM_THINKING", raising=False)
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


# ---------------------------------------------------------------------------
# Additional focused test (c): empty event stream yields only terminal + DONE
# ---------------------------------------------------------------------------

def test_no_events_yields_only_terminal_and_done():
    """An empty event stream must produce exactly one agent frame (turn_result) + [DONE]."""
    events: list[RuntimeEvent] = []
    terminal = EngineResult(terminal=Terminal.completed)
    frames = list(sse_frames_for(iter(events), terminal))
    text = b"".join(frames).decode()

    assert text.count("event: agent") == 1
    assert '"type":"turn_result"' in text
    assert text.rstrip().endswith("data: [DONE]")


# ---------------------------------------------------------------------------
# Additional focused test (d): non-finite floats in terminal do not crash
# ---------------------------------------------------------------------------

def test_non_finite_usage_and_cost_do_not_crash():
    """Non-finite cost_usd/usage floats must be sanitized, not raise ValueError."""
    terminal = EngineResult(
        terminal=Terminal.completed,
        usage={"input_tokens": float("inf"), "output_tokens": 5},
        cost_usd=float("nan"),
    )
    frames = list(sse_frames_for(iter([]), terminal))
    text = b"".join(frames).decode()

    assert text.rstrip().endswith("data: [DONE]")

    turn_result_line = next(
        (line for line in text.splitlines() if "turn_result" in line and line.startswith("data:")),
        None,
    )
    assert turn_result_line is not None, "No turn_result data line found"

    payload = json.loads(turn_result_line[len("data:"):].strip())
    assert payload["cost_usd"] == 0.0
    assert payload["usage"]["input_tokens"] is None
    assert payload["usage"]["output_tokens"] == 5


# ---------------------------------------------------------------------------
# Additional focused test (e): terminal error is redacted before the wire
# ---------------------------------------------------------------------------

def test_terminal_error_is_redacted():
    """A terminal error carrying a filesystem path must be scrubbed, not leaked.

    An engine exception's ``str(exc)`` can embed secrets / filesystem paths; the
    terminal ``turn_result.error`` must be redacted the same way visible text and
    error events are.
    """
    secret_path = "/home/ocuser/.openclaw/secret.key"
    terminal = EngineResult(
        terminal=Terminal.error,
        error=f"boom at {secret_path}",
        session_id="s-err",
        turn_id="t-err",
    )
    frames = list(sse_frames_for(iter([]), terminal))
    text = b"".join(frames).decode()

    # The literal secret path must NOT appear anywhere in the serialized stream.
    assert secret_path not in text

    turn_result_line = next(
        (line for line in text.splitlines() if "turn_result" in line and line.startswith("data:")),
        None,
    )
    assert turn_result_line is not None, "No turn_result data line found"

    payload = json.loads(turn_result_line[len("data:"):].strip())
    assert payload["type"] == "turn_result"
    # The error is redacted to a path/private marker, never the raw path.
    assert payload["error"] is not None
    assert secret_path not in payload["error"]
    assert "[redacted-path]" in payload["error"] or "[redacted-private]" in payload["error"]


def test_terminal_error_none_stays_none():
    """A None terminal error must remain None (no spurious redaction)."""
    terminal = EngineResult(terminal=Terminal.completed, error=None)
    frames = list(sse_frames_for(iter([]), terminal))
    text = b"".join(frames).decode()

    turn_result_line = next(
        (line for line in text.splitlines() if "turn_result" in line and line.startswith("data:")),
        None,
    )
    assert turn_result_line is not None
    payload = json.loads(turn_result_line[len("data:"):].strip())
    assert payload["error"] is None


# ---------------------------------------------------------------------------
# missing_runtime_receipt reconciliation (streaming surface)
# ---------------------------------------------------------------------------

def _decode_frame(frame: bytes | None) -> dict:
    assert frame is not None
    line = next(
        line for line in frame.decode().splitlines() if line.startswith("data:")
    )
    return json.loads(line[len("data:"):].strip())


def test_missing_runtime_receipt_turn_end_is_reconciled_to_committed():
    """A committed-without-receipt turn_end (projected to aborted/missing_runtime_receipt)
    must be normalized back to committed on the streaming surface so the client does
    not surface a terminal error after a fully streamed reply."""
    event = RuntimeEvent(
        type="status",
        payload={
            "type": "turn_end",
            "status": "aborted",
            "reason": "missing_runtime_receipt",
            "turnId": "t1",
        },
        turn_id="t1",
    )
    payload = _decode_frame(frame_for_event(event))
    assert payload["type"] == "turn_end"
    assert payload["status"] == "committed"
    assert "reason" not in payload
    assert payload.get("stopReason") == "end_turn"


def test_genuine_aborted_turn_end_is_not_reconciled():
    """A real abort (non missing_runtime_receipt reason) must stay aborted."""
    event = RuntimeEvent(
        type="status",
        payload={
            "type": "turn_end",
            "status": "aborted",
            "reason": "safety",
            "turnId": "t1",
        },
        turn_id="t1",
    )
    payload = _decode_frame(frame_for_event(event))
    assert payload["status"] == "aborted"
    assert payload["reason"] == "safety"


def test_committed_turn_end_passes_through_unchanged():
    """An already-committed turn_end must not be altered."""
    event = RuntimeEvent(
        type="status",
        payload={
            "type": "turn_end",
            "status": "committed",
            "stopReason": "end_turn",
            "turnId": "t1",
        },
        turn_id="t1",
    )
    payload = _decode_frame(frame_for_event(event))
    assert payload["status"] == "committed"
