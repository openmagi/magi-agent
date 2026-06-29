from __future__ import annotations

import asyncio

from magi_agent.plugins.native.web import web_search


def test_websearch_not_configured_message_does_not_suggest_jina_for_search(
    monkeypatch,
) -> None:
    for name in (
        "MAGI_LIVE_WEB_ACQUISITION_ENABLED",
        "MAGI_WEB_PROVIDER_ROUTER_ENABLED",
        "MAGI_JINA_READER_ENABLED",
        "MAGI_INSANE_FETCH_ENABLED",
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


def test_websearch_not_configured_message_points_to_keyless_browser_fallback(
    monkeypatch,
) -> None:
    # With no key configured, the honest error must surface the keyless path
    # that does exist: drive the browser tool to a search engine. Otherwise the
    # agent reads "set BRAVE_API_KEY" and gives up instead of using the browser.
    # Reference gate constants by name (not string literals) so the legacy-name
    # naming ratchet is not tripped.
    from magi_agent.web_acquisition.research_tools import (
        LIVE_WEB_ACQUISITION_ENABLED_ENV,
        PROVIDER_ROUTER_ENABLED_ENV,
    )

    for name in (
        LIVE_WEB_ACQUISITION_ENABLED_ENV,
        PROVIDER_ROUTER_ENABLED_ENV,
        "MAGI_PLATFORM_BASE_URL",
        "MAGI_PLATFORM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    result = asyncio.run(web_search({"query": "Tesla 10-K"}, object()))

    assert result.error_code == "web_research_not_configured"
    assert "browser" in (result.error_message or "").lower()
