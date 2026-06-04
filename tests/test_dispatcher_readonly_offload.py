"""Live-path concurrency tests for ``ToolDispatcher`` readonly offload (PR14).

Google ADK 1.33.0 already fans out same-turn function calls concurrently
(``flows/llm_flows/functions.handle_function_call_list_async`` builds one
``asyncio.create_task`` per call and ``await``s ``asyncio.gather``). But magi's
readonly tool handlers are *synchronous* and do blocking file I/O, so under
ADK's gather each handler runs to completion on the event loop thread before the
next task is scheduled — there is no real I/O overlap.

These tests pin the genuinely-live seam: when ``MAGI_TOOL_CONCURRENCY_ENABLED``
is ON, ``ToolDispatcher`` runs *readonly / concurrency_safe* synchronous
handlers via ``asyncio.to_thread`` (bounded by a shared semaphore), so ADK's
existing gather produces real overlap. Workspace-mutating / unsafe / async
handlers are never offloaded — they run inline on the event loop thread,
preserving the write-barrier and per-tool permission guarantees. With the flag
OFF the dispatcher behaves exactly as before (fully inline).
"""
from __future__ import annotations

import asyncio
import threading
import time

from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


def _make_context() -> ToolContext:
    return ToolContext(botId="offload-test", turnId="t1", workspaceRoot="/tmp")


def _manifest(
    name: str,
    *,
    parallel_safety: str = "unsafe",
    mutates_workspace: bool = False,
    permission: str = "read",
) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"{name} tool",
        kind="custom",
        source=ToolSource(kind="custom-plugin", package="tests.tools"),
        permission=permission,
        input_schema={"type": "object", "additionalProperties": True},
        timeout_ms=5_000,
        available_in_modes=("plan", "act"),
        dangerous=False,
        mutates_workspace=mutates_workspace,
        tags=(),
        should_defer=False,
        latency_class="inline",
        adk_tool_type="FunctionTool",
        enabled_by_default=True,
        parallel_safety=parallel_safety,  # type: ignore[arg-type]
    )


def _registry_with_blocking_readonly(thread_names: list[str]) -> ToolRegistry:
    registry = ToolRegistry()
    for name in ("FileRead", "Glob", "Grep"):

        def handler(_args: dict, _ctx: ToolContext, _n: str = name) -> ToolResult:
            thread_names.append(threading.current_thread().name)
            time.sleep(0.1)  # blocking I/O proxy
            return ToolResult(status="ok", output={"tool": _n})

        registry.register(_manifest(name, parallel_safety="readonly"), handler=handler)
    return registry


def test_readonly_handlers_overlap_when_flag_on() -> None:
    """With the flag ON, three blocking readonly handlers run off-thread so a
    gather over three dispatch() calls overlaps (~0.1s, not ~0.3s)."""
    thread_names: list[str] = []
    registry = _registry_with_blocking_readonly(thread_names)
    dispatcher = ToolDispatcher(registry, readonly_offload_enabled=True, max_offload_concurrency=8)
    ctx = _make_context()

    async def run() -> float:
        t0 = time.monotonic()
        await asyncio.gather(
            dispatcher.dispatch("FileRead", {}, ctx, mode="act"),
            dispatcher.dispatch("Glob", {}, ctx, mode="act"),
            dispatcher.dispatch("Grep", {}, ctx, mode="act"),
        )
        return time.monotonic() - t0

    elapsed = asyncio.run(run())
    # Overlapped: well under the 0.3s a fully-serial run would take.
    assert elapsed < 0.25, f"expected overlap, took {elapsed:.3f}s"
    # Ran off the main/event-loop thread.
    assert all(n != "MainThread" for n in thread_names), thread_names


def test_readonly_handlers_serial_when_flag_off() -> None:
    """With the flag OFF, the same handlers run inline on the event loop thread
    and a gather does NOT overlap (~0.3s)."""
    thread_names: list[str] = []
    registry = _registry_with_blocking_readonly(thread_names)
    dispatcher = ToolDispatcher(registry, readonly_offload_enabled=False)
    ctx = _make_context()

    async def run() -> float:
        t0 = time.monotonic()
        await asyncio.gather(
            dispatcher.dispatch("FileRead", {}, ctx, mode="act"),
            dispatcher.dispatch("Glob", {}, ctx, mode="act"),
            dispatcher.dispatch("Grep", {}, ctx, mode="act"),
        )
        return time.monotonic() - t0

    elapsed = asyncio.run(run())
    assert elapsed >= 0.25, f"expected serial blocking, took {elapsed:.3f}s"
    assert all(n == "MainThread" for n in thread_names), thread_names


def test_unsafe_handler_not_offloaded_even_when_flag_on() -> None:
    """An ``unsafe`` (non-parallel-safe) tool runs inline even with the flag ON.

    Uses a read-permission tool marked ``parallel_safety="unsafe"`` (e.g. a
    stateful read that must not interleave) so it passes the permission policy
    yet must still run on the event loop thread — the offload only ever applies
    to readonly / concurrency_safe tools."""
    thread_names: list[str] = []
    registry = ToolRegistry()

    def stateful_handler(_args: dict, _ctx: ToolContext) -> ToolResult:
        thread_names.append(threading.current_thread().name)
        return ToolResult(status="ok", output={"ok": True})

    registry.register(
        _manifest("StatefulRead", parallel_safety="unsafe", permission="read"),
        handler=stateful_handler,
    )
    dispatcher = ToolDispatcher(registry, readonly_offload_enabled=True)
    ctx = _make_context()

    result = asyncio.run(dispatcher.dispatch("StatefulRead", {}, ctx, mode="act"))
    assert result.status == "ok"
    assert thread_names == ["MainThread"], thread_names


def test_permission_check_still_runs_under_offload() -> None:
    """A readonly tool that the permission policy denies must still be blocked —
    offload must not bypass the per-call permission/path checks."""
    registry = ToolRegistry()

    def handler(_args: dict, _ctx: ToolContext) -> ToolResult:  # pragma: no cover
        raise AssertionError("handler must not run when permission denies")

    # An "execute"-permission tool in plan mode is denied by the default policy;
    # but to keep this deterministic we deny via a not-exposed allowlist.
    registry.register(_manifest("FileRead", parallel_safety="readonly"), handler=handler)
    dispatcher = ToolDispatcher(registry, readonly_offload_enabled=True)
    ctx = _make_context()

    result = asyncio.run(
        dispatcher.dispatch("FileRead", {}, ctx, mode="act", exposed_tool_names=("Glob",))
    )
    assert result.status == "error"
    assert result.error_code == "tool_not_exposed"


def test_env_flag_parsers_single_source() -> None:
    """The PR14 flags resolve through the single-source ``config.env`` helpers."""
    from magi_agent.config.env import max_tool_concurrency, tool_concurrency_enabled

    assert tool_concurrency_enabled({}) is False
    assert tool_concurrency_enabled({"MAGI_TOOL_CONCURRENCY_ENABLED": "1"}) is True
    assert tool_concurrency_enabled({"MAGI_TOOL_CONCURRENCY_ENABLED": "true"}) is True
    assert tool_concurrency_enabled({"MAGI_TOOL_CONCURRENCY_ENABLED": "0"}) is False

    assert max_tool_concurrency({}) == 8
    assert max_tool_concurrency({"MAGI_MAX_TOOL_CONCURRENCY": "4"}) == 4
    # Clamped to at least 1.
    assert max_tool_concurrency({"MAGI_MAX_TOOL_CONCURRENCY": "0"}) == 1


def test_dispatcher_defaults_to_env_when_no_explicit_flag(monkeypatch) -> None:
    """When constructed without an explicit flag, the dispatcher reads the env
    single source (default OFF)."""
    monkeypatch.delenv("MAGI_TOOL_CONCURRENCY_ENABLED", raising=False)
    registry = ToolRegistry()
    dispatcher = ToolDispatcher(registry)
    assert dispatcher._readonly_offload_enabled is False

    monkeypatch.setenv("MAGI_TOOL_CONCURRENCY_ENABLED", "1")
    monkeypatch.setenv("MAGI_MAX_TOOL_CONCURRENCY", "3")
    dispatcher_on = ToolDispatcher(ToolRegistry())
    assert dispatcher_on._readonly_offload_enabled is True
    assert dispatcher_on._max_offload_concurrency == 3


def test_offload_semaphore_bounds_concurrency() -> None:
    """The offload semaphore bounds simultaneous off-thread handlers to
    ``max_offload_concurrency``."""
    active = 0
    peak = 0
    lock = threading.Lock()
    registry = ToolRegistry()

    def handler(_args: dict, _ctx: ToolContext, _n: str = "") -> ToolResult:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return ToolResult(status="ok")

    for name in ("R1", "R2", "R3", "R4"):
        registry.register(_manifest(name, parallel_safety="readonly"), handler=handler)
    dispatcher = ToolDispatcher(registry, readonly_offload_enabled=True, max_offload_concurrency=2)
    ctx = _make_context()

    async def run() -> None:
        await asyncio.gather(
            *(dispatcher.dispatch(n, {}, ctx, mode="act") for n in ("R1", "R2", "R3", "R4"))
        )

    asyncio.run(run())
    assert peak <= 2, f"semaphore did not bound concurrency, peak={peak}"


def test_async_readonly_handler_runs_inline_not_threaded() -> None:
    """An async (coroutine) readonly handler already yields to the loop, so it is
    awaited inline — never wrapped in to_thread."""
    thread_names: list[str] = []
    registry = ToolRegistry()

    async def handler(_args: dict, _ctx: ToolContext) -> ToolResult:
        thread_names.append(threading.current_thread().name)
        await asyncio.sleep(0)
        return ToolResult(status="ok", output={"async": True})

    registry.register(_manifest("Grep", parallel_safety="readonly"), handler=handler)
    dispatcher = ToolDispatcher(registry, readonly_offload_enabled=True)
    ctx = _make_context()

    result = asyncio.run(dispatcher.dispatch("Grep", {}, ctx, mode="act"))
    assert result.status == "ok"
    assert thread_names == ["MainThread"], thread_names
