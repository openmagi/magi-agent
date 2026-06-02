"""Integration tests for LLM hook dispatch via HookBus and YAML config.

Covers:
1. YAML parsing: LLM hook entry creates valid HookManifest with execution_type="llm"
2. YAML parsing: prompt_template and max_prompt_tokens are set from YAML
3. YAML parsing: LLM hook without prompt_template raises ValueError
4. HookBus dispatch: LLM hook dispatched through bus calls LLMHookExecutor
5. Evidence: LLM hook result includes llm_hook_classification metadata
"""
from __future__ import annotations

import os
import tempfile
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.hooks.bus import HookBus, RegisteredHook
from magi_agent.hooks.context import HookContext
from magi_agent.hooks.executors import HookExecutor, get_executor
from magi_agent.hooks.executors.llm_executor import LLMHookExecutor
from magi_agent.hooks.external_config import (
    ExternalHookConfig,
    load_external_hooks_from_yaml,
)
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.tools.manifest import ToolSource

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SOURCE = ToolSource(kind="builtin", package="test.fixtures")

_CONTEXT = HookContext(
    botId="bot-test-llm",
    sessionId="sess-llm-001",
    turnId="turn-llm-001",
    channel="web",
)

_HARNESS = build_default_resolved_harness_state()


def _llm_manifest(name: str = "llm-hook", **kwargs) -> HookManifest:
    defaults = dict(
        name=name,
        point=HookPoint.BEFORE_TOOL_USE,
        description="An LLM hook",
        source=_SOURCE,
        executionType="llm",
        promptTemplate="Evaluate safety: {tool_input}",
    )
    defaults.update(kwargs)
    return HookManifest(**defaults)


def _make_mock_executor(result: HookResult) -> MagicMock:
    executor = MagicMock(spec=HookExecutor)
    executor.execute = AsyncMock(return_value=result)
    return executor


# ---------------------------------------------------------------------------
# 1. YAML parsing: LLM hook entry creates valid HookManifest
# ---------------------------------------------------------------------------

def test_yaml_llm_hook_creates_valid_manifest():
    """YAML with execution_type: llm must produce a HookManifest with execution_type='llm'."""
    yaml_content = textwrap.dedent("""\
        hooks:
          - name: "custom-safety-check"
            point: "beforeToolUse"
            execution_type: "llm"
            prompt_template: "Evaluate if this is safe: {tool_input}"
            max_prompt_tokens: 1500
            timeoutMs: 3000
            failOpen: true
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        yaml_path = f.name

    try:
        hooks = load_external_hooks_from_yaml(yaml_path)
    finally:
        os.unlink(yaml_path)

    assert len(hooks) == 1
    manifest = hooks[0].manifest
    assert manifest.name == "custom-safety-check"
    assert manifest.execution_type == "llm"
    assert manifest.point == HookPoint.BEFORE_TOOL_USE
    assert manifest.fail_open is True
    assert manifest.timeout_ms == 3000


# ---------------------------------------------------------------------------
# 2. YAML parsing: prompt_template and max_prompt_tokens set from YAML
# ---------------------------------------------------------------------------

def test_yaml_llm_hook_sets_prompt_template_and_max_tokens():
    """prompt_template and max_prompt_tokens must be parsed from YAML entries."""
    yaml_content = textwrap.dedent("""\
        hooks:
          - name: "safety-llm"
            point: "beforeToolUse"
            execution_type: "llm"
            prompt_template: "Is this tool call safe? Tool: {tool_name}, Input: {tool_input}"
            max_prompt_tokens: 2500
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        yaml_path = f.name

    try:
        hooks = load_external_hooks_from_yaml(yaml_path)
    finally:
        os.unlink(yaml_path)

    assert len(hooks) == 1
    manifest = hooks[0].manifest
    assert manifest.prompt_template == "Is this tool call safe? Tool: {tool_name}, Input: {tool_input}"
    assert manifest.max_prompt_tokens == 2500


# ---------------------------------------------------------------------------
# 3. YAML parsing: LLM hook without prompt_template raises ValueError
# ---------------------------------------------------------------------------

def test_yaml_llm_hook_without_prompt_template_is_skipped():
    """An LLM hook entry missing prompt_template must fail validation and be skipped."""
    yaml_content = textwrap.dedent("""\
        hooks:
          - name: "bad-llm-hook"
            point: "beforeToolUse"
            execution_type: "llm"
            max_prompt_tokens: 1500
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        yaml_path = f.name

    try:
        hooks = load_external_hooks_from_yaml(yaml_path)
    finally:
        os.unlink(yaml_path)

    # The entry should be skipped (logged as warning), not raise
    assert len(hooks) == 0


def test_manifest_llm_without_prompt_template_raises():
    """Directly constructing an LLM manifest without prompt_template must raise ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        HookManifest(
            name="bad-llm",
            point=HookPoint.BEFORE_TOOL_USE,
            description="missing template",
            source=_SOURCE,
            executionType="llm",
        )
    errors = exc_info.value.errors()
    assert any("prompt_template" in str(e).lower() for e in errors)


# ---------------------------------------------------------------------------
# 4. HookBus dispatch: LLM hook dispatched through bus calls LLMHookExecutor
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_llm_hook_dispatches_to_executor_in_run_async():
    """run_async() must route LLM hooks to the LLM executor."""
    expected = HookResult(
        action="permission_decision",
        decision="approve",
        reason="safe",
        metadata={"llm_hook_classification": {"model": "test", "decision": "approve"}},
    )
    mock_executor = _make_mock_executor(expected)

    hook = RegisteredHook(manifest=_llm_manifest(), handler=AsyncMock())
    bus = HookBus(hooks=(hook,), llm_executor=mock_executor)

    result = await bus.run_async(
        point=HookPoint.BEFORE_TOOL_USE, context=_CONTEXT, harness_state=_HARNESS
    )

    mock_executor.execute.assert_awaited_once_with(_CONTEXT, hook.manifest)
    assert result.final_action == "continue"


def test_llm_hook_skipped_in_sync_run_with_warning(caplog):
    """run() must skip LLM hooks and log a warning (they require async)."""
    import logging

    hook = RegisteredHook(manifest=_llm_manifest("sync-llm-hook"), handler=MagicMock())
    bus = HookBus(hooks=(hook,))

    with caplog.at_level(logging.WARNING, logger="magi_agent.hooks.bus"):
        result = bus.run(
            point=HookPoint.BEFORE_TOOL_USE, context=_CONTEXT, harness_state=_HARNESS
        )

    assert result.final_action == "continue"
    assert any("sync-llm-hook" in rec.message and "run_async" in rec.message for rec in caplog.records)


@pytest.mark.anyio
async def test_missing_llm_executor_returns_continue(caplog):
    """When llm_executor is None, run_async() must log a warning and return continue."""
    import logging

    hook = RegisteredHook(manifest=_llm_manifest("no-executor-llm"), handler=AsyncMock())
    with patch(
        "magi_agent.hooks.bus.get_executor",
        side_effect=lambda t: None,
    ):
        bus = HookBus(hooks=(hook,))

    with caplog.at_level(logging.WARNING, logger="magi_agent.hooks.bus"):
        result = await bus.run_async(
            point=HookPoint.BEFORE_TOOL_USE, context=_CONTEXT, harness_state=_HARNESS
        )

    assert result.final_action == "continue"
    assert any("no llm executor" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 5. Evidence: LLM hook result includes llm_hook_classification metadata
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_llm_hook_result_includes_classification_metadata():
    """LLM hook results dispatched through bus must carry llm_hook_classification metadata."""
    classification_meta = {
        "model": "gemini-2.0-flash",
        "decision": "approve",
        "latency_ms": 42.5,
        "raw_response": "ALLOW - this is safe",
        "prompt_chars": 120,
    }
    expected = HookResult(
        action="permission_decision",
        decision="approve",
        reason="this is safe",
        metadata={"llm_hook_classification": classification_meta},
    )
    mock_executor = _make_mock_executor(expected)

    hook = RegisteredHook(manifest=_llm_manifest("evidence-hook"), handler=AsyncMock())
    bus = HookBus(hooks=(hook,), llm_executor=mock_executor)

    result = await bus.run_async(
        point=HookPoint.BEFORE_TOOL_USE, context=_CONTEXT, harness_state=_HARNESS
    )

    assert len(result.results) == 1
    hook_result = result.results[0]
    assert "llm_hook_classification" in hook_result.metadata
    meta = hook_result.metadata["llm_hook_classification"]
    assert meta["model"] == "gemini-2.0-flash"
    assert meta["decision"] == "approve"
    assert meta["latency_ms"] == 42.5


# ---------------------------------------------------------------------------
# 6. HookBus auto-resolves LLM executor from registry
# ---------------------------------------------------------------------------

def test_hookbus_auto_resolves_llm_executor_from_registry():
    """HookBus() with no executor kwargs must pick up the LLM executor from get_executor()."""
    bus = HookBus()
    assert isinstance(bus._llm_executor, LLMHookExecutor)


# ---------------------------------------------------------------------------
# 7. LLM executor registered in registry
# ---------------------------------------------------------------------------

def test_get_executor_returns_llm_executor():
    """get_executor('llm') must return the registered LLMHookExecutor instance."""
    executor = get_executor("llm")
    assert isinstance(executor, LLMHookExecutor)


# ---------------------------------------------------------------------------
# 8. MAGI_LLM_HOOKS_ENABLED env gate
# ---------------------------------------------------------------------------

def test_llm_hooks_enabled_defaults_to_true():
    """When MAGI_LLM_HOOKS_ENABLED is not set, llm_hooks_enabled defaults to True."""
    env = {k: v for k, v in os.environ.items() if k != "MAGI_LLM_HOOKS_ENABLED"}
    with patch.dict(os.environ, env, clear=True):
        config = ExternalHookConfig.from_env()
    assert config.llm_hooks_enabled is True


def test_llm_hooks_disabled_via_env():
    """Setting MAGI_LLM_HOOKS_ENABLED=false must set llm_hooks_enabled to False."""
    with patch.dict(os.environ, {"MAGI_LLM_HOOKS_ENABLED": "false", "MAGI_EXTERNAL_HOOKS_ENABLED": "true"}):
        config = ExternalHookConfig.from_env()
    assert config.enabled is True
    assert config.llm_hooks_enabled is False


def test_yaml_llm_hook_filtered_when_disabled():
    """LLM hooks in YAML must be filtered out when llm_hooks_enabled is False."""
    yaml_content = textwrap.dedent("""\
        hooks:
          - name: "cmd-hook"
            point: "afterToolUse"
            execution_type: "command"
            command: "/usr/local/bin/check.sh"
          - name: "llm-safety"
            point: "beforeToolUse"
            execution_type: "llm"
            prompt_template: "Check safety: {tool_input}"
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        yaml_path = f.name

    try:
        config = ExternalHookConfig(enabled=True, llm_hooks_enabled=False)
        hooks = load_external_hooks_from_yaml(yaml_path, config=config)
    finally:
        os.unlink(yaml_path)

    # Only the command hook should remain; the LLM hook should be filtered out.
    assert len(hooks) == 1
    assert hooks[0].manifest.name == "cmd-hook"
    assert hooks[0].manifest.execution_type == "command"
