"""Unit tests for the Gemini content-ordering repair (before_model control).

Regression target: multi-tool turns assembled an LlmRequest whose contents had
two consecutive ``model`` turns (e.g. a text turn then a ``function_call`` turn),
which Gemini rejects with HTTP 400 "function call turn comes immediately after a
user turn or after a function response turn", crashing the turn as
``runner_error``. The repair merges adjacent same-role turns so roles alternate.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

from magi_agent.adk_bridge.control_plane import (
    GeminiContentOrderingRepairControl,
    build_gemini_content_ordering_control,
)
from magi_agent.adk_bridge.gemini_content_ordering import (
    REPAIR_DISABLED_ENV,
    repair_gemini_content_ordering,
)


def _content(role: str, *parts: object) -> NS:
    return NS(role=role, parts=list(parts))


def _part(**kwargs: object) -> NS:
    return NS(**kwargs)


def _roles(contents: object) -> list[object]:
    return [c.role for c in contents]


def _part_counts(contents: object) -> list[int]:
    return [len(c.parts) for c in contents]


def test_merges_model_text_then_function_call_turn() -> None:
    seq = [
        _content("user", _part(text="hi")),
        _content("model", _part(text="let me search")),
        _content("model", _part(function_call={"name": "WebSearch"})),
    ]
    out = repair_gemini_content_ordering(seq)
    assert out is not None
    # function_call model turn folded into the preceding model turn -> now
    # immediately preceded by the user turn.
    assert _roles(out) == ["user", "model"]
    assert _part_counts(out) == [1, 2]


def test_valid_alternating_sequence_is_noop() -> None:
    seq = [
        _content("user", _part(text="hi")),
        _content("model", _part(function_call={"name": "x"})),
        _content("user", _part(function_response={"name": "x"})),
        _content("model", _part(text="done")),
    ]
    assert repair_gemini_content_ordering(seq) is None


def test_realistic_multi_tool_violation() -> None:
    seq = [
        _content("user", _part(text="research")),
        _content("model", _part(function_call={"name": "WebSearch", "id": "1"})),
        _content("user", _part(function_response={"name": "WebSearch", "id": "1"})),
        _content("model", _part(text="found stuff")),
        _content("model", _part(function_call={"name": "WebSearch", "id": "2"})),
    ]
    out = repair_gemini_content_ordering(seq)
    assert _roles(out) == ["user", "model", "user", "model"]
    # trailing model turn carries text + function_call, preceded by a
    # function_response (user) turn -> Gemini-valid.
    assert _part_counts(out) == [1, 1, 1, 2]


def test_collapses_three_consecutive_model_turns() -> None:
    seq = [
        _content("user", _part(text="q")),
        _content("model", _part(text="a")),
        _content("model", _part(text="b")),
        _content("model", _part(function_call={"name": "t"})),
    ]
    out = repair_gemini_content_ordering(seq)
    assert _roles(out) == ["user", "model"]
    assert _part_counts(out) == [1, 3]


def test_drops_none_entries() -> None:
    out = repair_gemini_content_ordering(
        [_content("user", _part(text="a")), None, _content("user", _part(text="b"))]
    )
    assert out is not None
    assert _roles(out) == ["user"]
    assert _part_counts(out) == [2]


def test_noop_inputs() -> None:
    assert repair_gemini_content_ordering([]) is None
    assert repair_gemini_content_ordering([_content("user", _part(text="x"))]) is None
    assert repair_gemini_content_ordering(None) is None
    assert repair_gemini_content_ordering("nope") is None


def test_control_mutates_llm_request_in_place() -> None:
    control = GeminiContentOrderingRepairControl()
    request = NS(
        contents=[
            _content("user", _part(text="q")),
            _content("model", _part(text="a")),
            _content("model", _part(function_call={"name": "t"})),
        ]
    )
    asyncio.run(control.on_before_model(callback_context=None, llm_request=request))
    assert _roles(request.contents) == ["user", "model"]
    assert _part_counts(request.contents) == [1, 2]
    assert control.name == "magi_gemini_content_order_repair"


def test_control_leaves_valid_request_untouched() -> None:
    control = GeminiContentOrderingRepairControl()
    valid = [
        _content("user", _part(text="q")),
        _content("model", _part(function_call={"name": "t"})),
        _content("user", _part(function_response={"name": "t"})),
    ]
    request = NS(contents=valid)
    asyncio.run(control.on_before_model(callback_context=None, llm_request=request))
    assert request.contents is valid


def test_kill_switch_gating() -> None:
    assert build_gemini_content_ordering_control({}) is not None
    assert build_gemini_content_ordering_control({REPAIR_DISABLED_ENV: "1"}) is None
    assert build_gemini_content_ordering_control({REPAIR_DISABLED_ENV: "true"}) is None
    assert build_gemini_content_ordering_control({REPAIR_DISABLED_ENV: "0"}) is not None
