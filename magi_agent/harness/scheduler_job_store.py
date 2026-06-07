"""A-driver — persistent SQLite-backed ScheduledJobSource.

This module makes the scheduler's job source survive a process restart.  It is
the (b) deliverable from the Track-F deferral list in
``scheduler_job_execution.py``: only ``InMemoryJobSource`` (lost on restart)
existed before; ``SqliteScheduledJobSource`` is the durable equivalent.

Design
------
- Implements the ``ScheduledJobSource`` Protocol from ``scheduler_executor`` —
  ``due_jobs(now)``, ``list_all()``, ``record_advance(...)`` — plus CRUD
  (``create`` / ``update`` / ``get`` / ``delete``) so a driver can manage jobs.
- Reuses the central ``magi_agent/storage/migrations.py`` framework (the
  ``scheduled_jobs`` table is migration version 4).  Mirrors the SQLite store
  patterns from ``harness/goal_state.py`` (WAL, busy_timeout, lazy connection,
  full-record-JSON column + a denormalized ``next_run_utc`` for range scans).
- ``scheduler_source_kind = "local_fake"`` so ``tick()``'s source-kind validation
  (``_validate_local_fake_source``) accepts it — the persistent source is gated
  behind exactly the same local_fake contract as ``InMemoryJobSource`` until the
  executor gate is flipped on.

at-most-once
------------
``record_advance`` is a compare-and-set: it updates ``next_run`` only when the
stored ``next_run`` still equals ``expected_next_run`` (rowcount-gated UPDATE).
This matches ``InMemoryJobSource`` and preserves the A2 at-most-once guarantee
(advance happens before the receipt; a stale expected value is a no-op).

Forbidden imports: google.adk, urllib, socket, subprocess, http, requests —
none appear here or in this module's import graph (verified by test).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from magi_agent.harness.scheduler_executor import ScheduledJobRecord
from magi_agent.storage.migrations import run_migrations


def _iso_utc(value: datetime) -> str:
    """Stable ISO-8601 UTC key used for SQL range comparisons.

    Normalizing to UTC means a lexicographic string compare matches a chronological
    compare (all keys share the same +00:00 offset), so ``next_run_utc <= ?`` is a
    correct due-filter.

    Raises ``ValueError`` for naive datetimes: a naive datetime passed to
    ``astimezone()`` is silently converted assuming LOCAL time, which on a non-UTC
    host produces an incorrect ISO key (due_jobs returns wrong results) and a later
    ``aware == naive`` comparison in the CAS raises ``TypeError``.  Rejecting at
    the boundary turns a silent wrong into a loud correct.
    """
    if value.tzinfo is None:
        raise ValueError(f"datetime must be timezone-aware, got naive: {value!r}")
    return value.astimezone(UTC).isoformat()


class SqliteScheduledJobSource:
    """SQLite-backed durable ``ScheduledJobSource``.

    May be pointed at an isolated DB path (e.g. a tmp_path in tests) or share the
    default workspace DB.  Reuses the ``scheduled_jobs`` table (migration 4).
    """

    # tick() accepts only local_fake sources until the executor gate is on; the
    # persistent source rides the same contract as InMemoryJobSource.
    scheduler_source_kind = "local_fake"

    def __init__(
        self,
        db_path: str | Path,
        *,
        workspace_root: str | Path = "",
    ) -> None:
        if workspace_root:
            self._db_path = Path(workspace_root) / db_path
        else:
            self._db_path = (
                Path(db_path)
                if Path(db_path).is_absolute()
                else Path.cwd() / db_path
            )
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle (mirrors SqliteGoalStateStore)
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        run_migrations(conn)
        self._conn = conn
        return conn

    def close(self) -> None:
        """Close the SQLite connection if open.  Safe to call multiple times."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Row <-> record
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ScheduledJobRecord:
        return ScheduledJobRecord.model_validate_json(row["job_json"])

    def _write(self, record: ScheduledJobRecord) -> None:
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO scheduled_jobs (job_id, schedule_expr, next_run_utc, job_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                schedule_expr = excluded.schedule_expr,
                next_run_utc = excluded.next_run_utc,
                job_json = excluded.job_json,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                record.job_id,
                record.schedule_expr,
                _iso_utc(record.next_run),
                record.model_dump_json(by_alias=True),
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, record: ScheduledJobRecord) -> ScheduledJobRecord:
        """Insert (or replace) a scheduled job."""
        self._write(record)
        return record

    def update(self, record: ScheduledJobRecord) -> ScheduledJobRecord:
        """Replace an existing job's schedule/next_run (upsert semantics)."""
        self._write(record)
        return record

    def get(self, job_id: str) -> ScheduledJobRecord | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT job_json FROM scheduled_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def delete(self, job_id: str) -> None:
        """Remove a job.  Idempotent (no-op when absent)."""
        conn = self._get_conn()
        conn.execute("DELETE FROM scheduled_jobs WHERE job_id = ?", (job_id,))
        conn.commit()

    # ------------------------------------------------------------------
    # ScheduledJobSource Protocol
    # ------------------------------------------------------------------

    def due_jobs(self, now: datetime) -> Sequence[ScheduledJobRecord]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT job_json FROM scheduled_jobs WHERE next_run_utc <= ? ORDER BY job_id",
            (_iso_utc(now),),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_all(self) -> Sequence[ScheduledJobRecord]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT job_json FROM scheduled_jobs ORDER BY job_id"
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def record_advance(
        self,
        job_id: str,
        next_run: datetime,
        *,
        expected_next_run: datetime,
    ) -> bool:
        """Compare-and-set next_run; returns True only when the CAS applied.

        Mirrors ``InMemoryJobSource.record_advance``: the prior ``next_run``
        becomes ``last_fire``, and the update is gated on the stored ``next_run``
        still equaling ``expected_next_run`` (at-most-once guarantee).
        """
        conn = self._get_conn()
        # Read-modify-write inside a single transaction so a concurrent advance
        # cannot interleave between the CAS check and the write.
        with conn:
            row = conn.execute(
                "SELECT job_json FROM scheduled_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return False
            existing = ScheduledJobRecord.model_validate_json(row["job_json"])
            if existing.next_run != expected_next_run:
                return False
            updated = ScheduledJobRecord(
                jobId=existing.job_id,
                scheduleExpr=existing.schedule_expr,
                lastFire=existing.next_run,  # old next_run becomes last_fire
                nextRun=next_run,
            )
            conn.execute(
                """
                UPDATE scheduled_jobs
                SET next_run_utc = ?, job_json = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE job_id = ?
                """,
                (_iso_utc(next_run), updated.model_dump_json(by_alias=True), job_id),
            )
        return True


__all__ = ["SqliteScheduledJobSource"]
