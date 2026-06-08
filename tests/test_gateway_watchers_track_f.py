"""Track F — watcher-fleet builders compose the existing loop driver + channel
adapters (no reinvention) and health projection + CLI parsing.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from magi_agent.gateway.daemon import GatewayWatcher
from magi_agent.gateway.watchers import (
    build_channel_poll_watcher,
    build_scheduler_cron_watcher,
)


# ---------------------------------------------------------------------------
# Cron watcher composes SchedulerLoopDriver.run_forever
# ---------------------------------------------------------------------------

def test_scheduler_cron_watcher_drives_loop_driver() -> None:
    ticks = {"n": 0}

    class FakeDriver:
        async def run_forever(self, *, interval_seconds: float, stop_event: asyncio.Event) -> int:
            count = 0
            while not stop_event.is_set():
                ticks["n"] += 1
                count += 1
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.01)
                except asyncio.TimeoutError:
                    continue
            return count

    watcher = build_scheduler_cron_watcher(
        driver=FakeDriver(), interval_seconds=0.01
    )
    assert isinstance(watcher, GatewayWatcher)
    assert watcher.name == "scheduler_cron"

    async def _inner() -> None:
        stop = asyncio.Event()

        async def driver() -> None:
            while ticks["n"] < 1:
                await asyncio.sleep(0.005)
            stop.set()

        await asyncio.wait_for(
            asyncio.gather(watcher.run(stop), driver()), timeout=5.0
        )

    asyncio.run(_inner())
    assert ticks["n"] >= 1


def test_scheduler_cron_watcher_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    watcher = build_scheduler_cron_watcher(driver=object(), interval_seconds=1.0)
    monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
    assert watcher.is_enabled() is False
    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
    assert watcher.is_enabled() is True


# ---------------------------------------------------------------------------
# Channel poll watcher composes a platform poll function
# ---------------------------------------------------------------------------

def test_channel_poll_watcher_loops_poll_fn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CHANNEL_LIVE_TELEGRAM", "1")
    polls = {"n": 0}

    def poll_once() -> int:
        polls["n"] += 1
        return 0

    watcher = build_channel_poll_watcher(
        channel_type="telegram",
        poll_once=poll_once,
        is_enabled=lambda: True,
        interval_seconds=0.01,
    )
    assert watcher.name == "channel_telegram"

    async def _inner() -> None:
        stop = asyncio.Event()

        async def driver() -> None:
            while polls["n"] < 2:
                await asyncio.sleep(0.005)
            stop.set()

        await asyncio.wait_for(
            asyncio.gather(watcher.run(stop), driver()), timeout=5.0
        )

    asyncio.run(_inner())
    assert polls["n"] >= 2


def test_channel_poll_watcher_respects_gate() -> None:
    watcher = build_channel_poll_watcher(
        channel_type="discord",
        poll_once=lambda: 0,
        is_enabled=lambda: False,
        interval_seconds=0.01,
    )
    assert watcher.is_enabled() is False


def test_channel_poll_watcher_poll_error_does_not_stop_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single poll raising must not kill the watcher loop (degradation)."""
    calls = {"n": 0}

    def flaky_poll() -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient poll error")
        return 0

    watcher = build_channel_poll_watcher(
        channel_type="slack",
        poll_once=flaky_poll,
        is_enabled=lambda: True,
        interval_seconds=0.005,
    )

    async def _inner() -> None:
        stop = asyncio.Event()

        async def driver() -> None:
            while calls["n"] < 3:
                await asyncio.sleep(0.005)
            stop.set()

        await asyncio.wait_for(
            asyncio.gather(watcher.run(stop), driver()), timeout=5.0
        )

    asyncio.run(_inner())
    assert calls["n"] >= 3  # kept polling past the transient error
