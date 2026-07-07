"""U3 (B3): the MAGI_HOSTED_GOVERNED_TURN_ENABLED branch must route the durable
session service through the single-flight lease (magi_agent.runtime.session_ownership),
the SAME chokepoint the legacy gate5b4c3 boundary uses.

Before U3 the governed branch called the bare durable factory
(``durable_hosted_session_factory()()``): no registry entry, no busy-fallback,
no ``session_reused`` verdict, no release. Two overlapping same-session turns
could then interleave writes into one durable SQLite session. These tests pin
the lease semantics on the serving seam:

(a) two overlapping same-key turns -> the second gets a DISTINCT service (NOT the
    durable singleton) and ``lease.reused`` is False (busy-fallback);
(b) a normal turn then a second turn on the same key reuses (``reused=True`` on
    the second) after the first released with seeded=True;
(c) a turn that raises before a result still releases with seeded=False, so the
    provisional miss entry is discarded and the next same-key turn is a miss;
(d) bypass (reuse flag off, or empty session_key_digest) builds a fresh
    in-memory service with no registry interaction and a ``None`` lease.

Scope note: U3 is lease acquire/release + reused capture only. Seed-on-empty
history suppression (U4) and continuity result fields (U8) are separate units,
so these tests observe the lease directly (a spy over the ownership helper) plus
the session service handed to ``build_hosted_runtime``, not result fields.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import magi_agent.runtime.session_ownership as ownership_mod
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.app import create_app
from magi_agent.shadow.hosted_session_substrate import (
    get_durable_hosted_session_service,
    reset_durable_hosted_session_service,
)
from magi_agent.shadow.session_service_registry import (
    default_session_service_registry,
    reset_default_session_service_registry,
)
from tests.test_chat_routes_hosted_governed_turn import (
    _CANARY_BODY,
    _FakeSessionService,
    _canary_headers,
    _make_boundary_result,
    _make_canary_runtime,
)


@pytest.fixture(autouse=True)
def _reset_session_state() -> Any:
    reset_default_session_service_registry()
    reset_durable_hosted_session_service()
    yield
    reset_default_session_service_registry()
    reset_durable_hosted_session_service()


def _install_lease_spies(monkeypatch, *, collect_raises: bool = False) -> tuple[list, list]:
    """Install serving fakes and return (recorded_leases, captured_services).

    ``recorded_leases`` is a list of ``(kwargs, lease)`` for every
    ``acquire_hosted_session_lease`` call made by the governed branch, wrapping
    the REAL helper so genuine registry/lease semantics run. ``captured_services``
    is the ``session_service`` handed to each ``build_hosted_runtime`` call.
    """
    recorded_leases: list = []
    captured_services: list = []
    real_acquire = ownership_mod.acquire_hosted_session_lease

    def spy_acquire(**kwargs: object):  # noqa: ANN202
        lease = real_acquire(**kwargs)
        recorded_leases.append((kwargs, lease))
        return lease

    def fake_build_hosted_runtime(**kwargs: object) -> object:
        captured_services.append(kwargs.get("session_service"))
        return SimpleNamespace()

    async def _noop_gen():  # noqa: ANN202
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        return _noop_gen()

    async def fake_collect(*args: object, **kwargs: object) -> object:
        if collect_raises:
            raise RuntimeError("collect boom before result")
        return _make_boundary_result(output_text="governed answer")

    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.acquire_hosted_session_lease",
        spy_acquire,
    )
    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.build_hosted_runtime",
        fake_build_hosted_runtime,
    )
    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn
    )
    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.collect_engine_to_boundary_result",
        fake_collect,
    )
    return recorded_leases, captured_services


def _governed_env(monkeypatch, tmp_path: Any, *, reuse: str, db: str) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", reuse)
    monkeypatch.setenv("MAGI_HOSTED_SESSION_DB", db)
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))


def _post(runtime: object, *, digest: str, session_id: str | None) -> Any:
    body = dict(_CANARY_BODY)
    if session_id is not None:
        body["sessionId"] = session_id
    return TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers(digest),
        json=body,
    )


# ---------------------------------------------------------------------------
# (b) reuse across two sequential same-key turns
# ---------------------------------------------------------------------------


def test_second_same_key_turn_reuses_after_first_releases(monkeypatch, tmp_path: Any) -> None:
    """Turn 1 (miss, reused=False) releases seeded; turn 2 on the same key hits
    the reusable durable entry (reused=True). Both run against the durable
    singleton, proving the governed path now flows through the lease registry."""
    _governed_env(monkeypatch, tmp_path, reuse="1", db="1")
    recorded, captured = _install_lease_spies(monkeypatch)

    runtime = _make_canary_runtime(tmp_path)
    resp1 = _post(runtime, digest="a" * 64, session_id="sess-reuse")
    assert resp1.status_code == 200, resp1.json()
    resp2 = _post(runtime, digest="b" * 64, session_id="sess-reuse")
    assert resp2.status_code == 200, resp2.json()

    durable = get_durable_hosted_session_service(str(tmp_path / "adk_sessions.db"))
    assert len(recorded) == 2
    assert recorded[0][1] is not None and recorded[0][1].reused is False
    assert recorded[1][1] is not None and recorded[1][1].reused is True
    assert captured[0] is durable
    assert captured[1] is durable


# ---------------------------------------------------------------------------
# (a) overlapping same-key turn gets the busy-fallback (not the durable singleton)
# ---------------------------------------------------------------------------


def test_overlapping_same_key_turn_gets_busy_fallback(monkeypatch, tmp_path: Any) -> None:
    """While one turn holds the lease for a key, an overlapping same-key turn is
    handed a DISTINCT fresh in-memory service (never the durable singleton) and
    reports reused=False. Simulate the overlap by holding the lease (via the real
    helper, so it is not recorded by the spy) across the second request."""
    _governed_env(monkeypatch, tmp_path, reuse="1", db="1")
    recorded, captured = _install_lease_spies(monkeypatch)

    runtime = _make_canary_runtime(tmp_path)
    # Turn 1 establishes the reusable durable entry (miss -> release seeded).
    resp1 = _post(runtime, digest="a" * 64, session_id="sess-overlap")
    assert resp1.status_code == 200, resp1.json()
    durable = get_durable_hosted_session_service(str(tmp_path / "adk_sessions.db"))
    assert captured[0] is durable

    # Hold the lease on the exact same key (real helper, off the spy path) so the
    # key is busy when the next served turn acquires it.
    held = ownership_mod.acquire_hosted_session_lease(**recorded[0][0])
    assert held is not None and held.reused is True
    try:
        resp2 = _post(runtime, digest="b" * 64, session_id="sess-overlap")
        assert resp2.status_code == 200, resp2.json()
    finally:
        held.release(seeded=True)

    # The served overlapping turn (recorded[1]) got the busy-fallback: a fresh
    # in-memory service distinct from the durable singleton, reused=False.
    assert recorded[1][1] is not None and recorded[1][1].reused is False
    assert captured[1] is not durable
    assert isinstance(captured[1], _FakeSessionService)


# ---------------------------------------------------------------------------
# (c) failure before a result releases seeded=False -> next turn is a miss
# ---------------------------------------------------------------------------


def test_failed_turn_before_result_releases_unseeded(monkeypatch, tmp_path: Any) -> None:
    """A turn that raises before collect returns leaves boundary_result None, so
    the lease releases with seeded=False and the provisional miss entry is
    discarded. The registry is empty afterward and the next same-key turn is a
    fresh miss (reused=False), never a stale hit."""
    _governed_env(monkeypatch, tmp_path, reuse="1", db="1")
    recorded, captured = _install_lease_spies(monkeypatch, collect_raises=True)

    runtime = _make_canary_runtime(tmp_path)
    resp1 = _post(runtime, digest="a" * 64, session_id="sess-fail")
    # collect raised -> runner_error fallback.
    assert resp1.status_code == 502, resp1.json()
    assert len(recorded) == 1
    assert recorded[0][1] is not None and recorded[0][1].reused is False
    # seeded=False release discarded the provisional entry.
    assert len(default_session_service_registry()) == 0

    resp2 = _post(runtime, digest="b" * 64, session_id="sess-fail")
    assert resp2.status_code == 502, resp2.json()
    assert len(recorded) == 2
    # Next turn is a fresh miss, not a reuse of the discarded entry.
    assert recorded[1][1] is not None and recorded[1][1].reused is False


# ---------------------------------------------------------------------------
# (d) bypass: no lease, fresh in-memory service, no registry interaction
# ---------------------------------------------------------------------------


def test_bypass_when_reuse_flag_off(monkeypatch, tmp_path: Any) -> None:
    """Reuse flag OFF: the lease helper returns None (bypass); the branch builds a
    fresh in-memory service and never touches the registry, even with a session
    key present and the durable DB flag on."""
    _governed_env(monkeypatch, tmp_path, reuse="0", db="1")
    recorded, captured = _install_lease_spies(monkeypatch)

    runtime = _make_canary_runtime(tmp_path)
    resp = _post(runtime, digest="a" * 64, session_id="sess-bypass")
    assert resp.status_code == 200, resp.json()

    assert len(recorded) == 1
    assert recorded[0][1] is None
    assert isinstance(captured[0], _FakeSessionService)
    assert len(default_session_service_registry()) == 0


def test_bypass_when_session_key_digest_empty(monkeypatch, tmp_path: Any) -> None:
    """Empty session_key_digest (no sessionId): the lease helper returns None
    (bypass) because the session id would be per-request-unique; the branch
    builds a fresh in-memory service and never touches the registry."""
    _governed_env(monkeypatch, tmp_path, reuse="1", db="1")
    recorded, captured = _install_lease_spies(monkeypatch)

    runtime = _make_canary_runtime(tmp_path)
    resp = _post(runtime, digest="a" * 64, session_id=None)
    assert resp.status_code == 200, resp.json()

    assert len(recorded) == 1
    assert recorded[0][1] is None
    assert isinstance(captured[0], _FakeSessionService)
    assert len(default_session_service_registry()) == 0
