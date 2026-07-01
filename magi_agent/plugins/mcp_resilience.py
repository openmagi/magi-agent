"""Reusable MCP resilience primitive (timeout / bounded reconnect / circuit breaker).

WS9 PR9a. A single, provider-agnostic primitive that the OSS contract boundary
(``McpAdapter.call_tool``) and, later, the live composio seam (PR9a-2) share. It
adds, all default-OFF behind ``McpResiliencePolicy.enabled``:

- a per-attempt call timeout (bounds the caller; see the honest caveat below),
- a bounded reconnect loop with exponential backoff (capped),
- a per-``server_ref`` circuit breaker held in a lock-guarded registry,
- non-retryable auth handling (an auth failure is surfaced, never looped on),
- a single user-message translation point (``mcp_user_message``).

Honest caveat: ``call_with_resilience`` uses an explicitly-daemon worker thread
plus an ``Event.wait`` deadline, so the timeout bounds the *caller*, not the
underlying socket. On a timeout the worker thread is abandoned (never joined),
so pytest teardown is never blocked by a still-sleeping provider. The only
synchronous provider is the local-fake; the real live paths get true socket
timeouts (PR9a-2 async via ``asyncio.wait_for``; PR9b JS).

Shared-registry safety: the sync path here takes the registry ``threading.Lock``
on every mutation. The async twin (PR9a-2) mutates registry state only inside its
single-threaded event loop. The two paths are never live together because the
sync ``McpAdapter`` path has zero production instantiations (it is a default-OFF
contract boundary). The async path must additionally take the lock OR document
that precondition, per the design.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import threading
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# Reason codes (new; they map onto the existing frozen McpCallStatus literal,
# never widen it). The field carrying them is already an open tuple[str, ...].
REASON_NEEDS_REAUTH = "mcp_needs_reauth"
REASON_SERVER_UNREACHABLE = "mcp_server_unreachable"
REASON_CIRCUIT_OPEN = "mcp_circuit_open"


_POLICY_CONFIG = ConfigDict(
    frozen=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class McpResiliencePolicy(BaseModel):
    """Immutable resilience policy. Defaults match the Hermes parity target."""

    model_config = _POLICY_CONFIG

    enabled: bool = False
    call_timeout_ms: int = Field(default=30000, ge=1, le=600000)
    circuit_fail_threshold: int = Field(default=3, ge=1, le=20)
    circuit_cooldown_ms: int = Field(default=60000, ge=1000)
    reconnect_max_attempts: int = Field(default=5, ge=1, le=10)
    reconnect_backoff_base_ms: int = Field(default=500, ge=1)
    reconnect_backoff_cap_ms: int = Field(default=60000, ge=1000)


class McpServerUnreachable(Exception):
    """Raised when reconnect attempts are exhausted or the breaker is open.

    ``reason_code`` disambiguates the two conditions for the call-site mapping:
    ``mcp_server_unreachable`` (attempts exhausted / per-attempt timeout) or
    ``mcp_circuit_open`` (short-circuited by an open breaker, provider not run).
    """

    def __init__(
        self,
        message: str = "mcp server unreachable",
        *,
        reason_code: str = REASON_SERVER_UNREACHABLE,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class _CallTimeout(Exception):
    """Internal: a single provider attempt exceeded ``call_timeout_ms``."""


CircuitState = Literal["closed", "open", "half_open"]


@dataclass
class CircuitBreakerState:
    """In-process, per-``server_ref`` breaker state. Ephemeral (no persistence)."""

    state: CircuitState = "closed"
    consecutive_failures: int = 0
    opened_at_ns: int | None = None
    # Guards the single half-open trial: True while the one admitted trial is
    # in flight so concurrent callers are short-circuited (mcp_circuit_open).
    half_open_in_flight: bool = False


_DEFAULT_REGISTRY: CircuitBreakerRegistry | None = None


class CircuitBreakerRegistry:
    """Lock-guarded map of ``server_ref`` -> :class:`CircuitBreakerState`.

    A module singleton is available via :meth:`default`; tests reset it with
    :meth:`reset_for_test`. The injectable ``clock`` (monotonic ns) is used only
    when a method is called without an explicit ``now_ns`` (so a single clock
    source can flow from ``call_with_resilience``).
    """

    def __init__(self, *, clock: Callable[[], int] = time.monotonic_ns) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._states: dict[str, CircuitBreakerState] = {}

    @classmethod
    def default(cls) -> CircuitBreakerRegistry:
        global _DEFAULT_REGISTRY
        if _DEFAULT_REGISTRY is None:
            _DEFAULT_REGISTRY = cls()
        return _DEFAULT_REGISTRY

    @classmethod
    def reset_for_test(cls) -> None:
        global _DEFAULT_REGISTRY
        _DEFAULT_REGISTRY = None

    def _now(self, now_ns: int | None) -> int:
        return now_ns if now_ns is not None else self._clock()

    def _state(self, server_ref: str) -> CircuitBreakerState:
        state = self._states.get(server_ref)
        if state is None:
            state = CircuitBreakerState()
            self._states[server_ref] = state
        return state

    def snapshot(self, server_ref: str) -> CircuitBreakerState:
        """Return the live state object for ``server_ref`` (test introspection)."""
        with self._lock:
            return self._state(server_ref)

    def allow(
        self,
        server_ref: str,
        policy: McpResiliencePolicy,
        *,
        now_ns: int | None = None,
    ) -> tuple[bool, str | None]:
        """Decide whether a call may proceed. Admits exactly one half-open trial."""
        now = self._now(now_ns)
        with self._lock:
            state = self._state(server_ref)
            if state.state == "closed":
                return True, None
            if state.state == "open":
                opened = state.opened_at_ns
                cooldown_ns = policy.circuit_cooldown_ms * 1_000_000
                if opened is not None and (now - opened) >= cooldown_ns:
                    # Transition to half-open and admit this one caller only.
                    state.state = "half_open"
                    state.half_open_in_flight = True
                    return True, None
                return False, REASON_CIRCUIT_OPEN
            # half_open: a trial is already in flight -> short-circuit others.
            if state.half_open_in_flight:
                return False, REASON_CIRCUIT_OPEN
            state.half_open_in_flight = True
            return True, None

    def record_success(self, server_ref: str) -> None:
        with self._lock:
            state = self._state(server_ref)
            state.state = "closed"
            state.consecutive_failures = 0
            state.opened_at_ns = None
            state.half_open_in_flight = False

    def record_failure(
        self,
        server_ref: str,
        policy: McpResiliencePolicy,
        *,
        now_ns: int | None = None,
    ) -> None:
        now = self._now(now_ns)
        with self._lock:
            state = self._state(server_ref)
            state.consecutive_failures += 1
            if state.state == "half_open":
                # The single trial failed: re-arm cooldown (not stuck half-open).
                state.state = "open"
                state.opened_at_ns = now
                state.half_open_in_flight = False
                return
            if state.consecutive_failures >= policy.circuit_fail_threshold:
                state.state = "open"
                state.opened_at_ns = now
                state.half_open_in_flight = False


def _run_with_timeout(
    fn: Callable[[], Any],
    timeout_s: float,
) -> Any:
    """Run ``fn`` on an explicitly-daemon thread, bounding the caller.

    On timeout the worker thread is ABANDONED (never joined) and ``_CallTimeout``
    is raised, so neither the caller nor pytest teardown blocks on a still-running
    provider. Any exception raised by ``fn`` is re-raised to the caller.
    """
    box: dict[str, Any] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - faithfully forward to caller
            box["error"] = exc
        finally:
            done.set()

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    if not done.wait(timeout_s):
        raise _CallTimeout()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def call_with_resilience(
    policy: McpResiliencePolicy,
    registry: CircuitBreakerRegistry,
    server_ref: str,
    fn: Callable[[], Any],
    *,
    reconnect: Callable[[str], None] | None = None,
    auth_error_types: tuple[type[BaseException], ...] = (),
    clock: Callable[[], int] = time.monotonic_ns,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Run ``fn`` under timeout + bounded reconnect + circuit breaker.

    When ``policy.enabled`` is False this is a pure pass-through (``fn`` is called
    exactly once and any exception propagates unchanged). When enabled:

    - an open breaker short-circuits with ``McpServerUnreachable(mcp_circuit_open)``
      WITHOUT invoking ``fn``;
    - each attempt is bounded by ``policy.call_timeout_ms``;
    - an ``auth_error_types`` exception is NON-retryable: a breaker failure is
      recorded and the original exception is re-raised (the caller maps it to
      ``mcp_needs_reauth``);
    - any other exception / timeout is retryable up to ``reconnect_max_attempts``
      with exponential backoff capped at ``reconnect_backoff_cap_ms``; once
      exhausted a breaker failure is recorded and
      ``McpServerUnreachable(mcp_server_unreachable)`` is raised.
    """
    if not policy.enabled:
        return fn()

    admitted, deny_reason = registry.allow(server_ref, policy, now_ns=clock())
    if not admitted:
        raise McpServerUnreachable(reason_code=deny_reason or REASON_CIRCUIT_OPEN)

    timeout_s = policy.call_timeout_ms / 1000.0
    last_exc: BaseException | None = None
    for attempt in range(1, policy.reconnect_max_attempts + 1):
        try:
            result = _run_with_timeout(fn, timeout_s)
        except auth_error_types as exc:
            # Auth will not fix itself by retrying; record + re-raise.
            registry.record_failure(server_ref, policy, now_ns=clock())
            raise exc
        except (_CallTimeout, Exception) as exc:  # noqa: BLE001 - retryable transport
            last_exc = exc
            if attempt < policy.reconnect_max_attempts:
                backoff_ms = min(
                    policy.reconnect_backoff_base_ms * (2 ** (attempt - 1)),
                    policy.reconnect_backoff_cap_ms,
                )
                sleep(backoff_ms / 1000.0)
                if reconnect is not None:
                    reconnect(server_ref)
                continue
            break
        except BaseException:
            # A non-Exception escape (KeyboardInterrupt / SystemExit /
            # GeneratorExit) while a half-open trial holds the in-flight latch
            # would otherwise leave the breaker stuck half-open, denying every
            # future call. Record a failure (which re-arms the breaker and
            # clears the latch) before propagating.
            registry.record_failure(server_ref, policy, now_ns=clock())
            raise
        else:
            registry.record_success(server_ref)
            return result

    registry.record_failure(server_ref, policy, now_ns=clock())
    raise McpServerUnreachable(
        f"mcp server unreachable after {policy.reconnect_max_attempts} attempts",
        reason_code=REASON_SERVER_UNREACHABLE,
    ) from last_exc


async def async_call_with_resilience(
    policy: McpResiliencePolicy,
    registry: CircuitBreakerRegistry,
    server_ref: str,
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    reconnect: Callable[[str], Any] | None = None,
    auth_error_types: tuple[type[BaseException], ...] = (),
    clock: Callable[[], int] = time.monotonic_ns,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Any:
    """Async twin of :func:`call_with_resilience` (shares the SAME breaker).

    Reuses the exact :class:`CircuitBreakerRegistry` state machine
    (``allow`` / ``record_success`` / ``record_failure``); only the timeout and
    sleep mechanism differ. The per-attempt deadline is a REAL socket timeout via
    :func:`asyncio.wait_for` (not the sync thread-pool bound) and backoff is via
    :func:`asyncio.sleep`. ``coro_factory`` must return a FRESH awaitable on each
    call so a retried attempt re-issues the underlying request.

    When ``policy.enabled`` is False this awaits ``coro_factory()`` exactly once
    and propagates any exception unchanged (byte-identical pass-through). The same
    ``BaseException``-clears-the-latch safety as the sync twin applies: a
    non-``Exception`` escape (``CancelledError`` / ``KeyboardInterrupt`` /
    ``SystemExit``) records a breaker failure (clearing any in-flight half-open
    latch) before propagating, so the breaker never sticks half-open.
    """
    if not policy.enabled:
        return await coro_factory()

    admitted, deny_reason = registry.allow(server_ref, policy, now_ns=clock())
    if not admitted:
        raise McpServerUnreachable(reason_code=deny_reason or REASON_CIRCUIT_OPEN)

    timeout_s = policy.call_timeout_ms / 1000.0
    last_exc: BaseException | None = None
    for attempt in range(1, policy.reconnect_max_attempts + 1):
        try:
            result = await asyncio.wait_for(coro_factory(), timeout_s)
        except auth_error_types as exc:
            # Auth will not fix itself by retrying; record + re-raise.
            registry.record_failure(server_ref, policy, now_ns=clock())
            raise exc
        except Exception as exc:  # noqa: BLE001 - retryable transport (incl TimeoutError)
            last_exc = exc
            if attempt < policy.reconnect_max_attempts:
                backoff_ms = min(
                    policy.reconnect_backoff_base_ms * (2 ** (attempt - 1)),
                    policy.reconnect_backoff_cap_ms,
                )
                await sleep(backoff_ms / 1000.0)
                if reconnect is not None:
                    maybe = reconnect(server_ref)
                    if inspect.isawaitable(maybe):
                        await maybe
                continue
            break
        except BaseException:
            # CancelledError / KeyboardInterrupt / SystemExit while a half-open
            # trial holds the in-flight latch would otherwise leave the breaker
            # stuck half-open. Record a failure (re-arms + clears the latch)
            # before propagating, mirroring the sync twin.
            registry.record_failure(server_ref, policy, now_ns=clock())
            raise
        else:
            registry.record_success(server_ref)
            return result

    registry.record_failure(server_ref, policy, now_ns=clock())
    raise McpServerUnreachable(
        f"mcp server unreachable after {policy.reconnect_max_attempts} attempts",
        reason_code=REASON_SERVER_UNREACHABLE,
    ) from last_exc


_USER_MESSAGES = {
    REASON_NEEDS_REAUTH: "Reconnect {server} to continue; its authorization expired.",
    REASON_SERVER_UNREACHABLE: "Could not reach {server}; I was unable to run the requested tool.",
    REASON_CIRCUIT_OPEN: (
        "{server} is temporarily unavailable after repeated failures; will retry shortly."
    ),
}


def mcp_user_message(decision: Any) -> str | None:
    """Translate a decision's resilience reason code into an actionable string.

    Returns ``None`` for an ``ok`` / non-resilience decision. ``decision`` is
    duck-typed: it needs ``reason_codes`` (an iterable of str) and an optional
    ``diagnostic_metadata`` mapping carrying ``serverRef`` for the message.
    """
    reason_codes = tuple(getattr(decision, "reason_codes", ()) or ())
    metadata = getattr(decision, "diagnostic_metadata", None) or {}
    server = "the MCP server"
    if isinstance(metadata, dict):
        ref = metadata.get("serverRef")
        if isinstance(ref, str) and ref:
            server = ref
    for code in reason_codes:
        template = _USER_MESSAGES.get(code)
        if template is not None:
            return template.format(server=server)
    return None
