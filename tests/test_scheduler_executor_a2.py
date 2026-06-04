"""A2 — SchedulerExecutor: file-lock lease holder + at-most-once tick (no execution).

TDD: RED → GREEN → REFACTOR
Tests cover:
  - file-lock prevents concurrent double-fire (2nd acquire → skipped)
  - due-detection (next_run <= now fires, future next_run does not)
  - advance-before-execute ordering (record_advance called before receipt emission)
  - lease rejection paths (missing / owner-mismatch / stale → no fire)
  - idempotent re-tick within same window (already-advanced job does not re-fire)
  - frozen models, authority flags all False, local_fake receipt shape
"""
from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers to build test fixtures
# ---------------------------------------------------------------------------

def _make_lease(*, owner_digest: str = "owner:test-abc", now_ms: int = 1_000_000) -> Any:
    from magi_agent.harness.scheduler_runtime import SchedulerLease

    return SchedulerLease(
        leaseId="lease:test-abc",
        ownerDigest=owner_digest,
        acquiredAt=now_ms - 1000,
        expiresAt=now_ms + 60_000,
    )


def _make_source(jobs: list[dict[str, Any]]) -> Any:
    """Build an InMemoryJobSource pre-populated with jobs."""
    from magi_agent.harness.scheduler_executor import InMemoryJobSource, ScheduledJobRecord

    records: list[ScheduledJobRecord] = []
    for j in jobs:
        records.append(
            ScheduledJobRecord(
                jobId=j["job_id"],
                scheduleExpr=j["schedule_expr"],
                lastFire=j.get("last_fire"),
                nextRun=datetime.fromtimestamp(j["next_run_ms"] / 1000, tz=UTC),
            )
        )
    return InMemoryJobSource(records)


def _now_dt(ms: int = 1_000_000) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


# ---------------------------------------------------------------------------
# Basic import / frozen model test
# ---------------------------------------------------------------------------

def test_scheduler_executor_imports_cleanly() -> None:
    from magi_agent.harness.scheduler_executor import (  # noqa: F401
        ScheduledJobRecord,
        ScheduledJobSource,
        InMemoryJobSource,
        SchedulerTickResult,
        SchedulerExecutorAuthorityFlags,
        tick,
    )


def test_scheduled_job_record_is_frozen() -> None:
    from magi_agent.harness.scheduler_executor import ScheduledJobRecord

    rec = ScheduledJobRecord(
        jobId="job:abc123",
        scheduleExpr="every 10m",
        lastFire=None,
        nextRun=_now_dt(),
    )
    with pytest.raises(Exception):
        rec.job_id = "mutated"  # type: ignore[misc]


def test_tick_result_is_frozen() -> None:
    from magi_agent.harness.scheduler_executor import SchedulerTickResult

    result = SchedulerTickResult(
        status="tick_skipped_lock_held",
        firedJobIds=(),
        skippedJobIds=(),
        leaseState="missing",
        evidenceDigest="sha256:" + "a" * 64,
    )
    with pytest.raises(Exception):
        result.status = "mutated"  # type: ignore[misc]


def test_authority_flags_all_false() -> None:
    from magi_agent.harness.scheduler_executor import SchedulerExecutorAuthorityFlags

    flags = SchedulerExecutorAuthorityFlags()
    values = flags.model_dump(by_alias=True).values()
    assert all(v is False for v in values), "All authority flags must be False"


# ---------------------------------------------------------------------------
# Due-detection: job with next_run <= now fires; future job does not
# ---------------------------------------------------------------------------

def test_due_job_fires_when_next_run_lte_now(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_executor import tick

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)

    # next_run is 1 second before now — due
    source = _make_source([
        {
            "job_id": "job:due-001",
            "schedule_expr": "every 10m",
            "next_run_ms": now_ms - 1_000,
        }
    ])

    result = tick(now=now, source=source, lease=lease, lock_dir=tmp_path)
    assert result.status == "tick_completed"
    assert "job:due-001" in result.fired_job_ids


def test_future_job_does_not_fire(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_executor import tick

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)

    # next_run is 1 second AFTER now — not due
    source = _make_source([
        {
            "job_id": "job:future-001",
            "schedule_expr": "every 10m",
            "next_run_ms": now_ms + 1_000,
        }
    ])

    result = tick(now=now, source=source, lease=lease, lock_dir=tmp_path)
    assert result.status == "tick_completed"
    assert "job:future-001" not in result.fired_job_ids
    assert "job:future-001" in result.skipped_job_ids


def test_no_jobs_returns_tick_completed_with_empty_fired(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_executor import tick

    now = _now_dt()
    lease = _make_lease()
    source = _make_source([])

    result = tick(now=now, source=source, lease=lease, lock_dir=tmp_path)
    assert result.status == "tick_completed"
    assert result.fired_job_ids == ()
    assert result.skipped_job_ids == ()


# ---------------------------------------------------------------------------
# Advance-before-execute: record_advance is called before receipt emission
# ---------------------------------------------------------------------------

def test_advance_called_before_receipt(tmp_path: Any) -> None:
    """record_advance must be called BEFORE the execution-intent receipt is emitted.

    Ordering proof: both the record_advance call and the on_receipt callback
    append a marker to call_order.  The test asserts that for every job the
    advance marker appears at a strictly lower index than the receipt marker.
    """
    from magi_agent.harness.scheduler_executor import (
        InMemoryJobSource,
        ScheduledJobRecord,
        tick,
    )

    call_order: list[str] = []

    class TrackingJobSource(InMemoryJobSource):
        def record_advance(self, job_id: str, next_run: datetime) -> None:
            call_order.append(f"advance:{job_id}")
            super().record_advance(job_id, next_run)

    def on_receipt(job_id: str, receipt: dict[str, object]) -> None:
        call_order.append(f"receipt:{job_id}")

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)

    record = ScheduledJobRecord(
        jobId="job:track-001",
        scheduleExpr="every 10m",
        lastFire=None,
        nextRun=datetime.fromtimestamp((now_ms - 500) / 1000, tz=UTC),
    )
    source = TrackingJobSource([record])

    result = tick(now=now, source=source, lease=lease, lock_dir=tmp_path, on_receipt=on_receipt)
    assert result.status == "tick_completed"
    assert "job:track-001" in result.fired_job_ids

    # Both markers must be present
    assert "advance:job:track-001" in call_order, "record_advance was never called"
    assert "receipt:job:track-001" in call_order, "on_receipt was never called"

    # Ordering proof: advance must appear strictly before receipt
    advance_idx = call_order.index("advance:job:track-001")
    receipt_idx = call_order.index("receipt:job:track-001")
    assert advance_idx < receipt_idx, (
        f"advance ({advance_idx}) must come before receipt ({receipt_idx}); "
        f"full order: {call_order}"
    )


def test_retick_same_window_does_not_refire(tmp_path: Any) -> None:
    """A second tick at the same now must not re-fire (next_run already advanced)."""
    from magi_agent.harness.scheduler_executor import (
        InMemoryJobSource,
        ScheduledJobRecord,
        tick,
    )

    receipt_calls: list[str] = []

    def on_receipt(job_id: str, receipt: dict[str, object]) -> None:
        receipt_calls.append(job_id)

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)

    record = ScheduledJobRecord(
        jobId="job:retick-001",
        scheduleExpr="every 10m",
        lastFire=None,
        nextRun=datetime.fromtimestamp((now_ms - 500) / 1000, tz=UTC),
    )
    source = InMemoryJobSource([record])

    # First tick — should fire once
    result1 = tick(now=now, source=source, lease=lease, lock_dir=tmp_path, on_receipt=on_receipt)
    assert "job:retick-001" in result1.fired_job_ids

    # Second tick at SAME now — next_run is already advanced; must not fire again
    result2 = tick(now=now, source=source, lease=lease, lock_dir=tmp_path, on_receipt=on_receipt)
    assert "job:retick-001" not in result2.fired_job_ids

    # on_receipt called exactly once (from the first tick only)
    assert receipt_calls.count("job:retick-001") == 1, (
        f"expected exactly 1 receipt for job:retick-001, got {receipt_calls}"
    )


def test_record_advance_updates_next_run(tmp_path: Any) -> None:
    """After a tick, the job's next_run in the source is advanced past now."""
    from magi_agent.harness.scheduler_executor import tick

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)

    source = _make_source([
        {
            "job_id": "job:advance-001",
            "schedule_expr": "every 10m",
            "next_run_ms": now_ms - 500,
        }
    ])

    result = tick(now=now, source=source, lease=lease, lock_dir=tmp_path)
    assert result.status == "tick_completed"
    assert "job:advance-001" in result.fired_job_ids

    # After tick, next_run must be in the future (advanced)
    advanced_jobs = source.due_jobs(now)
    due_ids = [j.job_id for j in advanced_jobs]
    assert "job:advance-001" not in due_ids, "After advance, job should no longer be due at same now"


# ---------------------------------------------------------------------------
# Idempotent re-tick: already-advanced job does not re-fire
# ---------------------------------------------------------------------------

def test_idempotent_retick_within_same_window(tmp_path: Any) -> None:
    """A second tick at the same now does not re-fire an already-advanced job."""
    from magi_agent.harness.scheduler_executor import tick

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)

    source = _make_source([
        {
            "job_id": "job:idem-001",
            "schedule_expr": "every 10m",
            "next_run_ms": now_ms - 500,
        }
    ])

    # First tick — should fire
    result1 = tick(now=now, source=source, lease=lease, lock_dir=tmp_path)
    assert "job:idem-001" in result1.fired_job_ids

    # Second tick at SAME now — should NOT fire (next_run already advanced)
    result2 = tick(now=now, source=source, lease=lease, lock_dir=tmp_path)
    assert "job:idem-001" not in result2.fired_job_ids


# ---------------------------------------------------------------------------
# Lease rejection paths
# ---------------------------------------------------------------------------

def test_tick_without_lease_is_blocked(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_executor import tick

    now = _now_dt()
    source = _make_source([
        {"job_id": "job:blocked-001", "schedule_expr": "every 10m", "next_run_ms": 0}
    ])

    result = tick(now=now, source=source, lease=None, lock_dir=tmp_path)
    assert result.status == "tick_blocked_lease"
    assert result.lease_state == "missing"
    assert result.fired_job_ids == ()


def test_tick_with_owner_mismatch_is_blocked(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_executor import tick, SchedulerLease

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = SchedulerLease(
        leaseId="lease:mismatch",
        ownerDigest="owner:WRONG",
        acquiredAt=now_ms - 1000,
        expiresAt=now_ms + 60_000,
    )

    source = _make_source([
        {"job_id": "job:mismatch-001", "schedule_expr": "every 10m", "next_run_ms": 0}
    ])

    # Pass a different owner_digest to tick
    result = tick(
        now=now,
        source=source,
        lease=lease,
        lock_dir=tmp_path,
        owner_digest="owner:CORRECT",
    )
    assert result.status == "tick_blocked_lease"
    assert result.lease_state == "owner_mismatch"
    assert result.fired_job_ids == ()


def test_tick_with_stale_lease_is_blocked(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_executor import tick, SchedulerLease

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    # Lease expired before now
    lease = SchedulerLease(
        leaseId="lease:stale",
        ownerDigest="owner:abc",
        acquiredAt=now_ms - 10_000,
        expiresAt=now_ms - 1,  # expired
    )

    source = _make_source([
        {"job_id": "job:stale-001", "schedule_expr": "every 10m", "next_run_ms": 0}
    ])

    result = tick(now=now, source=source, lease=lease, lock_dir=tmp_path, owner_digest="owner:abc")
    assert result.status == "tick_blocked_lease"
    assert result.lease_state == "stale"
    assert result.fired_job_ids == ()


def test_blocked_tick_does_not_fire_any_job(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_executor import tick

    now = _now_dt()
    source = _make_source([
        {"job_id": "job:no-lease-001", "schedule_expr": "every 10m", "next_run_ms": 0},
        {"job_id": "job:no-lease-002", "schedule_expr": "every 5m", "next_run_ms": 0},
    ])

    result = tick(now=now, source=source, lease=None, lock_dir=tmp_path)
    assert result.fired_job_ids == ()
    # No advances should have happened
    due_jobs = source.due_jobs(now)
    assert len(due_jobs) == 2, "Jobs should still be due — no advance without valid lease"


# ---------------------------------------------------------------------------
# File-lock: concurrent tick is a no-op (lock held → skipped)
# ---------------------------------------------------------------------------

def test_concurrent_tick_skipped_when_lock_held(tmp_path: Any) -> None:
    """Two concurrent ticks: second one must get status tick_skipped_lock_held."""
    from magi_agent.harness.scheduler_executor import (
        acquire_tick_lock,
        tick,
    )

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    source = _make_source([
        {"job_id": "job:concurrent-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}
    ])

    results: list[Any] = []
    barrier = threading.Barrier(2)
    lock_held_event = threading.Event()
    release_event = threading.Event()

    def first_tick() -> None:
        with acquire_tick_lock(lock_dir=tmp_path):
            lock_held_event.set()
            barrier.wait()
            # Hold lock while second thread tries
            release_event.wait(timeout=3.0)
        # Record that we got the lock
        results.append("first_held")

    def second_tick() -> None:
        barrier.wait()
        # Try to tick while lock is held — should be skipped
        result = tick(now=now, source=source, lease=lease, lock_dir=tmp_path)
        results.append(result)
        release_event.set()

    t1 = threading.Thread(target=first_tick)
    t2 = threading.Thread(target=second_tick)
    t1.start()
    t2.start()
    lock_held_event.wait(timeout=3.0)
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    second_results = [r for r in results if hasattr(r, "status")]
    assert len(second_results) == 1
    assert second_results[0].status == "tick_skipped_lock_held"
    assert second_results[0].fired_job_ids == ()


# ---------------------------------------------------------------------------
# Evidence digest is present on tick result
# ---------------------------------------------------------------------------

def test_tick_result_has_evidence_digest(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_executor import tick

    now = _now_dt()
    lease = _make_lease()
    source = _make_source([])

    result = tick(now=now, source=source, lease=lease, lock_dir=tmp_path)
    assert result.evidence_digest.startswith("sha256:"), (
        f"evidence_digest must be a sha256: prefixed string, got: {result.evidence_digest!r}"
    )


# ---------------------------------------------------------------------------
# Execution authority flags are always False
# ---------------------------------------------------------------------------

def test_tick_result_authority_flags_all_false(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_executor import tick

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    source = _make_source([
        {"job_id": "job:auth-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}
    ])

    result = tick(now=now, source=source, lease=lease, lock_dir=tmp_path)
    values = result.authority_flags.model_dump(by_alias=True).values()
    assert all(v is False for v in values), "All authority flags on tick result must be False"


# ---------------------------------------------------------------------------
# Module graph import purity (no forbidden imports)
# ---------------------------------------------------------------------------

def test_scheduler_executor_no_live_adk_imports() -> None:
    """scheduler_executor must not import ADK runners, network libs, or live infra.

    LIVE / INFRA forbidden set (must never appear — test fails if any are loaded):
      google.adk, google.genai, magi_agent.adk_bridge, magi_agent.transport,
      magi_agent.routing, magi_agent.deploy, magi_agent.chat_proxy,
      magi_agent.runtime_selector, magi_agent.k8s, kubernetes, telegram, discord,
      requests, httpx, aiohttp, playwright, selenium.

    DIRECT import check for stdlib dangerous trio (urllib / socket / subprocess):
      scheduler_executor's own top-level imports must NOT include urllib, socket, or
      subprocess.  We verify this by inspecting the source text — these modules must
      not appear as direct imports in this boundary module.

    PRE-EXISTING TRANSITIVE NOTE: importing pydantic (or scheduler_runtime, which
    imports pydantic) causes urllib, socket, and subprocess to appear in sys.modules
    as transitive side-effects on all scheduler modules in this repo.  This is a
    known pre-existing issue tracked by the two already-RED tests on origin/main:
      - tests/test_live_scheduler_runtime_boundary.py::test_scheduler_runtime_boundary_has_no_live_imports
      - tests/test_cron_scheduler_modules_have_no_live_runtime_imports (if present)
    Because those transients are injected by pydantic — not by scheduler_executor
    itself — we do NOT include them in the sys.modules check below (which would make
    this test fail for the same pre-existing reason, not a regression we own).
    """
    import ast
    import subprocess
    import sys
    from pathlib import Path

    # --- Part 1: Verify scheduler_executor has NO direct imports of the dangerous trio ---
    src = Path(__file__).parent.parent / "magi_agent" / "harness" / "scheduler_executor.py"
    tree = ast.parse(src.read_text())
    direct_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                direct_imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                direct_imports.add(node.module.split(".")[0])
    dangerous_direct = {"urllib", "socket", "subprocess"} & direct_imports
    assert not dangerous_direct, (
        f"scheduler_executor directly imports stdlib dangerous modules: {dangerous_direct}. "
        "These must not appear as direct top-level imports in this boundary module."
    )

    # --- Part 2: subprocess-isolated sys.modules check for live/infra imports ---
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.harness.scheduler_executor")

# Genuinely forbidden: ADK runners, live agent infrastructure, heavy network libs.
# Note: urllib/socket/subprocess are intentionally excluded from this sys.modules
# check because pydantic's transitive import graph pulls them on ALL scheduler
# modules (pre-existing, tracked by two already-RED tests on origin/main).
# scheduler_executor does not directly import them (asserted separately above).
forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "magi_agent.adk_bridge",
    "magi_agent.transport",
    "magi_agent.routing",
    "magi_agent.deploy",
    "magi_agent.chat_proxy",
    "magi_agent.runtime_selector",
    "magi_agent.k8s",
    "kubernetes",
    "telegram",
    "discord",
    "requests",
    "httpx",
    "aiohttp",
    "playwright",
    "selenium",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"forbidden live/infra modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


# ---------------------------------------------------------------------------
# Multiple due jobs: all fired, all advanced
# ---------------------------------------------------------------------------

def test_multiple_due_jobs_all_fired(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_executor import tick

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)

    source = _make_source([
        {"job_id": "job:multi-001", "schedule_expr": "every 5m", "next_run_ms": now_ms - 1000},
        {"job_id": "job:multi-002", "schedule_expr": "every 10m", "next_run_ms": now_ms - 2000},
        {"job_id": "job:multi-003", "schedule_expr": "every 15m", "next_run_ms": now_ms + 1000},  # not due
    ])

    result = tick(now=now, source=source, lease=lease, lock_dir=tmp_path)
    assert result.status == "tick_completed"
    assert set(result.fired_job_ids) == {"job:multi-001", "job:multi-002"}
    assert "job:multi-003" in result.skipped_job_ids
