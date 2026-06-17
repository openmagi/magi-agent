"""Tests for run_governed_turn primitive (Task 1.2).

No real model / provider keys required — uses a fake runtime built with
MockRunner so the test is hermetic.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import mock

from magi_agent.runtime.turn_context import TurnContext
from magi_agent.runtime.governed_turn import run_governed_turn
from tests.support.engine_fakes import MockRunner, text_event
from magi_agent.cli.engine import MagiEngineDriver


def _fake_runtime() -> SimpleNamespace:
    driver = MagiEngineDriver(runner=MockRunner([text_event("hi")]))
    return SimpleNamespace(engine=driver, gate=None)


# ---------------------------------------------------------------------------
# Recording stub for harness_state injection assertions
# ---------------------------------------------------------------------------

class _RecordingEngine:
    """Minimal engine stub that records the turn_input it receives and yields
    one fake event so callers get a non-empty stream."""

    def __init__(self) -> None:
        self.recorded_turn_input: dict | None = None

    async def run_turn_stream(
        self,
        runtime: object,
        turn_input: object,
        *,
        cancel: object,
        gate: object,
    ):
        self.recorded_turn_input = turn_input  # type: ignore[assignment]
        yield "stub-event"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_reuses_provided_runtime_and_injects_harness_state() -> None:
    """Happy-path: events flow through when a pre-built runtime is provided."""
    rt = _fake_runtime()
    ctx = TurnContext(prompt="go", session_id="s1", turn_id="t1")

    async def _collect() -> list[object]:
        out: list[object] = []
        agen = run_governed_turn(ctx, runtime=rt)
        async for item in agen:
            out.append(item)
        return out

    events = asyncio.run(_collect())
    assert events  # the fake turn produced at least one event/terminal


def test_provided_runtime_skips_build_runtime() -> None:
    """When runtime is provided, _build_runtime must NOT be called (reuse path)."""
    rt = _fake_runtime()
    ctx = TurnContext(prompt="skip-build", session_id="s2", turn_id="t2")

    with mock.patch(
        "magi_agent.runtime.governed_turn._build_runtime"
    ) as mock_build:

        async def _run() -> None:
            async for _ in run_governed_turn(ctx, runtime=rt):
                pass

        asyncio.run(_run())
        mock_build.assert_not_called()


def test_harness_state_reaches_run_turn_stream() -> None:
    """ctx and its harness_state must reach the engine via to_turn_input()."""
    recording_engine = _RecordingEngine()
    rt = SimpleNamespace(engine=recording_engine, gate=None)
    ctx = TurnContext(prompt="check-injection", session_id="s3", turn_id="t3")

    async def _run() -> None:
        async for _ in run_governed_turn(ctx, runtime=rt):
            pass

    asyncio.run(_run())

    ti = recording_engine.recorded_turn_input
    assert ti is not None, "run_turn_stream was never called"
    assert ti["harness_state"] is ctx, (
        "harness_state in turn_input must be the TurnContext object itself"
    )
    assert ti["prompt"] == ctx.prompt, (
        f"prompt mismatch: got {ti['prompt']!r}, expected {ctx.prompt!r}"
    )
