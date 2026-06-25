"""F-EXEC2 production-wire tests: ``shell_check`` lifecycle fan-outs run
through the real runtime chokepoints (governed_turn pre_final +
facades.execute_tool_with_hooks before_tool_use).

Locks the fix for the prior "defined but never invoked" failure mode where
the two F-EXEC2 advertised slots had no runtime caller outside the helper
file itself (the same BLOCKER 1 pattern the F-EXEC1 commit message
documented for its own 9-of-11 dead-callsite problem). Each test below
authors a rule at one of the two v1 gate slots and asserts:

* the runner is actually invoked through the live wire when the master
  flag is ON, and
* a ``passed=False`` (or non-zero exit) verdict with ``action == "block"``
  short-circuits the production hot path (governed_turn replaces the
  EngineResult with a synthetic policy-blocked terminal; facades
  execute_tool_with_hooks returns a blocked ToolResult and never invokes
  the dispatcher).
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from magi_agent.adk_bridge.lifecycle_shell_command_control import (
    reset_shared_budget_for_tests,
)
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.customize.store import set_custom_rule
from magi_agent.facades import execute_tool_with_hooks
from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.hooks.bus import HookBus, HookBusObservation, HookBusRunResult
from magi_agent.hooks.context import HookContext
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.governed_turn import run_governed_turn
from magi_agent.runtime.turn_context import TurnContext
from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.result import ToolResult


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)


def _check_rule(
    *,
    rid: str,
    fires_at: str,
    action: str,
    inline: str,
    timeout_seconds: int = 5,
) -> dict:
    return {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "firesAt": fires_at,
        "action": action,
        "what": {
            "kind": "shell_check",
            "payload": {
                "source": "inline",
                "inline": inline,
                "timeout_seconds": timeout_seconds,
                "shell": "bash",
            },
        },
    }


def _continue() -> HookBusRunResult:
    return HookBusRunResult(
        final_action="continue",
        results=(),
        observation=HookBusObservation(),
        harness_state=build_default_resolved_harness_state(),
    )


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


@pytest.fixture
def cfg_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """F-EXEC2 master flag ON. F-EXEC1 OFF so the test isolates the new wire."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    reset_shared_budget_for_tests()
    return cfile


# ---------------------------------------------------------------------------
# governed_turn — pre_final wire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_governed_turn_fires_shell_check_pre_final_and_blocks_on_failed(
    cfg_on: Path,
) -> None:
    """pre_final shell_check rule with action=block + passed=false ⇒ engine
    result is REPLACED with a policy-blocked terminal.

    This is the load-bearing assertion: the new
    ``_ShellCheckPreFinalCollector`` MUST be wired into ``run_governed_turn``
    so the operator's pre_final verifier short-circuits the final answer.
    """
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_wire_pre_final_block",
            fires_at="pre_final",
            action="block",
            # passed=false JSON ⇒ honest block via the F-LIFE4a verdict
            # reducer (rule action=block AND passed=False).
            inline="echo '{\"passed\": false, \"reason\": \"no_facts_survey\"}'",
        ),
        path=cfg_on,
    )

    ctx = TurnContext(
        prompt="hi", session_id="sess-check-pf", turn_id="turn-check-pf"
    )
    items = [
        item
        async for item in run_governed_turn(
            ctx,
            runtime=_FakeRuntime(
                [
                    RuntimeEvent(
                        type="token",
                        payload={"type": "text_delta", "delta": "draft answer"},
                    ),
                    EngineResult(
                        terminal=Terminal.completed,
                        usage={"input_tokens": 1, "output_tokens": 1},
                        cost_usd=0.0,
                        session_id="sess-check-pf",
                        turn_id="turn-check-pf",
                    ),
                ]
            ),
        )
    ]

    # Last terminal item must be the synthetic policy-blocked one.
    terminals = [it for it in items if isinstance(it, EngineResult)]
    assert terminals, "expected at least one EngineResult in stream"
    final = terminals[-1]
    assert final.terminal == Terminal.aborted
    assert "customize_policy_blocked" in (final.error or "")
    assert "pre_final" in (final.error or "")


@pytest.mark.asyncio
async def test_governed_turn_shell_check_pre_final_passes_through_on_proceed(
    cfg_on: Path,
) -> None:
    """passed=true JSON ⇒ verdict=proceed ⇒ original EngineResult is yielded
    unchanged (no policy_blocked replacement)."""
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_wire_pre_final_pass",
            fires_at="pre_final",
            action="block",
            inline="echo '{\"passed\": true}'",
        ),
        path=cfg_on,
    )

    ctx = TurnContext(
        prompt="hi", session_id="sess-check-pf-ok", turn_id="turn-check-pf-ok"
    )
    items = [
        item
        async for item in run_governed_turn(
            ctx,
            runtime=_FakeRuntime(
                [
                    EngineResult(
                        terminal=Terminal.completed,
                        usage={"input_tokens": 1, "output_tokens": 1},
                        cost_usd=0.0,
                        session_id="sess-check-pf-ok",
                        turn_id="turn-check-pf-ok",
                    ),
                ]
            ),
        )
    ]

    terminals = [it for it in items if isinstance(it, EngineResult)]
    assert terminals and terminals[-1].terminal == Terminal.completed


@pytest.mark.asyncio
async def test_governed_turn_shell_check_off_path_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Master flag OFF ⇒ no collector created, no subprocess spawn."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    reset_shared_budget_for_tests()
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_off_wire",
            fires_at="pre_final",
            action="block",
            inline="exit 1",
        ),
        path=cfile,
    )

    async def fail_apply(rule, **kwargs):
        raise AssertionError(
            "shell_check runner must not be invoked when master flag is OFF"
        )

    monkeypatch.setattr(
        "magi_agent.customize.shell_check.apply_shell_check_rule", fail_apply
    )

    ctx = TurnContext(
        prompt="hi", session_id="sess-check-off", turn_id="turn-check-off"
    )
    items = [
        item
        async for item in run_governed_turn(
            ctx,
            runtime=_FakeRuntime(
                [
                    EngineResult(
                        terminal=Terminal.completed,
                        usage={"input_tokens": 1, "output_tokens": 1},
                        cost_usd=0.0,
                        session_id="sess-check-off",
                        turn_id="turn-check-off",
                    ),
                ]
            ),
        )
    ]

    terminals = [it for it in items if isinstance(it, EngineResult)]
    assert terminals and terminals[-1].terminal == Terminal.completed


# ---------------------------------------------------------------------------
# facades.execute_tool_with_hooks — before_tool_use wire
# ---------------------------------------------------------------------------


def test_facades_before_tool_use_shell_check_block_short_circuits_dispatch(
    cfg_on: Path,
) -> None:
    """before_tool_use shell_check + action=block + passed=false ⇒ blocked
    ToolResult is returned and the dispatcher is NEVER invoked."""
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_wire_before_block",
            fires_at="before_tool_use",
            action="block",
            inline="echo '{\"passed\": false, \"reason\": \"unsafe_command\"}'",
        ),
        path=cfg_on,
    )

    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(
        return_value=ToolResult(status="ok", output="should not run")
    )
    hook_bus = MagicMock(spec=HookBus)
    hook_bus.run = MagicMock(return_value=_continue())

    result, _before, _after = asyncio.run(
        execute_tool_with_hooks(
            dispatcher,
            hook_bus,
            tool_name="shell_exec",
            arguments={"command": "rm -rf /"},
            context=ToolContext(botId="b"),
            hook_context=HookContext(botId="b"),
            harness_state=build_default_resolved_harness_state(),
            mode="act",
        )
    )

    assert result.status == "blocked"
    assert dispatcher.dispatch.await_count == 0
    md = result.metadata or {}
    assert md.get("blocked_by") == "shell_check_rule"
    assert md.get("rule_id") == "cr_fexec2_wire_before_block"
    assert md.get("reason") == "unsafe_command"


def test_facades_before_tool_use_shell_check_audit_does_not_block(
    cfg_on: Path,
) -> None:
    """before_tool_use shell_check + action=audit + passed=false ⇒ dispatcher
    runs (audit-action never blocks even on failed verdict)."""
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_wire_before_audit",
            fires_at="before_tool_use",
            action="audit",
            inline="echo '{\"passed\": false, \"reason\": \"informational\"}'",
        ),
        path=cfg_on,
    )

    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(
        return_value=ToolResult(status="ok", output="real output")
    )
    hook_bus = MagicMock(spec=HookBus)
    hook_bus.run = MagicMock(return_value=_continue())

    result, _before, _after = asyncio.run(
        execute_tool_with_hooks(
            dispatcher,
            hook_bus,
            tool_name="shell_exec",
            arguments={"command": "ls"},
            context=ToolContext(botId="b"),
            hook_context=HookContext(botId="b"),
            harness_state=build_default_resolved_harness_state(),
            mode="act",
        )
    )

    assert result.status == "ok"
    assert result.output == "real output"
    assert dispatcher.dispatch.await_count == 1


def test_facades_before_tool_use_shell_check_passes_through_on_passed_true(
    cfg_on: Path,
) -> None:
    """passed=true ⇒ verdict=proceed ⇒ dispatcher runs normally."""
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_wire_before_pass",
            fires_at="before_tool_use",
            action="block",
            inline="echo '{\"passed\": true}'",
        ),
        path=cfg_on,
    )

    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(
        return_value=ToolResult(status="ok", output="real output")
    )
    hook_bus = MagicMock(spec=HookBus)
    hook_bus.run = MagicMock(return_value=_continue())

    result, _before, _after = asyncio.run(
        execute_tool_with_hooks(
            dispatcher,
            hook_bus,
            tool_name="shell_exec",
            arguments={"command": "ls"},
            context=ToolContext(botId="b"),
            hook_context=HookContext(botId="b"),
            harness_state=build_default_resolved_harness_state(),
            mode="act",
        )
    )

    assert result.status == "ok"
    assert dispatcher.dispatch.await_count == 1


def test_facades_before_tool_use_shell_check_off_path_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Master flag OFF ⇒ no subprocess invocation, dispatcher runs verbatim."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_off_facade",
            fires_at="before_tool_use",
            action="block",
            inline="exit 1",
        ),
        path=cfile,
    )

    async def fail_apply(rule, **kwargs):
        raise AssertionError(
            "shell_check runner must not be invoked when master flag is OFF"
        )

    monkeypatch.setattr(
        "magi_agent.customize.shell_check.apply_shell_check_rule", fail_apply
    )

    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(
        return_value=ToolResult(status="ok", output="real output")
    )
    hook_bus = MagicMock(spec=HookBus)
    hook_bus.run = MagicMock(return_value=_continue())

    result, _, _ = asyncio.run(
        execute_tool_with_hooks(
            dispatcher,
            hook_bus,
            tool_name="shell_exec",
            arguments={"command": "ls"},
            context=ToolContext(botId="b"),
            hook_context=HookContext(botId="b"),
            harness_state=build_default_resolved_harness_state(),
            mode="act",
        )
    )

    assert result.status == "ok"
    assert dispatcher.dispatch.await_count == 1
