"""PR9b — Turnkey learning bootstrap (auto-run the safe reflect tier on startup).

Covers the bootstrap that actually RUNS the safe reflect tier in the background
on a default install:

    operator's local sessions (RealTranscriptSource over the SAME session DB the
    runtime writes) → structural signal extraction → deterministic label →
    eval-gate → store ``proposed`` items → dashboard shows them for human
    approval.

No prompt injection, no LLM cost, no behavior change until the operator opts in.

Hard guarantees asserted here:

* Default config ⇒ bootstrap is ACTIVE: ``run_once()`` reads sessions, produces
  ``proposed`` items in a temp ``SqliteLearningStore`` using the DETERMINISTIC
  labeler.
* Master off (``MAGI_LEARNING_ENABLED=false``) ⇒ bootstrap is a NO-OP (no store
  writes, no background task scheduled).
* FAIL-OPEN: a session reader / store that raises on construction or during a
  pass NEVER propagates — the bootstrap degrades silently and app startup still
  proceeds.
* Labeler selection: deterministic by default; ``MAGI_LEARNING_LABELER=llm``
  with no model client ⇒ falls back to deterministic (no crash).
* injection/live remain OFF (config) and there are no injection side effects.
* Non-reentrancy: overlapping ``run_once()`` calls don't double-process /
  corrupt the watermark.
* ``stop()`` cancels the background task cleanly.
* The three frozen ``Literal[False]`` authority flags are never flipped.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from magi_agent.learning.bootstrap import LearningBootstrap
from magi_agent.learning.config import (
    ENV_DASHBOARD,
    ENV_INJECTION,
    ENV_LABELER,
    ENV_LIVE,
    ENV_MASTER,
    ENV_REFLECTION,
    ENV_REFLECTION_INTERVAL,
    ENV_TELEMETRY,
    resolve_learning_config,
)
from magi_agent.learning.store import SqliteLearningStore

_ALL_ENV = (
    ENV_MASTER,
    ENV_REFLECTION,
    ENV_DASHBOARD,
    ENV_TELEMETRY,
    ENV_LABELER,
    ENV_INJECTION,
    ENV_LIVE,
    ENV_REFLECTION_INTERVAL,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with all MAGI_LEARNING_* env vars UNSET (default-ON)."""
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _candidate_producing_session(session_id: str, ts: str) -> dict[str, Any]:
    """A persisted-session row whose state yields a structural signal.

    Mirrors the draft-vs-final + redirect shape used by PR3/PR4 tests so the
    deterministic pipeline produces at least one candidate.
    """
    return {
        "id": session_id,
        "app_name": "magi",
        "user_id": "local-user",
        "state": {
            "turns": [
                {"role": "user", "text": "no"},
                {"role": "assistant", "text": "draft"},
                {"role": "user", "text": "actually do X instead"},
                {"role": "assistant", "text": "final"},
            ],
            "finalOutput": "final",
            "draftOutput": "draft",
        },
        "created_at": ts,
        "updated_at": ts,
    }


class FakeSessionReader:
    """In-memory ``SessionPersistenceReader`` over canned rows."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls = 0

    def list_sync(
        self,
        app_name: str,
        user_id: str | None = None,
        *,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self.calls += 1
        rows = [
            r
            for r in self._rows
            if r["app_name"] == app_name
            and (user_id is None or r["user_id"] == user_id)
            and (since is None or r["updated_at"] > since)
        ]
        rows.sort(key=lambda r: r["updated_at"])
        if limit is not None:
            rows = rows[:limit]
        return rows


class ExplodingSessionReader:
    """A reader that raises on every read (fail-open exercise)."""

    def list_sync(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("session DB exploded")


class SlowSessionReader:
    """Reader that blocks until released, to exercise non-reentrancy."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls = 0
        self.gate = asyncio.Event()

    def list_sync(
        self,
        app_name: str,
        user_id: str | None = None,
        *,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self.calls += 1
        rows = [
            r
            for r in self._rows
            if r["app_name"] == app_name
            and (since is None or r["updated_at"] > since)
        ]
        rows.sort(key=lambda r: r["updated_at"])
        return rows


# ---------------------------------------------------------------------------
# 1. Default config ⇒ bootstrap active; produces proposed items deterministically
# ---------------------------------------------------------------------------


def test_default_run_once_produces_proposed_items(tmp_path) -> None:
    store = SqliteLearningStore(workspace_root=str(tmp_path))
    reader = FakeSessionReader(
        [_candidate_producing_session("s1", "2026-06-03T10:00:00Z")]
    )
    boot = LearningBootstrap(
        learning_store=store,
        session_reader=reader,
        app_name="magi",
        user_id="local-user",
    )

    async def _run() -> None:
        await boot.start()
        try:
            assert boot.active is True
            result = await boot.run_once()
            assert result is not None
            assert result.status == "ok"
        finally:
            await boot.stop()

    asyncio.run(_run())

    assert reader.calls >= 1
    page = store.list(tenant_id="local")
    store.close()
    assert len(page.items) >= 1
    assert {item.status for item in page.items} <= {"proposed", "active"}


def test_default_uses_deterministic_labeler(tmp_path) -> None:
    store = SqliteLearningStore(workspace_root=str(tmp_path))
    reader = FakeSessionReader([])
    boot = LearningBootstrap(
        learning_store=store,
        session_reader=reader,
        app_name="magi",
        user_id="local-user",
    )
    asyncio.run(boot.start())
    try:
        assert boot.labeler_kind == "deterministic"
    finally:
        asyncio.run(boot.stop())
        store.close()


# ---------------------------------------------------------------------------
# 2. Master off ⇒ no-op
# ---------------------------------------------------------------------------


def test_master_off_is_noop(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_MASTER, "false")
    store = SqliteLearningStore(workspace_root=str(tmp_path))
    reader = FakeSessionReader(
        [_candidate_producing_session("s1", "2026-06-03T10:00:00Z")]
    )
    boot = LearningBootstrap(
        learning_store=store,
        session_reader=reader,
        app_name="magi",
        user_id="local-user",
    )

    async def _run() -> None:
        await boot.start()
        try:
            assert boot.active is False
            result = await boot.run_once()
            # Either a disabled no-op result or None; never an ``ok`` pass.
            assert result is None or result.status != "ok"
        finally:
            await boot.stop()

    asyncio.run(_run())

    # No reads, no store writes.
    assert reader.calls == 0
    page = store.list(tenant_id="local")
    store.close()
    assert len(page.items) == 0
    assert boot._task is None  # no background task scheduled


def test_reflection_explicitly_off_is_noop(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_REFLECTION, "false")
    store = SqliteLearningStore(workspace_root=str(tmp_path))
    reader = FakeSessionReader(
        [_candidate_producing_session("s1", "2026-06-03T10:00:00Z")]
    )
    boot = LearningBootstrap(
        learning_store=store,
        session_reader=reader,
        app_name="magi",
        user_id="local-user",
    )
    asyncio.run(boot.start())
    try:
        assert boot.active is False
    finally:
        asyncio.run(boot.stop())
    assert reader.calls == 0
    store.close()


# ---------------------------------------------------------------------------
# 3. Fail-open
# ---------------------------------------------------------------------------


def test_run_once_failopen_on_reader_raise(tmp_path) -> None:
    store = SqliteLearningStore(workspace_root=str(tmp_path))
    boot = LearningBootstrap(
        learning_store=store,
        session_reader=ExplodingSessionReader(),
        app_name="magi",
        user_id="local-user",
    )

    async def _run() -> None:
        await boot.start()
        try:
            # Must NOT raise even though the reader explodes.
            result = await boot.run_once()
            assert result is None or result.status in {"error", "disabled"}
        finally:
            await boot.stop()

    asyncio.run(_run())
    store.close()


def test_start_failopen_on_store_factory_raise(tmp_path) -> None:
    def _boom() -> SqliteLearningStore:
        raise RuntimeError("cannot open learning DB")

    boot = LearningBootstrap(
        learning_store_factory=_boom,
        session_reader=FakeSessionReader([]),
        app_name="magi",
        user_id="local-user",
    )

    async def _run() -> None:
        # start() must not raise even though the store factory explodes.
        await boot.start()
        assert boot.active is False
        result = await boot.run_once()
        assert result is None
        await boot.stop()

    asyncio.run(_run())


def test_start_failopen_on_reader_factory_raise(tmp_path) -> None:
    store = SqliteLearningStore(workspace_root=str(tmp_path))

    def _boom() -> Any:
        raise RuntimeError("session DB missing on fresh install")

    boot = LearningBootstrap(
        learning_store=store,
        session_reader_factory=_boom,
        app_name="magi",
        user_id="local-user",
    )

    async def _run() -> None:
        await boot.start()
        assert boot.active is False
        await boot.stop()

    asyncio.run(_run())
    store.close()


# ---------------------------------------------------------------------------
# 4. Labeler selection
# ---------------------------------------------------------------------------


def test_llm_labeler_without_client_falls_back_to_deterministic(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(ENV_LABELER, "llm")
    store = SqliteLearningStore(workspace_root=str(tmp_path))
    reader = FakeSessionReader(
        [_candidate_producing_session("s1", "2026-06-03T10:00:00Z")]
    )
    boot = LearningBootstrap(
        learning_store=store,
        session_reader=reader,
        app_name="magi",
        user_id="local-user",
        # no model_client supplied
    )

    async def _run() -> None:
        await boot.start()
        try:
            assert boot.active is True
            assert boot.labeler_kind == "deterministic"  # fell back
            result = await boot.run_once()
            assert result is not None
            assert result.status == "ok"
        finally:
            await boot.stop()

    asyncio.run(_run())
    store.close()


# ---------------------------------------------------------------------------
# 5. injection/live remain off
# ---------------------------------------------------------------------------


def test_injection_and_live_stay_off_by_default() -> None:
    cfg = resolve_learning_config(env={})
    assert cfg.injection_effective is False
    assert cfg.live_effective is False


def test_run_once_has_no_injection_side_effects(tmp_path, monkeypatch) -> None:
    """A reflect pass writes only LOCAL items and never PROMPT-injects them.

    "Injection" here is prompt/behaviour injection (``injection_effective``),
    which stays config-gated OFF — distinct from the policy-gated store status.
    The eval gate may auto-activate non-rule *examples* (a safe, local,
    policy-gated write), but RULES never auto-activate, and the injection tier
    is never consulted by the reflect pass.
    """
    store = SqliteLearningStore(workspace_root=str(tmp_path))
    reader = FakeSessionReader(
        [_candidate_producing_session("s1", "2026-06-03T10:00:00Z")]
    )
    boot = LearningBootstrap(
        learning_store=store,
        session_reader=reader,
        app_name="magi",
        user_id="local-user",
    )

    async def _run() -> None:
        await boot.start()
        try:
            await boot.run_once()
        finally:
            await boot.stop()

    asyncio.run(_run())
    # The injection tier remains OFF for the default install.
    assert resolve_learning_config(env={}).injection_effective is False
    page = store.list(tenant_id="local")
    store.close()
    # Only safe local statuses; no RULE was auto-activated (rules await human
    # approval — no-direct-mutation policy).
    assert {item.status for item in page.items} <= {"proposed", "active"}
    assert all(
        item.status == "proposed" for item in page.items if item.kind == "rule"
    )


# ---------------------------------------------------------------------------
# 6. Non-reentrancy
# ---------------------------------------------------------------------------


def test_run_once_non_reentrant(tmp_path) -> None:
    store = SqliteLearningStore(workspace_root=str(tmp_path))
    reader = FakeSessionReader(
        [
            _candidate_producing_session("s1", "2026-06-03T10:00:00Z"),
            _candidate_producing_session("s2", "2026-06-03T11:00:00Z"),
        ]
    )
    boot = LearningBootstrap(
        learning_store=store,
        session_reader=reader,
        app_name="magi",
        user_id="local-user",
    )

    async def _run() -> None:
        await boot.start()
        try:
            # Fire two overlapping run_once() calls; the second must skip while
            # the first is still in flight (or serialize) — never double-process.
            results = await asyncio.gather(boot.run_once(), boot.run_once())
            # At least one ran; the watermark must be the max ts after the pass
            # (not corrupted by a concurrent advance).
            assert any(r is not None and r.status == "ok" for r in results)
            assert boot.watermark == "2026-06-03T11:00:00.000000Z"
        finally:
            await boot.stop()

    asyncio.run(_run())
    store.close()


# ---------------------------------------------------------------------------
# 7. stop() cancels cleanly
# ---------------------------------------------------------------------------


def test_stop_cancels_background_task_cleanly(tmp_path) -> None:
    store = SqliteLearningStore(workspace_root=str(tmp_path))
    reader = FakeSessionReader([])
    boot = LearningBootstrap(
        learning_store=store,
        session_reader=reader,
        app_name="magi",
        user_id="local-user",
    )

    async def _run() -> None:
        await boot.start()
        assert boot._task is not None
        await boot.stop()
        assert boot._task is None

    asyncio.run(_run())
    store.close()


def test_stop_is_idempotent_without_start(tmp_path) -> None:
    store = SqliteLearningStore(workspace_root=str(tmp_path))
    boot = LearningBootstrap(
        learning_store=store,
        session_reader=FakeSessionReader([]),
        app_name="magi",
        user_id="local-user",
    )
    # stop() before start() must be a harmless no-op.
    asyncio.run(boot.stop())
    store.close()


# ---------------------------------------------------------------------------
# 8. Frozen flags unchanged
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 9. app.py lifespan wiring — fail-open, never crashes startup
# ---------------------------------------------------------------------------


def _runtime():
    from magi_agent.config.models import BuildInfo, RuntimeConfig
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-test",
            user_id="user-test",
            gateway_token="gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-test", build_sha="sha-test"),
        )
    )


def test_create_app_lifespan_default_starts_and_stops(tmp_path, monkeypatch) -> None:
    """Default install: app starts (bootstrap active) and shuts down cleanly."""
    from fastapi.testclient import TestClient

    from magi_agent.app import create_app

    # Run from a temp cwd so the default session/learning DBs land in tmp.
    monkeypatch.chdir(tmp_path)
    app = create_app(_runtime())
    with TestClient(app) as client:  # runs lifespan start
        resp = client.get("/health")
        assert resp.status_code == 200


def test_create_app_lifespan_master_off(tmp_path, monkeypatch) -> None:
    """Master off: app still starts; bootstrap is inert."""
    from fastapi.testclient import TestClient

    from magi_agent.app import create_app

    monkeypatch.setenv(ENV_MASTER, "false")
    monkeypatch.chdir(tmp_path)
    app = create_app(_runtime())
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_create_app_lifespan_failopen_on_bootstrap_start(tmp_path, monkeypatch) -> None:
    """A bootstrap whose start() raises must NOT crash app startup."""
    from fastapi.testclient import TestClient

    import magi_agent.app as app_module
    from magi_agent.app import create_app

    class _ExplodingBootstrap:
        async def start(self) -> None:
            raise RuntimeError("boom in start")

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(
        app_module, "_build_learning_bootstrap", lambda _runtime: _ExplodingBootstrap()
    )
    monkeypatch.chdir(tmp_path)
    app = create_app(_runtime())
    with TestClient(app) as client:  # lifespan start must swallow the error
        assert client.get("/health").status_code == 200


def test_frozen_authority_flags_unchanged() -> None:
    from magi_agent.gates.learning_readiness import LearningReadinessConfig
    from magi_agent.harness.learning_executor import (
        LearningReflectionConfig,
        LearningReflectionResult,
    )

    rc = LearningReadinessConfig(reflectAuthority=True)  # forged truthy
    assert rc.reflect_authority is False

    ec = LearningReflectionConfig(
        llmAttached=True,
        productionWriteEnabled=True,
        realTranscriptSourceAttached=True,
    )
    assert ec.llm_attached is False
    assert ec.production_write_enabled is False
    assert ec.real_transcript_source_attached is False

    res = LearningReflectionResult(
        status="disabled",
        candidates=(),
        watermark=None,
        counters={},
        llmAttached=True,
        productionWriteEnabled=True,
        realTranscriptSourceAttached=True,
    )
    assert res.llm_attached is False
    assert res.production_write_enabled is False
    assert res.real_transcript_source_attached is False
