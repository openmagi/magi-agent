from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from collections.abc import Callable

from google.adk.events import Event
from google.adk.sessions import BaseSessionService, Session
from google.adk.sessions.base_session_service import GetSessionConfig, ListSessionsResponse
from pydantic import BaseModel, ConfigDict, Field

from ..storage.session_store import SessionSqliteStore, SessionStoreConfig

logger = logging.getLogger(__name__)


class SessionDeletionRequiresReviewError(RuntimeError):
    """Session deletion is intentionally disabled pending product review."""


class DurableSessionProjection(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    session_ref: str = Field(alias="sessionRef")
    app_name: str = Field(alias="appName")
    user_ref: str = Field(alias="userRef")
    event_count: int = Field(alias="eventCount")
    state_digest: str = Field(alias="stateDigest")
    approved_state_refs: dict[str, object] = Field(default_factory=dict, alias="approvedStateRefs")

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")


class WorkspaceSessionService(BaseSessionService):
    openmagi_local_fake_provider = True

    def __init__(
        self,
        *,
        app_name: str,
        event_sink: Callable[[Event], None] | None = None,
        store: SessionSqliteStore | None = None,
    ) -> None:
        self.app_name = app_name
        self.event_sink = event_sink
        self._sessions: dict[tuple[str, str, str], Session] = {}
        self._store = store

    @classmethod
    def create_with_persistence(
        cls,
        *,
        app_name: str,
        workspace_root: str = "",
        event_sink: Callable[[Event], None] | None = None,
    ) -> WorkspaceSessionService:
        enabled = os.environ.get("MAGI_SESSION_PERSISTENCE_ENABLED", "").lower() in (
            "1", "true", "yes",
        )
        if not enabled:
            return cls(app_name=app_name, event_sink=event_sink)

        try:
            config = SessionStoreConfig(enabled=True)
            store = SessionSqliteStore(config=config, workspace_root=workspace_root)
            instance = cls(app_name=app_name, event_sink=event_sink, store=store)
            return instance
        except Exception:
            logger.warning("Failed to initialize session store, falling back to in-memory", exc_info=True)
            return cls(app_name=app_name, event_sink=event_sink)

    async def restore_sessions(self) -> int:
        if self._store is None:
            return 0
        try:
            rows = await self._store.list(self.app_name)
            restored = 0
            for row in rows:
                key = (row["app_name"], row["user_id"], row["id"])
                if key not in self._sessions:
                    session = Session(
                        id=row["id"],
                        app_name=row["app_name"],
                        user_id=row["user_id"],
                        state=dict(row.get("state") or {}),
                        events=[],
                    )
                    self._sessions[key] = session
                    restored += 1
            if restored > 0:
                logger.info("Restored %d session(s) from SQLite", restored)
            return restored
        except Exception:
            logger.warning("Failed to restore sessions from store", exc_info=True)
            return 0

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, object] | None = None,
        session_id: str | None = None,
    ) -> Session:
        sid = session_id or str(uuid.uuid4())
        key = (app_name, user_id, sid)
        session = self._sessions.get(key)
        if session is None:
            session = Session(
                id=sid,
                app_name=app_name,
                user_id=user_id,
                state=dict(state or {}),
                events=[],
            )
            self._sessions[key] = session
            await self._persist_session(session)
        return session

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: GetSessionConfig | None = None,
    ) -> Session | None:
        _ = config
        session = self._sessions.get((app_name, user_id, session_id))
        if session is not None:
            return session

        if self._store is not None:
            try:
                row = await self._store.load(app_name, user_id, session_id)
                if row is not None:
                    session = Session(
                        id=row["id"],
                        app_name=row["app_name"],
                        user_id=row["user_id"],
                        state=dict(row.get("state") or {}),
                        events=[],
                    )
                    self._sessions[(app_name, user_id, session_id)] = session
                    return session
            except Exception:
                logger.warning("Failed to load session from store", exc_info=True)

        return None

    async def list_sessions(
        self,
        *,
        app_name: str,
        user_id: str | None = None,
    ) -> ListSessionsResponse:
        sessions = [
            session
            for (session_app, session_user, _sid), session in self._sessions.items()
            if session_app == app_name and (user_id is None or session_user == user_id)
        ]
        return ListSessionsResponse(sessions=sessions)

    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        _ = (app_name, user_id, session_id)
        raise SessionDeletionRequiresReviewError("session deletion requires product review")

    async def append_event(self, session: Session, event: Event) -> Event:
        appended = await super().append_event(session, event)
        if self.event_sink is not None and appended is event:
            self.event_sink(event)
        await self._persist_session(session)
        return appended

    async def _persist_session(self, session: Session) -> None:
        if self._store is None:
            return
        try:
            state = dict(session.state) if session.state else {}
            await self._store.save(session.id, session.app_name, session.user_id, state)
        except Exception:
            logger.warning("Failed to persist session %s", session.id, exc_info=True)


def project_session_for_durable_store(session: Session) -> DurableSessionProjection:
    safe_state = {
        key: value
        for key, value in dict(session.state or {}).items()
        if str(key).startswith("openmagi.")
        and not _unsafe_session_state_key(str(key))
        and not _unsafe_session_state_value(value)
    }
    return DurableSessionProjection(
        sessionRef=f"session:{_digest_ref(session.id)}",
        appName=session.app_name,
        userRef=f"user:{_digest_ref(session.user_id)}",
        eventCount=len(session.events),
        stateDigest=_digest_json(safe_state),
        approvedStateRefs={
            f"state:{_digest_ref(key)}": _digest_json(value) for key, value in safe_state.items()
        },
    )


def _unsafe_session_state_key(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "raw",
            "prompt",
            "output",
            "authorization",
            "cookie",
            "secret",
            "token",
            "password",
            "private",
        )
    )


def _unsafe_session_state_value(value: object) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return any(
            marker in lowered
            for marker in (
                "authorization:",
                "bearer ",
                "cookie:",
                "set-cookie:",
                "sk-",
                "ghp_",
                "raw prompt",
                "raw output",
                "hidden reasoning",
                "chain of thought",
                "/users/",
                "/home/",
                "/workspace/",
                "/data/bots/",
            )
        )
    if isinstance(value, dict):
        return any(
            _unsafe_session_state_key(str(key)) or _unsafe_session_state_value(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list | tuple):
        return any(_unsafe_session_state_value(item) for item in value)
    return False


def _digest_ref(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _digest_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
