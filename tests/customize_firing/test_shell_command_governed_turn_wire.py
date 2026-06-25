"""F-EXEC1 production-wire tests: ``shell_command`` lifecycle fan-outs run
through ``run_governed_turn`` for the 5 governed-turn slots.

Locks the fix for the prior "defined but never invoked" failure mode where
9 of the 11 advertised shell_command lifecycle slots had no runtime caller
outside the helper file itself. Each test below authors a rule at a single
slot and asserts the runner is actually invoked through the live wire when
the master flag is ON.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from magi_agent.adk_bridge.lifecycle_shell_command_control import (
    reset_shared_budget_for_tests,
)
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.customize.store import set_custom_rule
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.governed_turn import run_governed_turn
from magi_agent.runtime.turn_context import TurnContext


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)


def _rule(*, rid: str, fires_at: str, action: str = "audit", inline: str = "exit 0") -> dict:
    return {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "firesAt": fires_at,
        "action": action,
        "what": {
            "kind": "shell_command",
            "payload": {
                "source": "inline",
                "inline": inline,
                "timeout_seconds": 5,
                "shell": "bash",
            },
        },
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


def _child_stream(final_text: str) -> list[object]:
    return [
        RuntimeEvent(type="token", payload={"type": "text_delta", "delta": final_text}),
        EngineResult(
            terminal=Terminal.completed,
            usage={"input_tokens": 1, "output_tokens": 1},
            cost_usd=0.0,
            session_id="sess-x",
            turn_id="turn-x",
        ),
    ]


@pytest.fixture
def shell_flags_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    reset_shared_budget_for_tests()
    return cfile


@pytest.mark.asyncio
async def test_governed_turn_fires_shell_before_turn_start(
    shell_flags_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_custom_rule(
        _rule(rid="cr_wire_before_turn_start", fires_at="before_turn_start"),
        path=shell_flags_on,
    )

    spy = AsyncMock(
        return_value=(
            {"rule_id": "cr_wire_before_turn_start", "status": "executed", "passed": True, "exit_code": 0},
            "proceed",
        )
    )
    monkeypatch.setattr(
        "magi_agent.customize.shell_command.apply_shell_command_rule", spy
    )

    ctx = TurnContext(prompt="hi", session_id="sess-bt", turn_id="turn-bt")
    _ = [item async for item in run_governed_turn(ctx, runtime=_FakeRuntime([]))]

    # Wire is alive: the runner WAS invoked from run_governed_turn for the
    # before_turn_start slot.
    assert spy.await_count >= 1
    invoked_rule = spy.await_args_list[0].args[0]
    assert invoked_rule.get("id") == "cr_wire_before_turn_start"


@pytest.mark.asyncio
async def test_governed_turn_fires_shell_on_user_prompt_submit(
    shell_flags_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_custom_rule(
        _rule(rid="cr_wire_on_user_prompt_submit", fires_at="on_user_prompt_submit"),
        path=shell_flags_on,
    )

    invoked_ids: list[str] = []

    async def spy_apply(rule, **kwargs):
        invoked_ids.append(rule.get("id"))
        return (
            {"rule_id": rule.get("id"), "status": "executed", "passed": True, "exit_code": 0},
            "proceed",
        )

    monkeypatch.setattr(
        "magi_agent.customize.shell_command.apply_shell_command_rule", spy_apply
    )

    ctx = TurnContext(prompt="hi", session_id="sess-ups", turn_id="turn-ups")
    _ = [item async for item in run_governed_turn(ctx, runtime=_FakeRuntime([]))]

    assert "cr_wire_on_user_prompt_submit" in invoked_ids


@pytest.mark.asyncio
async def test_governed_turn_fires_shell_after_turn_end_on_top_level(
    shell_flags_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_custom_rule(
        _rule(rid="cr_wire_after_turn_end", fires_at="after_turn_end"),
        path=shell_flags_on,
    )

    invoked_ids: list[str] = []

    async def spy_apply(rule, **kwargs):
        invoked_ids.append(rule.get("id"))
        return (
            {"rule_id": rule.get("id"), "status": "executed", "passed": True, "exit_code": 0},
            "proceed",
        )

    monkeypatch.setattr(
        "magi_agent.customize.shell_command.apply_shell_command_rule", spy_apply
    )

    # Top-level turn (depth=0)
    ctx = TurnContext(prompt="hi", session_id="sess-ate", turn_id="turn-ate")
    final_text = "final answer"
    _ = [
        item
        async for item in run_governed_turn(
            ctx, runtime=_FakeRuntime(_child_stream(final_text))
        )
    ]

    assert "cr_wire_after_turn_end" in invoked_ids


@pytest.mark.asyncio
async def test_governed_turn_fires_shell_on_subagent_stop_on_child(
    shell_flags_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_custom_rule(
        _rule(rid="cr_wire_on_subagent_stop", fires_at="on_subagent_stop"),
        path=shell_flags_on,
    )

    invoked_ids: list[str] = []

    async def spy_apply(rule, **kwargs):
        invoked_ids.append(rule.get("id"))
        return (
            {"rule_id": rule.get("id"), "status": "executed", "passed": True, "exit_code": 0},
            "proceed",
        )

    monkeypatch.setattr(
        "magi_agent.customize.shell_command.apply_shell_command_rule", spy_apply
    )

    # Child turn (depth>0)
    ctx = TurnContext(prompt="hi", session_id="sess-ss", turn_id="turn-ss", depth=1)
    _ = [
        item
        async for item in run_governed_turn(
            ctx, runtime=_FakeRuntime(_child_stream("done"))
        )
    ]

    assert "cr_wire_on_subagent_stop" in invoked_ids


@pytest.mark.asyncio
async def test_governed_turn_fires_shell_pre_final_and_blocks_on_nonzero(
    shell_flags_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pre_final shell rule with action=block + exit non-zero ⇒ engine result
    is REPLACED with a policy-blocked terminal."""
    set_custom_rule(
        _rule(
            rid="cr_wire_pre_final_block",
            fires_at="pre_final",
            action="block",
            inline="exit 2",
        ),
        path=shell_flags_on,
    )

    ctx = TurnContext(prompt="hi", session_id="sess-pf", turn_id="turn-pf")
    items = [
        item
        async for item in run_governed_turn(
            ctx,
            runtime=_FakeRuntime(
                [
                    RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "draft"}),
                    EngineResult(
                        terminal=Terminal.completed,
                        usage={"input_tokens": 1, "output_tokens": 1},
                        cost_usd=0.0,
                        session_id="sess-pf",
                        turn_id="turn-pf",
                    ),
                ]
            ),
        )
    ]

    # Last item must be the synthetic policy-blocked terminal.
    terminals = [it for it in items if isinstance(it, EngineResult)]
    assert terminals, "expected at least one EngineResult in stream"
    final = terminals[-1]
    assert final.terminal == Terminal.aborted
    assert "customize_policy_blocked" in (final.error or "")
    assert "pre_final" in (final.error or "")


@pytest.mark.asyncio
async def test_governed_turn_shell_off_path_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Master shell flag OFF ⇒ no runner invocation even with a rule."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(
        _rule(rid="cr_off_wire", fires_at="before_turn_start"),
        path=cfile,
    )

    async def fail_apply(rule, **kwargs):
        raise AssertionError("runner must not be invoked when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.shell_command.apply_shell_command_rule", fail_apply
    )

    ctx = TurnContext(prompt="hi", session_id="sess-off", turn_id="turn-off")
    _ = [item async for item in run_governed_turn(ctx, runtime=_FakeRuntime([]))]
