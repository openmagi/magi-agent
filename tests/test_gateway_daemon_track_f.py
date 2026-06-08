"""Track F — GatewayDaemon watcher-fleet tests.

Covers:
  - gate OFF (default) → no watchers started (no-op)
  - gate ON → all gate-enabled watchers started, gate-disabled ones skipped
  - graceful degradation: a watcher that raises does NOT crash the daemon;
    the surviving watchers (e.g. cron) keep running
  - bounded-backoff restart of a failing watcher, then marked failed
  - clean shutdown on stop_event
  - health projection states (running/failed/disabled per watcher)

No real network / uvicorn — every watcher is an injected fake coroutine that the
test drives with a stop_event.  Async tests follow the repo convention of
``asyncio.run(_inner())`` inside a sync test (no pytest-asyncio dependency).
"""
from __future__ import annotations

import asyncio

import pytest

from magi_agent.gateway.daemon import (
    GatewayDaemon,
    GatewayWatcher,
    is_gateway_daemon_enabled,
)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def test_gate_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_GATEWAY_DAEMON_ENABLED", raising=False)
    assert is_gateway_daemon_enabled() is False


def test_gate_on_when_truthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    assert is_gateway_daemon_enabled() is True
    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "false")
    assert is_gateway_daemon_enabled() is False


# ---------------------------------------------------------------------------
# Helpers — fake watchers
# ---------------------------------------------------------------------------

def _counting_watcher(
    name: str, counter: dict[str, int], *, gate: bool = True
) -> GatewayWatcher:
    async def run(stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            counter[name] = counter.get(name, 0) + 1
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=0.01)
            except asyncio.TimeoutError:
                continue

    return GatewayWatcher(name=name, run=run, is_enabled=lambda: gate)


# ---------------------------------------------------------------------------
# Gate-off → no watchers
# ---------------------------------------------------------------------------

def test_gate_off_starts_no_watchers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_GATEWAY_DAEMON_ENABLED", raising=False)
    counter: dict[str, int] = {}
    daemon = GatewayDaemon(watchers=[_counting_watcher("cron", counter)])

    async def _inner() -> None:
        stop = asyncio.Event()
        stop.set()  # immediate stop so run returns even if (wrongly) started
        await daemon.run(stop_event=stop)

    asyncio.run(_inner())
    assert counter == {}  # no watcher ran
    assert daemon.started_watcher_names() == ()


# ---------------------------------------------------------------------------
# Gate-on → enabled watchers start, disabled skipped
# ---------------------------------------------------------------------------

def test_gate_on_starts_enabled_skips_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    counter: dict[str, int] = {}
    daemon = GatewayDaemon(
        watchers=[
            _counting_watcher("cron", counter, gate=True),
            _counting_watcher("telegram", counter, gate=False),
        ]
    )

    async def _inner() -> None:
        stop = asyncio.Event()

        async def driver() -> None:
            while counter.get("cron", 0) < 1:
                await asyncio.sleep(0.005)
            stop.set()

        await asyncio.wait_for(
            asyncio.gather(daemon.run(stop_event=stop), driver()), timeout=5.0
        )

    asyncio.run(_inner())
    assert counter.get("cron", 0) >= 1
    assert "telegram" not in counter  # gate off → not started
    assert daemon.started_watcher_names() == ("cron",)
    health = daemon.health_projection()
    assert health["watchers"]["telegram"]["state"] == "disabled"
    assert health["watchers"]["cron"]["state"] in {"running", "stopped"}


# ---------------------------------------------------------------------------
# Graceful degradation — a failing watcher must NOT crash the daemon
# ---------------------------------------------------------------------------

def test_failing_channel_watcher_does_not_crash_cron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    counter: dict[str, int] = {}

    async def always_fails(stop_event: asyncio.Event) -> None:
        raise RuntimeError("channel connect failed")

    failing = GatewayWatcher(name="telegram", run=always_fails, is_enabled=lambda: True)
    cron = _counting_watcher("cron", counter, gate=True)

    daemon = GatewayDaemon(watchers=[failing, cron], max_restarts=2, backoff_base=0.001)

    async def _inner() -> None:
        stop = asyncio.Event()

        async def driver() -> None:
            while counter.get("cron", 0) < 3:
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.05)  # let failing watcher exhaust restarts
            stop.set()

        await asyncio.wait_for(
            asyncio.gather(daemon.run(stop_event=stop), driver()), timeout=5.0
        )

    asyncio.run(_inner())
    assert counter.get("cron", 0) >= 3  # cron survived
    health = daemon.health_projection()
    assert health["watchers"]["telegram"]["state"] == "failed"
    assert health["watchers"]["telegram"]["restarts"] >= 1
    assert health["daemonEnabled"] is True


def test_all_channels_fail_cron_keeps_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hermes #5196: ALL channel adapters down → daemon stays up for cron."""
    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    counter: dict[str, int] = {}

    async def boom(stop_event: asyncio.Event) -> None:
        raise ConnectionError("down")

    watchers = [
        GatewayWatcher(name="telegram", run=boom, is_enabled=lambda: True),
        GatewayWatcher(name="discord", run=boom, is_enabled=lambda: True),
        _counting_watcher("cron", counter, gate=True),
    ]
    daemon = GatewayDaemon(watchers=watchers, max_restarts=1, backoff_base=0.001)

    async def _inner() -> None:
        stop = asyncio.Event()

        async def driver() -> None:
            while counter.get("cron", 0) < 2:
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.05)
            stop.set()

        await asyncio.wait_for(
            asyncio.gather(daemon.run(stop_event=stop), driver()), timeout=5.0
        )

    asyncio.run(_inner())
    assert counter.get("cron", 0) >= 2
    health = daemon.health_projection()
    assert health["watchers"]["telegram"]["state"] == "failed"
    assert health["watchers"]["discord"]["state"] == "failed"
    assert health["watchers"]["cron"]["state"] in {"running", "stopped"}


# ---------------------------------------------------------------------------
# Bounded-backoff restart then recover
# ---------------------------------------------------------------------------

def test_watcher_restarts_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    state = {"calls": 0, "ticks": 0}

    async def flaky(stop_event: asyncio.Event) -> None:
        state["calls"] += 1
        if state["calls"] <= 2:
            raise RuntimeError("transient")
        while not stop_event.is_set():
            state["ticks"] += 1
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=0.01)
            except asyncio.TimeoutError:
                continue

    watcher = GatewayWatcher(name="flaky", run=flaky, is_enabled=lambda: True)
    daemon = GatewayDaemon(watchers=[watcher], max_restarts=5, backoff_base=0.001)

    async def _inner() -> None:
        stop = asyncio.Event()

        async def driver() -> None:
            while state["ticks"] < 1:
                await asyncio.sleep(0.005)
            stop.set()

        await asyncio.wait_for(
            asyncio.gather(daemon.run(stop_event=stop), driver()), timeout=5.0
        )

    asyncio.run(_inner())
    assert state["calls"] >= 3  # restarted past the 2 transient failures
    assert state["ticks"] >= 1
    health = daemon.health_projection()
    assert health["watchers"]["flaky"]["restarts"] >= 2


# ---------------------------------------------------------------------------
# Clean shutdown
# ---------------------------------------------------------------------------

def test_clean_shutdown_cancels_watchers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    cancelled = {"flag": False}

    async def long_runner(stop_event: asyncio.Event) -> None:
        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            cancelled["flag"] = True
            raise

    watcher = GatewayWatcher(name="w", run=long_runner, is_enabled=lambda: True)
    daemon = GatewayDaemon(watchers=[watcher])

    async def _inner() -> None:
        stop = asyncio.Event()

        async def driver() -> None:
            await asyncio.sleep(0.02)
            stop.set()

        await asyncio.wait_for(
            asyncio.gather(daemon.run(stop_event=stop), driver()), timeout=2.0
        )

    asyncio.run(_inner())
    health = daemon.health_projection()
    assert health["watchers"]["w"]["state"] == "stopped"
    assert cancelled["flag"] is True
