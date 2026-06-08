"""Watcher-fleet builders — COMPOSE the existing always-on blocks (Track F).

These factories wrap the existing building blocks into :class:`GatewayWatcher`s
for the daemon to supervise.  They reinvent NOTHING: the cron watcher delegates
to ``SchedulerLoopDriver.run_forever`` (the existing ticker) and the channel
watchers drive an injected per-platform poll/read function (the existing
``channels.*_live`` adapters).  Each watcher carries its own gate so the daemon
honours the per-watcher default-OFF discipline.

Import-clean: no real network client is constructed here.  The loop driver,
provider ports, and poll functions are all injected by the operator wiring.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Any, Protocol

from magi_agent.gateway.daemon import GatewayWatcher

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scheduler cron watcher — wraps SchedulerLoopDriver.run_forever
# ---------------------------------------------------------------------------

class _LoopDriverLike(Protocol):
    async def run_forever(
        self, *, interval_seconds: float, stop_event: asyncio.Event
    ) -> int: ...


def _scheduler_executor_enabled() -> bool:
    raw = os.environ.get("MAGI_SCHEDULER_EXECUTOR_ENABLED", "")
    return bool(raw) and raw.strip().lower() not in {"0", "false", "no", "off"}


def build_scheduler_cron_watcher(
    *,
    driver: _LoopDriverLike,
    interval_seconds: float,
) -> GatewayWatcher:
    """Wrap an injected ``SchedulerLoopDriver`` as the cron-ticker watcher.

    The watcher's gate is ``MAGI_SCHEDULER_EXECUTOR_ENABLED`` so the cron loop
    only *executes* real turns when the scheduler executor is on; with the gate
    off the daemon does not start it (the driver itself would only record
    local_fake ticks anyway, so we skip it entirely to keep the fleet quiet).
    """

    async def run(stop_event: asyncio.Event) -> None:
        await driver.run_forever(
            interval_seconds=interval_seconds, stop_event=stop_event
        )

    return GatewayWatcher(
        name="scheduler_cron",
        run=run,
        is_enabled=_scheduler_executor_enabled,
    )


# ---------------------------------------------------------------------------
# Channel poll watcher — wraps an injected per-platform poll/read function
# ---------------------------------------------------------------------------

def build_channel_poll_watcher(
    *,
    channel_type: str,
    poll_once: Callable[[], Any],
    is_enabled: Callable[[], bool],
    interval_seconds: float,
) -> GatewayWatcher:
    """Wrap an injected single-cycle poll function as a continuous watcher.

    ``poll_once`` is the per-platform single poll/read cycle — typically a thin
    closure over ``channels.telegram_live.poll_and_dispatch`` /
    ``channels.discord_live.read_and_dispatch`` bound to the operator-injected
    provider port + poll state.  This builder only adds the loop + interval +
    per-cycle error resilience; it constructs no client.

    A single ``poll_once`` raising is caught + logged and the loop continues
    (transient connect/poll failures must not stop the watcher) — the daemon's
    outer supervisor handles persistent failures via restart/mark-failed.

    Gate: ``is_enabled`` is the injected per-platform gate (e.g.
    ``channels.telegram_live.is_live_telegram_enabled``).
    """

    name = f"channel_{channel_type}"

    async def run(stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(poll_once)
            except Exception:  # noqa: BLE001 — transient poll error must not stop loop
                _log.warning("channel %s poll cycle failed", channel_type, exc_info=True)
            if stop_event.is_set():
                break
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                continue

    return GatewayWatcher(name=name, run=run, is_enabled=is_enabled)


__all__ = ["build_channel_poll_watcher", "build_scheduler_cron_watcher"]
