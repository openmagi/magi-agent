import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

from magi_agent.browser.autonomous.tool import BROWSER_TOOL_NAME
from magi_agent.cli.tool_runtime import build_cli_tool_runtime


def _adk_ctx(tool_name: str) -> object:
    return SimpleNamespace(function_call=SimpleNamespace(name=tool_name, id="call-1"))


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


def test_browser_task_bypass_scope_does_not_request_approval() -> None:
    with patch.dict(
        os.environ,
        {
            "MAGI_BROWSER_TOOL_ENABLED": "1",
            "MAGI_PERMISSION_SCOPE_FROM_MODE": "1",
        },
        clear=False,
    ):
        os.environ.pop("MAGI_BROWSER_TOOL_KILL_SWITCH", None)
        runtime = build_cli_tool_runtime(
            workspace_root="/tmp",
            permission_mode="bypassPermissions",
        )
        context = runtime.tool_context_factory(_adk_ctx(BROWSER_TOOL_NAME))

        result = asyncio.run(
            runtime.dispatcher.dispatch(
                BROWSER_TOOL_NAME,
                {"task": "open https://example.com"},
                context,
                mode="act",
            )
        )

    assert result.status != "needs_approval"
    assert result.metadata.get("reason") != "net permission requires approval"


def test_browser_task_explicit_default_scope_still_requests_approval() -> None:
    with patch.dict(
        os.environ,
        {
            "MAGI_BROWSER_TOOL_ENABLED": "1",
            "MAGI_PERMISSION_SCOPE_FROM_MODE": "1",
        },
        clear=False,
    ):
        os.environ.pop("MAGI_BROWSER_TOOL_KILL_SWITCH", None)
        runtime = build_cli_tool_runtime(
            workspace_root="/tmp",
            permission_mode="default",
        )
        context = runtime.tool_context_factory(_adk_ctx(BROWSER_TOOL_NAME))

        result = asyncio.run(
            runtime.dispatcher.dispatch(
                BROWSER_TOOL_NAME,
                {"task": "open https://example.com"},
                context,
                mode="act",
            )
        )

    assert result.status == "needs_approval"
    assert result.metadata.get("reason") == "net permission requires approval"


def test_browser_task_bypass_scope_does_not_override_plan_mode() -> None:
    with patch.dict(
        os.environ,
        {
            "MAGI_BROWSER_TOOL_ENABLED": "1",
            "MAGI_PERMISSION_SCOPE_FROM_MODE": "1",
        },
        clear=False,
    ):
        os.environ.pop("MAGI_BROWSER_TOOL_KILL_SWITCH", None)
        runtime = build_cli_tool_runtime(
            workspace_root="/tmp",
            permission_mode="bypassPermissions",
        )
        context = runtime.tool_context_factory(_adk_ctx(BROWSER_TOOL_NAME))

        result = asyncio.run(
            runtime.dispatcher.dispatch(
                BROWSER_TOOL_NAME,
                {"task": "open https://example.com"},
                context,
                mode="plan",
            )
        )

    assert result.status == "needs_approval"
    assert result.metadata.get("reason") == "net permission requires approval"
