"""Regression: a provider final aggregate that MISMATCHES the streamed text must
not be re-emitted on top of the already-streamed partials.

Live incident (Kimi K2.6 via fireworks/litellm, local serve 0.1.141): a long
turn streamed its final answer token-by-token as partial ``text_delta`` events,
then one more non-partial event re-emitted the whole block as a single chunk in
a TRUNCATED, re-prefixed variant literally ending in a "...". The reconciliation
helper assumes the aggregate is a superset of the streamed text and returns the
whole aggregate when it finds no overlap, so the clarification paragraph rendered
twice and the message ended cut mid-word.

The fix treats the streamed text as the source of truth: when text already
streamed this run and the aggregate shares no overlap with it, the aggregate is
dropped for the display delta and the durable transcript carries the streamed
text instead. These tests concatenate the emitted ``text_delta`` deltas exactly
as the store reducer / frontend do and assert no duplication.
"""

from google.adk.events import Event
from google.genai import types

from magi_agent.adk_bridge.event_adapter import (
    OpenMagiEventBridge,
    _unstreamed_final_text,
)


def _text_event(text: str, *, partial: bool) -> Event:
    return Event(
        author="model",
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        partial=partial,
        invocation_id="turn-1",
    )


def _project(events: list[Event]) -> list:
    bridge = OpenMagiEventBridge()
    projections = []
    for event in events:
        projections.append(bridge.project_adk_event(event, turn_id="turn-1"))
    return projections


def _joined_text_deltas(projections: list) -> str:
    parts: list[str] = []
    for projection in projections:
        for agent_event in projection.agent_events:
            if agent_event.get("type") == "text_delta":
                parts.append(str(agent_event["delta"]))
    return "".join(parts)


def test_mismatched_truncated_aggregate_is_not_re_emitted() -> None:
    # Stream "AAA BBB" token-by-token, then a truncated re-prefixed aggregate
    # "AAA BB..." (no clean overlap with the streamed tail). The aggregate must
    # be dropped so the block appears exactly once and the message is not cut.
    projections = _project(
        [
            _text_event("AAA ", partial=True),
            _text_event("BBB", partial=True),
            _text_event("AAA BB...", partial=False),
        ]
    )
    joined = _joined_text_deltas(projections)
    assert joined == "AAA BBB"
    assert joined.count("AAA BBB") == 1
    assert "AAA BB..." not in joined
    assert "..." not in joined
    # The durable transcript carries the streamed truth, not the truncated copy.
    final = projections[-1]
    assert final.transcript_entries[0].kind == "assistant_text"
    assert final.transcript_entries[0].text == "AAA BBB"


def test_normal_tail_completion_still_emits_only_unstreamed_suffix() -> None:
    # The case the reconciliation was built for: the aggregate extends the
    # streamed prefix, so only the genuine unstreamed tail is emitted.
    projections = _project(
        [
            _text_event("AAA ", partial=True),
            _text_event("AAA BBB", partial=False),
        ]
    )
    final = projections[-1]
    assert [
        e["delta"] for e in final.agent_events if e.get("type") == "text_delta"
    ] == ["BBB"]
    assert _joined_text_deltas(projections) == "AAA BBB"
    assert final.transcript_entries[0].text == "AAA BBB"


def test_nothing_streamed_emits_whole_aggregate() -> None:
    # No partials streamed: the aggregate is the only source and emits whole
    # (the reasoning-promotion / no-stream path is unchanged).
    projections = _project([_text_event("AAA", partial=False)])
    final = projections[0]
    assert [
        e["delta"] for e in final.agent_events if e.get("type") == "text_delta"
    ] == ["AAA"]
    assert final.transcript_entries[0].text == "AAA"


def test_unstreamed_final_text_guard_drops_whole_aggregate_mismatch() -> None:
    # Unit contract for the guard: streamed non-empty + no overlap -> "".
    assert _unstreamed_final_text("AAA BB...", "AAA BBB") == ""
    # Genuine tail completion still returns only the unstreamed suffix.
    assert _unstreamed_final_text("AAA BBB", "AAA ") == "BBB"
    # Nothing streamed -> whole aggregate emits (no guard).
    assert _unstreamed_final_text("AAA", "") == "AAA"
    # Fully-streamed aggregate (equal) -> nothing left, unchanged.
    assert _unstreamed_final_text("AAA", "AAA") == ""
