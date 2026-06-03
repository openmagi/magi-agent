"""Learning KB — store protocol and SQLite implementation.

Design constraints:
- No public API may write status="active" directly.
- Activation only through approve() or auto_activate(), both gated by
  policy.assert_activation_allowed().
- Uses stdlib sqlite3, matching the SessionSqliteStore patterns.
- Sync methods only (async wrappers are out of scope for PR1).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from magi_agent.learning.models import LearningItem, LearningKind, LearningScope, LearningStatus
from magi_agent.learning.policy import assert_activation_allowed

logger = logging.getLogger(__name__)

DEFAULT_LEARNING_DB_PATH = ".openmagi/learning.db"

_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS learning_items (
            id          TEXT NOT NULL,
            tenant_id   TEXT NOT NULL DEFAULT 'local',
            kind        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'proposed',
            scope_json  TEXT NOT NULL DEFAULT '{}',
            content_json TEXT NOT NULL DEFAULT '{}',
            rationale   TEXT NOT NULL DEFAULT '',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            version     INTEGER NOT NULL DEFAULT 1,
            supersedes  TEXT,
            embedding_ref TEXT,
            stats_json  TEXT NOT NULL DEFAULT '{}',
            eval_observation_ref TEXT,
            approval_ref TEXT,
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            PRIMARY KEY (id)
        );
        CREATE INDEX IF NOT EXISTS idx_learning_items_tenant_kind_status
            ON learning_items (tenant_id, kind, status);
        CREATE INDEX IF NOT EXISTS idx_learning_items_tenant_status
            ON learning_items (tenant_id, status);
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS learning_eval_observations (
            ref         TEXT PRIMARY KEY,
            item_id     TEXT NOT NULL,
            before_json TEXT NOT NULL DEFAULT '{}',
            after_json  TEXT NOT NULL DEFAULT '{}',
            sample_n    INTEGER NOT NULL DEFAULT 0,
            passed      INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_eval_obs_item_id
            ON learning_eval_observations (item_id);
        """,
    ),
    (
        3,
        """
        CREATE TABLE IF NOT EXISTS learning_approvals (
            ref         TEXT PRIMARY KEY,
            item_id     TEXT NOT NULL,
            approver    TEXT NOT NULL,
            eval_observation_ref TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_approvals_item_id
            ON learning_approvals (item_id);
        """,
    ),
]


def _run_migrations(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _learning_schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    row = conn.execute(
        "SELECT MAX(version) FROM _learning_schema_version"
    ).fetchone()
    current = row[0] if row[0] is not None else 0

    applied = 0
    for version, sql in _MIGRATIONS:
        if version <= current:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO _learning_schema_version (version) VALUES (?)", (version,)
        )
        applied += 1

    conn.commit()
    return applied


@dataclass(frozen=True)
class Page:
    """Paginated result from LearningStore.list()."""

    items: tuple[LearningItem, ...]
    next_cursor: str | None = None


@runtime_checkable
class LearningStore(Protocol):
    """Protocol for learning KB stores."""

    def propose(self, item: LearningItem) -> LearningItem: ...
    def get(self, item_id: str) -> LearningItem | None: ...
    def list(
        self,
        *,
        tenant_id: str,
        kind: LearningKind | None = None,
        status: LearningStatus | None = None,
        scope: LearningScope | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page: ...
    def retrieve(
        self,
        *,
        tenant_id: str,
        scope: LearningScope,
        kinds: tuple[LearningKind, ...] = ("rule", "example"),
        k: int = 8,
    ) -> tuple[LearningItem, ...]: ...
    def record_eval_observation(
        self,
        *,
        item_id: str,
        before: dict[str, object],
        after: dict[str, object],
        sample_n: int,
        passed: bool,
    ) -> str: ...
    def approve(
        self,
        item_id: str,
        *,
        approver: str,
        eval_observation_ref: str | None,
    ) -> LearningItem: ...
    def auto_activate(
        self,
        item_id: str,
        *,
        eval_observation_ref: str | None,
    ) -> LearningItem: ...
    def edit(
        self,
        item_id: str,
        *,
        patch: dict[str, object],
        editor: str,
    ) -> LearningItem: ...
    def archive(self, item_id: str, *, actor: str) -> LearningItem: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row_to_item(row: sqlite3.Row) -> LearningItem:
    payload: dict[str, object] = {
        "id": row["id"],
        "tenantId": row["tenant_id"],
        "kind": row["kind"],
        "status": row["status"],
        "scope": json.loads(row["scope_json"]),
        "content": json.loads(row["content_json"]),
        "rationale": row["rationale"],
        "provenance": json.loads(row["provenance_json"]),
        "version": row["version"],
        "supersedes": row["supersedes"],
        "embeddingRef": row["embedding_ref"],
        "stats": json.loads(row["stats_json"]),
        "evalObservationRef": row["eval_observation_ref"],
        "approvalRef": row["approval_ref"],
    }
    return LearningItem.model_validate(payload)


def _item_to_row_dict(item: LearningItem) -> dict[str, object]:
    return {
        "id": item.id,
        "tenant_id": item.tenant_id,
        "kind": item.kind,
        "status": item.status,
        "scope_json": json.dumps(item.scope.model_dump(by_alias=True)),
        "content_json": json.dumps(dict(item.content)),
        "rationale": item.rationale,
        "provenance_json": json.dumps(item.provenance.model_dump(by_alias=True)),
        "version": item.version,
        "supersedes": item.supersedes,
        "embedding_ref": item.embedding_ref,
        "stats_json": json.dumps(item.stats.model_dump(by_alias=True)),
        "eval_observation_ref": item.eval_observation_ref,
        "approval_ref": item.approval_ref,
        "updated_at": _now_iso(),
    }


class SqliteLearningStore:
    """Concrete SQLite-backed LearningStore.

    Thread-safety: sqlite3 connection created with check_same_thread=False
    and WAL journal mode, consistent with SessionSqliteStore patterns.
    """

    def __init__(
        self,
        db_path: str = DEFAULT_LEARNING_DB_PATH,
        workspace_root: str | Path = "",
    ) -> None:
        self._db_path = db_path
        self._workspace_root = workspace_root
        self._conn: sqlite3.Connection | None = None

    @property
    def db_full_path(self) -> Path:
        root = Path(self._workspace_root) if self._workspace_root else Path.cwd()
        return root / self._db_path

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

        applied = _run_migrations(conn)
        if applied > 0:
            logger.info("Applied %d learning store migration(s)", applied)

        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def propose(self, item: LearningItem) -> LearningItem:
        """Store *item* as proposed, stripping any eval/approval refs."""
        conn = self._get_conn()

        # Rebuild with forced status and stripped refs
        safe = LearningItem.model_validate(
            item.model_dump(by_alias=True)
            | {
                "status": "proposed",
                "evalObservationRef": None,
                "approvalRef": None,
            }
        )

        row = _item_to_row_dict(safe)
        conn.execute(
            """
            INSERT INTO learning_items (
                id, tenant_id, kind, status, scope_json, content_json,
                rationale, provenance_json, version, supersedes, embedding_ref,
                stats_json, eval_observation_ref, approval_ref, updated_at
            ) VALUES (
                :id, :tenant_id, :kind, :status, :scope_json, :content_json,
                :rationale, :provenance_json, :version, :supersedes, :embedding_ref,
                :stats_json, :eval_observation_ref, :approval_ref, :updated_at
            )
            ON CONFLICT(id) DO UPDATE SET
                tenant_id = excluded.tenant_id,
                kind       = excluded.kind,
                status     = excluded.status,
                scope_json = excluded.scope_json,
                content_json = excluded.content_json,
                rationale  = excluded.rationale,
                provenance_json = excluded.provenance_json,
                version    = excluded.version,
                supersedes = excluded.supersedes,
                embedding_ref = excluded.embedding_ref,
                stats_json = excluded.stats_json,
                eval_observation_ref = excluded.eval_observation_ref,
                approval_ref = excluded.approval_ref,
                updated_at = excluded.updated_at
            """,
            row,
        )
        conn.commit()
        return safe

    def get(self, item_id: str) -> LearningItem | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM learning_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_item(row)

    def list(
        self,
        *,
        tenant_id: str,
        kind: LearningKind | None = None,
        status: LearningStatus | None = None,
        scope: LearningScope | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page:
        conn = self._get_conn()

        clauses = ["tenant_id = ?"]
        params: list[object] = [tenant_id]

        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)

        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        if cursor is not None:
            clauses.append("id > ?")
            params.append(cursor)

        where = " AND ".join(clauses)
        params.append(limit + 1)

        rows = conn.execute(
            f"SELECT * FROM learning_items WHERE {where} ORDER BY id LIMIT ?",
            params,
        ).fetchall()

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = tuple(_row_to_item(r) for r in page_rows)
        next_cursor = page_rows[-1]["id"] if has_more else None

        # Optional scope filter (in-memory, since scope is a JSON blob)
        if scope is not None:
            items = tuple(
                i for i in items if i.scope.task_kind == scope.task_kind
            )

        return Page(items=items, next_cursor=next_cursor)

    def retrieve(
        self,
        *,
        tenant_id: str,
        scope: LearningScope,
        kinds: tuple[LearningKind, ...] = ("rule", "example"),
        k: int = 8,
    ) -> tuple[LearningItem, ...]:
        """Return active items matching *scope* and *kinds*, up to *k* items.

        Scope matching is exact on task_kind only (channel/tags filtering
        can be layered in a later PR once vector ranking is wired).
        """
        conn = self._get_conn()

        placeholders = ",".join("?" * len(kinds))
        rows = conn.execute(
            f"""
            SELECT * FROM learning_items
            WHERE tenant_id = ?
              AND status = 'active'
              AND kind IN ({placeholders})
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (tenant_id, *kinds, k * 4),  # over-fetch to allow scope filtering
        ).fetchall()

        items = tuple(
            _row_to_item(r)
            for r in rows
            if json.loads(r["scope_json"]).get("taskKind") == scope.task_kind
        )
        return items[:k]

    def record_eval_observation(
        self,
        *,
        item_id: str,
        before: dict[str, object],
        after: dict[str, object],
        sample_n: int,
        passed: bool,
    ) -> str:
        """Persist an eval observation and return its ref string."""
        conn = self._get_conn()
        ref = f"eval-obs:{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO learning_eval_observations
                (ref, item_id, before_json, after_json, sample_n, passed)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ref,
                item_id,
                json.dumps(before),
                json.dumps(after),
                sample_n,
                int(passed),
            ),
        )
        conn.commit()
        return ref

    def approve(
        self,
        item_id: str,
        *,
        approver: str,
        eval_observation_ref: str | None,
    ) -> LearningItem:
        """Activate a proposed rule item after human approval.

        Enforces both policy invariants:
        - eval_observation_ref must be present
        - approval_ref is generated from the approver record
        """
        conn = self._get_conn()

        item = self._require_item(conn, item_id)

        # Generate an approval_ref to satisfy assert_activation_allowed
        approval_ref = f"approval:{uuid.uuid4().hex}"

        assert_activation_allowed(
            item,
            eval_observation_ref=eval_observation_ref,
            approval_ref=approval_ref,
        )

        conn.execute(
            """
            INSERT INTO learning_approvals
                (ref, item_id, approver, eval_observation_ref)
            VALUES (?, ?, ?, ?)
            """,
            (approval_ref, item_id, approver, eval_observation_ref or ""),
        )

        conn.execute(
            """
            UPDATE learning_items
            SET status = 'active',
                eval_observation_ref = ?,
                approval_ref = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (eval_observation_ref, approval_ref, _now_iso(), item_id),
        )
        conn.commit()

        return self._require_item(conn, item_id)

    def auto_activate(
        self,
        item_id: str,
        *,
        eval_observation_ref: str | None,
    ) -> LearningItem:
        """Activate a proposed non-rule item automatically via eval gate.

        Enforces policy:self-improvement.eval-observation-required@1.
        For rule items, also enforces no-direct-mutation (raises PolicyViolation).
        """
        conn = self._get_conn()
        item = self._require_item(conn, item_id)

        # For rules, auto_activate has no approval_ref -> policy violation
        assert_activation_allowed(
            item,
            eval_observation_ref=eval_observation_ref,
            approval_ref=None,
        )

        conn.execute(
            """
            UPDATE learning_items
            SET status = 'active',
                eval_observation_ref = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (eval_observation_ref, _now_iso(), item_id),
        )
        conn.commit()

        return self._require_item(conn, item_id)

    def edit(
        self,
        item_id: str,
        *,
        patch: dict[str, object],
        editor: str,
    ) -> LearningItem:
        """Create a new version with *patch* applied.

        The original item is preserved; the new version has
        version+1 and supersedes pointing to the original id.
        """
        conn = self._get_conn()
        original = self._require_item(conn, item_id)

        new_id = f"{item_id}:v{original.version + 1}"
        new_data = original.model_dump(by_alias=True) | patch | {
            "id": new_id,
            "version": original.version + 1,
            "supersedes": item_id,
            # New version starts as proposed again
            "status": "proposed",
            "evalObservationRef": None,
            "approvalRef": None,
        }
        new_item = LearningItem.model_validate(new_data)

        row = _item_to_row_dict(new_item)
        conn.execute(
            """
            INSERT INTO learning_items (
                id, tenant_id, kind, status, scope_json, content_json,
                rationale, provenance_json, version, supersedes, embedding_ref,
                stats_json, eval_observation_ref, approval_ref, updated_at
            ) VALUES (
                :id, :tenant_id, :kind, :status, :scope_json, :content_json,
                :rationale, :provenance_json, :version, :supersedes, :embedding_ref,
                :stats_json, :eval_observation_ref, :approval_ref, :updated_at
            )
            """,
            row,
        )
        conn.commit()
        return new_item

    def archive(self, item_id: str, *, actor: str) -> LearningItem:
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE learning_items
            SET status = 'archived', updated_at = ?
            WHERE id = ?
            """,
            (_now_iso(), item_id),
        )
        conn.commit()
        return self._require_item(conn, item_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_item(self, conn: sqlite3.Connection, item_id: str) -> LearningItem:
        row = conn.execute(
            "SELECT * FROM learning_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"LearningItem not found: {item_id!r}")
        return _row_to_item(row)


__all__ = [
    "DEFAULT_LEARNING_DB_PATH",
    "LearningStore",
    "Page",
    "SqliteLearningStore",
]
