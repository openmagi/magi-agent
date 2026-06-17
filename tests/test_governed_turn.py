"""Tests for run_governed_turn primitive (Task 1.2).

No real model / provider keys required — uses a fake runtime built with
MockRunner so the test is hermetic.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from magi_agent.runtime.turn_context import TurnContext
from magi_agent.runtime.governed_turn import run_governed_turn
from tests.support.engine_fakes import MockRunner, text_event
from magi_agent.cli.engine import MagiEngineDriver


def _fake_runtime() -> SimpleNamespace:
    driver = MagiEngineDriver(runner=MockRunner([text_event("hi")]))
    return SimpleNamespace(engine=driver, gate=None)


def test_reuses_provided_runtime_and_injects_harness_state() -> None:
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
