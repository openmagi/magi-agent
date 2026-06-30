"""WS8 PR8a-1: pure poll-resilience policy unit tests.

Deterministic: a fixed-seed ``random.Random(0)`` is injected so jitter cannot
flake, and ``now_ms`` is a plain int the test controls. Assertions on the
backoff are on the full-jitter BAND (inclusive bounds), never an exact value.
"""
from __future__ import annotations

import random

import pytest

from magi_agent.gateway.poll_resilience import (
    PollCircuitState,
    PollResilienceConfig,
    next_backoff_ms,
    on_failure,
    on_success,
    resolve_poll_resilience_config,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any leaked MAGI_TELEGRAM_POLL_* exports so the suite is hermetic."""
    for key in list(__import__("os").environ):
        if key.startswith("MAGI_TELEGRAM_POLL_"):
            monkeypatch.delenv(key, raising=False)


class _UpperBoundRng:
    """random.Random stand-in whose randint always returns its upper bound."""

    def randint(self, a: int, b: int) -> int:
        return b


def _cfg(**overrides: object) -> PollResilienceConfig:
    base = {
        "enabled": True,
        "backoff_base_ms": 1000,
        "backoff_cap_ms": 300000,
        "circuit_threshold": 5,
        "circuit_cooldown_ms": 60000,
        "persistent_failure_threshold": 10,
    }
    base.update(overrides)
    return PollResilienceConfig(**base)  # type: ignore[arg-type]


def _assert_band(value: int, low: int, high: int) -> None:
    assert low <= value <= high, f"{value} not in [{low}, {high}]"


# ---------------------------------------------------------------------------
# next_backoff_ms: full-jitter band + monotonic ceiling
# ---------------------------------------------------------------------------

def test_next_backoff_full_jitter_band() -> None:
    rng = random.Random(0)
    _assert_band(next_backoff_ms(1, base_ms=1000, cap_ms=300000, rng=rng), 0, 1000)
    _assert_band(next_backoff_ms(5, base_ms=1000, cap_ms=300000, rng=rng), 0, 16000)
    _assert_band(next_backoff_ms(20, base_ms=1000, cap_ms=300000, rng=rng), 0, 300000)


def test_next_backoff_zero_failures_is_zero() -> None:
    assert next_backoff_ms(0, base_ms=1000, cap_ms=300000, rng=random.Random(0)) == 0


def test_next_backoff_monotonic_ceiling() -> None:
    rng = _UpperBoundRng()
    for n in range(1, 7):
        expected = min(300000, 1000 * 2 ** (n - 1))
        assert next_backoff_ms(n, base_ms=1000, cap_ms=300000, rng=rng) == expected
    # Far past the ceiling crossing, the ceiling is pinned at the cap.
    assert next_backoff_ms(30, base_ms=1000, cap_ms=300000, rng=rng) == 300000


# ---------------------------------------------------------------------------
# Circuit breaker: open / half-open ownership / reopen growth
# ---------------------------------------------------------------------------

def test_breaker_opens_at_threshold() -> None:
    cfg = _cfg()
    state = PollCircuitState()
    rng = random.Random(0)
    t0 = 10_000
    directive = None
    for _ in range(cfg.circuit_threshold):
        directive = on_failure(state, t0, cfg, rng=rng)
    assert directive is not None
    assert directive.circuit_open is True
    assert directive.sleep_ms == cfg.circuit_cooldown_ms
    assert state.state == "open"
    assert state.opened_at_ms == t0
    assert state.consecutive_failures == cfg.circuit_threshold


def test_open_to_half_open_is_policy_owned_and_reopens_on_trial_failure() -> None:
    cfg = _cfg()
    state = PollCircuitState()
    rng = random.Random(0)
    t0 = 0
    for _ in range(cfg.circuit_threshold):
        on_failure(state, t0, cfg, rng=rng)
    assert state.state == "open"
    # Cooldown exactly elapsed: the policy must flip open->half_open itself, and
    # because this trial fails it re-opens with a grown cooldown.
    directive = on_failure(state, t0 + cfg.circuit_cooldown_ms, cfg, rng=rng)
    assert state.state == "open"
    assert state.reopen_count == 1
    assert directive.sleep_ms == min(cfg.backoff_cap_ms, cfg.circuit_cooldown_ms * 2 ** 1)


def test_open_plus_failure_before_cooldown_stays_open() -> None:
    cfg = _cfg()
    state = PollCircuitState()
    rng = random.Random(0)
    t0 = 0
    for _ in range(cfg.circuit_threshold):
        on_failure(state, t0, cfg, rng=rng)
    half = cfg.circuit_cooldown_ms // 2
    failures_before = state.consecutive_failures
    directive = on_failure(state, t0 + half, cfg, rng=rng)
    assert state.state == "open"
    assert state.reopen_count == 0
    assert state.consecutive_failures == failures_before + 1
    assert directive.sleep_ms == cfg.circuit_cooldown_ms - half


def test_half_open_success_closes_breaker() -> None:
    cfg = _cfg()
    state = PollCircuitState()
    rng = random.Random(0)
    t0 = 0
    for _ in range(cfg.circuit_threshold):
        on_failure(state, t0, cfg, rng=rng)
    directive = on_success(
        state, t0 + cfg.circuit_cooldown_ms, cfg, normal_interval_ms=1000
    )
    assert state.state == "closed"
    assert state.consecutive_failures == 0
    assert state.opened_at_ms is None
    assert state.reopen_count == 0
    assert directive.circuit_open is False
    assert directive.sleep_ms == 1000


def test_half_open_reopen_grows_cooldown_geometrically_capped() -> None:
    cfg = _cfg()
    state = PollCircuitState()
    rng = random.Random(0)
    t0 = 0
    for _ in range(cfg.circuit_threshold):
        on_failure(state, t0, cfg, rng=rng)

    now = t0 + cfg.circuit_cooldown_ms
    expected_reopen = 0
    last_sleep = None
    for _ in range(8):
        directive = on_failure(state, now, cfg, rng=rng)
        expected_reopen += 1
        expected = min(cfg.backoff_cap_ms, cfg.circuit_cooldown_ms * 2 ** expected_reopen)
        assert state.reopen_count == expected_reopen
        assert directive.sleep_ms == expected
        last_sleep = directive.sleep_ms
        # advance past the new (longer) cooldown for the next trial
        now = state.opened_at_ms + expected
    # Eventually pinned at the cap and stays there.
    assert last_sleep == cfg.backoff_cap_ms


# ---------------------------------------------------------------------------
# Persistent-failure notice: one-shot, re-armed on success
# ---------------------------------------------------------------------------

def test_persistent_notice_is_one_shot_and_rearms_on_success() -> None:
    cfg = _cfg(persistent_failure_threshold=3, circuit_threshold=2)
    state = PollCircuitState()
    rng = random.Random(0)
    notifies = []
    now = 0
    for _ in range(6):
        directive = on_failure(state, now, cfg, rng=rng)
        notifies.append(directive.should_notify)
        # advance generously past whatever cooldown was set so the breaker cycles
        now = (state.opened_at_ms or 0) + cfg.backoff_cap_ms + 1
    # exactly one notify, on the crossing of the threshold
    assert notifies.count(True) == 1
    assert notifies.index(True) == 2  # 3rd failure crosses threshold==3

    # recovery re-arms
    on_success(state, now, cfg)
    assert state.notified is False
    refired = []
    for _ in range(4):
        directive = on_failure(state, now, cfg, rng=rng)
        refired.append(directive.should_notify)
        now = (state.opened_at_ms or 0) + cfg.backoff_cap_ms + 1
    assert refired.count(True) == 1


# ---------------------------------------------------------------------------
# resolve_poll_resilience_config: env parse
# ---------------------------------------------------------------------------

def test_resolve_config_empty_env_is_disabled_defaults() -> None:
    cfg = resolve_poll_resilience_config({})
    assert cfg.enabled is False
    assert cfg.backoff_base_ms == 1000
    assert cfg.backoff_cap_ms == 300000
    assert cfg.circuit_threshold == 5
    assert cfg.circuit_cooldown_ms == 60000
    assert cfg.persistent_failure_threshold == 10


def test_resolve_config_flag_on() -> None:
    cfg = resolve_poll_resilience_config(
        {"MAGI_TELEGRAM_POLL_RESILIENCE_ENABLED": "1"}
    )
    assert cfg.enabled is True


def test_resolve_config_int_overrides() -> None:
    cfg = resolve_poll_resilience_config(
        {
            "MAGI_TELEGRAM_POLL_RESILIENCE_ENABLED": "1",
            "MAGI_TELEGRAM_POLL_BACKOFF_BASE_MS": "250",
            "MAGI_TELEGRAM_POLL_BACKOFF_CAP_MS": "120000",
            "MAGI_TELEGRAM_POLL_CIRCUIT_THRESHOLD": "3",
            "MAGI_TELEGRAM_POLL_CIRCUIT_COOLDOWN_MS": "30000",
            "MAGI_TELEGRAM_POLL_PERSISTENT_THRESHOLD": "7",
        }
    )
    assert cfg.backoff_base_ms == 250
    assert cfg.backoff_cap_ms == 120000
    assert cfg.circuit_threshold == 3
    assert cfg.circuit_cooldown_ms == 30000
    assert cfg.persistent_failure_threshold == 7


def test_resolve_config_malformed_int_falls_back_no_crash() -> None:
    cfg = resolve_poll_resilience_config(
        {
            "MAGI_TELEGRAM_POLL_RESILIENCE_ENABLED": "1",
            "MAGI_TELEGRAM_POLL_CIRCUIT_THRESHOLD": "abc",
        }
    )
    assert cfg.circuit_threshold == 5  # default


@pytest.mark.parametrize("value", ["off", "disabled", "0", ""])
def test_resolve_config_strict_allowlist_keeps_disabled(value: str) -> None:
    cfg = resolve_poll_resilience_config(
        {"MAGI_TELEGRAM_POLL_RESILIENCE_ENABLED": value}
    )
    assert cfg.enabled is False
