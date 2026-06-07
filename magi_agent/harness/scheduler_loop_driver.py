"""A-driver — SchedulerLoopDriver: the periodic loop that fires due jobs.

This is the (a) deliverable from the Track-F deferral list in
``scheduler_job_execution.py``: there was NO production caller of
``execute_due_jobs``.  This driver is that caller.

What it does
------------
Given an injected persistent ``ScheduledJobSource`` (deliverable b —
``SqliteScheduledJobSource``) and an injected ``CronTurnRunner`` (deliverable c —
``CronTurnRunnerAdapter``), it calls ``execute_due_jobs`` for one tick
(``run_once``) or on a timer until stopped (``run_forever``).

Lease / lock reuse (no reinvention)
-----------------------------------
The at-most-once file lock + lease validation live in
``scheduler_executor.tick`` (consumed by ``execute_due_jobs``).  The driver does
NOT reimplement them: it builds a ``SchedulerLease`` via the injected
``lease_factory`` and hands ``lock_dir`` straight through to
``execute_due_jobs`` → ``tick`` → ``acquire_tick_lock``.  The file lock is the
cross-process at-most-once guard; the lease is the owner/expiry guard.

Gating (default-OFF, no new always-on behavior)
-----------------------------------------------
The driver adds NO authority of its own.  Whether a real agent turn runs is still
governed entirely by the existing gates inside ``execute_due_jobs``
(``MAGI_SCHEDULER_EXECUTOR_ENABLED`` default OFF, ``MAGI_SCHEDULER_SHADOW``
default ON, the A5 kill-switch, and the optional ``readiness_execution_mode``).
With the gate OFF, ``run_once`` produces exactly the A2 local_fake tick (the
runner is never touched).  ``run_forever`` is a thin timer around ``run_once`` —
it does not flip any gate, so a running loop with the gate OFF only records
local_fake ticks.

Async loop
----------
``run_forever`` is cleanly stoppable: it checks ``stop_event`` before and after
each tick and uses ``asyncio.wait_for`` on the event for the inter-tick sleep so a
stop interrupts the wait immediately.  It returns the number of ticks executed.

Forbidden imports: google.adk, urllib, socket, subprocess, http, requests at
module top level (the runner/source are injected) — verified by test.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from magi_agent.harness.scheduler_executor import ScheduledJobSource
from magi_agent.harness.scheduler_job_execution import (
    CronTurnRunner,
    JobExecutionConfig,
    JobExecutionResult,
    execute_due_jobs,
)
from magi_agent.harness.scheduler_runtime import SchedulerLease


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class SchedulerLoopDriver:
    """Periodic driver around ``execute_due_jobs``.

    Parameters
    ----------
    source:
        The (persistent) ``ScheduledJobSource``.
    runner:
        The injected ``CronTurnRunner`` (real path: ``CronTurnRunnerAdapter``).
    owner_digest:
        Scheduler lease owner digest (must match the lease the factory builds).
    lock_dir:
        File-lock directory handed through to ``tick`` (at-most-once guard).
    lease_factory:
        Callable ``(now) -> SchedulerLease`` building a valid lease for *now*.
        Injected so the driver does not own lease persistence/renewal (that is the
        Track-F daemon's job); a default self-owned lease is used when omitted.
    config:
        Optional explicit ``JobExecutionConfig``.  When ``None`` (default),
        ``execute_due_jobs`` reads the env gates per tick (default-OFF).
    readiness_execution_mode:
        Optional A5 readiness projection forwarded to ``execute_due_jobs``.
    """

    def __init__(
        self,
        *,
        source: ScheduledJobSource,
        runner: CronTurnRunner,
        owner_digest: str,
        lock_dir: Path | None = None,
        lease_factory: Callable[[datetime], SchedulerLease] | None = None,
        config: JobExecutionConfig | None = None,
        readiness_execution_mode: Literal["disabled", "shadow", "live"] | None = None,
    ) -> None:
        self._source = source
        self._runner = runner
        self._owner_digest = owner_digest
        self._lock_dir = lock_dir
        self._lease_factory = lease_factory or self._default_lease_factory
        self._config = config
        self._readiness_execution_mode = readiness_execution_mode

    def _default_lease_factory(self, now: datetime) -> SchedulerLease:
        now_ms = int(now.astimezone(UTC).timestamp() * 1000)
        return SchedulerLease(
            leaseId=f"lease:loop:{self._owner_digest}",
            ownerDigest=self._owner_digest,
            acquiredAt=now_ms,
            expiresAt=now_ms + 60_000,
        )

    def run_once(self, *, now: datetime | None = None) -> JobExecutionResult:
        """Execute exactly one scheduler tick.

        Builds a fresh lease for *now* and delegates to ``execute_due_jobs``,
        which acquires the file lock, validates the lease, fires due jobs, and
        (only when gated on) invokes the injected runner.  Returns the full
        ``JobExecutionResult`` (tick result + per-job executions).
        """
        tick_now = now if now is not None else _utc_now()
        lease = self._lease_factory(tick_now)
        return execute_due_jobs(
            now=tick_now,
            source=self._source,
            lease=lease,
            owner_digest=self._owner_digest,
            runner=self._runner,
            config=self._config,
            lock_dir=self._lock_dir,
            readiness_execution_mode=self._readiness_execution_mode,
        )

    async def run_forever(
        self,
        *,
        interval_seconds: float,
        stop_event: asyncio.Event,
    ) -> int:
        """Run ``run_once`` on a timer until *stop_event* is set.

        Returns the number of ticks executed.  Cleanly stoppable: the loop checks
        ``stop_event`` before each tick and uses ``asyncio.wait_for`` on the event
        for the inter-tick sleep so a set event interrupts the wait immediately.
        ``run_once`` is synchronous (it drives the runner off the event loop via
        ``execute_due_jobs``), so it is offloaded to a thread to keep the event
        loop responsive to ``stop_event``.
        """
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be > 0")

        ticks = 0
        while not stop_event.is_set():
            await asyncio.to_thread(self.run_once)
            ticks += 1
            if stop_event.is_set():
                break
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                # Interval elapsed without a stop — loop again.
                continue
        return ticks


__all__ = ["SchedulerLoopDriver"]
