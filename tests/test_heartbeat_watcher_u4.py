"""U4 -- HeartbeatWatcher: periodic quiet self-check agent turn.

TDD: gate (MAGI_HEARTBEAT_ENABLED), interval config, suppression token,
output delivery only when non-suppressed, watcher name, registry in
build_default_watchers.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Gate: MAGI_HEARTBEAT_ENABLED (default OFF)
# ---------------------------------------------------------------------------

def test_heartbeat_watcher_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_HEARTBEAT_ENABLED", raising=False)

    from magi_agent.gateway.heartbeat_watcher import build_heartbeat_watcher

    watcher = build_heartbeat_watcher()
    assert watcher.is_enabled() is False


def test_heartbeat_watcher_gate_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_HEARTBEAT_ENABLED", "1")

    from magi_agent.gateway.heartbeat_watcher import build_heartbeat_watcher

    watcher = build_heartbeat_watcher()
    assert watcher.is_enabled() is True


def test_heartbeat_watcher_name() -> None:
    from magi_agent.gateway.heartbeat_watcher import build_heartbeat_watcher

    watcher = build_heartbeat_watcher()
    assert watcher.name == "heartbeat"


# ---------------------------------------------------------------------------
# Interval: MAGI_HEARTBEAT_INTERVAL_SECONDS (default 1800, floor 60)
# ---------------------------------------------------------------------------

def test_heartbeat_interval_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_HEARTBEAT_INTERVAL_SECONDS", raising=False)

    from magi_agent.gateway.heartbeat_watcher import heartbeat_interval_seconds

    assert heartbeat_interval_seconds() == 1800


def test_heartbeat_interval_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_HEARTBEAT_INTERVAL_SECONDS", "300")

    from magi_agent.gateway.heartbeat_watcher import heartbeat_interval_seconds

    assert heartbeat_interval_seconds() == 300


def test_heartbeat_interval_below_floor_uses_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_HEARTBEAT_INTERVAL_SECONDS", "10")

    from magi_agent.gateway.heartbeat_watcher import heartbeat_interval_seconds

    assert heartbeat_interval_seconds() == 60


def test_heartbeat_interval_invalid_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_HEARTBEAT_INTERVAL_SECONDS", "not_a_number")

    from magi_agent.gateway.heartbeat_watcher import heartbeat_interval_seconds

    assert heartbeat_interval_seconds() == 1800


# ---------------------------------------------------------------------------
# Suppression token: MAGI_HEARTBEAT_SUPPRESS_TOKEN (default "HEARTBEAT_OK")
# ---------------------------------------------------------------------------

def test_heartbeat_suppress_token_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_HEARTBEAT_SUPPRESS_TOKEN", raising=False)

    from magi_agent.gateway.heartbeat_watcher import heartbeat_suppress_token

    assert heartbeat_suppress_token() == "HEARTBEAT_OK"


def test_heartbeat_suppress_token_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_HEARTBEAT_SUPPRESS_TOKEN", "ALL_GOOD")

    from magi_agent.gateway.heartbeat_watcher import heartbeat_suppress_token

    assert heartbeat_suppress_token() == "ALL_GOOD"


# ---------------------------------------------------------------------------
# Output delivery: suppress when token present, deliver when absent
# ---------------------------------------------------------------------------

def test_heartbeat_run_suppresses_when_token_in_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the turn output contains the suppress token, no delivery happens.
    Verified by injecting a fake engine that returns a suppressed output and
    a recording delivery sink.
    """
    monkeypatch.setenv("MAGI_HEARTBEAT_ENABLED", "1")
    monkeypatch.setenv("MAGI_HEARTBEAT_SUPPRESS_TOKEN", "HEARTBEAT_OK")

    deliveries: list[str] = []

    from magi_agent.gateway.heartbeat_watcher import _run_heartbeat_tick

    async def fake_engine(prompt: str) -> str:
        return "System nominal. HEARTBEAT_OK. All checks passed."

    asyncio.run(_run_heartbeat_tick(
        engine=fake_engine,
        suppress_token="HEARTBEAT_OK",
        deliver=lambda output: deliveries.append(output),
    ))

    assert deliveries == []  # suppressed


def test_heartbeat_run_delivers_when_token_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the turn output does NOT contain the suppress token, delivery happens."""
    monkeypatch.setenv("MAGI_HEARTBEAT_ENABLED", "1")

    deliveries: list[str] = []

    from magi_agent.gateway.heartbeat_watcher import _run_heartbeat_tick

    async def fake_engine(prompt: str) -> str:
        return "Warning: memory usage elevated. Consider clearing context."

    asyncio.run(_run_heartbeat_tick(
        engine=fake_engine,
        suppress_token="HEARTBEAT_OK",
        deliver=lambda output: deliveries.append(output),
    ))

    assert len(deliveries) == 1
    assert "elevated" in deliveries[0]


def test_heartbeat_run_engine_error_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising engine must not propagate -- heartbeat tick is best-effort."""
    deliveries: list[str] = []

    from magi_agent.gateway.heartbeat_watcher import _run_heartbeat_tick

    async def boom_engine(prompt: str) -> str:
        raise RuntimeError("engine unavailable")

    # Must not raise.
    asyncio.run(_run_heartbeat_tick(
        engine=boom_engine,
        suppress_token="HEARTBEAT_OK",
        deliver=lambda output: deliveries.append(output),
    ))

    assert deliveries == []  # error => nothing delivered


def test_heartbeat_run_engine_timeout_does_not_crash_and_does_not_deliver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-1: a hung engine (never returns) must time out and not deliver.

    _run_heartbeat_tick wraps engine() in asyncio.wait_for with a configurable
    bound.  On TimeoutError the tick returns cleanly with no delivery.
    """
    deliveries: list[str] = []

    from magi_agent.gateway.heartbeat_watcher import _run_heartbeat_tick

    async def hung_engine(prompt: str) -> str:
        # Never returns -- simulates a wedged governed turn.
        await asyncio.Event().wait()
        return ""  # unreachable

    # Inject a tiny timeout so the test completes fast.
    asyncio.run(_run_heartbeat_tick(
        engine=hung_engine,
        suppress_token="HEARTBEAT_OK",
        deliver=lambda output: deliveries.append(output),
        timeout_seconds=0.05,
    ))

    assert deliveries == []  # timeout => nothing delivered


# ---------------------------------------------------------------------------
# Watcher loop: ticks on the configured interval, stops on stop_event
# ---------------------------------------------------------------------------

def test_heartbeat_watcher_loop_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_HEARTBEAT_ENABLED", "1")

    ticks: list[str] = []

    from magi_agent.gateway.heartbeat_watcher import build_heartbeat_watcher
    from magi_agent.gateway.daemon import GatewayWatcher

    async def fake_engine(prompt: str) -> str:
        ticks.append(prompt)
        return "WARNING: something went wrong"  # no suppress token -> delivers

    watcher = build_heartbeat_watcher(
        interval_seconds=0.01,
        engine=fake_engine,
        deliver=lambda out: None,
    )
    assert isinstance(watcher, GatewayWatcher)

    async def _inner() -> None:
        stop = asyncio.Event()

        async def stopper() -> None:
            while len(ticks) < 1:
                await asyncio.sleep(0.005)
            stop.set()

        await asyncio.wait_for(
            asyncio.gather(watcher.run(stop), stopper()), timeout=5.0
        )

    asyncio.run(_inner())
    assert len(ticks) >= 1


# ---------------------------------------------------------------------------
# build_default_watchers includes heartbeat watcher
# ---------------------------------------------------------------------------

def test_build_default_watchers_includes_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """build_default_watchers() must include a watcher named 'heartbeat'."""
    monkeypatch.setenv("MAGI_SCHEDULER_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.delenv("MAGI_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MAGI_DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MAGI_SLACK_BOT_TOKEN", raising=False)

    from magi_agent.gateway.watchers import build_default_watchers

    watchers = build_default_watchers()
    names = [w.name for w in watchers]
    assert "heartbeat" in names
