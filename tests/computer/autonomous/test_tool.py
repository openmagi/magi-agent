import asyncio

from magi_agent.computer.autonomous.tool import (
    COMPUTER_TOOL_NAME,
    _computer_task_handler,
    _consent_from_context,
    bind_computer_toolhost_handler,
    register_computer_tool_manifest,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.registry import ToolRegistry


def test_manifest_registers() -> None:
    registry = ToolRegistry()
    register_computer_tool_manifest(registry)
    assert registry.resolve_registration(COMPUTER_TOOL_NAME) is not None


def test_binding_returns_tool_name() -> None:
    registry = ToolRegistry()
    register_computer_tool_manifest(registry)
    assert COMPUTER_TOOL_NAME in bind_computer_toolhost_handler(registry)


def test_binding_without_registration_returns_empty() -> None:
    assert bind_computer_toolhost_handler(ToolRegistry()) == ()


def test_handler_missing_binary_blocks(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    result = asyncio.run(
        _computer_task_handler({"task": "x"}, ToolContext(botId="t"))
    )
    assert result.status == "blocked"
    assert result.error_code == "cua_driver_missing"


def test_handler_missing_task(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/cua-driver")
    result = asyncio.run(_computer_task_handler({}, ToolContext(botId="t")))
    assert result.status == "error"
    assert result.error_code == "missing_task"


def test_handler_no_provider_blocks(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/cua-driver")
    monkeypatch.setattr(
        "magi_agent.cli.providers.resolve_provider_config",
        lambda *a, **k: None,
    )
    result = asyncio.run(
        _computer_task_handler({"task": "x"}, ToolContext(botId="t"))
    )
    assert result.status == "blocked"
    assert result.error_code == "no_provider"


def test_consent_deny_when_no_ask_user() -> None:
    consent = _consent_from_context(ToolContext(botId="t"))
    assert asyncio.run(consent("type: password")) is False
