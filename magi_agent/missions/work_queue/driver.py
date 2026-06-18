"""WorkQueueDriver — the periodic dispatcher tick for the durable work-queue.

What it does
------------
``run_once`` executes one dispatcher tick:

1. Reclaim stale claims (expired + dead-worker tasks → ready).
2. Promote todo tasks whose parents are done → ready.
3. Fetch up to ``max_spawn`` ready tasks.
4. For each task: attempt an atomic CAS claim; if won, run via the injected
   runner; record completion or failure.
5. Return a ``WorkQueueTickResult`` tally.

``run_once`` is synchronous (mirrors ``SchedulerLoopDriver.run_once``).  The
runner is async, so each task is run via ``asyncio.run(runner.run_task(...))``.
When ``run_once`` is offloaded to a thread by a ``run_forever`` loop, blocking
is safe.

Forbidden imports: google.adk, network, subprocess (runner is injected).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from pydantic import BaseModel, ConfigDict

from magi_agent.missions.work_queue.store import WorkQueueStore
from magi_agent.missions.work_queue.runner import WorkTaskRunner

logger = logging.getLogger(__name__)


class WorkQueueTickResult(BaseModel):
    """Immutable tally returned by a single ``run_once`` tick."""

    model_config = ConfigDict(frozen=True)

    reclaimed: int = 0
    promoted: int = 0
    claimed: int = 0
    completed: int = 0
    failed: int = 0


class WorkQueueDriver:
    """Drives one tick of the work-queue dispatch loop.

    Parameters
    ----------
    store:
        The backing work-queue store (``SqliteWorkQueueStore`` or
        ``InMemoryWorkQueueStore``).
    runner:
        Async ``WorkTaskRunner`` — called via ``asyncio.run`` inside the
        synchronous ``run_once``.
    claimer:
        Stable identity string for this dispatcher process
        (e.g. ``"dispatcher-0"``).
    max_spawn:
        Maximum number of tasks to claim and run per tick.
    pid_alive:
        Optional callable ``(pid: int) -> bool`` injected for testing; if
        ``None`` the default OS-kill probe in the store is used.
    """

    def __init__(
        self,
        store: WorkQueueStore,
        runner: WorkTaskRunner,
        *,
        claimer: str,
        max_spawn: int = 4,
        pid_alive: Callable[[int], bool] | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._claimer = claimer
        self._max_spawn = max_spawn
        self._pid_alive = pid_alive

    def run_once(self, *, now: int | None = None) -> WorkQueueTickResult:
        """Execute one dispatcher tick and return the tally.

        Steps
        -----
        1. ``release_stale_claims`` — reclaim expired / dead-worker tasks.
        2. ``recompute_ready``  — promote unblocked todo tasks to ready.
        3. ``ready_tasks``      — fetch up to ``max_spawn`` candidates.
        4. For each candidate: claim (CAS), run, complete or record_failure.
        5. Return ``WorkQueueTickResult``.
        """
        reclaimed: int = self._store.release_stale_claims(
            now=now,
            pid_alive=self._pid_alive,
        )
        promoted: int = self._store.recompute_ready()
        ready = self._store.ready_tasks(limit=self._max_spawn)

        claimed: int = 0
        completed: int = 0
        failed: int = 0

        for task in ready:
            claimed_task = self._store.claim(task.id, claimer=self._claimer, now=now)
            if claimed_task is None:
                # Lost the CAS race — another dispatcher claimed it first.
                logger.debug("CAS loser for task %s; skipping", task.id)
                continue

            claimed += 1
            try:
                result = asyncio.run(self._runner.run_task(claimed_task))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unhandled exception running task %s", task.id)
                self._store.record_failure(
                    task.id,
                    outcome="failed",
                    error=f"unhandled exception: {exc}",
                )
                failed += 1
                continue

            if result.outcome == "completed":
                self._store.complete(task.id, result=result.summary)
                completed += 1
            else:
                self._store.record_failure(
                    task.id,
                    outcome="failed",
                    error=result.error,
                )
                failed += 1

        return WorkQueueTickResult(
            reclaimed=reclaimed,
            promoted=promoted,
            claimed=claimed,
            completed=completed,
            failed=failed,
        )


__all__ = [
    "WorkQueueDriver",
    "WorkQueueTickResult",
]
