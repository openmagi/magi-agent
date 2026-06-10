"""Live-wiring seam for the native web tools.

Sits on top of the frozen not-configured contract (PR #381): a default
install keeps returning the honest ``web_research_not_configured`` error,
while a live-configured environment (master gate + provider router gate +
at least one provider source) must delegate to the env-assembled live
research boundary instead of erroring or fabricating results.
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.plugins.native.web import (
    WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE,
    _live_provider_configured,
    web_fetch,
    web_search,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

_LIVE_WEB_FLAGS = (
    "CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED",
    "CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_KILL_SWITCH",
    "CORE_AGENT_PYTHON_WEB_PROVIDER_ROUTER_ENABLED",
    "CORE_AGENT_PYTHON_JINA_READER_ENABLED",
    "CORE_AGENT_PYTHON_INSANE_FETCH_ENABLED",
    "MAGI_JINA_API_KEY",
    "MAGI_PLATFORM_BASE_URL",
    "MAGI_PLATFORM_API_KEY",
)


@pytest.fixture
def fresh_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure every live-web env flag/key is unset (fresh install)."""
    for name in _LIVE_WEB_FLAGS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def live_env(monkeypatch: pytest.MonkeyPatch, fresh_env: None) -> None:
    """Minimal live configuration: master gate + router gate + one provider."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_WEB_PROVIDER_ROUTER_ENABLED", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_JINA_READER_ENABLED", "1")


def _context() -> ToolContext:
    return ToolContext(botId="test-bot")


class _RecordingBoundary:
    """Stands in for the env-assembled live research boundary."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_tool(
        self,
        tool_name: str,
        arguments: object,
        context: object | None = None,
    ) -> ToolResult:
        self.calls.append(tool_name)
        return ToolResult(status="ok", output="live", llmOutput="live")


def test_live_provider_configured_false_on_fresh_install(fresh_env: None) -> None:
    assert _live_provider_configured() is False


def test_live_provider_configured_requires_router_gate(
    live_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CORE_AGENT_PYTHON_WEB_PROVIDER_ROUTER_ENABLED")
    assert _live_provider_configured() is False


def test_live_provider_configured_requires_a_provider_source(
    live_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CORE_AGENT_PYTHON_JINA_READER_ENABLED")
    assert _live_provider_configured() is False


def test_live_provider_configured_respects_kill_switch(
    live_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_KILL_SWITCH", "1")
    assert _live_provider_configured() is False


def test_live_provider_configured_with_platform_pair(
    live_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CORE_AGENT_PYTHON_JINA_READER_ENABLED")
    monkeypatch.setenv("MAGI_PLATFORM_BASE_URL", "https://platform.example")
    monkeypatch.setenv("MAGI_PLATFORM_API_KEY", "secret-key")
    assert _live_provider_configured() is True


def test_web_search_delegates_to_live_boundary_when_configured(
    live_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    boundary = _RecordingBoundary()
    # PR-2: the plugin now delegates via build_native_web_boundary, which returns
    # the env-assembled live boundary (or None → not-configured error).
    monkeypatch.setattr(
        "magi_agent.plugins.native.web.build_native_web_boundary",
        lambda env=None: boundary,
    )

    result = asyncio.run(web_search({"query": "openmagi"}, _context()))

    assert result.status == "ok"
    assert boundary.calls == ["WebSearch"]


def test_web_fetch_delegates_to_live_boundary_when_configured(
    live_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    boundary = _RecordingBoundary()
    monkeypatch.setattr(
        "magi_agent.plugins.native.web.build_native_web_boundary",
        lambda env=None: boundary,
    )

    result = asyncio.run(web_fetch({"url": "https://example.com/"}, _context()))

    assert result.status == "ok"
    assert boundary.calls == ["WebFetch"]


def test_unconfigured_install_keeps_frozen_error_contract(fresh_env: None) -> None:
    result = asyncio.run(web_search({"query": "openmagi"}, _context()))

    assert result.status == "error"
    assert result.error_code == WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE
    assert result.error_code == "web_research_not_configured"
