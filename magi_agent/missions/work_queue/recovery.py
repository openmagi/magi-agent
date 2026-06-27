"""WS1 PR1b - thin boot-sweep glue for durable background-task crash-resume.

This module is the PRIMARY WS1 deliverable's boot wiring: on a fresh process
start it reclaims background tasks whose owning worker pid is dead (immediate
boot reclaim, IGNORING the still-valid lease) and runs one dispatcher tick so
the reclaimed work re-runs.

It is glue over the EXISTING work-queue store + driver: the additive
``SqliteWorkQueueStore.reclaim_running_for_dead_pids`` (dead-pid reclaim) plus
the existing ``release_stale_claims`` (genuinely-expired leases) and
``WorkQueueDriver.run_once``. There is NO new queue and NO ``ops/job_queue``
wiring.

Guarantee: AT-LEAST-ONCE. A partially-executed task whose child side-effect
already fired before the crash is re-run whole and the side effect re-fires.
True exactly-once requires WS7-outbox (per-side-effect idempotency) and is a
HARD activation prerequisite before any side-effecting background task is
enabled in a production profile.

Default-OFF: gated by ``MAGI_DURABLE_STARTUP_RECOVERY_ENABLED``. When the flag
is OFF this helper is a no-op and reclaims nothing (OFF path byte-identical).

Forbidden imports: google.adk, network, subprocess (store + driver are injected).
"""
from __future__ import annotations

from typing import Callable

from magi_agent.missions.work_queue.driver import WorkQueueDriver
from magi_agent.missions.work_queue.store import WorkQueueStore


def recover_background_tasks(
    store: WorkQueueStore,
    driver: WorkQueueDriver,
    *,
    enabled: bool,
    pid_alive: Callable[[int], bool] | None = None,
    now: int | None = None,
) -> tuple[str, ...]:
    """Reclaim dead-pid background tasks at boot and run one dispatcher tick.

    Steps (only when *enabled* is True):

    1. ``reclaim_running_for_dead_pids`` - flip ``running`` rows whose worker
       pid is dead back to ``ready`` immediately, ignoring the still-valid lease.
    2. ``release_stale_claims`` - reclaim genuinely-expired leases too.
    3. ``run_once`` - dispatch the reclaimed (and any other ready) tasks.

    Returns the ids of the tasks reclaimed by step 1 so the caller can log /
    surface them. When *enabled* is False the helper short-circuits to a no-op
    and returns an empty tuple - nothing is read or written.
    """
    if not enabled:
        return ()

    # Capture which running rows are reclaimed by the dead-pid sweep so the
    # caller can surface them. ready_tasks(limit=...) after the reclaim would
    # also include genuinely-expired leases and pre-existing ready tasks, so we
    # diff the running set before/after the dead-pid reclaim instead.
    before_running = _running_task_ids(store)
    store.reclaim_running_for_dead_pids(now=now, pid_alive=pid_alive)
    after_running = _running_task_ids(store)
    reclaimed_ids = tuple(sorted(before_running - after_running))

    # Genuinely-expired leases (the existing path) + dispatch tick.
    store.release_stale_claims(now=now, pid_alive=pid_alive)
    driver.run_once(now=now)

    return reclaimed_ids


def _running_task_ids(store: WorkQueueStore) -> set[str]:
    """Best-effort snapshot of currently-``running`` task ids.

    Uses the store's board/list seam when present; falls back to an empty set so
    the boot sweep degrades to "ran a tick, reported no reclaimed ids" rather
    than raising.
    """
    lister = getattr(store, "list_tasks", None)
    if callable(lister):
        try:
            return {task.id for task in lister(status="running", limit=10_000)}
        except Exception:  # noqa: BLE001 - boot sweep must not raise on listing
            return set()
    return set()


__all__ = ["recover_background_tasks"]
