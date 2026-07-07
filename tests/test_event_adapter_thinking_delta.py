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


def test_thought_after_text_in_same_event_is_suppressed(monkeypatch) -> None:
    # Reasoning-model interleaving artifact: once the ANSWER text has begun, a
    # later thought part in the same partial event belongs BEFORE the answer and
    # must not be surfaced as a thinking_delta (it would ladder the answer into
    # per-token fragments with Thought blocks between). Parts arrive as
    # [text, thought]: the text sets the turn answer latch, so the trailing
    # thought yields NO thinking_delta.
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = _model_event(
        parts=[
            types.Part(text="visible answer"),
            types.Part(text="interleaved reasoning", thought=True),
        ],
        partial=True,
    )
    projection = bridge.project_adk_event(event, turn_id="turn-think")
    kinds = [e.get("type") for e in projection.agent_events]
    assert "text_delta" in kinds
    assert "thinking_delta" not in kinds


def test_thought_after_text_across_events_in_turn_is_suppressed(monkeypatch) -> None:
    # The answer latch is TURN-scoped: an answer text_delta in an earlier partial
    # event suppresses thinking_delta for a thought part in a LATER partial event
    # of the same turn.
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    bridge = OpenMagiEventBridge(live_compatible=True)
    text_event = _model_event(parts=[types.Part(text="the answer")], partial=True, idx=0)
    text_projection = bridge.project_adk_event(text_event, turn_id="turn-think")
    assert any(e.get("type") == "text_delta" for e in text_projection.agent_events)

    thought_event = _model_event(
        parts=[types.Part(text="post-answer reasoning", thought=True)],
        partial=True,
        idx=1,
    )
    thought_projection = bridge.project_adk_event(thought_event, turn_id="turn-think")
    assert not any(
        e.get("type") == "thinking_delta" for e in thought_projection.agent_events
    )


def test_thought_before_any_text_still_projects_thinking_delta(monkeypatch) -> None:
    # Pre-answer thinking is preserved: a thought part that arrives before any
    # answer text_delta in the turn still surfaces on the thinking channel.
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = _model_event(
        parts=[types.Part(text="reasoning before the answer", thought=True)],
        partial=True,
    )
    projection = bridge.project_adk_event(event, turn_id="turn-think")
    assert any(e.get("type") == "thinking_delta" for e in projection.agent_events)
