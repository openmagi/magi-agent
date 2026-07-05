"""PR-3: guarded empty-text terminal promotion fallback.

A reasoning model that DEGRADES into answering inside ``reasoning_content``
(unsigned ``thought`` parts) with no ``content`` produces a clean, committed
turn whose only visible answer is empty. These tests pin the guarded fallback
that promotes such unsigned reasoning to a real final answer, and the guards
that keep genuine chain-of-thought models untouched.
"""
from __future__ import annotations

from google.adk.events import Event
from google.genai import types

from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge


def _final_event(parts: list[types.Part], *, event_id: str = "event-final") -> Event:
    return Event(
        id=event_id,
        author="model",
        content=types.Content(role="model", parts=parts),
        partial=False,
        turn_complete=True,
        invocation_id="turn-1",
    )


def _partial_thought_event(text: str) -> Event:
    return Event(
        author="model",
        content=types.Content(role="model", parts=[types.Part(text=text, thought=True)]),
        partial=True,
        invocation_id="turn-1",
    )


def _agent_types(projection) -> list[object]:
    return [event.get("type") for event in projection.agent_events]


def _text_deltas(projection) -> list[str]:
    return [
        event.get("delta")
        for event in projection.agent_events
        if event.get("type") == "text_delta"
    ]


def _normalized_types(projection) -> list[str]:
    return [event.type for event in projection.normalized_events]


def _promotion_markers(projection) -> list[dict]:
    return [
        event
        for event in projection.agent_events
        if event.get("type") == "runtime_trace"
        and event.get("reasonCode") == "reasoning_promoted_to_final"
    ]


def test_reasoning_only_final_turn_promotes_unsigned_thought_to_final_text() -> None:
    # Multi-part unsigned reasoning, no prior text_delta anywhere in the turn.
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = _final_event(
        [
            types.Part(text="당기순이익: ", thought=True),
            types.Part(text="424,129", thought=True),
        ]
    )

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    # Promoted answer is the "" (verbatim, NO newline) join of the thought texts.
    assert _text_deltas(projection) == ["당기순이익: 424,129"]
    # It flowed through the standard final-text path: transcript + completed.
    assistant = [
        entry for entry in projection.transcript_entries if entry.kind == "assistant_text"
    ]
    assert len(assistant) == 1
    assert assistant[0].text == "당기순이익: 424,129"
    assert "model.message.completed" in _normalized_types(projection)
    # The named audit marker fired exactly once.
    assert _normalized_types(projection).count("model.reasoning_promoted_to_final") == 1
    # Operator-visible warning marker fired exactly once.
    markers = _promotion_markers(projection)
    assert len(markers) == 1
    assert markers[0]["severity"] == "warning"
    # Turn still ends committed (live path).
    turn_ends = [e for e in projection.agent_events if e.get("type") == "turn_end"]
    assert turn_ends and turn_ends[0]["status"] == "committed"


def test_promotion_preserves_real_newlines_in_reasoning_verbatim() -> None:
    bridge = OpenMagiEventBridge()
    event = _final_event(
        [
            types.Part(text="line1\n", thought=True),
            types.Part(text="line2", thought=True),
        ]
    )

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    # Join is "" so a genuine newline inside a fragment survives, but NO
    # separator newline is injected between fragments.
    assert _text_deltas(projection) == ["line1\nline2"]


def test_signed_final_thought_is_never_promoted() -> None:
    # Anthropic-style: the final thinking part carries a thought_signature.
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = _final_event(
        [
            types.Part(
                text="private chain of thought",
                thought=True,
                thought_signature=b"sig-abc",
            )
        ]
    )

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    assert _text_deltas(projection) == []
    assert _promotion_markers(projection) == []
    assert "model.reasoning_promoted_to_final" not in _normalized_types(projection)
    assert [
        entry for entry in projection.transcript_entries if entry.kind == "assistant_text"
    ] == []


def test_any_signed_thought_part_blocks_promotion_of_the_whole_event() -> None:
    # A mix of unsigned + signed thought parts on the final event: the presence
    # of ANY signed part means this is genuine CoT and must not be promoted.
    bridge = OpenMagiEventBridge()
    event = _final_event(
        [
            types.Part(text="unsigned fragment", thought=True),
            types.Part(text="signed fragment", thought=True, thought_signature=b"sig"),
        ]
    )

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    assert _text_deltas(projection) == []
    assert _promotion_markers(projection) == []


def test_turn_that_streamed_text_earlier_does_not_promote() -> None:
    # A partial answer streamed earlier in the turn, then a reasoning-only final
    # event: the answer already exists, so CoT must NOT be appended.
    bridge = OpenMagiEventBridge()
    partial = Event(
        author="model",
        content=types.Content(role="model", parts=[types.Part(text="The answer is 4.")]),
        partial=True,
        invocation_id="turn-1",
    )
    first = bridge.project_adk_event(partial, turn_id="turn-1")
    assert _text_deltas(first) == ["The answer is 4."]

    final = _final_event([types.Part(text="reasoning tail", thought=True)])
    projection = bridge.project_adk_event(final, turn_id="turn-1")

    assert _promotion_markers(projection) == []
    assert "model.reasoning_promoted_to_final" not in _normalized_types(projection)
    # No spurious extra assistant answer from the reasoning.
    assert [
        entry for entry in projection.transcript_entries if entry.kind == "assistant_text"
    ] == []


def test_final_event_with_real_text_uses_that_text_not_promotion() -> None:
    # A normal terminal event that carries real (non-thought) content plus some
    # reasoning: the real text is the answer, and NO promotion marker fires.
    bridge = OpenMagiEventBridge()
    event = _final_event(
        [
            types.Part(text="hidden reasoning", thought=True),
            types.Part(text="Final answer: 42"),
        ]
    )

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    assert _text_deltas(projection) == ["Final answer: 42"]
    assert _promotion_markers(projection) == []
    assert "model.reasoning_promoted_to_final" not in _normalized_types(projection)


def test_non_final_thought_only_event_does_not_promote() -> None:
    # A non-terminal reasoning-only event (partial) must never trigger the
    # terminal fallback.
    bridge = OpenMagiEventBridge()
    projection = bridge.project_adk_event(
        _partial_thought_event("mid-turn reasoning"), turn_id="turn-1"
    )

    assert _promotion_markers(projection) == []
    assert "model.reasoning_promoted_to_final" not in _normalized_types(projection)


def test_marker_fires_exactly_when_promotion_happens_across_a_turn() -> None:
    # Integration-shaped: a Kimi-shaped turn that streams reasoning fragments
    # across several partial events (no answer text), then degrades into a
    # reasoning-only terminal event. Promotion fires exactly once, at the end.
    bridge = OpenMagiEventBridge(live_compatible=True)

    total_promotion_markers = 0
    total_text_deltas: list[str] = []

    # Streamed reasoning fragments (partials) - NOT the answer channel.
    for fragment in ("재", "무", "제표를 ", "분석"):
        step = bridge.project_adk_event(
            _partial_thought_event(fragment), turn_id="turn-1"
        )
        total_promotion_markers += len(_promotion_markers(step))
        total_text_deltas.extend(_text_deltas(step))

    # No promotion and no answer text before the terminal event.
    assert total_promotion_markers == 0
    assert total_text_deltas == []

    final = _final_event(
        [
            types.Part(text="당기순이익은 ", thought=True),
            types.Part(text="424,129원입니다.", thought=True),
        ]
    )
    terminal = bridge.project_adk_event(final, turn_id="turn-1")

    assert _promotion_markers(terminal) and len(_promotion_markers(terminal)) == 1
    assert _text_deltas(terminal) == ["당기순이익은 424,129원입니다."]
    # The turn as a whole surfaced a real, non-empty final answer.
    assert len("".join(total_text_deltas + _text_deltas(terminal))) > 0


def test_tool_boundary_then_reasoning_only_terminal_promotes() -> None:
    # A turn that runs a tool then degrades into reasoning-only on the terminal
    # step still yields a promoted final answer (turn-scoped text_emitted stays
    # False across the tool boundary).
    bridge = OpenMagiEventBridge(live_compatible=True)

    call = Event(
        id="event-call",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-1", name="Search", args={"q": "재무제표"}
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )
    call_projection = bridge.project_adk_event(call, turn_id="turn-1")
    assert _promotion_markers(call_projection) == []

    final = _final_event([types.Part(text="분석 결과: 이익 증가", thought=True)])
    projection = bridge.project_adk_event(final, turn_id="turn-1")

    assert _text_deltas(projection) == ["분석 결과: 이익 증가"]
    assert len(_promotion_markers(projection)) == 1
