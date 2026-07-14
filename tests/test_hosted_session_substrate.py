"""Durable hosted ADK session substrate (PR-3): SqliteSessionService on the PVC.

These tests exercise the real ADK ``SqliteSessionService`` (aiosqlite ships in
the runtime image) at a tmp path to prove sessions and EVENTS survive a
simulated pod restart. (P5-M1b retired the legacy runner boundary; the durable
substrate's continuity through the serving seam is covered by
``tests/test_gate5b_serving_session_lease.py`` and
``tests/test_gate5b_serving_seed_on_empty.py``.)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi_agent.shadow.hosted_session_substrate import (
    durable_hosted_session_factory,
    get_durable_hosted_session_service,
    reset_durable_hosted_session_service,
)


@pytest.fixture(autouse=True)
def _reset_substrate() -> None:
    reset_durable_hosted_session_service()
    yield
    reset_durable_hosted_session_service()


def test_durable_factory_none_when_db_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_HOSTED_SESSION_DB", "0")
    assert durable_hosted_session_factory() is None


def test_durable_factory_returns_process_singleton_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_HOSTED_SESSION_DB", "1")
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    factory_a = durable_hosted_session_factory()
    factory_b = durable_hosted_session_factory()
    assert factory_a is not None and factory_b is not None
    # Same singleton service across calls (the lease registry fronts one store).
    assert factory_a() is factory_b()
    # And the DB path lives on the PVC under MAGI_STATE_DIR, not under HOME.
    from magi_agent.config.env import hosted_session_db_path

    assert hosted_session_db_path() == tmp_path / "adk_sessions.db"


def test_sqlite_substrate_persists_events_across_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A NEW service instance over the same file sees prior events (restart)."""
    monkeypatch.setenv("MAGI_HOSTED_SESSION_DB", "1")
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    db_path = str(tmp_path / "adk_sessions.db")

    from google.adk.events import Event
    from google.genai import types as _genai_types

    async def _turn_a() -> None:
        service = get_durable_hosted_session_service(db_path)
        assert service is not None
        session = await service.create_session(
            app_name="app", user_id="u", session_id="sid"
        )
        await service.append_event(
            session,
            Event(
                author="user",
                content=_genai_types.Content(
                    parts=[_genai_types.Part.from_text(text="turn A")], role="user"
                ),
            ),
        )

    asyncio.run(_turn_a())

    # Simulate a pod restart: drop the process singleton so the next resolve
    # builds a brand-new SqliteSessionService over the same on-disk file.
    reset_durable_hosted_session_service()

    async def _turn_b() -> int:
        service = get_durable_hosted_session_service(db_path)
        assert service is not None
        session = await service.get_session(
            app_name="app", user_id="u", session_id="sid"
        )
        assert session is not None
        return len(session.events)

    assert asyncio.run(_turn_b()) == 1
