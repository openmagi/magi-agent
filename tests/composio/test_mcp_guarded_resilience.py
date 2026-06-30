"""WS9 PR9a-2: resilience on the live composio ADK dispatcher seam.

These exercise ``_DispatcherGuardedTool.run_async`` with the resilience policy
threaded in. The breaker MUST be keyed on the per-endpoint ``server_ref`` digest
(a ``sha256(mcp_url)`` value), NOT on ``mcp_server_label`` (which defaults to
``"composio"`` for every server and would collapse all composio endpoints to one
shared breaker). All time-sensitive paths use tiny numerics so the suite is fast
and never sleeps a real cooldown.
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.composio.mcp import (
    _DispatcherGuardedTool,
    _classify_mcp_exception,
    ComposioReceiptLedger,
)
from magi_agent.plugins.mcp_resilience import (
    CircuitBreakerRegistry,
    McpResiliencePolicy,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _AllowDecision:
    action = "allow"
    reason = "ok"
    metadata: dict[str, object] = {}


class _AllowArbiter:
    """Always-allow arbiter so we isolate the resilience behavior."""

    def decide_external_mcp_call(self, name, args, context, *, mode):  # noqa: ANN001
        return _AllowDecision()


class _FakeInner:
    """ADK-tool stand-in: counts calls, runs an injected async behavior."""

    def __init__(self, name: str, behavior) -> None:  # noqa: ANN001
        self.name = name
        self.calls = 0
        self._behavior = behavior

    async def run_async(self, *, args: dict[str, object], tool_context: object = None):
        self.calls += 1
        return await self._behavior(self.calls, dict(args))


class _AuthHttpError(Exception):
    """Carries an HTTP status so ``_classify_mcp_exception`` sees an auth signal."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"http {status_code}")
        self.status_code = status_code


def _ledger() -> ComposioReceiptLedger:
    return ComposioReceiptLedger()


def _ctx_factory(**_kwargs: object) -> object:
    return object()


def _tool(
    inner: _FakeInner,
    *,
    resilience: McpResiliencePolicy | None,
    server_ref: str | None,
    registry: CircuitBreakerRegistry | None,
    ledger: ComposioReceiptLedger | None = None,
) -> _DispatcherGuardedTool:
    return _DispatcherGuardedTool(
        inner,
        arbiter=_AllowArbiter(),
        mode="act",
        context_factory=_ctx_factory,
        receipt_ledger=ledger if ledger is not None else _ledger(),
        resilience=resilience,
        server_ref=server_ref,
        registry=registry,
    )


def _enabled_policy(**overrides: object) -> McpResiliencePolicy:
    base: dict[str, object] = {
        "enabled": True,
        "call_timeout_ms": 30000,
        "circuit_fail_threshold": 3,
        "reconnect_max_attempts": 1,
    }
    base.update(overrides)
    return McpResiliencePolicy(**base)


async def _ok(_call: int, args: dict[str, object]) -> dict[str, object]:
    return {"status": "ok", "echo": args}


async def _always_fail(_call: int, _args: dict[str, object]) -> dict[str, object]:
    raise RuntimeError("transport boom")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_guarded_tool_passthrough_when_disabled() -> None:
    ledger = _ledger()
    inner = _FakeInner("composio-GMAIL_SEND", _ok)
    tool = _tool(
        inner,
        resilience=McpResiliencePolicy(enabled=False),
        server_ref="digestA",
        registry=CircuitBreakerRegistry(),
        ledger=ledger,
    )

    result = asyncio.run(tool.run_async(args={"to": "a@b.com"}, tool_context=None))

    assert result == {"status": "ok", "echo": {"to": "a@b.com"}}
    assert inner.calls == 1
    assert len(ledger.receipts()) == 1


def test_guarded_tool_timeout_returns_structured_error() -> None:
    ledger = _ledger()

    async def _hang(_call: int, _args: dict[str, object]) -> dict[str, object]:
        await asyncio.sleep(5.0)
        return {"status": "ok"}

    inner = _FakeInner("composio-SLOW", _hang)
    tool = _tool(
        inner,
        resilience=_enabled_policy(call_timeout_ms=20),
        server_ref="digestA",
        registry=CircuitBreakerRegistry(),
        ledger=ledger,
    )

    result = asyncio.run(tool.run_async(args={}, tool_context=None))

    assert result["reason"] == "mcp_server_unreachable"
    assert result["status"] in ("error", "blocked")
    assert result["tool"] == "composio-SLOW"
    # arbiter receipt appended before the call, even though it failed.
    assert len(ledger.receipts()) == 1


def test_guarded_tool_circuit_open_short_circuits() -> None:
    registry = CircuitBreakerRegistry()
    inner = _FakeInner("composio-DEAD", _always_fail)
    tool = _tool(
        inner,
        resilience=_enabled_policy(circuit_fail_threshold=3),
        server_ref="digestA",
        registry=registry,
    )

    for _ in range(3):
        asyncio.run(tool.run_async(args={}, tool_context=None))
    assert inner.calls == 3  # one attempt per call (reconnect_max_attempts=1)

    result = asyncio.run(tool.run_async(args={}, tool_context=None))
    assert result["reason"] == "mcp_circuit_open"
    assert inner.calls == 3  # provider NOT invoked while breaker open


def test_guarded_tool_breaker_keyed_per_endpoint() -> None:
    # ONE shared registry, TWO distinct endpoint digests. Endpoint A failures
    # must NOT open endpoint B's breaker. A wrong impl keyed on the shared
    # mcp_server_label="composio" would open B too and fail this test.
    registry = CircuitBreakerRegistry()
    policy = _enabled_policy(circuit_fail_threshold=3)

    inner_a = _FakeInner("composio-A", _always_fail)
    tool_a = _tool(inner_a, resilience=policy, server_ref="digestA", registry=registry)

    inner_b = _FakeInner("composio-B", _ok)
    tool_b = _tool(inner_b, resilience=policy, server_ref="digestB", registry=registry)

    for _ in range(3):
        asyncio.run(tool_a.run_async(args={}, tool_context=None))
    # A is now open.
    open_a = asyncio.run(tool_a.run_async(args={}, tool_context=None))
    assert open_a["reason"] == "mcp_circuit_open"

    # B over a DISTINCT endpoint digest still invokes its inner tool.
    result_b = asyncio.run(tool_b.run_async(args={"to": "x"}, tool_context=None))
    assert result_b == {"status": "ok", "echo": {"to": "x"}}
    assert inner_b.calls == 1


def test_guarded_tool_auth_error_needs_reauth() -> None:
    ledger = _ledger()

    async def _auth_fail(_call: int, _args: dict[str, object]) -> dict[str, object]:
        raise _AuthHttpError(401)

    inner = _FakeInner("composio-GMAIL_SEND", _auth_fail)
    tool = _tool(
        inner,
        resilience=_enabled_policy(reconnect_max_attempts=5),
        server_ref="digestA",
        registry=CircuitBreakerRegistry(),
        ledger=ledger,
    )

    result = asyncio.run(tool.run_async(args={"to": "a@b.com"}, tool_context=None))

    assert result["reason"] == "mcp_needs_reauth"
    assert inner.calls == 1  # auth is NOT retried despite reconnect_max_attempts=5
    assert len(ledger.receipts()) == 1


def test_classify_ambiguous_is_transport() -> None:
    # An opaque exception with no auth signal is conservatively transport
    # (retryable) so a flaky network never shows a misleading "reconnect" msg.
    assert _classify_mcp_exception(RuntimeError("connection reset by peer")) == "transport"
    assert _classify_mcp_exception(_AuthHttpError(401)) == "auth"
    assert _classify_mcp_exception(_AuthHttpError(403)) == "auth"
    assert _classify_mcp_exception(Exception("invalid_grant: token expired")) == "auth"
    assert _classify_mcp_exception(Exception("503 service unavailable")) == "transport"


@pytest.mark.parametrize(
    "resilience",
    [None, McpResiliencePolicy(enabled=False)],
)
def test_guarded_tool_off_path_byte_identical(resilience) -> None:  # noqa: ANN001
    ledger = _ledger()
    inner = _FakeInner("composio-GMAIL_SEND", _ok)
    tool = _tool(
        inner,
        resilience=resilience,
        server_ref="digestA",
        registry=CircuitBreakerRegistry(),
        ledger=ledger,
    )

    result = asyncio.run(tool.run_async(args={"to": "a@b.com"}, tool_context=None))

    # exact inner await ran once, arbiter ran (receipt appended once), result is
    # the inner tool's raw result.
    assert result == {"status": "ok", "echo": {"to": "a@b.com"}}
    assert inner.calls == 1
    assert len(ledger.receipts()) == 1
