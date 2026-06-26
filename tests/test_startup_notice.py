"""Unit tests for the local serve startup notice.

The notice tells a fresh ``magi serve`` operator whether chat is ready and,
when no provider is configured, how to finish setup. With the onboarding
wizard flag ON it points at the dashboard (no restart); with it OFF it keeps
the env-var / config.toml guidance.
"""

from __future__ import annotations

from unittest.mock import patch

import magi_agent.main as main_module


def _notice(capsys, port: int = 8080) -> str:
    main_module._print_local_startup_notice(port)
    return capsys.readouterr().err


def test_notice_provider_ready(capsys) -> None:
    fake = type("P", (), {"provider": "anthropic", "model": "claude-sonnet-4-6"})()
    with patch("magi_agent.cli.providers.resolve_provider_config", return_value=fake):
        out = _notice(capsys)
    assert "Chat is ready." in out
    assert "Finish setup in the dashboard" not in out


def test_notice_no_provider_wizard_on_points_at_dashboard(monkeypatch, capsys) -> None:
    monkeypatch.setenv("MAGI_ONBOARDING_WIZARD_ENABLED", "1")
    with patch("magi_agent.cli.providers.resolve_provider_config", return_value=None):
        out = _notice(capsys, port=8080)
    assert "Finish setup in the dashboard: http://localhost:8080/dashboard" in out
    assert "no restart needed" in out
    # The wizard path supersedes the restart-serve guidance.
    assert "restart serve" not in out


def test_notice_no_provider_wizard_off_keeps_env_guidance(monkeypatch, capsys) -> None:
    monkeypatch.delenv("MAGI_ONBOARDING_WIZARD_ENABLED", raising=False)
    with patch("magi_agent.cli.providers.resolve_provider_config", return_value=None):
        out = _notice(capsys)
    assert "ANTHROPIC_API_KEY" in out
    assert "restart serve" in out
    assert "Finish setup in the dashboard" not in out


def test_notice_has_no_em_dash_in_added_lines(monkeypatch, capsys) -> None:
    monkeypatch.setenv("MAGI_ONBOARDING_WIZARD_ENABLED", "1")
    with patch("magi_agent.cli.providers.resolve_provider_config", return_value=None):
        out = _notice(capsys)
    # The provider-status + wizard lines this feature owns must be em-dash free.
    for line in out.splitlines():
        if "Model provider:" in line or "Finish setup" in line:
            assert "—" not in line
