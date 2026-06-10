"""`magi gateway status` must report actual daemon liveness (pidfile probe),
not merely whether the env gate is set."""
from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

from magi_agent.cli.app import app
from magi_agent.gateway import pidfile

runner = CliRunner()


def test_status_reports_running_when_pidfile_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    pidfile.write_pidfile(pid=os.getpid())
    result = runner.invoke(app, ["gateway", "status"])
    assert result.exit_code == 0
    out = result.stdout.lower()
    assert "running" in out
    assert str(os.getpid()) in result.stdout


def test_status_reports_not_running_without_pidfile(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    result = runner.invoke(app, ["gateway", "status"])
    assert result.exit_code == 0
    out = result.stdout.lower()
    # gate enabled but no live daemon process
    assert "enabled" in out
    assert "not running" in out
