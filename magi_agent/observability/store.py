from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from magi_agent.observability.models import ActivityEvent

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS activity_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            REAL NOT NULL,
  session_id    TEXT,
  run_id        TEXT,
  parent_run_id TEXT,
  kind          TEXT NOT NULL,
  tool_name     TEXT,
  status        TEXT,
  summary       TEXT,
  payload_json  TEXT,
  elapsed_ms    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ae_ts ON activity_events(ts);
CREATE INDEX IF NOT EXISTS idx_ae_session ON activity_events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_ae_kind ON activity_events(kind, ts);
"""


def _row_to_dict(row: sqlite3.Row) -> dict:
    payload = row["payload_json"]
    return {
        "id": row["id"],
        "ts": row["ts"],
        "session_id": row["session_id"],
        "run_id": row["run_id"],
        "parent_run_id": row["parent_run_id"],
        "kind": row["kind"],
        "tool_name": row["tool_name"],
        "status": row["status"],
        "summary": row["summary"],
        "payload": json.loads(payload) if payload else {},
        "elapsed_ms": row["elapsed_ms"],
    }


class ActivityStore:
    """SQLite-backed append-only store. Thread-safe, fail-open at call sites."""

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._closed = False
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(_DDL)
            self._conn.commit()

    def record_event(self, event: ActivityEvent) -> int:
        if self._closed:
            return -1
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO activity_events "
                    "(ts, session_id, run_id, parent_run_id, kind, tool_name, status, summary, payload_json, elapsed_ms) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        event.ts, event.session_id, event.run_id, event.parent_run_id,
                        event.kind, event.tool_name, event.status, event.summary,
                        json.dumps(event.payload, separators=(",", ":"), default=str) if event.payload else None,
                        event.elapsed_ms,
                    ),
                )
                self._conn.commit()
                return int(cur.lastrowid)
        except Exception:
            logger.debug("activity store record_event failed", exc_info=True)
            return -1

    def list_events(
        self,
        *,
        session_id: str | None = None,
        kind: str | None = None,
        exclude_kind: str | None = None,
        status: str | None = None,
        q: str | None = None,
        since_id: int | None = None,
        before_id: int | None = None,
        limit: int = 200,
    ) -> list[dict]:
        if self._closed:
            return []
        clauses: list[str] = []
        args: list[object] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            args.append(session_id)
        if kind is not None:
            tokens = [t.strip() for t in kind.split(",") if t.strip()]
            if len(tokens) > 1:
                placeholders = ",".join("?" * len(tokens))
                clauses.append(f"kind IN ({placeholders})")
                args.extend(tokens)
            else:
                clauses.append("kind = ?")
                args.append(tokens[0] if tokens else kind)
        if exclude_kind is not None:
            tokens = [t.strip() for t in exclude_kind.split(",") if t.strip()]
            if tokens:
                placeholders = ",".join("?" * len(tokens))
                clauses.append(f"kind NOT IN ({placeholders})")
                args.extend(tokens)
        if status is not None:
            tokens = [t.strip() for t in status.split(",") if t.strip()]
            if tokens:
                placeholders = ",".join("?" * len(tokens))
                clauses.append(f"status IN ({placeholders})")
                args.extend(tokens)
        if q is not None:
            clauses.append("summary LIKE ?")
            args.append(f"%{q}%")
        if since_id is not None:
            clauses.append("id > ?")
            args.append(since_id)
        if before_id is not None:
            clauses.append("id < ?")
            args.append(before_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, min(int(limit), 1000))
        sql = f"SELECT * FROM activity_events{where} ORDER BY id ASC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [_row_to_dict(r) for r in rows]

    def count_events(self) -> int:
        if self._closed:
            return 0
        try:
            with self._lock:
                return int(self._conn.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0])
        except Exception:
            logger.debug("activity store count_events failed", exc_info=True)
            return 0

    def prune(self, *, max_events: int | None = None, retention_days: int | None = None) -> int:
        if self._closed:
            return 0
        removed = 0
        try:
            with self._lock:
                if retention_days is not None:
                    cutoff = time.time() - retention_days * 86400
                    cur = self._conn.execute("DELETE FROM activity_events WHERE ts < ?", (cutoff,))
                    removed += cur.rowcount or 0
                if max_events is not None:
                    cur = self._conn.execute(
                        "DELETE FROM activity_events WHERE id NOT IN "
                        "(SELECT id FROM activity_events ORDER BY id DESC LIMIT ?)",
                        (max_events,),
                    )
                    removed += cur.rowcount or 0
                self._conn.commit()
        except Exception:
            logger.debug("activity store prune failed", exc_info=True)
        return removed

    def list_sessions(self, *, limit: int = 100) -> list[dict]:
        if self._closed:
            return []
        limit = max(1, min(int(limit), 1000))
        sql = (
            "SELECT session_id AS id, COUNT(*) AS event_count, "
            "MIN(ts) AS started_at, MAX(ts) AS last_active, "
            "SUM(CASE WHEN kind='tool_start' THEN 1 ELSE 0 END) AS tool_count "
            "FROM activity_events WHERE session_id IS NOT NULL "
            "GROUP BY session_id ORDER BY last_active DESC LIMIT ?"
        )
        try:
            with self._lock:
                rows = self._conn.execute(sql, (limit,)).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.debug("activity store list_sessions failed", exc_info=True)
            return []

    def latest_event_with_kind_like(self, needle: str) -> dict | None:
        if self._closed:
            return None
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT * FROM activity_events WHERE kind LIKE ? ORDER BY id DESC LIMIT 1",
                    (f"%{needle}%",),
                ).fetchone()
            return _row_to_dict(row) if row else None
        except Exception:
            logger.debug("activity store latest_event_with_kind_like failed", exc_info=True)
            return None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()
