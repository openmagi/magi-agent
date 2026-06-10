"""Tests for per-tool latency instrumentation (Principle 4).

Verifies that ``ToolDispatcher.dispatch`` records wall-clock elapsed time in
``ToolResult.latency_ms`` for every successfully-executed handler.  The field
is:

* non-negative (int >= 0),
* present on results that ran a handler,
* absent (None) on results that were blocked before the handler ran.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx() -> ToolContext:
    return ToolContext(botId="latency-test", turnId="t1", workspaceRoot="/tmp")


def _manifest(name: str, *, permission: str = "read") -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"{name} tool",
        kind="custom",
        source=ToolSource(kind="custom-plugin", package="tests.tools"),
        permission=permission,  # type: ignore[arg-type]
        input_schema={"type": "object", "additionalProperties": True},
        timeout_ms=5_000,
        available_in_modes=("plan", "act"),
        dangerous=False,
        mutates_workspace=False,
        tags=(),
        should_defer=False,
        latency_class="inline",
        adk_tool_type="FunctionTool",
        enabled_by_default=True,
        parallel_safety="readonly",
    )


def _registry_with(name: str, handler: object) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_manifest(name), handler=handler)  # type: ignore[arg-type]
    return registry


# ---------------------------------------------------------------------------
# latency_ms field contract
# ---------------------------------------------------------------------------

def test_latency_ms_is_non_negative_after_dispatch() -> None:
    """A dispatched result carries a non-negative integer latency_ms."""

    def handler(_args: dict, _ctx: ToolContext) -> ToolResult:
        return ToolResult(status="ok", output="done")

    registry = _registry_with("Echo", handler)
    dispatcher = ToolDispatcher(registry, readonly_offload_enabled=False)

    result = asyncio.run(dispatcher.dispatch("Echo", {}, _ctx(), mode="act"))

    assert result.latency_ms is not None
    assert isinstance(result.latency_ms, int)
    assert result.latency_ms >= 0


def test_latency_ms_reflects_handler_wall_clock() -> None:
    """latency_ms is at least as large as the handler's sleep duration."""
    sleep_ms = 30

    def slow_handler(_args: dict, _ctx: ToolContext) -> ToolResult:
        time.sleep(sleep_ms / 1000)
        return ToolResult(status="ok", output="slow")

    registry = _registry_with("SlowTool", slow_handler)
    dispatcher = ToolDispatcher(registry, readonly_offload_enabled=False)

    result = asyncio.run(dispatcher.dispatch("SlowTool", {}, _ctx(), mode="act"))

    assert result.latency_ms is not None
    assert result.latency_ms >= sleep_ms // 2  # generous lower bound


def test_latency_ms_is_none_when_tool_not_found() -> None:
    """Blocked results (tool not found) carry no latency_ms — handler never ran."""
    registry = ToolRegistry()
    dispatcher = ToolDispatcher(registry, readonly_offload_enabled=False)

    result = asyncio.run(dispatcher.dispatch("NoSuchTool", {}, _ctx(), mode="act"))

    assert result.status == "error"
    assert result.latency_ms is None


def test_latency_ms_is_none_when_tool_disabled() -> None:
    """Blocked results (tool disabled) carry no latency_ms."""

    def handler(_args: dict, _ctx: ToolContext) -> ToolResult:
        return ToolResult(status="ok", output="done")

    registry = _registry_with("DisabledTool", handler)
    registry.disable("DisabledTool")
    dispatcher = ToolDispatcher(registry, readonly_offload_enabled=False)

    result = asyncio.run(dispatcher.dispatch("DisabledTool", {}, _ctx(), mode="act"))

    assert result.status == "blocked"
    assert result.latency_ms is None


def test_latency_ms_preserved_through_async_handler() -> None:
    """latency_ms is set even when the handler is a coroutine."""

    async def async_handler(_args: dict, _ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(0)
        return ToolResult(status="ok", output="async-done")

    registry = ToolRegistry()
    registry.register(
        ToolManifest(
            name="AsyncTool",
            description="async test tool",
            kind="custom",
            source=ToolSource(kind="custom-plugin", package="tests.tools"),
            permission="read",
            input_schema={"type": "object", "additionalProperties": True},
            timeout_ms=5_000,
            available_in_modes=("plan", "act"),
            dangerous=False,
            mutates_workspace=False,
            tags=(),
            should_defer=False,
            latency_class="inline",
            adk_tool_type="FunctionTool",
            enabled_by_default=True,
            parallel_safety="unsafe",
        ),
        handler=async_handler,
    )
    dispatcher = ToolDispatcher(registry, readonly_offload_enabled=False)

    result = asyncio.run(dispatcher.dispatch("AsyncTool", {}, _ctx(), mode="act"))

    assert result.latency_ms is not None
    assert result.latency_ms >= 0


def test_latency_ms_present_on_error_result_from_handler() -> None:
    """A handler that returns status=error still gets latency_ms recorded."""

    def error_handler(_args: dict, _ctx: ToolContext) -> ToolResult:
        return ToolResult(status="error", errorMessage="something went wrong")

    registry = _registry_with("ErrorTool", error_handler)
    dispatcher = ToolDispatcher(registry, readonly_offload_enabled=False)

    result = asyncio.run(dispatcher.dispatch("ErrorTool", {}, _ctx(), mode="act"))

    assert result.status == "error"
    assert result.latency_ms is not None
    assert result.latency_ms >= 0


# ---------------------------------------------------------------------------
# ToolResult field contract
# ---------------------------------------------------------------------------

def test_tool_result_latency_ms_field_accepts_alias() -> None:
    """ToolResult accepts both ``latencyMs`` and ``latency_ms`` on construction."""
    r1 = ToolResult(status="ok", latencyMs=42)  # type: ignore[call-arg]
    r2 = ToolResult(status="ok", latency_ms=42)

    assert r1.latency_ms == 42
    assert r2.latency_ms == 42


def test_tool_result_latency_ms_serialises_as_camel_case() -> None:
    """ToolResult serialises latency_ms as ``latencyMs`` (camelCase alias)."""
    result = ToolResult(status="ok", latency_ms=99)
    dumped = result.model_dump(by_alias=True)
    assert dumped["latencyMs"] == 99


def test_tool_result_latency_ms_defaults_none() -> None:
    """latency_ms is None by default (not set by tool handlers directly)."""
    result = ToolResult(status="ok")
    assert result.latency_ms is None
