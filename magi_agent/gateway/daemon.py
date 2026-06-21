"""GatewayDaemon — the supervised asyncio watcher fleet (Track F).

Design
------
The daemon owns NO new authority and constructs NO network clients.  It is a
thin asyncio supervisor over a list of INJECTED :class:`GatewayWatcher`s.  Each
watcher wraps an existing always-on building block:

  - the scheduler cron ticker (``SchedulerLoopDriver.run_forever``)
  - a per-platform channel poll loop (``channels.*_live`` poll/read functions)
  - a session-expiry watcher / a platform-reconnect watcher

The composition (which loop driver, which channel ports) lives in
``gateway.watchers``; this module is the supervision contract only.

Gating (default-OFF, two layers)
--------------------------------
1. Daemon gate ``MAGI_GATEWAY_DAEMON_ENABLED`` (default OFF).  When OFF,
   :meth:`GatewayDaemon.run` starts NO watcher and returns immediately — a
   complete no-op.
2. Per-watcher gate ``GatewayWatcher.is_enabled()`` (e.g. a channel watcher's
   ``MAGI_CHANNEL_LIVE_*``; the cron watcher's ``MAGI_SCHEDULER_EXECUTOR_ENABLED``).
   A watcher whose gate is OFF is recorded as ``disabled`` and never started.

Graceful degradation (Hermes #5196)
-----------------------------------
A watcher coroutine that raises does NOT crash the daemon.  The supervisor
catches the exception, logs it, and restarts the watcher with bounded
exponential backoff up to ``max_restarts``.  After the budget is exhausted the
watcher is marked ``failed`` and left down — the OTHER watchers (notably the
cron ticker) keep running.  If ALL channel watchers fail, cron still runs.

Clean shutdown
--------------
``run`` watches the injected ``stop_event``: once set, all watcher tasks are
cancelled and awaited, and ``run`` returns.  No watcher can wedge shutdown.

Import-clean: no real network/uvicorn at module top level; the run-loop is
awaitable so tests drive it with a ``stop_event`` and fake watchers.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Daemon gate
# ---------------------------------------------------------------------------

def is_gateway_daemon_enabled() -> bool:
    """Return True iff ``MAGI_GATEWAY_DAEMON_ENABLED`` is truthy (default OFF).

    Evaluated at call time so tests/operators can flip the env without reload.
    I-2 PR A: was a denylist check guarded by ``bool(raw) and ...`` which
    still silently enabled the daemon on any unknown non-empty value (e.g.
    ``"disabled"``). Now uses the canonical strict-allowlist semantics.
    """
    from magi_agent.config._truthy import env_bool  # noqa: PLC0415

    return env_bool(os.environ, "MAGI_GATEWAY_DAEMON_ENABLED", default=False)


# ---------------------------------------------------------------------------
# Watcher descriptor
# ---------------------------------------------------------------------------

WatcherRun = Callable[[asyncio.Event], Coroutine[Any, Any, Any]]


@dataclass(frozen=True)
class GatewayWatcher:
    """One supervised watcher.

    Attributes
    ----------
    name : str
        Stable identifier (e.g. ``"scheduler_cron"``, ``"channel_telegram"``).
    run : Callable[[asyncio.Event], Awaitable]
        Coroutine factory.  Receives the daemon ``stop_event`` and should loop
        until it is set.  May raise — the daemon supervises and restarts.
    is_enabled : Callable[[], bool]
        Per-watcher gate.  Evaluated at start time; ``False`` → not started.
    """

    name: str
    run: WatcherRun
    is_enabled: Callable[[], bool] = field(default=lambda: True)


# ---------------------------------------------------------------------------
# Per-watcher supervision state (mutable; reflected into health projection)
# ---------------------------------------------------------------------------

@dataclass
class _WatcherState:
    state: str = "pending"  # pending | running | failed | stopped | disabled
    restarts: int = 0


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

class GatewayDaemon:
    """Supervised asyncio watcher fleet (default-OFF)."""

    def __init__(
        self,
        *,
        watchers: Sequence[GatewayWatcher],
        max_restarts: int = 5,
        backoff_base: float = 0.5,
        backoff_cap: float = 30.0,
    ) -> None:
        self._watchers = tuple(watchers)
        self._max_restarts = max_restarts
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._states: dict[str, _WatcherState] = {
            w.name: _WatcherState() for w in self._watchers
        }
        self._started: list[str] = []

    # -- introspection ----------------------------------------------------

    def started_watcher_names(self) -> tuple[str, ...]:
        return tuple(self._started)

    def health_projection(self) -> dict[str, object]:
        """Delegate to ops.health for the redacted projection (single source)."""
        from magi_agent.ops.health import gateway_daemon_health_projection

        watcher_states = {
            name: {"state": st.state, "restarts": st.restarts}
            for name, st in self._states.items()
        }
        return gateway_daemon_health_projection(watcher_states=watcher_states)

    # -- run --------------------------------------------------------------

    async def run(self, *, stop_event: asyncio.Event) -> None:
        """Start the gate-enabled watchers and supervise until ``stop_event``.

        Gate OFF → returns immediately without starting any watcher.
        """
        if not is_gateway_daemon_enabled():
            _log.info("gateway daemon gate OFF — no watchers started")
            return

        tasks: list[asyncio.Task[None]] = []
        for watcher in self._watchers:
            try:
                enabled = bool(watcher.is_enabled())
            except Exception:  # noqa: BLE001 — a bad gate must not crash startup
                _log.warning("watcher %s gate check failed; treating as disabled",
                             watcher.name, exc_info=True)
                enabled = False
            if not enabled:
                self._states[watcher.name].state = "disabled"
                continue
            self._started.append(watcher.name)
            self._states[watcher.name].state = "running"
            tasks.append(
                asyncio.create_task(
                    self._supervise(watcher, stop_event), name=f"gateway:{watcher.name}"
                )
            )

        if not tasks:
            # All watchers disabled — nothing to supervise; still respect stop.
            await stop_event.wait()
            return

        # Wait until a stop is requested, then drain the supervised tasks.
        try:
            await stop_event.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _supervise(
        self, watcher: GatewayWatcher, stop_event: asyncio.Event
    ) -> None:
        """Run one watcher, restarting on failure with bounded backoff.

        A watcher that raises is logged + restarted up to ``max_restarts``; once
        the budget is exhausted it is marked ``failed`` and this coroutine
        returns WITHOUT propagating — so the daemon (and the other watchers)
        keep running.  A clean return (stop honoured) marks it ``stopped``.
        """
        state = self._states[watcher.name]
        while not stop_event.is_set():
            try:
                await watcher.run(stop_event)
                # Clean completion (watcher honoured stop_event).
                state.state = "stopped"
                return
            except asyncio.CancelledError:
                state.state = "stopped"
                raise
            except Exception:  # noqa: BLE001 — degradation: never crash the daemon
                _log.warning(
                    "gateway watcher %s raised; restart %d/%d",
                    watcher.name,
                    state.restarts + 1,
                    self._max_restarts,
                    exc_info=True,
                )
                if state.restarts >= self._max_restarts:
                    state.state = "failed"
                    return
                state.restarts += 1
                backoff = min(
                    self._backoff_cap,
                    self._backoff_base * (2 ** (state.restarts - 1)),
                )
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    continue  # backoff elapsed → restart
                else:
                    state.state = "stopped"  # stop requested during backoff
                    return
        state.state = "stopped"


def build_default_gateway_daemon() -> GatewayDaemon:
    """Daemon with the first-party watcher set (each watcher self-gates)."""
    from magi_agent.gateway.watchers import build_default_watchers  # noqa: PLC0415

    return GatewayDaemon(watchers=build_default_watchers())


__all__ = [
    "GatewayDaemon",
    "GatewayWatcher",
    "build_default_gateway_daemon",
    "is_gateway_daemon_enabled",
]
