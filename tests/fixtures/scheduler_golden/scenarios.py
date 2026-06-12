"""Three tick scenarios over the REAL tick() with InMemoryJobSource + a tmp
lock dir. Fixed `now`; receipts/evidence digests are pure functions of the
inputs, so the public projections are byte-stable. Lease/lock constructions
copied from tests/test_scheduler_executor_a2.py.

Same-process lock note: ``acquire_tick_lock`` uses ``flock``, which is
per-(file, fd) — the nested ``tick`` opens its own fd, so the inner
acquisition fails and ``tick_skipped_lock_held`` is returned (the shipped
``_LockHeld`` path the a2 suite already relies on).
"""
from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from magi_agent.harness.scheduler_executor import (
    InMemoryJobSource,
    ScheduledJobRecord,
    acquire_tick_lock,
    tick,
)
from magi_agent.harness.scheduler_runtime import SchedulerLease

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_NOW_MS = int(_NOW.timestamp() * 1000)
_OWNER = "owner-digest-1"


def _lease() -> SchedulerLease:
    return SchedulerLease(
        leaseId="lease-1",
        ownerDigest=_OWNER,
        acquiredAt=_NOW_MS - 1_000,
        expiresAt=_NOW_MS + 60_000,
    )


def _source() -> InMemoryJobSource:
    return InMemoryJobSource(
        [
            ScheduledJobRecord(  # due (next_run in the past)
                jobId="job-due", scheduleExpr="every 10m",
                nextRun=_NOW - timedelta(minutes=1),
            ),
            ScheduledJobRecord(  # not due
                jobId="job-later", scheduleExpr="every 10m",
                nextRun=_NOW + timedelta(hours=1),
            ),
        ]
    )


def _project(result: Any) -> dict[str, Any]:
    return result.public_projection()


def run_tick_fires_due_scenario() -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        source = _source()
        first = tick(now=_NOW, source=source, lease=_lease(),
                     lock_dir=Path(tmp), owner_digest=_OWNER)
        # Second tick at the same instant: job-due advanced, nothing fires.
        second = tick(now=_NOW, source=source, lease=_lease(),
                      lock_dir=Path(tmp), owner_digest=_OWNER)
        return [
            {"scenario": "first_tick", **_project(first)},
            {"scenario": "second_tick_no_refire", **_project(second)},
        ]


def run_tick_blocked_lease_scenario() -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        result = tick(now=_NOW, source=_source(), lease=None,
                      lock_dir=Path(tmp), owner_digest=_OWNER)
        return [{"scenario": "lease_missing", **_project(result)}]


def run_tick_lock_held_scenario() -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        with acquire_tick_lock(lock_dir=Path(tmp)):
            result = tick(now=_NOW, source=_source(), lease=_lease(),
                          lock_dir=Path(tmp), owner_digest=_OWNER)
        return [{"scenario": "lock_held", **_project(result)}]
