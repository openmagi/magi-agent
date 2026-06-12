"""C3.1 — SchedulePolicy seam on tick() (dual-load).

The "which job / when" decision is a swappable policy; the kernel mechanism
(file lock, lease validation, at-most-once record_advance-before-receipt)
stays policy-agnostic. ``policy=None`` resolves to the first-party cron
policy — byte-identical to the legacy hardcode.
"""
from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from magi_agent.harness.scheduler_executor import (
    CronSchedulePolicy,
    InMemoryJobSource,
    ScheduledJobRecord,
    tick,
)
from magi_agent.harness.scheduler_runtime import SchedulerLease

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_NOW_MS = int(_NOW.timestamp() * 1000)


def _lease() -> SchedulerLease:
    return SchedulerLease(leaseId="lease-1", ownerDigest="owner-1",
                          acquiredAt=_NOW_MS - 1000, expiresAt=_NOW_MS + 60000)


def _due_source() -> InMemoryJobSource:
    return InMemoryJobSource([
        ScheduledJobRecord(jobId="job-a", scheduleExpr="every 10m",
                           nextRun=_NOW - timedelta(minutes=1)),
        ScheduledJobRecord(jobId="job-b", scheduleExpr="every 10m",
                           nextRun=_NOW - timedelta(minutes=2)),
    ])


class _SuppressBPolicy(CronSchedulePolicy):
    """User-shaped policy: refuses to fire job 'job-b' this tick."""

    def select_due(self, due, *, now):
        return [j for j in due if j.job_id != "job-b"]


def test_injected_policy_filters_due_selection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = _due_source()
        result = tick(now=_NOW, source=source, lease=_lease(),
                      lock_dir=Path(tmp), owner_digest="owner-1",
                      policy=_SuppressBPolicy())
        # Mechanism boundary: a policy-suppressed job is NOT advanced — it
        # stays due for the next tick (suppression is not a fire).
        still_due = {j.job_id for j in source.due_jobs(_NOW)}
    assert result.fired_job_ids == ("job-a",)
    assert "job-b" in result.skipped_job_ids
    assert still_due == {"job-b"}


def test_default_policy_is_byte_identical_to_legacy() -> None:
    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        legacy = tick(now=_NOW, source=_due_source(), lease=_lease(),
                      lock_dir=Path(tmp_a), owner_digest="owner-1")
        packed = tick(now=_NOW, source=_due_source(), lease=_lease(),
                      lock_dir=Path(tmp_b), owner_digest="owner-1",
                      policy=CronSchedulePolicy())
    assert legacy.model_dump(by_alias=True) == packed.model_dump(by_alias=True)


def test_execute_due_jobs_accepts_policy_passthrough() -> None:
    """scheduler_job_execution passes the policy through to the inner tick()
    (gate OFF default: result is exactly the tick result)."""
    from magi_agent.harness.scheduler_job_execution import (
        JobExecutionConfig,
        execute_due_jobs,
    )

    def _runner(**_kwargs: object) -> object:  # never invoked (gate OFF)
        raise AssertionError("runner must not be invoked when the gate is off")

    with tempfile.TemporaryDirectory() as tmp:
        result = execute_due_jobs(
            now=_NOW,
            source=_due_source(),
            lease=_lease(),
            owner_digest="owner-1",
            runner=_runner,
            config=JobExecutionConfig(executorEnabled=False, shadow=True),
            lock_dir=Path(tmp),
            policy=_SuppressBPolicy(),
        )
    assert result.tick_result.fired_job_ids == ("job-a",)
    assert "job-b" in result.tick_result.skipped_job_ids
    assert result.executions == ()
