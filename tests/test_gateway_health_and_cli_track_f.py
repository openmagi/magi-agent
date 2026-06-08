"""Track F — gateway health projection (ops/health.py additive) + CLI parsing."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from magi_agent.ops.health import (
    default_runtime_ops_health_metadata,
    gateway_daemon_health_projection,
)


# ---------------------------------------------------------------------------
# Health projection — additive, does not change existing metadata
# ---------------------------------------------------------------------------

def test_existing_health_metadata_unchanged() -> None:
    meta = default_runtime_ops_health_metadata()
    # spot-check the contract is intact (additive change must not touch this)
    assert meta["schemaVersion"] == "openmagi.ops.health.v1"
    assert meta["enabled"] is False


def test_gateway_health_projection_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_GATEWAY_DAEMON_ENABLED", raising=False)
    proj = gateway_daemon_health_projection(watcher_states={})
    assert proj["daemonEnabled"] is False
    assert proj["status"] == "disabled"
    assert proj["watchers"] == {}


def test_gateway_health_projection_states(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    proj = gateway_daemon_health_projection(
        watcher_states={
            "scheduler_cron": {"state": "running", "restarts": 0},
            "channel_telegram": {"state": "failed", "restarts": 3},
            "channel_discord": {"state": "disabled", "restarts": 0},
        }
    )
    assert proj["daemonEnabled"] is True
    assert proj["status"] == "running"
    assert proj["watchers"]["scheduler_cron"]["state"] == "running"
    assert proj["watchers"]["channel_telegram"]["state"] == "failed"
    assert proj["watchers"]["channel_discord"]["state"] == "disabled"
    # cron-ticker + per-channel states surfaced
    assert "cronTicker" in proj
    assert proj["cronTicker"]["state"] == "running"


def test_gateway_health_projection_redacts_unknown_keys() -> None:
    """Only whitelisted fields (state, restarts) survive — no raw secrets."""
    proj = gateway_daemon_health_projection(
        watcher_states={
            "channel_telegram": {
                "state": "running",
                "restarts": 0,
                "bot_token": "12345:SECRET",  # must NOT survive
            }
        }
    )
    tele = proj["watchers"]["channel_telegram"]
    assert "bot_token" not in tele
    assert tele["state"] == "running"


# ---------------------------------------------------------------------------
# CLI `magi gateway` subcommand parsing
# ---------------------------------------------------------------------------

runner = CliRunner()


def test_gateway_status_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    from magi_agent.cli.app import app

    monkeypatch.delenv("MAGI_GATEWAY_DAEMON_ENABLED", raising=False)
    result = runner.invoke(app, ["gateway", "status"])
    assert result.exit_code == 0
    assert "disabled" in result.stdout.lower()


def test_gateway_status_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from magi_agent.cli.app import app

    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    result = runner.invoke(app, ["gateway", "status"])
    assert result.exit_code == 0
    assert "enabled" in result.stdout.lower()


def test_gateway_install_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from magi_agent.cli.app import app

    target = tmp_path / "magi-gateway.service"
    result = runner.invoke(
        app,
        ["gateway", "install", "--target-path", str(target), "--manager", "systemd"],
    )
    assert result.exit_code == 0
    assert target.exists()


def test_gateway_unknown_action_errors() -> None:
    from magi_agent.cli.app import app

    result = runner.invoke(app, ["gateway", "frobnicate"])
    assert result.exit_code != 0


def test_gateway_start_gate_off_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """`gateway start` with the gate OFF must return immediately (no daemon)."""
    from magi_agent.cli.app import app

    monkeypatch.delenv("MAGI_GATEWAY_DAEMON_ENABLED", raising=False)
    result = runner.invoke(app, ["gateway", "start"])
    assert result.exit_code == 0
    assert "disabled" in result.stdout.lower() or "not enabled" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Single-source-of-truth: is_gateway_daemon_enabled() and
# gateway_daemon_health_projection()["daemonEnabled"] must NEVER diverge —
# including for garbage / non-canonical truthy values.
# ---------------------------------------------------------------------------

def test_gate_and_health_agree_on_garbage_truthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """`=garbage` → daemon gate ON; health must also report daemonEnabled=True."""
    from magi_agent.gateway.daemon import is_gateway_daemon_enabled

    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "garbage")
    gate_result = is_gateway_daemon_enabled()
    health_result = gateway_daemon_health_projection()["daemonEnabled"]
    assert gate_result is True, "is_gateway_daemon_enabled() should be True for 'garbage'"
    assert health_result is True, "daemonEnabled in health projection should match gate"
    assert gate_result == health_result, "gate and health must not diverge"


def test_gate_and_health_agree_on_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """`=off` → both gate and health report False."""
    from magi_agent.gateway.daemon import is_gateway_daemon_enabled

    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "off")
    gate_result = is_gateway_daemon_enabled()
    health_result = gateway_daemon_health_projection()["daemonEnabled"]
    assert gate_result is False
    assert health_result is False
    assert gate_result == health_result


def test_gate_and_health_agree_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset → both gate and health report False (default-OFF preserved)."""
    from magi_agent.gateway.daemon import is_gateway_daemon_enabled

    monkeypatch.delenv("MAGI_GATEWAY_DAEMON_ENABLED", raising=False)
    gate_result = is_gateway_daemon_enabled()
    health_result = gateway_daemon_health_projection()["daemonEnabled"]
    assert gate_result is False
    assert health_result is False
    assert gate_result == health_result
