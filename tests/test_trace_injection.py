"""Tests for PR2 trace injection into tool, hook, evidence, and harness modules.

Verifies that trace.record() calls are emitted at the correct points when
tracing is active, and that no crash occurs when tracing is disabled.
"""
from __future__ import annotations

import asyncio

import pytest

from magi_agent.telemetry.execution_trace import ExecutionTrace
from magi_agent.telemetry.trace_context import get_trace, set_trace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_trace(turn_id: str = "test-turn") -> ExecutionTrace:
    trace = ExecutionTrace(turn_id=turn_id)
    set_trace(trace)
    return trace


def _teardown_trace() -> None:
    set_trace(None)


def _entries_for(trace: ExecutionTrace, *, layer: str | None = None, action: str | None = None) -> list[dict]:
    entries = trace.to_json()
    result = entries
    if layer is not None:
        result = [e for e in result if e["layer"] == layer]
    if action is not None:
        result = [e for e in result if e["action"] == action]
    return result


# ---------------------------------------------------------------------------
# tools/dispatcher.py
# ---------------------------------------------------------------------------


class TestToolDispatcherTraceInjection:
    """Verify trace points in ToolDispatcher.dispatch()."""

    def _make_dispatcher(self) -> object:
        from magi_agent.tools.context import ToolContext
        from magi_agent.tools.dispatcher import ToolDispatcher
        from magi_agent.tools.manifest import ToolManifest, ToolSource
        from magi_agent.tools.registry import ToolRegistry
        from magi_agent.tools.result import ToolResult

        registry = ToolRegistry()
        source = ToolSource(kind="builtin", package="test")
        manifest = ToolManifest(
            name="TestTool",
            description="A test tool",
            kind="core",
            source=source,
            permission="read",
            inputSchema={"type": "object"},
            timeoutMs=30_000,
            parallelSafety="readonly",
            availableInModes=("plan", "act"),
            enabled_by_default=True,
        )
        registry.register(manifest, handler=lambda args, ctx: ToolResult(status="ok", output="done"))
        dispatcher = ToolDispatcher(registry)
        return dispatcher, ToolContext(botId="test-bot")

    def test_resolve_trace_recorded(self) -> None:
        trace = _setup_trace()
        try:
            dispatcher, ctx = self._make_dispatcher()
            asyncio.run(
                dispatcher.dispatch("TestTool", {}, ctx, mode="act")
            )
            resolve_entries = _entries_for(trace, layer="tool", action="resolve")
            assert len(resolve_entries) >= 1
            assert "found=True" in resolve_entries[0]["detail"]
        finally:
            _teardown_trace()

    def test_permission_check_trace_recorded(self) -> None:
        trace = _setup_trace()
        try:
            dispatcher, ctx = self._make_dispatcher()
            asyncio.run(
                dispatcher.dispatch("TestTool", {}, ctx, mode="act")
            )
            perm_entries = _entries_for(trace, layer="tool", action="permission_check")
            assert len(perm_entries) >= 1
            assert "decision=allow" in perm_entries[0]["detail"]
        finally:
            _teardown_trace()

    def test_execute_trace_with_duration(self) -> None:
        trace = _setup_trace()
        try:
            dispatcher, ctx = self._make_dispatcher()
            asyncio.run(
                dispatcher.dispatch("TestTool", {}, ctx, mode="act")
            )
            exec_entries = _entries_for(trace, layer="tool", action="execute")
            assert len(exec_entries) >= 1
            assert "status=ok" in exec_entries[0]["detail"]
            assert exec_entries[0]["duration_ms"] is not None
            assert exec_entries[0]["duration_ms"] >= 0
        finally:
            _teardown_trace()

    def test_resolve_not_found_trace(self) -> None:
        trace = _setup_trace()
        try:
            dispatcher, ctx = self._make_dispatcher()
            asyncio.run(
                dispatcher.dispatch("NonExistent", {}, ctx, mode="act")
            )
            resolve_entries = _entries_for(trace, layer="tool", action="resolve")
            assert len(resolve_entries) >= 1
            assert "found=False" in resolve_entries[0]["detail"]
        finally:
            _teardown_trace()

    def test_no_crash_when_trace_disabled(self) -> None:
        set_trace(None)
        dispatcher, ctx = self._make_dispatcher()
        result = asyncio.run(
            dispatcher.dispatch("TestTool", {}, ctx, mode="act")
        )
        assert result.status == "ok"


# ---------------------------------------------------------------------------
# tools/concurrent_dispatcher.py
# ---------------------------------------------------------------------------


class TestConcurrentDispatcherTraceInjection:
    """Verify trace points in ConcurrentToolDispatcher.dispatch_batch()."""

    def _make_concurrent_dispatcher(self, *, enabled: bool = True):
        from magi_agent.tools.concurrency import ConcurrencyConfig, ToolCall
        from magi_agent.tools.concurrent_dispatcher import ConcurrentToolDispatcher
        from magi_agent.tools.context import ToolContext
        from magi_agent.tools.manifest import ToolManifest, ToolSource
        from magi_agent.tools.registry import ToolRegistry
        from magi_agent.tools.result import ToolResult

        registry = ToolRegistry()
        source = ToolSource(kind="builtin", package="test")
        for name in ("Read", "Write"):
            manifest = ToolManifest(
                name=name,
                description=f"Tool {name}",
                kind="core",
                source=source,
                permission="read",
                inputSchema={"type": "object"},
                timeoutMs=30_000,
                parallelSafety="readonly",
                availableInModes=("plan", "act"),
                enabled_by_default=True,
            )
            registry.register(manifest, handler=lambda args, ctx: ToolResult(status="ok", output="done"))

        class MockBaseDispatcher:
            def __init__(self, reg: ToolRegistry) -> None:
                self.registry = reg

            async def dispatch(self, name, arguments, context, *, mode, exposed_tool_names=None):
                return ToolResult(status="ok", output=f"{name}_done")

        config = ConcurrencyConfig(enabled=enabled, max_concurrency=4)
        base = MockBaseDispatcher(registry)
        dispatcher = ConcurrentToolDispatcher(base, config)
        ctx = ToolContext(botId="test-bot")
        calls = (
            ToolCall(name="Read", arguments={}, tool_use_id="call_0"),
            ToolCall(name="Write", arguments={}, tool_use_id="call_1"),
        )
        return dispatcher, ctx, calls

    def test_partition_trace_recorded(self) -> None:
        trace = _setup_trace()
        try:
            dispatcher, ctx, calls = self._make_concurrent_dispatcher(enabled=True)
            asyncio.run(
                dispatcher.dispatch_batch(calls, ctx, mode="act")
            )
            partition_entries = _entries_for(trace, layer="tool", action="partition")
            assert len(partition_entries) >= 1
            assert "batch_count=" in partition_entries[0]["detail"]
            assert "total_calls=2" in partition_entries[0]["detail"]
        finally:
            _teardown_trace()

    def test_batch_start_end_trace_recorded(self) -> None:
        trace = _setup_trace()
        try:
            dispatcher, ctx, calls = self._make_concurrent_dispatcher(enabled=True)
            asyncio.run(
                dispatcher.dispatch_batch(calls, ctx, mode="act")
            )
            start_entries = _entries_for(trace, layer="tool", action="batch_start")
            end_entries = _entries_for(trace, layer="tool", action="batch_end")
            assert len(start_entries) >= 1
            assert len(end_entries) >= 1
            assert "index=" in start_entries[0]["detail"]
            assert "concurrent=" in start_entries[0]["detail"]
            # batch_end should have duration_ms
            assert end_entries[0]["duration_ms"] is not None
            assert end_entries[0]["duration_ms"] >= 0
        finally:
            _teardown_trace()

    def test_no_crash_when_trace_disabled(self) -> None:
        set_trace(None)
        dispatcher, ctx, calls = self._make_concurrent_dispatcher(enabled=True)
        results = asyncio.run(
            dispatcher.dispatch_batch(calls, ctx, mode="act")
        )
        assert len(results) == 2


# ---------------------------------------------------------------------------
# hooks/bus.py
# ---------------------------------------------------------------------------


class TestHookBusTraceInjection:
    """Verify trace points in HookBus.run() and run_async()."""

    def _make_bus(self, *, hook_count: int = 1):
        from magi_agent.harness.resolved import build_default_resolved_harness_state
        from magi_agent.hooks.bus import HookBus, RegisteredHook
        from magi_agent.hooks.context import HookContext
        from magi_agent.hooks.manifest import HookManifest, HookPoint, HookScope
        from magi_agent.hooks.result import HookResult
        from magi_agent.tools.manifest import ToolSource

        hooks = []
        for i in range(hook_count):
            manifest = HookManifest(
                name=f"test-hook-{i}",
                point=HookPoint.BEFORE_TOOL_USE,
                description=f"test hook {i}",
                source=ToolSource(kind="builtin", package="test"),
                blocking=True,
                fail_open=True,
                enabled=True,
                priority=i,
                scope=HookScope(),
            )
            hooks.append(RegisteredHook(
                manifest=manifest,
                handler=lambda ctx: HookResult(action="continue"),
            ))

        bus = HookBus(hooks=tuple(hooks))
        context = HookContext(bot_id="test-bot")
        harness_state = build_default_resolved_harness_state()
        return bus, context, harness_state

    def test_run_trace_recorded(self) -> None:
        from magi_agent.hooks.manifest import HookPoint

        trace = _setup_trace()
        try:
            bus, context, harness_state = self._make_bus(hook_count=2)
            bus.run(point=HookPoint.BEFORE_TOOL_USE, context=context, harness_state=harness_state)
            run_entries = _entries_for(trace, layer="hook", action="run")
            assert len(run_entries) >= 1
            assert "point=beforeToolUse" in run_entries[0]["detail"]
            assert "effective=" in run_entries[0]["detail"]
        finally:
            _teardown_trace()

    def test_run_async_trace_recorded(self) -> None:
        from magi_agent.hooks.manifest import HookPoint

        trace = _setup_trace()
        try:
            bus, context, harness_state = self._make_bus(hook_count=2)
            asyncio.run(
                bus.run_async(point=HookPoint.BEFORE_TOOL_USE, context=context, harness_state=harness_state)
            )
            run_entries = _entries_for(trace, layer="hook", action="run")
            assert len(run_entries) >= 1
            assert "point=beforeToolUse" in run_entries[0]["detail"]
        finally:
            _teardown_trace()

    def test_no_crash_when_trace_disabled(self) -> None:
        from magi_agent.hooks.manifest import HookPoint

        set_trace(None)
        bus, context, harness_state = self._make_bus()
        result = bus.run(point=HookPoint.BEFORE_TOOL_USE, context=context, harness_state=harness_state)
        assert result.final_action == "continue"


# ---------------------------------------------------------------------------
# evidence/contracts.py
# ---------------------------------------------------------------------------


class TestEvidenceContractTraceInjection:
    """Verify trace points in evaluate_evidence_contract()."""

    def test_evaluate_trace_recorded(self) -> None:
        from magi_agent.evidence.contracts import evaluate_evidence_contract
        from magi_agent.evidence.types import (
            EvidenceContract,
            EvidenceRecord,
            EvidenceRequirement,
            EvidenceSource,
        )

        trace = _setup_trace()
        try:
            contract = EvidenceContract(
                id="test-contract-1",
                triggers=("beforeCommit",),
                requirements=(
                    EvidenceRequirement(type="TestRun", fields={}),
                ),
                on_missing="audit",
            )
            record = EvidenceRecord(
                type="TestRun",
                status="ok",
                fields={},
                source=EvidenceSource(kind="tool_trace"),
                observed_at=1000,
            )
            verdict = evaluate_evidence_contract(contract, [record])
            eval_entries = _entries_for(trace, layer="evidence", action="evaluate")
            assert len(eval_entries) >= 1
            assert "contract_id=test-contract-1" in eval_entries[0]["detail"]
            assert "ok=True" in eval_entries[0]["detail"]
        finally:
            _teardown_trace()

    def test_evaluate_failing_contract_trace(self) -> None:
        from magi_agent.evidence.contracts import evaluate_evidence_contract
        from magi_agent.evidence.types import (
            EvidenceContract,
            EvidenceRequirement,
        )

        trace = _setup_trace()
        try:
            contract = EvidenceContract(
                id="test-contract-fail",
                triggers=("beforeCommit",),
                requirements=(
                    EvidenceRequirement(type="TestRun", fields={}),
                ),
                on_missing="audit",
            )
            verdict = evaluate_evidence_contract(contract, [])
            eval_entries = _entries_for(trace, layer="evidence", action="evaluate")
            assert len(eval_entries) >= 1
            assert "ok=False" in eval_entries[0]["detail"]
        finally:
            _teardown_trace()

    def test_no_crash_when_trace_disabled(self) -> None:
        from magi_agent.evidence.contracts import evaluate_evidence_contract
        from magi_agent.evidence.types import (
            EvidenceContract,
            EvidenceRequirement,
        )

        set_trace(None)
        contract = EvidenceContract(
            id="test-no-trace",
            triggers=("beforeCommit",),
            requirements=(
                EvidenceRequirement(type="TestRun", fields={}),
            ),
            on_missing="audit",
        )
        verdict = evaluate_evidence_contract(contract, [])
        assert verdict.ok is False


# ---------------------------------------------------------------------------
# harness/engine.py
# ---------------------------------------------------------------------------


class TestHarnessEngineTraceInjection:
    """Verify trace points in HarnessEngine.resolve()."""

    def test_resolve_trace_recorded(self) -> None:
        from magi_agent.harness.engine import HarnessEngine, HarnessResolutionRequest
        from magi_agent.hooks.manifest import HookManifest, HookPoint, HookScope
        from magi_agent.tools.manifest import ToolSource

        trace = _setup_trace()
        try:
            source = ToolSource(kind="builtin", package="test")
            hooks = (
                HookManifest(
                    name="h1",
                    point=HookPoint.BEFORE_TOOL_USE,
                    description="test hook h1",
                    source=source,
                    blocking=True,
                    fail_open=True,
                    enabled=True,
                    priority=0,
                    scope=HookScope(),
                ),
                HookManifest(
                    name="h2",
                    point=HookPoint.AFTER_TOOL_USE,
                    description="test hook h2",
                    source=source,
                    blocking=True,
                    fail_open=True,
                    enabled=True,
                    priority=0,
                    scope=HookScope(),
                ),
            )
            engine = HarnessEngine(hooks=hooks)
            request = HarnessResolutionRequest(agent_role="general")
            selected, state = engine.resolve(request)
            resolve_entries = _entries_for(trace, layer="harness", action="resolve")
            assert len(resolve_entries) >= 1
            assert "hooks=" in resolve_entries[0]["detail"]
            assert "role=general" in resolve_entries[0]["detail"]
        finally:
            _teardown_trace()

    def test_no_crash_when_trace_disabled(self) -> None:
        from magi_agent.harness.engine import HarnessEngine, HarnessResolutionRequest

        set_trace(None)
        engine = HarnessEngine()
        request = HarnessResolutionRequest(agent_role="general")
        selected, state = engine.resolve(request)
        assert isinstance(selected, tuple)
