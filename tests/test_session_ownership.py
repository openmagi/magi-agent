"""Unit tests for the consolidated session-ownership module (WS-A / U1).

These lock the semantics moved out of the legacy live runner boundary into
``magi_agent/runtime/session_ownership.py``: the fail-open event-count probe,
the seed-on-empty include-history verdict, the seeded-history message count,
and the hosted single-flight lease chokepoint (miss/hit/busy-overlap/release
plus the flag and empty-digest bypasses that return ``None``).

Hermetic: no env dependency (the reuse flag and durable factory are patched),
and every lease test uses a fresh local ``SessionServiceRegistry`` so there is
no process-global state to reset.
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.runtime.session_ownership import (
    HostedSessionLease,
    acquire_hosted_session_lease,
    probe_session_event_count,
    resolve_include_history,
    seeded_history_message_count,
)
from magi_agent.shadow.session_service_registry import SessionServiceRegistry


# ── probe_session_event_count (fail-open) ────────────────────────────────────


class _SyncSessionService:
    def __init__(self, session: object) -> None:
        self._session = session

    def get_session(self, *, app_name: str, user_id: str, session_id: str) -> object:
        return self._session


class _AsyncSessionService:
    def __init__(self, session: object) -> None:
        self._session = session

    async def get_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> object:
        return self._session


class _RaisingSessionService:
    def get_session(self, *, app_name: str, user_id: str, session_id: str) -> object:
        raise RuntimeError("boom")


class _Session:
    def __init__(self, events: object) -> None:
        self.events = events


def _probe(service: object) -> int | None:
    return asyncio.run(
        probe_session_event_count(
            service,
            app_name="app",
            user_id="user",
            session_id="sid",
        )
    )


def test_probe_no_get_session_is_fail_open_none() -> None:
    assert _probe(object()) is None


def test_probe_counts_events_sync() -> None:
    service = _SyncSessionService(_Session(["e1", "e2", "e3"]))
    assert _probe(service) == 3


def test_probe_counts_events_async() -> None:
    service = _AsyncSessionService(_Session(["e1"]))
    assert _probe(service) == 1


def test_probe_missing_session_is_zero() -> None:
    assert _probe(_SyncSessionService(None)) == 0


def test_probe_events_none_is_none() -> None:
    assert _probe(_SyncSessionService(_Session(None))) is None


def test_probe_get_session_error_is_none() -> None:
    assert _probe(_RaisingSessionService()) is None


# ── resolve_include_history (seed-on-empty truth table) ───────────────────────


def test_resolve_seeds_when_reused_but_empty() -> None:
    assert resolve_include_history(session_reused=True, session_event_count=0) is True


def test_resolve_no_seed_when_reused_and_populated() -> None:
    assert resolve_include_history(session_reused=True, session_event_count=5) is False


def test_resolve_no_seed_when_fresh_but_already_populated() -> None:
    assert resolve_include_history(session_reused=False, session_event_count=2) is False


def test_resolve_seeds_when_fresh_and_empty() -> None:
    assert resolve_include_history(session_reused=False, session_event_count=0) is True


def test_resolve_undeterminable_falls_back_to_not_reused() -> None:
    # None event count -> registry verdict: reused means already-seeded.
    assert resolve_include_history(session_reused=True, session_event_count=None) is False
    assert (
        resolve_include_history(session_reused=False, session_event_count=None) is True
    )


# ── seeded_history_message_count ──────────────────────────────────────────────


class _RunnerInput:
    def __init__(self, history: object) -> None:
        self.sanitized_recent_history = history


def test_seeded_count_counts_valid_turns() -> None:
    ri = _RunnerInput(
        (
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        )
    )
    assert seeded_history_message_count(ri) == 2


def test_seeded_count_ignores_empty_and_odd_shapes() -> None:
    ri = _RunnerInput(
        (
            {"role": "user", "content": ""},
            {"role": "system", "content": "x"},
            "not-a-mapping",
            {"role": "assistant", "content": "keep"},
        )
    )
    assert seeded_history_message_count(ri) == 1


def test_seeded_count_no_history_is_zero() -> None:
    assert seeded_history_message_count(_RunnerInput(())) == 0
    assert seeded_history_message_count(object()) == 0


# ── acquire_hosted_session_lease + HostedSessionLease ─────────────────────────


class _InMemoryService:
    """Distinct-object factory stand-in for ADK ``InMemorySessionService``."""

    _counter = 0

    def __init__(self) -> None:
        type(self)._counter += 1
        self.instance_id = type(self)._counter


@pytest.fixture()
def patch_reuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the hosted reuse flag ON and the durable factory OFF."""
    import magi_agent.config.env as env_mod
    import magi_agent.shadow.hosted_session_substrate as substrate_mod

    monkeypatch.setattr(env_mod, "is_hosted_session_reuse_enabled", lambda: True)
    monkeypatch.setattr(substrate_mod, "durable_hosted_session_factory", lambda: None)


def _acquire(registry: SessionServiceRegistry, digest: str = "d1") -> HostedSessionLease | None:
    return acquire_hosted_session_lease(
        bot_id_digest="bot-1",
        session_id="sess-1",
        session_key_digest=digest,
        in_memory_factory=_InMemoryService,
        registry=registry,
    )


def test_acquire_bypass_when_reuse_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import magi_agent.config.env as env_mod

    monkeypatch.setattr(env_mod, "is_hosted_session_reuse_enabled", lambda: False)
    registry = SessionServiceRegistry()
    assert _acquire(registry) is None
    assert len(registry) == 0


def test_acquire_bypass_when_empty_digest(patch_reuse: None) -> None:
    registry = SessionServiceRegistry()
    assert _acquire(registry, digest="") is None
    assert len(registry) == 0


def test_acquire_miss_builds_and_registers(patch_reuse: None) -> None:
    registry = SessionServiceRegistry()
    lease = _acquire(registry)
    assert lease is not None
    assert lease.reused is False
    assert isinstance(lease.service, _InMemoryService)
    assert len(registry) == 1


def test_acquire_hit_reuses_after_release(patch_reuse: None) -> None:
    registry = SessionServiceRegistry()
    first = _acquire(registry)
    assert first is not None and first.reused is False
    first.release(seeded=True)

    second = _acquire(registry)
    assert second is not None
    assert second.reused is True
    assert second.service is first.service


def test_acquire_busy_overlap_gets_fresh_unregistered_service(patch_reuse: None) -> None:
    registry = SessionServiceRegistry()
    first = _acquire(registry)
    assert first is not None and first.reused is False
    # No release yet: the key is busy, so an overlapping acquire single-flights.
    second = _acquire(registry)
    assert second is not None
    assert second.reused is False
    assert second.service is not first.service


def test_release_seeded_false_before_event_discards_entry(patch_reuse: None) -> None:
    registry = SessionServiceRegistry()
    first = _acquire(registry)
    assert first is not None and first.reused is False
    # Failure before any runner event: release discards the provisional entry.
    first.release(seeded=False)
    second = _acquire(registry)
    assert second is not None
    assert second.reused is False  # behaves like a fresh miss


def test_acquire_durable_factory_uses_singleton_with_inmemory_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import magi_agent.config.env as env_mod
    import magi_agent.shadow.hosted_session_substrate as substrate_mod

    monkeypatch.setattr(env_mod, "is_hosted_session_reuse_enabled", lambda: True)

    durable_singleton = object()
    monkeypatch.setattr(
        substrate_mod,
        "durable_hosted_session_factory",
        lambda: (lambda: durable_singleton),
    )
    registry = SessionServiceRegistry()

    first = _acquire(registry)
    assert first is not None
    assert first.service is durable_singleton  # miss -> durable singleton
    # Busy overlap must NOT hand out the same durable singleton; it uses the
    # in-memory fallback factory instead.
    second = _acquire(registry)
    assert second is not None
    assert second.service is not durable_singleton
    assert isinstance(second.service, _InMemoryService)
