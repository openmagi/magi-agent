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

PR-F-LIFE3: at each task status transition (claimed / completed / failed /
short_circuited) the driver fires the ``on_task_checkpoint`` custom_rule
audit fan-out behind a fail-open envelope so a misbehaving rule cannot
wedge dispatch. The emit is gated by
:func:`magi_agent.customize.lifecycle_audit.lifecycle_extra_emitters_enabled`
so the OFF contract is byte-identical to before this PR.

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


def _emit_task_checkpoint_sync(
    *, task_id: str, checkpoint_kind: str, summary_text: str
) -> None:
    """PR-F-LIFE3 sync helper: fire the ``on_task_checkpoint`` audit fan-out.

    ``run_once`` is synchronous (the work-queue driver runs in a worker
    thread) so the async fan-out is invoked via ``asyncio.run`` — which
    must NOT collide with an outer event loop. The helper checks the
    triple-gate first, then short-circuits on OFF, so the OFF cost is
    one helper call + one comparison. On ON the call is wrapped in a
    fresh asyncio loop. Fail-open at every step: any failure (import
    error, busy event loop, fan-out raise) leaves the dispatcher tick
    intact.

    PR-F-EXEC1: this helper ALSO drives the sibling shell_command
    fan-out at ``on_task_checkpoint`` so an operator-authored
    ``shell_command`` rule with ``firesAt=on_task_checkpoint`` runs from
    this single chokepoint at every task state transition. The shell
    surface is gated independently by ``shell_command_enabled`` so the
    OFF path for the shell flag is byte-identical even when the
    llm_criterion path is ON.
    """
    try:
        from magi_agent.customize.lifecycle_audit import (
            lifecycle_extra_emitters_enabled,
            run_task_checkpoint_audit,
        )

        if lifecycle_extra_emitters_enabled():
            try:
                from magi_agent.adk_bridge.lifecycle_llm_call_control import (
                    _build_critic_factory,
                )

                factory = _build_critic_factory()
            except Exception:
                factory = None
            try:
                asyncio.run(
                    run_task_checkpoint_audit(
                        task_id=task_id,
                        checkpoint_kind=checkpoint_kind,
                        summary_text=summary_text,
                        model_factory=factory,
                    )
                )
            except RuntimeError:
                # An outer event loop is already running (unexpected on this
                # sync path, but defensive): drop the emit rather than
                # raising into the dispatcher.
                pass

        # PR-F-EXEC1 shell_command fan-out at on_task_checkpoint. Gated
        # independently of lifecycle_extra_emitters so an operator can
        # author a shell hook without enabling the llm_criterion path.
        _emit_shell_command_task_checkpoint_sync(
            task_id=task_id,
            checkpoint_kind=checkpoint_kind,
            summary_text=summary_text,
        )

        # PR-M7 hosted projection: transition seam (section 7.2). Reuses this
        # single checkpoint chokepoint so every current and future transition
        # is projected by construction. Fail-open and non-blocking (enqueue
        # only); inert unless the hosted projector config is present.
        from magi_agent.missions.projector import (  # noqa: PLC0415
            notify_task_checkpoint,
        )

        notify_task_checkpoint(
            task_id=task_id,
            checkpoint_kind=checkpoint_kind,
            summary_text=summary_text,
        )
    except Exception:
        # Fail-open: never break dispatch.
        return None
    return None


def _emit_shell_command_task_checkpoint_sync(
    *, task_id: str, checkpoint_kind: str, summary_text: str
) -> None:
    """PR-F-EXEC1 sync helper: fire ``shell_command`` at ``on_task_checkpoint``.

    Sibling of :func:`_emit_task_checkpoint_sync` — shares the same
    asyncio.run boundary, the same fail-open contract, and is gated by
    :func:`magi_agent.customize.lifecycle_audit.shell_command_enabled`
    (master shell flag). Threads the shared per-(session, turn) budget
    from the ``_ACTIVE_TURN_IDENTITY`` ContextVar published by
    ``run_governed_turn`` — the work-queue dispatcher runs OUTSIDE a
    governed turn so identity is typically unset and ``remaining_budget``
    resolves to ``None`` (no cap). When the dispatcher is invoked from
    inside a turn (e.g. by a tool that enqueues a task) the budget IS
    shared with the surrounding turn.
    """
    try:
        from magi_agent.adk_bridge.lifecycle_shell_command_control import (
            shell_budget_for,
        )
        from magi_agent.customize.lifecycle_audit import (
            run_shell_command_at_on_task_checkpoint,
            shell_command_enabled,
        )

        if not shell_command_enabled():
            return None
        remaining, decrement_fn = shell_budget_for()
        try:
            asyncio.run(
                run_shell_command_at_on_task_checkpoint(
                    task_id=task_id,
                    checkpoint_kind=checkpoint_kind,
                    summary_text=summary_text,
                    remaining_budget=remaining,
                    decrement_fn=decrement_fn,
                )
            )
        except RuntimeError:
            return None
    except Exception:
        return None
    return None


class WorkQueueTickResult(BaseModel):
    """Immutable tally returned by a single ``run_once`` tick."""

    model_config = ConfigDict(frozen=True)

    reclaimed: int = 0
    promoted: int = 0
    claimed: int = 0
    completed: int = 0
    failed: int = 0
    short_circuited: int = 0


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
        short_circuited: int = 0

        for task in ready:
            claimed_task = self._store.claim(task.id, claimer=self._claimer, now=now)
            if claimed_task is None:
                # Lost the CAS race — another dispatcher claimed it first.
                logger.debug("CAS loser for task %s; skipping", task.id)
                continue

            claimed += 1
            # PR-F-LIFE3: on_task_checkpoint emit — claimed transition.
            _emit_task_checkpoint_sync(
                task_id=claimed_task.id,
                checkpoint_kind="claimed",
                summary_text=claimed_task.title,
            )

            # Exactly-once at dispatch: keyed tasks must be enqueued via
            # store.create_idempotent (unique idempotency_key constraint).
            # Bypassing that allows two same-key ready tasks to be claimed
            # and run in a single tick before either is marked 'completed',
            # causing both side effects to fire. P6 real-runner enqueue seam
            # MUST route all keyed enqueues through create_idempotent.
            key = claimed_task.idempotency_key
            if key is not None:
                prior = self._store.completed_task_for_key(key, exclude_task_id=claimed_task.id)
                if prior is not None:
                    self._store.complete(claimed_task.id, result=prior.result)
                    short_circuited += 1
                    # PR-F-LIFE3: on_task_checkpoint emit — short-circuit
                    # transition (treated as completed-via-dedupe).
                    _emit_task_checkpoint_sync(
                        task_id=claimed_task.id,
                        checkpoint_kind="short_circuited",
                        summary_text=prior.result or "",
                    )
                    continue

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
                # PR-F-LIFE3: on_task_checkpoint emit — failed (exception).
                _emit_task_checkpoint_sync(
                    task_id=task.id,
                    checkpoint_kind="failed",
                    summary_text=f"unhandled exception: {exc}",
                )
                continue

            if result.outcome == "completed":
                self._store.complete(task.id, result=result.summary)
                completed += 1
                # PR-F-LIFE3: on_task_checkpoint emit — completed transition.
                _emit_task_checkpoint_sync(
                    task_id=task.id,
                    checkpoint_kind="completed",
                    summary_text=result.summary or "",
                )
            else:
                self._store.record_failure(
                    task.id,
                    outcome="failed",
                    error=result.error,
                )
                failed += 1
                # PR-F-LIFE3: on_task_checkpoint emit — failed transition.
                _emit_task_checkpoint_sync(
                    task_id=task.id,
                    checkpoint_kind="failed",
                    summary_text=result.error or "",
                )

        return WorkQueueTickResult(
            reclaimed=reclaimed,
            promoted=promoted,
            claimed=claimed,
            completed=completed,
            failed=failed,
            short_circuited=short_circuited,
        )

    async def run_forever(
        self,
        *,
        interval_seconds: float,
        stop_event: asyncio.Event,
    ) -> int:
        """Run ``run_once`` on a timer until *stop_event* is set.

        Returns the number of ticks executed.  Cleanly stoppable: the loop
        checks ``stop_event`` before each tick and uses ``asyncio.wait_for``
        on the event for the inter-tick sleep so a set event interrupts the
        wait immediately.  ``run_once`` is synchronous so it is offloaded to
        a thread via ``asyncio.to_thread`` to keep the event loop responsive.
        """
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be > 0")

        ticks = 0
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(self.run_once)
            except Exception:  # noqa: BLE001 — transient errors must not kill the loop
                logger.warning("work-queue driver tick failed", exc_info=True)
            ticks += 1
            if stop_event.is_set():
                break
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                # Interval elapsed without a stop — loop again.
                continue
        return ticks


__all__ = [
    "WorkQueueDriver",
    "WorkQueueTickResult",
]
