from magi_agent.computer.autonomous.tool import COMPUTER_TOOL_NAME
from magi_agent.tools.registry import ToolRegistry


def test_gated_off_does_not_register() -> None:
    from magi_agent.config.env import computer_tool_enabled

    assert computer_tool_enabled(env={}) is False


def test_register_and_bind_round_trip() -> None:
    from magi_agent.computer.autonomous.tool import (
        bind_computer_toolhost_handler,
        register_computer_tool_manifest,
    )

    registry = ToolRegistry()
    register_computer_tool_manifest(registry)
    bound = bind_computer_toolhost_handler(registry)
    assert bound == (COMPUTER_TOOL_NAME,)
    reg = registry.resolve_registration(COMPUTER_TOOL_NAME)
    assert reg is not None and reg.manifest.permission == "computer"
