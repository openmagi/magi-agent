"""PR-3 (guarded universal empty-text terminal fallback).

When a turn ends with ONLY unsigned reasoning parts and NO answer text was
emitted anywhere in the turn, the model answered inside reasoning_content and
the standard projection would emit an EMPTY final answer (the incident:
text_len=0 with a clean committed turn). This fallback promotes the unsigned
thought text through the normal flush_final_text path so a real text_delta /
AssistantTextEntry / model.message.completed are produced and segments stay
coherent.

Guards (all must hold): is_final_response, at least one unsigned thought part
with text, NO signed thought part (excludes Anthropic; GPT-5.5 has no raw CoT),
no non-thought text this event, no tool part, and NO text_delta emitted at any
earlier point in the turn. Genuine thinking models keep their CoT private.
"""
from __future__ import annotations

from google.adk.events import Event
from google.genai import types

from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge

_TS = 1_779_200_000


def _thought_event(
    texts: list[str],
    *,
    turn_id: str,
    partial: bool,
    turn_complete: bool = False,
    signed: bool = False,
    extra_text: str | None = None,
    timestamp: int = _TS,
) -> Event:
    parts: list[types.Part] = []
    for t in texts:
        kwargs: dict = {"text": t, "thought": True}
        if signed:
            kwargs["thought_signature"] = b"sig"
        parts.append(types.Part(**kwargs))
    if extra_text is not None:
        parts.append(types.Part(text=extra_text))
    return Event(
        id=f"evt-{turn_id}-{timestamp}",
        author="model",
        content=types.Content(role="model", parts=parts),
        partial=partial,
        turn_complete=turn_complete,
        invocation_id=turn_id,
        timestamp=timestamp,
    )


def _agent_types(projection) -> list[str]:
    return [e.get("type") for e in projection.agent_events]


def test_reasoning_only_final_is_promoted_to_text() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    turn_id = "promote-turn"
    projection = bridge.project_adk_event(
        _thought_event(
            ["기", "순", "이", "익", ": 100"],
            turn_id=turn_id,
            partial=False,
            turn_complete=True,
        ),
        turn_id=turn_id,
    )
    types_seen = _agent_types(projection)
    # Exactly one promoted text_delta with the ""-joined thought text.
    text_deltas = [e for e in projection.agent_events if e.get("type") == "text_delta"]
    assert len(text_deltas) == 1
    assert text_deltas[0]["delta"] == "기순이익: 100"
    # The promotion marker fired.
    assert "reasoning_promoted" in types_seen
    # Standard transcript + normalized outputs are produced.
    assert [entry.kind for entry in projection.transcript_entries] == ["assistant_text"]
    assert projection.transcript_entries[0].text == "기순이익: 100"
    completed = [e for e in projection.normalized_events if e.type == "model.message.completed"]
    assert len(completed) == 1
    # Turn still ends committed.
    turn_ends = [e for e in projection.agent_events if e.get("type") == "turn_end"]
    assert turn_ends and turn_ends[0]["status"] == "committed"


def test_signed_final_thought_is_not_promoted() -> None:
    """Anthropic's final thinking part is signed: CoT must NOT be promoted."""
    bridge = OpenMagiEventBridge(live_compatible=True)
    turn_id = "signed-turn"
    projection = bridge.project_adk_event(
        _thought_event(
            ["secret chain of thought"],
            turn_id=turn_id,
            partial=False,
            turn_complete=True,
            signed=True,
        ),
        turn_id=turn_id,
    )
    assert "reasoning_promoted" not in _agent_types(projection)
    assert not [e for e in projection.agent_events if e.get("type") == "text_delta"]
    # No assistant_text transcript entry from the private CoT.
    assert [entry.kind for entry in projection.transcript_entries] == []


def test_no_promotion_when_text_streamed_earlier_in_turn() -> None:
    """If a real answer text_delta was emitted earlier, a trailing unsigned
    thought-only final must NOT be promoted (turn_text_emitted guard)."""
    bridge = OpenMagiEventBridge(live_compatible=True)
    turn_id = "mixed-turn"
    # Earlier partial text.
    bridge.project_adk_event(
        Event(
            id="evt-a",
            author="model",
            content=types.Content(role="model", parts=[types.Part(text="Answer.")]),
            partial=True,
            invocation_id=turn_id,
            timestamp=_TS,
        ),
        turn_id=turn_id,
    )
    # Final event carries only unsigned thought.
    projection = bridge.project_adk_event(
        _thought_event(
            ["afterthought reasoning"],
            turn_id=turn_id,
            partial=False,
            turn_complete=True,
            timestamp=_TS + 1,
        ),
        turn_id=turn_id,
    )
    assert "reasoning_promoted" not in _agent_types(projection)
    # The thought is not promoted into an assistant_text entry.
    assert "assistant_text" not in [e.kind for e in projection.transcript_entries]


def test_normal_mixed_final_is_unchanged() -> None:
    """A final event with both thought and real text parts: the real text is
    emitted normally and NO reasoning_promoted marker fires."""
    bridge = OpenMagiEventBridge(live_compatible=True)
    turn_id = "normal-turn"
    projection = bridge.project_adk_event(
        _thought_event(
            ["reasoning"],
            turn_id=turn_id,
            partial=False,
            turn_complete=True,
            extra_text="The real answer.",
        ),
        turn_id=turn_id,
    )
    assert "reasoning_promoted" not in _agent_types(projection)
    text_deltas = [e for e in projection.agent_events if e.get("type") == "text_delta"]
    assert len(text_deltas) == 1
    assert text_deltas[0]["delta"] == "The real answer."


def test_empty_thought_text_does_not_promote() -> None:
    """An unsigned thought part with no text is not a promotable answer."""
    bridge = OpenMagiEventBridge(live_compatible=True)
    turn_id = "empty-thought-turn"
    projection = bridge.project_adk_event(
        _thought_event(
            [""],
            turn_id=turn_id,
            partial=False,
            turn_complete=True,
        ),
        turn_id=turn_id,
    )
    assert "reasoning_promoted" not in _agent_types(projection)
    assert not [e for e in projection.agent_events if e.get("type") == "text_delta"]


def test_integration_shaped_kimi_turn_yields_nonempty_final_text() -> None:
    """Incident-shaped: 2 partial thinking deltas then a reasoning-only final
    (Korean per-char) with empty content. The turn must end with a non-empty
    final answer equal to the final step's reasoning, and thinking deltas must
    stay on their own channel."""
    import os

    os.environ.pop("MAGI_STREAM_THINKING", None)
    bridge = OpenMagiEventBridge(live_compatible=True)
    turn_id = "kimi-incident"
    # Streamed partial reasoning (thinking channel; may be gated off).
    bridge.project_adk_event(
        _thought_event(["재"], turn_id=turn_id, partial=True, timestamp=_TS),
        turn_id=turn_id,
    )
    bridge.project_adk_event(
        _thought_event(["무"], turn_id=turn_id, partial=True, timestamp=_TS + 1),
        turn_id=turn_id,
    )
    # Reasoning-only final aggregate (empty content, all thought).
    final = bridge.project_adk_event(
        _thought_event(
            ["재무", "상태: ", "양호"],
            turn_id=turn_id,
            partial=False,
            turn_complete=True,
            timestamp=_TS + 2,
        ),
        turn_id=turn_id,
    )
    text_deltas = [e for e in final.agent_events if e.get("type") == "text_delta"]
    assert len(text_deltas) == 1
    assert text_deltas[0]["delta"] == "재무상태: 양호"
    assert "reasoning_promoted" in _agent_types(final)
