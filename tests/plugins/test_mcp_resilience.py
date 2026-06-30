"""WS9 PR9a: tests for the reusable MCP resilience primitive.

All time/clock/sleep is injected so the suite is hermetic and fast (never sleeps
real wall-clock cooldowns). The breaker clock is a fake monotonic-ns source; the
backoff sleep is a no-op stub.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from magi_agent.plugins.mcp_resilience import (
    CircuitBreakerRegistry,
    McpResiliencePolicy,
    McpServerUnreachable,
    REASON_CIRCUIT_OPEN,
    REASON_NEEDS_REAUTH,
    REASON_SERVER_UNREACHABLE,
    call_with_resilience,
    mcp_user_message,
)


class _FakeClock:
    """Injectable monotonic-ns clock."""

    def __init__(self) -> None:
        self._ns = 1_000_000_000

    def now(self) -> int:
        return self._ns

    def advance_ms(self, ms: int) -> None:
        self._ns += ms * 1_000_000


def _no_sleep(_seconds: float) -> None:
    return None


class _AuthError(Exception):
    """Stand-in for a provider auth error type (non-retryable)."""


def _policy(**overrides: object) -> McpResiliencePolicy:
    base: dict[str, object] = {"enabled": True}
    base.update(overrides)
    return McpResiliencePolicy(**base)


def test_passthrough_when_disabled() -> None:
    registry = CircuitBreakerRegistry()
    calls = {"n": 0}

    def provider() -> object:
        calls["n"] += 1
        raise ValueError("boom")

    policy = McpResiliencePolicy()  # enabled=False
    with pytest.raises(ValueError):
        call_with_resilience(policy, registry, "A", provider)
    # Disabled => single call, exception propagates unchanged (no retry, no
    # breaker mutation): the adapter maps this to error/mcp_provider_call_failed.
    assert calls["n"] == 1
    assert registry.snapshot("A").state == "closed"


def test_timeout_yields_unreachable() -> None:
    clock = _FakeClock()
    registry = CircuitBreakerRegistry()
    policy = _policy(call_timeout_ms=50, reconnect_max_attempts=1)

    def slow_provider() -> object:
        time.sleep(5.0)  # far past the 50ms deadline; abandoned on timeout
        return {"content": []}

    start = time.monotonic()
    with pytest.raises(McpServerUnreachable) as exc:
        call_with_resilience(
            policy, registry, "A", slow_provider, clock=clock.now, sleep=_no_sleep
        )
    elapsed = time.monotonic() - start
    assert exc.value.reason_code == REASON_SERVER_UNREACHABLE
    # The provider thread does not block the assertion (returns near the 50ms
    # deadline, not the 5s sleep).
    assert elapsed < 2.0


def test_bounded_reconnect_success() -> None:
    clock = _FakeClock()
    registry = CircuitBreakerRegistry()
    policy = _policy(reconnect_max_attempts=5)
    calls = {"n": 0}

    def provider() -> object:
        calls["n"] += 1
        if calls["n"] <= policy.reconnect_max_attempts - 1:
            raise ConnectionError("transient")
        return {"content": [{"type": "text", "text": "ok"}]}

    result = call_with_resilience(
        policy, registry, "A", provider, clock=clock.now, sleep=_no_sleep
    )
    assert result == {"content": [{"type": "text", "text": "ok"}]}
    assert calls["n"] == policy.reconnect_max_attempts  # failures + 1
    assert registry.snapshot("A").state == "closed"


def test_bounded_reconnect_exhausted() -> None:
    clock = _FakeClock()
    registry = CircuitBreakerRegistry()
    policy = _policy(reconnect_max_attempts=3)
    calls = {"n": 0}

    def provider() -> object:
        calls["n"] += 1
        raise ConnectionError("always down")

    with pytest.raises(McpServerUnreachable) as exc:
        call_with_resilience(
            policy, registry, "A", provider, clock=clock.now, sleep=_no_sleep
        )
    assert exc.value.reason_code == REASON_SERVER_UNREACHABLE
    # Exactly reconnect_max_attempts invocations, never more (burn-bug fixed).
    assert calls["n"] == 3


def _drive_failures(registry, policy, clock, n: int) -> int:
    calls = {"n": 0}

    def provider() -> object:
        calls["n"] += 1
        raise ConnectionError("down")

    for _ in range(n):
        try:
            call_with_resilience(
                policy, registry, "A", provider, clock=clock.now, sleep=_no_sleep
            )
        except McpServerUnreachable:
            pass
    return calls["n"]


def test_circuit_opens_after_threshold() -> None:
    clock = _FakeClock()
    registry = CircuitBreakerRegistry()
    policy = _policy(circuit_fail_threshold=3, reconnect_max_attempts=1)

    invoked = _drive_failures(registry, policy, clock, 3)
    assert invoked == 3
    assert registry.snapshot("A").state == "open"

    # 4th call short-circuits: provider NOT invoked, call count frozen at 3.
    def provider() -> object:
        raise AssertionError("provider must not be invoked while breaker open")

    with pytest.raises(McpServerUnreachable) as exc:
        call_with_resilience(
            policy, registry, "A", provider, clock=clock.now, sleep=_no_sleep
        )
    assert exc.value.reason_code == REASON_CIRCUIT_OPEN


def test_circuit_half_open_recovers() -> None:
    clock = _FakeClock()
    registry = CircuitBreakerRegistry()
    policy = _policy(
        circuit_fail_threshold=3, reconnect_max_attempts=1, circuit_cooldown_ms=60000
    )
    _drive_failures(registry, policy, clock, 3)
    assert registry.snapshot("A").state == "open"

    clock.advance_ms(60001)  # past cooldown
    calls = {"n": 0}

    def provider() -> object:
        calls["n"] += 1
        return {"content": [{"type": "text", "text": "recovered"}]}

    result = call_with_resilience(
        policy, registry, "A", provider, clock=clock.now, sleep=_no_sleep
    )
    assert result == {"content": [{"type": "text", "text": "recovered"}]}
    assert calls["n"] == 1  # one half-open trial
    assert registry.snapshot("A").state == "closed"

    # Subsequent calls flow normally.
    again = call_with_resilience(
        policy, registry, "A", provider, clock=clock.now, sleep=_no_sleep
    )
    assert again == {"content": [{"type": "text", "text": "recovered"}]}
    assert calls["n"] == 2


def test_half_open_admits_exactly_one() -> None:
    clock = _FakeClock()
    registry = CircuitBreakerRegistry()
    policy = _policy(
        circuit_fail_threshold=3, reconnect_max_attempts=1, circuit_cooldown_ms=60000
    )
    _drive_failures(registry, policy, clock, 3)
    clock.advance_ms(60001)

    release = threading.Event()
    counter_lock = threading.Lock()
    call_count = {"n": 0}
    results: list[tuple[str, object]] = []
    results_lock = threading.Lock()

    def provider() -> object:
        with counter_lock:
            call_count["n"] += 1
        release.wait(2.0)  # block the single trial while peers race allow()
        return {"content": [{"type": "text", "text": "ok"}]}

    def worker() -> None:
        try:
            out = call_with_resilience(
                policy, registry, "A", provider, clock=clock.now, sleep=_no_sleep
            )
            with results_lock:
                results.append(("ok", out))
        except McpServerUnreachable as err:
            with results_lock:
                results.append(("denied", err.reason_code))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()

    # Wait until the single admitted trial has entered the provider.
    deadline = time.monotonic() + 2.0
    while call_count["n"] < 1 and time.monotonic() < deadline:
        time.sleep(0.001)
    time.sleep(0.05)  # give the other 7 callers time to be denied
    release.set()
    for t in threads:
        t.join(timeout=3.0)

    assert call_count["n"] == 1  # provider invoked at most once
    denied = [r for r in results if r[0] == "denied"]
    assert len(denied) == 7
    assert all(code == REASON_CIRCUIT_OPEN for _, code in denied)


def test_half_open_failure_rearms() -> None:
    clock = _FakeClock()
    registry = CircuitBreakerRegistry()
    policy = _policy(
        circuit_fail_threshold=3, reconnect_max_attempts=1, circuit_cooldown_ms=60000
    )
    _drive_failures(registry, policy, clock, 3)
    clock.advance_ms(60001)

    def failing_provider() -> object:
        raise ConnectionError("still down")

    with pytest.raises(McpServerUnreachable):
        call_with_resilience(
            policy, registry, "A", failing_provider, clock=clock.now, sleep=_no_sleep
        )
    state = registry.snapshot("A")
    assert state.state == "open"  # re-armed, not stuck half_open
    assert state.opened_at_ns == clock.now()  # cooldown reset to "now"
    assert state.half_open_in_flight is False


def test_half_open_base_exception_does_not_stick_breaker() -> None:
    # A non-Exception escape (KeyboardInterrupt et al.) during the single
    # half-open trial must not leave the in-flight latch set, which would deny
    # every future call forever. The breaker must re-arm (clear the latch).
    clock = _FakeClock()
    registry = CircuitBreakerRegistry()
    policy = _policy(
        circuit_fail_threshold=3, reconnect_max_attempts=1, circuit_cooldown_ms=60000
    )
    _drive_failures(registry, policy, clock, 3)
    clock.advance_ms(60001)

    def interrupted_provider() -> object:
        raise KeyboardInterrupt("ctrl-c mid-trial")

    with pytest.raises(KeyboardInterrupt):
        call_with_resilience(
            policy, registry, "A", interrupted_provider, clock=clock.now, sleep=_no_sleep
        )
    state = registry.snapshot("A")
    assert state.half_open_in_flight is False  # latch cleared, not stuck
    assert state.state == "open"  # re-armed for a future trial after cooldown


def test_auth_error_not_retried_and_needs_reauth() -> None:
    clock = _FakeClock()
    registry = CircuitBreakerRegistry()
    policy = _policy(reconnect_max_attempts=5)
    calls = {"n": 0}

    def provider() -> object:
        calls["n"] += 1
        raise _AuthError("invalid_grant")

    with pytest.raises(_AuthError):
        call_with_resilience(
            policy,
            registry,
            "A",
            provider,
            auth_error_types=(_AuthError,),
            clock=clock.now,
            sleep=_no_sleep,
        )
    assert calls["n"] == 1  # NOT retried
    # A breaker failure is still recorded (a dead-auth server should eventually open).
    assert registry.snapshot("A").consecutive_failures == 1

    # The reason code maps to a user-visible needs-reauth message.
    decision = SimpleNamespace(
        reason_codes=(REASON_NEEDS_REAUTH,),
        diagnostic_metadata={"serverRef": "mcp:notes"},
    )
    message = mcp_user_message(decision)
    assert message is not None
    assert "Reconnect" in message
    assert "mcp:notes" in message


def test_breaker_per_server_isolation() -> None:
    clock = _FakeClock()
    registry = CircuitBreakerRegistry()
    policy = _policy(circuit_fail_threshold=3, reconnect_max_attempts=1)

    # Two distinct per-endpoint keys (distinct mcp_url digests, not two labels).
    ref_a = "mcp:" + "a" * 16
    ref_b = "mcp:" + "b" * 16

    def down() -> object:
        raise ConnectionError("down")

    for _ in range(3):
        try:
            call_with_resilience(
                policy, registry, ref_a, down, clock=clock.now, sleep=_no_sleep
            )
        except McpServerUnreachable:
            pass

    assert registry.snapshot(ref_a).state == "open"
    # Endpoint B is untouched and still admits calls.
    assert registry.snapshot(ref_b).state == "closed"

    calls_b = {"n": 0}

    def ok_b() -> object:
        calls_b["n"] += 1
        return {"content": [{"type": "text", "text": "b-ok"}]}

    result = call_with_resilience(
        policy, registry, ref_b, ok_b, clock=clock.now, sleep=_no_sleep
    )
    assert result == {"content": [{"type": "text", "text": "b-ok"}]}
    assert calls_b["n"] == 1


def test_mcp_user_message_mapping() -> None:
    server_meta = {"serverRef": "mcp:zapier"}
    for code in (REASON_NEEDS_REAUTH, REASON_SERVER_UNREACHABLE, REASON_CIRCUIT_OPEN):
        decision = SimpleNamespace(
            reason_codes=(code,), diagnostic_metadata=server_meta
        )
        message = mcp_user_message(decision)
        assert isinstance(message, str)
        assert message
        assert "mcp:zapier" in message

    ok_decision = SimpleNamespace(reason_codes=(), diagnostic_metadata={})
    assert mcp_user_message(ok_decision) is None
