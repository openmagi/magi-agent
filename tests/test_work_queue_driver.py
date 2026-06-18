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
