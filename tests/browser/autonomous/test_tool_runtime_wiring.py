import os
from unittest.mock import patch

from magi_agent.browser.autonomous.tool import BROWSER_TOOL_NAME
from magi_agent.cli.tool_runtime import build_cli_tool_runtime


def test_browser_tool_registered_by_default() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MAGI_BROWSER_TOOL_ENABLED", None)
        os.environ.pop("MAGI_BROWSER_TOOL_KILL_SWITCH", None)
        runtime = build_cli_tool_runtime(workspace_root="/tmp")
        assert runtime.registry.resolve(BROWSER_TOOL_NAME) is not None
        assert runtime.registry.is_enabled(BROWSER_TOOL_NAME)


def test_browser_tool_not_registered_when_explicitly_off() -> None:
    with patch.dict(os.environ, {"MAGI_BROWSER_TOOL_ENABLED": "0"}):
        runtime = build_cli_tool_runtime(workspace_root="/tmp")
        assert runtime.registry.resolve(BROWSER_TOOL_NAME) is None


def test_browser_tool_registered_when_enabled() -> None:
    with patch.dict(os.environ, {"MAGI_BROWSER_TOOL_ENABLED": "1"}):
        os.environ.pop("MAGI_BROWSER_TOOL_KILL_SWITCH", None)
        runtime = build_cli_tool_runtime(workspace_root="/tmp")
        assert runtime.registry.resolve(BROWSER_TOOL_NAME) is not None
        assert runtime.registry.is_enabled(BROWSER_TOOL_NAME)


def test_browser_tool_kill_switch_wins() -> None:
    with patch.dict(
        os.environ,
        {
            "MAGI_BROWSER_TOOL_ENABLED": "1",
            "MAGI_BROWSER_TOOL_KILL_SWITCH": "1",
        },
    ):
        runtime = build_cli_tool_runtime(workspace_root="/tmp")
        assert runtime.registry.resolve(BROWSER_TOOL_NAME) is None
