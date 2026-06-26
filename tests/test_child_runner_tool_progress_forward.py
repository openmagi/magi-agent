"""Tests for child tool lifecycle forwarding into parent progress stream.

PR2/3 of the subagent-rich-emission series.

WHY
---
PR1 enriched ``child_started`` with ``agentName``/``model``/``taskTitle`` so the
chip label is meaningful.  But clicking the chip still showed only "Running
delegated child" — the per-subagent progress log was empty of any actual
child activity because ``_collect_turn_text_legacy`` only forwarded TEXT
chunks (and a generic placeholder), never the child's tool calls.

CONTRACT (privacy)
------------------
- Tool **names** are part of the public schema and SAFE to surface.
- Tool **arguments** and **results** are NOT forwarded — they may carry
  private data the child is operating on.
- Detail format: ``"Tool: <ToolName> | <phase>"`` where ``phase`` ∈
  ``{"start", "end"}``.  Matches the existing ``child_progress`` event family
  emitted via ``progress_sink``.
"""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from magi_agent.runtime.child_runner_live import RealLocalChildRunner


# --------------------------------------------------------------------------- #
# Fakes (shape-mimic ADK ``event.content.parts``)                              #
# --------------------------------------------------------------------------- #


class _FakeFunctionCall:
    def __init__(self, name: str, args: dict[str, object] | None = None) -> None:
        self.name = name
        self.args = args or {}


class _FakeFunctionResponse:
    def __init__(self, name: str, response: dict[str, object] | None = None) -> None:
        self.name = name
        self.response = response or {}


class _FakePart:
    def __init__(
        self,
        *,
        text: str | None = None,
        function_call: _FakeFunctionCall | None = None,
        function_response: _FakeFunctionResponse | None = None,
    ) -> None:
        self.text = text
        self.function_call = function_call
        self.function_response = function_response


class _FakeContent:
    def __init__(self, parts: list[_FakePart]) -> None:
        self.parts = parts


class _FakeEvent:
    def __init__(self, parts: list[_FakePart]) -> None:
        self.content = _FakeContent(parts)


def _provider_config(api_key: str = "sk-test") -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key=api_key)


def _request() -> object:
    from magi_agent.runtime.child_runner_boundary import ChildTaskRequest

    return ChildTaskRequest(
        parentExecutionId="parent-exec-1",
        turnId="turn-1",
        taskId="task-1",
        objective="Compute 1+1",
    )


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #


def test_function_call_part_emits_tool_start_progress() -> None:
    """An ADK event with a function_call part → child_progress with detail
    ``"Tool: <name> | start"``."""

    class _CallRunner:
        async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
            yield _FakeEvent([_FakePart(function_call=_FakeFunctionCall("WebSearch"))])
            yield _FakeEvent([_FakePart(text="ANSWER: done")])

    progress_events: list[dict[str, object]] = []
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=_CallRunner(),
        progress_sink=lambda event: progress_events.append(dict(event)),
    )

    output = asyncio.run(runner.run_child(_request()))
    assert output["status"] == "completed"
    details = [event["detail"] for event in progress_events]
    assert "Tool: WebSearch | start" in details


def test_function_response_part_emits_tool_end_progress() -> None:
    """An ADK event with a function_response part → child_progress with detail
    ``"Tool: <name> | end"``."""

    class _ResponseRunner:
        async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
            yield _FakeEvent(
                [
                    _FakePart(
                        function_response=_FakeFunctionResponse(
                            "WebSearch", {"results": "private content"}
                        )
                    )
                ]
            )
            yield _FakeEvent([_FakePart(text="ANSWER: done")])

    progress_events: list[dict[str, object]] = []
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=_ResponseRunner(),
        progress_sink=lambda event: progress_events.append(dict(event)),
    )

    output = asyncio.run(runner.run_child(_request()))
    assert output["status"] == "completed"
    details = [event["detail"] for event in progress_events]
    assert "Tool: WebSearch | end" in details


def test_tool_args_and_results_never_leak_into_progress() -> None:
    """Privacy contract: only tool NAMES are forwarded, never args or
    responses.  A grepping-ish check on the entire progress event list."""

    class _PrivateRunner:
        async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
            yield _FakeEvent(
                [
                    _FakePart(
                        function_call=_FakeFunctionCall(
                            "Bash",
                            {"command": "echo PRIVATE_PROMPT_BODY"},
                        )
                    )
                ]
            )
            yield _FakeEvent(
                [
                    _FakePart(
                        function_response=_FakeFunctionResponse(
                            "Bash", {"stdout": "PRIVATE_TOOL_RESULT_PAYLOAD"}
                        )
                    )
                ]
            )
            yield _FakeEvent([_FakePart(text="ANSWER: done")])

    progress_events: list[dict[str, object]] = []
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=_PrivateRunner(),
        progress_sink=lambda event: progress_events.append(dict(event)),
    )

    asyncio.run(runner.run_child(_request()))
    serialized = repr(progress_events)
    assert "PRIVATE_PROMPT_BODY" not in serialized
    assert "PRIVATE_TOOL_RESULT_PAYLOAD" not in serialized
    assert "echo " not in serialized
    # Tool name MUST appear though.
    assert "Bash" in serialized


def test_multiple_tools_in_sequence_all_emit_progress() -> None:
    class _SeqRunner:
        async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
            yield _FakeEvent([_FakePart(function_call=_FakeFunctionCall("WebSearch"))])
            yield _FakeEvent(
                [_FakePart(function_response=_FakeFunctionResponse("WebSearch"))]
            )
            yield _FakeEvent([_FakePart(function_call=_FakeFunctionCall("Calculation"))])
            yield _FakeEvent(
                [_FakePart(function_response=_FakeFunctionResponse("Calculation"))]
            )
            yield _FakeEvent([_FakePart(text="ANSWER: 42")])

    progress_events: list[dict[str, object]] = []
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=_SeqRunner(),
        progress_sink=lambda event: progress_events.append(dict(event)),
    )

    asyncio.run(runner.run_child(_request()))
    tool_phase_lines = [
        event["detail"]
        for event in progress_events
        if isinstance(event.get("detail"), str)
        and str(event["detail"]).startswith("Tool:")
    ]
    assert tool_phase_lines == [
        "Tool: WebSearch | start",
        "Tool: WebSearch | end",
        "Tool: Calculation | start",
        "Tool: Calculation | end",
    ]


def test_tool_call_progress_event_type_is_child_progress() -> None:
    """Schema check: emitted events use ``type="child_progress"`` so the parent
    sanitizer routes them the same as legacy text-chunk progress."""

    class _MinimalRunner:
        async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
            yield _FakeEvent([_FakePart(function_call=_FakeFunctionCall("WebSearch"))])
            yield _FakeEvent([_FakePart(text="ANSWER: done")])

    progress_events: list[dict[str, object]] = []
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=_MinimalRunner(),
        progress_sink=lambda event: progress_events.append(dict(event)),
    )

    asyncio.run(runner.run_child(_request()))
    tool_event = next(
        event
        for event in progress_events
        if isinstance(event.get("detail"), str)
        and str(event["detail"]).startswith("Tool:")
    )
    assert tool_event["type"] == "child_progress"
