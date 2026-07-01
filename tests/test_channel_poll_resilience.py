"""WS8 PR8a-2: poll-loop wiring tests for ``build_channel_poll_watcher``.

The loop's sleep is captured by monkeypatching ``asyncio.wait_for`` in the
``watchers`` module namespace (the documented seam). ``fake_wait_for`` records
the timeout, closes the inner ``stop_event.wait()`` coroutine so it does not
leak, and on the target cycle sets ``stop_event`` + returns (terminating the
loop) instead of raising ``asyncio.TimeoutError``.
"""
from __future__ import annotations

import asyncio
import random

import pytest

import magi_agent.gateway.watchers as watchers
from magi_agent.gateway.poll_resilience import PollResilienceConfig
from magi_agent.gateway.watchers import build_channel_poll_watcher


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ):
        if key.startswith("MAGI_TELEGRAM_POLL_") or key == "MAGI_CHANNEL_LIVE_TELEGRAM":
            monkeypatch.delenv(key, raising=False)


class _UpperBoundRng:
    def randint(self, a: int, b: int) -> int:
        return b


class _SteppingClock:
    """Monotonic-ms stand-in returning an increasing value, one step per call."""

    def __init__(self, step_ms: int = 1) -> None:
        self._now = 0
        self._step = step_ms

    def __call__(self) -> int:
        now = self._now
        self._now += self._step
        return now


def _make_poll_once(*, fail_count: int, error: Exception | None = None):
    state = {"calls": 0}

    def poll_once() -> int:
        state["calls"] += 1
        if state["calls"] <= fail_count:
            raise error or RuntimeError("transient poll failure")
        return 0

    return poll_once, state


def _drive(watcher, *, terminate_after: int, monkeypatch: pytest.MonkeyPatch):
    recorded: list[float] = []
    stop_event = asyncio.Event()

    async def fake_wait_for(awaitable, *, timeout):
        recorded.append(timeout)
        if asyncio.iscoroutine(awaitable):
            awaitable.close()
        if len(recorded) >= terminate_after:
            stop_event.set()
            return None
        raise asyncio.TimeoutError

    monkeypatch.setattr(watchers.asyncio, "wait_for", fake_wait_for)
    asyncio.run(watcher.run(stop_event))
    return recorded


# ---------------------------------------------------------------------------
# OFF path: byte-identical legacy fixed-interval sleeps
# ---------------------------------------------------------------------------

def test_off_path_uses_fixed_interval_and_never_calls_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"on_failure": 0}

    def _spy(*args, **kwargs):  # pragma: no cover - asserted to never run
        calls["on_failure"] += 1
        raise AssertionError("OFF path must not call the resilience policy")

    monkeypatch.setattr(watchers, "on_failure", _spy)

    poll_once, _ = _make_poll_once(fail_count=3)
    watcher = build_channel_poll_watcher(
        channel_type="telegram",
        poll_once=poll_once,
        is_enabled=lambda: True,
        interval_seconds=1.0,
        poll_resilience_config=None,  # OFF
    )
    recorded = _drive(watcher, terminate_after=4, monkeypatch=monkeypatch)
    assert recorded == [1.0, 1.0, 1.0, 1.0]
    assert calls["on_failure"] == 0


def test_off_path_when_config_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    poll_once, _ = _make_poll_once(fail_count=2)
    watcher = build_channel_poll_watcher(
        channel_type="telegram",
        poll_once=poll_once,
        is_enabled=lambda: True,
        interval_seconds=1.0,
        poll_resilience_config=PollResilienceConfig(enabled=False),
    )
    recorded = _drive(watcher, terminate_after=3, monkeypatch=monkeypatch)
    assert recorded == [1.0, 1.0, 1.0]


# ---------------------------------------------------------------------------
# ON path: backoff bands, ceilings, reset after success
# ---------------------------------------------------------------------------

def test_on_path_backoff_within_band_and_resets_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = PollResilienceConfig(enabled=True)  # base 1000ms, cap 300000ms
    poll_once, _ = _make_poll_once(fail_count=3)
    watcher = build_channel_poll_watcher(
        channel_type="telegram",
        poll_once=poll_once,
        is_enabled=lambda: True,
        interval_seconds=1.0,
        poll_resilience_config=cfg,
        clock=_SteppingClock(),
        rng=random.Random(0),
    )
    recorded = _drive(watcher, terminate_after=4, monkeypatch=monkeypatch)
    # cycle1 fail -> [0, 1.0]; cycle2 -> [0, 2.0]; cycle3 -> [0, 4.0]
    assert 0 <= recorded[0] <= 1.0
    assert 0 <= recorded[1] <= 2.0
    assert 0 <= recorded[2] <= 4.0
    # cycle4 success -> reset to interval_seconds
    assert recorded[3] == 1.0


def test_on_path_ceilings_non_decreasing_with_upper_bound_rng(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = PollResilienceConfig(enabled=True)
    poll_once, _ = _make_poll_once(fail_count=4)
    watcher = build_channel_poll_watcher(
        channel_type="telegram",
        poll_once=poll_once,
        is_enabled=lambda: True,
        interval_seconds=1.0,
        poll_resilience_config=cfg,
        clock=_SteppingClock(),
        rng=_UpperBoundRng(),
    )
    recorded = _drive(watcher, terminate_after=4, monkeypatch=monkeypatch)
    # ceilings are base*2^(n-1)/1000 seconds: 1, 2, 4, 8
    assert recorded == [1.0, 2.0, 4.0, 8.0]
    assert recorded == sorted(recorded)


# ---------------------------------------------------------------------------
# ON path: breaker opens at threshold (cooldown sleep observable)
# ---------------------------------------------------------------------------

def test_on_path_breaker_opens_at_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = PollResilienceConfig(enabled=True)  # threshold 5, cooldown 60000ms
    poll_once, _ = _make_poll_once(fail_count=99)  # always fail
    watcher = build_channel_poll_watcher(
        channel_type="telegram",
        poll_once=poll_once,
        is_enabled=lambda: True,
        interval_seconds=1.0,
        poll_resilience_config=cfg,
        clock=_SteppingClock(),
        rng=_UpperBoundRng(),
    )
    recorded = _drive(watcher, terminate_after=5, monkeypatch=monkeypatch)
    # 5th consecutive failure opens the breaker -> sleep == cooldown (60s)
    assert recorded[4] == 60.0


# ---------------------------------------------------------------------------
# ON path: persistent-failure one-shot notice + token-scrubbed excerpt
# ---------------------------------------------------------------------------

def test_on_path_persistent_failure_notice_once_with_scrubbed_excerpt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = PollResilienceConfig(
        enabled=True,
        circuit_threshold=2,
        circuit_cooldown_ms=1000,
        backoff_cap_ms=4000,
        persistent_failure_threshold=4,
    )
    # Synthetic token-shaped fragment (assembled, never a contiguous literal).
    fake_tok = "12345" + "6:" + "AAAABBBB" + "CCCCDDDD"
    err = RuntimeError("boom " + fake_tok + " secret")
    poll_once, _ = _make_poll_once(fail_count=99, error=err)

    notices: list[dict] = []

    watcher = build_channel_poll_watcher(
        channel_type="telegram",
        poll_once=poll_once,
        is_enabled=lambda: True,
        interval_seconds=1.0,
        poll_resilience_config=cfg,
        on_persistent_failure=lambda n: notices.append(dict(n)),
        clock=_SteppingClock(step_ms=4001),  # always past any cooldown
        rng=_UpperBoundRng(),
    )
    _drive(watcher, terminate_after=6, monkeypatch=monkeypatch)

    assert len(notices) == 1
    notice = notices[0]
    assert notice["telegramPollPersistentFailure"] is True
    assert notice["consecutiveFailures"] >= cfg.persistent_failure_threshold
    assert "[redacted-token]" in notice["lastErrorExcerpt"]
    assert fake_tok[:11] not in notice["lastErrorExcerpt"]
