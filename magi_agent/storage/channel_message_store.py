"""Durable per-channel message log for local-serve multi-window history sync.

Stores user and assistant messages in migration-8 ``channel_messages`` table
inside the shared ``.openmagi/sessions.db`` (WAL). The server-assigned
monotonic ``seq`` is the polling cursor: callers pass ``after_seq`` to fetch
only rows added since their last poll, so the server is the single source of
truth and all open windows converge without clobbering each other.

Design ref: docs/plans/2026-07-13-local-serve-multi-window-history-sync-design.md
Unit: U1 (backend store only).  No transport imports; storage layer.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .migrations import run_migrations
from .session_store import DEFAULT_SESSION_DB_PATH

logger = logging.getLogger(__name__)

# Process-level registry: resolved db-path string -> ChannelMessageStore
# Populated lazily by channel_message_store_for().
_STORE_REGISTRY: dict[str, "ChannelMessageStore"] = {}


class ChannelMessageStore:
    """Append-only SQLite store for per-channel message history.

    Uses the same ``.openmagi/sessions.db`` file as ``SessionSqliteStore``.
    Two concurrent WAL readers/writers from one process are safe (SQLite WAL
    design allows multiple readers + one writer; busy_timeout=5000ms handles
    the rare single-writer lock).
    """

    def __init__(
        self,
        workspace_root: str | Path = "",
        db_path: str = DEFAULT_SESSION_DB_PATH,
    ) -> None:
        self.workspace_root = workspace_root
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        # Serialize all DB ops on the single shared connection. In the runtime,
        # appends run via asyncio.to_thread (a threadpool), so two windows'
        # turns can call into this store concurrently on the SAME connection.
        # sqlite3's serialized mode prevents corruption but can still raise
        # OperationalError ("database is locked" / "Recursive use of cursors")
        # under contention; a process-local lock makes concurrent writes
        # deterministically safe. Each op is sub-millisecond for a single-user
        # local process, so the serialization cost is negligible.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def db_full_path(self) -> Path:
        root = Path(self.workspace_root) if self.workspace_root else Path.cwd()
        return root / self.db_path

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn

        db_full = self.db_full_path
        db_full.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(db_full), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")

        applied = run_migrations(conn)
        if applied > 0:
            logger.info("Applied %d channel-message store migration(s)", applied)

        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Sync methods
    # ------------------------------------------------------------------

    def append_message_sync(
        self,
        *,
        message_id: str,
        session_id: str,
        role: str,
        content: str,
        app_name: str = "",
        channel: str = "",
        turn_id: str | None = None,
        created_at_ms: int | None = None,
        incomplete: bool = False,
        terminal: str | None = None,
    ) -> int | None:
        """INSERT OR IGNORE; return new seq, or None when deduped.

        ``None`` means the ``(session_id, message_id)`` pair was already
        present (idempotent: the caller can ignore the return value safely).
        """
        ts = created_at_ms if created_at_ms is not None else int(time.time() * 1000)
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO channel_messages
                    (message_id, app_name, session_id, channel, role, content,
                     turn_id, created_at, incomplete, terminal)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    app_name,
                    session_id,
                    channel,
                    role,
                    content,
                    turn_id,
                    ts,
                    1 if incomplete else 0,
                    terminal,
                ),
            )
            conn.commit()
            if cursor.lastrowid and cursor.rowcount > 0:
                return cursor.lastrowid
            return None

    def list_messages_sync(
        self,
        *,
        session_id: str,
        app_name: str = "",
        after_seq: int | None = None,
        limit: int | None = None,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return rows ordered by seq ASC.

        ``after_seq`` is exclusive: only rows with ``seq > after_seq``.
        ``limit`` takes the TAIL (highest seqs) so "latest N" is one query:

            SELECT * FROM (SELECT ... ORDER BY seq DESC LIMIT ?) ORDER BY seq ASC

        When both ``after_seq`` and ``limit`` are given, ``after_seq`` is
        applied first (in the inner query) and then the tail cap is applied.

        ``channel`` and ``session_id`` are alternative primary scopes:
        - When ``channel`` is provided, filter by ``channel = ?`` (cross-session
          scope) instead of ``session_id = ?``.  This makes the initial full-load
          return ALL reset sessions for the same channel so prior-session messages
          remain visible after a Reset.
        - When ``channel`` is None (default), filter by ``session_id = ?`` as
          before -- byte-identical behaviour for all existing callers.
        ``app_name`` and ``after_seq`` compose with whichever scope is active.
        """
        # Build WHERE clause.  channel (when provided) is the cross-session
        # scope; session_id is the reset-aware per-session scope.  app_name is
        # only constrained when the caller passes a truthy value.
        if channel is not None:
            clauses: list[str] = ["channel = ?"]
            params: list[Any] = [channel]
        else:
            clauses = ["session_id = ?"]
            params = [session_id]
        if app_name:
            clauses.append("app_name = ?")
            params.append(app_name)
        if after_seq is not None:
            clauses.append("seq > ?")
            params.append(after_seq)
        where = " AND ".join(clauses)

        if limit is not None:
            # TAIL: inner desc-limit subquery, outer asc for caller ordering
            sql = (
                f"SELECT * FROM "
                f"(SELECT * FROM channel_messages WHERE {where} "
                f"ORDER BY seq DESC LIMIT ?) "
                f"ORDER BY seq ASC"
            )
            exec_params: tuple[Any, ...] = (*params, int(limit))
        else:
            sql = f"SELECT * FROM channel_messages WHERE {where} ORDER BY seq ASC"
            exec_params = tuple(params)

        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(sql, exec_params).fetchall()

        return [
            {
                "seq": r["seq"],
                "message_id": r["message_id"],
                "app_name": r["app_name"],
                "session_id": r["session_id"],
                "channel": r["channel"],
                "role": r["role"],
                "content": r["content"],
                "turn_id": r["turn_id"],
                "created_at": r["created_at"],
                "incomplete": bool(r["incomplete"]),
                "terminal": r["terminal"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Async wrappers (asyncio.to_thread; no aiosqlite dependency)
    # ------------------------------------------------------------------

    async def append_message(
        self,
        *,
        message_id: str,
        session_id: str,
        role: str,
        content: str,
        app_name: str = "",
        channel: str = "",
        turn_id: str | None = None,
        created_at_ms: int | None = None,
        incomplete: bool = False,
        terminal: str | None = None,
    ) -> int | None:
        return await asyncio.to_thread(
            self.append_message_sync,
            message_id=message_id,
            session_id=session_id,
            role=role,
            content=content,
            app_name=app_name,
            channel=channel,
            turn_id=turn_id,
            created_at_ms=created_at_ms,
            incomplete=incomplete,
            terminal=terminal,
        )

    async def list_messages(
        self,
        *,
        session_id: str,
        app_name: str = "",
        after_seq: int | None = None,
        limit: int | None = None,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.list_messages_sync,
            session_id=session_id,
            app_name=app_name,
            after_seq=after_seq,
            limit=limit,
            channel=channel,
        )


# ---------------------------------------------------------------------------
# Process-level accessor
# ---------------------------------------------------------------------------


def channel_message_store_for(
    workspace_root: str | Path,
) -> "ChannelMessageStore | None":
    """Lazy singleton per resolved db path.

    Returns None when:
    - ``MAGI_LOCAL_CHANNEL_HISTORY_ENABLED`` flag is OFF, OR
    - Store initialisation fails (fail-soft: logs a warning, does not raise).

    Both the turn handler and the GET endpoint go through this accessor so
    they share one SQLite connection.  The flag check lives here (one gate;
    both callers inherit it).
    """
    from magi_agent.config.flags import flag_profile_bool  # noqa: PLC0415

    if not flag_profile_bool("MAGI_LOCAL_CHANNEL_HISTORY_ENABLED"):
        return None

    key = str(Path(workspace_root).resolve()) if workspace_root else str(Path.cwd())
    if key in _STORE_REGISTRY:
        return _STORE_REGISTRY[key]

    try:
        store = ChannelMessageStore(workspace_root=workspace_root)
        # Force connection + migration eagerly so failures surface here.
        store._get_conn()  # noqa: SLF001
        _STORE_REGISTRY[key] = store
        return store
    except Exception:
        logger.warning(
            "Failed to initialise ChannelMessageStore for %s, "
            "channel history disabled for this process",
            key,
            exc_info=True,
        )
        return None


def _reset_channel_message_store_singletons_for_tests() -> None:
    """Clear the process-level registry.  Test-only helper."""
    _STORE_REGISTRY.clear()
