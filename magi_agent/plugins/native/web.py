from __future__ import annotations

import os
from collections.abc import Mapping

from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.research_tools import (
    INSANE_FETCH_ENABLED_ENV,
    JINA_READER_ENABLED_ENV,
    MAGI_PLATFORM_API_KEY_ENV,
    MAGI_PLATFORM_BASE_URL_ENV,
    PROVIDER_ROUTER_ENABLED_ENV,
    build_native_web_boundary,
    live_web_acquisition_active,
)

# Frozen contract (PR #381): external callers import these; the live wiring
# below falls back to them. Keep the code value and result shape stable.
WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE = "web_research_not_configured"

_WEBSEARCH_NOT_CONFIGURED_MESSAGE = (
    "WebSearch is not configured. No live search provider is enabled, so the "
    "agent cannot search the web. For the local CLI direct web toolset, set "
    "BRAVE_API_KEY and FIRECRAWL_API_KEY, or set MAGI_WEB_SEARCH_PROVIDER=serpapi "
    "with SERPAPI_API_KEY and FIRECRAWL_API_KEY. For the native WebSearch "
    "provider router, enable live web acquisition plus the web provider router, and set "
    "MAGI_PLATFORM_BASE_URL+MAGI_PLATFORM_API_KEY."
)

_WEBFETCH_NOT_CONFIGURED_MESSAGE = (
    "WebFetch is not configured. No live fetch provider is enabled, so the "
    "agent cannot fetch web pages through the native provider router. For the "
    "local CLI direct web toolset, set BRAVE_API_KEY and FIRECRAWL_API_KEY. "
    "For the native WebFetch provider router, enable live web acquisition plus "
    "the web provider router, then set MAGI_PLATFORM_BASE_URL+MAGI_PLATFORM_API_KEY "
    "or enable the advanced fetch provider gate."
)

# Duplicated deliberately to match the env-gate truthy convention used by
# research_tools.py / the harness-canary gates (no shared helper by design).
_TRUE_VALUES = frozenset({"1", "on", "true", "yes"})


def _is_true(value: object) -> bool:
    return str(value or "").strip().casefold() in _TRUE_VALUES


def _not_configured_result(tool_name: str) -> ToolResult:
    """Honest not-configured error for native web tools.

    Returned when no live web provider is enabled. This replaces the previous
    fabricated ``search://`` stub that was projected as a successful result and
    caused the model to hallucinate "real" web results. The live wiring in
    ``web_search``/``web_fetch`` falls back to this helper and
    ``WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE`` (frozen contract).
    """
    return ToolResult(
        status="error",
        error_code=WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE,
        error_message=(
            _WEBSEARCH_NOT_CONFIGURED_MESSAGE
            if tool_name == "WebSearch"
            else _WEBFETCH_NOT_CONFIGURED_MESSAGE
        ),
        metadata={"tool": tool_name},
    )


def _live_provider_configured(env: Mapping[str, str] | None = None) -> bool:
    """True when the env-gated live web path can actually reach a provider.

    Mirrors the three activation levels of ``build_live_research_boundary``:
    master gate (and kill switch) via ``live_web_acquisition_active``, the
    provider router gate, and at least one provider source (platform endpoint
    pair / jina-reader / insane-fetch).
    """
    resolved: Mapping[str, str] = os.environ if env is None else env
    if not live_web_acquisition_active(env=resolved):
        return False
    if not _is_true(resolved.get(PROVIDER_ROUTER_ENABLED_ENV)):
        return False
    has_platform = bool(resolved.get(MAGI_PLATFORM_BASE_URL_ENV, "").strip()) and bool(
        resolved.get(MAGI_PLATFORM_API_KEY_ENV, "").strip()
    )
    return (
        has_platform
        or _is_true(resolved.get(JINA_READER_ENABLED_ENV))
        or _is_true(resolved.get(INSANE_FETCH_ENABLED_ENV))
    )


async def web_search(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    boundary = build_native_web_boundary(os.environ)
    if boundary is None:
        return _not_configured_result("WebSearch")
    return await boundary.execute_tool("WebSearch", arguments, context)


async def web_fetch(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    boundary = build_native_web_boundary(os.environ)
    if boundary is None:
        return _not_configured_result("WebFetch")
    return await boundary.execute_tool("WebFetch", arguments, context)
