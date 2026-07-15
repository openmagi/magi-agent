from __future__ import annotations

import sqlite3

import pytest

from magi_agent.execution_authority.migrations import (
    UnsupportedAuthoritySchema,
    migrate_authority_database,
)


def test_v1_migration_creates_the_authority_schema_in_wal_mode(tmp_path) -> None:
    path = tmp_path / "execution-authority.db"

    migrate_authority_database(path)

    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        version = connection.execute("SELECT version, checksum FROM _schema_version").fetchone()
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()

    assert {
        "_schema_version",
        "authority_task_contracts",
        "authority_partitions",
        "authority_events",
        "authority_heads",
        "authority_actions",
        "authority_action_attempts",
        "authority_contract_uses",
        "authority_projection_cursors",
        "authority_epoch_required_projections",
        "authority_epochs",
        "authority_leases",
        "authority_user_decisions",
        "authority_user_decision_receipts",
        "authority_workspaces",
        "authority_workspace_commits",
        "authority_completion_verdicts",
        "authority_outbox",
    } <= names
    assert version is not None and version[0] == 1 and len(version[1]) == 64
    assert journal_mode == ("wal",)


def test_migration_is_idempotent_and_refuses_an_unknown_version(tmp_path) -> None:
    path = tmp_path / "execution-authority.db"
    assert migrate_authority_database(path) == 1
    assert migrate_authority_database(path) == 1

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM _schema_version").fetchone() == (1,)
        connection.execute("DELETE FROM _schema_version")
        connection.execute(
            "INSERT INTO _schema_version(version, checksum, applied_at) VALUES (2, 'x', 'now')"
        )
        connection.commit()

    with pytest.raises(UnsupportedAuthoritySchema, match="version 2"):
        migrate_authority_database(path)
