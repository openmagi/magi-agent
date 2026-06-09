from __future__ import annotations

import asyncio
import importlib.util

import magi_agent.browser.autonomous.tool as tool_module
from magi_agent.browser.autonomous.tool import (
    BROWSER_TOOL_NAME,
    _browser_task_handler,
    bind_browser_toolhost_handler,
    context_profile_dir,
    register_browser_tool_manifest,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.registry import ToolRegistry


def test_manifest_registers() -> None:
    registry = ToolRegistry()
    register_browser_tool_manifest(registry)
    assert registry.resolve_registration(BROWSER_TOOL_NAME) is not None


def test_binding_returns_tool_name() -> None:
    registry = ToolRegistry()
    register_browser_tool_manifest(registry)
    bound = bind_browser_toolhost_handler(registry)
    assert BROWSER_TOOL_NAME in bound


def test_binding_without_registration_returns_empty() -> None:
    registry = ToolRegistry()
    assert bind_browser_toolhost_handler(registry) == ()


def test_context_profile_dir_uses_workspace_root() -> None:
    context = ToolContext(botId="test", workspaceRoot="/work")
    assert context_profile_dir(context) == "/work/.magi-browser-profile"


def test_context_profile_dir_defaults_to_tmp() -> None:
    context = ToolContext(botId="test")
    assert context_profile_dir(context) == "/tmp/.magi-browser-profile"


def test_handler_missing_task() -> None:
    # With empty args the handler returns a non-"ok" no-op early result
    # before any network: either "error"/missing_task (browser_use present) or
    # "blocked"/browser_extra_missing (extra absent). Deterministic regardless.
    result = asyncio.run(_browser_task_handler({}, ToolContext(botId="test")))
    assert result.status in {"error", "blocked"}
    if result.status == "error":
        assert result.error_code == "missing_task"
    else:
        assert result.error_code == "browser_extra_missing"


def test_handler_no_provider_returns_blocked(monkeypatch) -> None:
    # The browser extra is installed in this worktree; with a real task but no
    # provider configured, build_chat_model raises BridgeError -> blocked.
    if importlib.util.find_spec("browser_use") is None:
        import pytest  # noqa: PLC0415

        pytest.skip("browser extra not installed")

    monkeypatch.setattr(
        "magi_agent.cli.providers.resolve_provider_config",
        lambda *args, **kwargs: None,
    )
    result = asyncio.run(
        _browser_task_handler({"task": "do a thing"}, ToolContext(botId="test"))
    )
    assert result.status == "blocked"
    assert result.error_code == "no_provider"


def test_blocked_run_metadata_is_surfaced_sanitized(monkeypatch) -> None:
    # A blocked engine result carrying violation metadata must reach the
    # ToolResult, sanitized (raw URLs dropped, policy reasons kept). Network-free:
    # we stub the provider resolution, chat-model build, and the engine run.
    if importlib.util.find_spec("browser_use") is None:
        import pytest  # noqa: PLC0415

        pytest.skip("browser extra not installed")

    from magi_agent.browser.autonomous.engine import BrowserRunResult  # noqa: PLC0415

    monkeypatch.setattr(
        "magi_agent.cli.providers.resolve_provider_config",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "magi_agent.browser.autonomous.provider_bridge.build_chat_model",
        lambda _cfg: object(),
    )

    async def _fake_run(self, **kwargs):
        return BrowserRunResult(
            status="blocked",
            error_code="invalid_url",
            metadata={
                "violations": [
                    {"url": "http://127.0.0.1/", "reason": "local_url_blocked"}
                ]
            },
        )

    monkeypatch.setattr(
        "magi_agent.browser.autonomous.engine.BrowserEngine.run",
        _fake_run,
    )

    result = asyncio.run(
        _browser_task_handler(
            {"task": "go somewhere blocked"}, ToolContext(botId="test")
        )
    )

    assert result.status == "blocked"
    assert result.error_code == "invalid_url"
    # Metadata surfaced and non-empty...
    assert result.metadata
    # ...but the raw blocked URL is NOT leaked verbatim anywhere in it.
    assert "127.0.0.1" not in str(result.metadata)
    # ...and the non-sensitive policy reason IS carried through.
    assert "local_url_blocked" in str(result.metadata)


def test_module_does_not_import_browser_use_at_top() -> None:
    import sys  # noqa: PLC0415

    source = open(tool_module.__file__, encoding="utf-8").read()
    # browser_use must only be referenced via find_spec / lazy imports, never a
    # top-level `import browser_use`.
    assert "\nimport browser_use" not in source
    assert "\nfrom browser_use" not in source
