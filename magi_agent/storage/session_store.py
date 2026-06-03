from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .migrations import run_migrations

logger = logging.getLogger(__name__)

DEFAULT_SESSION_DB_PATH = ".openmagi/sessions.db"


@dataclass(frozen=True)
class SessionStoreConfig:
    enabled: bool = False
    db_path: str = DEFAULT_SESSION_DB_PATH


@dataclass(frozen=True)
class SessionUsage:
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0
    cost_usd: float = 0.0
    turn_count: int = 0


class SessionSqliteStore:
    def __init__(
        self,
        config: SessionStoreConfig,
        workspace_root: str | Path = "",
    ) -> None:
        self.config = config
        self.workspace_root = workspace_root
        self._conn: sqlite3.Connection | None = None

    @property
    def db_full_path(self) -> Path:
        root = Path(self.workspace_root) if self.workspace_root else Path.cwd()
        return root / self.config.db_path

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn

        db_path = self.db_full_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(db_path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")

        applied = run_migrations(conn)
        if applied > 0:
            logger.info("Applied %d session store migration(s)", applied)

        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def save_sync(
        self,
        session_id: str,
        app_name: str,
        user_id: str,
        state: dict[str, Any],
    ) -> None:
        conn = self._get_conn()
        now = self._now_iso()
        conn.execute(
            """
            INSERT INTO sessions (id, app_name, user_id, state_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (session_id, app_name, user_id, json.dumps(state), now, now),
        )
        conn.commit()

    def load_sync(
        self, app_name: str, user_id: str, session_id: str
    ) -> dict[str, Any] | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ? AND app_name = ? AND user_id = ?",
            (session_id, app_name, user_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "app_name": row["app_name"],
            "user_id": row["user_id"],
            "state": json.loads(row["state_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_sync(
        self,
        app_name: str,
        user_id: str | None = None,
        *,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """List persisted sessions for an app (optionally scoped to a user).

        Default behavior is unchanged: all matching rows ordered by
        ``updated_at DESC``.  Two OPTIONAL, backward-compatible refinements are
        available for watermark-incremental readers (e.g. learning reflection):

        * ``since`` — when set, only rows with ``updated_at > since`` are
          returned, applied at the SQL layer (``WHERE updated_at > ?``) so the
          whole table is never loaded into Python just to be filtered out.
        * ``limit`` — when set, caps the number of rows returned
          (``LIMIT ?``).  When ``since`` is supplied the ordering switches to
          ``updated_at ASC`` so the cap keeps the OLDEST-after-watermark rows
          (the next incremental batch) rather than the newest.
        """
        conn = self._get_conn()
        clauses = ["app_name = ?"]
        params: list[Any] = [app_name]
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if since is not None:
            clauses.append("updated_at > ?")
            params.append(since)
        order = "ASC" if since is not None else "DESC"
        sql = (
            "SELECT * FROM sessions WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY updated_at {order}"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [
            {
                "id": r["id"],
                "app_name": r["app_name"],
                "user_id": r["user_id"],
                "state": json.loads(r["state_json"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def delete_sync(self, session_id: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cursor.rowcount > 0

    def update_metadata_sync(
        self,
        session_id: str,
        *,
        agent_name: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        tokens_cache_read: int = 0,
        tokens_cache_write: int = 0,
        cost_usd: float = 0.0,
        increment_turn: bool = False,
    ) -> None:
        conn = self._get_conn()
        now = self._now_iso()
        turn_inc = 1 if increment_turn else 0
        conn.execute(
            """
            INSERT INTO session_metadata
                (session_id, agent_name, model, provider,
                 tokens_in, tokens_out, tokens_cache_read, tokens_cache_write,
                 cost_usd, turn_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                agent_name = COALESCE(excluded.agent_name, session_metadata.agent_name),
                model = COALESCE(excluded.model, session_metadata.model),
                provider = COALESCE(excluded.provider, session_metadata.provider),
                tokens_in = session_metadata.tokens_in + excluded.tokens_in,
                tokens_out = session_metadata.tokens_out + excluded.tokens_out,
                tokens_cache_read = session_metadata.tokens_cache_read + excluded.tokens_cache_read,
                tokens_cache_write = session_metadata.tokens_cache_write + excluded.tokens_cache_write,
                cost_usd = session_metadata.cost_usd + excluded.cost_usd,
                turn_count = session_metadata.turn_count + ?,
                updated_at = excluded.updated_at
            """,
            (
                session_id, agent_name, model, provider,
                tokens_in, tokens_out, tokens_cache_read, tokens_cache_write,
                cost_usd, turn_inc, now,
                turn_inc,
            ),
        )
        conn.commit()

    def get_usage_sync(self, session_id: str) -> SessionUsage | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM session_metadata WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return SessionUsage(
            tokens_in=row["tokens_in"],
            tokens_out=row["tokens_out"],
            tokens_cache_read=row["tokens_cache_read"],
            tokens_cache_write=row["tokens_cache_write"],
            cost_usd=row["cost_usd"],
            turn_count=row["turn_count"],
        )

    def db_size_bytes(self) -> int:
        try:
            return self.db_full_path.stat().st_size
        except OSError:
            return 0

    # Async wrappers using asyncio.to_thread (no aiosqlite dependency)

    async def save(
        self, session_id: str, app_name: str, user_id: str, state: dict[str, Any]
    ) -> None:
        await asyncio.to_thread(self.save_sync, session_id, app_name, user_id, state)

    async def load(
        self, app_name: str, user_id: str, session_id: str
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.load_sync, app_name, user_id, session_id)

    async def list(
        self,
        app_name: str,
        user_id: str | None = None,
        *,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            lambda: self.list_sync(app_name, user_id, since=since, limit=limit)
        )

    async def delete(self, session_id: str) -> bool:
        return await asyncio.to_thread(self.delete_sync, session_id)

    async def update_metadata(self, session_id: str, **kwargs: Any) -> None:
        await asyncio.to_thread(self.update_metadata_sync, session_id, **kwargs)

    async def get_usage(self, session_id: str) -> SessionUsage | None:
        return await asyncio.to_thread(self.get_usage_sync, session_id)
