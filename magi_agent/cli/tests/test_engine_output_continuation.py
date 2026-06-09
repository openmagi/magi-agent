"""LIVE tests for output-continuation: resume a response truncated at the
model's per-response output-token cap by re-invoking and appending.

These drive the ACTUAL run-invocation seam (``MagiEngineDriver`` consuming a
fake runner's ``run_async``) and prove:

* a turn whose final response stops with finish_reason=MAX_TOKENS triggers a
  genuine SECOND ``run_async`` (continuation) and the appended output reaches
  the consumer;
* the continuation budget bounds the re-invocations (no infinite loop);
* with the feature OFF (``output_continuation=None``) the truncated turn is NOT
  resumed (single invocation — byte-identical to pre-feature streaming).
"""

from __future__ import annotations

import asyncio

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.cli.headless import drain
from magi_agent.runtime.output_continuation import OutputContinuationConfig

from google.adk.events import Event  # noqa: E402
from google.genai import types  # noqa: E402


def _truncated_turn(text: str) -> list[Event]:
    """A streaming text turn whose final event stops at the output cap."""
    return [
        Event(
            author="model",
            partial=True,
            content=types.Content(role="model", parts=[types.Part(text=text)]),
        ),
        Event(
            author="model",
            partial=False,
            content=types.Content(role="model", parts=[types.Part(text=text)]),
            finish_reason=types.FinishReason.MAX_TOKENS,
        ),
    ]


def _complete_turn(text: str) -> list[Event]:
    return [
        Event(
            author="model",
            partial=True,
            content=types.Content(role="model", parts=[types.Part(text=text)]),
        ),
        Event(
            author="model",
            partial=False,
            content=types.Content(role="model", parts=[types.Part(text=text)]),
            finish_reason=types.FinishReason.STOP,
        ),
    ]


def _turn_input(session_id: str, turn_id: str = "turn-1", prompt: str = "go") -> dict:
    return {"prompt": prompt, "session_id": session_id, "turn_id": turn_id}


class _TruncateThenCompleteRunner:
    """``run_async`` truncates on the 1st invocation, completes on the 2nd."""

    def __init__(self) -> None:
        self.invocations = 0

    async def run_async(self, **_kwargs: object):
        self.invocations += 1
        events = (
            _truncated_turn("Part one")
            if self.invocations == 1
            else _complete_turn("Part two")
        )
        for event in events:
            yield event
        if False:  # pragma: no cover - generator type hint
            yield None


class _AlwaysTruncateRunner:
    """``run_async`` always stops at the output cap (resumable every time)."""

    def __init__(self) -> None:
        self.invocations = 0

    async def run_async(self, **_kwargs: object):
        self.invocations += 1
        for event in _truncated_turn(f"chunk {self.invocations}"):
            yield event
        if False:  # pragma: no cover - generator type hint
            yield None


def _config(max_continuations: int = 4) -> OutputContinuationConfig:
    return OutputContinuationConfig(enabled=True, max_continuations=max_continuations)


def test_truncated_response_is_resumed_and_appended() -> None:
    runner = _TruncateThenCompleteRunner()
    driver = MagiEngineDriver(runner=runner, output_continuation=_config())
    cancel = asyncio.Event()

    events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-cont"), cancel=cancel))
    )

    # Genuine SECOND invocation: the model was re-invoked to resume.
    assert runner.invocations == 2
    # A continuation status event was surfaced.
    assert any(
        e.payload.get("type") == "output_continuation" for e in events
    )
    # Both the truncated part and the resumed part reached the consumer.
    assert any("Part one" in str(e.payload) for e in events)
    assert any("Part two" in str(e.payload) for e in events)
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal is Terminal.completed


def test_truncated_attempt_does_not_emit_turn_end_before_continuation() -> None:
    runner = _TruncateThenCompleteRunner()
    driver = MagiEngineDriver(runner=runner, output_continuation=_config())
    cancel = asyncio.Event()

    events, _terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-cont-order"), cancel=cancel))
    )

    continuation_index = next(
        i
        for i, event in enumerate(events)
        if event.payload.get("type") == "output_continuation"
    )
    early_turn_end_events = [
        event
        for event in events[:continuation_index]
        if event.payload.get("type") == "turn_end"
    ]

    assert early_turn_end_events == []


def test_continuation_budget_bounds_reinvocations() -> None:
    runner = _AlwaysTruncateRunner()
    driver = MagiEngineDriver(
        runner=runner, output_continuation=_config(max_continuations=2)
    )
    cancel = asyncio.Event()

    _events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-cont-budget"), cancel=cancel))
    )

    # initial + 2 continuations = 3 invocations; bounded (not infinite).
    assert runner.invocations == 3
    assert isinstance(terminal, EngineResult)


def test_flag_off_does_not_resume_truncated_turn() -> None:
    runner = _TruncateThenCompleteRunner()
    driver = MagiEngineDriver(runner=runner, output_continuation=None)
    cancel = asyncio.Event()

    events, _terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-cont-off"), cancel=cancel))
    )

    # Single invocation: truncated turn is NOT resumed when the feature is off.
    assert runner.invocations == 1
    assert not any(
        e.payload.get("type") == "output_continuation" for e in events
    )
