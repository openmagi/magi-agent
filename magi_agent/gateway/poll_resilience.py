"""WS8 PR8a-1: pure Telegram poll-resilience policy (default-OFF).

A side-effect-free policy module: full-jitter exponential backoff plus a
per-watcher circuit breaker with a policy-owned ``open -> half_open`` transition.
The module reads no clock and opens no socket: ``now_ms`` is a plain ``int``
passed in by the caller and the jitter source is an injected ``random.Random``,
so every function is fully deterministic under test.

Design: ``docs/plans/2026-06-25-magi-reliability-WS8-telegram-robustness-design.md``
(this is the OSS half; the flag stays OFF until the separate activation PR).

Imports: standard library only (``dataclasses``, ``random``). The only env read
lives inside :func:`resolve_poll_resilience_config`, which delegates to the
canonical strict-allowlist ``env_bool`` leaf. No ``httpx``/``requests``/``socket``.
"""
from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "PollResilienceConfig",
    "PollCircuitState",
    "PollDirective",
    "next_backoff_ms",
    "on_failure",
    "on_success",
    "resolve_poll_resilience_config",
]


# Module-default jitter source. Tests inject a fixed-seed ``random.Random`` so
# they can assert on the full-jitter band; production uses system entropy.
_DEFAULT_RNG = random.Random()


@dataclass(frozen=True)
class PollResilienceConfig:
    """Frozen, env-resolved tuning for the poll-resilience policy (default-OFF).

    Thresholds mirror Hermes parity: backoff cap 300s (its reconnect watcher's
    30s->300s exponential cap) and a 5-fail / 60s breaker (its MCP breaker shape
    applied to the poll path). See the design's decision note (§8).
    """

    enabled: bool = False
    backoff_base_ms: int = 1000
    backoff_cap_ms: int = 300000
    circuit_threshold: int = 5
    circuit_cooldown_ms: int = 60000
    persistent_failure_threshold: int = 10


@dataclass
class PollCircuitState:
    """Mutable per-watcher breaker state. In-memory only; never persisted.

    A pod restart resets it to ``closed`` with zero failures, which is correct:
    a restart is itself the heaviest recovery action (design §3.6).
    """

    consecutive_failures: int = 0
    state: str = "closed"  # one of: "closed", "open", "half_open"
    opened_at_ms: int | None = None
    reopen_count: int = 0
    notified: bool = False


@dataclass(frozen=True)
class PollDirective:
    """What the watcher loop should do after one poll cycle."""

    sleep_ms: int
    circuit_open: bool
    should_notify: bool


def next_backoff_ms(
    failures: int,
    *,
    base_ms: int,
    cap_ms: int,
    rng: random.Random,
) -> int:
    """Full-jitter exponential backoff (AWS "full jitter").

    The *ceiling* doubles each failure up to ``cap_ms``:
    ``ceil = min(cap_ms, base_ms * 2 ** (failures - 1))`` (for ``failures >= 1``);
    the returned sleep is uniformly distributed in ``[0, ceil]`` to avoid a
    thundering-herd on shared-outage recovery. ``failures <= 0`` returns ``0``.
    """
    if failures <= 0:
        return 0
    ceil = min(cap_ms, base_ms * 2 ** (failures - 1))
    return rng.randint(0, ceil)


def _cooldown_for(state: PollCircuitState, cfg: PollResilienceConfig) -> int:
    """Geometric breaker cooldown: ``min(cap, base_cooldown * 2 ** reopen_count)``.

    Identical to the value used both when the breaker (re)opens and when
    :func:`_advance_open_state` decides the cooldown has elapsed.
    """
    return min(
        cfg.backoff_cap_ms,
        cfg.circuit_cooldown_ms * 2 ** state.reopen_count,
    )


def _advance_open_state(
    state: PollCircuitState,
    now_ms: int,
    cfg: PollResilienceConfig,
) -> None:
    """Policy-owned ``open -> half_open`` flip, driven solely by ``now_ms``.

    Run *first* on every :func:`on_failure` / :func:`on_success` call. If the
    breaker is open and the (geometric) cooldown has elapsed, flip to
    ``half_open`` so this call's poll is treated as the single trial cycle. The
    watcher never assigns ``state.state`` itself; this is what makes the
    half-open branches reachable (design CRITICAL note §3.1).
    """
    if state.state == "open" and state.opened_at_ms is not None:
        if now_ms - state.opened_at_ms >= _cooldown_for(state, cfg):
            state.state = "half_open"


def _maybe_notify(state: PollCircuitState, cfg: PollResilienceConfig) -> bool:
    """One-shot persistent-failure notice: fire once when the failure counter
    crosses ``persistent_failure_threshold`` upward and re-arm only on success."""
    if (
        state.consecutive_failures >= cfg.persistent_failure_threshold
        and not state.notified
    ):
        state.notified = True
        return True
    return False


def on_failure(
    state: PollCircuitState,
    now_ms: int,
    cfg: PollResilienceConfig,
    *,
    rng: random.Random | None = None,
) -> PollDirective:
    """Advance the breaker after a failed poll cycle and return the directive."""
    rng = rng if rng is not None else _DEFAULT_RNG
    _advance_open_state(state, now_ms, cfg)

    if state.state == "half_open":
        # The post-cooldown trial failed: re-open and grow the cooldown.
        state.consecutive_failures += 1
        state.reopen_count += 1
        state.state = "open"
        state.opened_at_ms = now_ms
        sleep_ms = _cooldown_for(state, cfg)
        circuit_open = True
    elif state.state == "open":
        # Failure observed BEFORE the cooldown window closed (early poll). The
        # breaker stays open; wait out the remaining cooldown. Not a trial, so
        # ``reopen_count`` does not advance.
        state.consecutive_failures += 1
        remaining = state.opened_at_ms + _cooldown_for(state, cfg) - now_ms
        sleep_ms = max(0, remaining)
        circuit_open = True
    else:  # closed
        state.consecutive_failures += 1
        if state.consecutive_failures >= cfg.circuit_threshold:
            state.state = "open"
            state.opened_at_ms = now_ms
            state.reopen_count = 0
            sleep_ms = cfg.circuit_cooldown_ms
            circuit_open = True
        else:
            sleep_ms = next_backoff_ms(
                state.consecutive_failures,
                base_ms=cfg.backoff_base_ms,
                cap_ms=cfg.backoff_cap_ms,
                rng=rng,
            )
            circuit_open = False

    should_notify = _maybe_notify(state, cfg)
    return PollDirective(
        sleep_ms=sleep_ms,
        circuit_open=circuit_open,
        should_notify=should_notify,
    )


def on_success(
    state: PollCircuitState,
    now_ms: int,
    cfg: PollResilienceConfig,
    *,
    normal_interval_ms: int = 1000,
) -> PollDirective:
    """Reset the breaker on a clean poll cycle and resume normal cadence.

    Applies :func:`_advance_open_state` first (so a post-cooldown success is the
    half-open trial that closes the breaker), then resets every counter and
    re-arms the persistent-failure notice. ``normal_interval_ms`` is the
    watcher's poll interval in ms, returned unchanged for cadence symmetry.
    """
    _advance_open_state(state, now_ms, cfg)
    state.state = "closed"
    state.consecutive_failures = 0
    state.opened_at_ms = None
    state.reopen_count = 0
    state.notified = False
    return PollDirective(
        sleep_ms=normal_interval_ms,
        circuit_open=False,
        should_notify=False,
    )


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    """Guarded int parse: unset or malformed falls back to ``default`` (no crash)."""
    raw = env.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def resolve_poll_resilience_config(
    env: Mapping[str, str],
) -> PollResilienceConfig:
    """Resolve the config from ``env`` (default-OFF, int-guarded).

    The master boolean uses the canonical strict-allowlist ``env_bool`` leaf, so
    only ``1``/``true``/``yes``/``on`` enable it; ``off``/``disabled``/``0`` and
    any unknown value keep ``enabled=False``.
    """
    from magi_agent.config._truthy import env_bool  # noqa: PLC0415 (local: only env read)

    return PollResilienceConfig(
        enabled=env_bool(
            env, "MAGI_TELEGRAM_POLL_RESILIENCE_ENABLED", default=False
        ),
        backoff_base_ms=_int_env(env, "MAGI_TELEGRAM_POLL_BACKOFF_BASE_MS", 1000),
        backoff_cap_ms=_int_env(env, "MAGI_TELEGRAM_POLL_BACKOFF_CAP_MS", 300000),
        circuit_threshold=_int_env(env, "MAGI_TELEGRAM_POLL_CIRCUIT_THRESHOLD", 5),
        circuit_cooldown_ms=_int_env(
            env, "MAGI_TELEGRAM_POLL_CIRCUIT_COOLDOWN_MS", 60000
        ),
        persistent_failure_threshold=_int_env(
            env, "MAGI_TELEGRAM_POLL_PERSISTENT_THRESHOLD", 10
        ),
    )
