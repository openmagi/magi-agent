"""Tests for the local serve session-service reuse registry.

The point of this module is turn-to-turn continuity on the LOCAL ``magi serve``
path: consecutive turns on one channel must reuse the same
``WorkspaceSessionService`` (so ADK session events accumulate), while distinct
channels stay isolated and nothing is ever written to disk.

See docs/plans/2026-07-06-local-serve-session-continuity-fix-design.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.shadow.session_service_registry import SessionServiceRegistry
from magi_agent.transport import local_session_registry
from magi_agent.transport.local_session_registry import (
    acquire_local_session_service,
    local_session_service_registry,
    reset_local_session_service_registry,
)

APP = "magi-cli"


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_local_session_service_registry()
    yield
    reset_local_session_service_registry()


def test_same_key_returns_identical_service():
    first = acquire_local_session_service(app_name=APP, session_id="chan-1")
    second = acquire_local_session_service(app_name=APP, session_id="chan-1")
    assert first is second


def test_distinct_session_ids_isolated():
    a = acquire_local_session_service(app_name=APP, session_id="chan-1")
    b = acquire_local_session_service(app_name=APP, session_id="chan-2")
    assert a is not b


def test_distinct_app_names_isolated():
    a = acquire_local_session_service(app_name="magi-cli", session_id="chan-1")
    b = acquire_local_session_service(app_name="other-app", session_id="chan-1")
    assert a is not b


def test_singleton_registry_is_stable():
    assert local_session_service_registry() is local_session_service_registry()


def test_reset_drops_state():
    before = acquire_local_session_service(app_name=APP, session_id="chan-1")
    reset_local_session_service_registry()
    after = acquire_local_session_service(app_name=APP, session_id="chan-1")
    assert before is not after


def test_lru_cap_evicts_oldest(monkeypatch):
    small = SessionServiceRegistry(max_entries=2, ttl_seconds=3600.0)
    monkeypatch.setattr(local_session_registry, "_registry", small)
    first = acquire_local_session_service(app_name=APP, session_id="a")
    acquire_local_session_service(app_name=APP, session_id="b")
    acquire_local_session_service(app_name=APP, session_id="c")  # evicts "a"
    reacquired = acquire_local_session_service(app_name=APP, session_id="a")
    assert reacquired is not first


def test_idle_ttl_evicts(monkeypatch):
    clock = {"t": 1000.0}
    ttl_registry = SessionServiceRegistry(
        max_entries=64, ttl_seconds=100.0, clock=lambda: clock["t"]
    )
    monkeypatch.setattr(local_session_registry, "_registry", ttl_registry)
    first = acquire_local_session_service(app_name=APP, session_id="chan-1")
    clock["t"] += 200.0  # past TTL
    after = acquire_local_session_service(app_name=APP, session_id="chan-1")
    assert after is not first


def test_service_has_no_disk_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    service = acquire_local_session_service(app_name=APP, session_id="chan-1")
    # In-memory only: no durable store, so incognito continuity never touches disk.
    assert getattr(service, "_store") is None
    assert list(Path(tmp_path).iterdir()) == []
