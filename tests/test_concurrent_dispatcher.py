"""Tests for ConcurrentToolDispatcher.

Verifies that the concurrent batch dispatcher correctly:
- Falls back to sequential execution when config.enabled=False
- Dispatches single calls sequentially
- Executes concurrent-safe batches in parallel
- Executes exclusive batches sequentially
- Handles mixed batches producing results in request order
- Isolates errors (one failure does not cascade)
- Respects semaphore concurrency limits
- Preserves result order (not completion order)
- Records ToolBatchExecution evidence
- Converts exceptions to error ToolResults
"""
from __future__ import annotations

import asyncio

from openmagi_core_agent.tools.concurrency import (
    ConcurrencyConfig,
    ToolCall,
)
from openmagi_core_agent.tools.concurrent_dispatcher import (
    ConcurrentToolDispatcher,
    ToolBatchExecution,
    _with_duration,
)
from openmagi_core_agent.tools.context import ToolContext
from openmagi_core_agent.tools.manifest import ToolManifest, ToolSource
from openmagi_core_agent.tools.registry import ToolRegistry
from openmagi_core_agent.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context() -> ToolContext:
    return ToolContext(botId="test-bot")


def _call(name: str, idx: int = 0) -> ToolCall:
    return ToolCall(name=name, arguments={}, tool_use_id=f"call_{name}_{idx}")


def _ok(name: str, duration_ms: int | None = None) -> ToolResult:
    return ToolResult(status="ok", output={"tool": name}, duration_ms=duration_ms)


def _make_registry_with(*names: str, safe: bool = True) -> ToolRegistry:
    """Create a registry whose tools are either all concurrent-safe or all unsafe."""
    registry = ToolRegistry()
    source = ToolSource(kind="builtin", package="test")
    for name in names:
        manifest = ToolManifest(
            name=name,
            description=f"Test tool {name}",
            kind="core",
            source=source,
            permission="read",
            inputSchema={"type": "object"},
            timeoutMs=30_000,
            parallelSafety="readonly" if safe else "unsafe",
            availableInModes=("plan", "act"),
            enabled_by_default=True,
        )
        registry.register(manifest)
    return registry


class _MockDispatcher:
    """Minimal dispatcher mock that returns predefined results or raises."""

    def __init__(
        self,
        registry: ToolRegistry,
        results: dict[str, ToolResult] | None = None,
        *,
        raise_for: set[str] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.registry = registry
        self._results = results or {}
        self._raise_for = raise_for or set()
        self._delay = delay
        self.call_log: list[str] = []

    async def dispatch(
        self,
        name: str,
        arguments: dict[str, object],
        context: ToolContext,
        *,
        mode: str,
        exposed_tool_names: tuple[str, ...] | None = None,
    ) -> ToolResult:
        self.call_log.append(name)
        if self._delay:
            await asyncio.sleep(self._delay)
        if name in self._raise_for:
            raise RuntimeError(f"simulated failure in {name}")
        return self._results.get(name, _ok(name))


# ---------------------------------------------------------------------------
# 1. Disabled config falls back to sequential
# ---------------------------------------------------------------------------


def test_disabled_config_falls_back_to_sequential() -> None:
    """With config.enabled=False, all calls are dispatched sequentially."""
    registry = _make_registry_with("FileRead", "Grep")
    mock = _MockDispatcher(registry)
    dispatcher = ConcurrentToolDispatcher(
        base_dispatcher=mock,
        config=ConcurrencyConfig(enabled=False),
    )
    calls = (_call("FileRead", 0), _call("Grep", 1))

    results = asyncio.run(
        dispatcher.dispatch_batch(calls, _make_context(), mode="act")
    )
    assert len(results) == 2
    assert all(r.status == "ok" for r in results)
    # Calls were logged in order
    assert mock.call_log == ["FileRead", "Grep"]


# ---------------------------------------------------------------------------
# 2. Single call dispatched sequentially even with enabled=True
# ---------------------------------------------------------------------------


def test_single_call_dispatched_sequentially() -> None:
    """Even with concurrency enabled, a single call takes the sequential path."""
    registry = _make_registry_with("FileRead")
    mock = _MockDispatcher(registry, results={"FileRead": _ok("FileRead")})
    dispatcher = ConcurrentToolDispatcher(
        base_dispatcher=mock,
        config=ConcurrencyConfig(enabled=True),
    )
    calls = (_call("FileRead"),)

    results = asyncio.run(
        dispatcher.dispatch_batch(calls, _make_context(), mode="act")
    )
    assert len(results) == 1
    assert results[0].status == "ok"
    assert mock.call_log == ["FileRead"]


# ---------------------------------------------------------------------------
# 3. Concurrent batch actually runs in parallel (verified via counter)
# ---------------------------------------------------------------------------


def test_concurrent_batch_runs_in_parallel() -> None:
    """Concurrent-safe tools in the same batch run simultaneously."""
    max_concurrent: list[int] = [0]
    active: list[int] = [0]

    class _CountingDispatcher:
        def __init__(self, reg: ToolRegistry) -> None:
            self.registry = reg

        async def dispatch(
            self,
            name: str,
            arguments: dict[str, object],
            context: ToolContext,
            *,
            mode: str,
            exposed_tool_names: tuple[str, ...] | None = None,
        ) -> ToolResult:
            active[0] += 1
            if active[0] > max_concurrent[0]:
                max_concurrent[0] = active[0]
            await asyncio.sleep(0.05)
            active[0] -= 1
            return _ok(name)

    registry = _make_registry_with("A", "B", "C")
    dispatcher = ConcurrentToolDispatcher(
        base_dispatcher=_CountingDispatcher(registry),
        config=ConcurrencyConfig(enabled=True, max_concurrency=10),
    )
    calls = (_call("A", 0), _call("B", 1), _call("C", 2))

    results = asyncio.run(
        dispatcher.dispatch_batch(calls, _make_context(), mode="act")
    )
    assert len(results) == 3
    assert all(r.status == "ok" for r in results)
    # At least 2 must have run simultaneously (in practice all 3 do)
    assert max_concurrent[0] >= 2


# ---------------------------------------------------------------------------
# 4. Exclusive batch runs sequentially
# ---------------------------------------------------------------------------


def test_exclusive_batch_runs_sequentially() -> None:
    """Unsafe tools are dispatched one by one."""
    registry = _make_registry_with("W1", "W2", "W3", safe=False)
    mock = _MockDispatcher(registry)
    dispatcher = ConcurrentToolDispatcher(
        base_dispatcher=mock,
        config=ConcurrencyConfig(enabled=True),
    )
    calls = (_call("W1", 0), _call("W2", 1), _call("W3", 2))

    results = asyncio.run(
        dispatcher.dispatch_batch(calls, _make_context(), mode="act")
    )
    assert len(results) == 3
    assert mock.call_log == ["W1", "W2", "W3"]


# ---------------------------------------------------------------------------
# 5. Mixed batches produce results in correct (request) order
# ---------------------------------------------------------------------------


def test_mixed_batches_results_in_request_order() -> None:
    """Mixed safe/unsafe calls produce results aligned with input call order."""
    # Build registry: R1/R2 safe, W1 unsafe+mutates, R3 safe but forced exclusive
    registry = ToolRegistry()
    source = ToolSource(kind="builtin", package="test")

    def _reg(name: str, safe: bool, mutates: bool = False) -> None:
        registry.register(
            ToolManifest(
                name=name,
                description=name,
                kind="core",
                source=source,
                permission="read" if safe else "write",
                inputSchema={"type": "object"},
                timeoutMs=30_000,
                parallelSafety="readonly" if safe else "unsafe",
                mutatesWorkspace=mutates,
                availableInModes=("plan", "act"),
                enabled_by_default=True,
                sideEffectClass="local_workspace" if mutates else "none",
            )
        )

    _reg("R1", safe=True)
    _reg("R2", safe=True)
    _reg("W1", safe=False, mutates=True)
    _reg("R3", safe=True)  # forced exclusive after W1

    mock = _MockDispatcher(
        registry,
        results={
            "R1": _ok("R1"),
            "R2": _ok("R2"),
            "W1": _ok("W1"),
            "R3": _ok("R3"),
        },
    )
    dispatcher = ConcurrentToolDispatcher(
        base_dispatcher=mock,
        config=ConcurrencyConfig(enabled=True),
    )
    calls = (_call("R1", 0), _call("R2", 1), _call("W1", 2), _call("R3", 3))

    results = asyncio.run(
        dispatcher.dispatch_batch(calls, _make_context(), mode="act")
    )
    assert len(results) == 4
    assert results[0].output == {"tool": "R1"}
    assert results[1].output == {"tool": "R2"}
    assert results[2].output == {"tool": "W1"}
    assert results[3].output == {"tool": "R3"}


# ---------------------------------------------------------------------------
# 6. Error isolation — one failure does not cascade
# ---------------------------------------------------------------------------


def test_error_isolation_in_concurrent_batch() -> None:
    """When one concurrent tool raises, others still succeed."""
    registry = _make_registry_with("A", "B", "C")
    mock = _MockDispatcher(
        registry,
        results={"A": _ok("A"), "C": _ok("C")},
        raise_for={"B"},
    )
    dispatcher = ConcurrentToolDispatcher(
        base_dispatcher=mock,
        config=ConcurrencyConfig(enabled=True, max_concurrency=10),
    )
    calls = (_call("A", 0), _call("B", 1), _call("C", 2))

    results = asyncio.run(
        dispatcher.dispatch_batch(calls, _make_context(), mode="act")
    )
    assert len(results) == 3
    assert results[0].status == "ok"
    assert results[1].status == "error"
    assert results[1].error_code == "concurrent_dispatch_error"
    assert results[2].status == "ok"


# ---------------------------------------------------------------------------
# 7. Semaphore limits concurrency
# ---------------------------------------------------------------------------


def test_semaphore_limits_concurrency() -> None:
    """max_concurrency=2 ensures at most 2 tools run simultaneously."""
    max_active: list[int] = [0]
    active: list[int] = [0]

    class _LimitedDispatcher:
        def __init__(self, reg: ToolRegistry) -> None:
            self.registry = reg

        async def dispatch(
            self,
            name: str,
            arguments: dict[str, object],
            context: ToolContext,
            *,
            mode: str,
            exposed_tool_names: tuple[str, ...] | None = None,
        ) -> ToolResult:
            active[0] += 1
            if active[0] > max_active[0]:
                max_active[0] = active[0]
            await asyncio.sleep(0.05)
            active[0] -= 1
            return _ok(name)

    registry = _make_registry_with("T1", "T2", "T3", "T4", "T5")
    dispatcher = ConcurrentToolDispatcher(
        base_dispatcher=_LimitedDispatcher(registry),
        config=ConcurrencyConfig(enabled=True, max_concurrency=2),
    )
    calls = tuple(_call(f"T{i+1}", i) for i in range(5))

    results = asyncio.run(
        dispatcher.dispatch_batch(calls, _make_context(), mode="act")
    )
    assert len(results) == 5
    assert max_active[0] <= 2


# ---------------------------------------------------------------------------
# 8. Results returned in request order (not completion order)
# ---------------------------------------------------------------------------


def test_results_in_request_order_not_completion_order() -> None:
    """Slower tools that complete later must still appear at their original index."""
    delays = {"Fast1": 0.0, "Slow": 0.1, "Fast2": 0.0}

    class _DelayedDispatcher:
        def __init__(self, reg: ToolRegistry) -> None:
            self.registry = reg

        async def dispatch(
            self,
            name: str,
            arguments: dict[str, object],
            context: ToolContext,
            *,
            mode: str,
            exposed_tool_names: tuple[str, ...] | None = None,
        ) -> ToolResult:
            await asyncio.sleep(delays.get(name, 0.0))
            return ToolResult(status="ok", output={"name": name})

    registry = _make_registry_with("Fast1", "Slow", "Fast2")
    dispatcher = ConcurrentToolDispatcher(
        base_dispatcher=_DelayedDispatcher(registry),
        config=ConcurrencyConfig(enabled=True, max_concurrency=10),
    )
    calls = (_call("Fast1", 0), _call("Slow", 1), _call("Fast2", 2))

    results = asyncio.run(
        dispatcher.dispatch_batch(calls, _make_context(), mode="act")
    )
    assert len(results) == 3
    assert results[0].output == {"name": "Fast1"}
    assert results[1].output == {"name": "Slow"}
    assert results[2].output == {"name": "Fast2"}


# ---------------------------------------------------------------------------
# 9. ToolBatchExecution evidence recorded
# ---------------------------------------------------------------------------


def test_batch_execution_evidence_recorded() -> None:
    """ConcurrentToolDispatcher records ToolBatchExecution for each batch."""
    evidence_log: list[ToolBatchExecution] = []

    class _EvidenceDispatcher(ConcurrentToolDispatcher):
        def _record_evidence(self, evidence: ToolBatchExecution) -> None:
            evidence_log.append(evidence)

    registry = _make_registry_with("R1", "R2")
    mock = _MockDispatcher(registry)
    dispatcher = _EvidenceDispatcher(
        base_dispatcher=mock,
        config=ConcurrencyConfig(enabled=True),
    )
    calls = (_call("R1", 0), _call("R2", 1))

    asyncio.run(dispatcher.dispatch_batch(calls, _make_context(), mode="act"))

    assert len(evidence_log) == 1  # R1 + R2 are both safe -> one concurrent batch
    ev = evidence_log[0]
    assert ev.batch_index == 0
    assert ev.tool_count == 2
    assert ev.is_concurrent is True
    assert ev.tool_names == ("R1", "R2")
    assert len(ev.individual_durations) == 2
    assert len(ev.statuses) == 2
    assert all(s == "ok" for s in ev.statuses)


# ---------------------------------------------------------------------------
# 10. Exception in concurrent batch produces error ToolResult
# ---------------------------------------------------------------------------


def test_exception_converted_to_error_tool_result() -> None:
    """A RuntimeError raised by the base dispatcher in a concurrent batch is wrapped."""
    registry = _make_registry_with("Good", "Boom")
    mock = _MockDispatcher(registry, raise_for={"Boom"})
    dispatcher = ConcurrentToolDispatcher(
        base_dispatcher=mock,
        config=ConcurrencyConfig(enabled=True, max_concurrency=10),
    )
    calls = (_call("Good", 0), _call("Boom", 1))

    results = asyncio.run(
        dispatcher.dispatch_batch(calls, _make_context(), mode="act")
    )
    assert results[0].status == "ok"
    assert results[1].status == "error"
    assert results[1].error_code == "concurrent_dispatch_error"
    assert "simulated failure" in (results[1].error_message or "")


# ---------------------------------------------------------------------------
# 11. _with_duration preserves all fields
# ---------------------------------------------------------------------------


def test_with_duration_preserves_all_fields() -> None:
    original = ToolResult(
        status="ok",
        output={"x": 1},
        error_code=None,
        error_message=None,
        retryable=True,
        metadata={"k": "v"},
    )
    result = _with_duration(original, 42)
    assert result.duration_ms == 42
    assert result.status == "ok"
    assert result.output == {"x": 1}
    assert result.retryable is True
    assert result.metadata == {"k": "v"}


# ---------------------------------------------------------------------------
# 12. registry property delegates to base dispatcher
# ---------------------------------------------------------------------------


def test_registry_property_delegates() -> None:
    registry = _make_registry_with("X")
    mock = _MockDispatcher(registry)
    dispatcher = ConcurrentToolDispatcher(base_dispatcher=mock)
    assert dispatcher.registry is registry
