"""Integration tests for ConcurrentToolDispatcher with the ADK tool adapter.

These tests verify that:
- ``build_concurrency_config()`` reads env vars correctly and applies defaults.
- ``build_concurrent_dispatcher()`` produces a ``ConcurrentToolDispatcher``
  that is a drop-in replacement for the base dispatcher in ADK tool wrappers.
- Single ``dispatch()`` calls (how ADK's FunctionTool invokes tools) continue
  to work correctly through the concurrent dispatcher.
- ``dispatch_batch()`` dispatches multiple calls, respecting parallel_safety.
- The config gate (enabled vs disabled) produces sequential vs concurrent
  execution paths.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from unittest.mock import patch

import pytest

from magi_agent.adk_bridge.tool_adapter import (
    build_adk_function_tools_for_registry,
    build_adk_tool_for_manifest,
    build_concurrency_config,
    build_concurrent_dispatcher,
)
from magi_agent.tools.concurrency import ConcurrencyConfig, ToolCall
from magi_agent.tools.concurrent_dispatcher import ConcurrentToolDispatcher
from magi_agent.tools.context import ToolContext as OpenMagiToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_manifest(
    name: str,
    *,
    description: str | None = None,
    permission: str = "read",
    modes: tuple[str, ...] = ("plan", "act"),
    parallel_safety: str = "unsafe",
    mutates_workspace: bool = False,
    adk_tool_type: str = "FunctionTool",
    enabled_by_default: bool = True,
) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=description or f"{name} tool",
        kind="custom",
        source=ToolSource(kind="custom-plugin", package="tests.tools"),
        permission=permission,
        input_schema={"type": "object", "additionalProperties": True},
        timeout_ms=5_000,
        available_in_modes=modes,
        dangerous=False,
        mutates_workspace=mutates_workspace,
        tags=(),
        should_defer=False,
        latency_class="inline",
        adk_tool_type=adk_tool_type,
        enabled_by_default=enabled_by_default,
        parallel_safety=parallel_safety,  # type: ignore[arg-type]
    )


def make_context_factory(
    *,
    bot_id: str = "bot-concurrent-test",
    turn_id: str = "turn-1",
) -> Callable[[object], OpenMagiToolContext]:
    def factory(adk_tool_context: object) -> OpenMagiToolContext:
        return OpenMagiToolContext(
            bot_id=bot_id,
            turn_id=turn_id,
            workspace_root="/tmp/workspace",
            adk_tool_context=adk_tool_context,
        )
    return factory


def run_async(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. build_concurrency_config reads env vars correctly
# ---------------------------------------------------------------------------


def test_build_concurrency_config_reads_env_enabled() -> None:
    with patch.dict(os.environ, {"MAGI_TOOL_CONCURRENCY_ENABLED": "1", "MAGI_MAX_TOOL_CONCURRENCY": "4"}):
        config = build_concurrency_config()

    assert config.enabled is True
    assert config.max_concurrency == 4


# ---------------------------------------------------------------------------
# 2. build_concurrency_config uses defaults when env not set
# ---------------------------------------------------------------------------


def test_build_concurrency_config_defaults_when_env_absent() -> None:
    env_without_keys = {
        k: v
        for k, v in os.environ.items()
        if k not in ("MAGI_TOOL_CONCURRENCY_ENABLED", "MAGI_MAX_TOOL_CONCURRENCY")
    }
    with patch.dict(os.environ, env_without_keys, clear=True):
        config = build_concurrency_config()

    assert config.enabled is True
    assert config.max_concurrency == 8


def test_build_concurrency_config_disabled_when_env_zero() -> None:
    with patch.dict(os.environ, {"MAGI_TOOL_CONCURRENCY_ENABLED": "0"}):
        config = build_concurrency_config()

    assert config.enabled is False


# ---------------------------------------------------------------------------
# 3. build_concurrent_dispatcher returns ConcurrentToolDispatcher
# ---------------------------------------------------------------------------


def test_build_concurrent_dispatcher_returns_correct_type() -> None:
    registry = ToolRegistry()
    base = ToolDispatcher(registry)
    config = ConcurrencyConfig(enabled=True, max_concurrency=4)

    dispatcher = build_concurrent_dispatcher(base, config=config)

    assert isinstance(dispatcher, ConcurrentToolDispatcher)


def test_build_concurrent_dispatcher_uses_explicit_config() -> None:
    registry = ToolRegistry()
    base = ToolDispatcher(registry)
    config = ConcurrencyConfig(enabled=True, max_concurrency=2)

    dispatcher = build_concurrent_dispatcher(base, config=config)

    # Access internal config to verify it was applied.
    assert dispatcher._config.enabled is True
    assert dispatcher._config.max_concurrency == 2


def test_build_concurrent_dispatcher_uses_env_config_when_none_given() -> None:
    registry = ToolRegistry()
    base = ToolDispatcher(registry)

    with patch.dict(os.environ, {"MAGI_TOOL_CONCURRENCY_ENABLED": "1", "MAGI_MAX_TOOL_CONCURRENCY": "6"}):
        dispatcher = build_concurrent_dispatcher(base)

    assert dispatcher._config.enabled is True
    assert dispatcher._config.max_concurrency == 6


def test_build_concurrent_dispatcher_exposes_registry_from_base() -> None:
    registry = ToolRegistry()
    base = ToolDispatcher(registry)

    dispatcher = build_concurrent_dispatcher(base, config=ConcurrencyConfig())

    assert dispatcher.registry is registry


# ---------------------------------------------------------------------------
# 4. ADK FunctionTool wrappers work with ConcurrentToolDispatcher (single calls)
# ---------------------------------------------------------------------------


def test_adk_function_tool_single_dispatch_via_concurrent_dispatcher() -> None:
    """FunctionTool wrappers work unchanged when the underlying dispatcher is
    a ConcurrentToolDispatcher — single dispatch() calls delegate to the base."""
    manifest = make_manifest("ReadFile", parallel_safety="readonly")
    calls: list[tuple[dict[str, object], OpenMagiToolContext]] = []

    def handler(arguments: dict[str, object], context: OpenMagiToolContext) -> ToolResult:
        calls.append((arguments, context))
        return ToolResult(status="ok", output={"content": arguments.get("path", "")})

    registry = ToolRegistry()
    registry.register(manifest, handler=handler)
    base = ToolDispatcher(registry)
    concurrent = build_concurrent_dispatcher(base, config=ConcurrencyConfig(enabled=True))

    tool = build_adk_tool_for_manifest(
        manifest,
        concurrent,  # type: ignore[arg-type]
        mode="act",
        tool_context_factory=make_context_factory(),
    )

    from google.adk.tools import FunctionTool

    assert isinstance(tool, FunctionTool)

    result = run_async(tool.run_async(args={"arguments": {"path": "/tmp/test.txt"}}, tool_context=object()))

    assert result["status"] == "ok"
    assert result["output"] == {"content": "/tmp/test.txt"}
    assert len(calls) == 1


def test_adk_registry_builder_accepts_concurrent_dispatcher() -> None:
    """build_adk_function_tools_for_registry accepts ConcurrentToolDispatcher."""
    manifest = make_manifest("Echo", parallel_safety="concurrency_safe")

    def handler(arguments: dict[str, object], _context: OpenMagiToolContext) -> ToolResult:
        return ToolResult(status="ok", output={"echo": arguments})

    registry = ToolRegistry()
    registry.register(manifest, handler=handler)
    base = ToolDispatcher(registry)
    concurrent = build_concurrent_dispatcher(base, config=ConcurrencyConfig(enabled=True))

    tools = build_adk_function_tools_for_registry(
        registry,
        concurrent,  # type: ignore[arg-type]
        mode="act",
        tool_context_factory=make_context_factory(),
        attach_enabled=True,
    )

    assert len(tools) == 1
    assert tools[0].name == "Echo"


# ---------------------------------------------------------------------------
# 5. dispatch_batch works through the concurrent dispatcher with mock tools
# ---------------------------------------------------------------------------


def test_dispatch_batch_returns_results_in_call_order() -> None:
    """dispatch_batch preserves ordering even when calls run concurrently."""
    execution_order: list[str] = []

    def make_handler(name: str) -> Callable[[dict[str, object], OpenMagiToolContext], ToolResult]:
        def handler(_arguments: dict[str, object], _context: OpenMagiToolContext) -> ToolResult:
            execution_order.append(name)
            return ToolResult(status="ok", output={"tool": name})
        return handler

    registry = ToolRegistry()
    for tool_name in ("Alpha", "Beta", "Gamma"):
        m = make_manifest(tool_name, parallel_safety="readonly")
        registry.register(m, handler=make_handler(tool_name))

    base = ToolDispatcher(registry)
    concurrent = build_concurrent_dispatcher(base, config=ConcurrencyConfig(enabled=True, max_concurrency=8))

    context = OpenMagiToolContext(
        bot_id="bot-1",
        turn_id="t1",
        workspace_root="/tmp",
        adk_tool_context=None,
    )

    calls = (
        ToolCall(name="Alpha", arguments={}, tool_use_id="tu-1"),
        ToolCall(name="Beta", arguments={}, tool_use_id="tu-2"),
        ToolCall(name="Gamma", arguments={}, tool_use_id="tu-3"),
    )

    results = run_async(concurrent.dispatch_batch(calls, context, mode="act"))

    assert len(results) == 3
    assert results[0].output == {"tool": "Alpha"}
    assert results[1].output == {"tool": "Beta"}
    assert results[2].output == {"tool": "Gamma"}
    assert set(execution_order) == {"Alpha", "Beta", "Gamma"}


def test_dispatch_batch_single_call_works() -> None:
    registry = ToolRegistry()
    manifest = make_manifest("Solo", parallel_safety="unsafe")

    def handler(_arguments: dict[str, object], _context: OpenMagiToolContext) -> ToolResult:
        return ToolResult(status="ok", output={"solo": True})

    registry.register(manifest, handler=handler)
    base = ToolDispatcher(registry)
    concurrent = build_concurrent_dispatcher(base, config=ConcurrencyConfig(enabled=True))

    context = OpenMagiToolContext(
        bot_id="bot-1",
        turn_id="t1",
        workspace_root="/tmp",
        adk_tool_context=None,
    )

    results = run_async(
        concurrent.dispatch_batch(
            (ToolCall(name="Solo", arguments={}, tool_use_id="tu-solo"),),
            context,
            mode="act",
        )
    )

    assert len(results) == 1
    assert results[0].status == "ok"


# ---------------------------------------------------------------------------
# 6. Config gate: disabled config gives sequential behavior
# ---------------------------------------------------------------------------


def test_dispatch_batch_disabled_config_runs_sequentially() -> None:
    """When config.enabled=False, dispatch_batch falls back to sequential execution."""
    execution_log: list[str] = []

    def make_handler(name: str) -> Callable[[dict[str, object], OpenMagiToolContext], ToolResult]:
        def handler(_arguments: dict[str, object], _context: OpenMagiToolContext) -> ToolResult:
            execution_log.append(name)
            return ToolResult(status="ok", output={"tool": name})
        return handler

    registry = ToolRegistry()
    for name in ("ToolA", "ToolB"):
        m = make_manifest(name, parallel_safety="readonly")
        registry.register(m, handler=make_handler(name))

    base = ToolDispatcher(registry)
    concurrent = build_concurrent_dispatcher(base, config=ConcurrencyConfig(enabled=False))

    context = OpenMagiToolContext(
        bot_id="bot-1",
        turn_id="t1",
        workspace_root="/tmp",
        adk_tool_context=None,
    )

    calls = (
        ToolCall(name="ToolA", arguments={}, tool_use_id="tu-a"),
        ToolCall(name="ToolB", arguments={}, tool_use_id="tu-b"),
    )

    results = run_async(concurrent.dispatch_batch(calls, context, mode="act"))

    assert len(results) == 2
    assert results[0].output == {"tool": "ToolA"}
    assert results[1].output == {"tool": "ToolB"}
    # Sequential: A before B.
    assert execution_log == ["ToolA", "ToolB"]


# ---------------------------------------------------------------------------
# 7. Config gate: enabled config dispatches concurrent-safe tools concurrently
# ---------------------------------------------------------------------------


def test_dispatch_batch_enabled_config_concurrent_safe_tools_all_succeed() -> None:
    """Enabled concurrent dispatch: all concurrent-safe tool calls succeed."""
    registry = ToolRegistry()
    tool_names = ("Fetch1", "Fetch2", "Fetch3")
    for name in tool_names:
        m = make_manifest(name, parallel_safety="readonly")
        registry.register(
            m,
            handler=lambda args, _ctx, n=name: ToolResult(status="ok", output={"fetched": n}),
        )

    base = ToolDispatcher(registry)
    concurrent = build_concurrent_dispatcher(
        base, config=ConcurrencyConfig(enabled=True, max_concurrency=3)
    )

    context = OpenMagiToolContext(
        bot_id="bot-1",
        turn_id="t1",
        workspace_root="/tmp",
        adk_tool_context=None,
    )

    calls = tuple(
        ToolCall(name=n, arguments={}, tool_use_id=f"tu-{i}")
        for i, n in enumerate(tool_names)
    )

    results = run_async(concurrent.dispatch_batch(calls, context, mode="act"))

    assert len(results) == 3
    fetched_names = {r.output["fetched"] for r in results}  # type: ignore[index]
    assert fetched_names == set(tool_names)
    assert all(r.status == "ok" for r in results)


def test_dispatch_batch_mixed_safety_preserves_exclusive_isolation() -> None:
    """Unsafe tools are run exclusively even when surrounded by concurrent-safe calls."""
    registry = ToolRegistry()
    safe = make_manifest("SafeRead", parallel_safety="readonly")
    unsafe = make_manifest("WriteFile", parallel_safety="unsafe")

    registry.register(
        safe,
        handler=lambda _a, _c: ToolResult(status="ok", output={"safe": True}),
    )
    registry.register(
        unsafe,
        handler=lambda _a, _c: ToolResult(status="ok", output={"unsafe": True}),
    )

    base = ToolDispatcher(registry)
    concurrent = build_concurrent_dispatcher(
        base, config=ConcurrencyConfig(enabled=True, max_concurrency=8)
    )

    context = OpenMagiToolContext(
        bot_id="bot-1",
        turn_id="t1",
        workspace_root="/tmp",
        adk_tool_context=None,
    )

    calls = (
        ToolCall(name="SafeRead", arguments={}, tool_use_id="tu-1"),
        ToolCall(name="WriteFile", arguments={}, tool_use_id="tu-2"),
        ToolCall(name="SafeRead", arguments={}, tool_use_id="tu-3"),
    )

    results = run_async(concurrent.dispatch_batch(calls, context, mode="act"))

    assert len(results) == 3
    assert results[0].output == {"safe": True}
    assert results[1].output == {"unsafe": True}
    assert results[2].output == {"safe": True}
    assert all(r.status == "ok" for r in results)
