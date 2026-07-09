"""Per-turn event budget accounting: count real WORK, not streaming deltas.

Regression cover for the mid-sentence truncation bug: a long answer was cut off
because every streamed text/thinking delta consumed the per-turn event budget,
so the turn self-exhausted the cap on its own streaming granularity. The fix
(1) excludes mid-stream partial deltas from the count and (2) raises + env-tunes
the default cap.
"""

from __future__ import annotations

from google.adk.events import Event
from google.genai import types

from magi_agent.engine.driver import (
    _DEFAULT_MAX_EVENT_COUNT,
    _adk_event_counts_toward_budget,
    _resolve_max_event_count,
)


def _text_event(text: str, *, partial: bool, turn_complete: bool = False) -> Event:
    return Event(
        author="model",
        partial=partial,
        turn_complete=turn_complete,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


def _call_event(tool: str, call_id: str) -> Event:
    return Event(
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(name=tool, args={}, id=call_id)
                )
            ],
        ),
    )


# --- _adk_event_counts_toward_budget --------------------------------------- #


def test_mid_stream_partial_delta_does_not_count() -> None:
    # A streaming text delta: partial=True, not turn_complete → excluded.
    assert _adk_event_counts_toward_budget(_text_event("hel", partial=True)) is False
    assert (
        _adk_event_counts_toward_budget(
            _text_event("thinking...", partial=True, turn_complete=False)
        )
        is False
    )


def test_final_event_counts_even_if_partial() -> None:
    # The final aggregated event carries turn_complete=True (even when the ADK
    # shape leaves partial=True) → must count.
    assert (
        _adk_event_counts_toward_budget(
            _text_event("done", partial=True, turn_complete=True)
        )
        is True
    )


def test_non_partial_text_counts() -> None:
    assert (
        _adk_event_counts_toward_budget(_text_event("full", partial=False)) is True
    )


def test_tool_call_event_counts() -> None:
    # function_call events have falsy partial → count as real work.
    assert _adk_event_counts_toward_budget(_call_event("Calculation", "c1")) is True


def test_mapping_event_shape_supported() -> None:
    assert _adk_event_counts_toward_budget({"partial": True}) is False
    assert (
        _adk_event_counts_toward_budget({"partial": True, "turn_complete": True})
        is True
    )
    assert _adk_event_counts_toward_budget({"partial": False}) is True
    assert _adk_event_counts_toward_budget({}) is True


# --- _resolve_max_event_count ---------------------------------------------- #


def test_resolve_default_when_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_MAX_TURN_EVENT_COUNT", raising=False)
    assert _resolve_max_event_count() == _DEFAULT_MAX_EVENT_COUNT
    assert _DEFAULT_MAX_EVENT_COUNT >= 20000  # generous-budget policy


def test_resolve_positive_env_override(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_MAX_TURN_EVENT_COUNT", "50000")
    assert _resolve_max_event_count() == 50000


def test_resolve_invalid_env_falls_back(monkeypatch) -> None:
    for bad in ("0", "-5", "", "abc", "  "):
        monkeypatch.setenv("MAGI_MAX_TURN_EVENT_COUNT", bad)
        assert _resolve_max_event_count() == _DEFAULT_MAX_EVENT_COUNT
