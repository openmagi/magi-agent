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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
_DEFAULT_MAX_RESULTS = 8
_FIRECRAWL_MAX_CHARS = 12_000


def web_search(query: str) -> str:
    """Search the web using Brave Search and return the top results.

    Args:
        query: The search query.

    Returns:
        Up to 8 results, each with title, URL, and a snippet, separated by
        blank lines. Returns a short error string on any failure (never raises).
    """
    key = os.environ.get("BRAVE_API_KEY", "")
    if not key:
        return "search error: BRAVE_API_KEY not set"
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
    except Exception as exc:  # noqa: BLE001
        return f"search error: {exc!r}"
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
    key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not key:
        return "fetch error: FIRECRAWL_API_KEY not set"
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
    except Exception as exc:  # noqa: BLE001
        return f"fetch error: {exc!r}"
    md = str((data.get("data") or {}).get("markdown") or "")  # type: ignore[union-attr]
    return md[:_FIRECRAWL_MAX_CHARS] if md else "No content."


def build_web_search_tools() -> list[object]:
    """Return ADK FunctionTools for web_search and web_fetch.

    Default-OFF: returns an empty list when ``BRAVE_API_KEY`` or
    ``FIRECRAWL_API_KEY`` is absent from the environment, so agents that do
    not have these keys configured receive no extra tools and no import-time
    errors.
    """
    if not os.environ.get("BRAVE_API_KEY") or not os.environ.get("FIRECRAWL_API_KEY"):
        return []
    from google.adk.tools import FunctionTool  # noqa: PLC0415

    return [FunctionTool(web_search), FunctionTool(web_fetch)]
