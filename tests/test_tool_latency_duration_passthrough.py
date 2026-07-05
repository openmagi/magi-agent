"""Regression tests for the dashboard "every tool latency shows 0ms" bug.

Two projection paths dropped real tool duration:

1. The local (non-hosted) ADK event bridge hardcoded ``durationMs: 0`` on the
   ``tool_end`` agent event and never threaded a real wall-clock duration.
2. History replay (``transcript_entries_to_agent_events``) hardcoded
   ``durationMs: 0`` because ``ToolResultEntry`` carried no duration field.

The fix threads the real duration through and OMITS the key when it is genuinely
unknown (a response with no correlated call-side start time), rather than
reporting a misleading ``0``.
"""

from __future__ import annotations

from google.adk.events import Event
from google.genai import types

from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge
from magi_agent.runtime.events import transcript_entries_to_agent_events
from magi_agent.runtime.transcript import ToolCallEntry, ToolResultEntry


def _call_event(event_id: str, tool_id: str, name: str) -> Event:
    return Event(
        id=event_id,
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id=tool_id,
                        name=name,
                        args={"query": "adk"},
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )


def _response_event(event_id: str, tool_id: str, name: str) -> Event:
    return Event(
        id=event_id,
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=tool_id,
                        name=name,
                        response={"results": ["alpha"]},
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )


def test_local_tool_end_threads_real_duration_when_call_seen_first() -> None:
    bridge = OpenMagiEventBridge()
    bridge.project_adk_event(_call_event("event-1", "tool-1", "Search"), turn_id="turn-1")
    projection = bridge.project_adk_event(
        _response_event("event-2", "tool-1", "Search"), turn_id="turn-1"
    )

    tool_end = projection.agent_events[0]
    assert tool_end["type"] == "tool_end"
    # Real (non-hardcoded) wall-clock duration is present and a non-negative int.
    assert "durationMs" in tool_end
    assert isinstance(tool_end["durationMs"], int)
    assert tool_end["durationMs"] >= 0

    # The transcript entry (used for history replay) also carries the duration.
    result_entry = projection.transcript_entries[0]
    assert result_entry.kind == "tool_result"
    assert result_entry.duration_ms is not None
    assert result_entry.duration_ms >= 0


def test_local_tool_end_omits_duration_when_start_unknown() -> None:
    # A response with no correlated call-side start time: duration is genuinely
    # unknown, so the key is OMITTED rather than reported as a misleading 0.
    bridge = OpenMagiEventBridge()
    projection = bridge.project_adk_event(
        _response_event("event-2", "tool-orphan", "Search"), turn_id="turn-1"
    )
    tool_end = projection.agent_events[0]
    assert tool_end["type"] == "tool_end"
    assert "durationMs" not in tool_end
    assert projection.transcript_entries[0].duration_ms is None


def test_history_replay_emits_real_duration_from_transcript_entry() -> None:
    entries = [
        ToolCallEntry(
            ts=1.0,
            turnId="turn-1",
            toolUseId="tool-1",
            name="Search",
            input={"query": "adk"},
        ),
        ToolResultEntry(
            ts=2.0,
            turnId="turn-1",
            toolUseId="tool-1",
            status="ok",
            output='{"ok": true}',
            durationMs=1234,
        ),
    ]
    events = transcript_entries_to_agent_events(entries)
    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert tool_end["durationMs"] == 1234


def test_history_replay_omits_duration_when_transcript_entry_has_none() -> None:
    entries = [
        ToolResultEntry(
            ts=2.0,
            turnId="turn-1",
            toolUseId="tool-1",
            status="ok",
            output='{"ok": true}',
        ),
    ]
    events = transcript_entries_to_agent_events(entries)
    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert "durationMs" not in tool_end
