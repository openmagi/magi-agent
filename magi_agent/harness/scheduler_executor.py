"""A2 — SchedulerExecutor: file-lock lease holder + at-most-once tick.

Boundary module.  No agent spawn, no network, no DB writes.  All authority
flags are Literal[False].  Provides:

- ``acquire_tick_lock(lock_dir)`` — non-blocking OS file lock context manager.
  If the lock is already held the caller gets ``tick_skipped_lock_held``.
- ``tick(now, source, lease, ...)`` — acquire lock → validate lease → find due
  jobs → record_advance before receipt → emit local_fake receipt per due job.
- ``ScheduledJobSource`` — Protocol seam (injectable; no real persistence here).
- ``InMemoryJobSource`` — deterministic in-memory implementation for tests.
- ``ScheduledJobRecord`` — frozen model aligned with job_queue / cron_policy naming.

At-most-once guarantee: ``record_advance`` is called **before** the receipt for
each due job is recorded; a crash between advance and receipt misses at most one
fire (never double-fires).

Forbidden imports: urllib, socket, subprocess, http, requests — none of these
appear in this module or its local import graph.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.harness.scheduler_runtime import SchedulerLease
from magi_agent.missions.schedule_grammar import ScheduleSpec, next_run_at, parse_schedule


# ---------------------------------------------------------------------------
# Module-level config
# ---------------------------------------------------------------------------

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

_LOCK_FILENAME = ".tick.lock"

# Default lock dir under ~/.magi/scheduler/
def _default_lock_dir() -> Path:
    override = os.environ.get("MAGI_SCHEDULER_LOCK_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".magi" / "scheduler"


# ---------------------------------------------------------------------------
# Cross-platform file locking
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    import msvcrt  # type: ignore[import]

    def _lock_file_exclusive_nonblocking(fd: int) -> bool:
        """Try to acquire an exclusive lock on fd. Returns True on success."""
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False

    def _unlock_file(fd: int) -> None:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        except OSError:
            pass

else:
    import fcntl  # type: ignore[import]

    def _lock_file_exclusive_nonblocking(fd: int) -> bool:
        """Try to acquire an exclusive lock on fd. Returns True on success."""
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock_file(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ScheduledJobRecord(BaseModel):
    """Immutable job record for the scheduler executor seam.

    Field naming mirrors AgentJob (job_id, job_kind, …) from job_queue.py and
    the schedule_expression field shape from cron_policy.py.
    """

    model_config = _MODEL_CONFIG

    job_id: str = Field(alias="jobId")
    schedule_expr: str = Field(alias="scheduleExpr")
    last_fire: datetime | None = Field(default=None, alias="lastFire")
    next_run: datetime = Field(alias="nextRun")

    def _parsed_spec(self) -> ScheduleSpec:
        return parse_schedule(self.schedule_expr)

    def compute_next_run(self, *, now: datetime) -> datetime | None:
        """Compute the next run time after a fire at *now*."""
        spec = self._parsed_spec()
        return next_run_at(spec, now=now, last_fire=now)


class SchedulerExecutorAuthorityFlags(BaseModel):
    """All execution/agent-spawn authority flags are Literal[False].

    Pattern mirrors SchedulerAuthorityFlags from scheduler_runtime.py.
    """

    model_config = _MODEL_CONFIG

    background_task_started: Literal[False] = Field(
        default=False, alias="backgroundTaskStarted"
    )
    agent_spawned: Literal[False] = Field(default=False, alias="agentSpawned")
    production_channel_write: Literal[False] = Field(
        default=False, alias="productionChannelWrite"
    )
    channel_delivery_performed: Literal[False] = Field(
        default=False, alias="channelDeliveryPerformed"
    )
    live_tool_execution: Literal[False] = Field(
        default=False, alias="liveToolExecution"
    )
    filesystem_mutation_allowed: Literal[False] = Field(
        default=False, alias="filesystemMutationAllowed"
    )
    database_mutation_allowed: Literal[False] = Field(
        default=False, alias="databaseMutationAllowed"
    )
    network_call_allowed: Literal[False] = Field(
        default=False, alias="networkCallAllowed"
    )


TickStatus = Literal[
    "tick_completed",
    "tick_blocked_lease",
    "tick_skipped_lock_held",
]

LeaseState = Literal[
    "valid",
    "missing",
    "owner_mismatch",
    "stale",
]


class SchedulerTickResult(BaseModel):
    """Frozen summary of a single tick invocation."""

    model_config = _MODEL_CONFIG

    status: TickStatus
    fired_job_ids: tuple[str, ...] = Field(default=(), alias="firedJobIds")
    skipped_job_ids: tuple[str, ...] = Field(default=(), alias="skippedJobIds")
    lease_state: LeaseState = Field(alias="leaseState")
    evidence_digest: str = Field(alias="evidenceDigest")
    authority_flags: SchedulerExecutorAuthorityFlags = Field(
        default_factory=SchedulerExecutorAuthorityFlags,
        alias="authorityFlags",
    )

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "firedJobIds": list(self.fired_job_ids),
            "skippedJobIds": list(self.skipped_job_ids),
            "leaseState": self.lease_state,
            "evidenceDigest": self.evidence_digest,
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


# ---------------------------------------------------------------------------
# Job source Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ScheduledJobSource(Protocol):
    """Minimal seam for querying due jobs and recording next-run advances.

    Concrete implementations may be backed by a database, a Redis store, or
    (for tests) an in-memory list.  A2 only uses this Protocol — no real
    persistence is wired here.
    """

    def due_jobs(self, now: datetime) -> Sequence[ScheduledJobRecord]:
        """Return all jobs whose next_run <= now."""
        ...

    def record_advance(self, job_id: str, next_run: datetime) -> None:
        """Persist the new next_run for job_id (called before receipt emission)."""
        ...


# ---------------------------------------------------------------------------
# In-memory fake implementation (for tests and local-fake mode)
# ---------------------------------------------------------------------------

class InMemoryJobSource:
    """Deterministic in-memory job store for tests and local-fake ticks."""

    def __init__(self, records: Sequence[ScheduledJobRecord] = ()) -> None:
        # Keyed by job_id for O(1) advance updates
        self._records: dict[str, ScheduledJobRecord] = {r.job_id: r for r in records}

    def due_jobs(self, now: datetime) -> list[ScheduledJobRecord]:
        return [r for r in self._records.values() if r.next_run <= now]

    def record_advance(self, job_id: str, next_run: datetime) -> None:
        existing = self._records.get(job_id)
        if existing is None:
            return
        # Build updated record preserving immutability (construct new frozen model)
        updated = ScheduledJobRecord(
            jobId=existing.job_id,
            scheduleExpr=existing.schedule_expr,
            lastFire=existing.next_run,  # old next_run becomes last_fire
            nextRun=next_run,
        )
        self._records[job_id] = updated


# ---------------------------------------------------------------------------
# File-lock context manager
# ---------------------------------------------------------------------------

class _LockHeld(Exception):
    """Raised internally when the tick lock is already held."""


@contextmanager
def acquire_tick_lock(*, lock_dir: Path | None = None) -> Iterator[None]:
    """Non-blocking exclusive file lock for the scheduler tick.

    Yields if the lock was acquired.  Raises _LockHeld if already held (callers
    catch this to return a tick_skipped_lock_held result).
    """
    resolved_dir = lock_dir if lock_dir is not None else _default_lock_dir()
    resolved_dir.mkdir(parents=True, exist_ok=True)
    lock_path = resolved_dir / _LOCK_FILENAME
    # Open (or create) the lock file
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        acquired = _lock_file_exclusive_nonblocking(fd)
        if not acquired:
            os.close(fd)
            raise _LockHeld("tick lock already held by another process/thread")
        try:
            yield
        finally:
            _unlock_file(fd)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Digest helpers (no network, pure stdlib hashlib)
# ---------------------------------------------------------------------------

def _sha256_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _build_evidence_digest(
    *,
    now: datetime,
    lease_state: LeaseState,
    fired_job_ids: tuple[str, ...],
    skipped_job_ids: tuple[str, ...],
    status: TickStatus,
) -> str:
    return _sha256_digest(
        {
            "nowUtcIso": now.astimezone(UTC).isoformat(),
            "leaseState": lease_state,
            "firedJobIds": sorted(fired_job_ids),
            "skippedJobIds": sorted(skipped_job_ids),
            "status": status,
            "schemaVersion": "scheduler_executor.evidence.v1",
        }
    )


# ---------------------------------------------------------------------------
# Lease validation
# ---------------------------------------------------------------------------

def _validate_lease(
    lease: SchedulerLease | None,
    *,
    now: datetime,
    owner_digest: str,
) -> LeaseState:
    """Return the lease validation state without raising."""
    if lease is None:
        return "missing"
    now_ms = int(now.astimezone(UTC).timestamp() * 1000)
    if lease.owner_digest != owner_digest:
        return "owner_mismatch"
    if now_ms >= lease.expires_at:
        return "stale"
    return "valid"


# ---------------------------------------------------------------------------
# Core tick function
# ---------------------------------------------------------------------------

def tick(
    *,
    now: datetime,
    source: ScheduledJobSource,
    lease: SchedulerLease | None,
    lock_dir: Path | None = None,
    owner_digest: str = "owner:test-abc",
) -> SchedulerTickResult:
    """Execute one scheduler tick with at-most-once semantics.

    Steps (all inside the file lock):
    1. Acquire lock (non-blocking) — return tick_skipped_lock_held if busy.
    2. Validate lease — return tick_blocked_lease on any failure.
    3. Find due jobs (next_run <= now).
    4. For each due job: call record_advance THEN record the local_fake receipt.
    5. Release lock.
    6. Return frozen SchedulerTickResult with evidence digest.

    No agents are spawned.  All authority flags are False.
    """
    try:
        with acquire_tick_lock(lock_dir=lock_dir):
            return _tick_inside_lock(
                now=now,
                source=source,
                lease=lease,
                owner_digest=owner_digest,
            )
    except _LockHeld:
        evidence = _build_evidence_digest(
            now=now,
            lease_state="missing",
            fired_job_ids=(),
            skipped_job_ids=(),
            status="tick_skipped_lock_held",
        )
        return SchedulerTickResult(
            status="tick_skipped_lock_held",
            firedJobIds=(),
            skippedJobIds=(),
            leaseState="missing",
            evidenceDigest=evidence,
            authorityFlags=SchedulerExecutorAuthorityFlags(),
        )


def _tick_inside_lock(
    *,
    now: datetime,
    source: ScheduledJobSource,
    lease: SchedulerLease | None,
    owner_digest: str,
) -> SchedulerTickResult:
    """Core tick logic, called while the file lock is held."""
    lease_state = _validate_lease(lease, now=now, owner_digest=owner_digest)

    if lease_state != "valid":
        evidence = _build_evidence_digest(
            now=now,
            lease_state=lease_state,
            fired_job_ids=(),
            skipped_job_ids=(),
            status="tick_blocked_lease",
        )
        return SchedulerTickResult(
            status="tick_blocked_lease",
            firedJobIds=(),
            skippedJobIds=(),
            leaseState=lease_state,
            evidenceDigest=evidence,
            authorityFlags=SchedulerExecutorAuthorityFlags(),
        )

    # Find all jobs
    all_jobs = list(source.due_jobs(now))
    due = [j for j in all_jobs if j.next_run <= now]

    # Also find all jobs that are NOT due (to populate skipped_job_ids)
    # Re-query: get the full job list by calling due_jobs with far-future now
    # We track skipped as those NOT in the due set from source
    # Build a comprehensive list: any job that source knows about that's not due
    # We only know about jobs from due_jobs; skipped = jobs returned by a "list-all"
    # call. Since the Protocol only exposes due_jobs, we'll track skipped
    # as jobs that WERE known but not fired.
    # For InMemoryJobSource, the non-due jobs are those not returned by due_jobs(now).
    # We compute them by calling due_jobs with a far-future time to get ALL jobs.
    far_future = datetime(9999, 12, 31, tzinfo=UTC)
    all_known_jobs = list(source.due_jobs(far_future))
    skipped_ids = tuple(
        j.job_id for j in all_known_jobs if j.job_id not in {d.job_id for d in due}
    )

    fired_ids: list[str] = []
    local_fake_receipts: list[dict[str, object]] = []

    for job in due:
        # 1. Compute new next_run
        new_next_run = job.compute_next_run(now=now)
        if new_next_run is None:
            # 'once' schedule has no future run — advance past far future to not re-fire
            new_next_run = far_future

        # 2. Advance BEFORE recording receipt (at-most-once guarantee)
        source.record_advance(job.job_id, new_next_run)

        # 3. Record local_fake execution-intent receipt
        receipt = {
            "schemaVersion": "scheduler_executor.intent_receipt.local_fake.v1",
            "jobId": job.job_id,
            "scheduleExpr": job.schedule_expr,
            "firedAtUtcIso": now.astimezone(UTC).isoformat(),
            "newNextRunUtcIso": new_next_run.astimezone(UTC).isoformat(),
            "executionAllowed": False,
            "agentSpawned": False,
            "localFake": True,
        }
        local_fake_receipts.append(receipt)
        fired_ids.append(job.job_id)

    fired_tuple = tuple(fired_ids)
    evidence = _build_evidence_digest(
        now=now,
        lease_state=lease_state,
        fired_job_ids=fired_tuple,
        skipped_job_ids=skipped_ids,
        status="tick_completed",
    )

    return SchedulerTickResult(
        status="tick_completed",
        firedJobIds=fired_tuple,
        skippedJobIds=skipped_ids,
        leaseState=lease_state,
        evidenceDigest=evidence,
        authorityFlags=SchedulerExecutorAuthorityFlags(),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "InMemoryJobSource",
    "LeaseState",
    "ScheduledJobRecord",
    "ScheduledJobSource",
    "SchedulerExecutorAuthorityFlags",
    "SchedulerLease",
    "SchedulerTickResult",
    "TickStatus",
    "acquire_tick_lock",
    "tick",
]
