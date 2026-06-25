"""F-EXEC1 firing tests: ``shell_command`` action at multiple lifecycle slots.

Drives :func:`magi_agent.facades.execute_tool_with_hooks` and
:mod:`magi_agent.customize.lifecycle_audit` end-to-end through a tmp
``customize.json`` + the triple-gated flag combination
(``MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED`` strict-truthy +
``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` + ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``).

Uses real ``true`` / ``false`` / ``echo`` commands so the assertions reflect
actual subprocess invocations (not mocks).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from magi_agent.customize.lifecycle_audit import (
    run_shell_command_at_after_compaction,
    run_shell_command_at_on_user_prompt_submit,
    run_shell_command_at_pre_final,
)
from magi_agent.customize.store import set_custom_rule
from magi_agent.facades import execute_tool_with_hooks
from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.hooks.bus import HookBus, HookBusObservation, HookBusRunResult
from magi_agent.hooks.context import HookContext
from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.result import ToolResult


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)


def _rule(
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
            "kind": "shell_command",
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


@pytest.fixture
def cfg_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    # Avoid stale runtime profile interaction.
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


def test_before_tool_use_block_rule_blocks_dispatch(cfg_on: Path) -> None:
    set_custom_rule(
        _rule(
            rid="cr_fexec1_block_before",
            fires_at="before_tool_use",
            action="block",
            inline="exit 1",
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
            arguments={"command": "ls"},
            context=ToolContext(botId="b"),
            hook_context=HookContext(botId="b"),
            harness_state=build_default_resolved_harness_state(),
            mode="act",
        )
    )

    # Dispatch should be short-circuited; status is "blocked" and metadata
    # carries the rule id (the dispatcher mock should NOT have been called).
    assert result.status == "blocked"
    assert dispatcher.dispatch.await_count == 0
    md = result.metadata or {}
    assert md.get("blocked_by") == "shell_command_rule"
    assert md.get("rule_id") == "cr_fexec1_block_before"


def test_before_tool_use_audit_rule_does_not_block(cfg_on: Path) -> None:
    set_custom_rule(
        _rule(
            rid="cr_fexec1_audit_before",
            fires_at="before_tool_use",
            action="audit",
            inline="exit 1",  # non-zero but audit-action → never blocks
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


def test_after_tool_use_audit_runs_subprocess_and_preserves_output(
    cfg_on: Path,
) -> None:
    set_custom_rule(
        _rule(
            rid="cr_fexec1_after_audit",
            fires_at="after_tool_use",
            action="audit",
            inline="exit 0",
        ),
        path=cfg_on,
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

    # After-tool audit never mutates the dispatched result.
    assert result.status == "ok"
    assert result.output == "real output"


def test_off_path_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Master flag OFF → no subprocess, no block, dispatch returns verbatim."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(
        _rule(
            rid="cr_fexec1_off",
            fires_at="before_tool_use",
            action="block",
            inline="exit 1",
        ),
        path=cfile,
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


def test_pre_final_block_on_nonzero_exit(cfg_on: Path) -> None:
    set_custom_rule(
        _rule(
            rid="cr_fexec1_pre_final_block",
            fires_at="pre_final",
            action="block",
            inline="exit 2",
        ),
        path=cfg_on,
    )
    audits, verdict = asyncio.run(run_shell_command_at_pre_final(draft_text="ok"))
    assert verdict == "block"
    assert audits and audits[0]["status"] == "executed"
    assert audits[0]["exit_code"] == 2
    assert audits[0]["passed"] is False


def test_pre_final_audit_on_nonzero_exit_does_not_block(cfg_on: Path) -> None:
    set_custom_rule(
        _rule(
            rid="cr_fexec1_pre_final_audit",
            fires_at="pre_final",
            action="audit",
            inline="exit 1",
        ),
        path=cfg_on,
    )
    audits, verdict = asyncio.run(run_shell_command_at_pre_final(draft_text="ok"))
    assert verdict == "proceed"
    assert audits and audits[0]["status"] == "executed"
    assert audits[0]["exit_code"] == 1


def test_after_compaction_audit_runs_subprocess(cfg_on: Path) -> None:
    set_custom_rule(
        _rule(
            rid="cr_fexec1_after_compact",
            fires_at="after_compaction",
            action="audit",
            inline="echo done",
        ),
        path=cfg_on,
    )
    audits = asyncio.run(
        run_shell_command_at_after_compaction(summary_text="42 kept")
    )
    assert audits
    assert audits[0]["status"] == "executed"
    assert audits[0]["exit_code"] == 0
    assert "done" in audits[0]["stdout_truncated"]


def test_budget_exhaustion_short_circuits_fan_out(cfg_on: Path) -> None:
    """remaining_budget=0 → single budget_exhausted record, no spawn."""
    set_custom_rule(
        _rule(
            rid="cr_fexec1_budget_test",
            fires_at="on_user_prompt_submit",
            action="audit",
            inline="echo ran",
        ),
        path=cfg_on,
    )
    audits = asyncio.run(
        run_shell_command_at_on_user_prompt_submit(
            prompt_text="hello",
            remaining_budget=0,
        )
    )
    assert len(audits) == 1
    assert audits[0]["status"] == "budget_exhausted"


def test_budget_remaining_allows_first_call(cfg_on: Path) -> None:
    set_custom_rule(
        _rule(
            rid="cr_fexec1_budget_first",
            fires_at="on_user_prompt_submit",
            action="audit",
            inline="echo ran",
        ),
        path=cfg_on,
    )
    audits = asyncio.run(
        run_shell_command_at_on_user_prompt_submit(
            prompt_text="hello",
            remaining_budget=1,
        )
    )
    assert audits
    assert audits[0]["status"] == "executed"
