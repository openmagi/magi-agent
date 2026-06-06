from __future__ import annotations

from typer.testing import CliRunner


def _make_app():
    from magi_agent.cli.app import app

    return app


def _isolate_providers(monkeypatch, tmp_path) -> None:
    """Point config resolution at an empty temp file and clear provider keys."""
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent-config.toml"))
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "FIREWORKS_API_KEY",
        "MAGI_PROVIDER",
        "MAGI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_doctor_reports_no_provider(monkeypatch, tmp_path) -> None:
    _isolate_providers(monkeypatch, tmp_path)
    result = CliRunner().invoke(_make_app(), ["doctor"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "provider: NONE" in result.output
    # Actionable hint lists the supported provider env keys.
    assert "ANTHROPIC_API_KEY" in result.output
    # Config-file and workspace checks are always reported.
    assert "config file:" in result.output
    assert "workspace:" in result.output


def test_doctor_reports_configured_provider(monkeypatch, tmp_path) -> None:
    _isolate_providers(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    result = CliRunner().invoke(_make_app(), ["doctor"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "provider: OK (anthropic" in result.output
    # The secret value is never echoed.
    assert "sk-ant-test" not in result.output
