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


def test_run_actor_without_token_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    result = asyncio.run(
        apify.apify_run_actor({"actor_id": "apify~instagram-scraper", "run_input": "{}"}, _ctx())
    )
    assert result.status == "error"
    assert result.error_code == apify.APIFY_NOT_CONFIGURED_ERROR_CODE


def test_run_actor_missing_actor_id_is_bad_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "tok_secret")
    result = asyncio.run(apify.apify_run_actor({"run_input": "{}"}, _ctx()))
    assert result.status == "error"
    assert result.error_code == "apify_bad_input"


def test_run_actor_bad_json_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "tok_secret")
    result = asyncio.run(
        apify.apify_run_actor({"actor_id": "apify~x", "run_input": "{not json"}, _ctx())
    )
    assert result.status == "error"
    assert result.error_code == "apify_bad_input"


def test_run_actor_success_sends_cost_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "tok_secret")
    monkeypatch.delenv("APIFY_MAX_USD_PER_RUN", raising=False)
    captured: list[urllib.request.Request] = []

    def _open(request: urllib.request.Request, **_: object) -> _FakeResponse:
        captured.append(request)
        return _FakeResponse([{"post": 1}, {"post": 2}])

    monkeypatch.setattr(urllib.request, "urlopen", _open)

    result = asyncio.run(
        apify.apify_run_actor(
            {"actor_id": "apify~instagram-scraper", "run_input": {"directUrls": ["u"]}}, _ctx()
        )
    )
    assert result.status == "ok"
    assert result.output["item_count"] == 2
    url = captured[0].full_url
    assert "run-sync-get-dataset-items" in url
    assert "apify~instagram-scraper" in url
    assert "maxTotalChargeUsd=1.0" in url  # default cap applied
    assert captured[0].method == "POST"


def test_run_actor_408_is_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "tok_secret")

    def _raise408(*_a: object, **_k: object) -> None:
        raise urllib.error.HTTPError("https://api.apify.com/x", 408, "timeout", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _raise408)
    result = asyncio.run(
        apify.apify_run_actor({"actor_id": "apify~x", "run_input": "{}"}, _ctx())
    )
    assert result.status == "error"
    assert result.error_code == "apify_run_timeout"


def test_run_actor_never_leaks_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "tok_SUPER_SECRET")

    def _raise(*_a: object, **_k: object) -> None:
        raise urllib.error.URLError("boom https://api.apify.com/...?token=tok_SUPER_SECRET")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    result = asyncio.run(
        apify.apify_run_actor({"actor_id": "apify~x", "run_input": "{}"}, _ctx())
    )
    assert result.status == "error"
    assert result.error_code == "apify_unreachable"
    assert "tok_SUPER_SECRET" not in repr(result.model_dump())


def test_run_actor_empty_max_usd_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "tok_secret")
    monkeypatch.setenv("APIFY_MAX_USD_PER_RUN", "")  # set-but-empty must NOT disable the cap
    captured: list[urllib.request.Request] = []

    def _open(request: urllib.request.Request, **_: object) -> _FakeResponse:
        captured.append(request)
        return _FakeResponse([{"post": 1}])

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    result = asyncio.run(
        apify.apify_run_actor({"actor_id": "apify~x", "run_input": "{}"}, _ctx())
    )
    assert result.status == "ok"
    assert "maxTotalChargeUsd=1.0" in captured[0].full_url


def test_run_actor_non_408_http_error_is_apify_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "tok_secret")

    def _raise403(*_a: object, **_k: object) -> None:
        raise urllib.error.HTTPError("https://api.apify.com/x", 403, "forbidden", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _raise403)
    result = asyncio.run(
        apify.apify_run_actor({"actor_id": "apify~x", "run_input": "{}"}, _ctx())
    )
    assert result.status == "error"
    assert result.error_code == "apify_error"
    assert result.metadata.get("http_status") == 403
