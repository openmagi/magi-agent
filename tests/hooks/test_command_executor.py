"""Tests for CommandHookExecutor (PR 2).

Covers:
1.  Exit code 0 with valid JSON → success result (parsed HookResult)
2.  Exit code 0 with empty stdout → continue
3.  Exit code 2 → block, stderr used as reason
4.  Exit code 1 → warn, continue (non-blocking)
5.  Timeout (fail-open manifest) → continue
6.  Timeout (fail-closed manifest) → block
7.  Malformed JSON stdout → continue with warning
8.  Environment variables are injected correctly
9.  Path sanitization: absolute paths redacted
10. API key / Bearer token sanitization
11. Thinking blocks not included in hook input
12. JSON output with permissionDecision field
13. JSON output with additionalContext
14. Process cleanup on timeout (no zombie)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

from openmagi_core_agent.hooks.context import HookContext
from openmagi_core_agent.hooks.executors import get_executor
from openmagi_core_agent.hooks.executors.command_executor import (
    CommandHookExecutor,
    _build_env,
    _build_sanitized_hook_input,
    _parse_hook_output,
    _sanitize_value,
)
from openmagi_core_agent.hooks.manifest import HookManifest, HookPoint
from openmagi_core_agent.hooks.result import HookResult
from openmagi_core_agent.tools.manifest import ToolSource

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SOURCE = ToolSource(kind="builtin", package="test.fixtures")

_BASE_MANIFEST = dict(
    name="test-cmd-hook",
    point=HookPoint.BEFORE_TOOL_USE,
    description="Test command hook",
    source=_SOURCE,
    executionType="command",
    command="true",  # placeholder; overridden in each test
)

_CONTEXT = HookContext(
    botId="bot-abc123",
    sessionId="sess-xyz",
    turnId="turn-001",
    channel="web",
)

_CONTEXT_NO_OPTIONAL = HookContext(botId="bot-minimal")


def _make_manifest(command: str, *, fail_open: bool = True, timeout_ms: int = 5_000) -> HookManifest:
    return HookManifest(**{**_BASE_MANIFEST, "command": command, "failOpen": fail_open, "timeoutMs": timeout_ms})


def _echo_json(obj: object) -> str:
    """Return a bash one-liner that prints *obj* as JSON to stdout."""
    encoded = json.dumps(json.dumps(obj))  # double-encode for safe shell embedding
    return f"printf '%s' {encoded}"


# ---------------------------------------------------------------------------
# 1. Exit code 0 with valid JSON → success result
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_exit_0_valid_json_continue():
    manifest = _make_manifest(_echo_json({"continue": True}))
    executor = CommandHookExecutor()
    result = await executor.execute(_CONTEXT, manifest)
    assert result.action == "continue"


@pytest.mark.anyio
async def test_exit_0_valid_json_block_via_stop_reason():
    manifest = _make_manifest(_echo_json({"stopReason": "forbidden action"}))
    executor = CommandHookExecutor()
    result = await executor.execute(_CONTEXT, manifest)
    assert result.action == "block"
    assert result.reason == "forbidden action"


@pytest.mark.anyio
async def test_exit_0_valid_json_replace_via_updated_input():
    payload = {"updatedInput": {"key": "value"}}
    manifest = _make_manifest(_echo_json(payload))
    executor = CommandHookExecutor()
    result = await executor.execute(_CONTEXT, manifest)
    assert result.action == "replace"
    assert result.value == {"key": "value"}


# ---------------------------------------------------------------------------
# 2. Exit code 0 with empty stdout → continue
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_exit_0_empty_stdout_is_continue():
    manifest = _make_manifest("true")  # `true` exits 0, produces no output
    executor = CommandHookExecutor()
    result = await executor.execute(_CONTEXT, manifest)
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 3. Exit code 2 → block, stderr reason
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_exit_2_returns_block_with_stderr_reason():
    manifest = _make_manifest("echo 'blocked by policy' >&2; exit 2")
    executor = CommandHookExecutor()
    result = await executor.execute(_CONTEXT, manifest)
    assert result.action == "block"
    assert result.reason is not None
    assert "blocked by policy" in result.reason


@pytest.mark.anyio
async def test_exit_2_no_stderr_uses_fallback_reason():
    manifest = _make_manifest("exit 2")
    executor = CommandHookExecutor()
    result = await executor.execute(_CONTEXT, manifest)
    assert result.action == "block"
    assert result.reason is not None


# ---------------------------------------------------------------------------
# 4. Exit code 1 → warn, continue
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_exit_1_returns_continue():
    manifest = _make_manifest("exit 1")
    executor = CommandHookExecutor()
    result = await executor.execute(_CONTEXT, manifest)
    assert result.action == "continue"


@pytest.mark.anyio
async def test_exit_3_returns_continue():
    manifest = _make_manifest("exit 3")
    executor = CommandHookExecutor()
    result = await executor.execute(_CONTEXT, manifest)
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 5. Timeout fail-open → continue
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_timeout_fail_open_returns_continue():
    manifest = _make_manifest("sleep 10", fail_open=True, timeout_ms=100)
    executor = CommandHookExecutor()
    result = await executor.execute(_CONTEXT, manifest)
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 6. Timeout fail-closed → block
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_timeout_fail_closed_returns_block():
    manifest = _make_manifest("sleep 10", fail_open=False, timeout_ms=100)
    executor = CommandHookExecutor()
    result = await executor.execute(_CONTEXT, manifest)
    assert result.action == "block"
    assert result.reason is not None
    assert "timed out" in result.reason.lower()


# ---------------------------------------------------------------------------
# 7. Malformed JSON stdout → continue with warning
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_malformed_json_stdout_returns_continue():
    manifest = _make_manifest("echo 'not-valid-json'")
    executor = CommandHookExecutor()
    result = await executor.execute(_CONTEXT, manifest)
    assert result.action == "continue"


@pytest.mark.anyio
async def test_json_array_stdout_returns_continue():
    manifest = _make_manifest(_echo_json([1, 2, 3]))
    executor = CommandHookExecutor()
    result = await executor.execute(_CONTEXT, manifest)
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 8. Environment variables are injected correctly
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_build_env_returns_correct_keys():
    # Test _build_env directly to verify all MAGI_* keys are set correctly.
    manifest = _make_manifest("true")
    env = _build_env(_CONTEXT, manifest)
    assert env["MAGI_HOOK_EVENT"] == HookPoint.BEFORE_TOOL_USE.value
    assert env["MAGI_BOT_ID"] == "bot-abc123"
    assert env["MAGI_SESSION_ID"] == "sess-xyz"
    assert env["MAGI_TURN_ID"] == "turn-001"
    assert env["MAGI_TOOL_NAME"] == ""


@pytest.mark.anyio
async def test_env_vars_optional_fields_empty_when_absent():
    manifest = _make_manifest("true")
    env = _build_env(_CONTEXT_NO_OPTIONAL, manifest)
    assert env["MAGI_SESSION_ID"] == ""
    assert env["MAGI_TURN_ID"] == ""
    assert env["MAGI_BOT_ID"] == "bot-minimal"


# ---------------------------------------------------------------------------
# 9. Path sanitization: absolute paths redacted
# ---------------------------------------------------------------------------

def test_sanitize_value_redacts_users_path():
    result = _sanitize_value("/Users/kevin/projects/clawy/secret.txt")
    assert "<redacted_path>" in result
    assert "kevin" not in result


def test_sanitize_value_redacts_home_path():
    result = _sanitize_value("/home/ubuntu/.ssh/id_rsa")
    assert "<redacted_path>" in result
    assert "ubuntu" not in result


def test_sanitize_value_leaves_non_path_strings_intact():
    result = _sanitize_value("hello world")
    assert result == "hello world"


def test_build_sanitized_input_does_not_include_thinking_blocks():
    """HookContext has no thinking-blocks field; verify the payload has no such key."""
    manifest = _make_manifest("true")
    payload = _build_sanitized_hook_input(_CONTEXT, manifest)
    for key in payload:
        assert "thinking" not in key.lower()
        assert "scratchpad" not in key.lower()
        assert "reasoning" not in key.lower()


def test_build_sanitized_input_does_not_include_user_id():
    """userId is a sensitive field — it should not appear in the hook payload."""
    ctx = HookContext(botId="bot-1", userId="user-sensitive-id-123")
    manifest = _make_manifest("true")
    payload = _build_sanitized_hook_input(ctx, manifest)
    assert "userId" not in payload
    assert "user-sensitive-id-123" not in json.dumps(payload)


# ---------------------------------------------------------------------------
# 10. API key / Bearer token sanitization
# ---------------------------------------------------------------------------

def test_sanitize_value_redacts_openai_key():
    result = _sanitize_value("sk-abc123XYZabcdefghij")
    assert "sk-abc123XYZabcdefghij" not in result
    assert "<redacted_secret>" in result


def test_sanitize_value_redacts_bearer_token():
    result = _sanitize_value("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
    assert "eyJhbGciOiJIUzI1NiJ9" not in result
    assert "<redacted_secret>" in result


def test_sanitize_value_redacts_api_key_assignment():
    result = _sanitize_value("api_key=supersecretvalue123")
    assert "supersecretvalue123" not in result
    assert "<redacted_secret>" in result


# ---------------------------------------------------------------------------
# 11. Thinking blocks not included in hook input
# ---------------------------------------------------------------------------

def test_build_sanitized_input_safe_fields_only():
    """Payload must only contain safe fields we explicitly allow."""
    manifest = _make_manifest("true")
    payload = _build_sanitized_hook_input(_CONTEXT, manifest)
    allowed_keys = {
        "hookEvent", "hookName", "botId", "sessionId", "turnId",
        "channel", "locale", "memoryMode", "agentModel", "pluginId",
        "policyScope",
    }
    for key in payload:
        assert key in allowed_keys, f"Unexpected key in hook payload: {key}"


# ---------------------------------------------------------------------------
# 12. JSON output with permissionDecision field
# ---------------------------------------------------------------------------

def test_parse_hook_output_permission_approve():
    result = _parse_hook_output(json.dumps({"permissionDecision": "approve"}))
    assert result.action == "permission_decision"
    assert result.decision == "approve"


def test_parse_hook_output_permission_deny():
    result = _parse_hook_output(json.dumps({"permissionDecision": "deny", "reason": "not allowed"}))
    assert result.action == "permission_decision"
    assert result.decision == "deny"
    assert result.reason == "not allowed"


def test_parse_hook_output_permission_ask():
    result = _parse_hook_output(json.dumps({"permissionDecision": "ask"}))
    assert result.action == "permission_decision"
    assert result.decision == "ask"


def test_parse_hook_output_unknown_permission_falls_through_to_continue():
    result = _parse_hook_output(json.dumps({"permissionDecision": "maybe"}))
    assert result.action == "continue"


# ---------------------------------------------------------------------------
# 13. JSON output with additionalContext
# ---------------------------------------------------------------------------

def test_parse_hook_output_additional_context_only():
    result = _parse_hook_output(json.dumps({"additionalContext": "here is more info"}))
    assert result.action == "continue"
    assert result.metadata.get("additionalContext") == "here is more info"


def test_parse_hook_output_updated_input_with_additional_context():
    result = _parse_hook_output(
        json.dumps({"updatedInput": {"x": 1}, "additionalContext": "ctx"})
    )
    assert result.action == "replace"
    assert result.value == {"x": 1}
    assert result.metadata.get("additionalContext") == "ctx"


# ---------------------------------------------------------------------------
# 14. Process cleanup on timeout (no zombie)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_timeout_kills_process_no_zombie():
    """After a timeout, the child process must be reaped (no zombie)."""
    manifest = _make_manifest("sleep 60", fail_open=True, timeout_ms=150)
    executor = CommandHookExecutor()
    result = await executor.execute(_CONTEXT, manifest)
    assert result.action == "continue"
    # Give the OS a moment to reap
    await asyncio.sleep(0.05)
    # If the process were a zombie we'd see it in /proc (Linux) or ps.
    # We can't portably assert the PID is gone, but the test confirms no hang.


# ---------------------------------------------------------------------------
# 15. Registry integration
# ---------------------------------------------------------------------------

def test_command_executor_registered():
    """Importing the module must register CommandHookExecutor in the global registry."""
    executor = get_executor("command")
    assert executor is not None
    assert isinstance(executor, CommandHookExecutor)


def test_handler_still_absent():
    """'handler' execution type has no registered executor."""
    assert get_executor("handler") is None
