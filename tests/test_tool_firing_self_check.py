"""Tests for the tool firing self-check infrastructure (Principle 1).

Verifies that:

1. ``FiringTestHelper.assert_fires`` PASSES (``fired=True``) for a handler
   that genuinely invokes its fake provider.
2. ``FiringTestHelper.assert_fires`` FAILS (``fired=False``) for a handler
   that is a deliberate stub / silent no-op — exactly the pattern that let
   ``ImageUnderstand`` ship broken.
3. ``FiringAssertion.assert_ok()`` raises ``AssertionError`` for a no-op.
4. ``FiringAssertion.latency_ms`` is always non-negative.
5. ``ToolHealthChecker`` reports registry wiring without executing handlers.
6. ``FakeProvider`` records invocations and resets correctly.
"""
from __future__ import annotations

import asyncio

import pytest

from magi_agent.tools.context import ToolContext
from magi_agent.tools.health import (
    FakeProvider,
    FiringAssertion,
    FiringTestHelper,
    ToolHealthChecker,
    ToolHealthReport,
)
from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx() -> ToolContext:
    return ToolContext(botId="firing-test", turnId="t-health", workspaceRoot="/tmp")


def _manifest(name: str) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"{name} tool",
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
        parallel_safety="readonly",
    )


# ---------------------------------------------------------------------------
# FakeProvider
# ---------------------------------------------------------------------------

def test_fake_provider_starts_uncalled() -> None:
    provider = FakeProvider()
    assert provider.call_count == 0
    assert not provider.was_called


def test_fake_provider_records_call() -> None:
    provider = FakeProvider(return_value="hello")
    result = provider.record_call(tool_name="MyTool", arguments={"x": 1})
    assert result == "hello"
    assert provider.call_count == 1
    assert provider.was_called
    assert provider.calls[0] == {"tool_name": "MyTool", "arguments": {"x": 1}}


def test_fake_provider_reset_clears_calls() -> None:
    provider = FakeProvider()
    provider.record_call(tool_name="T", arguments={})
    provider.reset()
    assert provider.call_count == 0
    assert not provider.was_called


# ---------------------------------------------------------------------------
# FiringTestHelper — wired handler (PASSES)
# ---------------------------------------------------------------------------

def test_firing_helper_passes_for_wired_handler() -> None:
    """A handler that delegates to the provider passes the firing check."""
    provider = FakeProvider()

    def wired_handler(args: dict, ctx: ToolContext) -> ToolResult:
        provider.record_call(tool_name="WiredTool", arguments=args)
        return ToolResult(status="ok", output="wired-result")

    helper = FiringTestHelper(provider)
    assertion = helper.assert_fires(wired_handler, {}, _ctx(), tool_name="WiredTool")

    assert assertion.fired is True
    assert assertion.result is not None
    assert assertion.result.status == "ok"
    assert assertion.error is None
    assertion.assert_ok()  # must NOT raise


def test_firing_helper_latency_ms_non_negative_for_wired_handler() -> None:
    """latency_ms from FiringTestHelper is always non-negative."""
    provider = FakeProvider()

    def handler(args: dict, ctx: ToolContext) -> ToolResult:
        provider.record_call(tool_name="T", arguments=args)
        return ToolResult(status="ok")

    helper = FiringTestHelper(provider)
    assertion = helper.assert_fires(handler, {}, _ctx())

    assert assertion.latency_ms is not None
    assert assertion.latency_ms >= 0


# ---------------------------------------------------------------------------
# FiringTestHelper — no-op stub (FAILS)
# ---------------------------------------------------------------------------

def test_firing_helper_detects_silent_no_op() -> None:
    """A stub that never calls the provider is detected as a no-op (fired=False)."""
    provider = FakeProvider()

    def stub_handler(args: dict, ctx: ToolContext) -> ToolResult:
        # Simulates ImageUnderstand: returns ok but never calls the model/provider.
        return ToolResult(status="ok", output="vision not available")

    helper = FiringTestHelper(provider)
    assertion = helper.assert_fires(stub_handler, {}, _ctx(), tool_name="StubTool")

    assert assertion.fired is False


def test_firing_helper_assert_ok_raises_for_no_op() -> None:
    """assert_ok() raises AssertionError when the provider was not invoked."""
    provider = FakeProvider()

    def stub_handler(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(status="ok", output="silent no-op")

    helper = FiringTestHelper(provider)
    assertion = helper.assert_fires(stub_handler, {}, _ctx(), tool_name="StubTool")

    with pytest.raises(AssertionError, match="did NOT fire"):
        assertion.assert_ok()


def test_firing_helper_assert_no_op_raises_for_wired_handler() -> None:
    """assert_no_op() raises AssertionError when the provider WAS invoked."""
    provider = FakeProvider()

    def wired_handler(args: dict, ctx: ToolContext) -> ToolResult:
        provider.record_call(tool_name="T", arguments=args)
        return ToolResult(status="ok")

    helper = FiringTestHelper(provider)
    assertion = helper.assert_fires(wired_handler, {}, _ctx())

    with pytest.raises(AssertionError, match="fired its provider"):
        assertion.assert_no_op()


# ---------------------------------------------------------------------------
# FiringTestHelper — handler raises
# ---------------------------------------------------------------------------

def test_firing_helper_records_handler_exception() -> None:
    """A handler that raises is captured in assertion.error (fired=False here)."""
    provider = FakeProvider()

    def crashing_handler(args: dict, ctx: ToolContext) -> ToolResult:
        raise RuntimeError("backend unreachable")

    helper = FiringTestHelper(provider)
    assertion = helper.assert_fires(crashing_handler, {}, _ctx(), tool_name="CrashTool")

    assert assertion.error is not None
    assert "backend unreachable" in assertion.error
    assert assertion.result is None
    assert assertion.latency_ms is not None


# ---------------------------------------------------------------------------
# FiringTestHelper — async handler
# ---------------------------------------------------------------------------

def test_firing_helper_async_passes_for_wired_async_handler() -> None:
    """assert_fires_async works for an async handler that calls the provider."""
    provider = FakeProvider()

    async def async_wired(args: dict, ctx: ToolContext) -> ToolResult:
        provider.record_call(tool_name="AsyncTool", arguments=args)
        return ToolResult(status="ok", output="async-done")

    async def run() -> FiringAssertion:
        helper = FiringTestHelper(provider)
        return await helper.assert_fires_async(async_wired, {}, _ctx(), tool_name="AsyncTool")

    assertion = asyncio.run(run())
    assert assertion.fired is True
    assertion.assert_ok()


def test_firing_helper_async_detects_async_no_op() -> None:
    """assert_fires_async detects a no-op async handler."""
    provider = FakeProvider()

    async def async_stub(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(status="ok", output="not available")

    async def run() -> FiringAssertion:
        helper = FiringTestHelper(provider)
        return await helper.assert_fires_async(async_stub, {}, _ctx())

    assertion = asyncio.run(run())
    assert assertion.fired is False


# ---------------------------------------------------------------------------
# ToolHealthChecker
# ---------------------------------------------------------------------------

def test_health_checker_ok_for_wired_tool() -> None:
    """ToolHealthChecker reports ok for a bound tool without running it."""
    registry = ToolRegistry()
    external_provider = FakeProvider()

    def wired_handler(args: dict, ctx: ToolContext) -> ToolResult:
        raise AssertionError("ToolHealthChecker must not execute handlers")
        external_provider.record_call(tool_name="WiredTool", arguments=args)
        return ToolResult(status="ok", output="wired")

    registry.register(_manifest("WiredTool"), handler=wired_handler)

    checker = ToolHealthChecker(registry)
    report = checker.check("WiredTool")

    assert report.status == "ok"
    assert report.latency_ms is None
    assert external_provider.call_count == 0


def test_health_checker_no_handler_report() -> None:
    """ToolHealthChecker reports no_handler for a tool with no handler bound."""
    registry = ToolRegistry()
    registry.register(_manifest("UnboundTool"), handler=None)

    checker = ToolHealthChecker(registry)
    report = checker.check("UnboundTool")

    assert report.status == "no_handler"
    assert not report.healthy


def test_health_checker_does_not_execute_stub_handlers() -> None:
    """Checker stays structural even for handlers that would be silent stubs."""
    registry = ToolRegistry()

    def stub(args: dict, ctx: ToolContext) -> ToolResult:
        raise AssertionError("ToolHealthChecker must not execute handlers")

    registry.register(_manifest("StubTool"), handler=stub)
    checker = ToolHealthChecker(registry)
    report = checker.check("StubTool")

    assert report.status == "ok"
    assert report.healthy
    assert "FiringTestHelper" in report.detail


def test_health_checker_does_not_execute_raising_handler() -> None:
    """Checker must not run handlers that could mutate state or require credentials."""
    registry = ToolRegistry()

    def raising(args: dict, ctx: ToolContext) -> ToolResult:
        raise AssertionError("ToolHealthChecker must not execute handlers")

    registry.register(_manifest("RaisingTool"), handler=raising)
    checker = ToolHealthChecker(registry)
    report = checker.check("RaisingTool")

    assert report.status == "ok"
    assert report.healthy


def test_health_checker_not_found_report() -> None:
    """ToolHealthChecker reports error when tool name is not in registry."""
    registry = ToolRegistry()
    checker = ToolHealthChecker(registry)
    report = checker.check("GhostTool")

    assert report.status == "error"
    assert "not found" in report.detail


def test_health_checker_check_all_returns_report_per_enabled_tool() -> None:
    """check_all() returns one report per enabled tool available in mode."""
    registry = ToolRegistry()

    def handler_a(args: dict, ctx: ToolContext) -> ToolResult:
        raise AssertionError("ToolHealthChecker must not execute handlers")

    def raising_handler(args: dict, ctx: ToolContext) -> ToolResult:
        raise AssertionError("ToolHealthChecker must not execute handlers")

    registry.register(_manifest("ToolA"), handler=handler_a)
    registry.register(_manifest("ToolB"), handler=raising_handler)

    checker = ToolHealthChecker(registry)
    reports = checker.check_all()

    names = {r.tool_name for r in reports}
    assert "ToolA" in names
    assert "ToolB" in names
    a_report = next(r for r in reports if r.tool_name == "ToolA")
    assert a_report.status == "ok"
    b_report = next(r for r in reports if r.tool_name == "ToolB")
    assert b_report.status == "ok"


def test_health_report_latency_ms_is_none_without_handler_probe() -> None:
    """HealthReport.latency_ms is absent because structural checks do not run handlers."""
    registry = ToolRegistry()

    def stub(args: dict, ctx: ToolContext) -> ToolResult:
        raise AssertionError("ToolHealthChecker must not execute handlers")

    registry.register(_manifest("T"), handler=stub)
    checker = ToolHealthChecker(registry)
    report = checker.check("T")

    assert report.latency_ms is None
