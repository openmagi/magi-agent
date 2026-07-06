"""Durable hosted ADK session substrate (PR-3): SqliteSessionService on the PVC.

These tests exercise the real ADK ``SqliteSessionService`` (aiosqlite ships in
the runtime image) at a tmp path, both directly and through the live runner
boundary, to prove sessions and EVENTS survive a simulated pod restart.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi_agent.shadow.gate5b4c3_live_runner_boundary import Gate5B4C3LiveRunnerBoundary
from magi_agent.shadow.hosted_session_substrate import (
    DEFAULT_NUM_RECENT_EVENTS,
    durable_hosted_session_factory,
    get_durable_hosted_session_service,
    reset_durable_hosted_session_service,
)
from magi_agent.shadow.session_service_registry import SessionServiceRegistry

from tests.support.gate5b4c3_fakes import _ManualCalculationTool
from tests.test_gate5b4c3_live_runner_boundary import (
    _CURRENT_TURN_TEXT,
    _EventAppendingRunner,
    _HISTORY_MARKER,
    _real_session_primitives,
    _session_reuse_config,
    _session_reuse_request,
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


def _durable_boundary(registry: SessionServiceRegistry, *, append: bool) -> object:
    return Gate5B4C3LiveRunnerBoundary(
        lambda: _real_session_primitives(append=append),
        adk_tools=(_ManualCalculationTool,),
        session_service_registry=registry,
    )


def test_boundary_durable_substrate_holds_continuity_across_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The repro-as-a-test at the durable layer: a registry wipe (pod restart /
    image bump) no longer loses memory because the durable session persisted, so
    the emptiness probe reports events and the turn is NOT re-seeded blind."""
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", "1")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_DB", "1")
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))

    registry_1 = SessionServiceRegistry(max_entries=4, ttl_seconds=60.0)
    r1 = _durable_boundary(registry_1, append=True).invoke(
        _session_reuse_request(), config=_session_reuse_config()
    )
    assert r1.status == "completed"
    # Turn 1 is a miss over an empty durable session -> seed the sanitized turns.
    assert r1.session_reused is False
    assert r1.session_event_count == 0
    assert r1.seeded_message_count == 2

    # Restart: the process registry is wiped AND the durable singleton dropped.
    reset_durable_hosted_session_service()
    registry_2 = SessionServiceRegistry(max_entries=4, ttl_seconds=60.0)
    r2 = _durable_boundary(registry_2, append=True).invoke(
        _session_reuse_request(), config=_session_reuse_config()
    )
    assert r2.status == "completed"
    # Registry miss (new registry) -> reused False, but the durable session
    # reopened from disk holds turn 1's event, so history is NOT re-seeded.
    assert r2.session_reused is False
    assert r2.session_event_count >= 1
    assert r2.seeded_message_count == 0
    second_text = _EventAppendingRunner.run_kwargs["new_message"].parts[0].text
    assert _HISTORY_MARKER not in second_text
    assert second_text == _CURRENT_TURN_TEXT


def test_boundary_durable_run_config_bounds_recent_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", "1")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_DB", "1")
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))

    registry = SessionServiceRegistry(max_entries=4, ttl_seconds=60.0)
    result = _durable_boundary(registry, append=True).invoke(
        _session_reuse_request(), config=_session_reuse_config()
    )
    assert result.status == "completed"
    run_config = _EventAppendingRunner.run_kwargs.get("run_config")
    assert run_config is not None
    assert run_config.get_session_config is not None
    assert run_config.get_session_config.num_recent_events == DEFAULT_NUM_RECENT_EVENTS


def test_boundary_without_durable_flag_leaves_run_config_unbounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", "1")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_DB", "0")

    registry = SessionServiceRegistry(max_entries=4, ttl_seconds=60.0)
    result = _durable_boundary(registry, append=True).invoke(
        _session_reuse_request(), config=_session_reuse_config()
    )
    assert result.status == "completed"
    run_config = _EventAppendingRunner.run_kwargs.get("run_config")
    assert run_config is not None
    # In-memory substrate: no explicit fetch bound (ADK default), byte-identical.
    assert run_config.get_session_config is None
