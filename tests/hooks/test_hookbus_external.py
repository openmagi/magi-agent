"""Integration tests for HookBus external hook dispatch (PR 4).

Covers:
1.  Handler hooks work unchanged via run() (synchronous path)
2.  Handler hooks work unchanged via run_async()
3.  Command hook dispatches to CommandHookExecutor via run_async()
4.  HTTP hook dispatches to HttpHookExecutor via run_async()
5.  Command hook skipped in run() with warning logged
6.  HTTP hook skipped in run() with warning logged
7.  Missing command executor returns continue with warning
8.  Missing http executor returns continue with warning
9.  YAML config loading creates correct manifests (command + http)
10. Env-var substitution in YAML http_headers works
11. ExternalHookConfig reads from MAGI_EXTERNAL_HOOKS_ENABLED env var
12. load_external_hooks_from_yaml returns empty list for missing file
13. HookBus auto-resolves executors from registry by default
"""
from __future__ import annotations

import os
import tempfile
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.hooks.bus import HookBus, RegisteredHook
from magi_agent.hooks.context import HookContext
from magi_agent.hooks.executors import HookExecutor, get_executor
from magi_agent.hooks.external_config import (
    ExternalHookConfig,
    _resolve_env_vars,
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
    botId="bot-test-external",
    sessionId="sess-ext-001",
    turnId="turn-ext-001",
    channel="web",
)

_HARNESS = build_default_resolved_harness_state()


def _handler_manifest(name: str = "handler-hook") -> HookManifest:
    return HookManifest(
        name=name,
        point=HookPoint.BEFORE_TOOL_USE,
        description="A handler hook",
        source=_SOURCE,
        executionType="handler",
    )


def _command_manifest(name: str = "cmd-hook") -> HookManifest:
    return HookManifest(
        name=name,
        point=HookPoint.BEFORE_TOOL_USE,
        description="A command hook",
        source=_SOURCE,
        executionType="command",
        command="/usr/local/bin/check.sh",
    )


def _http_manifest(name: str = "http-hook") -> HookManifest:
    return HookManifest(
        name=name,
        point=HookPoint.BEFORE_TOOL_USE,
        description="An http hook",
        source=_SOURCE,
        executionType="http",
        url="https://security.internal/hooks/magi",
    )


def _make_mock_executor(result: HookResult) -> MagicMock:
    """Return a mock that satisfies the HookExecutor protocol."""
    executor = MagicMock(spec=HookExecutor)
    executor.execute = AsyncMock(return_value=result)
    return executor


# ---------------------------------------------------------------------------
# 1. Handler hooks work unchanged via run() (synchronous path)
# ---------------------------------------------------------------------------

def test_handler_hook_run_sync_unchanged():
    """Existing handler hooks must work identically after the PR 4 changes."""
    def _handler(ctx: HookContext) -> HookResult:
        return HookResult(action="continue", reason="handler-ran")

    hook = RegisteredHook(manifest=_handler_manifest(), handler=_handler)
    bus = HookBus(hooks=(hook,))
    result = bus.run(point=HookPoint.BEFORE_TOOL_USE, context=_CONTEXT, harness_state=_HARNESS)
    assert result.final_action == "continue"
    assert any(r.reason == "handler-ran" for r in result.results)


# ---------------------------------------------------------------------------
# 2. Handler hooks work unchanged via run_async()
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_handler_hook_run_async_unchanged():
    """Existing async handler hooks must work identically via run_async()."""
    async def _async_handler(ctx: HookContext) -> HookResult:
        return HookResult(action="continue", reason="async-handler-ran")

    hook = RegisteredHook(manifest=_handler_manifest("async-handler-hook"), handler=_async_handler)
    bus = HookBus(hooks=(hook,))
    result = await bus.run_async(
        point=HookPoint.BEFORE_TOOL_USE, context=_CONTEXT, harness_state=_HARNESS
    )
    assert result.final_action == "continue"
    assert any(r.reason == "async-handler-ran" for r in result.results)


# ---------------------------------------------------------------------------
# 3. Command hook dispatches to CommandHookExecutor via run_async()
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_command_hook_dispatches_to_executor_in_run_async():
    """run_async() must route command hooks to the command executor."""
    expected = HookResult(action="continue", reason="cmd-executed")
    mock_executor = _make_mock_executor(expected)

    hook = RegisteredHook(manifest=_command_manifest(), handler=AsyncMock())
    bus = HookBus(hooks=(hook,), command_executor=mock_executor)

    result = await bus.run_async(
        point=HookPoint.BEFORE_TOOL_USE, context=_CONTEXT, harness_state=_HARNESS
    )

    mock_executor.execute.assert_awaited_once_with(_CONTEXT, hook.manifest)
    assert result.final_action == "continue"
    assert result.results[0].reason == "cmd-executed"


# ---------------------------------------------------------------------------
# 4. HTTP hook dispatches to HttpHookExecutor via run_async()
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_http_hook_dispatches_to_executor_in_run_async():
    """run_async() must route http hooks to the http executor."""
    expected = HookResult(action="block", reason="security-denied")
    mock_executor = _make_mock_executor(expected)

    hook = RegisteredHook(manifest=_http_manifest(), handler=AsyncMock())
    bus = HookBus(hooks=(hook,), http_executor=mock_executor)

    result = await bus.run_async(
        point=HookPoint.BEFORE_TOOL_USE, context=_CONTEXT, harness_state=_HARNESS
    )

    mock_executor.execute.assert_awaited_once_with(_CONTEXT, hook.manifest)
    assert result.final_action == "block"


# ---------------------------------------------------------------------------
# 5. Command hook skipped in run() with warning logged
# ---------------------------------------------------------------------------

def test_command_hook_skipped_in_run_with_warning(caplog):
    """run() must skip command hooks and log a warning."""
    import logging

    hook = RegisteredHook(manifest=_command_manifest(), handler=MagicMock())
    bus = HookBus(hooks=(hook,))

    with caplog.at_level(logging.WARNING, logger="magi_agent.hooks.bus"):
        result = bus.run(
            point=HookPoint.BEFORE_TOOL_USE, context=_CONTEXT, harness_state=_HARNESS
        )

    # Skipped hook returns continue (not block)
    assert result.final_action == "continue"
    assert any("cmd-hook" in rec.message and "run_async" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 6. HTTP hook skipped in run() with warning logged
# ---------------------------------------------------------------------------

def test_http_hook_skipped_in_run_with_warning(caplog):
    """run() must skip http hooks and log a warning."""
    import logging

    hook = RegisteredHook(manifest=_http_manifest(), handler=MagicMock())
    bus = HookBus(hooks=(hook,))

    with caplog.at_level(logging.WARNING, logger="magi_agent.hooks.bus"):
        result = bus.run(
            point=HookPoint.BEFORE_TOOL_USE, context=_CONTEXT, harness_state=_HARNESS
        )

    assert result.final_action == "continue"
    assert any("http-hook" in rec.message and "run_async" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 7. Missing command executor returns continue with warning
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_missing_command_executor_returns_continue(caplog):
    """When command_executor is None, run_async() must log a warning and return continue."""
    import logging

    hook = RegisteredHook(manifest=_command_manifest("no-executor-cmd"), handler=AsyncMock())
    # Patch the registry to return None so auto-resolve gives None for all executor types.
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
    assert any("no command executor" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 8. Missing http executor returns continue with warning
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_missing_http_executor_returns_continue(caplog):
    """When http_executor is None, run_async() must log a warning and return continue."""
    import logging

    hook = RegisteredHook(manifest=_http_manifest("no-executor-http"), handler=AsyncMock())
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
    assert any("no http executor" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 9. YAML config loading creates correct manifests (command + http)
# ---------------------------------------------------------------------------

def test_load_external_hooks_from_yaml_creates_manifests():
    """load_external_hooks_from_yaml must create RegisteredHook instances with correct manifests."""
    yaml_content = textwrap.dedent("""\
        hooks:
          - name: "ci-lint-check"
            point: "afterToolUse"
            matcher: "Edit"
            execution_type: "command"
            command: "/usr/local/bin/lint-check.sh"
            timeoutMs: 10000
            failOpen: true
          - name: "security-webhook"
            point: "beforeToolUse"
            execution_type: "http"
            url: "https://security.internal/hooks/magi"
            http_headers:
              X-Custom: "static-value"
            timeoutMs: 5000
            failOpen: false
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        yaml_path = f.name

    try:
        hooks = load_external_hooks_from_yaml(yaml_path)
    finally:
        os.unlink(yaml_path)

    assert len(hooks) == 2

    cmd_hook = hooks[0]
    assert cmd_hook.manifest.name == "ci-lint-check"
    assert cmd_hook.manifest.execution_type == "command"
    assert cmd_hook.manifest.command == "/usr/local/bin/lint-check.sh"
    assert cmd_hook.manifest.timeout_ms == 10000
    assert cmd_hook.manifest.fail_open is True
    assert cmd_hook.manifest.point == HookPoint.AFTER_TOOL_USE

    http_hook = hooks[1]
    assert http_hook.manifest.name == "security-webhook"
    assert http_hook.manifest.execution_type == "http"
    assert http_hook.manifest.url == "https://security.internal/hooks/magi"
    assert http_hook.manifest.http_headers == {"X-Custom": "static-value"}
    assert http_hook.manifest.fail_open is False
    assert http_hook.manifest.point == HookPoint.BEFORE_TOOL_USE


# ---------------------------------------------------------------------------
# 10. Env-var substitution in YAML http_headers works
# ---------------------------------------------------------------------------

def test_load_yaml_env_var_substitution_in_headers():
    """${MAGI_HOOK_*} in http_headers must be resolved from the environment."""
    yaml_content = textwrap.dedent("""\
        hooks:
          - name: "auth-webhook"
            point: "beforeToolUse"
            execution_type: "http"
            url: "https://example.com/hook"
            http_headers:
              Authorization: "Bearer ${MAGI_HOOK_SECURITY_TOKEN}"
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        yaml_path = f.name

    try:
        with patch.dict(os.environ, {"MAGI_HOOK_SECURITY_TOKEN": "super-secret-123"}):
            hooks = load_external_hooks_from_yaml(yaml_path)
    finally:
        os.unlink(yaml_path)

    assert len(hooks) == 1
    assert hooks[0].manifest.http_headers == {"Authorization": "Bearer super-secret-123"}


# ---------------------------------------------------------------------------
# 11. ExternalHookConfig reads from MAGI_EXTERNAL_HOOKS_ENABLED env var
# ---------------------------------------------------------------------------

def test_external_hook_config_enabled_from_env():
    """ExternalHookConfig.from_env() must enable when MAGI_EXTERNAL_HOOKS_ENABLED=true."""
    with patch.dict(os.environ, {"MAGI_EXTERNAL_HOOKS_ENABLED": "true"}):
        config = ExternalHookConfig.from_env()
    assert config.enabled is True


def test_external_hook_config_disabled_when_flag_off():
    """ExternalHookConfig.from_env() must be disabled when the flag is explicit '0'."""
    with patch.dict(os.environ, {"MAGI_EXTERNAL_HOOKS_ENABLED": "0"}, clear=True):
        config = ExternalHookConfig.from_env()
    assert config.enabled is False


def test_external_hook_config_various_truthy_values():
    """MAGI_EXTERNAL_HOOKS_ENABLED should accept '1', 'true', 'yes'."""
    for truthy in ("1", "true", "yes", "TRUE", "YES"):
        with patch.dict(os.environ, {"MAGI_EXTERNAL_HOOKS_ENABLED": truthy}):
            assert ExternalHookConfig.from_env().enabled is True, f"expected enabled for '{truthy}'"

    for falsy in ("0", "false", "no", "off", ""):
        with patch.dict(os.environ, {"MAGI_EXTERNAL_HOOKS_ENABLED": falsy}):
            assert ExternalHookConfig.from_env().enabled is False, f"expected disabled for '{falsy}'"


def test_llm_hooks_env_on_is_truthy():
    """C3 (N-21): the canonical ``on`` value must enable the default-ON sub-switch."""
    env_base = {k: v for k, v in os.environ.items() if k != "MAGI_LLM_HOOKS_ENABLED"}
    with patch.dict(os.environ, {**env_base, "MAGI_LLM_HOOKS_ENABLED": "on"}, clear=True):
        assert ExternalHookConfig.from_env().llm_hooks_enabled is True
    with patch.dict(os.environ, {**env_base, "MAGI_LLM_HOOKS_ENABLED": "off"}, clear=True):
        assert ExternalHookConfig.from_env().llm_hooks_enabled is False
    with patch.dict(os.environ, env_base, clear=True):
        assert ExternalHookConfig.from_env().llm_hooks_enabled is True


# ---------------------------------------------------------------------------
# 12. load_external_hooks_from_yaml returns empty list for missing file
# ---------------------------------------------------------------------------

def test_load_yaml_missing_file_returns_empty():
    """A nonexistent YAML path must return an empty list without raising."""
    hooks = load_external_hooks_from_yaml("/nonexistent/path/agent.hooks.yaml")
    assert hooks == []


# ---------------------------------------------------------------------------
# 13. HookBus auto-resolves executors from registry by default
# ---------------------------------------------------------------------------

def test_hookbus_auto_resolves_executors_from_registry():
    """HookBus() with no executor kwargs must pick up executors from get_executor()."""
    from magi_agent.hooks.executors.command_executor import CommandHookExecutor
    from magi_agent.hooks.executors.http_executor import HttpHookExecutor

    bus = HookBus()
    assert isinstance(bus._command_executor, CommandHookExecutor)
    assert isinstance(bus._http_executor, HttpHookExecutor)


# ---------------------------------------------------------------------------
# 14. _resolve_env_vars helper — basic substitution
# ---------------------------------------------------------------------------

def test_resolve_env_vars_substitutes_known_magi_hook_vars():
    """_resolve_env_vars must replace ${MAGI_HOOK_*} with its env value."""
    with patch.dict(os.environ, {"MAGI_HOOK_MY_TOKEN": "abc123"}):
        result = _resolve_env_vars("Bearer ${MAGI_HOOK_MY_TOKEN}")
    assert result == "Bearer abc123"


def test_resolve_env_vars_rejects_non_magi_hook_vars_with_warning(caplog):
    """_resolve_env_vars must leave ${NON_MAGI_HOOK_VAR} as-is and log a warning."""
    import logging

    with caplog.at_level(logging.WARNING, logger="magi_agent.hooks.external_config"):
        result = _resolve_env_vars("prefix-${MY_TOKEN}-suffix")
    # Token should be left unchanged (not substituted) to prevent secret leakage
    assert result == "prefix-${MY_TOKEN}-suffix"
    assert any("MY_TOKEN" in rec.message for rec in caplog.records)


def test_resolve_env_vars_missing_magi_hook_var_substitutes_empty_with_warning(caplog):
    """_resolve_env_vars must substitute missing MAGI_HOOK_* vars with empty string and log a warning."""
    import logging

    env_without = {k: v for k, v in os.environ.items() if k != "MAGI_HOOK_DEFINITELY_MISSING_XYZ"}
    with caplog.at_level(logging.WARNING, logger="magi_agent.hooks.external_config"):
        with patch.dict(os.environ, env_without, clear=True):
            result = _resolve_env_vars("prefix-${MAGI_HOOK_DEFINITELY_MISSING_XYZ}-suffix")
    assert result == "prefix--suffix"
    assert any("MAGI_HOOK_DEFINITELY_MISSING_XYZ" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 15. Malformed YAML returns empty list without raising
# ---------------------------------------------------------------------------

def test_load_yaml_malformed_returns_empty_without_raising():
    """Syntactically invalid YAML must return [] without propagating an exception."""
    bad_yaml = "hooks: [\n  - name: broken\n    point: [unclosed"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(bad_yaml)
        yaml_path = f.name

    try:
        hooks = load_external_hooks_from_yaml(yaml_path)
    finally:
        os.unlink(yaml_path)

    assert hooks == []
