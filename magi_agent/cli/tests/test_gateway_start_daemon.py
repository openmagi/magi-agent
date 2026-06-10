"""`magi gateway start` must supervise (GatewayDaemon.run) by default and
keep the legacy single-tick behavior behind --once."""
from __future__ import annotations

from typer.testing import CliRunner

from magi_agent.cli.app import app

runner = CliRunner()


def test_gateway_start_default_runs_daemon(monkeypatch):
    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    calls = {}

    async def fake_run(self, *, stop_event):
        calls["ran"] = True

    monkeypatch.setattr(
        "magi_agent.gateway.daemon.GatewayDaemon.run", fake_run
    )
    result = runner.invoke(app, ["gateway", "start"])
    assert result.exit_code == 0
    assert calls.get("ran") is True


def test_gateway_start_once_keeps_single_tick(monkeypatch):
    monkeypatch.setenv("MAGI_GATEWAY_DAEMON_ENABLED", "1")
    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")

    class FakeTickResult:
        status = "ok"
        fired_job_ids = ()
        skipped_job_ids = ()

    class FakeResult:
        tick_result = FakeTickResult()
        executions = ()

    class FakeDriver:
        def run_once(self):
            return FakeResult()

    monkeypatch.setattr(
        "magi_agent.gateway.watchers.build_local_scheduler_cron_driver",
        lambda: FakeDriver(),
    )
    result = runner.invoke(app, ["gateway", "start", "--once"])
    assert result.exit_code == 0
    assert "scheduler_cron" in result.output
