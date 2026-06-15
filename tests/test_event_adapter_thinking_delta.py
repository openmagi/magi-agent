"""Thought parts must surface as thinking_delta events, not be dropped.

ADK marks model reasoning (Claude/Gemini extended thinking) as parts with
``thought=True``. The bridge previously dropped them entirely, so hosted chat
never received a thinking stream and the collapsible thinking block stayed
empty regardless of MAGI_STREAM_THINKING. They must be projected as
``thinking_delta`` so sse.py (gated by the flag) can forward them.
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


def test_partial_thought_part_projects_thinking_delta() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = _model_event(
        parts=[types.Part(text="Let me reason about this.", thought=True)],
        partial=True,
    )
    projection = bridge.project_adk_event(event, turn_id="turn-think")
    thinking = [e for e in projection.agent_events if e.get("type") == "thinking_delta"]
    assert len(thinking) == 1
    assert thinking[0]["delta"].strip() != ""
    # A thought part is NOT also leaked as visible answer text.
    assert not any(e.get("type") == "text_delta" for e in projection.agent_events)


def test_partial_non_thought_text_still_projects_text_delta() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = _model_event(
        parts=[types.Part(text="The answer is 42.")],
        partial=True,
    )
    projection = bridge.project_adk_event(event, turn_id="turn-think")
    text = [e for e in projection.agent_events if e.get("type") == "text_delta"]
    assert len(text) == 1
    assert not any(e.get("type") == "thinking_delta" for e in projection.agent_events)


def test_mixed_thought_and_text_parts_split_channels() -> None:
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
