"""Tests for telemetry/execution_trace.py and telemetry/trace_context.py."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest


class TestTraceEntry:
    """TraceEntry dataclass tests."""

    def test_create_entry_with_required_fields(self) -> None:
        from magi_agent.telemetry.execution_trace import TraceEntry

        entry = TraceEntry(
            timestamp=datetime(2026, 5, 27, tzinfo=timezone.utc),
            layer="turn",
            module="TurnManager",
            action="start",
        )
        assert entry.layer == "turn"
        assert entry.module == "TurnManager"
        assert entry.action == "start"
        assert entry.detail == ""
        assert entry.duration_ms is None

    def test_create_entry_with_all_fields(self) -> None:
        from magi_agent.telemetry.execution_trace import TraceEntry

        entry = TraceEntry(
            timestamp=datetime(2026, 5, 27, tzinfo=timezone.utc),
            layer="tool",
            module="ToolDispatcher",
            action="resolve",
            detail="name=Read",
            duration_ms=42,
        )
        assert entry.detail == "name=Read"
        assert entry.duration_ms == 42

    def test_entry_is_frozen(self) -> None:
        from magi_agent.telemetry.execution_trace import TraceEntry

        entry = TraceEntry(
            timestamp=datetime(2026, 5, 27, tzinfo=timezone.utc),
            layer="hook",
            module="HookBus",
            action="run",
        )
        with pytest.raises(FrozenInstanceError):
            entry.layer = "tool"  # type: ignore[misc]


class TestExecutionTrace:
    """ExecutionTrace recording and export tests."""

    def test_record_entries(self) -> None:
        from magi_agent.telemetry.execution_trace import ExecutionTrace

        trace = ExecutionTrace(turn_id="turn-001")
        trace.record(layer="turn", module="TurnManager", action="start")
        trace.record(layer="tool", module="ToolDispatcher", action="resolve", detail="name=Read", duration_ms=15)

        entries = trace.to_json()
        assert len(entries) == 2
        assert entries[0]["layer"] == "turn"
        assert entries[1]["detail"] == "name=Read"
        assert entries[1]["duration_ms"] == 15

    def test_summary_formatting(self) -> None:
        from magi_agent.telemetry.execution_trace import ExecutionTrace

        trace = ExecutionTrace(turn_id="turn-002")
        trace.record(layer="hook", module="HookBus", action="run", detail="point=beforeToolUse, effective=3", duration_ms=5)
        trace.record(layer="tool", module="ToolDispatcher", action="execute", detail="name=Bash", duration_ms=120)

        summary = trace.summary()
        assert "turn-002" in summary
        assert "hook" in summary
        assert "tool" in summary
        assert "HookBus" in summary
        assert "ToolDispatcher" in summary
        # Multi-line
        assert "\n" in summary

    def test_json_export_keys(self) -> None:
        from magi_agent.telemetry.execution_trace import ExecutionTrace

        trace = ExecutionTrace(turn_id="turn-003")
        trace.record(layer="evidence", module="EvidenceLedger", action="evaluate", duration_ms=8)

        entries = trace.to_json()
        assert len(entries) == 1
        entry = entries[0]
        assert set(entry.keys()) == {"timestamp", "layer", "module", "action", "detail", "duration_ms"}
        # Timestamp should be ISO string
        datetime.fromisoformat(entry["timestamp"])

    def test_json_is_serializable(self) -> None:
        from magi_agent.telemetry.execution_trace import ExecutionTrace

        trace = ExecutionTrace(turn_id="turn-json")
        trace.record(layer="context", module="ContextManager", action="compress", duration_ms=50)
        # Must not raise
        json.dumps(trace.to_json())

    def test_duration_breakdown_per_layer(self) -> None:
        from magi_agent.telemetry.execution_trace import ExecutionTrace

        trace = ExecutionTrace(turn_id="turn-004")
        trace.record(layer="hook", module="HookBus", action="run", duration_ms=10)
        trace.record(layer="hook", module="HookBus", action="run", duration_ms=20)
        trace.record(layer="tool", module="ToolDispatcher", action="execute", duration_ms=100)
        trace.record(layer="turn", module="TurnManager", action="complete")  # No duration

        breakdown = trace.duration_breakdown()
        assert breakdown["hook"] == 30
        assert breakdown["tool"] == 100
        assert "turn" not in breakdown  # None durations excluded

    def test_empty_trace_summary(self) -> None:
        from magi_agent.telemetry.execution_trace import ExecutionTrace

        trace = ExecutionTrace(turn_id="turn-empty")
        summary = trace.summary()
        assert "turn-empty" in summary
        assert isinstance(summary, str)

    def test_empty_trace_json(self) -> None:
        from magi_agent.telemetry.execution_trace import ExecutionTrace

        trace = ExecutionTrace(turn_id="turn-empty-json")
        assert trace.to_json() == []

    def test_empty_trace_duration_breakdown(self) -> None:
        from magi_agent.telemetry.execution_trace import ExecutionTrace

        trace = ExecutionTrace(turn_id="turn-empty-bd")
        assert trace.duration_breakdown() == {}

    def test_turn_id_stored(self) -> None:
        from magi_agent.telemetry.execution_trace import ExecutionTrace

        trace = ExecutionTrace(turn_id="my-turn-123")
        assert trace.turn_id == "my-turn-123"


class TestTraceContext:
    """trace_context.py contextvars and env gate tests."""

    def test_get_trace_returns_none_by_default(self) -> None:
        from magi_agent.telemetry.trace_context import get_trace

        # In a fresh context, should be None
        assert get_trace() is None

    def test_set_and_get_trace(self) -> None:
        from magi_agent.telemetry.execution_trace import ExecutionTrace
        from magi_agent.telemetry.trace_context import get_trace, set_trace

        trace = ExecutionTrace(turn_id="ctx-test")
        set_trace(trace)
        assert get_trace() is trace

        # Clean up
        set_trace(None)  # type: ignore[arg-type]

    def test_trace_enabled_reads_env(self) -> None:
        from magi_agent.telemetry.trace_context import trace_enabled

        old = os.environ.get("MAGI_EXECUTION_TRACE")
        try:
            os.environ["MAGI_EXECUTION_TRACE"] = "1"
            assert trace_enabled() is True

            os.environ["MAGI_EXECUTION_TRACE"] = "true"
            assert trace_enabled() is True

            os.environ["MAGI_EXECUTION_TRACE"] = "yes"
            assert trace_enabled() is True

            os.environ["MAGI_EXECUTION_TRACE"] = "0"
            assert trace_enabled() is False

            os.environ["MAGI_EXECUTION_TRACE"] = "false"
            assert trace_enabled() is False

            os.environ["MAGI_EXECUTION_TRACE"] = ""
            assert trace_enabled() is False
        finally:
            if old is None:
                os.environ.pop("MAGI_EXECUTION_TRACE", None)
            else:
                os.environ["MAGI_EXECUTION_TRACE"] = old

    def test_trace_enabled_default_false(self) -> None:
        from magi_agent.telemetry.trace_context import trace_enabled

        old = os.environ.pop("MAGI_EXECUTION_TRACE", None)
        try:
            assert trace_enabled() is False
        finally:
            if old is not None:
                os.environ["MAGI_EXECUTION_TRACE"] = old

    def test_concurrent_traces_via_contextvars(self) -> None:
        """Verify that concurrent async tasks each see their own trace."""
        from magi_agent.telemetry.execution_trace import ExecutionTrace
        from magi_agent.telemetry.trace_context import get_trace, set_trace

        results: dict[str, str | None] = {}

        async def worker(name: str) -> None:
            trace = ExecutionTrace(turn_id=name)
            set_trace(trace)
            # Yield to let other tasks run
            await asyncio.sleep(0)
            current = get_trace()
            results[name] = current.turn_id if current else None

        async def main() -> None:
            await asyncio.gather(
                asyncio.create_task(worker("task-A")),
                asyncio.create_task(worker("task-B")),
                asyncio.create_task(worker("task-C")),
            )

        asyncio.run(main())

        assert results["task-A"] == "task-A"
        assert results["task-B"] == "task-B"
        assert results["task-C"] == "task-C"
