"""F2-A: a contained child turn does NOT run the pre-final LLM criterion gate
chain, so a gate cannot false-fail the child's already-streamed correct answer
(the child_llm_collector_status_failed incident). A top-level turn still gates.
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.cli.headless import drain
from tests.support.engine_fakes import MockRunner, text_event

_SESSION = "s1"
_TURN = "t1"


def _turn_input(prompt: str = "compute 1+1") -> dict:
    return {"prompt": prompt, "session_id": _SESSION, "turn_id": _TURN}


async def _drive(driver: MagiEngineDriver) -> list[dict]:
    cancel = asyncio.Event()
    events, _ = await drain(
        driver.run_turn_stream(None, _turn_input(), cancel=cancel)
    )
    return [e.payload for e in events]  # type: ignore[union-attr]


def _blocking_driver(*, pre_final_llm_gates_allowed: bool) -> MagiEngineDriver:
    # A runner that streams a clean final answer, then a criterion factory that
    # is present so the built-in gates are model-capable.
    driver = MagiEngineDriver(
        runner=MockRunner([text_event("The answer is 2.", partial=True, turn_complete=True)]),
        criterion_model_factory=lambda: object(),
        pre_final_llm_gates_allowed=pre_final_llm_gates_allowed,
    )

    # Force the FIRST gate in the chain to BLOCK, so if the chain runs at all the
    # turn aborts with custom_llm_criterion_blocked.
    async def _always_block(**_kwargs: object) -> str:
        return "forced block for test"

    driver._maybe_llm_criterion_block = _always_block  # type: ignore[assignment,method-assign]
    return driver


def test_top_level_turn_runs_gate_and_blocks() -> None:
    # Control: with gates allowed (default), the forced-block gate aborts.
    driver = _blocking_driver(pre_final_llm_gates_allowed=True)
    payloads = asyncio.run(_drive(driver))
    kinds = [p.get("type") for p in payloads]
    assert "custom_llm_criterion_blocked" in kinds


def test_contained_child_turn_skips_gate_and_completes() -> None:
    # F2-A: with gates NOT allowed (child), the SAME forced-block gate never
    # runs, so the child's correct answer is not turned into a false failure.
    driver = _blocking_driver(pre_final_llm_gates_allowed=False)
    payloads = asyncio.run(_drive(driver))
    kinds = [p.get("type") for p in payloads]
    assert "custom_llm_criterion_blocked" not in kinds
    # The answer text was delivered.
    text = "".join(
        str(p.get("delta", "")) for p in payloads if p.get("type") == "text_delta"
    )
    assert "The answer is 2." in text


def test_flag_defaults_true() -> None:
    driver = MagiEngineDriver(runner=MockRunner([]))
    assert driver._pre_final_llm_gates_allowed is True
