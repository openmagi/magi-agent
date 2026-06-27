"""WS1 PR1b - immediate boot reclaim of freshly-crashed background tasks.

These tests cover the additive ``reclaim_running_for_dead_pids`` method on
``SqliteWorkQueueStore`` (and its Protocol / in-memory sibling) plus the thin
``recover_background_tasks`` boot-sweep glue. The guarantee delivered is
AT-LEAST-ONCE: a partially-executed task whose child side-effect already fired
before the crash is re-run whole on reclaim, re-firing the side effect. True
exactly-once requires WS7-outbox and is documented here as the explicit gap.

Design: clawy/docs/plans/2026-06-25-magi-reliability-WS1-durable-resume-design.md
sections 0.2 (Correction A), 0.3a, 0.3 Correction E (critical 1), and the PR1b
per-PR plan.
"""
from __future__ import annotations

import pytest

from magi_agent.missions.work_queue.models import WorkTask
from magi_agent.missions.work_queue.runner import WorkTaskRunResult


# --- reclaim_running_for_dead_pids (the new additive method) ----------------


def _claim_with_future_lease(store, task_id: str, *, worker_pid: int) -> None:
    """Drive a task into the real fresh-crash state: status='running' with a
    FUTURE claim_expires (now + 15min) owned by *worker_pid*."""
    store.create(WorkTask(id=task_id, title="x", status="ready", created_at=1))
    claimed = store.claim(task_id, claimer="w1", now=1000, worker_pid=worker_pid)
    assert claimed is not None and claimed.status == "running"
    # claim_expires is now+CLAIM_TTL (1000 + 900) = 1900, ~15min in the future
    assert claimed.claim_expires is not None and claimed.claim_expires > 1000


def test_reclaim_running_for_dead_pids_ignores_lease(tmp_path):
    """A fresh crash (future lease, dead pid) is NOT reclaimed by
    release_stale_claims (lease still valid) but IS reclaimed immediately by
    reclaim_running_for_dead_pids. Proves the boot-reclaim path is real and
    distinct from the lease-expiry path (Correction A)."""
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore

    store = SqliteWorkQueueStore(tmp_path / "wq.db")
    _claim_with_future_lease(store, "t", worker_pid=424242)

    # release_stale_claims at the same `now` reclaims NOTHING: the row's lease
    # is still valid (claim_expires > now), so the SELECT never selects it.
    assert store.release_stale_claims(now=1000, pid_alive=lambda _pid: False) == 0
    assert store.get("t").status == "running"

    # The NEW method ignores claim_expires and reclaims the dead-pid row.
    reclaimed = store.reclaim_running_for_dead_pids(now=1000, pid_alive=lambda _pid: False)
    assert reclaimed == 1
    got = store.get("t")
    assert got.status == "ready"
    assert got.claim_lock is None
    assert got.worker_pid is None
    assert got.current_run_id is None


def test_reclaim_keeps_live_pid(tmp_path):
    """A live worker's task (future lease, pid alive) is never reclaimed."""
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore

    store = SqliteWorkQueueStore(tmp_path / "wq.db")
    _claim_with_future_lease(store, "t", worker_pid=111)

    reclaimed = store.reclaim_running_for_dead_pids(now=1000, pid_alive=lambda _pid: True)
    assert reclaimed == 0
    assert store.get("t").status == "running"


def test_reclaim_in_memory_store_parity(tmp_path):
    """The in-memory store exposes the same new method with the same behaviour
    (Protocol parity)."""
    from magi_agent.missions.work_queue.store import InMemoryWorkQueueStore

    store = InMemoryWorkQueueStore()
    _claim_with_future_lease(store, "t", worker_pid=424242)

    assert store.release_stale_claims(now=1000, pid_alive=lambda _pid: False) == 0
    assert store.reclaim_running_for_dead_pids(now=1000, pid_alive=lambda _pid: False) == 1
    assert store.get("t").status == "ready"


# --- fake runners for the driver-level scenarios ----------------------------


class _RecordingRunner:
    """Records every run_task invocation; always completes."""

    def __init__(self) -> None:
        self.runs: list[str] = []

    async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
        self.runs.append(task.id)
        return WorkTaskRunResult(outcome="completed", summary=f"ran {task.id}")


class _SideEffectThenCrashRunner:
    """Fires a side effect (increments a counter) then RAISES before complete().

    Simulates a child that already sent a Telegram/MCP/channel message and then
    the process crashed before driver.run_once reached store.complete().
    """

    def __init__(self, counter: list[int]) -> None:
        self._counter = counter

    async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
        self._counter[0] += 1  # the external side effect fires
        raise RuntimeError("crash after side effect, before complete()")


class _OutboxStubRunner:
    """Minimal WS7-outbox shape: mark-side-effect-fired BEFORE firing it, and
    treat an already-fired marker as a no-op on re-run. Proves the WS7 mechanism
    is what closes the at-least-once gap."""

    def __init__(self, counter: list[int], fired: set[str]) -> None:
        self._counter = counter
        self._fired = fired

    async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
        if task.idempotency_key in self._fired:
            return WorkTaskRunResult(outcome="completed", summary="no-op (already fired)")
        # WS7 shape: record the side-effect as fired BEFORE complete() so a
        # crash-then-replay sees the marker and is a no-op.
        self._fired.add(task.idempotency_key or task.id)
        self._counter[0] += 1
        return WorkTaskRunResult(outcome="completed", summary="fired once")


def _driver(store, runner):
    from magi_agent.missions.work_queue.driver import WorkQueueDriver

    return WorkQueueDriver(store, runner, claimer="dispatcher-0")


# --- driver-level crash / reclaim scenarios ---------------------------------


def test_crash_release_dedupes_different_completed_row():
    """DIFFERENT-row dedupe: a re-leased task whose key already has a DIFFERENT
    completed sibling row is short-circuited via completed_task_for_key, not
    re-run. (This is the ONLY shape completed_task_for_key catches.)

    ``create()`` is fail-closed against duplicate keys, so (as the existing
    dispatcher dedupe test does) we seed the historic dup row by mutating the
    in-memory store directly. This test exercises ONLY the DIFFERENT-row dedupe;
    it does NOT claim exactly-once for a single partially-executed task."""
    from magi_agent.missions.work_queue.store import InMemoryWorkQueueStore

    store = InMemoryWorkQueueStore()
    runner = _RecordingRunner()

    # A DIFFERENT row with the same key, already completed.
    store.create(
        WorkTask(id="done", title="prior", status="completed", created_at=1,
                 idempotency_key="K", result="prior-result")
    )
    # The crashed row for the same key: running, future lease, dead pid.
    # Seed it directly (bypass the create() dup guard, like a historic dup).
    store._tasks["crashed"] = WorkTask(
        id="crashed", title="x", status="running", created_at=2,
        idempotency_key="K", claim_lock="w1", claim_expires=1900, worker_pid=424242,
    )
    # Another, unrelated keyless task that should run normally.
    store.create(WorkTask(id="other", title="y", status="ready", created_at=3))

    store.reclaim_running_for_dead_pids(now=1000, pid_alive=lambda _pid: False)
    result = _driver(store, runner).run_once(now=1001)

    # 'crashed' is short-circuited (no re-run); 'other' runs.
    assert "crashed" not in runner.runs
    assert "other" in runner.runs
    assert result.short_circuited == 1
    assert store.get("crashed").status == "completed"


def test_partial_execution_side_effect_refires_without_outbox(tmp_path):
    """AT-LEAST-ONCE reality (Correction E / critical 1): a SINGLE task whose
    child side-effect already fired then crashed before complete() is reclaimed
    and re-run WHOLE, re-firing the side effect. Asserts the counter is 2.

    This documents at-least-once honestly and prevents a future false
    'exactly-once' claim from passing GREEN while masking the re-fire."""
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore

    store = SqliteWorkQueueStore(tmp_path / "wq.db")
    counter = [0]

    # First "process": the task fires its side effect then crashes before
    # complete(). We simulate the crash by firing once directly and leaving the
    # row in the running+dead-pid state (no complete() was reached).
    store.create(WorkTask(id="t", title="send msg", status="ready", created_at=1,
                          idempotency_key="K"))
    store.claim("t", claimer="w1", now=1000, worker_pid=424242)
    counter[0] += 1  # the pre-crash side effect already fired

    # Reboot: reclaim the dead-pid task and re-run it whole.
    store.reclaim_running_for_dead_pids(now=2000, pid_alive=lambda _pid: False)
    runner = _SideEffectThenCrashRunner(counter)
    # run_once re-claims and re-runs; the runner fires AGAIN then raises.
    _driver(store, runner).run_once(now=2001)

    # completed_task_for_key finds NO other completed row (the same task is now
    # ready, not completed) -> the whole task re-runs -> side effect re-fires.
    assert counter[0] == 2  # at-least-once: re-fired


def test_partial_execution_idempotent_with_outbox_stub(tmp_path):
    """WS7-outbox shape closes the gap: mark-fired-before-complete makes the
    re-run a no-op. Asserts the counter stays at 1. Proves the fix is specified,
    not just named."""
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore

    store = SqliteWorkQueueStore(tmp_path / "wq.db")
    counter = [0]
    fired: set[str] = set()

    store.create(WorkTask(id="t", title="send msg", status="ready", created_at=1,
                          idempotency_key="K"))
    store.claim("t", claimer="w1", now=1000, worker_pid=424242)
    # First run fires once and records the marker BEFORE the (simulated) crash.
    fired.add("K")
    counter[0] += 1

    store.reclaim_running_for_dead_pids(now=2000, pid_alive=lambda _pid: False)
    runner = _OutboxStubRunner(counter, fired)
    _driver(store, runner).run_once(now=2001)

    assert counter[0] == 1  # exactly-once via the outbox marker


# --- recover_background_tasks boot-sweep glue -------------------------------


def test_recover_incomplete_returns_reclaimed_ids(tmp_path, monkeypatch):
    """recover_background_tasks reclaims dead-pid tasks, runs a tick, and returns
    the reclaimed task ids so the boot sweep can log/surface them."""
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore
    from magi_agent.missions.work_queue.recovery import recover_background_tasks

    monkeypatch.setenv("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED", "1")
    store = SqliteWorkQueueStore(tmp_path / "wq.db")
    runner = _RecordingRunner()
    _claim_with_future_lease(store, "t", worker_pid=424242)

    driver = _driver(store, runner)
    reclaimed = recover_background_tasks(
        store, driver, enabled=True, pid_alive=lambda _pid: False, now=2000
    )

    assert list(reclaimed) == ["t"]
    # The reclaimed task was re-run by the tick.
    assert "t" in runner.runs
    assert store.get("t").status == "completed"


def test_off_no_reclaim(tmp_path, monkeypatch):
    """With MAGI_DURABLE_STARTUP_RECOVERY_ENABLED unset, recover_background_tasks
    is a no-op: it reclaims nothing, runs no tick, and the crashed row is
    untouched (OFF path byte-identical)."""
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore
    from magi_agent.missions.work_queue.recovery import recover_background_tasks
    from magi_agent.config.flags import flag_bool

    monkeypatch.delenv("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED", raising=False)
    assert flag_bool("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED") is False

    store = SqliteWorkQueueStore(tmp_path / "wq.db")
    runner = _RecordingRunner()
    _claim_with_future_lease(store, "t", worker_pid=424242)

    driver = _driver(store, runner)
    reclaimed = recover_background_tasks(
        store, driver,
        enabled=flag_bool("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED"),
        pid_alive=lambda _pid: False,
    )

    assert list(reclaimed) == []
    assert runner.runs == []
    assert store.get("t").status == "running"  # untouched


def test_startup_recovery_flag_registered():
    """The new flag is registered in the typed flag registry (default-OFF)."""
    from magi_agent.config.flags import flag_bool

    # Resolves through the registry without raising (registered) and defaults OFF.
    assert flag_bool("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED") is False
