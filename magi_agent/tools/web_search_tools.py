"""Fast direct web tools — Brave Search + Firecrawl fetch.

No MCP connect/teardown overhead; each call is a single HTTPS request.
Default-OFF: ``build_web_search_tools()`` returns ``[]`` when the required
environment keys are absent.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
_DEFAULT_MAX_RESULTS = 8
_FIRECRAWL_MAX_CHARS = 12_000
_RESEARCH_FETCH_MAX_CHARS = 4_000  # per-source content cap for research briefs


# ---------------------------------------------------------------------------
# Raw callables — return parsed dicts; reusable by research_fact
# ---------------------------------------------------------------------------


def web_search_raw(query: str) -> dict[str, object]:
    """Search Brave and return the raw parsed JSON response dict.

    Returns a dict with structure ``{"web": {"results": [...]}}`` on success,
    or ``{"error": "..."}`` on any failure.  Never raises.
    """
    key = os.environ.get("BRAVE_API_KEY", "")
    if not key:
        return {"error": "BRAVE_API_KEY not set"}
    params = urllib.parse.urlencode({"q": query, "count": _DEFAULT_MAX_RESULTS})
    url = f"{_BRAVE_ENDPOINT}?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "X-Subscription-Token": key,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            data: dict[str, object] = json.load(resp)
        return data
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


def web_fetch_raw(url: str) -> dict[str, object]:
    """Fetch a page via Firecrawl and return the raw parsed JSON response dict.

    Returns a dict with structure ``{"data": {"markdown": "..."}}`` on success,
    or ``{"error": "..."}`` on any failure.  Never raises.
    """
    key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not key:
        return {"error": "FIRECRAWL_API_KEY not set"}
    body: bytes = json.dumps(
        {"url": url, "formats": ["markdown"], "onlyMainContent": True}
    ).encode()
    req = urllib.request.Request(
        _FIRECRAWL_ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310
            data: dict[str, object] = json.load(resp)
        return data
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


# ---------------------------------------------------------------------------
# Public tool callables — string-returning, used as ADK FunctionTools
# ---------------------------------------------------------------------------


def web_search(query: str) -> str:
    """Search the web using Brave Search and return the top results.

    Args:
        query: The search query.

    Returns:
        Up to 8 results, each with title, URL, and a snippet, separated by
        blank lines. Returns a short error string on any failure (never raises).
    """
    data = web_search_raw(query)
    if "error" in data:
        return f"search error: {data['error']}"
    web_section = data.get("web") or {}
    results: list[dict[str, object]] = (web_section.get("results") or [])[:_DEFAULT_MAX_RESULTS]  # type: ignore[assignment]
    if not results:
        return "No results."
    lines: list[str] = []
    for item in results:
        title = str(item.get("title") or "")
        item_url = str(item.get("url") or "")
        snippet = str(item.get("description") or "")
        lines.append(f"{title}\n{item_url}\n{snippet}")
    return "\n\n".join(lines)


def web_fetch(url: str) -> str:
    """Fetch a web page via Firecrawl and return its readable markdown content.

    Args:
        url: The full URL of the page to fetch.

    Returns:
        Page content as markdown (truncated to ~12 k chars). Returns a short
        error string on any failure (never raises).
    """
    data = web_fetch_raw(url)
    if "error" in data:
        return f"fetch error: {data['error']}"
    md = str((data.get("data") or {}).get("markdown") or "")  # type: ignore[union-attr]
    return md[:_FIRECRAWL_MAX_CHARS] if md else "No content."


# ---------------------------------------------------------------------------
# research_fact — multi-source first-pass research tool
# ---------------------------------------------------------------------------

#: Type alias for the injectable search callable used by research_fact.
_SearchFn = Callable[[str], dict[str, object]]
#: Type alias for the injectable fetch callable used by research_fact.
_FetchFn = Callable[[str], dict[str, object]]


def research_fact(
    question: str,
    *,
    search_fn: _SearchFn = web_search_raw,
    fetch_fn: _FetchFn = web_fetch_raw,
    n: int = 3,
    per_fetch_timeout: float = 30.0,
) -> str:
    """Research a factual question by consulting multiple web sources in parallel.

    Performs one Brave search, then fetches the top ``n`` result pages IN
    PARALLEL (via ``ThreadPoolExecutor``), and returns a consolidated evidence
    brief so the agent can see agreement or disagreement across sources BEFORE
    committing an answer.

    Args:
        question: The factual question to research.
        search_fn: Callable that takes a query string and returns a Brave-style
            raw dict (injectable for testing; defaults to ``web_search_raw``).
        fetch_fn: Callable that takes a URL string and returns a Firecrawl-style
            raw dict (injectable for testing; defaults to ``web_fetch_raw``).
        n: Maximum number of top URLs to fetch (default 3).
        per_fetch_timeout: Per-source fetch wall-clock timeout in seconds passed
            to the thread executor (default 30.0).

    Returns:
        A multi-source evidence brief string.  Each successful source appears as
        ``[i] <url>\\n<content snippet>``.  If all fetches fail the brief falls
        back to the raw search snippets so the agent always receives some
        context.  Never raises.
    """
    # Step 1: search
    try:
        search_data = search_fn(question)
    except Exception as exc:  # noqa: BLE001
        return f"research_fact: search failed: {exc!r}"

    web_section = search_data.get("web") or {}
    results: list[dict[str, object]] = list(  # type: ignore[assignment]
        (web_section.get("results") or [])[:n]
    )

    # Build a snippet fallback from search results (used if all fetches fail)
    search_snippets: list[str] = []
    urls: list[str] = []
    for item in results:
        u = str(item.get("url") or "")
        snippet = str(item.get("description") or item.get("title") or "")
        if u:
            urls.append(u)
            search_snippets.append(f"[search] {u}\n{snippet}")

    if not urls:
        # No URLs at all — return whatever came back
        return f"research_fact: no results for: {question}"

    # Step 2: fetch selected URLs in parallel
    source_briefs: list[str] = []

    def _fetch_one(idx_url: tuple[int, str]) -> tuple[int, str, str | None]:
        """Fetch one URL; return (idx, url, content_or_None)."""
        idx, url = idx_url
        try:
            raw = fetch_fn(url)
            md = str((raw.get("data") or {}).get("markdown") or "")  # type: ignore[union-attr]
            if md:
                return idx, url, md[:_RESEARCH_FETCH_MAX_CHARS]
            # fetch returned empty content — skip
            return idx, url, None
        except Exception:  # noqa: BLE001
            return idx, url, None

    indexed_urls = list(enumerate(urls))
    fetch_results: list[tuple[int, str, str | None]] = [None] * len(indexed_urls)  # type: ignore[list-item]

    with ThreadPoolExecutor(max_workers=len(indexed_urls)) as executor:
        futures = {executor.submit(_fetch_one, item): item[0] for item in indexed_urls}
        for future in as_completed(futures, timeout=per_fetch_timeout * len(indexed_urls)):
            try:
                idx, url, content = future.result(timeout=per_fetch_timeout)
                fetch_results[idx] = (idx, url, content)
            except Exception:  # noqa: BLE001
                orig_idx = futures[future]
                fetch_results[orig_idx] = (orig_idx, urls[orig_idx], None)

    # Assemble brief from successful fetches (preserve original order)
    successful = 0
    for entry in fetch_results:
        if entry is None:
            continue
        idx, url, content = entry
        if content is not None:
            source_briefs.append(f"[{successful + 1}] {url}\n{content}")
            successful += 1

    if source_briefs:
        return "\n\n---\n\n".join(source_briefs)

    # All fetches failed — fall back to search snippets
    if search_snippets:
        fallback_header = "research_fact: all fetches failed; returning search snippets:\n\n"
        return fallback_header + "\n\n".join(search_snippets)

    return f"research_fact: no usable sources found for: {question}"


# ---------------------------------------------------------------------------
# Tool catalog builder
# ---------------------------------------------------------------------------


def build_web_search_tools() -> list[object]:
    """Return ADK FunctionTools for web_search, web_fetch, and research_fact.

    Default-OFF: returns an empty list when ``BRAVE_API_KEY`` or
    ``FIRECRAWL_API_KEY`` is absent from the environment, so agents that do
    not have these keys configured receive no extra tools and no import-time
    errors.
    """
    if not os.environ.get("BRAVE_API_KEY") or not os.environ.get("FIRECRAWL_API_KEY"):
        return []
    from google.adk.tools import FunctionTool  # noqa: PLC0415

    return [FunctionTool(web_search), FunctionTool(web_fetch), FunctionTool(research_fact)]
