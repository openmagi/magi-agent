from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import magi_agent
from magi_agent import main as main_module
from magi_agent.config.env import RuntimeEnvError
from magi_agent.main import resolve_server_port

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_release_version_and_local_health_version_are_aligned() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    version = pyproject["project"]["version"]

    assert version == magi_agent.__version__
    config = main_module._parse_runtime_config({})  # noqa: SLF001
    assert config.build.version == f"{version}-local"


def test_resolve_server_port_uses_core_agent_port_when_no_args() -> None:
    assert resolve_server_port([], environ={"CORE_AGENT_PORT": "9090"}) == 9090


def test_resolve_server_port_supports_serve_port_command() -> None:
    assert resolve_server_port(["serve", "--port", "9091"], environ={}) == 9091


def test_resolve_server_port_supports_direct_port_option() -> None:
    assert resolve_server_port(["--port", "9092"], environ={}) == 9092


def test_resolve_server_port_rejects_unknown_commands() -> None:
    with pytest.raises(SystemExit):
        resolve_server_port(["run"], environ={})


def test_main_help_does_not_require_runtime_environment(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main_module.main(["--help"])

    assert exc_info.value.code == 0
    assert "usage: magi-agent" in capsys.readouterr().out


def test_main_serve_help_does_not_require_runtime_environment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main_module.main(["serve", "--help"])

    assert exc_info.value.code == 0
    assert "usage: magi-agent" in capsys.readouterr().out


def test_main_uses_local_runtime_defaults_when_env_is_absent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.delenv("MAGI_AGENT_LOCAL_CHAT_ROUTE", raising=False)
    monkeypatch.setattr(main_module.uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    main_module.main(["serve", "--port", "9093"])

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9093
    assert main_module.os.environ["MAGI_AGENT_LOCAL_CHAT_ROUTE"] == "on"


def test_main_can_require_runtime_environment(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_AGENT_REQUIRE_ENV", "1")

    with pytest.raises(RuntimeEnvError, match="Missing required runtime env"):
        main_module.main(["serve", "--port", "9094"])
