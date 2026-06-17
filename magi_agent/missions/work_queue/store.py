# magi_agent/missions/work_queue/store.py
from __future__ import annotations

import sqlite3
from pathlib import Path

from magi_agent.missions.work_queue.models import WorkTask
from magi_agent.storage.migrations import run_migrations

CLAIM_TTL_SECONDS = 15 * 60
CLAIM_HEARTBEAT_MAX_STALE_SECONDS = 60 * 60
DEFAULT_FAILURE_LIMIT = 2

_COLUMNS = (
    "id",
    "title",
    "body",
    "assignee",
    "status",
    "priority",
    "tenant",
    "session_id",
    "idempotency_key",
    "claim_lock",
    "claim_expires",
    "worker_pid",
    "last_heartbeat_at",
    "current_run_id",
    "consecutive_failures",
    "max_retries",
    "goal_mode",
    "goal_max_turns",
    "result",
    "last_failure_error",
    "created_at",
    "started_at",
    "completed_at",
)


class SqliteWorkQueueStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        conn = sqlite3.connect(str(self._db_path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        run_migrations(conn)
        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> WorkTask:
        d = {k: row[k] for k in _COLUMNS}
        d["goal_mode"] = bool(d["goal_mode"])
        return WorkTask(**d)

    def create(self, task: WorkTask) -> WorkTask:
        conn = self._get_conn()
        vals = task.model_dump()
        vals["goal_mode"] = 1 if vals["goal_mode"] else 0
        placeholders = ",".join("?" for _ in _COLUMNS)
        conn.execute(
            f"INSERT INTO work_queue_tasks ({','.join(_COLUMNS)}) VALUES ({placeholders})",
            tuple(vals[c] for c in _COLUMNS),
        )
        conn.commit()
        return task

    def get(self, task_id: str) -> WorkTask | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM work_queue_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return self._row_to_task(row) if row else None


__all__ = ["SqliteWorkQueueStore"]
