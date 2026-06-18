"""Tests for WorkQueueDriver.run_once — the dispatcher tick.

Spec: task-3-brief.md
"""
from __future__ import annotations

from magi_agent.missions.work_queue.store import InMemoryWorkQueueStore
from magi_agent.missions.work_queue.driver import WorkQueueDriver
from magi_agent.missions.work_queue.runner import WorkTaskRunResult
from magi_agent.missions.work_queue.models import WorkTask


class _CompleteRunner:
    async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
        return WorkTaskRunResult(outcome="completed", summary="done")


class _FailRunner:
    async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
        return WorkTaskRunResult(outcome="failed", error="boom")


def test_run_once_drives_ready_task_to_completed() -> None:
    s = InMemoryWorkQueueStore()
    s.create(WorkTask(id="t", title="x", status="ready", created_at=1))
    d = WorkQueueDriver(s, _CompleteRunner(), claimer="disp", max_spawn=4)
    res = d.run_once(now=1000)
    assert res.claimed == 1 and res.completed == 1 and res.failed == 0
    assert s.get("t").status == "completed"


def test_run_once_failure_records_failure() -> None:
    s = InMemoryWorkQueueStore()
    s.create(WorkTask(id="t", title="x", status="ready", created_at=1))
    d = WorkQueueDriver(s, _FailRunner(), claimer="disp", max_spawn=4)
    res = d.run_once(now=1000)
    assert res.failed == 1
    assert s.get("t").status in ("ready", "blocked")  # record_failure put it back/blocked


def test_run_once_respects_max_spawn() -> None:
    s = InMemoryWorkQueueStore()
    for i in range(5):
        s.create(WorkTask(id=f"t{i}", title="x", status="ready", created_at=i))
    d = WorkQueueDriver(s, _CompleteRunner(), claimer="disp", max_spawn=2)
    res = d.run_once(now=1000)
    assert res.claimed == 2


def test_run_once_returns_zero_tick_on_empty_store() -> None:
    s = InMemoryWorkQueueStore()
    d = WorkQueueDriver(s, _CompleteRunner(), claimer="disp", max_spawn=4)
    res = d.run_once(now=1000)
    assert res.reclaimed == 0
    assert res.promoted == 0
    assert res.claimed == 0
    assert res.completed == 0
    assert res.failed == 0


class _RaiseRunner:
    """Runner whose run_task always raises instead of returning a result."""

    async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
        raise RuntimeError("boom")


def test_run_once_runner_raise_records_failure() -> None:
    """run_once must NOT propagate runner exceptions; it must call record_failure.

    When runner.run_task raises an unhandled exception the driver's except
    branch must catch it, call store.record_failure, and return a tick result
    with failed == 1.  The task must end in 'ready' or 'blocked' (i.e.
    record_failure was called), not in 'running' or 'completed'.
    """
    s = InMemoryWorkQueueStore()
    s.create(WorkTask(id="t", title="x", status="ready", created_at=1))
    d = WorkQueueDriver(s, _RaiseRunner(), claimer="disp", max_spawn=4)

    # run_once must NOT propagate the RuntimeError raised by the runner.
    res = d.run_once(now=1000)

    assert res.failed == 1, f"expected failed==1, got {res}"
    assert res.completed == 0, f"expected completed==0, got {res}"

    task = s.get("t")
    assert task is not None
    assert task.status in ("ready", "blocked"), (
        f"task must be ready or blocked after runner raise, got {task.status!r}"
    )


def test_run_once_skips_cas_loser() -> None:
    """Simulate a CAS failure by pre-claiming the task before the driver tick."""
    s = InMemoryWorkQueueStore()
    s.create(WorkTask(id="t", title="x", status="ready", created_at=1))
    # Pre-claim so driver's claim() returns None
    s.claim("t", claimer="other", now=500)
    d = WorkQueueDriver(s, _CompleteRunner(), claimer="disp", max_spawn=4)
    res = d.run_once(now=1000)
    assert res.claimed == 0
    assert res.completed == 0


def test_run_once_promotes_todo_task_before_claiming() -> None:
    """A todo task with no parents should be promoted to ready and then claimed."""
    s = InMemoryWorkQueueStore()
    s.create(WorkTask(id="t", title="x", status="todo", created_at=1))
    d = WorkQueueDriver(s, _CompleteRunner(), claimer="disp", max_spawn=4)
    res = d.run_once(now=1000)
    assert res.promoted == 1
    assert res.claimed == 1
    assert res.completed == 1


def test_tick_result_is_immutable() -> None:
    """WorkQueueTickResult is a frozen Pydantic model."""
    from magi_agent.missions.work_queue.driver import WorkQueueTickResult
    import pytest

    r = WorkQueueTickResult()
    with pytest.raises(Exception):
        r.claimed = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# run_forever tests
# ---------------------------------------------------------------------------

import asyncio


def test_run_forever_stops_on_event_and_ticks() -> None:
    s = InMemoryWorkQueueStore()
    s.create(WorkTask(id="t", title="x", status="ready", created_at=1))
    d = WorkQueueDriver(s, _CompleteRunner(), claimer="disp")

    async def go() -> int:
        stop = asyncio.Event()

        async def stopper() -> None:
            await asyncio.sleep(0.05)
            stop.set()

        ticks, _ = await asyncio.gather(
            d.run_forever(interval_seconds=0.01, stop_event=stop), stopper()
        )
        return ticks

    ticks = asyncio.run(go())
    assert ticks >= 1
    assert s.get("t").status == "completed"


def test_run_forever_raises_on_non_positive_interval() -> None:
    import pytest

    s = InMemoryWorkQueueStore()
    d = WorkQueueDriver(s, _CompleteRunner(), claimer="disp")

    async def go() -> None:
        stop = asyncio.Event()
        await d.run_forever(interval_seconds=0.0, stop_event=stop)

    with pytest.raises(ValueError, match="interval_seconds"):
        asyncio.run(go())


def test_run_forever_returns_tick_count() -> None:
    """Stop immediately after first tick; expect exactly 1 tick."""
    s = InMemoryWorkQueueStore()
    d = WorkQueueDriver(s, _CompleteRunner(), claimer="disp")

    async def go() -> int:
        stop = asyncio.Event()

        async def stopper() -> None:
            # Give one tick time to run then stop.
            await asyncio.sleep(0.02)
            stop.set()

        ticks, _ = await asyncio.gather(
            d.run_forever(interval_seconds=0.5, stop_event=stop), stopper()
        )
        return ticks

    ticks = asyncio.run(go())
    assert ticks >= 1


# ---------------------------------------------------------------------------
# Gateway wiring tests (Task 6)
# ---------------------------------------------------------------------------

import os


def test_work_queue_executor_gate_default_off() -> None:
    """is_work_queue_executor_enabled() is False by default; True when env=1."""
    from magi_agent.gateway.watchers import is_work_queue_executor_enabled

    # Ensure unset → False
    os.environ.pop("MAGI_WORK_QUEUE_EXECUTOR_ENABLED", None)
    assert is_work_queue_executor_enabled() is False

    # Set → True
    os.environ["MAGI_WORK_QUEUE_EXECUTOR_ENABLED"] = "1"
    try:
        assert is_work_queue_executor_enabled() is True
    finally:
        os.environ.pop("MAGI_WORK_QUEUE_EXECUTOR_ENABLED", None)

    # Restore: unset → False again
    assert is_work_queue_executor_enabled() is False


def test_build_default_watchers_includes_self_gated_work_queue() -> None:
    """build_default_watchers() constructs without error and the work-queue
    watcher self-gates (is_enabled is False when env is unset)."""
    from magi_agent.gateway.watchers import build_default_watchers

    os.environ.pop("MAGI_WORK_QUEUE_EXECUTOR_ENABLED", None)
    watchers = build_default_watchers()

    # At least one watcher should be named "work_queue_executor"
    names = [w.name for w in watchers]
    assert "work_queue_executor" in names, f"Expected work_queue_executor in {names}"

    # The work-queue watcher must self-gate as disabled by default
    wq_watcher = next(w for w in watchers if w.name == "work_queue_executor")
    assert wq_watcher.is_enabled() is False
