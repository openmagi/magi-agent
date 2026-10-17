"""Exercise native WebSearch through the real MCP SDK with hermetic HTTP."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import anyio
import httpx
import pytest

from magi_agent import __version__
from magi_agent.plugins.native.web import web_search
from magi_agent.tools.context import ToolContext
from magi_agent.web_acquisition.providers.parallel_search import (
    PARALLEL_MCP_URL,
    ParallelSearchProvider,
)
from magi_agent.web_acquisition.research_tools import build_native_web_boundary

ENV = {
    "MAGI_LIVE_WEB_ACQUISITION_ENABLED": "1",
    "MAGI_WEB_PROVIDER_ROUTER_ENABLED": "1",
    "MAGI_PARALLEL_SEARCH_ENABLED": "1",
}
SEARCH = {
    "results": [
        {
            "title": "Magi documentation",
            "url": "https://openmagi.ai/docs",
            "excerpts": ["Magi is a governed agent runtime.", "It supports web research."],
        }
    ]
}


class McpTransport(httpx.MockTransport):
    def __init__(self, *, result=None, failure=None, text_only=False):
        self.requests = []
        self.result = SEARCH if result is None else result
        self.failure = failure
        self.text_only = text_only
        self.closed = False
        self.cancelled = False
        super().__init__(self.handle)

    async def handle(self, request):
        self.requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(200)
        if request.method == "GET":
            return httpx.Response(405)
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            result = {
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif msg["method"] == "tools/list":
            result = {"tools": [{"name": "web_search", "inputSchema": {"type": "object"}}]}
        else:
            assert msg["method"] == "tools/call"
            if self.failure == "timeout":
                try:
                    await anyio.sleep_forever()
                finally:
                    self.cancelled = True
            if self.failure == "http":
                return httpx.Response(503)
            if self.failure == "protocol":
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32603, "message": "private provider detail"},
                    },
                )
            result = {
                "isError": self.failure == "tool",
                "content": [{"type": "text", "text": json.dumps(self.result)}],
            }
            if not self.text_only:
                result["structuredContent"] = self.result
        return httpx.Response(
            200,
            headers={"Mcp-Session-Id": "fixture-session"},
            json={
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": result,
            },
        )

    async def aclose(self):
        self.closed = True
        await super().aclose()


def native_search(monkeypatch, transport, *, timeout_s=2):
    monkeypatch.setattr("magi_agent.plugins.native.web.os.environ", ENV)
    monkeypatch.setattr(
        "magi_agent.web_acquisition.providers.parallel_search.ParallelSearchProvider",
        lambda: ParallelSearchProvider(transport=transport, timeout_s=timeout_s),
    )
    return asyncio.run(
        web_search(
            {"query": "Magi agent documentation"},
            ToolContext(botId="test-bot", sessionKey="test-session", turnId="test-turn"),
        )
    )


@pytest.mark.parametrize("text_only", [False, True])
def test_native_search_projects_useful_anonymous_results(monkeypatch, text_only):
    transport = McpTransport(text_only=text_only)
    result = native_search(monkeypatch, transport)
    assert result.status == "ok"
    assert result.output["providerId"] == "parallel.search"
    assert result.output["resultRefs"]
    assert result.output["sources"][0]["title"] == "Magi documentation"
    assert "https://openmagi.ai/docs" in result.output["publicPreview"]
    assert "governed agent runtime" in result.llm_output["publicPreview"]
    assert transport.closed
    assert any(r.method == "DELETE" for r in transport.requests)
    calls = []
    for request in transport.requests:
        assert str(request.url) == PARALLEL_MCP_URL
        assert request.headers["user-agent"] == f"magi-agent/{__version__}"
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        if request.method == "POST":
            msg = json.loads(request.content)
            if msg["method"] == "tools/call":
                calls.append(msg["params"])
    assert len(calls) == 1
    assert calls[0]["name"] == "web_search"
    assert calls[0]["arguments"]["objective"] == "Magi agent documentation"
    assert calls[0]["arguments"]["search_queries"] == ["Magi agent documentation"]
    assert len(calls[0]["arguments"]["session_id"]) == 64
    assert "test-session" not in calls[0]["arguments"]["session_id"]


@pytest.mark.parametrize("failure", ["tool", "protocol", "http", "timeout"])
def test_native_search_contains_errors_and_cancels_timed_out_work(monkeypatch, failure):
    transport = McpTransport(failure=failure)
    result = native_search(monkeypatch, transport, timeout_s=0.15 if failure == "timeout" else 2)
    assert result.status != "ok"
    assert "private provider detail" not in str(result)
    assert transport.closed
    if failure == "timeout":
        assert transport.cancelled


@pytest.mark.parametrize("data", [[], {"wrong": []}, {"results": "bad"}])
def test_malformed_payload_is_an_error(monkeypatch, data):
    result = native_search(monkeypatch, McpTransport(result=data))
    assert result.status != "ok"


def test_no_matches_does_not_fabricate_a_source(monkeypatch):
    result = native_search(monkeypatch, McpTransport(result={"results": []}))
    assert result.status != "ok"
    assert not (result.output or {}).get("sources")


def test_invalid_query_makes_no_request():
    transport = McpTransport()
    provider = ParallelSearchProvider(transport=transport)
    assert provider.search(SimpleNamespace(query=" ")) == {"status": "denied"}
    assert not transport.requests


@pytest.mark.parametrize(
    "override",
    [
        {"MAGI_PARALLEL_SEARCH_ENABLED": "0"},
        {"MAGI_PARALLEL_SEARCH_ENABLED": "invalid"},
        {"MAGI_LIVE_WEB_ACQUISITION_ENABLED": "0"},
        {"MAGI_WEB_PROVIDER_ROUTER_ENABLED": "0"},
        {"MAGI_LIVE_WEB_ACQUISITION_KILL_SWITCH": "1"},
    ],
)
def test_explicit_disabled_gates_preserved(override):
    assert build_native_web_boundary({**ENV, **override}) is None


def test_default_and_platform_priority_preserved():
    assert build_native_web_boundary({}) is None
    boundary = build_native_web_boundary(
        {
            **ENV,
            "MAGI_PLATFORM_BASE_URL": "https://platform.example",
            "MAGI_PLATFORM_API_KEY": "fixture",
        }
    )
    assert boundary._provider_router.config.providers == (
        "platform.search",
        "platform.fetch",
        "parallel.search",
    )


def test_unavailable_optional_sdk_returns_safe_error(monkeypatch):
    import builtins

    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "mcp":
            raise ImportError("fixture missing optional extra")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    provider = ParallelSearchProvider()
    assert provider.search(SimpleNamespace(query="query")) == {
        "status": "timeout",
        "reason": "parallel_search_failed",
    }


def test_configured_platform_search_wins_without_parallel_request(monkeypatch):
    transport = McpTransport()
    monkeypatch.setattr(
        "magi_agent.plugins.native.web.os.environ",
        {
            **ENV,
            "MAGI_PLATFORM_BASE_URL": "https://platform.example",
            "MAGI_PLATFORM_API_KEY": "fixture",
        },
    )
    monkeypatch.setattr(
        "magi_agent.web_acquisition.providers.platform_endpoint.PlatformEndpointProvider.search",
        lambda self, request: {
            "results": [
                {
                    "url": "https://example.com",
                    "title": "Platform result",
                    "snippet": "answer",
                }
            ]
        },
    )
    monkeypatch.setattr(
        "magi_agent.web_acquisition.providers.parallel_search.ParallelSearchProvider",
        lambda: ParallelSearchProvider(transport=transport),
    )
    result = asyncio.run(web_search({"query": "question"}, ToolContext(botId="test-bot")))
    assert result.status == "ok"
    assert result.output["providerId"] == "platform.search"
    assert transport.requests == []


def test_related_calls_reuse_session_identifier():
    sessions = []
    for query, session in [
        ("first", "session-one"),
        ("second", "session-one"),
        ("third", "session-two"),
    ]:
        transport = McpTransport()
        provider = ParallelSearchProvider(transport=transport)
        result = provider.search(SimpleNamespace(query=query, session_key_digest=session))
        assert "results" in result
        messages = [json.loads(r.content) for r in transport.requests if r.method == "POST"]
        call = next(m for m in messages if m["method"] == "tools/call")
        sessions.append(call["params"]["arguments"]["session_id"])
    assert sessions[0] == sessions[1]
    assert sessions[0] != sessions[2]


def test_private_urls_and_invalid_entries_are_not_returned(monkeypatch):
    payload = {
        "results": [
            {"url": "http://127.0.0.1/", "excerpts": ["private"]},
            {"url": "https://example.com", "excerpts": [42]},
            SEARCH["results"][0],
        ]
    }
    result = native_search(monkeypatch, McpTransport(result=payload))
    assert result.status == "ok"
    assert len(result.output["sources"]) == 1
    assert "127.0.0.1" not in str(result)
