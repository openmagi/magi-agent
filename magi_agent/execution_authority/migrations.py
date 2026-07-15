"""Versioned local SQLite schema for the dormant authority journal.

This module owns only additive, local database setup.  It neither opens a
runtime path by default nor attaches the authority kernel to a live route.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import sqlite3


class UnsupportedAuthoritySchema(RuntimeError):
    """Raised without mutation when a database is newer than this kernel."""


_V1_SCHEMA = """
CREATE TABLE _schema_version (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE authority_task_contracts (
    task_contract_id TEXT NOT NULL,
    task_version INTEGER NOT NULL,
    task_contract_digest TEXT NOT NULL UNIQUE,
    task_contract_snapshot_ref TEXT NOT NULL UNIQUE,
    canonical_snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_contract_id, task_version)
);
CREATE TABLE authority_partitions (
    partition_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('ready', 'recovering', 'quarantined')),
    recovery_owner_id TEXT,
    recovery_fencing_token INTEGER NOT NULL DEFAULT 0,
    quarantine_reason_digest TEXT,
    compare_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE authority_events (
    partition_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    action_id TEXT,
    attempt_id TEXT,
    idempotency_key TEXT NOT NULL,
    task_contract_digest TEXT NOT NULL,
    completion_epoch_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    row_checksum TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (partition_id, sequence),
    FOREIGN KEY (partition_id) REFERENCES authority_partitions(partition_id)
);
CREATE TABLE authority_heads (
    partition_id TEXT PRIMARY KEY REFERENCES authority_partitions(partition_id),
    last_sequence INTEGER NOT NULL,
    last_event_hash TEXT NOT NULL,
    compare_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE authority_actions (
    action_id TEXT PRIMARY KEY,
    partition_id TEXT NOT NULL REFERENCES authority_partitions(partition_id),
    idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    task_contract_digest TEXT NOT NULL,
    completion_epoch_id TEXT NOT NULL,
    admission_sequence INTEGER NOT NULL,
    compare_version INTEGER NOT NULL DEFAULT 0,
    UNIQUE (partition_id, idempotency_key)
);
CREATE TABLE authority_action_attempts (
    action_id TEXT NOT NULL REFERENCES authority_actions(action_id),
    attempt_id TEXT NOT NULL,
    partition_id TEXT NOT NULL REFERENCES authority_partitions(partition_id),
    state TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    authority_contract_id TEXT UNIQUE,
    authority_contract_digest TEXT UNIQUE,
    fencing_token INTEGER,
    compare_version INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (action_id, attempt_id)
);
CREATE TABLE authority_contract_uses (
    authority_contract_id TEXT PRIMARY KEY,
    authority_contract_digest TEXT NOT NULL UNIQUE,
    action_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    partition_id TEXT NOT NULL,
    consumed_event_sequence INTEGER NOT NULL,
    consumed_at TEXT NOT NULL,
    UNIQUE (action_id, attempt_id)
);
CREATE TABLE authority_projection_cursors (
    partition_id TEXT NOT NULL REFERENCES authority_partitions(partition_id),
    projection_id TEXT NOT NULL,
    acknowledged_sequence INTEGER NOT NULL DEFAULT 0,
    acknowledged_event_hash TEXT NOT NULL,
    state_root TEXT,
    compare_version INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (partition_id, projection_id)
);
CREATE TABLE authority_epochs (
    completion_epoch_id TEXT PRIMARY KEY,
    task_contract_id TEXT NOT NULL,
    task_version INTEGER NOT NULL,
    task_contract_digest TEXT NOT NULL,
    task_contract_snapshot_ref TEXT NOT NULL,
    state TEXT NOT NULL,
    last_admission_sequence INTEGER NOT NULL DEFAULT 0,
    compare_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE authority_epoch_required_projections (
    completion_epoch_id TEXT NOT NULL REFERENCES authority_epochs(completion_epoch_id),
    partition_id TEXT NOT NULL,
    projection_id TEXT NOT NULL,
    PRIMARY KEY (completion_epoch_id, partition_id, projection_id)
);
CREATE TABLE authority_leases (
    partition_id TEXT PRIMARY KEY REFERENCES authority_partitions(partition_id),
    owner_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    compare_version INTEGER NOT NULL
);
CREATE TABLE authority_user_decisions (
    decision_request_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    partition_id TEXT NOT NULL,
    task_contract_digest TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    compare_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE authority_user_decision_receipts (
    receipt_id TEXT PRIMARY KEY,
    receipt_digest TEXT NOT NULL UNIQUE,
    decision_request_id TEXT NOT NULL REFERENCES authority_user_decisions(decision_request_id),
    decision TEXT NOT NULL,
    authentication_nonce_digest TEXT NOT NULL UNIQUE,
    canonical_payload_json TEXT NOT NULL,
    recorded_event_sequence INTEGER NOT NULL
);
CREATE TABLE authority_workspaces (
    workspace_id TEXT PRIMARY KEY,
    current_generation INTEGER NOT NULL DEFAULT 0,
    state_root TEXT NOT NULL,
    publication_state TEXT NOT NULL,
    pending_commit_id TEXT UNIQUE,
    pending_generation INTEGER,
    compare_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE authority_workspace_commits (
    commit_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES authority_workspaces(workspace_id),
    action_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    state TEXT NOT NULL,
    decision_fencing_token INTEGER NOT NULL,
    active_fencing_token INTEGER NOT NULL,
    compare_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE authority_completion_verdicts (
    completion_id TEXT PRIMARY KEY,
    completion_epoch_id TEXT NOT NULL UNIQUE REFERENCES authority_epochs(completion_epoch_id),
    task_contract_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    verdict_digest TEXT NOT NULL,
    verdict_json TEXT NOT NULL
);
CREATE TABLE authority_outbox (
    outbox_id TEXT PRIMARY KEY,
    partition_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    delivery_state TEXT NOT NULL,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    delivered_at TEXT
);
""".strip()

_V1_CHECKSUM = sha256(_V1_SCHEMA.encode("utf-8")).hexdigest()


def migrate_authority_database(path: Path, *, busy_timeout_ms: int = 5_000) -> int:
    """Create or validate the authority v1 schema without destructive upgrades."""

    if not isinstance(path, Path):
        raise TypeError("path must be a pathlib.Path")
    if type(busy_timeout_ms) is not int or busy_timeout_ms < 0:
        raise ValueError("busy_timeout_ms must be a non-negative exact integer")
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        connection.execute("PRAGMA synchronous=FULL")
        existing = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = '_schema_version'"
        ).fetchone()
        if existing is None:
            connection.executescript(_V1_SCHEMA)
            connection.execute(
                "INSERT INTO _schema_version(version, checksum, applied_at) VALUES (?, ?, ?)",
                (1, _V1_CHECKSUM, datetime.now(UTC).isoformat()),
            )
            return 1
        rows = connection.execute("SELECT version, checksum FROM _schema_version").fetchall()
        if len(rows) != 1 or rows[0][0] != 1:
            version = rows[0][0] if rows else "none"
            raise UnsupportedAuthoritySchema(f"unsupported authority schema version {version}")
        if rows[0][1] != _V1_CHECKSUM:
            raise UnsupportedAuthoritySchema("authority schema checksum does not match v1")
        return 1


__all__ = ["UnsupportedAuthoritySchema", "migrate_authority_database"]
