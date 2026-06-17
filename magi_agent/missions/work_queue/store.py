# magi_agent/missions/work_queue/store.py
from __future__ import annotations

import json
import sqlite3
import time
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

    def link(self, parent_id: str, child_id: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO work_queue_task_links (parent_id, child_id) VALUES (?,?)",
            (parent_id, child_id),
        )
        conn.commit()

    def _set_status(self, task_id: str, status: str) -> None:  # test/helper seam
        conn = self._get_conn()
        conn.execute("UPDATE work_queue_tasks SET status=? WHERE id=?", (status, task_id))
        conn.commit()

    def recompute_ready(self) -> int:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id FROM work_queue_tasks WHERE status='todo'"
        ).fetchall()
        promoted = 0
        for r in rows:
            undone = conn.execute(
                "SELECT 1 FROM work_queue_task_links l "
                "JOIN work_queue_tasks p ON p.id = l.parent_id "
                "WHERE l.child_id = ? AND p.status NOT IN ('completed','archived') LIMIT 1",
                (r["id"],),
            ).fetchone()
            if undone:
                continue
            cur = conn.execute(
                "UPDATE work_queue_tasks SET status='ready' WHERE id=? AND status='todo'",
                (r["id"],),
            )
            if cur.rowcount == 1:
                self._append_event(conn, r["id"], "promoted", None)
                promoted += 1
        conn.commit()
        return promoted

    def claim(self, task_id, *, claimer, ttl=CLAIM_TTL_SECONDS, now=None, worker_pid=None):
        now = int(time.time()) if now is None else now
        conn = self._get_conn()
        # Parent-gate (mirror Hermes): never run while a parent is undone.
        # NOTE: The parent-gate SELECT and the CAS UPDATE below are NOT wrapped in a single
        # BEGIN IMMEDIATE transaction. This is safe under the intended single-dispatcher-writer
        # model (one dispatcher per board), but if multi-process writers are introduced, wrap
        # both in an EXCLUSIVE/IMMEDIATE transaction to close the parent-status TOCTOU window.
        undone = conn.execute(
            "SELECT 1 FROM work_queue_task_links l "
            "JOIN work_queue_tasks p ON p.id = l.parent_id "
            "WHERE l.child_id = ? AND p.status NOT IN ('completed','archived') LIMIT 1",
            (task_id,),
        ).fetchone()
        if undone:
            conn.execute(
                "UPDATE work_queue_tasks SET status='todo' WHERE id=? AND status='ready'",
                (task_id,),
            )
            self._append_event(conn, task_id, "claim_rejected", {"reason": "parents_not_done"})
            conn.commit()
            return None
        cur = conn.execute(
            "UPDATE work_queue_tasks "
            "SET status='running', claim_lock=?, claim_expires=?, worker_pid=?, "
            "    last_heartbeat_at=?, started_at=COALESCE(started_at, ?) "
            "WHERE id=? AND status='ready' AND claim_lock IS NULL",
            (claimer, now + ttl, worker_pid, now, now, task_id),
        )
        if cur.rowcount != 1:
            conn.commit()
            return None
        run_cur = conn.execute(
            "INSERT INTO work_queue_task_runs "
            "(task_id, status, claim_lock, claim_expires, worker_pid, last_heartbeat_at, started_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (task_id, "running", claimer, now + ttl, worker_pid, now, now),
        )
        conn.execute(
            "UPDATE work_queue_tasks SET current_run_id=? WHERE id=?",
            (run_cur.lastrowid, task_id),
        )
        self._append_event(conn, task_id, "claimed", {"claimer": claimer})
        conn.commit()
        return self.get(task_id)

    def heartbeat(self, task_id, *, claimer, now=None, ttl=CLAIM_TTL_SECONDS) -> bool:
        now = int(time.time()) if now is None else now
        conn = self._get_conn()
        cur = conn.execute(
            "UPDATE work_queue_tasks SET claim_expires=?, last_heartbeat_at=? "
            "WHERE id=? AND status='running' AND claim_lock=?",
            (now + ttl, now, task_id, claimer),
        )
        conn.commit()
        return cur.rowcount == 1

    def release_stale_claims(self, *, now=None, pid_alive=None) -> int:
        import os

        now = int(time.time()) if now is None else now
        if pid_alive is None:
            def pid_alive(pid):
                try:
                    os.kill(pid, 0)
                    return True
                except (OSError, TypeError):
                    return False
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, claim_lock, worker_pid, last_heartbeat_at "
            "FROM work_queue_tasks WHERE status='running' "
            "AND claim_expires IS NOT NULL AND claim_expires < ?",
            (now,),
        ).fetchall()
        reclaimed = 0
        for r in rows:
            hb = r["last_heartbeat_at"]
            hb_stale = hb is not None and (now - int(hb)) > CLAIM_HEARTBEAT_MAX_STALE_SECONDS
            alive = r["worker_pid"] is not None and pid_alive(r["worker_pid"]) and not hb_stale
            if alive:
                conn.execute(
                    "UPDATE work_queue_tasks SET claim_expires=? WHERE id=? AND status='running'",
                    (now + CLAIM_TTL_SECONDS, r["id"]),
                )
                self._append_event(conn, r["id"], "claim_extended", None)
                continue
            conn.execute(
                "UPDATE work_queue_task_runs SET status='released', outcome='reclaimed', ended_at=? "
                "WHERE task_id=? AND ended_at IS NULL",
                (now, r["id"]),
            )
            conn.execute(
                "UPDATE work_queue_tasks "
                "SET status='ready', claim_lock=NULL, claim_expires=NULL, worker_pid=NULL, current_run_id=NULL "
                "WHERE id=? AND status='running'",
                (r["id"],),
            )
            self._append_event(conn, r["id"], "reclaimed", None)
            reclaimed += 1
        conn.commit()
        return reclaimed

    def find_by_idempotency_key(self, key: str) -> WorkTask | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM work_queue_tasks WHERE idempotency_key = ? LIMIT 1",
            (key,),
        ).fetchone()
        return self._row_to_task(row) if row else None

    def record_failure(
        self,
        task_id: str,
        *,
        outcome: str,
        error: str | None = None,
        failure_limit: int = DEFAULT_FAILURE_LIMIT,
    ) -> WorkTask:
        conn = self._get_conn()
        conn.execute(
            "UPDATE work_queue_tasks "
            "SET consecutive_failures = consecutive_failures + 1, last_failure_error = ? "
            "WHERE id = ?",
            (error, task_id),
        )
        row = conn.execute(
            "SELECT consecutive_failures FROM work_queue_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        new_failures = row["consecutive_failures"]
        if new_failures >= failure_limit:
            conn.execute(
                "UPDATE work_queue_tasks SET status = 'blocked' WHERE id = ?",
                (task_id,),
            )
            self._append_event(conn, task_id, "blocked", {"outcome": outcome})
        else:
            conn.execute(
                "UPDATE work_queue_tasks SET status = 'ready' WHERE id = ?",
                (task_id,),
            )
            self._append_event(conn, task_id, "failed", {"outcome": outcome})
        conn.commit()
        result = self.get(task_id)
        assert result is not None
        return result

    def complete(self, task_id: str, *, result: str | None = None) -> WorkTask:
        now = int(time.time())
        conn = self._get_conn()
        conn.execute(
            "UPDATE work_queue_tasks "
            "SET status = 'completed', result = ?, completed_at = ?, consecutive_failures = 0 "
            "WHERE id = ?",
            (result, now, task_id),
        )
        self._append_event(conn, task_id, "completed", None)
        conn.commit()
        task = self.get(task_id)
        assert task is not None
        return task

    def _append_event(self, conn, task_id, kind, payload):
        conn.execute(
            "INSERT INTO work_queue_task_events (task_id, run_id, kind, payload, created_at) "
            "VALUES (?,?,?,?,?)",
            (task_id, None, kind, json.dumps(payload) if payload else None, int(time.time())),
        )


__all__ = ["SqliteWorkQueueStore"]
