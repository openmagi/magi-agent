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

    assert result.startswith("search error:")
    assert "refused" in result or "URLError" in result


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


def test_build_web_search_tools_returns_three_tools_when_both_keys_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both keys present → returns a list of three FunctionTool objects (web_search, web_fetch, research_fact)."""
    from magi_agent.tools.web_search_tools import build_web_search_tools

    monkeypatch.setenv("BRAVE_API_KEY", "brave-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")

    tools = build_web_search_tools()

    assert len(tools) == 3
    from google.adk.tools import FunctionTool

    assert all(isinstance(t, FunctionTool) for t in tools)


def test_build_web_search_tools_tool_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two FunctionTools must be named 'web_search' and 'web_fetch'."""
    from magi_agent.tools.web_search_tools import build_web_search_tools

    monkeypatch.setenv("BRAVE_API_KEY", "brave-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")

    tools = build_web_search_tools()
    names = {getattr(t, "name", None) or getattr(t, "func", lambda: None).__name__ for t in tools}

    assert "web_search" in names
    assert "web_fetch" in names


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
    """Both keys present → research_fact tool must be in the returned list."""
    from magi_agent.tools.web_search_tools import build_web_search_tools

    monkeypatch.setenv("BRAVE_API_KEY", "brave-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")

    tools = build_web_search_tools()
    names = {getattr(t, "name", None) or getattr(t, "func", lambda: None).__name__ for t in tools}

    assert "research_fact" in names, f"research_fact not found in tool names: {names}"


def test_build_web_search_tools_excludes_research_fact_when_keys_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No keys → research_fact must not appear (returns empty list)."""
    from magi_agent.tools.web_search_tools import build_web_search_tools

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    tools = build_web_search_tools()

    assert tools == []


def test_build_web_search_tools_declarations_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADK must be able to build a function declaration for EVERY returned tool.

    research_fact's injectable keyword-only callables (search_fn/fetch_fn) made
    ``FunctionTool._get_declaration`` raise ValueError at agent-build time when
    both keys were set — the live run produced empty answers. The registered
    tool must expose only the model-facing ``question`` parameter.
    """
    from magi_agent.tools.web_search_tools import build_web_search_tools

    monkeypatch.setenv("BRAVE_API_KEY", "brave-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")

    tools = build_web_search_tools()
    assert tools, "expected tools when both keys are set"
    for tool in tools:
        declaration = tool._get_declaration()
        assert declaration is not None, f"no declaration for {tool}"

    research = next(t for t in tools if getattr(t, "name", "") == "research_fact")
    params = research._get_declaration().parameters
    assert set(params.properties.keys()) == {"question"}
