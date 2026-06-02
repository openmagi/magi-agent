from __future__ import annotations

import pytest

from openmagi_core_agent import main as main_module
from openmagi_core_agent.main import resolve_server_port


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
