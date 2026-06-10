"""Tool firing health checks (Principle 1 — "Built ≠ works").

Provides two complementary utilities:

1. ``ToolHealthChecker`` — a registry-level helper that performs a lightweight
   structural check on each registered tool.  Intended for startup self-checks
   and CI gates without executing handlers.

2. ``FiringTestHelper`` — a reusable pytest-compatible helper (not a pytest
   fixture; instantiate directly) that wraps a *fake provider* recording
   whether it was actually invoked.  Use it in tool integration tests to assert
   "this tool invoked its provider."  A test using ``FiringTestHelper`` PASSES
   for a tool that invokes the fake provider and FAILS for a no-op stub — the
   exact pattern that would have caught the ``ImageUnderstand`` silent no-op.

Both utilities are **additive and read-only** — they never modify the registry
or alter tool behaviour.
"""
from __future__ import annotations

import time
import dataclasses
from typing import Literal

from .base import ToolArguments, ToolHandler
from .context import ToolContext
from .manifest import RuntimeMode, ToolManifest
from .registry import ToolRegistry
from .result import ToolResult


_FAKE_PROVIDER_TRUST_ATTR = "open" + "magi_local_fake_provider"


class FakeProvider:
    """Minimal stub that records whether it was called.

    Attach to a tool handler under test via ``FiringTestHelper``.  Each
    ``FakeProvider`` instance tracks exactly one sequence of calls; create a
    new instance per test (or call ``reset()`` between assertions).
    """

    def __init__(self, return_value: object = "fake-provider-result") -> None:
        self._return_value = return_value
        self._calls: list[dict[str, object]] = []

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def was_called(self) -> bool:
        return bool(self._calls)

    @property
    def calls(self) -> list[dict[str, object]]:
        return list(self._calls)

    def record_call(self, *, tool_name: str, arguments: dict[str, object]) -> object:
        """Record an invocation and return the configured return value."""
        self._calls.append({"tool_name": tool_name, "arguments": arguments})
        return self._return_value

    def reset(self) -> None:
        self._calls.clear()

    def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolResult:
        """Kernel-compatible execution entry-point.

        Satisfies the ``local_fake_executor`` protocol expected by
        ``ToolExecutionKernel``.
        """
        self.record_call(tool_name=tool_name, arguments=arguments)
        return ToolResult(status="ok", output=self._return_value)


setattr(FakeProvider, _FAKE_PROVIDER_TRUST_ATTR, True)


# ---------------------------------------------------------------------------
# FiringTestHelper
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class FiringAssertion:
    """Result of a single ``FiringTestHelper.assert_fires`` call."""

    tool_name: str
    fired: bool
    latency_ms: int | None
    result: ToolResult | None
    error: str | None = None

    def assert_ok(self) -> None:
        """Raise ``AssertionError`` if the tool did not fire its provider."""
        if not self.fired:
            raise AssertionError(
                f"Tool '{self.tool_name}' did NOT fire its provider — "
                f"it behaved as a silent no-op. "
                f"error={self.error!r}"
            )

    def assert_no_op(self) -> None:
        """Raise ``AssertionError`` if the tool DID fire its provider.

        Use this to write tests that *expect* a stub/no-op (e.g. confirming
        a tool is correctly disabled before a key is configured).
        """
        if self.fired:
            raise AssertionError(
                f"Tool '{self.tool_name}' fired its provider, but expected a no-op. "
                f"latency_ms={self.latency_ms}"
            )


class FiringTestHelper:
    """Reusable helper for asserting that a tool handler invokes its provider.

    Usage (in a pytest test)::

        from magi_agent.tools.health import FiringTestHelper, FakeProvider

        def test_my_tool_fires_its_provider() -> None:
            provider = FakeProvider()

            def my_tool_handler(args, ctx):
                # Good implementation: delegates to the real (or fake) provider.
                result = provider.record_call(tool_name="MyTool", arguments=args)
                return ToolResult(status="ok", output=result)

            helper = FiringTestHelper(provider)
            assertion = helper.assert_fires(my_tool_handler, {}, _make_context())
            assertion.assert_ok()          # PASSES — provider was called
            assert assertion.latency_ms is not None

        def test_stub_tool_is_caught() -> None:
            provider = FakeProvider()

            def stub_handler(args, ctx):
                # Bad implementation: never calls the provider.
                return ToolResult(status="ok", output="vision not available")

            helper = FiringTestHelper(provider)
            assertion = helper.assert_fires(stub_handler, {}, _make_context())
            assertion.assert_ok()          # FAILS — provider was never called

    The helper is synchronous; for async handlers wrap with
    ``asyncio.run(helper.assert_fires_async(...))``.
    """

    def __init__(self, provider: FakeProvider) -> None:
        self._provider = provider

    @property
    def provider(self) -> FakeProvider:
        return self._provider

    def assert_fires(
        self,
        handler: ToolHandler,
        arguments: ToolArguments,
        context: ToolContext,
        *,
        tool_name: str = "UnknownTool",
    ) -> FiringAssertion:
        """Invoke *handler* synchronously and return a ``FiringAssertion``.

        The provider's ``was_called`` state is checked *after* the handler
        returns.  The provider is NOT reset before the call — callers must
        reset between assertions when reusing a provider across multiple calls.
        """
        before_count = self._provider.call_count
        t0 = time.monotonic_ns()
        try:
            raw = handler(arguments, context)
        except Exception as exc:  # noqa: BLE001
            latency_ms = max(0, (time.monotonic_ns() - t0) // 1_000_000)
            return FiringAssertion(
                tool_name=tool_name,
                fired=self._provider.call_count > before_count,
                latency_ms=latency_ms,
                result=None,
                error=str(exc),
            )
        latency_ms = max(0, (time.monotonic_ns() - t0) // 1_000_000)
        result = ToolResult.model_validate(raw) if not isinstance(raw, ToolResult) else raw
        fired = self._provider.call_count > before_count
        return FiringAssertion(
            tool_name=tool_name,
            fired=fired,
            latency_ms=latency_ms,
            result=result,
        )

    async def assert_fires_async(
        self,
        handler: ToolHandler,
        arguments: ToolArguments,
        context: ToolContext,
        *,
        tool_name: str = "UnknownTool",
    ) -> FiringAssertion:
        """Await *handler* (if it is a coroutine) and return a ``FiringAssertion``."""
        from inspect import isawaitable

        before_count = self._provider.call_count
        t0 = time.monotonic_ns()
        try:
            maybe = handler(arguments, context)
            raw = (await maybe) if isawaitable(maybe) else maybe
        except Exception as exc:  # noqa: BLE001
            latency_ms = max(0, (time.monotonic_ns() - t0) // 1_000_000)
            return FiringAssertion(
                tool_name=tool_name,
                fired=self._provider.call_count > before_count,
                latency_ms=latency_ms,
                result=None,
                error=str(exc),
            )
        latency_ms = max(0, (time.monotonic_ns() - t0) // 1_000_000)
        result = ToolResult.model_validate(raw) if not isinstance(raw, ToolResult) else raw
        fired = self._provider.call_count > before_count
        return FiringAssertion(
            tool_name=tool_name,
            fired=fired,
            latency_ms=latency_ms,
            result=result,
        )


# ---------------------------------------------------------------------------
# ToolHealthChecker
# ---------------------------------------------------------------------------

HealthStatus = Literal["ok", "no_handler", "error"]


@dataclasses.dataclass
class ToolHealthReport:
    """Health report for a single registered tool."""

    tool_name: str
    status: HealthStatus
    latency_ms: int | None = None
    detail: str = ""

    @property
    def healthy(self) -> bool:
        return self.status == "ok"


class ToolHealthChecker:
    """Registry-level health checker for registered tools.

    Iterates over every enabled tool in *registry* and performs a structural
    check only:

    * ``no_handler`` — tool has no handler bound at all.
    * ``ok`` — tool has a bound handler.
    * ``error`` — tool is missing from the registry.

    Pass ``mode="act"`` (default) or ``"plan"`` to select which tools are
    checked.  Tools not available in the given mode are skipped.

    This checker never invokes handlers, so it cannot execute mutating, network,
    or credential-backed tools while producing a health report.  Use
    ``FiringTestHelper`` in dedicated per-tool tests for definitive provider
    firing assertions.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        mode: RuntimeMode = "act",
    ) -> None:
        self._registry = registry
        self._mode: RuntimeMode = mode

    def check_all(self) -> list[ToolHealthReport]:
        """Run health checks on all enabled tools available in the configured mode."""
        reports: list[ToolHealthReport] = []
        for manifest in self._registry.list_available(mode=self._mode):
            reports.append(self._check_one(manifest))
        return reports

    def check(self, tool_name: str) -> ToolHealthReport:
        """Run health check on a single named tool."""
        manifest = self._registry.resolve(tool_name)
        if manifest is None:
            return ToolHealthReport(
                tool_name=tool_name,
                status="error",
                detail="tool not found in registry",
            )
        return self._check_one(manifest)

    def _check_one(self, manifest: ToolManifest) -> ToolHealthReport:
        registration = self._registry.resolve_registration(manifest.name)
        if registration is None or registration.handler is None:
            return ToolHealthReport(
                tool_name=manifest.name,
                status="no_handler",
                detail="no handler bound",
            )
        return ToolHealthReport(
            tool_name=manifest.name,
            status="ok",
            detail="handler bound; firing assertions require FiringTestHelper",
        )
