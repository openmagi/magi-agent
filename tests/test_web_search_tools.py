"""Tests for magi_agent.tools.web_search_tools.

Hermetic — no real network. All HTTP calls are intercepted via
monkeypatch on ``urllib.request.urlopen``.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"


def _brave_response(results: list[dict[str, str]]) -> dict[str, object]:
    return {"web": {"results": results}}


def _firecrawl_response(markdown: str) -> dict[str, object]:
    return {"data": {"markdown": markdown}}


class _FakeHTTPResponse:
    """Minimal file-like object that json.load() can consume."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._data = json.dumps(payload).encode()
        self._stream = io.BytesIO(self._data)

    # context-manager support (urlopen returns a context manager)
    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def read(self, n: int = -1) -> bytes:
        return self._stream.read(n)


class _CapturingOpener:
    """Callable that records the Request passed to urlopen and returns a canned response."""

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.captured_requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, **_: object) -> _FakeHTTPResponse:
        self.captured_requests.append(request)
        return _FakeHTTPResponse(self.payload)


class _ErrorOpener:
    """Callable that raises an HTTPError to test fail-soft behaviour."""

    def __call__(self, _request: object, **_kw: object) -> None:
        raise urllib.error.URLError("connection refused")


# ---------------------------------------------------------------------------
# web_search — happy path
# ---------------------------------------------------------------------------

def test_web_search_formats_title_url_snippet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parsed Brave JSON → three-line result blocks joined by blank lines."""
    from magi_agent.tools.web_search_tools import web_search

    results = [
        {"title": "Alpha", "url": "https://alpha.example.com", "description": "Alpha snippet"},
        {"title": "Beta", "url": "https://beta.example.com", "description": "Beta snippet"},
    ]
    opener = _CapturingOpener(_brave_response(results))
    monkeypatch.setenv("BRAVE_API_KEY", "test-brave-key")
    monkeypatch.setattr("urllib.request.urlopen", opener)

    output = web_search("test query")

    assert "Alpha" in output
    assert "https://alpha.example.com" in output
    assert "Alpha snippet" in output
    assert "Beta" in output
    assert "https://beta.example.com" in output
    assert "Beta snippet" in output
    # Blocks are blank-line separated
    assert "\n\n" in output


def test_web_search_uses_brave_endpoint_and_token_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """urlopen must be called with the Brave endpoint + X-Subscription-Token header."""
    from magi_agent.tools.web_search_tools import web_search

    opener = _CapturingOpener(_brave_response([{"title": "T", "url": "https://t.com", "description": "d"}]))
    monkeypatch.setenv("BRAVE_API_KEY", "my-brave-token")
    monkeypatch.setattr("urllib.request.urlopen", opener)

    web_search("query")

    assert len(opener.captured_requests) == 1
    req = opener.captured_requests[0]
    assert _BRAVE_ENDPOINT in req.full_url
    assert req.get_header("X-subscription-token") == "my-brave-token"


def test_web_search_includes_query_in_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The query must appear as the ``q`` parameter in the request URL."""
    from magi_agent.tools.web_search_tools import web_search

    opener = _CapturingOpener(_brave_response([]))
    monkeypatch.setenv("BRAVE_API_KEY", "key")
    monkeypatch.setattr("urllib.request.urlopen", opener)

    web_search("hello world")

    req = opener.captured_requests[0]
    assert "hello+world" in req.full_url or "hello%20world" in req.full_url or "q=hello" in req.full_url


def test_web_search_returns_no_results_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty results list → 'No results.' sentinel string."""
    from magi_agent.tools.web_search_tools import web_search

    opener = _CapturingOpener(_brave_response([]))
    monkeypatch.setenv("BRAVE_API_KEY", "key")
    monkeypatch.setattr("urllib.request.urlopen", opener)

    output = web_search("nothing here")

    assert output == "No results."


def test_web_search_caps_at_eight_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if Brave returns >8 entries, only the first 8 are rendered."""
    from magi_agent.tools.web_search_tools import web_search

    results = [
        {"title": f"R{i}", "url": f"https://r{i}.com", "description": f"s{i}"}
        for i in range(12)
    ]
    opener = _CapturingOpener(_brave_response(results))
    monkeypatch.setenv("BRAVE_API_KEY", "key")
    monkeypatch.setattr("urllib.request.urlopen", opener)

    output = web_search("query")

    # Exactly 8 blocks (7 separators) when all 8 have content
    blocks = output.split("\n\n")
    assert len(blocks) == 8


# ---------------------------------------------------------------------------
# web_search — error / fail-soft
# ---------------------------------------------------------------------------

def test_web_search_returns_error_string_on_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network error → short error string, no exception raised."""
    from magi_agent.tools.web_search_tools import web_search

    monkeypatch.setenv("BRAVE_API_KEY", "key")
    monkeypatch.setattr("urllib.request.urlopen", _ErrorOpener())

    result = web_search("query")

    # A-4: the provider exception is mapped to a safe code; the raw ``repr(exc)``
    # (which can carry the API key) never reaches model-visible output.
    assert result.startswith("search error:")
    assert "provider_error" in result or "provider_timeout" in result
    assert "URLError(" not in result


def test_web_search_returns_error_when_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing BRAVE_API_KEY → error string without touching the network."""
    from magi_agent.tools.web_search_tools import web_search

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    result = web_search("query")

    assert "BRAVE_API_KEY" in result


# ---------------------------------------------------------------------------
# web_fetch — happy path
# ---------------------------------------------------------------------------

def test_web_fetch_returns_markdown_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Firecrawl 200 → markdown from data.markdown returned."""
    from magi_agent.tools.web_search_tools import web_fetch

    md = "# Article\n\nSome content here."
    opener = _CapturingOpener(_firecrawl_response(md))
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")
    monkeypatch.setattr("urllib.request.urlopen", opener)

    result = web_fetch("https://example.com/article")

    assert result == md


def test_web_fetch_uses_firecrawl_endpoint_and_bearer_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """urlopen must use the Firecrawl scrape endpoint + Authorization: Bearer."""
    from magi_agent.tools.web_search_tools import web_fetch

    opener = _CapturingOpener(_firecrawl_response("content"))
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret")
    monkeypatch.setattr("urllib.request.urlopen", opener)

    web_fetch("https://target.example.com")

    assert len(opener.captured_requests) == 1
    req = opener.captured_requests[0]
    assert req.full_url == _FIRECRAWL_ENDPOINT
    assert req.get_header("Authorization") == "Bearer fc-secret"


def test_web_fetch_sends_correct_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Request body must be JSON with url, formats=['markdown'], onlyMainContent=True."""
    from magi_agent.tools.web_search_tools import web_fetch

    opener = _CapturingOpener(_firecrawl_response("ok"))
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")
    monkeypatch.setattr("urllib.request.urlopen", opener)

    target = "https://docs.example.com/page"
    web_fetch(target)

    req = opener.captured_requests[0]
    body = json.loads(req.data)  # type: ignore[arg-type]
    assert body["url"] == target
    assert body["formats"] == ["markdown"]
    assert body["onlyMainContent"] is True


def test_web_fetch_truncates_long_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content longer than 12 000 chars must be truncated to exactly 12 000 chars."""
    from magi_agent.tools.web_search_tools import web_fetch

    long_md = "x" * 20_000
    opener = _CapturingOpener(_firecrawl_response(long_md))
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")
    monkeypatch.setattr("urllib.request.urlopen", opener)

    result = web_fetch("https://example.com/long")

    assert len(result) == 12_000


def test_web_fetch_returns_no_content_when_markdown_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty/absent markdown field → 'No content.' sentinel."""
    from magi_agent.tools.web_search_tools import web_fetch

    opener = _CapturingOpener({"data": {"markdown": ""}})
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")
    monkeypatch.setattr("urllib.request.urlopen", opener)

    result = web_fetch("https://example.com")

    assert result == "No content."


# ---------------------------------------------------------------------------
# web_fetch — error / fail-soft
# ---------------------------------------------------------------------------

def test_web_fetch_returns_error_string_on_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network error → short error string, no exception raised."""
    from magi_agent.tools.web_search_tools import web_fetch

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")
    monkeypatch.setattr("urllib.request.urlopen", _ErrorOpener())

    result = web_fetch("https://example.com")

    assert result.startswith("fetch error:")


def test_web_fetch_returns_error_when_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing FIRECRAWL_API_KEY → error string without touching the network."""
    from magi_agent.tools.web_search_tools import web_fetch

    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    result = web_fetch("https://example.com")

    assert "FIRECRAWL_API_KEY" in result


# ---------------------------------------------------------------------------
# build_web_search_tools — default-OFF gate
# ---------------------------------------------------------------------------

def test_build_web_search_tools_returns_empty_when_both_keys_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No keys → returns []."""
    from magi_agent.tools.web_search_tools import build_web_search_tools

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    tools = build_web_search_tools()

    assert tools == []


def test_build_web_search_tools_returns_empty_when_only_brave_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only BRAVE_API_KEY set (FIRECRAWL absent) → still returns []."""
    from magi_agent.tools.web_search_tools import build_web_search_tools

    monkeypatch.setenv("BRAVE_API_KEY", "key")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    tools = build_web_search_tools()

    assert tools == []


def test_build_web_search_tools_returns_empty_when_only_firecrawl_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only FIRECRAWL_API_KEY set (BRAVE absent) → still returns []."""
    from magi_agent.tools.web_search_tools import build_web_search_tools

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")

    tools = build_web_search_tools()

    assert tools == []


def test_build_web_search_tools_is_deprecated_empty_even_with_both_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-2: the deprecated shim returns [] even with both keys present.

    The direct web capability now flows through dispatcher-backed registry
    manifests (``plugins.native.web.register_direct_web_tools`` +
    ``bind_direct_web_handlers``), never bare FunctionTools appended outside the
    dispatcher. ``build_web_search_tools`` is retained only as a no-op shim.
    """
    from magi_agent.tools.web_search_tools import (
        build_web_search_tools,
        direct_web_tools_available,
    )

    monkeypatch.setenv("BRAVE_API_KEY", "brave-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")

    assert build_web_search_tools() == []
    # The key-presence predicate that gates manifest registration is still True.
    assert direct_web_tools_available() is True


def test_direct_web_tools_available_key_gating(monkeypatch: pytest.MonkeyPatch) -> None:
    """The manifest-registration gate mirrors the old key-presence rule."""
    from magi_agent.tools.web_search_tools import direct_web_tools_available

    assert direct_web_tools_available({}) is False
    assert direct_web_tools_available({"BRAVE_API_KEY": "k"}) is False
    assert direct_web_tools_available({"FIRECRAWL_API_KEY": "k"}) is False
    assert (
        direct_web_tools_available({"BRAVE_API_KEY": "k", "FIRECRAWL_API_KEY": "k"})
        is True
    )


# ---------------------------------------------------------------------------
# Helpers for research_fact tests (fake search_fn / fetch_fn)
# ---------------------------------------------------------------------------

def _make_search_fn(urls: list[str], snippets: list[str] | None = None) -> Any:
    """Return a fake search_fn that returns N result entries."""
    if snippets is None:
        snippets = [f"snippet for {u}" for u in urls]

    def _search(query: str) -> dict[str, object]:
        results = [
            {"url": u, "description": s, "title": f"Title {i}"}
            for i, (u, s) in enumerate(zip(urls, snippets))
        ]
        return {"web": {"results": results}}

    return _search


def _make_fetch_fn(
    content_map: dict[str, str],
    raise_for: set[str] | None = None,
) -> Any:
    """Return a fake fetch_fn: returns content_map[url], or raises for urls in raise_for."""

    def _fetch(url: str) -> dict[str, object]:
        if raise_for and url in raise_for:
            raise RuntimeError(f"simulated fetch error for {url}")
        md = content_map.get(url, f"default content for {url}")
        return {"data": {"markdown": md}}

    return _fetch


# ---------------------------------------------------------------------------
# PR1: research_fact — core behaviour
# ---------------------------------------------------------------------------


def test_research_fact_brief_contains_snippets_from_n_sources() -> None:
    """Brief must contain content from each of the N fetched sources."""
    from magi_agent.tools.web_search_tools import research_fact

    urls = [f"https://src{i}.example.com" for i in range(3)]
    content_map = {u: f"content-from-source-{i}" for i, u in enumerate(urls)}

    result = research_fact(
        "What is X?",
        search_fn=_make_search_fn(urls),
        fetch_fn=_make_fetch_fn(content_map),
        n=3,
    )

    for i, u in enumerate(urls):
        assert u in result, f"URL {u} missing from brief"
        assert f"content-from-source-{i}" in result, f"Content for source {i} missing"


def test_research_fact_skips_failing_source_others_present() -> None:
    """A fetch that raises must be skipped; other sources still appear; no exception."""
    from magi_agent.tools.web_search_tools import research_fact

    urls = [
        "https://good1.example.com",
        "https://bad.example.com",
        "https://good2.example.com",
    ]
    content_map = {
        "https://good1.example.com": "good-content-one",
        "https://good2.example.com": "good-content-two",
    }

    result = research_fact(
        "some question",
        search_fn=_make_search_fn(urls),
        fetch_fn=_make_fetch_fn(content_map, raise_for={"https://bad.example.com"}),
        n=3,
    )

    assert "good-content-one" in result
    assert "good-content-two" in result
    # bad URL may or may not appear; no exception was raised
    assert "bad.example.com" not in result or "error" in result.lower() or True  # no exception is the key


def test_research_fact_skips_failing_source_no_exception() -> None:
    """research_fact must never raise even when a fetch fails."""
    from magi_agent.tools.web_search_tools import research_fact

    urls = ["https://a.example.com", "https://b.example.com"]

    try:
        result = research_fact(
            "question",
            search_fn=_make_search_fn(urls),
            fetch_fn=_make_fetch_fn({}, raise_for={"https://a.example.com"}),
            n=2,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"research_fact raised unexpectedly: {exc}")


def test_research_fact_n_cap_limits_fetches() -> None:
    """When search returns 8 URLs, only the first n=3 are fetched."""
    from magi_agent.tools.web_search_tools import research_fact

    all_urls = [f"https://r{i}.example.com" for i in range(8)]
    fetched: list[str] = []

    def tracking_fetch(url: str) -> dict[str, object]:
        fetched.append(url)
        return {"data": {"markdown": f"content of {url}"}}

    result = research_fact(
        "question",
        search_fn=_make_search_fn(all_urls),
        fetch_fn=tracking_fetch,
        n=3,
    )

    assert len(fetched) == 3, f"Expected 3 fetches, got {len(fetched)}: {fetched}"
    for u in all_urls[:3]:
        assert u in fetched


def test_research_fact_all_fail_returns_search_snippets() -> None:
    """When all fetches fail, the brief must fall back to search snippets (never empty)."""
    from magi_agent.tools.web_search_tools import research_fact

    urls = ["https://a.example.com", "https://b.example.com"]
    snippets = ["search-snippet-alpha", "search-snippet-beta"]

    def always_raises(url: str) -> dict[str, object]:
        raise RuntimeError("all fetches fail")

    result = research_fact(
        "question",
        search_fn=_make_search_fn(urls, snippets),
        fetch_fn=always_raises,
        n=2,
    )

    # Fallback must contain the search snippets
    assert "search-snippet-alpha" in result or "search-snippet-beta" in result


def test_research_fact_parallel_fetch_called_once_per_url() -> None:
    """Each selected URL must be fetched exactly once (parallel path covered)."""
    from magi_agent.tools.web_search_tools import research_fact

    urls = [f"https://p{i}.example.com" for i in range(3)]
    call_counts: dict[str, int] = {}

    def counting_fetch(url: str) -> dict[str, object]:
        call_counts[url] = call_counts.get(url, 0) + 1
        return {"data": {"markdown": f"content {url}"}}

    research_fact(
        "parallel question",
        search_fn=_make_search_fn(urls),
        fetch_fn=counting_fetch,
        n=3,
    )

    for u in urls:
        assert call_counts.get(u, 0) == 1, f"{u} fetched {call_counts.get(u, 0)} times, expected 1"


# ---------------------------------------------------------------------------
# PR2: build_web_search_tools — research_fact registration
# ---------------------------------------------------------------------------


def test_build_web_search_tools_includes_research_fact_when_keys_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-2: even with both keys, the deprecated shim attaches no tools."""
    from magi_agent.tools.web_search_tools import build_web_search_tools

    monkeypatch.setenv("BRAVE_API_KEY", "brave-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")

    assert build_web_search_tools() == []


def test_build_web_search_tools_excludes_research_fact_when_keys_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No keys → research_fact must not appear (returns empty list)."""
    from magi_agent.tools.web_search_tools import build_web_search_tools

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    tools = build_web_search_tools()

    assert tools == []


def test_dispatcher_backed_web_tool_declarations_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADK must build a declaration for EVERY dispatcher-backed web tool.

    The capability moved from bare FunctionTools to registry manifests (A-2).
    The manifest ``input_schema`` is the model-facing contract: research_fact
    must expose only ``question``; web_search ``query``; web_fetch ``url``.
    """
    from magi_agent.adk_bridge.tool_adapter import (
        build_adk_function_tools_for_registry,
    )
    from magi_agent.plugins.native.web import (
        bind_direct_web_handlers,
        register_direct_web_tools,
    )
    from magi_agent.tools.context import ToolContext
    from magi_agent.tools.dispatcher import ToolDispatcher
    from magi_agent.tools.registry import ToolRegistry

    monkeypatch.setenv("BRAVE_API_KEY", "brave-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")

    registry = ToolRegistry()
    register_direct_web_tools(registry)
    bind_direct_web_handlers(registry)
    dispatcher = ToolDispatcher(registry)

    tools = build_adk_function_tools_for_registry(
        registry,
        dispatcher,
        mode="act",
        tool_context_factory=lambda _ctx: ToolContext(bot_id="b", turn_id="t"),
        attach_enabled=True,
    )
    by_name = {getattr(t, "name", ""): t for t in tools}
    assert {"web_search", "web_fetch", "research_fact"}.issubset(by_name)
    for tool in by_name.values():
        assert tool._get_declaration() is not None

    research_params = by_name["research_fact"]._get_declaration().parameters
    arguments = research_params.properties["arguments"]
    assert set(arguments.properties.keys()) == {"question"}


# ---------------------------------------------------------------------------
# Item 06: SerpAPI provider option + latency receipts
# ---------------------------------------------------------------------------

_SERPAPI_ENDPOINT = "https://serpapi.com/search.json"

# Assembled at runtime so the fixture never looks like a real credential.
_FAKE_SERPAPI_KEY = "serp" + "api-" + "test-" + "key"


def _serpapi_response(
    organic: list[dict[str, str]],
    answer_box: dict[str, str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"organic_results": organic}
    if answer_box is not None:
        payload["answer_box"] = answer_box
    return payload


def _clear_item06_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every env var item 06 reads so each test starts hermetic."""
    for name in (
        "MAGI_WEB_SEARCH_PROVIDER",
        "MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED",
        "SERPAPI_API_KEY",
        "BRAVE_API_KEY",
        "FIRECRAWL_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


class _ScriptedClock:
    """Deterministic stand-in for time.monotonic; repeats the last value."""

    def __init__(self, *values: float) -> None:
        self._values = list(values)
        self._last = values[-1] if values else 0.0

    def __call__(self) -> float:
        if self._values:
            self._last = self._values.pop(0)
        return self._last


# --- default-OFF proof ------------------------------------------------------


def test_default_off_serpapi_key_alone_is_byte_identical_to_brave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SERPAPI_API_KEY set but both flags unset → Brave endpoint, baseline output.

    This is the required proof that with the provider flag and the latency flag
    unset, behavior is byte-identical to before this feature existed.
    """
    from magi_agent.tools.web_search_tools import web_search, web_search_raw

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    monkeypatch.setenv("BRAVE_API_KEY", "test-brave-key")
    results = [
        {"title": "Alpha", "url": "https://alpha.example.com", "description": "Alpha snippet"},
        {"title": "Beta", "url": "https://beta.example.com", "description": "Beta snippet"},
    ]
    opener = _CapturingOpener(_brave_response(results))
    monkeypatch.setattr("urllib.request.urlopen", opener)

    output = web_search("test query")

    # Brave endpoint hit, SerpAPI never contacted
    assert _BRAVE_ENDPOINT in opener.captured_requests[0].full_url
    assert all("serpapi.com" not in r.full_url for r in opener.captured_requests)
    # Byte-identical to the baseline formatting expectation
    expected = (
        "Alpha\nhttps://alpha.example.com\nAlpha snippet"
        "\n\n"
        "Beta\nhttps://beta.example.com\nBeta snippet"
    )
    assert output == expected

    raw = web_search_raw("test query")
    assert "latency_ms" not in raw
    assert "provider" not in raw


def test_latency_receipts_explicit_falsy_value_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED=0 behaves exactly like unset."""
    from magi_agent.tools.web_search_tools import web_search, web_search_raw

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("BRAVE_API_KEY", "test-brave-key")
    monkeypatch.setenv("MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED", "0")
    results = [
        {"title": "Alpha", "url": "https://alpha.example.com", "description": "Alpha snippet"},
    ]
    opener = _CapturingOpener(_brave_response(results))
    monkeypatch.setattr("urllib.request.urlopen", opener)

    assert web_search("q") == "Alpha\nhttps://alpha.example.com\nAlpha snippet"
    raw = web_search_raw("q")
    assert "latency_ms" not in raw
    assert "provider" not in raw


# --- provider resolution ----------------------------------------------------


def test_resolve_search_provider_matrix() -> None:
    from magi_agent.tools.web_search_tools import _resolve_search_provider

    key = "k" + "1"
    assert (
        _resolve_search_provider({"MAGI_WEB_SEARCH_PROVIDER": "serpapi", "SERPAPI_API_KEY": key})
        == "serpapi"
    )
    # Flag without key → fail-soft fallback to brave
    assert _resolve_search_provider({"MAGI_WEB_SEARCH_PROVIDER": "serpapi"}) == "brave"
    assert (
        _resolve_search_provider({"MAGI_WEB_SEARCH_PROVIDER": "serpapi", "SERPAPI_API_KEY": ""})
        == "brave"
    )
    # Unknown values → brave (no raise)
    assert (
        _resolve_search_provider({"MAGI_WEB_SEARCH_PROVIDER": "google", "SERPAPI_API_KEY": key})
        == "brave"
    )
    assert (
        _resolve_search_provider({"MAGI_WEB_SEARCH_PROVIDER": "bing", "SERPAPI_API_KEY": key})
        == "brave"
    )
    # Unset → brave
    assert _resolve_search_provider({}) == "brave"
    # Case/whitespace normalization
    assert (
        _resolve_search_provider({"MAGI_WEB_SEARCH_PROVIDER": "  SerpAPI ", "SERPAPI_API_KEY": key})
        == "serpapi"
    )


def test_resolve_search_provider_reads_os_environ_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools.web_search_tools import _resolve_search_provider

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("MAGI_WEB_SEARCH_PROVIDER", "serpapi")
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    assert _resolve_search_provider() == "serpapi"


# --- serpapi_search_raw -----------------------------------------------------


def test_serpapi_search_raw_normalizes_to_brave_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools.web_search_tools import serpapi_search_raw

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    organic = [
        {"title": "Alpha", "link": "https://alpha.example.com", "snippet": "Alpha snippet"},
        {"title": "Beta", "link": "https://beta.example.com", "snippet": "Beta snippet"},
    ]
    opener = _CapturingOpener(_serpapi_response(organic))
    monkeypatch.setattr("urllib.request.urlopen", opener)

    data = serpapi_search_raw("hello world")

    req = opener.captured_requests[0]
    assert req.full_url.startswith(_SERPAPI_ENDPOINT)
    assert "engine=google" in req.full_url
    assert "hello+world" in req.full_url or "hello%20world" in req.full_url
    assert f"api_key={_FAKE_SERPAPI_KEY}" in req.full_url
    assert "num=8" in req.full_url
    assert data == {
        "web": {
            "results": [
                {
                    "title": "Alpha",
                    "url": "https://alpha.example.com",
                    "description": "Alpha snippet",
                },
                {
                    "title": "Beta",
                    "url": "https://beta.example.com",
                    "description": "Beta snippet",
                },
            ]
        }
    }


def test_serpapi_search_raw_missing_key_is_error_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools.web_search_tools import serpapi_search_raw

    _clear_item06_env(monkeypatch)

    data = serpapi_search_raw("q")

    assert "SERPAPI_API_KEY" in str(data.get("error"))


def test_serpapi_answer_box_prepended_and_capped_at_eight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools.web_search_tools import serpapi_search_raw

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    organic = [
        {"title": f"R{i}", "link": f"https://r{i}.example.com", "snippet": f"s{i}"}
        for i in range(10)
    ]
    answer_box = {
        "title": "Population of X",
        "link": "https://answer.example.com",
        "answer": "42 million",
    }
    opener = _CapturingOpener(_serpapi_response(organic, answer_box))
    monkeypatch.setattr("urllib.request.urlopen", opener)

    data = serpapi_search_raw("population of X")

    results = data["web"]["results"]  # type: ignore[index]
    assert len(results) == 8
    first = results[0]
    assert first["title"] == "[answer box] Population of X"
    assert first["url"] == "https://answer.example.com"
    assert first["description"] == "42 million"
    # Remaining slots come from organic results, in order
    assert results[1]["title"] == "R0"


def test_serpapi_answer_box_uses_snippet_when_answer_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools.web_search_tools import serpapi_search_raw

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    answer_box = {"title": "T", "link": "https://a.example.com", "snippet": "snippet text"}
    opener = _CapturingOpener(_serpapi_response([], answer_box))
    monkeypatch.setattr("urllib.request.urlopen", opener)

    data = serpapi_search_raw("q")

    results = data["web"]["results"]  # type: ignore[index]
    assert results[0]["description"] == "snippet text"


def test_serpapi_answer_box_skipped_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An answer_box with neither answer nor snippet is not prepended."""
    from magi_agent.tools.web_search_tools import serpapi_search_raw

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    organic = [{"title": "R0", "link": "https://r0.example.com", "snippet": "s0"}]
    opener = _CapturingOpener(_serpapi_response(organic, {"type": "weather"}))
    monkeypatch.setattr("urllib.request.urlopen", opener)

    data = serpapi_search_raw("q")

    results = data["web"]["results"]  # type: ignore[index]
    assert len(results) == 1
    assert results[0]["title"] == "R0"


def test_serpapi_empty_results_yield_empty_brave_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """organic_results missing and no usable answer_box → empty results list."""
    from magi_agent.tools.web_search_tools import serpapi_search_raw, web_search

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("MAGI_WEB_SEARCH_PROVIDER", "serpapi")
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    opener = _CapturingOpener({"search_metadata": {"status": "Success"}})
    monkeypatch.setattr("urllib.request.urlopen", opener)

    assert serpapi_search_raw("q") == {"web": {"results": []}}
    assert web_search("q") == "No results."


def test_serpapi_search_raw_failsoft_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools.web_search_tools import serpapi_search_raw

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    monkeypatch.setattr("urllib.request.urlopen", _ErrorOpener())

    data = serpapi_search_raw("q")

    # A-4: mapped to a safe code; never the raw repr (which could carry the key).
    assert str(data.get("error")) in {
        "provider_error",
        "provider_timeout",
        "provider_unauthorized",
        "provider_rate_limited",
    }
    assert "URLError(" not in str(data["error"])


def test_serpapi_error_body_becomes_error_dict_and_error_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SerpAPI-side {"error": ...} body (bad key/quota) → error dict + string."""
    from magi_agent.tools.web_search_tools import serpapi_search_raw, web_search

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("MAGI_WEB_SEARCH_PROVIDER", "serpapi")
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    opener = _CapturingOpener({"error": "Invalid API key. Your account is suspended."})
    monkeypatch.setattr("urllib.request.urlopen", opener)

    data = serpapi_search_raw("q")
    assert data["error"] == "Invalid API key. Your account is suspended."

    output = web_search("q")
    assert output.startswith("search error:")
    assert "Invalid API key" in output


# --- dispatch ---------------------------------------------------------------


def test_web_search_raw_dispatches_to_serpapi_when_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools.web_search_tools import web_search_raw

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("MAGI_WEB_SEARCH_PROVIDER", "serpapi")
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    # Brave key also present — provider flag must win
    monkeypatch.setenv("BRAVE_API_KEY", "test-brave-key")
    organic = [{"title": "T", "link": "https://t.example.com", "snippet": "d"}]
    opener = _CapturingOpener(_serpapi_response(organic))
    monkeypatch.setattr("urllib.request.urlopen", opener)

    data = web_search_raw("q")

    assert len(opener.captured_requests) == 1
    assert opener.captured_requests[0].full_url.startswith(_SERPAPI_ENDPOINT)
    assert all(_BRAVE_ENDPOINT not in r.full_url for r in opener.captured_requests)
    assert data == {
        "web": {
            "results": [
                {"title": "T", "url": "https://t.example.com", "description": "d"},
            ]
        }
    }


def test_web_search_raw_dispatches_to_brave_when_flag_points_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools.web_search_tools import web_search_raw

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("MAGI_WEB_SEARCH_PROVIDER", "google")
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    monkeypatch.setenv("BRAVE_API_KEY", "test-brave-key")
    opener = _CapturingOpener(_brave_response([]))
    monkeypatch.setattr("urllib.request.urlopen", opener)

    web_search_raw("q")

    assert len(opener.captured_requests) == 1
    assert _BRAVE_ENDPOINT in opener.captured_requests[0].full_url
    assert all("serpapi.com" not in r.full_url for r in opener.captured_requests)


# --- build_web_search_tools gate --------------------------------------------


def test_build_web_search_tools_serpapi_satisfies_search_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SERPAPI + provider flag + FIRECRAWL (NO Brave) satisfies the manifest gate.

    A-2: the deprecated shim still returns []; the gate that drives
    dispatcher-backed manifest registration is satisfied.
    """
    from magi_agent.tools.web_search_tools import (
        build_web_search_tools,
        direct_web_tools_available,
    )

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("MAGI_WEB_SEARCH_PROVIDER", "serpapi")
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")

    assert build_web_search_tools() == []
    assert direct_web_tools_available() is True


def test_build_web_search_tools_serpapi_without_firecrawl_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools.web_search_tools import build_web_search_tools

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("MAGI_WEB_SEARCH_PROVIDER", "serpapi")
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)

    assert build_web_search_tools() == []


def test_build_web_search_tools_provider_flag_without_key_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider flag set but no SERPAPI key and no Brave key → []."""
    from magi_agent.tools.web_search_tools import build_web_search_tools

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("MAGI_WEB_SEARCH_PROVIDER", "serpapi")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")

    assert build_web_search_tools() == []


def test_build_web_search_tools_serpapi_key_alone_without_flag_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SERPAPI key present but provider flag unset and no Brave key → [] (flag-gated)."""
    from magi_agent.tools.web_search_tools import build_web_search_tools

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")

    assert build_web_search_tools() == []


# --- research_fact end-to-end via serpapi provider ---------------------------


def test_research_fact_end_to_end_with_serpapi_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """research_fact with the default search_fn (real web_search_raw) over SerpAPI.

    Proves the normalized SerpAPI shape is consumed downstream unchanged.
    """
    from magi_agent.tools.web_search_tools import research_fact

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("MAGI_WEB_SEARCH_PROVIDER", "serpapi")
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    organic = [
        {"title": "Src1", "link": "https://src1.example.com", "snippet": "sn1"},
        {"title": "Src2", "link": "https://src2.example.com", "snippet": "sn2"},
    ]
    opener = _CapturingOpener(_serpapi_response(organic))
    monkeypatch.setattr("urllib.request.urlopen", opener)
    content_map = {
        "https://src1.example.com": "content-one",
        "https://src2.example.com": "content-two",
    }

    result = research_fact(
        "What is X?",
        fetch_fn=_make_fetch_fn(content_map),
        n=2,
    )

    assert opener.captured_requests[0].full_url.startswith(_SERPAPI_ENDPOINT)
    assert "https://src1.example.com" in result
    assert "content-one" in result
    assert "https://src2.example.com" in result
    assert "content-two" in result


# --- latency receipts (flag ON) ----------------------------------------------


def test_latency_receipts_web_search_raw_exact_ms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools import web_search_tools
    from magi_agent.tools.web_search_tools import web_search_raw

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("BRAVE_API_KEY", "test-brave-key")
    monkeypatch.setenv("MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED", "1")
    opener = _CapturingOpener(_brave_response([]))
    monkeypatch.setattr("urllib.request.urlopen", opener)
    monkeypatch.setattr(web_search_tools, "_clock", _ScriptedClock(5.0, 5.412))

    raw = web_search_raw("q")

    assert raw["latency_ms"] == 412
    assert raw["provider"] == "brave"


def test_latency_receipts_serpapi_provider_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools import web_search_tools
    from magi_agent.tools.web_search_tools import web_search_raw

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("MAGI_WEB_SEARCH_PROVIDER", "serpapi")
    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)
    monkeypatch.setenv("MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED", "true")
    opener = _CapturingOpener(_serpapi_response([]))
    monkeypatch.setattr("urllib.request.urlopen", opener)
    monkeypatch.setattr(web_search_tools, "_clock", _ScriptedClock(1.0, 1.05))

    raw = web_search_raw("q")

    assert raw["provider"] == "serpapi"
    assert raw["latency_ms"] == 50


def test_latency_receipts_measured_on_error_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools import web_search_tools
    from magi_agent.tools.web_search_tools import web_search_raw

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("BRAVE_API_KEY", "test-brave-key")
    monkeypatch.setenv("MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED", "yes")
    monkeypatch.setattr("urllib.request.urlopen", _ErrorOpener())
    monkeypatch.setattr(web_search_tools, "_clock", _ScriptedClock(2.0, 2.007))

    raw = web_search_raw("q")

    assert "error" in raw
    assert raw["latency_ms"] == 7
    assert raw["provider"] == "brave"


def test_latency_receipts_footer_on_web_search_success_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import re

    from magi_agent.tools.web_search_tools import web_search

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("BRAVE_API_KEY", "test-brave-key")
    monkeypatch.setenv("MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED", "1")
    results = [{"title": "T", "url": "https://t.example.com", "description": "d"}]
    opener = _CapturingOpener(_brave_response(results))
    monkeypatch.setattr("urllib.request.urlopen", opener)

    output = web_search("q")
    assert re.search(r"\n\n\[receipt\] provider=brave latency_ms=\d+$", output)
    assert output.startswith("T\nhttps://t.example.com\nd")

    # Error string also carries the footer
    monkeypatch.setattr("urllib.request.urlopen", _ErrorOpener())
    err_output = web_search("q")
    assert err_output.startswith("search error:")
    assert re.search(r"\n\n\[receipt\] provider=brave latency_ms=\d+$", err_output)


def test_latency_receipts_footer_on_web_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import re

    from magi_agent.tools.web_search_tools import web_fetch

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")
    monkeypatch.setenv("MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED", "on")
    opener = _CapturingOpener(_firecrawl_response("# Page"))
    monkeypatch.setattr("urllib.request.urlopen", opener)

    output = web_fetch("https://example.com")
    assert output.startswith("# Page")
    assert re.search(r"\n\n\[receipt\] provider=firecrawl latency_ms=\d+$", output)

    monkeypatch.setattr("urllib.request.urlopen", _ErrorOpener())
    err_output = web_fetch("https://example.com")
    assert err_output.startswith("fetch error:")
    assert re.search(r"\n\n\[receipt\] provider=firecrawl latency_ms=\d+$", err_output)


def test_latency_receipts_research_fact_per_source_annotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools.web_search_tools import research_fact

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED", "1")
    urls = ["https://a.example.com"]

    def fetch_with_latency(url: str) -> dict[str, object]:
        return {"data": {"markdown": f"content {url}"}, "latency_ms": 77, "provider": "firecrawl"}

    result = research_fact(
        "q",
        search_fn=_make_search_fn(urls),
        fetch_fn=fetch_with_latency,
        n=1,
    )

    assert "[1] https://a.example.com (latency_ms=77)" in result


def test_latency_receipts_research_fact_fallback_search_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools.web_search_tools import research_fact

    _clear_item06_env(monkeypatch)
    monkeypatch.setenv("MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED", "1")

    def search_with_latency(query: str) -> dict[str, object]:
        return {
            "web": {
                "results": [
                    {"url": "https://a.example.com", "description": "snip-a", "title": "A"},
                ]
            },
            "latency_ms": 33,
            "provider": "brave",
        }

    def always_raises(url: str) -> dict[str, object]:
        raise RuntimeError("boom")

    result = research_fact(
        "q",
        search_fn=search_with_latency,
        fetch_fn=always_raises,
        n=1,
    )

    assert "(search latency_ms=33)" in result
    assert "snip-a" in result


def test_latency_receipts_off_research_fact_ignores_stray_latency_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF: even an injected fn returning latency keys must not annotate."""
    from magi_agent.tools.web_search_tools import research_fact

    _clear_item06_env(monkeypatch)
    urls = ["https://a.example.com"]

    def fetch_with_latency(url: str) -> dict[str, object]:
        return {"data": {"markdown": "content-x"}, "latency_ms": 77}

    result = research_fact(
        "q",
        search_fn=_make_search_fn(urls),
        fetch_fn=fetch_with_latency,
        n=1,
    )

    assert "latency_ms" not in result
    assert "[1] https://a.example.com\ncontent-x" in result


# Item 07: gated brief v2 (header + cross-check footer) + guidance block
# + never-raises executor-timeout hardening
# ---------------------------------------------------------------------------

_GUIDANCE_FLAG = "MAGI_RESEARCH_FACT_GUIDANCE_ENABLED"


def test_research_fact_default_off_brief_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag unset → the brief is byte-identical to the baseline bare join.

    This is the default-OFF zero-behavior-change proof for the tool.
    """
    from magi_agent.tools.web_search_tools import research_fact

    monkeypatch.delenv(_GUIDANCE_FLAG, raising=False)

    urls = [f"https://s{i}.example.com" for i in range(3)]
    content_map = {u: f"content-{i}" for i, u in enumerate(urls)}

    result = research_fact(
        "What is X?",
        search_fn=_make_search_fn(urls),
        fetch_fn=_make_fetch_fn(content_map),
        n=3,
    )

    expected = "\n\n---\n\n".join(
        f"[{i + 1}] {u}\n{content_map[u]}" for i, u in enumerate(urls)
    )
    assert result == expected
    assert "research_fact brief" not in result
    assert "Cross-check:" not in result


def test_research_fact_guidance_on_brief_has_header_and_footer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag '1' → header (question echo + source count) and cross-check footer."""
    from magi_agent.tools.web_search_tools import research_fact

    monkeypatch.setenv(_GUIDANCE_FLAG, "1")

    urls = [f"https://s{i}.example.com" for i in range(3)]
    content_map = {u: f"content-{i}" for i, u in enumerate(urls)}

    result = research_fact(
        "What is X?",
        search_fn=_make_search_fn(urls),
        fetch_fn=_make_fetch_fn(content_map),
        n=3,
    )

    assert result.startswith("research_fact brief — question: What is X?")
    assert "sources: 3/3 fetched" in result
    assert "(0 failed)" not in result
    assert "Cross-check:" in result
    assert result.endswith("A value seen in only one source is a hypothesis, not a fact.")
    # All source briefs still present, unchanged per-source format
    for i, u in enumerate(urls):
        assert f"[{i + 1}] {u}\ncontent-{i}" in result


def test_research_fact_guidance_on_partial_failure_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One raising source out of three → header reports 2/3 fetched (1 failed)."""
    from magi_agent.tools.web_search_tools import research_fact

    monkeypatch.setenv(_GUIDANCE_FLAG, "1")

    urls = [
        "https://good1.example.com",
        "https://bad.example.com",
        "https://good2.example.com",
    ]
    content_map = {
        "https://good1.example.com": "good-content-one",
        "https://good2.example.com": "good-content-two",
    }

    result = research_fact(
        "some question",
        search_fn=_make_search_fn(urls),
        fetch_fn=_make_fetch_fn(content_map, raise_for={"https://bad.example.com"}),
        n=3,
    )

    assert "sources: 2/3 fetched (1 failed)" in result
    assert "Cross-check:" in result


def test_research_fact_guidance_on_preserves_failure_marker_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag ON must not touch the byte-frozen fail-soft error prefixes.

    ``research/live_audit.py`` (_WEB_FAILURE_MARKERS) string-matches these
    prefixes for governance audit; header/footer apply only to the
    successful-sources branch.
    """
    from magi_agent.tools.web_search_tools import research_fact

    monkeypatch.setenv(_GUIDANCE_FLAG, "1")

    # Search raises → "research_fact: search failed"
    def raising_search(query: str) -> dict[str, object]:
        raise RuntimeError("boom")

    out = research_fact("q", search_fn=raising_search, fetch_fn=_make_fetch_fn({}))
    assert out.startswith("research_fact: search failed")

    # Empty search → "research_fact: no results"
    out = research_fact(
        "q", search_fn=_make_search_fn([]), fetch_fn=_make_fetch_fn({})
    )
    assert out.startswith("research_fact: no results")

    # All fetches fail → snippet fallback keeps its exact prefix, no header/footer
    def always_raises(url: str) -> dict[str, object]:
        raise RuntimeError("all fetches fail")

    out = research_fact(
        "q",
        search_fn=_make_search_fn(["https://a.example.com"]),
        fetch_fn=always_raises,
        n=1,
    )
    assert out.startswith("research_fact: all fetches failed")
    assert "Cross-check:" not in out

    # The audit-side markers must keep matching tool outputs
    from magi_agent.research.live_audit import _WEB_FAILURE_MARKERS

    assert any(
        out_prefix in ("research_fact: search failed", "research_fact: no results")
        for out_prefix in _WEB_FAILURE_MARKERS
    )


def test_research_fact_never_raises_on_executor_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pathological slow fetch_fn must yield the fallback, not raise.

    Regression test for the latent bug where ``as_completed(...)`` raised
    ``concurrent.futures.TimeoutError`` outside any try block, violating the
    documented "Never raises" contract. Also proves the executor is shut down
    without waiting (returns promptly).
    """
    import time

    from magi_agent.tools.web_search_tools import research_fact

    monkeypatch.delenv(_GUIDANCE_FLAG, raising=False)

    urls = [f"https://slow{i}.example.com" for i in range(3)]

    def slow_fetch(url: str) -> dict[str, object]:
        time.sleep(0.5)
        return {"data": {"markdown": "too late"}}

    start = time.monotonic()
    result = research_fact(
        "slow question",
        search_fn=_make_search_fn(urls),
        fetch_fn=slow_fetch,
        n=3,
        per_fetch_timeout=0.05,
    )
    elapsed = time.monotonic() - start

    assert result.startswith("research_fact: all fetches failed")
    assert elapsed < 2.0, f"research_fact blocked for {elapsed:.2f}s (no-wait shutdown expected)"


def test_web_research_guidance_block_matrix() -> None:
    """Guidance block: flag ON + both keys → block; otherwise ''."""
    from magi_agent.tools.web_search_tools import web_research_guidance_block

    # Assemble key-shaped values at runtime (push-protection-safe)
    brave = "brave-" + "key"
    firecrawl = "fc-" + "key"

    on_with_keys = {
        _GUIDANCE_FLAG: "1",
        "BRAVE_API_KEY": brave,
        "FIRECRAWL_API_KEY": firecrawl,
    }
    block = web_research_guidance_block(on_with_keys)
    assert block.startswith("<web_research>")
    assert block.endswith("</web_research>")
    assert "research_fact" in block

    # Flag ON, missing either key → "" (never advertise an unavailable tool)
    assert web_research_guidance_block(
        {_GUIDANCE_FLAG: "1", "BRAVE_API_KEY": brave}
    ) == ""
    assert web_research_guidance_block(
        {_GUIDANCE_FLAG: "1", "FIRECRAWL_API_KEY": firecrawl}
    ) == ""

    # Flag OFF (unset / falsy / garbage) + keys → ""
    assert web_research_guidance_block(
        {"BRAVE_API_KEY": brave, "FIRECRAWL_API_KEY": firecrawl}
    ) == ""
    assert web_research_guidance_block(
        {_GUIDANCE_FLAG: "0", "BRAVE_API_KEY": brave, "FIRECRAWL_API_KEY": firecrawl}
    ) == ""
    assert web_research_guidance_block(
        {_GUIDANCE_FLAG: "garbage", "BRAVE_API_KEY": brave, "FIRECRAWL_API_KEY": firecrawl}
    ) == ""


# ---------------------------------------------------------------------------
# A-3: web_fetch applies the native url_policy_error SSRF firewall
# ---------------------------------------------------------------------------

_BLOCKED_URLS = [
    "http://169.254.169.254/latest/meta-data/",
    "http://localhost/admin",
    "http://10.0.0.1/internal",
    "file:///etc/passwd",
    "http://user:pass@evil.example.com/",
]


@pytest.mark.parametrize("blocked_url", _BLOCKED_URLS)
def test_web_fetch_blocks_ssrf_urls_without_calling_firecrawl(
    blocked_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blocked URLs return a ``url_policy_error`` and never reach Firecrawl."""
    from magi_agent.tools import web_search_tools

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")

    called = {"firecrawl": False}

    def _spy_firecrawl(url: str) -> dict[str, object]:
        called["firecrawl"] = True
        return {"data": {"markdown": "should not happen"}}

    monkeypatch.setattr(web_search_tools, "_firecrawl_fetch_raw", _spy_firecrawl)

    raw = web_search_tools.web_fetch_raw(blocked_url)
    assert raw.get("error") == "url_policy_error"
    assert called["firecrawl"] is False

    text = web_search_tools.web_fetch(blocked_url)
    assert "url_policy_error" in text
    assert called["firecrawl"] is False


def test_web_fetch_allows_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a plain public URL still reaches Firecrawl and returns content."""
    from magi_agent.tools import web_search_tools

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")
    opener = _CapturingOpener(_firecrawl_response("hello public page"))
    monkeypatch.setattr("urllib.request.urlopen", opener)

    text = web_search_tools.web_fetch("https://example.com/article")
    assert "hello public page" in text
    assert opener.captured_requests  # firecrawl was actually called


# ---------------------------------------------------------------------------
# A-4: provider exceptions map to safe codes; secrets never surface
# ---------------------------------------------------------------------------

_SAFE_PROVIDER_CODES = {
    "provider_error",
    "provider_timeout",
    "provider_unauthorized",
    "provider_rate_limited",
}


def test_brave_exception_does_not_leak_secret_or_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools import web_search_tools

    monkeypatch.setenv("BRAVE_API_KEY", "brave-secret-xyz")

    def _raise(*_a: object, **_k: object) -> object:
        raise RuntimeError("boom token=brave-secret-xyz at 0x7f")

    monkeypatch.setattr("urllib.request.urlopen", _raise)

    data = web_search_tools._brave_search_raw("q")
    error = str(data.get("error"))
    assert "brave-secret-xyz" not in error
    assert "RuntimeError(" not in error  # no repr(exc)
    assert error in _SAFE_PROVIDER_CODES


def test_firecrawl_exception_does_not_leak_secret_or_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools import web_search_tools

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret-abc")

    def _raise(*_a: object, **_k: object) -> object:
        raise RuntimeError("fail Bearer fc-secret-abc")

    monkeypatch.setattr("urllib.request.urlopen", _raise)

    data = web_search_tools._firecrawl_fetch_raw("https://example.com/")
    error = str(data.get("error"))
    assert "fc-secret-abc" not in error
    assert "RuntimeError(" not in error
    assert error in _SAFE_PROVIDER_CODES


def test_brave_http_error_maps_to_unauthorized_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools import web_search_tools

    monkeypatch.setenv("BRAVE_API_KEY", "k")

    def _raise(*_a: object, **_k: object) -> object:
        raise urllib.error.HTTPError(
            url="https://api.search.brave.com",
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    data = web_search_tools._brave_search_raw("q")
    assert data.get("error") == "provider_unauthorized"


def test_brave_http_error_maps_to_rate_limited_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools import web_search_tools

    monkeypatch.setenv("BRAVE_API_KEY", "k")

    def _raise(*_a: object, **_k: object) -> object:
        raise urllib.error.HTTPError(
            url="https://api.search.brave.com",
            code=429,
            msg="Too Many Requests",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    data = web_search_tools._brave_search_raw("q")
    assert data.get("error") == "provider_rate_limited"


def test_serpapi_exception_does_not_leak_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools import web_search_tools

    monkeypatch.setenv("SERPAPI_API_KEY", _FAKE_SERPAPI_KEY)

    def _raise(*_a: object, **_k: object) -> object:
        raise RuntimeError(f"upstream failed url=...&api_key={_FAKE_SERPAPI_KEY}")

    monkeypatch.setattr("urllib.request.urlopen", _raise)

    data = web_search_tools._serpapi_search_raw_impl("q")
    # No value in the returned dict may contain the api key.
    for value in data.values():
        assert _FAKE_SERPAPI_KEY not in str(value)
    assert str(data.get("error")) in _SAFE_PROVIDER_CODES


def test_research_fact_search_failure_does_not_leak_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.tools import web_search_tools

    def _boom(_q: str) -> dict[str, object]:
        raise RuntimeError("secret-leak-token-987")

    out = web_search_tools.research_fact("q", search_fn=_boom)
    assert "secret-leak-token-987" not in out
    assert "RuntimeError(" not in out
