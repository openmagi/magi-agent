# magi_agent/missions/work_queue/store.py
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

from magi_agent.missions.work_queue.models import WorkTask

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


# ---------------------------------------------------------------------------
# WorkQueueStore Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class WorkQueueStore(Protocol):
    """Minimal seam for durable work-queue CRUD.

    Concrete implementations may be backed by SQLite (SqliteWorkQueueStore)
    or an in-memory dict (InMemoryWorkQueueStore) for tests.

    Forbidden imports: google.adk, socket, subprocess, urllib, requests, http
    (verified by test_work_queue_import_boundary.py).
    """

    def create(self, task: WorkTask) -> WorkTask:
        """Persist a new task; return it unchanged."""
        ...

    def get(self, task_id: str) -> WorkTask | None:
        """Return the task for *task_id*, or None if not found."""
        ...

    def link(self, parent_id: str, child_id: str) -> None:
        """Record a DAG dependency: *child_id* must not run until *parent_id* is done."""
        ...

    def recompute_ready(self) -> int:
        """Promote all ``todo`` tasks whose parents are done to ``ready``.

        Returns the number of tasks promoted.
        """
        ...

    def claim(
        self,
        task_id: str,
        *,
        claimer: str,
        ttl: int = CLAIM_TTL_SECONDS,
        now: int | None = None,
        worker_pid: int | None = None,
    ) -> WorkTask | None:
        """Atomically claim *task_id* for *claimer*.

        Returns the updated task on success, or None if the task is already
        claimed or its parents are not yet done (CAS loser).
        """
        ...

    def heartbeat(
        self,
        task_id: str,
        *,
        claimer: str,
        now: int | None = None,
        ttl: int = CLAIM_TTL_SECONDS,
    ) -> bool:
        """Extend the claim TTL.  Returns True if the heartbeat was recorded."""
        ...

    def release_stale_claims(
        self,
        *,
        now: int | None = None,
        pid_alive: object = None,
    ) -> int:
        """Release expired claims whose workers are no longer alive.

        Returns the number of tasks reclaimed to ``ready``.
        """
        ...

    def find_by_idempotency_key(self, key: str) -> WorkTask | None:
        """Return the task whose ``idempotency_key`` matches *key*, or None."""
        ...

    def record_failure(
        self,
        task_id: str,
        *,
        outcome: str,
        error: str | None = None,
        failure_limit: int = DEFAULT_FAILURE_LIMIT,
    ) -> WorkTask:
        """Increment ``consecutive_failures``; transition to ``blocked`` at *failure_limit*."""
        ...

    def complete(self, task_id: str, *, result: str | None = None) -> WorkTask:
        """Mark *task_id* completed and store *result*."""
        ...


# ---------------------------------------------------------------------------
# InMemoryWorkQueueStore
# ---------------------------------------------------------------------------


class InMemoryWorkQueueStore:
    """Deterministic dict-backed work-queue store for tests and local-fake mode.

    Mirrors InMemoryGoalStateStore in magi_agent/harness/goal_state.py.
    Implements WorkQueueStore Protocol with the same observable behaviour as
    SqliteWorkQueueStore: atomic single-winner claim (CAS), DAG parent-gating
    in recompute_ready, circuit-breaker record_failure, idempotency lookup.
    """

    work_queue_store_kind = "local_fake"

    def __init__(self) -> None:
        self._tasks: dict[str, WorkTask] = {}
        # parent_id -> set of child_ids
        self._links: dict[str, set[str]] = {}
        # child_id -> set of parent_ids (reverse index for fast lookup)
        self._parents: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update(self, task_id: str, **fields: object) -> WorkTask:
        """Return a new frozen WorkTask with *fields* updated and store it."""
        current = self._tasks[task_id]
        updated = current.model_copy(update=fields)
        self._tasks[task_id] = updated
        return updated

    def _parents_done(self, task_id: str) -> bool:
        """Return True if all parents of *task_id* are in DONE_STATES."""
        from magi_agent.missions.work_queue.models import DONE_STATES

        parent_ids = self._parents.get(task_id, set())
        for pid in parent_ids:
            parent = self._tasks.get(pid)
            if parent is None or parent.status not in DONE_STATES:
                return False
        return True

    # ------------------------------------------------------------------
    # Public protocol methods
    # ------------------------------------------------------------------

    def create(self, task: WorkTask) -> WorkTask:
        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> WorkTask | None:
        return self._tasks.get(task_id)

    def link(self, parent_id: str, child_id: str) -> None:
        self._links.setdefault(parent_id, set()).add(child_id)
        self._parents.setdefault(child_id, set()).add(parent_id)

    def _set_status(self, task_id: str, status: str) -> None:  # test/helper seam
        self._update(task_id, status=status)

    def recompute_ready(self) -> int:
        promoted = 0
        for task_id, task in list(self._tasks.items()):
            if task.status != "todo":
                continue
            if self._parents_done(task_id):
                self._update(task_id, status="ready")
                promoted += 1
        return promoted

    def claim(
        self,
        task_id: str,
        *,
        claimer: str,
        ttl: int = CLAIM_TTL_SECONDS,
        now: int | None = None,
        worker_pid: int | None = None,
    ) -> WorkTask | None:
        now = int(time.time()) if now is None else now
        task = self._tasks.get(task_id)
        if task is None:
            return None
        # Parent-gate: demote to todo if parents are not done
        if not self._parents_done(task_id):
            if task.status == "ready":
                self._update(task_id, status="todo")
            return None
        # CAS: only claim if status='ready' and claim_lock is None
        if task.status != "ready" or task.claim_lock is not None:
            return None
        return self._update(
            task_id,
            status="running",
            claim_lock=claimer,
            claim_expires=now + ttl,
            worker_pid=worker_pid,
            last_heartbeat_at=now,
            started_at=task.started_at if task.started_at is not None else now,
        )

    def heartbeat(
        self,
        task_id: str,
        *,
        claimer: str,
        now: int | None = None,
        ttl: int = CLAIM_TTL_SECONDS,
    ) -> bool:
        now = int(time.time()) if now is None else now
        task = self._tasks.get(task_id)
        if task is None or task.status != "running" or task.claim_lock != claimer:
            return False
        self._update(task_id, claim_expires=now + ttl, last_heartbeat_at=now)
        return True

    def release_stale_claims(
        self,
        *,
        now: int | None = None,
        pid_alive: object = None,
    ) -> int:
        import os

        now = int(time.time()) if now is None else now
        if pid_alive is None:
            def pid_alive(pid: int) -> bool:  # type: ignore[misc]
                try:
                    os.kill(pid, 0)
                    return True
                except (OSError, TypeError):
                    return False
        reclaimed = 0
        for task_id, task in list(self._tasks.items()):
            if task.status != "running":
                continue
            if task.claim_expires is None or task.claim_expires >= now:
                continue
            hb = task.last_heartbeat_at
            hb_stale = hb is not None and (now - int(hb)) > CLAIM_HEARTBEAT_MAX_STALE_SECONDS
            alive = (
                task.worker_pid is not None
                and pid_alive(task.worker_pid)  # type: ignore[operator]
                and not hb_stale
            )
            if alive:
                self._update(task_id, claim_expires=now + CLAIM_TTL_SECONDS)
                continue
            self._update(
                task_id,
                status="ready",
                claim_lock=None,
                claim_expires=None,
                worker_pid=None,
                current_run_id=None,
            )
            reclaimed += 1
        return reclaimed

    def find_by_idempotency_key(self, key: str) -> WorkTask | None:
        for task in self._tasks.values():
            if task.idempotency_key == key:
                return task
        return None

    def record_failure(
        self,
        task_id: str,
        *,
        outcome: str,
        error: str | None = None,
        failure_limit: int = DEFAULT_FAILURE_LIMIT,
    ) -> WorkTask:
        task = self._tasks[task_id]
        new_failures = task.consecutive_failures + 1
        new_status = "blocked" if new_failures >= failure_limit else "ready"
        return self._update(
            task_id,
            consecutive_failures=new_failures,
            last_failure_error=error,
            status=new_status,
        )

    def complete(self, task_id: str, *, result: str | None = None) -> WorkTask:
        now = int(time.time())
        return self._update(
            task_id,
            status="completed",
            result=result,
            completed_at=now,
            consecutive_failures=0,
        )


# ---------------------------------------------------------------------------
# SqliteWorkQueueStore
# ---------------------------------------------------------------------------


class SqliteWorkQueueStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        from magi_agent.storage.migrations import run_migrations  # lazy: keep store import-clean
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


__all__ = [
    "CLAIM_HEARTBEAT_MAX_STALE_SECONDS",
    "CLAIM_TTL_SECONDS",
    "DEFAULT_FAILURE_LIMIT",
    "InMemoryWorkQueueStore",
    "SqliteWorkQueueStore",
    "WorkQueueStore",
]
