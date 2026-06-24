"""F-MUT1 firing test: ``prompt_injection`` mutator at ``before_tool_use``.

Drives :func:`magi_agent.facades.execute_tool_with_hooks` end-to-end through a
tmp ``customize.json`` + the triple-gated flag combination
(``MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED`` strict-truthy +
``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` + ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``).
Proves four things together:

1. A persisted ``prompt_injection`` rule with ``firesAt == "before_tool_use"``
   is loaded and applied before the dispatcher sees the args.
2. The dispatcher is invoked with the MUTATED arguments.
3. Optional ``condition.tool`` narrows firing to the right tool.
4. With the master flag OFF the rule is silently inert (dispatcher receives
   the original args, byte-identical).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from magi_agent.customize.store import set_custom_rule
from magi_agent.facades import execute_tool_with_hooks
from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.hooks.bus import HookBus, HookBusObservation, HookBusRunResult
from magi_agent.hooks.context import HookContext
from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.result import ToolResult

_RULE_ID = "cr_fmut1_shell_exec_dry_run"


def _rule(**over) -> dict:
    rule = {
        "id": _RULE_ID,
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "prompt_injection",
            "payload": {
                "mode": "append",
                "target_arg_key": "command",
                "value": " --dry-run",
                "condition": {"tool": "shell_exec"},
            },
        },
        "firesAt": "before_tool_use",
        "action": "audit",
    }
    rule.update(over)
    return rule


def _continue() -> HookBusRunResult:
    return HookBusRunResult(
        final_action="continue",
        results=(),
        observation=HookBusObservation(),
        harness_state=build_default_resolved_harness_state(),
    )


@pytest.fixture
def cfg_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_rule(), path=cfile)
    return cfile


def test_prompt_injection_appends_dry_run_to_shell_exec_command(
    cfg_on: Path,
) -> None:
    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(return_value=ToolResult(status="ok"))

    hook_bus = MagicMock(spec=HookBus)
    hook_bus.run = MagicMock(return_value=_continue())

    asyncio.run(
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

    dispatch_args = dispatcher.dispatch.call_args
    assert dispatch_args.args[1] == {"command": "ls --dry-run"}


def test_prompt_injection_does_not_fire_for_non_matching_tool(
    cfg_on: Path,
) -> None:
    """Condition.tool="shell_exec" narrows firing; "fetch_url" stays unmodified."""
    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(return_value=ToolResult(status="ok"))

    hook_bus = MagicMock(spec=HookBus)
    hook_bus.run = MagicMock(return_value=_continue())

    asyncio.run(
        execute_tool_with_hooks(
            dispatcher,
            hook_bus,
            tool_name="fetch_url",
            arguments={"command": "fetch"},
            context=ToolContext(botId="b"),
            hook_context=HookContext(botId="b"),
            harness_state=build_default_resolved_harness_state(),
            mode="act",
        )
    )

    dispatch_args = dispatcher.dispatch.call_args
    assert dispatch_args.args[1] == {"command": "fetch"}


def test_prompt_injection_inert_when_master_flag_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_rule(), path=cfile)

    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(return_value=ToolResult(status="ok"))

    hook_bus = MagicMock(spec=HookBus)
    hook_bus.run = MagicMock(return_value=_continue())

    asyncio.run(
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

    dispatch_args = dispatcher.dispatch.call_args
    # Byte-identical to today's behavior.
    assert dispatch_args.args[1] == {"command": "ls"}


def test_prompt_injection_inert_when_no_rules_authored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    # no set_custom_rule — empty customize

    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(return_value=ToolResult(status="ok"))

    hook_bus = MagicMock(spec=HookBus)
    hook_bus.run = MagicMock(return_value=_continue())

    asyncio.run(
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

    dispatch_args = dispatcher.dispatch.call_args
    assert dispatch_args.args[1] == {"command": "ls"}


def test_prompt_injection_does_not_fire_when_before_hook_blocks(
    cfg_on: Path,
) -> None:
    """If beforeToolUse hook BLOCKS, dispatcher must NOT be called at all —
    a blocked tool stays blocked, even if a prompt_injection rule is authored."""
    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(return_value=ToolResult(status="ok"))

    from magi_agent.hooks.result import HookResult

    block_result = HookBusRunResult(
        final_action="block",
        results=(HookResult(action="block", reason="denied"),),
        observation=HookBusObservation(blocked_by=("test-hook",)),
        harness_state=build_default_resolved_harness_state(),
    )
    hook_bus = MagicMock(spec=HookBus)
    hook_bus.run = MagicMock(return_value=block_result)

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

    assert result.status == "blocked"
    dispatcher.dispatch.assert_not_called()
