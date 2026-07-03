"""PR-F-LIFE1 production-wire test: turn-boundary audit fan-outs run through
``run_governed_turn`` (the canonical CLI/serve/child funnel).

The unit tests in ``test_user_prompt_submit_firing.py`` /
``test_subagent_stop_firing.py`` cover the sibling F-UX1 slots. This file
locks the F-LIFE1 wires:

* ``run_before_turn_start_audit`` fires at the TOP of ``run_governed_turn``
  (before the engine stream starts AND before the F-UX1
  ``on_user_prompt_submit`` fan-out).
* ``run_after_turn_end_audit`` fires in the ``finally`` block AFTER a
  top-level turn (``ctx.depth == 0``) completes — distinct from
  ``on_subagent_stop`` which only fires for child turns.

Each test pairs an "ON" path (master flag ON + matching rule) with an
"OFF" path (master flag OFF or rule missing) so the byte-identical default
contract is exercised.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.customize.store import set_custom_rule
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.governed_turn import run_governed_turn
from magi_agent.runtime.turn_context import TurnContext


_START_RULE_ID = "cr_flife1_governed_turn_before_turn_start_audit"
_START_CRITERION = "the inbound prompt does not contain raw credentials"
_PROMPT_TEXT = "Please fetch https://example.com with AKIA1234567890ABCDEF."

_END_RULE_ID = "cr_flife1_governed_turn_after_turn_end_audit"
_END_CRITERION = "the final answer is not empty and includes a summary"


def _start_rule() -> dict:
    return {
        "id": _START_RULE_ID,
        "scope": "always",
        "enabled": True,
        "what": {"kind": "llm_criterion", "payload": {"criterion": _START_CRITERION}},
        "firesAt": "before_turn_start",
        "action": "audit",
    }


def _end_rule() -> dict:
    return {
        "id": _END_RULE_ID,
        "scope": "always",
        "enabled": True,
        "what": {"kind": "llm_criterion", "payload": {"criterion": _END_CRITERION}},
        "firesAt": "after_turn_end",
        "action": "audit",
    }


class _FakeEngine:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    async def run_turn_stream(
        self, _none: object, _turn_input: object, *, cancel: object, gate: object
    ) -> AsyncIterator[object]:
        for item in self._items:
            yield item


class _FakeRuntime:
    def __init__(self, items: list[object]) -> None:
        self.engine = _FakeEngine(items)
        self.gate = None


def _final_stream(final_text: str) -> list[object]:
    return [
        RuntimeEvent(type="token", payload={"type": "text_delta", "delta": final_text}),
        EngineResult(
            terminal=Terminal.completed,
            usage={"input_tokens": 10, "output_tokens": 10},
            cost_usd=0.0,
            session_id="sess-top",
            turn_id="turn-1",
        ),
    ]


def _flags_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    # Sentinel non-None factory so the audit fan-out reaches the (mocked)
    # evaluate_criterion call instead of short-circuiting to status="skipped".
    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn._build_lifecycle_critic_factory",
        lambda: object(),
    )
    return cfile


@pytest.mark.asyncio
async def test_governed_turn_fires_before_turn_start_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Top-level governed turn with a matching before_turn_start rule MUST
    invoke the criterion judge exactly once with the inbound prompt text."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_start_rule(), path=cfile)

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None, evidence_context=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    ctx = TurnContext(
        prompt=_PROMPT_TEXT,
        session_id="sess-top",
        turn_id="turn-1",
    )
    items = [item async for item in run_governed_turn(ctx, runtime=_FakeRuntime([]))]

    # Stream pass-through unchanged (empty stream → no items).
    assert items == []
    # Wire is alive: the judge was invoked with the inbound prompt text.
    assert len(calls) == 1
    assert calls[0]["criterion"] == _START_CRITERION
    assert calls[0]["draft_text"] == _PROMPT_TEXT


@pytest.mark.asyncio
async def test_governed_turn_before_turn_start_inert_when_master_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Master flag OFF ⇒ governed_turn wire is a no-op even with a rule.
    Locks the byte-identical default contract."""
    cfile = _flags_on(monkeypatch, tmp_path)
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED", "0")
    set_custom_rule(_start_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None, evidence_context=None):
        raise AssertionError(
            "before_turn_start judge must not be invoked when master flag is OFF"
        )

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    ctx = TurnContext(prompt=_PROMPT_TEXT, session_id="sess-top", turn_id="turn-1")
    _ = [item async for item in run_governed_turn(ctx, runtime=_FakeRuntime([]))]


@pytest.mark.asyncio
async def test_governed_turn_fires_after_turn_end_audit_on_top_level_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Top-level turn (``ctx.depth == 0``) with a matching rule MUST invoke
    the criterion judge with the ACTUAL final assistant text collected off
    the event stream — not an empty string."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_end_rule(), path=cfile)

    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None, evidence_context=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "looks good")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    final_text = "Here is the summary of the work."
    ctx = TurnContext(
        prompt="Do a quick summary",
        session_id="sess-top",
        turn_id="turn-1",
    )
    items = [
        item
        async for item in run_governed_turn(
            ctx, runtime=_FakeRuntime(_final_stream(final_text))
        )
    ]

    # Stream passed through untouched.
    assert len(items) == 2
    # Wire is alive: the judge ran with the REAL final_text accumulated off
    # the event stream.
    assert len(calls) == 1
    assert calls[0]["criterion"] == _END_CRITERION
    assert calls[0]["draft_text"] == final_text


@pytest.mark.asyncio
async def test_governed_turn_after_turn_end_inert_on_child_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Child turn (``ctx.depth > 0``) MUST NOT fire after_turn_end — that
    slot only applies to top-level turns. The on_subagent_stop slot covers
    spawned children."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_end_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None, evidence_context=None):
        raise AssertionError(
            "after_turn_end must not fire on a child (depth>0) turn"
        )

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    ctx = TurnContext(
        prompt="hello", session_id="sess-child", turn_id="turn-1", depth=1
    )
    _ = [
        item
        async for item in run_governed_turn(
            ctx, runtime=_FakeRuntime(_final_stream("child output"))
        )
    ]


@pytest.mark.asyncio
async def test_governed_turn_after_turn_end_skips_when_no_text_emitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty final_text on a top-level turn ⇒ audit short-circuits to
    status='skipped' (mirrors the on_subagent_stop finding #3 guard)."""
    cfile = _flags_on(monkeypatch, tmp_path)
    set_custom_rule(_end_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None, evidence_context=None):
        raise AssertionError(
            "judge must not be invoked when there is no content to judge"
        )

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    # Top-level turn but stream has NO text_delta events.
    ctx = TurnContext(prompt="hello", session_id="sess-top", turn_id="turn-1")
    empty_stream: list[object] = [
        EngineResult(
            terminal=Terminal.completed,
            usage={},
            cost_usd=0.0,
            session_id="sess-top",
            turn_id="turn-1",
        )
    ]
    _ = [
        item
        async for item in run_governed_turn(ctx, runtime=_FakeRuntime(empty_stream))
    ]


@pytest.mark.asyncio
async def test_governed_turn_after_turn_end_inert_when_master_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Master flag OFF ⇒ after_turn_end wire is a no-op (byte-identical OFF
    contract)."""
    cfile = _flags_on(monkeypatch, tmp_path)
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED", "0")
    set_custom_rule(_end_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None, evidence_context=None):
        raise AssertionError(
            "after_turn_end judge must not be invoked when master flag is OFF"
        )

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    ctx = TurnContext(prompt="hello", session_id="sess-top", turn_id="turn-1")
    _ = [
        item
        async for item in run_governed_turn(
            ctx, runtime=_FakeRuntime(_final_stream("done"))
        )
    ]
