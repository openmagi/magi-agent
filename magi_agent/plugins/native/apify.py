"""Apify Actor marketplace tools — REST over api.apify.com.

Two tools:
  * apify_search_actors  — free discovery (no token), public store search.
  * apify_run_actor      — paid execution (bring-your-own APIFY_TOKEN), added
                           in Task 2.

All egress targets the fixed host api.apify.com (no arbitrary-URL fetch), so the
web SSRF firewall is intentionally not applied here. Handlers never raise; every
failure returns a structured ToolResult (mirrors plugins/native/web.py).
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from magi_agent.plugins.native._common import ok_result
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

APIFY_NOT_CONFIGURED_ERROR_CODE = "apify_not_configured"

_STORE_ENDPOINT = "https://api.apify.com/v2/store"
_SEARCH_LIMIT = 10
_SEARCH_TIMEOUT_S = 30


def _error(tool: str, code: str, message: str, **meta: object) -> ToolResult:
    return ToolResult(
        status="error",
        error_code=code,
        error_message=message,
        metadata={"tool": tool, **meta},
    )


async def apify_search_actors(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    """Discover Apify Actors by keyword. Free; no APIFY_TOKEN required.

    Args:
        query: Natural-language keyword, e.g. "instagram scraper" or "google maps".

    Returns:
        Up to 10 ranked Actors, each with actor_id ("username~name"), title,
        description, categories, rating, and total_runs.
    """
    query = str(arguments.get("query") or "").strip()
    if not query:
        return _error("apify_search_actors", "apify_bad_input",
                      "apify_search_actors requires a non-empty 'query'.")
    params = urllib.parse.urlencode({"search": query, "limit": _SEARCH_LIMIT})
    request = urllib.request.Request(
        f"{_STORE_ENDPOINT}?{params}", headers={"Accept": "application/json"},
    )
    def _fetch() -> object:
        with urllib.request.urlopen(request, timeout=_SEARCH_TIMEOUT_S) as response:  # noqa: S310
            return json.load(response)

    try:
        payload = await asyncio.to_thread(_fetch)
    except Exception as exc:  # noqa: BLE001
        return _error("apify_search_actors", "apify_unreachable",
                      f"Apify store search failed: {exc!r}")
    items = ((payload or {}).get("data") or {}).get("items")
    if not isinstance(items, list):
        items = []
    actors: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or "")
        name = str(item.get("name") or "")
        if not username or not name:
            continue
        stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
        categories = item.get("categories")
        if not isinstance(categories, list):
            categories = []
        actors.append({
            "actor_id": f"{username}~{name}",
            "title": str(item.get("title") or ""),
            "description": str(item.get("description") or "")[:500],
            "categories": categories,
            "rating": stats.get("actorReviewRating"),
            "total_runs": stats.get("totalRuns"),
        })
    return ok_result("apify_search_actors", {"actors": actors, "count": len(actors)})
