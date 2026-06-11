"""Fast direct web tools — Brave Search (or opt-in SerpAPI) + Firecrawl fetch.

No MCP connect/teardown overhead; each call is a single HTTPS request.
Default-OFF: ``build_web_search_tools()`` returns ``[]`` when the required
environment keys are absent.

Provider selection (default unset → Brave, byte-identical to before):
set ``MAGI_WEB_SEARCH_PROVIDER=serpapi`` together with ``SERPAPI_API_KEY`` to
serve searches from a real Google SERP via SerpAPI, normalized to the Brave
response shape so all downstream consumers work unchanged.

Latency receipts (default unset → OFF): set
``MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED=1`` to add ``latency_ms``/``provider``
keys to the raw dicts and an in-band ``[receipt] ...`` footer to the string
tools, so the model can budget around slow providers/URLs.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    pass

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
_SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
_DEFAULT_MAX_RESULTS = 8
_FIRECRAWL_MAX_CHARS = 12_000
_RESEARCH_FETCH_MAX_CHARS = 4_000  # per-source content cap for research briefs

_PROVIDER_ENV = "MAGI_WEB_SEARCH_PROVIDER"
_LATENCY_ENV = "MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED"
# Repo truthy convention (magi_agent/config/env.py); replicated locally so this
# leaf tool module does not import sealed config modules.
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

#: Injectable monotonic clock for latency receipts (monkeypatchable in tests).
_clock: Callable[[], float] = time.monotonic


# ---------------------------------------------------------------------------
# Provider / flag resolution
# ---------------------------------------------------------------------------


def _resolve_search_provider(
    env: Mapping[str, str] | None = None,
) -> Literal["brave", "serpapi"]:
    """Resolve the active web-search provider from the environment.

    Returns ``"serpapi"`` iff ``MAGI_WEB_SEARCH_PROVIDER`` (stripped,
    lowercased) equals ``"serpapi"`` AND ``SERPAPI_API_KEY`` is non-empty.
    Anything else — unset flag, unknown value, missing key — falls back to
    ``"brave"`` (fail-soft; never raises).
    """
    source = os.environ if env is None else env
    selected = (source.get(_PROVIDER_ENV) or "").strip().lower()
    if selected == "serpapi" and source.get("SERPAPI_API_KEY"):
        return "serpapi"
    return "brave"


def _latency_receipts_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True iff ``MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED`` is truthy (default OFF)."""
    source = os.environ if env is None else env
    return (source.get(_LATENCY_ENV) or "").strip().lower() in _TRUE_VALUES


def _with_receipt(
    provider: str, fn: Callable[[], dict[str, object]]
) -> dict[str, object]:
    """Run ``fn`` and, when latency receipts are enabled, stamp the result.

    Adds top-level ``latency_ms`` (int, floored at 0) and ``provider`` keys to
    the returned dict — on success AND error shapes. When the flag is off the
    result passes through untouched (byte-identical to baseline).
    """
    if not _latency_receipts_enabled():
        return fn()
    start = _clock()
    data = fn()
    elapsed_ms = max(0, int(round((_clock() - start) * 1000)))
    data["latency_ms"] = elapsed_ms
    data["provider"] = provider
    return data


# ---------------------------------------------------------------------------
# Raw callables — return parsed dicts; reusable by research_fact
# ---------------------------------------------------------------------------


def _brave_search_raw(query: str) -> dict[str, object]:
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


def _serpapi_search_raw_impl(query: str) -> dict[str, object]:
    """SerpAPI Google search, normalized to the Brave response shape.

    Mapping: ``organic_results[].title → title``, ``.link → url``,
    ``.snippet → description``. When an ``answer_box`` with usable content is
    present, a synthetic ``[answer box]`` result is prepended. Total results
    are capped at ``_DEFAULT_MAX_RESULTS``. Never raises.
    """
    key = os.environ.get("SERPAPI_API_KEY", "")
    if not key:
        return {"error": "SERPAPI_API_KEY not set"}
    try:
        params = urllib.parse.urlencode(
            {
                "engine": "google",
                "q": query,
                "num": _DEFAULT_MAX_RESULTS,
                "api_key": key,
            }
        )
        url = f"{_SERPAPI_ENDPOINT}?{params}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            data: object = json.load(resp)
        if not isinstance(data, dict):
            return {"error": f"unexpected SerpAPI response type: {type(data).__name__}"}
        if "error" in data:
            return {"error": str(data["error"])}
        results: list[dict[str, object]] = []
        answer_box = data.get("answer_box")
        if isinstance(answer_box, dict):
            description = str(
                answer_box.get("answer") or answer_box.get("snippet") or ""
            )
            if description:
                results.append(
                    {
                        "title": "[answer box] " + str(answer_box.get("title") or ""),
                        "url": str(answer_box.get("link") or ""),
                        "description": description,
                    }
                )
        organic = data.get("organic_results") or []
        if isinstance(organic, list):
            for item in organic:
                if not isinstance(item, dict):
                    continue
                results.append(
                    {
                        "title": str(item.get("title") or ""),
                        "url": str(item.get("link") or ""),
                        "description": str(item.get("snippet") or ""),
                    }
                )
        return {"web": {"results": results[:_DEFAULT_MAX_RESULTS]}}
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


def serpapi_search_raw(query: str) -> dict[str, object]:
    """Real-Google SERP via SerpAPI, normalized to Brave shape.  Never raises.

    Success: ``{"web": {"results": [{"title","url","description"}, ...]}}``
    (plus ``latency_ms``/``provider`` keys when receipts are enabled).
    Failure: ``{"error": "<repr or serpapi error string>"}`` (plus the same
    receipt keys when enabled).
    """
    return _with_receipt("serpapi", lambda: _serpapi_search_raw_impl(query))


def web_search_raw(query: str) -> dict[str, object]:
    """Search the configured provider and return the raw parsed response dict.

    Dispatches to Brave by default, or to SerpAPI when
    ``MAGI_WEB_SEARCH_PROVIDER=serpapi`` and ``SERPAPI_API_KEY`` are set
    (responses are normalized to the Brave shape either way).

    Returns a dict with structure ``{"web": {"results": [...]}}`` on success,
    or ``{"error": "..."}`` on any failure.  Never raises.
    """
    if _resolve_search_provider() == "serpapi":
        return serpapi_search_raw(query)
    return _with_receipt("brave", lambda: _brave_search_raw(query))


def _firecrawl_fetch_raw(url: str) -> dict[str, object]:
    """Fetch a page via Firecrawl and return the raw parsed JSON response dict."""
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


def web_fetch_raw(url: str) -> dict[str, object]:
    """Fetch a page via Firecrawl and return the raw parsed JSON response dict.

    Returns a dict with structure ``{"data": {"markdown": "..."}}`` on success,
    or ``{"error": "..."}`` on any failure.  Never raises.
    """
    return _with_receipt("firecrawl", lambda: _firecrawl_fetch_raw(url))


# ---------------------------------------------------------------------------
# Public tool callables — string-returning, used as ADK FunctionTools
# ---------------------------------------------------------------------------


def _append_receipt_footer(text: str, data: Mapping[str, object]) -> str:
    """Append the in-band latency receipt footer when the raw dict carries one.

    The keys only exist when ``MAGI_WEB_TOOL_LATENCY_RECEIPTS_ENABLED`` is
    truthy, so flag-OFF output is byte-identical to baseline.
    """
    if "latency_ms" in data and "provider" in data:
        return f"{text}\n\n[receipt] provider={data['provider']} latency_ms={data['latency_ms']}"
    return text


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
        return _append_receipt_footer(f"search error: {data['error']}", data)
    web_section = data.get("web") or {}
    results: list[dict[str, object]] = (web_section.get("results") or [])[:_DEFAULT_MAX_RESULTS]  # type: ignore[assignment]
    if not results:
        return _append_receipt_footer("No results.", data)
    lines: list[str] = []
    for item in results:
        title = str(item.get("title") or "")
        item_url = str(item.get("url") or "")
        snippet = str(item.get("description") or "")
        lines.append(f"{title}\n{item_url}\n{snippet}")
    return _append_receipt_footer("\n\n".join(lines), data)


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
        return _append_receipt_footer(f"fetch error: {data['error']}", data)
    md = str((data.get("data") or {}).get("markdown") or "")  # type: ignore[union-attr]
    return _append_receipt_footer(
        md[:_FIRECRAWL_MAX_CHARS] if md else "No content.", data
    )


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
    receipts_on = _latency_receipts_enabled()

    def _fetch_one(idx_url: tuple[int, str]) -> tuple[int, str, str | None, int | None]:
        """Fetch one URL; return (idx, url, content_or_None, latency_ms_or_None)."""
        idx, url = idx_url
        try:
            raw = fetch_fn(url)
            latency_value = raw.get("latency_ms") if isinstance(raw, dict) else None
            latency = (
                int(latency_value)
                if isinstance(latency_value, (int, float))
                else None
            )
            md = str((raw.get("data") or {}).get("markdown") or "")  # type: ignore[union-attr]
            if md:
                return idx, url, md[:_RESEARCH_FETCH_MAX_CHARS], latency
            # fetch returned empty content — skip
            return idx, url, None, latency
        except Exception:  # noqa: BLE001
            return idx, url, None, None

    indexed_urls = list(enumerate(urls))
    fetch_results: list[tuple[int, str, str | None, int | None]] = [None] * len(indexed_urls)  # type: ignore[list-item]

    # NOT a `with` block: the context-manager exit blocks on slow threads, and
    # `as_completed(...)` raises TimeoutError when the overall deadline passes —
    # previously OUTSIDE any try, violating the "Never raises" contract above.
    # On timeout we keep whatever already completed; if nothing completed the
    # search-snippet fallback below fires.
    executor = ThreadPoolExecutor(max_workers=len(indexed_urls))
    try:
        futures = {executor.submit(_fetch_one, item): item[0] for item in indexed_urls}
        for future in as_completed(futures, timeout=per_fetch_timeout * len(indexed_urls)):
            try:
                idx, url, content, latency = future.result(timeout=per_fetch_timeout)
                fetch_results[idx] = (idx, url, content, latency)
            except Exception:  # noqa: BLE001
                orig_idx = futures[future]
                fetch_results[orig_idx] = (orig_idx, urls[orig_idx], None, None)
    except Exception:  # noqa: BLE001, S110
        # Overall as_completed deadline (or any executor pathology) — fail soft.
        pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    # Assemble brief from successful fetches (preserve original order)
    successful = 0
    for entry in fetch_results:
        if entry is None:
            continue
        idx, url, content, latency = entry
        if content is not None:
            header = f"[{successful + 1}] {url}"
            if receipts_on and latency is not None:
                header += f" (latency_ms={latency})"
            source_briefs.append(f"{header}\n{content}")
            successful += 1

    if source_briefs:
        body = "\n\n---\n\n".join(source_briefs)
        if not _guidance_enabled():
            return body  # baseline bytes, default path
        header = (
            f"research_fact brief — question: {question}\n"
            f"sources: {len(source_briefs)}/{len(urls)} fetched"
            + (
                f" ({len(urls) - len(source_briefs)} failed)"
                if len(source_briefs) < len(urls)
                else ""
            )
        )
        footer = (
            "Cross-check: compare the specific values/claims across the sources above "
            "BEFORE answering. If sources disagree, state the disagreement and prefer "
            "the most authoritative/primary source; cite which source number supports "
            "your value. A value seen in only one source is a hypothesis, not a fact."
        )
        return f"{header}\n\n{body}\n\n{footer}"

    # All fetches failed — fall back to search snippets
    # (prefix byte-frozen: research/live_audit.py string-matches it)
    if search_snippets:
        search_latency = (
            search_data.get("latency_ms") if isinstance(search_data, dict) else None
        )
        if receipts_on and isinstance(search_latency, (int, float)):
            fallback_header = (
                "research_fact: all fetches failed "
                f"(search latency_ms={int(search_latency)}); "
                "returning search snippets:\n\n"
            )
        else:
            fallback_header = "research_fact: all fetches failed; returning search snippets:\n\n"
        return fallback_header + "\n\n".join(search_snippets)

    return f"research_fact: no usable sources found for: {question}"


def _guidance_enabled() -> bool:
    """Strict-truthy read of MAGI_RESEARCH_FACT_GUIDANCE_ENABLED; fail-soft OFF."""
    try:
        from magi_agent.config.env import (  # noqa: PLC0415
            is_research_fact_guidance_enabled,
        )

        return is_research_fact_guidance_enabled()
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# System-prompt guidance block (single source of truth for the text)
# ---------------------------------------------------------------------------

_WEB_RESEARCH_GUIDANCE = (
    "<web_research>\n"
    "For factual lookups (dates, numbers, names, versions, titles), prefer\n"
    "research_fact(question) over a single web_search + web_fetch: it reads several\n"
    "sources in one call and returns a per-source evidence brief.\n"
    "\n"
    "Cross-check pattern — follow it for every fact you commit:\n"
    '1. research_fact("When was the Townes Building completed?")\n'
    '2. The brief shows [1] city-register: "completed 1962" and [2] local-news:\n'
    '   "opened in 1963" — the sources DISAGREE.\n'
    "3. Do not silently take the first value. Either web_fetch the most\n"
    "   authoritative source (official/primary page) to resolve it, or state the\n"
    "   discrepancy explicitly in your answer with both sources.\n"
    "Never present a single-source value as settled fact when sources conflict.\n"
    "</web_research>"
)


def web_research_guidance_block(env: Mapping[str, str] | None = None) -> str:
    """Gated ``<web_research>`` system-prompt fragment advertising research_fact.

    Returns ``""`` unless ``MAGI_RESEARCH_FACT_GUIDANCE_ENABLED`` is truthy AND
    both ``BRAVE_API_KEY`` and ``FIRECRAWL_API_KEY`` are set (never advertise an
    unavailable tool — same rule as the file-tools prompt block). Fail-open:
    ``""`` on any error so prompt assembly never breaks.
    """
    try:
        from magi_agent.config.env import (  # noqa: PLC0415
            is_research_fact_guidance_enabled,
        )

        source: Mapping[str, str] = os.environ if env is None else env
        if not is_research_fact_guidance_enabled(source):
            return ""
        if not source.get("BRAVE_API_KEY") or not source.get("FIRECRAWL_API_KEY"):
            return ""
        return _WEB_RESEARCH_GUIDANCE
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Tool catalog builder
# ---------------------------------------------------------------------------


def _research_fact_tool(question: str) -> str:
    """Research a factual question by reading multiple web sources in parallel and return a consolidated evidence brief (URL + snippet per source, with agreement/disagreement made visible)."""
    return research_fact(question)


# ADK builds a function declaration from the callable's signature; the public
# research_fact carries injectable keyword-only callables (search_fn/fetch_fn)
# which made FunctionTool._get_declaration raise ValueError at agent-build
# time. The registered tool is this question-only wrapper; __name__ keeps the
# advertised tool name.
_research_fact_tool.__name__ = "research_fact"


def build_web_search_tools() -> list[object]:
    """Return ADK FunctionTools for web_search, web_fetch, and research_fact.

    Default-OFF: returns an empty list when no search provider is configured
    (``BRAVE_API_KEY`` absent and the SerpAPI provider not selected) or when
    ``FIRECRAWL_API_KEY`` is absent from the environment, so agents that do
    not have these keys configured receive no extra tools and no import-time
    errors. With ``MAGI_WEB_SEARCH_PROVIDER`` unset this reduces exactly to
    the original BRAVE+FIRECRAWL key check.
    """
    search_available = (
        bool(os.environ.get("BRAVE_API_KEY"))
        or _resolve_search_provider() == "serpapi"
    )
    if not search_available or not os.environ.get("FIRECRAWL_API_KEY"):
        return []
    from google.adk.tools import FunctionTool  # noqa: PLC0415

    return [
        FunctionTool(web_search),
        FunctionTool(web_fetch),
        FunctionTool(_research_fact_tool),
    ]
