"""CliRunner tests for the `legalbench` subcommand in cli/app.py."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner


def _make_app():
    """Import and return the Typer app (deferred to keep test module import cheap)."""
    from magi_agent.cli.app import app
    return app


class TestLegalbenchGateOff:
    def test_gate_off_exits_1_and_shows_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """legalbench subcommand must exit 1 and print the gate-disabled message when env is unset."""
        monkeypatch.delenv("MAGI_LEGAL_HARNESS_ENABLED", raising=False)

        runner = CliRunner()
        result = runner.invoke(_make_app(), ["legalbench"], catch_exceptions=False)

        assert result.exit_code == 1
        # The gate-disabled message is echoed to stderr; CliRunner merges it into output.
        assert "MAGI_LEGAL_HARNESS_ENABLED" in result.output
