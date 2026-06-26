# magi_agent/storage/durable_checkpoint_store.py
"""WS1 PR1a - durable checkpoint + plan-ledger substrate (local sqlite only).

This is the foundational substrate for WS1 durable crash-resume. It co-locates
two new tables - ``durable_checkpoints`` and ``plan_ledger`` - inside the proven
work-queue sqlite file (``work_queue.db``, WAL), reusing the same path resolver
and connection posture as ``SqliteWorkQueueStore``.

Everything here is gated behind the master flag ``MAGI_DURABLE_LOCAL_WRITES_ENABLED``
(default OFF). With the flag OFF the store is a pure no-op: it opens no
connection, creates no table, and writes no file, so behaviour is byte-identical
to today. The tables are created idempotently (``CREATE TABLE IF NOT EXISTS``)
only on first write under the ON master.

Persistence-time safety: a checkpoint's identifier/digest fields are validated
by the frozen ``ExecutionCheckpoint`` schema BEFORE the row is written, so no
secret fragments or malformed digests reach the DB. Only digests, a watermark
uuid, an integer line count, and the working-directory path are persisted - no
message bodies, prompts, or tool outputs.

This module deliberately imports NEITHER ``storage/durable_store.py`` (the
hosted fail-closed contract layer) NOR ``storage/sqlite_store.py`` (the
append-only engine): the hosted ``Literal[False]`` guard is untouched, and the
mutable ``superseded`` / ``resumable`` / ``resume_attempt_count`` columns plus
the "latest resumable" query are incompatible with the append-only triggers
there. See the WS1 design doc, sections 0.6 and 8 (R7), for the rationale.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from magi_agent.config.flags import flag_bool
from magi_agent.missions.work_queue.store import work_queue_db_path_from_env
from magi_agent.runtime.checkpointing import ExecutionCheckpoint


_LOCAL_WRITES_FLAG = "MAGI_DURABLE_LOCAL_WRITES_ENABLED"

# Operational + schema columns of durable_checkpoints, in DDL order. There is
# deliberately NO seq_offset column: the chain index is unsound as a watermark
# (WS1 design correction 4); truncation is by watermark_uuid only.
_DDL_CHECKPOINTS = """
CREATE TABLE IF NOT EXISTS durable_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    watermark_uuid TEXT,
    evidence_line_count INTEGER NOT NULL DEFAULT 0,
    cwd TEXT,
    state_digest TEXT NOT NULL,
    ledger_head_digest TEXT NOT NULL,
    effective_policy_snapshot_digest TEXT NOT NULL,
    context_projection_digest TEXT NOT NULL,
    pending_approval_refs TEXT NOT NULL DEFAULT '[]',
    resumable INTEGER NOT NULL DEFAULT 0,
    resume_attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    superseded INTEGER NOT NULL DEFAULT 0
)
"""

_DDL_PLAN_LEDGER = """
CREATE TABLE IF NOT EXISTS plan_ledger (
    entry_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    op TEXT NOT NULL,
    item_digest TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_DDL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_durable_ckpt_run_turn "
    "ON durable_checkpoints(run_id, turn_id)",
    "CREATE INDEX IF NOT EXISTS idx_plan_ledger_session "
    "ON plan_ledger(session_id)",
)


@dataclass(frozen=True)
class StoredCheckpoint:
    """A persisted checkpoint plus its operational columns.

    The frozen ``ExecutionCheckpoint`` carries the schema-validated identifier
    and digest fields; the operational fields (``turn_id``, ``watermark_uuid``,
    ``evidence_line_count``, ``cwd``, ``resume_attempt_count``, ``superseded``)
    live alongside it because they are NOT part of the frozen schema.
    """

    checkpoint: ExecutionCheckpoint
    turn_id: str
    watermark_uuid: str | None
    evidence_line_count: int
    cwd: str | None
    resume_attempt_count: int
    superseded: bool


class DurableCheckpointStore:
    """SQLite-backed durable checkpoint store, co-located in work_queue.db.

    No-op unless ``MAGI_DURABLE_LOCAL_WRITES_ENABLED`` is truthy. The connection
    is opened lazily on first write/read-under-master so the OFF path never
    touches the filesystem.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._explicit_path = Path(db_path) if db_path is not None else None
        self._conn: sqlite3.Connection | None = None
        self._ddl_applied = False

    # -- gating ------------------------------------------------------------

    @staticmethod
    def _enabled() -> bool:
        return flag_bool(_LOCAL_WRITES_FLAG)

    def _db_path(self) -> Path:
        if self._explicit_path is not None:
            return self._explicit_path
        return work_queue_db_path_from_env()

    # -- connection / DDL --------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        path = self._db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn
        self._ensure_tables(conn)
        return conn

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        if self._ddl_applied:
            return
        conn.execute(_DDL_CHECKPOINTS)
        conn.execute(_DDL_PLAN_LEDGER)
        for index_sql in _DDL_INDEXES:
            conn.execute(index_sql)
        conn.commit()
        self._ddl_applied = True

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._ddl_applied = False

    # -- writes ------------------------------------------------------------

    def put(
        self,
        checkpoint: ExecutionCheckpoint,
        *,
        turn_id: str,
        watermark_uuid: str | None = None,
        evidence_line_count: int = 0,
        cwd: str | None = None,
    ) -> None:
        """Persist a checkpoint, superseding earlier ones for the same turn.

        No-op when the master flag is OFF. The ``ExecutionCheckpoint`` schema
        has already validated its identifier/digest fields by the time the
        instance exists, so the row is safe to write.
        """
        if not self._enabled():
            return
        conn = self._get_conn()
        # Supersede earlier checkpoints for this (run_id, turn_id) first so the
        # latest write is the only non-superseded row.
        conn.execute(
            "UPDATE durable_checkpoints SET superseded=1 "
            "WHERE run_id=? AND turn_id=? AND superseded=0",
            (checkpoint.run_id, turn_id),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO durable_checkpoints (
                checkpoint_id, run_id, turn_id, step_id, workflow_version,
                watermark_uuid, evidence_line_count, cwd,
                state_digest, ledger_head_digest,
                effective_policy_snapshot_digest, context_projection_digest,
                pending_approval_refs, resumable, resume_attempt_count,
                created_at, superseded
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
            """,
            (
                checkpoint.checkpoint_id,
                checkpoint.run_id,
                turn_id,
                checkpoint.step_id,
                checkpoint.workflow_version,
                watermark_uuid,
                int(evidence_line_count),
                cwd,
                checkpoint.state_digest,
                checkpoint.ledger_head_digest,
                checkpoint.effective_policy_snapshot_digest,
                checkpoint.context_projection_digest,
                json.dumps(list(checkpoint.pending_approval_refs)),
                1 if checkpoint.resumable else 0,
                self._existing_attempt_count(conn, checkpoint.run_id, turn_id),
                checkpoint.created_at.isoformat(),
            ),
        )
        conn.commit()

    @staticmethod
    def _existing_attempt_count(
        conn: sqlite3.Connection, run_id: str, turn_id: str
    ) -> int:
        """Carry forward the resume-attempt count across a same-turn supersede.

        A new after-tool checkpoint for a turn already being resumed must not
        reset the bound, otherwise a crash-on-resume loop could never terminate.
        """
        row = conn.execute(
            "SELECT MAX(resume_attempt_count) AS n FROM durable_checkpoints "
            "WHERE run_id=? AND turn_id=?",
            (run_id, turn_id),
        ).fetchone()
        value = row["n"] if row is not None else None
        return int(value) if value is not None else 0

    def increment_resume_attempt(self, run_id: str, turn_id: str) -> int:
        """Bump the resume-attempt counter for the turn's live checkpoint(s).

        Returns the new attempt count. No-op (returns 0) when the master is OFF.
        """
        if not self._enabled():
            return 0
        conn = self._get_conn()
        conn.execute(
            "UPDATE durable_checkpoints SET resume_attempt_count=resume_attempt_count+1 "
            "WHERE run_id=? AND turn_id=?",
            (run_id, turn_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT MAX(resume_attempt_count) AS n FROM durable_checkpoints "
            "WHERE run_id=? AND turn_id=?",
            (run_id, turn_id),
        ).fetchone()
        value = row["n"] if row is not None else None
        return int(value) if value is not None else 0

    def mark_superseded(self, checkpoint_id: str) -> None:
        if not self._enabled():
            return
        conn = self._get_conn()
        conn.execute(
            "UPDATE durable_checkpoints SET superseded=1 WHERE checkpoint_id=?",
            (checkpoint_id,),
        )
        conn.commit()

    # -- reads -------------------------------------------------------------

    def get_latest_resumable(
        self, run_id: str, turn_id: str
    ) -> StoredCheckpoint | None:
        """Return the latest non-superseded, resumable checkpoint for the turn."""
        if not self._enabled():
            return None
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM durable_checkpoints "
            "WHERE run_id=? AND turn_id=? AND superseded=0 AND resumable=1 "
            "ORDER BY created_at DESC, step_id DESC LIMIT 1",
            (run_id, turn_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_stored(row)

    def list_resumable_turns(self) -> list[StoredCheckpoint]:
        """Return every non-superseded, resumable checkpoint (one per live turn)."""
        if not self._enabled():
            return []
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM durable_checkpoints "
            "WHERE superseded=0 AND resumable=1 "
            "ORDER BY created_at DESC, step_id DESC",
        ).fetchall()
        return [self._row_to_stored(row) for row in rows]

    def is_superseded(self, checkpoint_id: str) -> bool | None:
        """Return the superseded flag for a checkpoint, or None if absent/OFF."""
        if not self._enabled():
            return None
        conn = self._get_conn()
        row = conn.execute(
            "SELECT superseded FROM durable_checkpoints WHERE checkpoint_id=?",
            (checkpoint_id,),
        ).fetchone()
        if row is None:
            return None
        return bool(row["superseded"])

    # -- introspection (used by the DDL/contract tests) --------------------

    def checkpoint_columns(self) -> tuple[str, ...]:
        if not self._enabled():
            return ()
        conn = self._get_conn()
        rows = conn.execute("PRAGMA table_info(durable_checkpoints)").fetchall()
        return tuple(row["name"] for row in rows)

    def has_table(self, name: str) -> bool:
        if not self._enabled():
            return False
        conn = self._get_conn()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return row is not None

    def plan_ledger_row_count(self) -> int:
        if not self._enabled():
            return 0
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) AS n FROM plan_ledger").fetchone()
        return int(row["n"]) if row is not None else 0

    # -- row mapping -------------------------------------------------------

    @staticmethod
    def _row_to_stored(row: sqlite3.Row) -> StoredCheckpoint:
        refs_raw = row["pending_approval_refs"]
        refs = tuple(json.loads(refs_raw)) if refs_raw else ()
        checkpoint = ExecutionCheckpoint(
            runId=row["run_id"],
            checkpointId=row["checkpoint_id"],
            stepId=row["step_id"],
            workflowVersion=row["workflow_version"],
            stateDigest=row["state_digest"],
            ledgerHeadDigest=row["ledger_head_digest"],
            effectivePolicySnapshotDigest=row["effective_policy_snapshot_digest"],
            contextProjectionDigest=row["context_projection_digest"],
            pendingApprovalRefs=refs,
            resumable=bool(row["resumable"]),
            createdAt=datetime.fromisoformat(row["created_at"]),
        )
        return StoredCheckpoint(
            checkpoint=checkpoint,
            turn_id=row["turn_id"],
            watermark_uuid=row["watermark_uuid"],
            evidence_line_count=int(row["evidence_line_count"]),
            cwd=row["cwd"],
            resume_attempt_count=int(row["resume_attempt_count"]),
            superseded=bool(row["superseded"]),
        )
