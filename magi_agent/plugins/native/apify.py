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

_RUN_SYNC_TEMPLATE = "https://api.apify.com/v2/actors/{actor_id}/run-sync-get-dataset-items"
_DEFAULT_MAX_USD = "1.0"
_RUN_TIMEOUT_S = 300
_ITEMS_LIMIT = 100
_MAX_BODY_BYTES = 200_000


def _error(tool: str, code: str, message: str, **meta: object) -> ToolResult:
    return ToolResult(
        status="error",
        error_code=code,
        error_message=message,
        metadata={"tool": tool, **meta},
    )


def _resolve_max_usd() -> str:
    """Return a validated positive per-run USD cap, falling back to the default.

    A misconfigured APIFY_MAX_USD_PER_RUN (empty, zero, negative, non-numeric)
    must never weaken the cost cap, so anything that does not parse as a positive
    float falls back to _DEFAULT_MAX_USD.
    """
    raw = os.environ.get("APIFY_MAX_USD_PER_RUN")
    if raw:
        try:
            if float(raw) > 0:
                return raw
        except ValueError:
            pass
    return _DEFAULT_MAX_USD


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


async def apify_run_actor(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    """Run an Apify Actor and return its dataset items. Paid; needs APIFY_TOKEN.

    Args:
        actor_id: Tilde-separated id from apify_search_actors, e.g.
            "apify~instagram-scraper".
        run_input: The Actor's input as a JSON object (or a JSON string).

    Returns:
        The Actor's structured dataset items (run + fetch in one call). Hard-capped
        at APIFY_MAX_USD_PER_RUN (default $1.00) and 300s by Apify.
    """
    actor_id = str(arguments.get("actor_id") or "").strip()
    if not actor_id:
        return _error("apify_run_actor", "apify_bad_input",
                      "apify_run_actor requires 'actor_id' (e.g. 'apify~instagram-scraper').")
    token = os.environ.get("APIFY_TOKEN", "")
    if not token:
        return _error("apify_run_actor", APIFY_NOT_CONFIGURED_ERROR_CODE,
                      "apify_run_actor needs APIFY_TOKEN (your own Apify account). "
                      "Discovery via apify_search_actors works without it; running an "
                      "Actor costs money on your account.")
    raw_input = arguments.get("run_input")
    if isinstance(raw_input, str):
        text = raw_input.strip()
        try:
            run_input: object = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            return _error("apify_run_actor", "apify_bad_input",
                          f"run_input must be valid JSON: {exc}")
    elif isinstance(raw_input, dict):
        run_input = raw_input
    elif raw_input is None:
        run_input = {}
    else:
        return _error("apify_run_actor", "apify_bad_input",
                      "run_input must be a JSON object or JSON string.")
    body = json.dumps(run_input).encode("utf-8")
    query = urllib.parse.urlencode({
        "token": token,
        "timeout": _RUN_TIMEOUT_S,
        "maxTotalChargeUsd": _resolve_max_usd(),
        "format": "json",
        "limit": _ITEMS_LIMIT,
    })
    safe_actor = urllib.parse.quote(actor_id, safe="~")
    url = _RUN_SYNC_TEMPLATE.format(actor_id=safe_actor) + f"?{query}"
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )

    def _run() -> bytes:
        with urllib.request.urlopen(request, timeout=_RUN_TIMEOUT_S + 15) as response:  # noqa: S310
            return response.read(_MAX_BODY_BYTES + 1)

    try:
        raw = await asyncio.to_thread(_run)
    except urllib.error.HTTPError as exc:
        if exc.code == 408:
            return _error("apify_run_actor", "apify_run_timeout",
                          "Actor run exceeded the 300s sync limit; narrow the input "
                          "or run a smaller job.", http_status=408)
        return _error("apify_run_actor", "apify_error",
                      f"Apify returned HTTP {exc.code}.", http_status=exc.code)
    except Exception as exc:  # noqa: BLE001  # token is in the URL — never echo exc detail
        return _error("apify_run_actor", "apify_unreachable",
                      f"Apify run failed ({type(exc).__name__}).")
    if len(raw) > _MAX_BODY_BYTES:
        raw = raw[:_MAX_BODY_BYTES]
    try:
        items = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        items = []
    item_count = len(items) if isinstance(items, list) else 0
    return ok_result("apify_run_actor",
                     {"actor_id": actor_id, "items": items, "item_count": item_count})
