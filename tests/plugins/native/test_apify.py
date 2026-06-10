from __future__ import annotations

import asyncio
import io
import json
import urllib.error
import urllib.request

import pytest

from magi_agent.plugins.native import apify
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult


def _ctx() -> ToolContext:
    return ToolContext(botId="test-bot")


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._stream = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def read(self, n: int = -1) -> bytes:
        return self._stream.read(n)


class _CapturingOpener:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, **_: object) -> _FakeResponse:
        self.requests.append(request)
        return _FakeResponse(self.payload)


_STORE_FIXTURE = {
    "data": {
        "total": 2672,
        "items": [
            {
                "title": "Instagram Scraper",
                "name": "instagram-scraper",
                "username": "apify",
                "description": "Scrape Instagram posts, profiles, hashtags.",
                "categories": ["SOCIAL_MEDIA"],
                "stats": {"actorReviewRating": 4.68, "totalRuns": 140932837},
            },
            {  # malformed: missing username -> must be skipped
                "title": "Broken",
                "name": "broken",
                "stats": {},
            },
        ],
    }
}


def test_search_actors_parses_store_response(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _CapturingOpener(_STORE_FIXTURE)
    monkeypatch.setattr(urllib.request, "urlopen", opener)

    result = asyncio.run(apify.apify_search_actors({"query": "instagram scraper"}, _ctx()))

    assert isinstance(result, ToolResult)
    assert result.status == "ok"
    actors = result.output["actors"]
    assert len(actors) == 1  # malformed entry skipped
    assert actors[0]["actor_id"] == "apify~instagram-scraper"
    assert actors[0]["rating"] == 4.68
    assert actors[0]["total_runs"] == 140932837
    # query reached the store endpoint
    assert "search=instagram" in opener.requests[0].full_url


def test_search_actors_needs_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", _CapturingOpener(_STORE_FIXTURE))

    result = asyncio.run(apify.apify_search_actors({"query": "x"}, _ctx()))
    assert result.status == "ok"  # discovery works with no token


def test_search_actors_empty_query_is_bad_input() -> None:
    result = asyncio.run(apify.apify_search_actors({"query": "  "}, _ctx()))
    assert result.status == "error"
    assert result.error_code == "apify_bad_input"


def test_search_actors_network_failure_is_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: object, **_k: object) -> None:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    result = asyncio.run(apify.apify_search_actors({"query": "x"}, _ctx()))
    assert result.status == "error"
    assert result.error_code == "apify_unreachable"


def test_search_actors_missing_items_key_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _CapturingOpener({"data": {}}))
    result = asyncio.run(apify.apify_search_actors({"query": "x"}, _ctx()))
    assert result.status == "ok"
    assert result.output["actors"] == []


def test_search_actors_non_list_items_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _CapturingOpener({"data": {"items": 42}}))
    result = asyncio.run(apify.apify_search_actors({"query": "x"}, _ctx()))
    assert result.status == "ok"
    assert result.output["actors"] == []
