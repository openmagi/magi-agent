from __future__ import annotations

import asyncio

from magi_agent.plugins.native.web import web_search


def test_websearch_not_configured_message_does_not_suggest_jina_for_search(
    monkeypatch,
) -> None:
    for name in (
        "CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED",
        "CORE_AGENT_PYTHON_WEB_PROVIDER_ROUTER_ENABLED",
        "CORE_AGENT_PYTHON_JINA_READER_ENABLED",
        "CORE_AGENT_PYTHON_INSANE_FETCH_ENABLED",
        "MAGI_JINA_API_KEY",
        "MAGI_PLATFORM_BASE_URL",
        "MAGI_PLATFORM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    result = asyncio.run(web_search({"query": "Tesla 10-K"}, object()))

    assert result.status == "error"
    assert result.error_code == "web_research_not_configured"
    assert "MAGI_JINA_API_KEY" not in (result.error_message or "")
    assert "Jina" not in (result.error_message or "")
    assert "search provider" in (result.error_message or "")
