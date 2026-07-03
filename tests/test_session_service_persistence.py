from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from magi_agent.adk_bridge.session_service import (
    WorkspaceSessionService,
)
from magi_agent.storage.session_store import (
    SessionSqliteStore,
    SessionStoreConfig,
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


@pytest.fixture()
def service(store: SessionSqliteStore) -> WorkspaceSessionService:
    return WorkspaceSessionService(app_name="test_app", store=store)


@pytest.fixture()
def memory_only_service() -> WorkspaceSessionService:
    return WorkspaceSessionService(app_name="test_app")


class TestCreateSessionPersistence:
    def test_create_persists_to_store(
        self, service: WorkspaceSessionService, store: SessionSqliteStore
    ) -> None:
        async def _run() -> None:
            session = await service.create_session(
                app_name="test_app", user_id="u1", state={"k": "v"}
            )
            row = store.load_sync("test_app", "u1", session.id)
            assert row is not None
            assert row["state"]["k"] == "v"

        asyncio.run(_run())

    def test_create_without_store(self, memory_only_service: WorkspaceSessionService) -> None:
        async def _run() -> None:
            session = await memory_only_service.create_session(
                app_name="test_app", user_id="u1"
            )
            assert session is not None
            assert session.id

        asyncio.run(_run())


class TestGetSessionFallback:
    def test_get_from_memory(self, service: WorkspaceSessionService) -> None:
        async def _run() -> None:
            created = await service.create_session(
                app_name="test_app", user_id="u1", session_id="s1"
            )
            got = await service.get_session(
                app_name="test_app", user_id="u1", session_id="s1"
            )
            assert got is created

        asyncio.run(_run())

    def test_get_falls_back_to_store(
        self, service: WorkspaceSessionService, store: SessionSqliteStore
    ) -> None:
        async def _run() -> None:
            await service.create_session(
                app_name="test_app", user_id="u1", session_id="s1", state={"from": "store"}
            )
            service._sessions.clear()

            got = await service.get_session(
                app_name="test_app", user_id="u1", session_id="s1"
            )
            assert got is not None
            assert got.state.get("from") == "store"

        asyncio.run(_run())

    def test_get_returns_none_when_missing(
        self, service: WorkspaceSessionService
    ) -> None:
        async def _run() -> None:
            got = await service.get_session(
                app_name="test_app", user_id="u1", session_id="missing"
            )
            assert got is None

        asyncio.run(_run())


class TestRestoreSessions:
    def test_restore_from_sqlite(
        self, service: WorkspaceSessionService, store: SessionSqliteStore
    ) -> None:
        async def _run() -> None:
            await service.create_session(
                app_name="test_app", user_id="u1", session_id="s1", state={"a": 1}
            )
            await service.create_session(
                app_name="test_app", user_id="u1", session_id="s2", state={"b": 2}
            )
            service._sessions.clear()
            assert len(service._sessions) == 0

            restored = await service.restore_sessions()
            assert restored == 2
            assert len(service._sessions) == 2

        asyncio.run(_run())

    def test_restore_skips_existing(
        self, service: WorkspaceSessionService, store: SessionSqliteStore
    ) -> None:
        async def _run() -> None:
            await service.create_session(
                app_name="test_app", user_id="u1", session_id="s1"
            )
            restored = await service.restore_sessions()
            assert restored == 0

        asyncio.run(_run())

    def test_restore_without_store(
        self, memory_only_service: WorkspaceSessionService
    ) -> None:
        async def _run() -> None:
            restored = await memory_only_service.restore_sessions()
            assert restored == 0

        asyncio.run(_run())


class TestFailOpen:
    def test_create_continues_on_store_error(self, tmp_workspace: Path) -> None:
        config = SessionStoreConfig(enabled=True)
        store = SessionSqliteStore(config=config, workspace_root=str(tmp_workspace))
        service = WorkspaceSessionService(app_name="test_app", store=store)

        original_save = store.save_sync
        store.save_sync = MagicMock(side_effect=RuntimeError("disk full"))

        async def _run() -> None:
            session = await service.create_session(
                app_name="test_app", user_id="u1", state={"ok": True}
            )
            assert session is not None
            assert session.state.get("ok") is True

        asyncio.run(_run())
        store.close()

    def test_get_continues_on_store_error(self, tmp_workspace: Path) -> None:
        config = SessionStoreConfig(enabled=True)
        store = SessionSqliteStore(config=config, workspace_root=str(tmp_workspace))
        service = WorkspaceSessionService(app_name="test_app", store=store)

        store.load_sync = MagicMock(side_effect=RuntimeError("io error"))

        async def _run() -> None:
            got = await service.get_session(
                app_name="test_app", user_id="u1", session_id="nope"
            )
            assert got is None

        asyncio.run(_run())
        store.close()

    def test_restore_continues_on_store_error(self, tmp_workspace: Path) -> None:
        config = SessionStoreConfig(enabled=True)
        store = SessionSqliteStore(config=config, workspace_root=str(tmp_workspace))
        service = WorkspaceSessionService(app_name="test_app", store=store)

        store.list_sync = MagicMock(side_effect=RuntimeError("corrupt db"))

        async def _run() -> None:
            restored = await service.restore_sessions()
            assert restored == 0

        asyncio.run(_run())
        store.close()


class TestCreateWithPersistence:
    def test_enabled_via_env(self, tmp_workspace: Path) -> None:
        with patch.dict(os.environ, {"MAGI_SESSION_PERSISTENCE_ENABLED": "true"}):
            service = WorkspaceSessionService.create_with_persistence(
                app_name="test_app",
                workspace_root=str(tmp_workspace),
            )
            assert service._store is not None
            assert service._store.config.db_path == ".openmagi/sessions.db"
            assert "opencode" not in service._store.config.db_path.lower()

    def test_disabled_when_flag_off(self) -> None:
        with patch.dict(os.environ, {"MAGI_SESSION_PERSISTENCE_ENABLED": "0"}, clear=True):
            service = WorkspaceSessionService.create_with_persistence(
                app_name="test_app",
            )
            assert service._store is None

    def test_fallback_on_init_error(self) -> None:
        with patch.dict(os.environ, {"MAGI_SESSION_PERSISTENCE_ENABLED": "1"}):
            with patch(
                "magi_agent.adk_bridge.session_service.SessionSqliteStore",
                side_effect=RuntimeError("boom"),
            ):
                service = WorkspaceSessionService.create_with_persistence(
                    app_name="test_app",
                )
                assert service._store is None


class TestRestartSimulation:
    def test_full_restart_cycle(self, tmp_workspace: Path) -> None:
        async def _run() -> None:
            config = SessionStoreConfig(enabled=True)
            store1 = SessionSqliteStore(config=config, workspace_root=str(tmp_workspace))
            svc1 = WorkspaceSessionService(app_name="app", store=store1)

            await svc1.create_session(
                app_name="app", user_id="u1", session_id="sid1", state={"phase": "before"}
            )
            store1.close()

            store2 = SessionSqliteStore(config=config, workspace_root=str(tmp_workspace))
            svc2 = WorkspaceSessionService(app_name="app", store=store2)
            restored = await svc2.restore_sessions()
            assert restored == 1

            session = await svc2.get_session(
                app_name="app", user_id="u1", session_id="sid1"
            )
            assert session is not None
            assert session.state.get("phase") == "before"
            store2.close()

        asyncio.run(_run())
