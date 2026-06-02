"""Tests for openmagi_core_agent.facades and transport.debug_trace."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmagi_core_agent.facades import execute_tool_with_hooks
from openmagi_core_agent.harness.resolved import (
    ResolvedHarnessPresetState,
    build_default_resolved_harness_state,
)
from openmagi_core_agent.hooks.bus import (
    HookBus,
    HookBusObservation,
    HookBusRunResult,
)
from openmagi_core_agent.hooks.context import HookContext
from openmagi_core_agent.hooks.manifest import HookPoint
from openmagi_core_agent.hooks.result import HookResult
from openmagi_core_agent.telemetry.execution_trace import ExecutionTrace
from openmagi_core_agent.tools.context import ToolContext
from openmagi_core_agent.tools.dispatcher import ToolDispatcher
from openmagi_core_agent.tools.result import ToolResult
from openmagi_core_agent.transport.debug_trace import router as debug_trace_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hook_context() -> HookContext:
    return HookContext(botId="test-bot")


def _make_tool_context() -> ToolContext:
    return ToolContext(botId="test-bot")


def _make_harness_state() -> ResolvedHarnessPresetState:
    return build_default_resolved_harness_state()


def _continue_run_result(harness_state: ResolvedHarnessPresetState) -> HookBusRunResult:
    return HookBusRunResult(
        final_action="continue",
        results=(),
        observation=HookBusObservation(),
        harness_state=harness_state,
    )


def _block_run_result(harness_state: ResolvedHarnessPresetState) -> HookBusRunResult:
    return HookBusRunResult(
        final_action="block",
        results=(HookResult(action="block", reason="denied"),),
        observation=HookBusObservation(blocked_by=("test-hook",)),
        harness_state=harness_state,
    )


# ---------------------------------------------------------------------------
# execute_tool_with_hooks — normal flow
# ---------------------------------------------------------------------------

class TestExecuteToolWithHooksNormalFlow:
    def test_normal_flow_returns_three_tuple(self) -> None:
        harness_state = _make_harness_state()
        continue_result = _continue_run_result(harness_state)
        tool_result = ToolResult(status="ok", output="hello")

        dispatcher = AsyncMock(spec=ToolDispatcher)
        dispatcher.dispatch = AsyncMock(return_value=tool_result)

        hook_bus = MagicMock(spec=HookBus)
        hook_bus.run = MagicMock(return_value=continue_result)

        result, before, after = asyncio.run(execute_tool_with_hooks(
            dispatcher,
            hook_bus,
            tool_name="Read",
            arguments={"path": "/tmp/x"},
            context=_make_tool_context(),
            hook_context=_make_hook_context(),
            harness_state=harness_state,
            mode="act",
        ))

        assert result.status == "ok"
        assert result.output == "hello"
        assert before is not None
        assert before.final_action == "continue"
        assert after is not None
        assert after.final_action == "continue"

    def test_normal_flow_calls_hooks_in_order(self) -> None:
        """Verify beforeToolUse then afterToolUse are called with correct HookPoints."""
        harness_state = _make_harness_state()
        continue_result = _continue_run_result(harness_state)
        tool_result = ToolResult(status="ok")

        dispatcher = AsyncMock(spec=ToolDispatcher)
        dispatcher.dispatch = AsyncMock(return_value=tool_result)

        call_order: list[str] = []

        def mock_run(
            *,
            point: HookPoint,
            context: HookContext,
            harness_state: ResolvedHarnessPresetState,
        ) -> HookBusRunResult:
            call_order.append(point.value)
            return continue_result

        hook_bus = MagicMock(spec=HookBus)
        hook_bus.run = mock_run

        asyncio.run(execute_tool_with_hooks(
            dispatcher,
            hook_bus,
            tool_name="Write",
            arguments={},
            context=_make_tool_context(),
            hook_context=_make_hook_context(),
            harness_state=harness_state,
            mode="act",
        ))

        assert call_order == ["beforeToolUse", "afterToolUse"]


# ---------------------------------------------------------------------------
# execute_tool_with_hooks — before-hook blocks
# ---------------------------------------------------------------------------

class TestExecuteToolWithHooksBlocked:
    def test_blocked_returns_blocked_result(self) -> None:
        harness_state = _make_harness_state()
        block_result = _block_run_result(harness_state)

        dispatcher = AsyncMock(spec=ToolDispatcher)
        hook_bus = MagicMock(spec=HookBus)
        hook_bus.run = MagicMock(return_value=block_result)

        result, before, after = asyncio.run(execute_tool_with_hooks(
            dispatcher,
            hook_bus,
            tool_name="Bash",
            arguments={"command": "rm -rf /"},
            context=_make_tool_context(),
            hook_context=_make_hook_context(),
            harness_state=harness_state,
            mode="act",
        ))

        assert result.status == "blocked"
        assert result.metadata.get("blocked_by") == "beforeToolUse_hook"
        assert before is not None
        assert before.final_action == "block"
        assert after is None

    def test_blocked_does_not_call_dispatch(self) -> None:
        harness_state = _make_harness_state()
        block_result = _block_run_result(harness_state)

        dispatcher = AsyncMock(spec=ToolDispatcher)
        hook_bus = MagicMock(spec=HookBus)
        hook_bus.run = MagicMock(return_value=block_result)

        asyncio.run(execute_tool_with_hooks(
            dispatcher,
            hook_bus,
            tool_name="Bash",
            arguments={},
            context=_make_tool_context(),
            hook_context=_make_hook_context(),
            harness_state=harness_state,
            mode="act",
        ))

        dispatcher.dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# Debug trace endpoint
# ---------------------------------------------------------------------------

def _test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(debug_trace_router)
    return app


class TestDebugTraceEndpoint:
    def test_404_when_tracing_disabled(self) -> None:
        client = TestClient(_test_app())
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAGI_EXECUTION_TRACE", None)
            resp = client.get("/v1/debug/trace")
        assert resp.status_code == 404
        assert resp.json()["error"] == "tracing not enabled"

    def test_204_when_no_active_trace(self) -> None:
        client = TestClient(_test_app())
        with patch.dict(os.environ, {"MAGI_EXECUTION_TRACE": "1"}):
            with patch(
                "openmagi_core_agent.transport.debug_trace.get_trace",
                return_value=None,
            ):
                resp = client.get("/v1/debug/trace")
        assert resp.status_code == 204

    def test_200_with_active_trace(self) -> None:
        trace = ExecutionTrace(turn_id="turn-abc")
        trace.record("tool", "ToolDispatcher", "dispatch", "name=Read", duration_ms=42)

        client = TestClient(_test_app())
        with patch.dict(os.environ, {"MAGI_EXECUTION_TRACE": "1"}):
            with patch(
                "openmagi_core_agent.transport.debug_trace.get_trace",
                return_value=trace,
            ):
                resp = client.get("/v1/debug/trace")

        assert resp.status_code == 200
        body = resp.json()
        assert body["turn_id"] == "turn-abc"
        assert len(body["entries"]) == 1
        assert body["entries"][0]["layer"] == "tool"
        assert body["entries"][0]["duration_ms"] == 42
        assert "duration_breakdown" in body
        assert body["duration_breakdown"]["tool"] == 42
