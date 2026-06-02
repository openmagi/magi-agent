from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

import pytest

from openmagi_core_agent.storage.migrations import MIGRATIONS, run_migrations
from openmagi_core_agent.storage.session_store import (
    SessionSqliteStore,
    SessionStoreConfig,
    SessionUsage,
)


@pytest.fixture()
def tmp_workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def store(tmp_workspace: Path) -> SessionSqliteStore:
    config = SessionStoreConfig(enabled=True)
    s = SessionSqliteStore(config=config, workspace_root=str(tmp_workspace))
    yield s
    s.close()


# --- Migration tests ---


class TestMigrations:
    def test_run_migrations_creates_tables(self, tmp_workspace: Path) -> None:
        db_path = tmp_workspace / "test.db"
        conn = sqlite3.connect(str(db_path))
        applied = run_migrations(conn)
        assert applied == len(MIGRATIONS)

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "sessions" in tables
        assert "session_metadata" in tables
        assert "_schema_version" in tables
        conn.close()

    def test_run_migrations_idempotent(self, tmp_workspace: Path) -> None:
        db_path = tmp_workspace / "test.db"
        conn = sqlite3.connect(str(db_path))
        first = run_migrations(conn)
        second = run_migrations(conn)
        assert first == len(MIGRATIONS)
        assert second == 0
        conn.close()

    def test_incremental_migration(self, tmp_workspace: Path) -> None:
        db_path = tmp_workspace / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _schema_version "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT '')"
        )
        conn.execute("INSERT INTO _schema_version (version) VALUES (1)")
        conn.executescript(MIGRATIONS[0][1])
        conn.commit()

        applied = run_migrations(conn)
        assert applied == len(MIGRATIONS) - 1
        conn.close()


# --- CRUD tests ---


class TestSessionCRUD:
    def test_save_and_load(self, store: SessionSqliteStore) -> None:
        store.save_sync("s1", "app", "user1", {"key": "value"})
        row = store.load_sync("app", "user1", "s1")
        assert row is not None
        assert row["id"] == "s1"
        assert row["state"] == {"key": "value"}

    def test_load_nonexistent(self, store: SessionSqliteStore) -> None:
        assert store.load_sync("app", "user1", "nope") is None

    def test_save_upsert(self, store: SessionSqliteStore) -> None:
        store.save_sync("s1", "app", "user1", {"v": 1})
        store.save_sync("s1", "app", "user1", {"v": 2})
        row = store.load_sync("app", "user1", "s1")
        assert row is not None
        assert row["state"]["v"] == 2

    def test_list_sessions(self, store: SessionSqliteStore) -> None:
        store.save_sync("s1", "app", "user1", {})
        store.save_sync("s2", "app", "user1", {})
        store.save_sync("s3", "app", "user2", {})

        user1 = store.list_sync("app", "user1")
        assert len(user1) == 2

        all_app = store.list_sync("app")
        assert len(all_app) == 3

        empty = store.list_sync("other_app")
        assert len(empty) == 0

    def test_delete(self, store: SessionSqliteStore) -> None:
        store.save_sync("s1", "app", "user1", {})
        assert store.delete_sync("s1") is True
        assert store.load_sync("app", "user1", "s1") is None
        assert store.delete_sync("s1") is False

    def test_state_roundtrip_complex(self, store: SessionSqliteStore) -> None:
        state = {
            "nested": {"list": [1, 2, 3], "null": None},
            "unicode": "한국어 テスト",
            "bool": True,
        }
        store.save_sync("s1", "app", "user1", state)
        row = store.load_sync("app", "user1", "s1")
        assert row is not None
        assert row["state"] == state


# --- Metadata tests ---


class TestSessionMetadata:
    def test_update_and_get_usage(self, store: SessionSqliteStore) -> None:
        store.save_sync("s1", "app", "user1", {})
        store.update_metadata_sync(
            "s1",
            agent_name="main",
            model="claude-opus-4-6",
            tokens_in=100,
            tokens_out=50,
            cost_usd=0.01,
            increment_turn=True,
        )
        usage = store.get_usage_sync("s1")
        assert usage is not None
        assert usage.tokens_in == 100
        assert usage.tokens_out == 50
        assert usage.turn_count == 1
        assert abs(usage.cost_usd - 0.01) < 1e-9

    def test_metadata_accumulates(self, store: SessionSqliteStore) -> None:
        store.save_sync("s1", "app", "user1", {})
        store.update_metadata_sync("s1", tokens_in=100, tokens_out=50, increment_turn=True)
        store.update_metadata_sync("s1", tokens_in=200, tokens_out=80, increment_turn=True)
        usage = store.get_usage_sync("s1")
        assert usage is not None
        assert usage.tokens_in == 300
        assert usage.tokens_out == 130
        assert usage.turn_count == 2

    def test_get_usage_nonexistent(self, store: SessionSqliteStore) -> None:
        assert store.get_usage_sync("nope") is None

    def test_metadata_model_update(self, store: SessionSqliteStore) -> None:
        store.save_sync("s1", "app", "user1", {})
        store.update_metadata_sync("s1", model="model-a")
        store.update_metadata_sync("s1", model="model-b", tokens_in=10)
        usage = store.get_usage_sync("s1")
        assert usage is not None
        assert usage.tokens_in == 10


# --- Async tests ---


class TestAsyncWrappers:
    def test_async_save_load(self, store: SessionSqliteStore) -> None:
        async def _run() -> None:
            await store.save("s1", "app", "user1", {"async": True})
            row = await store.load("app", "user1", "s1")
            assert row is not None
            assert row["state"]["async"] is True

        asyncio.run(_run())

    def test_async_list_delete(self, store: SessionSqliteStore) -> None:
        async def _run() -> None:
            await store.save("s1", "app", "user1", {})
            await store.save("s2", "app", "user1", {})
            rows = await store.list("app", "user1")
            assert len(rows) == 2
            deleted = await store.delete("s1")
            assert deleted is True
            rows = await store.list("app", "user1")
            assert len(rows) == 1

        asyncio.run(_run())

    def test_async_metadata(self, store: SessionSqliteStore) -> None:
        async def _run() -> None:
            await store.save("s1", "app", "user1", {})
            await store.update_metadata(
                "s1", tokens_in=50, tokens_out=25, increment_turn=True
            )
            usage = await store.get_usage("s1")
            assert usage is not None
            assert usage.tokens_in == 50
            assert usage.turn_count == 1

        asyncio.run(_run())


# --- DB path and size ---


class TestDBPath:
    def test_default_db_path_is_openmagi_neutral(self) -> None:
        config = SessionStoreConfig(enabled=True)
        assert config.db_path == ".openmagi/sessions.db"
        assert "opencode" not in config.db_path.lower()

    def test_db_full_path(self, tmp_workspace: Path) -> None:
        config = SessionStoreConfig(enabled=True, db_path="custom/db.sqlite3")
        s = SessionSqliteStore(config=config, workspace_root=str(tmp_workspace))
        assert s.db_full_path == tmp_workspace / "custom" / "db.sqlite3"

    def test_db_size_bytes(self, store: SessionSqliteStore) -> None:
        store.save_sync("s1", "app", "user1", {})
        size = store.db_size_bytes()
        assert size > 0

    def test_db_size_nonexistent(self, tmp_workspace: Path) -> None:
        config = SessionStoreConfig(enabled=True, db_path="nope/db.sqlite3")
        s = SessionSqliteStore(config=config, workspace_root=str(tmp_workspace))
        assert s.db_size_bytes() == 0


# --- WAL mode ---


class TestWALMode:
    def test_wal_enabled(self, store: SessionSqliteStore) -> None:
        conn = store._get_conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
