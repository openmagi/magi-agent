from __future__ import annotations

import asyncio

import pytest

from magi_agent.plugins.native.web import (
    WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE,
    _not_configured_result,
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


def _context() -> ToolContext:
    return ToolContext(botId="test-bot")


def test_web_search_not_configured_returns_honest_error(fresh_env: None) -> None:
    result = asyncio.run(web_search({"query": "tallest mountain"}, _context()))

    assert isinstance(result, ToolResult)
    assert result.status == "error"
    assert result.error_code == WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE
    assert result.error_code == "web_research_not_configured"


def test_web_fetch_not_configured_returns_honest_error(fresh_env: None) -> None:
    result = asyncio.run(web_fetch({"url": "https://example.com/"}, _context()))

    assert isinstance(result, ToolResult)
    assert result.status == "error"
    assert result.error_code == WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE


def test_no_fabricated_stub_in_search_output(fresh_env: None) -> None:
    result = asyncio.run(web_search({"query": "tallest mountain"}, _context()))

    rendered = repr(result.model_dump())
    assert "search://" not in rendered
    assert "stub" not in rendered.lower()
    assert "tallest mountain" not in rendered  # no fabricated result for the query


def test_no_fabricated_stub_in_fetch_output(fresh_env: None) -> None:
    result = asyncio.run(web_fetch({"url": "https://example.com/"}, _context()))

    rendered = repr(result.model_dump())
    assert "stub" not in rendered.lower()
    assert "Local fetched source" not in rendered


def test_error_message_names_activation_flags(fresh_env: None) -> None:
    result = asyncio.run(web_search({"query": "x"}, _context()))

    message = result.error_message or ""
    assert "CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED" in message
    assert "CORE_AGENT_PYTHON_JINA_READER_ENABLED" in message


def test_not_configured_result_helper_contract() -> None:
    result = _not_configured_result("WebSearch")

    assert isinstance(result, ToolResult)
    assert result.status == "error"
    assert result.error_code == "web_research_not_configured"
    assert result.metadata.get("tool") == "WebSearch"
