"""Tests for the hosted session-service registry (08-PR5).

Security-first: the registry is the multitenant seam for hosted session
reuse. The isolation tests here — distinct bot digests / session ids never
sharing a session service, and eviction (LRU / TTL / explicit) never
resurrecting prior content — are the point of the feature, not incidental
coverage.
"""

from __future__ import annotations

import threading

import pytest

from magi_agent.shadow.session_service_registry import (
    SessionServiceRegistry,
    default_session_service_registry,
    reset_default_session_service_registry,
)


BOT_A = "sha256:" + "a" * 64
BOT_B = "sha256:" + "b" * 64
SESSION_1 = "session-key-digest-1"
SESSION_2 = "session-key-digest-2"


class _FakeSessionService:
    """Stand-in for ADK InMemorySessionService with observable content."""

    def __init__(self) -> None:
        self.events: list[str] = []


class _ManualClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def _registry(
    *,
    max_entries: int = 4,
    ttl_seconds: float = 60.0,
) -> tuple[SessionServiceRegistry, _ManualClock]:
    clock = _ManualClock()
    registry = SessionServiceRegistry(
        max_entries=max_entries,
        ttl_seconds=ttl_seconds,
        clock=clock,
    )
    return registry, clock


# ---------------------------------------------------------------------------
# Core get-or-create semantics
# ---------------------------------------------------------------------------
def test_registry_miss_creates_via_factory_and_hit_returns_same_instance() -> None:
    registry, _clock = _registry()

    first, first_reused = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)
    second, second_reused = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)

    assert isinstance(first, _FakeSessionService)
    assert first_reused is False
    assert second is first
    assert second_reused is True
    assert len(registry) == 1


# ---------------------------------------------------------------------------
# Isolation (security a/b): distinct tenants never share a session service
# ---------------------------------------------------------------------------
def test_registry_isolation_distinct_bot_digests_with_identical_session_id() -> None:
    registry, _clock = _registry()

    bot_a_service, _ = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)
    bot_a_service.events.append("bot-a-private-history")
    bot_b_service, bot_b_reused = registry.get_or_create(
        (BOT_B, SESSION_1),
        _FakeSessionService,
    )

    assert bot_b_reused is False
    assert bot_b_service is not bot_a_service
    assert bot_b_service.events == []


def test_registry_isolation_distinct_session_ids_for_same_bot() -> None:
    registry, _clock = _registry()

    session_1_service, _ = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)
    session_1_service.events.append("session-1-private-history")
    session_2_service, session_2_reused = registry.get_or_create(
        (BOT_A, SESSION_2),
        _FakeSessionService,
    )

    assert session_2_reused is False
    assert session_2_service is not session_1_service
    assert session_2_service.events == []


def test_registry_rejects_empty_key_parts_instead_of_shared_bucket() -> None:
    registry, _clock = _registry()

    with pytest.raises(ValueError):
        registry.get_or_create(("", SESSION_1), _FakeSessionService)
    with pytest.raises(ValueError):
        registry.get_or_create((BOT_A, "   "), _FakeSessionService)
    assert len(registry) == 0


# ---------------------------------------------------------------------------
# TTL eviction (security c)
# ---------------------------------------------------------------------------
def test_registry_ttl_expiry_evicts_and_builds_fresh_session() -> None:
    registry, clock = _registry(ttl_seconds=60.0)

    stale, _ = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)
    stale.events.append("must-not-resurrect")
    clock.advance(61.0)
    fresh, fresh_reused = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)

    assert fresh_reused is False
    assert fresh is not stale
    assert fresh.events == []


def test_registry_ttl_is_idle_based_and_access_refreshes_it() -> None:
    registry, clock = _registry(ttl_seconds=60.0)

    first, _ = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)
    clock.advance(40.0)
    second, second_reused = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)
    clock.advance(40.0)
    third, third_reused = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)

    assert second is first
    assert second_reused is True
    assert third is first
    assert third_reused is True


def test_registry_purges_expired_entries_on_access_to_bound_memory() -> None:
    registry, clock = _registry(max_entries=8, ttl_seconds=60.0)

    registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)
    registry.get_or_create((BOT_A, SESSION_2), _FakeSessionService)
    clock.advance(61.0)
    registry.get_or_create((BOT_B, SESSION_1), _FakeSessionService)

    assert len(registry) == 1


# ---------------------------------------------------------------------------
# LRU cap (security d)
# ---------------------------------------------------------------------------
def test_registry_lru_cap_evicts_least_recently_used_and_never_blocks() -> None:
    registry, _clock = _registry(max_entries=2)

    oldest, _ = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)
    registry.get_or_create((BOT_A, SESSION_2), _FakeSessionService)
    # Touch SESSION_1 so SESSION_2 becomes the least recently used entry.
    registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)
    registry.get_or_create((BOT_B, SESSION_1), _FakeSessionService)

    assert len(registry) == 2
    survivor, survivor_reused = registry.get_or_create(
        (BOT_A, SESSION_1),
        _FakeSessionService,
    )
    assert survivor is oldest
    assert survivor_reused is True
    _evicted, evicted_reused = registry.get_or_create(
        (BOT_A, SESSION_2),
        _FakeSessionService,
    )
    assert evicted_reused is False


def test_registry_lru_evicted_session_never_resurrects_prior_content() -> None:
    registry, _clock = _registry(max_entries=1)

    stale, _ = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)
    stale.events.append("must-not-resurrect")
    registry.get_or_create((BOT_A, SESSION_2), _FakeSessionService)
    fresh, fresh_reused = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)

    assert fresh_reused is False
    assert fresh is not stale
    assert fresh.events == []


# ---------------------------------------------------------------------------
# Explicit evict (security e)
# ---------------------------------------------------------------------------
def test_registry_explicit_evict_forces_fresh_session_without_resurrection() -> None:
    registry, _clock = _registry()

    stale, _ = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)
    stale.events.append("must-not-resurrect")

    assert registry.evict((BOT_A, SESSION_1)) is True
    assert registry.evict((BOT_A, SESSION_1)) is False
    fresh, fresh_reused = registry.get_or_create((BOT_A, SESSION_1), _FakeSessionService)
    assert fresh_reused is False
    assert fresh is not stale
    assert fresh.events == []


# ---------------------------------------------------------------------------
# Construction validation + concurrency
# ---------------------------------------------------------------------------
def test_registry_rejects_non_positive_caps() -> None:
    with pytest.raises(ValueError):
        SessionServiceRegistry(max_entries=0, ttl_seconds=60.0)
    with pytest.raises(ValueError):
        SessionServiceRegistry(max_entries=4, ttl_seconds=0.0)


def test_registry_concurrent_get_or_create_yields_single_instance_per_key() -> None:
    registry, _clock = _registry(max_entries=8)
    created: list[_FakeSessionService] = []
    results: list[object] = []
    barrier = threading.Barrier(8)

    def factory() -> _FakeSessionService:
        service = _FakeSessionService()
        created.append(service)
        return service

    def worker() -> None:
        barrier.wait()
        service, _reused = registry.get_or_create((BOT_A, SESSION_1), factory)
        results.append(service)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 1
    assert all(service is created[0] for service in results)


# ---------------------------------------------------------------------------
# Per-key single-flight (security finding 1): try_acquire / release
# ---------------------------------------------------------------------------
def test_registry_try_acquire_miss_creates_and_reuses_only_after_release() -> None:
    registry, _clock = _registry()

    first, first_reused = registry.try_acquire((BOT_A, SESSION_1), _FakeSessionService)
    assert isinstance(first, _FakeSessionService)
    assert first_reused is False
    assert len(registry) == 1

    assert registry.release((BOT_A, SESSION_1), first) is True
    second, second_reused = registry.try_acquire((BOT_A, SESSION_1), _FakeSessionService)
    assert second is first
    assert second_reused is True


def test_registry_try_acquire_busy_key_returns_fresh_unregistered_fallback() -> None:
    registry, _clock = _registry()

    held, _ = registry.try_acquire((BOT_A, SESSION_1), _FakeSessionService)
    held.events.append("in-flight-turn-history")

    # Second concurrent same-key acquire while the first turn still holds the
    # key: a FRESH service, not reused, never the in-flight instance.
    fallback, fallback_reused = registry.try_acquire(
        (BOT_A, SESSION_1),
        _FakeSessionService,
    )

    assert fallback_reused is False
    assert fallback is not held
    assert fallback.events == []
    # The fallback is never registered: the held entry stays the only one.
    assert len(registry) == 1

    assert registry.release((BOT_A, SESSION_1), held) is True
    reacquired, reacquired_reused = registry.try_acquire(
        (BOT_A, SESSION_1),
        _FakeSessionService,
    )
    assert reacquired is held
    assert reacquired_reused is True


def test_registry_busy_fallback_uses_distinct_fallback_factory_when_provided() -> None:
    """PR-3: the durable-substrate path shares ONE process-singleton service via
    the primary factory, but a busy-overlap must NEVER hand out that singleton
    (concurrent mutation of one durable session). A distinct fallback_factory
    supplies a fresh throwaway service for the overlap instead."""
    registry, _clock = _registry()
    singleton = _FakeSessionService()

    def primary() -> _FakeSessionService:
        return singleton

    def fallback() -> _FakeSessionService:
        return _FakeSessionService()

    held, held_reused = registry.try_acquire(
        (BOT_A, SESSION_1), primary, fallback_factory=fallback
    )
    assert held is singleton
    assert held_reused is False

    overlap, overlap_reused = registry.try_acquire(
        (BOT_A, SESSION_1), primary, fallback_factory=fallback
    )
    # Busy overlap: fresh throwaway from fallback_factory, never the singleton.
    assert overlap_reused is False
    assert overlap is not singleton
    assert len(registry) == 1

    assert registry.release((BOT_A, SESSION_1), held) is True
    reacquired, reacquired_reused = registry.try_acquire(
        (BOT_A, SESSION_1), primary, fallback_factory=fallback
    )
    # After release the same singleton is reused (durable continuity).
    assert reacquired is singleton
    assert reacquired_reused is True


def test_registry_fallback_factory_defaults_to_primary_factory() -> None:
    """Back-compat: without fallback_factory the busy overlap uses the primary
    factory, exactly like today."""
    registry, _clock = _registry()
    held, _ = registry.try_acquire((BOT_A, SESSION_1), _FakeSessionService)
    overlap, overlap_reused = registry.try_acquire((BOT_A, SESSION_1), _FakeSessionService)
    assert overlap_reused is False
    assert overlap is not held
    assert isinstance(overlap, _FakeSessionService)


def test_registry_release_is_identity_checked_and_idempotent() -> None:
    registry, _clock = _registry()

    held, _ = registry.try_acquire((BOT_A, SESSION_1), _FakeSessionService)
    fallback, _ = registry.try_acquire((BOT_A, SESSION_1), _FakeSessionService)

    # Releasing the unregistered busy-fallback must not clear the holder's
    # mark — otherwise a third turn could reuse the still-in-flight service.
    assert registry.release((BOT_A, SESSION_1), fallback) is False
    still_fallback, still_reused = registry.try_acquire(
        (BOT_A, SESSION_1),
        _FakeSessionService,
    )
    assert still_reused is False
    assert still_fallback is not held

    assert registry.release((BOT_A, SESSION_1), held) is True
    # Double release is a safe no-op (exception-safe finally paths may race).
    assert registry.release((BOT_A, SESSION_1), held) is False


def test_registry_release_in_finally_after_exception_restores_reuse() -> None:
    registry, _clock = _registry()
    key = (BOT_A, SESSION_1)

    held, _ = registry.try_acquire(key, _FakeSessionService)
    with pytest.raises(RuntimeError):
        try:
            raise RuntimeError("turn failed mid-runner")
        finally:
            registry.release(key, held)

    reacquired, reacquired_reused = registry.try_acquire(key, _FakeSessionService)
    assert reacquired is held
    assert reacquired_reused is True


def test_registry_release_unseeded_miss_discards_entry_for_next_miss() -> None:
    registry, _clock = _registry()
    key = (BOT_A, SESSION_1)

    first, first_reused = registry.try_acquire(key, _FakeSessionService)
    assert first_reused is False

    assert registry.release(key, first, seeded=False) is True
    assert len(registry) == 0

    second, second_reused = registry.try_acquire(key, _FakeSessionService)
    assert second_reused is False
    assert second is not first


def test_registry_release_after_eviction_is_noop_and_never_unmarks_new_holder() -> None:
    registry, _clock = _registry(max_entries=1)
    key = (BOT_A, SESSION_1)

    old, _ = registry.try_acquire(key, _FakeSessionService)
    # LRU pressure evicts the held entry while its turn is still running...
    registry.try_acquire((BOT_A, SESSION_2), _FakeSessionService)
    # ...and a new same-key turn registers a fresh entry it now holds.
    new_holder, new_reused = registry.try_acquire(key, _FakeSessionService)
    assert new_reused is False
    assert new_holder is not old

    # The stale lease from the evicted turn must not unmark the new holder.
    assert registry.release(key, old) is False
    fallback, fallback_reused = registry.try_acquire(key, _FakeSessionService)
    assert fallback_reused is False
    assert fallback is not new_holder
    assert len(registry) == 1


def test_registry_concurrent_try_acquire_same_key_never_shares_a_service() -> None:
    registry, _clock = _registry(max_entries=8)
    results: list[object] = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        service, _reused = registry.try_acquire((BOT_A, SESSION_1), _FakeSessionService)
        with results_lock:
            results.append(service)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # No release happens between the acquires, so no two concurrent holders
    # may ever observe the same instance: one registered + 7 busy-fallbacks.
    assert len(results) == 8
    assert len({id(service) for service in results}) == 8
    assert len(registry) == 1


# ---------------------------------------------------------------------------
# Process-default registry + env-tunable caps
# ---------------------------------------------------------------------------
def test_default_registry_is_process_scoped_and_resettable() -> None:
    reset_default_session_service_registry()
    try:
        first = default_session_service_registry()
        second = default_session_service_registry()
        assert second is first
        reset_default_session_service_registry()
        assert default_session_service_registry() is not first
    finally:
        reset_default_session_service_registry()


def test_default_registry_reads_env_tunable_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE_MAX_ENTRIES", "7")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE_TTL_SECONDS", "120")
    reset_default_session_service_registry()
    try:
        registry = default_session_service_registry()
        assert registry.max_entries == 7
        assert registry.ttl_seconds == 120.0
    finally:
        reset_default_session_service_registry()


# ---------------------------------------------------------------------------
# MAGI_HOSTED_SESSION_REUSE flag (profile-aware default-ON) + cap readers
# ---------------------------------------------------------------------------
def test_hosted_session_reuse_flag_default_on_full_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.config.env import is_hosted_session_reuse_enabled

    monkeypatch.delenv("MAGI_HOSTED_SESSION_REUSE", raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    # Profile-aware default-ON: unset flag in the full/unset profile resolves ON.
    assert is_hosted_session_reuse_enabled() is True


def test_hosted_session_reuse_flag_default_off_under_safe_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.config.env import is_hosted_session_reuse_enabled

    monkeypatch.delenv("MAGI_HOSTED_SESSION_REUSE", raising=False)
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")
    assert is_hosted_session_reuse_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "ON", "True"])
def test_hosted_session_reuse_flag_explicit_truthy(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.config.env import is_hosted_session_reuse_enabled

    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", value)
    assert is_hosted_session_reuse_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "off", "no"])
def test_hosted_session_reuse_flag_explicit_falsy(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.config.env import is_hosted_session_reuse_enabled

    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", value)
    assert is_hosted_session_reuse_enabled() is False


def test_hosted_session_reuse_flag_registered_profile_default_on() -> None:
    from magi_agent.config.flags import get_flag

    spec = get_flag("MAGI_HOSTED_SESSION_REUSE")
    assert spec.kind == "profile_bool"
    assert spec.scope == "hosted"


def test_hosted_session_db_flag_registered_profile_default_on() -> None:
    from magi_agent.config.flags import get_flag

    spec = get_flag("MAGI_HOSTED_SESSION_DB")
    assert spec.kind == "profile_bool"
    assert spec.scope == "hosted"


def test_hosted_session_reuse_cap_readers_have_safe_defaults_and_clamping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.config.env import (
        hosted_session_reuse_max_entries,
        hosted_session_reuse_ttl_seconds,
    )

    monkeypatch.delenv("MAGI_HOSTED_SESSION_REUSE_MAX_ENTRIES", raising=False)
    monkeypatch.delenv("MAGI_HOSTED_SESSION_REUSE_TTL_SECONDS", raising=False)
    assert hosted_session_reuse_max_entries() == 64
    # Generous 6h idle lease (PR-3 budget bump 1800 -> 21600).
    assert hosted_session_reuse_ttl_seconds() == 21600.0

    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE_MAX_ENTRIES", "16")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE_TTL_SECONDS", "600")
    assert hosted_session_reuse_max_entries() == 16
    assert hosted_session_reuse_ttl_seconds() == 600.0

    # Invalid values fall back to defaults; non-positive values clamp to 1 so a
    # mis-set cap can never disable bounding or produce an unbounded registry.
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE_MAX_ENTRIES", "banana")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE_TTL_SECONDS", "banana")
    assert hosted_session_reuse_max_entries() == 64
    assert hosted_session_reuse_ttl_seconds() == 21600.0

    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE_MAX_ENTRIES", "0")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE_TTL_SECONDS", "-5")
    assert hosted_session_reuse_max_entries() == 1
    assert hosted_session_reuse_ttl_seconds() == 1.0
