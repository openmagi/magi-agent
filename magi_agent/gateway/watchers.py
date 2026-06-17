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
from pathlib import Path
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


def is_scheduler_executor_enabled() -> bool:
    raw = os.environ.get("MAGI_SCHEDULER_EXECUTOR_ENABLED", "")
    return bool(raw) and raw.strip().lower() not in {"0", "false", "no", "off"}


def _scheduler_executor_enabled() -> bool:
    return is_scheduler_executor_enabled()


def _scheduler_readiness_mode_from_env() -> str | None:
    raw = os.environ.get("MAGI_SCHEDULER_READINESS_EXECUTION_MODE", "")
    clean = raw.strip().lower()
    if clean in {"disabled", "shadow", "live"}:
        return clean
    return None


def _scheduler_db_path_from_env() -> Path:
    raw = os.environ.get("MAGI_SCHEDULER_DB_PATH", "")
    if raw.strip():
        return Path(raw).expanduser()
    state_dir = Path(os.environ.get("MAGI_STATE_DIR", "~/.magi")).expanduser()
    return state_dir / "scheduler" / "jobs.db"


def _scheduler_lock_dir_from_env() -> Path | None:
    raw = os.environ.get("MAGI_SCHEDULER_LOCK_DIR", "")
    if raw.strip():
        return Path(raw).expanduser()
    return None


def _scheduler_owner_digest_from_env() -> str:
    raw = os.environ.get("MAGI_SCHEDULER_OWNER_DIGEST", "")
    return raw.strip() or "owner:local-gateway"


class _SafeLocalCronTurnRunner:
    async def run_turn(self, plan: Any) -> Any:
        from magi_agent.harness.scheduler_job_execution import CronTurnResult

        return CronTurnResult(
            status="skipped",
            jobId=plan.job_id,
            runnerInvoked=False,
            output="scheduler live runner requires explicit operator wiring",
        )


def build_local_scheduler_cron_driver() -> _LoopDriverLike:
    """Build the local scheduler driver used by ``magi gateway start``.

    This composes the existing persistent job source and scheduler executor seam.
    It deliberately uses a safe local runner: even if an operator requests live
    mode, no ADK/client credentials or network authority are constructed here.
    """
    from typing import cast

    from magi_agent.harness.scheduler_loop_driver import SchedulerLoopDriver
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    return SchedulerLoopDriver(
        source=SqliteScheduledJobSource(_scheduler_db_path_from_env()),
        runner=_SafeLocalCronTurnRunner(),
        owner_digest=_scheduler_owner_digest_from_env(),
        lock_dir=_scheduler_lock_dir_from_env(),
        readiness_execution_mode=cast(Any, _scheduler_readiness_mode_from_env()),
    )


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


# Tick interval for the default cron watcher built by ``build_default_watchers``.
DEFAULT_CRON_TICK_INTERVAL_SECONDS = 60.0


def build_default_watchers() -> tuple[GatewayWatcher, ...]:
    """First-party watcher set for ``magi gateway start`` (each self-gates).

    Always includes the scheduler-cron ticker (gated by
    ``MAGI_SCHEDULER_EXECUTOR_ENABLED``).  Live channel watchers are appended
    only when their per-channel live gate is on AND the channel's credential is
    configured — the channel-watcher builder is fail-closed and returns ``None``
    otherwise, so with the gates OFF the fleet is byte-identical to cron-only.

    The channel-watcher builders are imported lazily so importing this module
    never pulls a network client (the concrete providers live behind that seam).
    """
    watchers: list[GatewayWatcher] = [
        build_scheduler_cron_watcher(
            driver=build_local_scheduler_cron_driver(),
            interval_seconds=DEFAULT_CRON_TICK_INTERVAL_SECONDS,
        )
    ]

    # Live channel watchers (self-host only; fail-closed). Lazy import avoids a
    # module-level cycle (channel_watchers imports build_channel_poll_watcher
    # from here) and keeps this module import-clean.
    from magi_agent.channels.turn_engine import make_engine_run_turn  # noqa: PLC0415
    from magi_agent.gateway.channel_watchers import (  # noqa: PLC0415
        build_discord_channel_watcher,
        build_telegram_channel_watcher,
        build_telegram_supervisor_watcher,
        is_dashboard_telegram_enabled,
    )

    # Engine-backed turn driver shared by every live channel watcher: an inbound
    # message drives one governed turn and the reply is delivered on the same
    # channel. Constructing the closure is cheap (no engine execution) and the
    # watchers self-gate, so with all channel gates OFF the fleet is unchanged.
    run_turn = make_engine_run_turn()

    if is_dashboard_telegram_enabled():
        # Dashboard-managed: long-lived supervisor that hot-reloads the token
        # from the vault. Mutually exclusive with the legacy env-only watcher.
        watchers.append(build_telegram_supervisor_watcher(run_turn=run_turn))
    else:
        telegram_watcher = build_telegram_channel_watcher(run_turn=run_turn)
        if telegram_watcher is not None:
            watchers.append(telegram_watcher)

    discord_watcher = build_discord_channel_watcher(run_turn=run_turn)
    if discord_watcher is not None:
        watchers.append(discord_watcher)

    return tuple(watchers)


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


__all__ = [
    "DEFAULT_CRON_TICK_INTERVAL_SECONDS",
    "build_channel_poll_watcher",
    "build_default_watchers",
    "build_local_scheduler_cron_driver",
    "build_scheduler_cron_watcher",
    "is_scheduler_executor_enabled",
]
