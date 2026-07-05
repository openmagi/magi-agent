"""M9 (missions x serve process-model) - co-locate the gateway daemon.

PR-M9 starts the gateway daemon INSIDE the serve ``create_app`` lifespan so
``python -m magi_agent`` (the hosted pod CMD and local ``magi serve``) runs the
watcher fleet (work_queue_executor + notify + mission_action_reconciler) in the
SAME process / event loop as uvicorn. Before this, the daemon was started ONLY
by the ``magi gateway`` CLI command, so a served install ran the FastAPI server
with no background executor and background WorkTasks never ran.

Hard guarantees asserted here:

* Gate ON (``MAGI_GATEWAY_DAEMON_ENABLED=1``) ⇒ lifespan builds the daemon and
  starts it as a supervised background asyncio task; shutdown sets the stop
  event and awaits the task (bounded, no hang).
* Gate OFF (unset) ⇒ NO daemon is built/started — byte-identical to today.
* Fail-open: a daemon build / create_task failure logs a warning and NEVER
  crashes app startup (the app still yields / serves ``/health``).
* Shutdown is bounded: a daemon whose ``run`` never returns on stop is cancelled
  by the wait_for timeout so shutdown cannot wedge.
* The local ``full`` profile turns the daemon flag ON (so a fresh ``magi serve``
  actually runs the executor); ``safe`` / ``eval`` keep it OFF.
"""

from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Runtime helper (mirrors the learning-bootstrap lifespan test).
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


# ---------------------------------------------------------------------------
# Fake daemons
# ---------------------------------------------------------------------------


class _RecordingDaemon:
    """Records run()/stop lifecycle; run() loops until stop_event is set."""

    def __init__(self) -> None:
        self.run_started = False
        self.stop_seen = False
        self.stop_event: asyncio.Event | None = None

    async def run(self, *, stop_event: asyncio.Event) -> None:
        self.run_started = True
        self.stop_event = stop_event
        await stop_event.wait()
        self.stop_seen = True


class _WedgingDaemon:
    """run() ignores the stop_event and never returns (shutdown-bound test)."""

    def __init__(self) -> None:
        self.run_started = False

    async def run(self, *, stop_event: asyncio.Event) -> None:
        self.run_started = True
        # Never observe stop_event: only a cancel can end this.
        await asyncio.Event().wait()


# ---------------------------------------------------------------------------
# 1. Gate ON: daemon starts in lifespan and stops cleanly on shutdown.
# ---------------------------------------------------------------------------


def test_lifespan_starts_and_stops_daemon_when_gate_on(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import magi_agent.gateway.daemon as daemon_mod
    from magi_agent.app import create_app

    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    monkeypatch.chdir(tmp_path)

    fake = _RecordingDaemon()
    monkeypatch.setattr(daemon_mod, "build_default_gateway_daemon", lambda: fake)

    app = create_app(_runtime())
    with TestClient(app) as client:  # runs lifespan start
        assert client.get("/health").status_code == 200
        # Daemon task started inside the lifespan event loop.
        assert fake.run_started is True
    # After the TestClient context exits, lifespan shutdown ran: stop_event set
    # and the run() coroutine observed it (clean, bounded shutdown).
    assert fake.stop_seen is True


# ---------------------------------------------------------------------------
# 2. Gate OFF: no daemon is built/started (byte-identical to today).
# ---------------------------------------------------------------------------


def test_lifespan_does_not_start_daemon_when_gate_off(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import magi_agent.gateway.daemon as daemon_mod
    from magi_agent.app import create_app

    monkeypatch.delenv("MAGI_GATEWAY_DAEMON_ENABLED", raising=False)
    monkeypatch.chdir(tmp_path)

    built = {"count": 0}

    def _boom():
        built["count"] += 1
        raise AssertionError("daemon must not be built when the gate is off")

    monkeypatch.setattr(daemon_mod, "build_default_gateway_daemon", _boom)

    app = create_app(_runtime())
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert built["count"] == 0


# ---------------------------------------------------------------------------
# 3. Fail-open: a daemon build/start failure never crashes startup.
# ---------------------------------------------------------------------------


def test_lifespan_failopen_on_daemon_build(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import magi_agent.gateway.daemon as daemon_mod
    from magi_agent.app import create_app

    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    monkeypatch.chdir(tmp_path)

    def _boom():
        raise RuntimeError("boom building daemon")

    monkeypatch.setattr(daemon_mod, "build_default_gateway_daemon", _boom)

    app = create_app(_runtime())
    with TestClient(app) as client:  # lifespan start must swallow the error
        assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------
# 4. Shutdown is bounded: a wedging daemon is cancelled, not awaited forever.
# ---------------------------------------------------------------------------


def test_lifespan_shutdown_is_bounded_for_wedging_daemon(
    tmp_path, monkeypatch
) -> None:
    from fastapi.testclient import TestClient

    import magi_agent.app as app_module
    import magi_agent.gateway.daemon as daemon_mod
    from magi_agent.app import create_app

    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    monkeypatch.chdir(tmp_path)

    # Shrink the shutdown timeout so the test is fast even though the daemon
    # never honours the stop event.
    monkeypatch.setattr(app_module, "_GATEWAY_SHUTDOWN_TIMEOUT_SECONDS", 0.2)

    fake = _WedgingDaemon()
    monkeypatch.setattr(daemon_mod, "build_default_gateway_daemon", lambda: fake)

    app = create_app(_runtime())
    # Exiting the context triggers lifespan shutdown; it must complete (bounded)
    # rather than hang, and must not raise TimeoutError/CancelledError.
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert fake.run_started is True
    # Reaching here means shutdown returned within the bounded timeout.


# ---------------------------------------------------------------------------
# 5. Local full profile turns the daemon flag ON; safe/eval keep it OFF.
# ---------------------------------------------------------------------------


def test_full_profile_enables_gateway_daemon(monkeypatch) -> None:
    from magi_agent.gateway.daemon import is_gateway_daemon_enabled
    from magi_agent.runtime.local_defaults import (
        LOCAL_FULL_RUNTIME_ENV_DEFAULTS,
        apply_local_full_runtime_defaults,
    )

    assert LOCAL_FULL_RUNTIME_ENV_DEFAULTS["MAGI_GATEWAY_DAEMON_ENABLED"] == "1"

    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    assert env["MAGI_GATEWAY_DAEMON_ENABLED"] == "1"

    # Prove ON through the REAL runtime reader (reads os.environ).
    monkeypatch.delenv("MAGI_GATEWAY_DAEMON_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", env["MAGI_GATEWAY_DAEMON_ENABLED"])
    assert is_gateway_daemon_enabled() is True


def test_safe_profile_does_not_enable_gateway_daemon(monkeypatch) -> None:
    from magi_agent.gateway.daemon import is_gateway_daemon_enabled
    from magi_agent.runtime.local_defaults import apply_local_full_runtime_defaults

    env = {"MAGI_RUNTIME_PROFILE": "safe"}
    apply_local_full_runtime_defaults(env)
    assert "MAGI_GATEWAY_DAEMON_ENABLED" not in env

    monkeypatch.delenv("MAGI_GATEWAY_DAEMON_ENABLED", raising=False)
    assert is_gateway_daemon_enabled() is False


def test_explicit_daemon_off_overrides_full_profile(monkeypatch) -> None:
    from magi_agent.gateway.daemon import is_gateway_daemon_enabled
    from magi_agent.runtime.local_defaults import apply_local_full_runtime_defaults

    # setdefault semantics: an explicit "0" wins (per-flag walk-back).
    env = {"MAGI_GATEWAY_DAEMON_ENABLED": "0"}
    apply_local_full_runtime_defaults(env)
    assert env["MAGI_GATEWAY_DAEMON_ENABLED"] == "0"

    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "0")
    assert is_gateway_daemon_enabled() is False
