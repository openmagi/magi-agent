from __future__ import annotations

from typer.testing import CliRunner


def _make_app():
    from magi_agent.cli.app import app

    return app


def test_doctor_reports_composio_default_auto_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.delenv("MAGI_COMPOSIO_ENABLED", raising=False)
    result = CliRunner().invoke(_make_app(), ["doctor"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "Composio: inactive" in result.output
    assert "not_configured" in result.output
    assert "COMPOSIO_API_KEY" in result.output


def test_doctor_reports_composio_missing_key_when_enabled(monkeypatch) -> None:
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.setenv("MAGI_COMPOSIO_ENABLED", "on")
    result = CliRunner().invoke(_make_app(), ["doctor"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "Composio: inactive" in result.output
    assert "missing_api_key" in result.output
    assert "COMPOSIO_API_KEY" in result.output


def test_doctor_reports_composio_active_without_leaking_key(monkeypatch) -> None:
    monkeypatch.setenv("COMPOSIO_API_KEY", "cp_test_secret")
    monkeypatch.setenv("MAGI_COMPOSIO_ENABLED", "on")
    monkeypatch.setattr(
        "magi_agent.composio.health.composio_package_available",
        lambda: True,
    )
    result = CliRunner().invoke(_make_app(), ["doctor"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "Composio: active" in result.output
    assert "cp_test_secret" not in result.output


def test_auth_composio_status_reports_active(monkeypatch) -> None:
    monkeypatch.setenv("COMPOSIO_API_KEY", "cp_test_secret")
    monkeypatch.setenv("MAGI_COMPOSIO_ENABLED", "on")
    monkeypatch.setattr(
        "magi_agent.composio.health.composio_package_available",
        lambda: True,
    )
    result = CliRunner().invoke(
        _make_app(),
        ["auth", "composio", "status"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "Composio auth: active" in result.output
    assert "cp_test_secret" not in result.output


def test_doctor_reports_composio_missing_package(monkeypatch) -> None:
    monkeypatch.setenv("COMPOSIO_API_KEY", "cp_test_secret")
    monkeypatch.setenv("MAGI_COMPOSIO_ENABLED", "on")
    monkeypatch.setattr(
        "magi_agent.composio.health.composio_package_available",
        lambda: False,
    )
    result = CliRunner().invoke(_make_app(), ["doctor"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "Composio: inactive" in result.output
    assert "missing_python_package" in result.output
    assert "optional extra" in result.output
    assert "cp_test_secret" not in result.output
