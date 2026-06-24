"""F-MUT2 firing test: ``output_rewrite`` mutator at ``after_tool_use``.

Drives :func:`magi_agent.facades.execute_tool_with_hooks` end-to-end through a
tmp ``customize.json`` + the triple-gated flag combination
(``MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED`` strict-truthy +
``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` + ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``).
Proves the rewrite consumer wire is REAL — the dispatcher returns text with a
sensitive token, and the RETURNED ToolResult arrives at the caller with the
token redacted (not just policy loaded into memory).
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
from magi_agent.hooks.result import HookResult
from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.result import ToolResult

_RULE_ID = "cr_fmut2_redact_aws_key"


def _rule(**over) -> dict:
    rule = {
        "id": _RULE_ID,
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "output_rewrite",
            "payload": {
                "mode": "redact",
                "pattern": "AKIA[0-9A-Z]{16}",
                "replacement": "***",
            },
        },
        "firesAt": "after_tool_use",
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
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_rule(), path=cfile)
    return cfile


def test_output_rewrite_redacts_aws_key_in_tool_result(cfg_on: Path) -> None:
    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(
        return_value=ToolResult(
            status="ok", output="hello AKIABCDEFGHIJKLMNOPQ world"
        )
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

    # ON-path: the redaction actually happened on the returned ToolResult.
    assert result.output == "hello *** world"
    # Status preserved.
    assert result.status == "ok"


def test_output_rewrite_inert_when_master_flag_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_rule(), path=cfile)

    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(
        return_value=ToolResult(
            status="ok", output="hello AKIABCDEFGHIJKLMNOPQ world"
        )
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

    # Byte-identical to today's behavior — the rule is loaded but the master
    # flag is OFF so the runtime never invokes the applier.
    assert result.output == "hello AKIABCDEFGHIJKLMNOPQ world"


def test_output_rewrite_inert_when_no_rules_authored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    # no set_custom_rule — empty customize

    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(
        return_value=ToolResult(
            status="ok", output="hello AKIABCDEFGHIJKLMNOPQ world"
        )
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

    assert result.output == "hello AKIABCDEFGHIJKLMNOPQ world"


def test_output_rewrite_does_not_fire_for_non_matching_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """toolMatch.include="shell_exec" narrows firing; "fetch_url" stays untouched."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    rule = _rule()
    rule["what"]["payload"]["toolMatch"] = {"include": ["shell_exec"]}
    set_custom_rule(rule, path=cfile)

    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(
        return_value=ToolResult(
            status="ok", output="hello AKIABCDEFGHIJKLMNOPQ world"
        )
    )

    hook_bus = MagicMock(spec=HookBus)
    hook_bus.run = MagicMock(return_value=_continue())

    result, _before, _after = asyncio.run(
        execute_tool_with_hooks(
            dispatcher,
            hook_bus,
            tool_name="fetch_url",
            arguments={"url": "https://example.com"},
            context=ToolContext(botId="b"),
            hook_context=HookContext(botId="b"),
            harness_state=build_default_resolved_harness_state(),
            mode="act",
        )
    )

    # fetch_url not in include → no rewrite.
    assert result.output == "hello AKIABCDEFGHIJKLMNOPQ world"


def _replace_after_tool(value: dict) -> HookBusRunResult:
    """Construct an AFTER_TOOL_USE replace HookBusRunResult carrying ``value``."""
    return HookBusRunResult(
        final_action="replace",
        results=(HookResult(action="replace", value=value),),
        observation=HookBusObservation(),
        harness_state=build_default_resolved_harness_state(),
    )


def _hookbus_replace_after(value: dict) -> MagicMock:
    bus = MagicMock(spec=HookBus)

    def _run(point, **_kwargs):  # type: ignore[no-untyped-def]
        from magi_agent.hooks.manifest import HookPoint

        if point == HookPoint.AFTER_TOOL_USE:
            return _replace_after_tool(value)
        return _continue()

    bus.run = MagicMock(side_effect=_run)
    return bus


def test_after_tool_use_hook_replace_overlays_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AFTER_TOOL_USE replace consumer projects AfterToolUseReplace.result_text → ToolResult.output."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "0")
    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(return_value=ToolResult(status="ok", output="raw"))

    bus = _hookbus_replace_after({"result_text": "scrubbed"})

    result, _b, _a = asyncio.run(
        execute_tool_with_hooks(
            dispatcher,
            bus,
            tool_name="shell_exec",
            arguments={"command": "ls"},
            context=ToolContext(botId="b"),
            hook_context=HookContext(botId="b"),
            harness_state=build_default_resolved_harness_state(),
            mode="act",
        )
    )

    assert result.output == "scrubbed"
    assert result.status == "ok"


def test_after_tool_use_hook_replace_overlays_status_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AfterToolUseReplace.status + structured_data overlay into ToolResult.status + metadata."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "0")
    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(return_value=ToolResult(status="ok", output="raw"))

    bus = _hookbus_replace_after(
        {"status": "error", "structured_data": {"reason": "blocked-by-hook"}}
    )

    result, _b, _a = asyncio.run(
        execute_tool_with_hooks(
            dispatcher,
            bus,
            tool_name="shell_exec",
            arguments={"command": "ls"},
            context=ToolContext(botId="b"),
            hook_context=HookContext(botId="b"),
            harness_state=build_default_resolved_harness_state(),
            mode="act",
        )
    )

    assert result.status == "error"
    assert result.metadata == {"reason": "blocked-by-hook"}
    assert result.output == "raw"


def test_after_tool_use_hook_replace_malformed_value_fails_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-dict / bad-schema replace value falls back to the original ToolResult."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "0")
    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(return_value=ToolResult(status="ok", output="raw"))

    bus = _hookbus_replace_after({"unknown_field": 1})

    result, _b, _a = asyncio.run(
        execute_tool_with_hooks(
            dispatcher,
            bus,
            tool_name="shell_exec",
            arguments={"command": "ls"},
            context=ToolContext(botId="b"),
            hook_context=HookContext(botId="b"),
            harness_state=build_default_resolved_harness_state(),
            mode="act",
        )
    )

    # extra="forbid" → coerce returns None → original result preserved.
    assert result.output == "raw"
    assert result.status == "ok"


def test_after_tool_use_hook_replace_composes_with_rule_redact(
    cfg_on: Path,
) -> None:
    """Hook overlays text first, then rule applies redact on the overlaid text."""
    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(return_value=ToolResult(status="ok", output="raw"))

    # Hook overlays text that contains the secret; rule then redacts.
    bus = _hookbus_replace_after({"result_text": "hook: AKIABCDEFGHIJKLMNOPQ tail"})

    result, _b, _a = asyncio.run(
        execute_tool_with_hooks(
            dispatcher,
            bus,
            tool_name="shell_exec",
            arguments={"command": "ls"},
            context=ToolContext(botId="b"),
            hook_context=HookContext(botId="b"),
            harness_state=build_default_resolved_harness_state(),
            mode="act",
        )
    )

    assert result.output == "hook: *** tail"
