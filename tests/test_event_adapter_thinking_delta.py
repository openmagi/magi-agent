"""Thought parts surface as thinking_delta — gated by MAGI_STREAM_THINKING.

ADK marks model reasoning (Claude/Gemini extended thinking, LiteLLM
reasoning_content e.g. Kimi) as parts with ``thought=True``. With
MAGI_STREAM_THINKING enabled the bridge projects them as ``thinking_delta`` so
the hosted UI can render the collapsible thinking block. With the flag off the
projection layer stays a hard privacy boundary and drops them entirely.
"""

from google.adk.events import Event
from google.genai import types

from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge


def _model_event(*, parts, partial, idx=0):
    return Event(
        id=f"ev-{idx}",
        author="model",
        partial=partial,
        content=types.Content(role="model", parts=parts),
        invocation_id="turn-think",
    )


def test_partial_thought_part_projects_thinking_delta_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = _model_event(
        parts=[types.Part(text="Let me reason about this.", thought=True)],
        partial=True,
    )
    projection = bridge.project_adk_event(event, turn_id="turn-think")
    thinking = [e for e in projection.agent_events if e.get("type") == "thinking_delta"]
    assert len(thinking) == 1
    assert thinking[0]["delta"].strip() != ""
    assert not any(e.get("type") == "text_delta" for e in projection.agent_events)


def test_thought_part_dropped_when_flag_off(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAM_THINKING", "0")
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = _model_event(
        parts=[types.Part(text="PRIVATE_THOUGHT secret", thought=True)],
        partial=True,
    )
    projection = bridge.project_adk_event(event, turn_id="turn-think")
    assert projection.agent_events == []


def test_partial_non_thought_text_still_projects_text_delta(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = _model_event(parts=[types.Part(text="The answer is 42.")], partial=True)
    projection = bridge.project_adk_event(event, turn_id="turn-think")
    text = [e for e in projection.agent_events if e.get("type") == "text_delta"]
    assert len(text) == 1
    assert not any(e.get("type") == "thinking_delta" for e in projection.agent_events)


def test_mixed_thought_and_text_parts_split_channels(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = _model_event(
        parts=[
            types.Part(text="internal reasoning", thought=True),
            types.Part(text="visible answer"),
        ],
        partial=True,
    )
    projection = bridge.project_adk_event(event, turn_id="turn-think")
    kinds = [(e.get("type"), e.get("delta")) for e in projection.agent_events]
    assert ("thinking_delta", "internal reasoning") in kinds
    assert ("text_delta", "visible answer") in kinds
