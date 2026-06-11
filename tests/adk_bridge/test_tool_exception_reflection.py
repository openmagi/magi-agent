"""Tests for the generic tool-exception reflection plugin (hermes mechanism 1).

A tool handler that raises (any tool except FileEdit/PatchApply) currently
propagates through ADK ``functions.py`` and kills the whole turn. The plugin
under test converts the exception into a model-visible corrective tool_result
with retry guidance and a per-invocation attempt budget, so the model
self-corrects and the turn continues.

Covered:
(a) builder returns None when disabled;
(b) corrective dict shape for a generic non-edit tool raise;
(c) per-invocation attempt budget: attempts 1..max return dicts, max+1 None;
(d) FileEdit/PatchApply are hard-skipped (specialized handler keeps priority);
(e) after_run_callback sweeps only the finished invocation's counters;
(f) tool_context lacking invocation_id falls back to the global scope key;
(g) internal failure (tool.name property raising) fails open (returns None).

Pure unit tests — no ADK Runner, no network, no model.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from magi_agent.adk_bridge.tool_exception_reflection import (
    TOOL_EXCEPTION_REFLECTION_RESPONSE_TYPE,
    MagiToolExceptionReflectionPlugin,
    build_tool_exception_reflection_plugin,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _FakeTool:
    def __init__(self, name: str = "Bash") -> None:
        self.name = name


class _FakeCtx:
    def __init__(self, invocation_id: str = "inv-test") -> None:
        self.invocation_id = invocation_id


class _NoInvocationCtx:
    """Tool-context fake WITHOUT an invocation_id attribute."""


class _ExplodingNameTool:
    """Tool fake whose .name property raises (internal-failure simulation)."""

    @property
    def name(self) -> str:
        raise RuntimeError("name introspection failed")


def _reflect(
    plugin: MagiToolExceptionReflectionPlugin,
    *,
    tool: Any = None,
    tool_context: Any = None,
    error: Exception | None = None,
) -> dict[str, Any] | None:
    return _run(
        plugin.on_tool_error_callback(
            tool=tool if tool is not None else _FakeTool("Bash"),
            tool_args={"command": "ls"},
            tool_context=tool_context if tool_context is not None else _FakeCtx(),
            error=error if error is not None else ValueError("boom"),
        )
    )


# ---------------------------------------------------------------------------
# (a) builder gating
# ---------------------------------------------------------------------------


def test_builder_returns_none_when_disabled() -> None:
    assert build_tool_exception_reflection_plugin(enabled=False, max_attempts=2) is None


def test_builder_returns_plugin_when_enabled() -> None:
    plugin = build_tool_exception_reflection_plugin(enabled=True, max_attempts=3)
    assert isinstance(plugin, MagiToolExceptionReflectionPlugin)
    assert plugin.max_attempts == 3


def test_plugin_rejects_invalid_budget() -> None:
    with pytest.raises(ValueError):
        MagiToolExceptionReflectionPlugin(max_attempts=0)


# ---------------------------------------------------------------------------
# (b) corrective dict shape
# ---------------------------------------------------------------------------


def test_generic_tool_raise_returns_corrective_dict() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=2)

    result = _reflect(
        plugin,
        tool=_FakeTool("Bash"),
        tool_context=_FakeCtx("inv-b"),
        error=ValueError("command exploded badly"),
    )

    assert result is not None
    assert result["response_type"] == TOOL_EXCEPTION_REFLECTION_RESPONSE_TYPE
    assert result["status"] == "error"
    assert result["error_type"] == "ValueError"
    assert "command exploded badly" in result["error_message"]
    assert result["retry_attempt"] == 1
    assert result["max_attempts"] == 2
    guidance = result["guidance"]
    assert "Bash" in guidance
    assert "retry" in guidance.lower()


def test_error_message_truncated_to_500_chars() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=2)

    result = _reflect(plugin, error=ValueError("x" * 2000))

    assert result is not None
    assert len(result["error_message"]) == 500


# ---------------------------------------------------------------------------
# (c) attempt budget: 1..max return dicts, max+1 returns None (re-raise)
# ---------------------------------------------------------------------------


def test_attempt_budget_fails_closed_after_max_attempts() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=2)
    ctx = _FakeCtx("inv-budget")

    first = _reflect(plugin, tool_context=ctx)
    second = _reflect(plugin, tool_context=ctx)
    third = _reflect(plugin, tool_context=ctx)

    assert first is not None and first["retry_attempt"] == 1
    assert second is not None and second["retry_attempt"] == 2
    assert third is None, "attempt max+1 must return None so ADK re-raises"


def test_attempt_budget_is_per_invocation() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=1)

    assert _reflect(plugin, tool_context=_FakeCtx("inv-1")) is not None
    assert _reflect(plugin, tool_context=_FakeCtx("inv-1")) is None
    # A different invocation has its own fresh budget.
    assert _reflect(plugin, tool_context=_FakeCtx("inv-2")) is not None


def test_attempt_budget_is_per_tool() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=1)
    ctx = _FakeCtx("inv-tools")

    assert _reflect(plugin, tool=_FakeTool("Bash"), tool_context=ctx) is not None
    assert _reflect(plugin, tool=_FakeTool("Bash"), tool_context=ctx) is None
    # Another tool in the same invocation has its own budget.
    assert _reflect(plugin, tool=_FakeTool("WebFetch"), tool_context=ctx) is not None


# ---------------------------------------------------------------------------
# (d) edit tools are hard-skipped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", ["FileEdit", "PatchApply"])
def test_edit_tools_always_return_none(tool_name: str) -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=5)

    for _ in range(3):
        assert (
            _reflect(
                plugin,
                tool=_FakeTool(tool_name),
                error=ValueError("old_text_not_found"),
            )
            is None
        ), f"{tool_name} must keep its specialized edit-retry handler"


# ---------------------------------------------------------------------------
# (e) after_run_callback sweeps only the finished invocation
# ---------------------------------------------------------------------------


def test_after_run_callback_sweeps_only_matching_invocation() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=5)
    ctx_a = _FakeCtx("inv-a")
    ctx_b = _FakeCtx("inv-b")

    _reflect(plugin, tool_context=ctx_a)
    _reflect(plugin, tool_context=ctx_a)
    _reflect(plugin, tool_context=ctx_b)

    _run(
        plugin.after_run_callback(
            invocation_context=SimpleNamespace(invocation_id="inv-a")
        )
    )

    # inv-a counters were swept -> next attempt restarts at 1.
    after_sweep_a = _reflect(plugin, tool_context=ctx_a)
    assert after_sweep_a is not None and after_sweep_a["retry_attempt"] == 1
    # inv-b counters survived -> next attempt continues at 2.
    after_sweep_b = _reflect(plugin, tool_context=ctx_b)
    assert after_sweep_b is not None and after_sweep_b["retry_attempt"] == 2


def test_after_run_callback_without_invocation_id_is_noop() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=5)
    _reflect(plugin, tool_context=_FakeCtx("inv-keep"))

    _run(plugin.after_run_callback(invocation_context=SimpleNamespace()))

    follow_up = _reflect(plugin, tool_context=_FakeCtx("inv-keep"))
    assert follow_up is not None and follow_up["retry_attempt"] == 2


# ---------------------------------------------------------------------------
# (f) missing invocation_id falls back to the global scope key
# ---------------------------------------------------------------------------


def test_missing_invocation_id_falls_back_to_global_scope() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=2)

    first = _reflect(plugin, tool_context=_NoInvocationCtx())
    second = _reflect(plugin, tool_context=_NoInvocationCtx())

    assert first is not None and first["retry_attempt"] == 1
    assert second is not None and second["retry_attempt"] == 2


# ---------------------------------------------------------------------------
# (g) internal failure fails open
# ---------------------------------------------------------------------------


def test_internal_failure_fails_open_returns_none() -> None:
    plugin = MagiToolExceptionReflectionPlugin(max_attempts=2)

    result = _reflect(plugin, tool=_ExplodingNameTool())

    assert result is None
