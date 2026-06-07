"""A-driver — scheduler loop driver: run_once + run_forever, gated default-OFF.

TDD:
  - gate OFF -> run_once does NOT execute (no runner call); A2 tick still recorded.
  - gate ON  -> run_once calls execute_due_jobs with the wired persistent source +
    runner adapter, advancing the persistent job.
  - run_forever stops cleanly on stop_event.
  - lease/lock reuse: the driver builds a lease and uses the executor file lock.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest


def _dt(seconds: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)


def _record(job_id: str, *, expr: str = "every 60s", next_run_s: int = 0):
    from magi_agent.harness.scheduler_executor import ScheduledJobRecord

    return ScheduledJobRecord(jobId=job_id, scheduleExpr=expr, lastFire=None, nextRun=_dt(next_run_s))


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def run_turn(self, plan: Any) -> Any:
        from magi_agent.harness.scheduler_job_execution import CronTurnResult

        self.calls.append(plan)
        return CronTurnResult(status="completed", jobId=plan.job_id, runnerInvoked=True)


def _make_store(tmp_path: Path):
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    store = SqliteScheduledJobSource(tmp_path / "jobs.db")
    store.create(_record("job:a"))
    return store


def _lease(now: datetime, owner: str = "owner:loop"):
    from magi_agent.harness.scheduler_runtime import SchedulerLease

    now_ms = int(now.timestamp() * 1000)
    return SchedulerLease(
        leaseId="lease:loop",
        ownerDigest=owner,
        acquiredAt=now_ms - 1000,
        expiresAt=now_ms + 60_000,
    )


# ---------------------------------------------------------------------------
# run_once — gating
# ---------------------------------------------------------------------------

def test_run_once_gate_off_no_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
    from magi_agent.harness.scheduler_loop_driver import SchedulerLoopDriver

    store = _make_store(tmp_path)
    runner = _FakeRunner()
    try:
        driver = SchedulerLoopDriver(
            source=store,
            runner=runner,
            owner_digest="owner:loop",
            lock_dir=tmp_path / "lock",
            lease_factory=_lease,
        )
        now = _dt(100)
        result = driver.run_once(now=now)
        # A2 tick still ran (job fired as local_fake), but NO runner invocation.
        assert result.tick_result.status == "tick_completed"
        assert runner.calls == []
        assert result.executions == ()
    finally:
        store.close()


def test_run_once_gate_on_live_invokes_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
    monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "0")
    from magi_agent.harness.scheduler_loop_driver import SchedulerLoopDriver

    store = _make_store(tmp_path)
    runner = _FakeRunner()
    try:
        driver = SchedulerLoopDriver(
            source=store,
            runner=runner,
            owner_digest="owner:loop",
            lock_dir=tmp_path / "lock",
            lease_factory=_lease,
            readiness_execution_mode="live",
        )
        now = _dt(100)
        result = driver.run_once(now=now)
        assert result.tick_result.fired_job_ids == ("job:a",)
        assert len(runner.calls) == 1
        # Persistent source advanced.
        got = store.get("job:a")
        assert got is not None
        assert got.next_run > now
    finally:
        store.close()


def test_run_once_gate_on_shadow_does_not_invoke_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
    monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "1")
    from magi_agent.harness.scheduler_loop_driver import SchedulerLoopDriver

    store = _make_store(tmp_path)
    runner = _FakeRunner()
    try:
        driver = SchedulerLoopDriver(
            source=store,
            runner=runner,
            owner_digest="owner:loop",
            lock_dir=tmp_path / "lock",
            lease_factory=_lease,
        )
        result = driver.run_once(now=_dt(100))
        assert runner.calls == []  # shadow: plan only
        assert len(result.executions) == 1
        assert result.executions[0].mode == "shadow"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# run_forever — clean stop
# ---------------------------------------------------------------------------

def test_run_forever_stops_on_stop_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
    from magi_agent.harness.scheduler_loop_driver import SchedulerLoopDriver

    store = _make_store(tmp_path)
    runner = _FakeRunner()

    async def _run() -> int:
        stop = asyncio.Event()
        driver = SchedulerLoopDriver(
            source=store,
            runner=runner,
            owner_digest="owner:loop",
            lock_dir=tmp_path / "lock",
            lease_factory=_lease,
        )
        # Stop after a short delay; the loop must exit promptly.
        async def _stopper() -> None:
            await asyncio.sleep(0.05)
            stop.set()

        stopper = asyncio.create_task(_stopper())
        ticks = await asyncio.wait_for(
            driver.run_forever(interval_seconds=0.01, stop_event=stop),
            timeout=2.0,
        )
        await stopper
        return ticks

    try:
        ticks = asyncio.run(_run())
        assert ticks >= 1  # ran at least one tick before stopping
    finally:
        store.close()


def test_run_forever_already_stopped_runs_zero_ticks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
    from magi_agent.harness.scheduler_loop_driver import SchedulerLoopDriver

    store = _make_store(tmp_path)
    runner = _FakeRunner()

    async def _run() -> int:
        stop = asyncio.Event()
        stop.set()  # already stopped
        driver = SchedulerLoopDriver(
            source=store,
            runner=runner,
            owner_digest="owner:loop",
            lock_dir=tmp_path / "lock",
            lease_factory=_lease,
        )
        return await asyncio.wait_for(
            driver.run_forever(interval_seconds=0.01, stop_event=stop),
            timeout=2.0,
        )

    try:
        assert asyncio.run(_run()) == 0
    finally:
        store.close()


# ---------------------------------------------------------------------------
# run_forever — tick exception resilience (Minor fix)
# ---------------------------------------------------------------------------

def test_run_forever_continues_after_tick_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tick that raises must NOT terminate the loop; the loop logs and continues.

    The driver uses asyncio.to_thread(self.run_once), so exceptions from that
    thread are caught and logged at WARNING level; the loop proceeds to the next
    interval.  The loop stops only when stop_event is set.
    """
    import logging

    monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)

    tick_count = 0
    logged_warnings: list[str] = []

    class _BrokenOnFirst:
        """Fails on the first call, succeeds afterwards."""
        def run_once(self, **_: Any) -> Any:
            nonlocal tick_count
            tick_count += 1
            if tick_count == 1:
                raise RuntimeError("transient tick error")
            from magi_agent.harness.scheduler_job_execution import JobExecutionResult, TickResult
            from magi_agent.harness.scheduler_runtime import SchedulerTickResult
            tick_result = SchedulerTickResult(
                status="tick_skipped_lock_held",
                firedJobIds=(),
                skippedJobIds=(),
                evidenceDigest="",
            )
            return JobExecutionResult(tickResult=tick_result, executions=())

    from magi_agent.harness.scheduler_loop_driver import SchedulerLoopDriver

    store = _make_store(tmp_path)

    # Patch the logger to capture warnings.
    orig_warning = logging.getLogger("magi_agent.harness.scheduler_loop_driver").warning

    def _capture_warning(msg: str, *args: Any, **kwargs: Any) -> None:
        logged_warnings.append(msg)
        orig_warning(msg, *args, **kwargs)

    logging.getLogger("magi_agent.harness.scheduler_loop_driver").warning = _capture_warning  # type: ignore[method-assign]

    broken = _BrokenOnFirst()

    async def _run() -> int:
        stop = asyncio.Event()
        driver = SchedulerLoopDriver(
            source=store,
            runner=_FakeRunner(),
            owner_digest="owner:loop",
            lock_dir=tmp_path / "lock",
            lease_factory=_lease,
        )
        # Monkey-patch run_once to the broken version so we control tick behavior
        # without triggering real execute_due_jobs machinery.
        driver.run_once = broken.run_once  # type: ignore[method-assign]

        async def _stopper() -> None:
            # Wait until at least 2 ticks have been attempted, then stop.
            import asyncio as _a
            while tick_count < 2:
                await _a.sleep(0.01)
            stop.set()

        stopper = asyncio.create_task(_stopper())
        ticks = await asyncio.wait_for(
            driver.run_forever(interval_seconds=0.01, stop_event=stop),
            timeout=3.0,
        )
        await stopper
        return ticks

    try:
        ticks = asyncio.run(_run())
        # Loop ran at least 2 ticks (first raised, second succeeded) — did not die.
        assert ticks >= 2, f"Expected >=2 ticks, got {ticks}"
        # The warning was logged.
        assert any("tick" in w.lower() or "scheduler" in w.lower() for w in logged_warnings), \
            f"Expected a warning to be logged, got: {logged_warnings}"
    finally:
        store.close()
        logging.getLogger("magi_agent.harness.scheduler_loop_driver").warning = orig_warning  # type: ignore[method-assign]
