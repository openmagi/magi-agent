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
    "MAGI_PLATFORM_BASE_URL+MAGI_PLATFORM_API_KEY. "
    "Without any key, use the browser tool to search: navigate to a search "
    "engine results page (e.g. a google.com/search query) and read the results."
)

_WEBFETCH_NOT_CONFIGURED_MESSAGE = (
    "WebFetch is not configured. No live fetch provider is enabled, so the "
    "agent cannot fetch web pages through the native provider router. For the "
    "local CLI direct web toolset, set BRAVE_API_KEY and FIRECRAWL_API_KEY. "
    "For the native WebFetch provider router, enable live web acquisition plus "
    "the web provider router, then set MAGI_PLATFORM_BASE_URL+MAGI_PLATFORM_API_KEY "
    "or enable the advanced fetch provider gate. "
    "Without any key, use the browser tool to open the page and read it."
)

def _is_true(value: object) -> bool:
    # I-2 PR A: delegates to the canonical truthy leaf so the truthy set
    # lives in exactly one place (was a local ``_TRUE_VALUES`` frozenset).
    from magi_agent.config._truthy import is_true  # noqa: PLC0415

    return is_true(str(value or ""))


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


# ===========================================================================
# Direct Brave/SerpAPI + Firecrawl web tools, routed THROUGH ToolDispatcher (A-2)
# ===========================================================================
#
# The fast direct web tools (``web_search``/``web_fetch``/``research_fact``)
# were historically appended as bare ADK FunctionTools OUTSIDE the dispatcher,
# bypassing URL policy, egress accounting, receipts, and redaction. These
# handlers expose the SAME raw functions as dispatcher-backed registry tools so
# every call crosses ``ToolDispatcher.dispatch`` (A-2). ``web_fetch`` applies
# the native ``url_policy_error`` SSRF firewall (A-3) at the raw layer; provider
# exceptions map to safe public error codes (A-4).

#: Lowercase model-facing tool names — preserves the model/recipe contract that
#: the previous bare FunctionTools advertised.
DIRECT_WEB_SEARCH_TOOL_NAME = "web_search"
DIRECT_WEB_FETCH_TOOL_NAME = "web_fetch"
DIRECT_RESEARCH_FACT_TOOL_NAME = "research_fact"

_DIRECT_WEB_TOOL_NAMES = (
    DIRECT_WEB_SEARCH_TOOL_NAME,
    DIRECT_WEB_FETCH_TOOL_NAME,
    DIRECT_RESEARCH_FACT_TOOL_NAME,
)


def _direct_error_result(tool_name: str, raw_error: str) -> ToolResult:
    """Map a raw direct-web error string to a safe ``ToolResult``.

    The raw layer already replaced ``repr(exc)`` with safe provider codes (A-4)
    and emits ``url_policy_error`` for blocked URLs (A-3). We surface the code as
    ``error_code`` and never echo secret-bearing detail.
    """
    return ToolResult(
        status="error",
        error_code=raw_error or "provider_error",
        error_message=f"{tool_name} error: {raw_error}",
        metadata={"tool": tool_name},
    )


async def handle_web_search(
    arguments: dict[str, object], context: ToolContext
) -> ToolResult:
    """Dispatcher-backed handler for the direct ``web_search`` tool."""
    from magi_agent.tools.web_search_tools import web_search_raw  # noqa: PLC0415

    query = str(arguments.get("query") or "").strip()
    if not query:
        return ToolResult(
            status="error",
            error_code="missing_query",
            error_message="query is required",
            metadata={"tool": DIRECT_WEB_SEARCH_TOOL_NAME},
        )
    data = web_search_raw(query)
    if isinstance(data, dict) and "error" in data:
        return _direct_error_result(DIRECT_WEB_SEARCH_TOOL_NAME, str(data["error"]))
    web_section = data.get("web") if isinstance(data, dict) else None
    results = (web_section or {}).get("results") if isinstance(web_section, dict) else []
    return ToolResult(
        status="ok",
        output={"results": results},
        metadata={"tool": DIRECT_WEB_SEARCH_TOOL_NAME},
    )


async def handle_web_fetch(
    arguments: dict[str, object], context: ToolContext
) -> ToolResult:
    """Dispatcher-backed handler for the direct ``web_fetch`` tool.

    A-3: ``web_fetch_raw`` runs the native ``url_policy_error`` SSRF firewall
    before any egress; a blocked URL returns ``error_code="url_policy_error"``
    without calling Firecrawl.
    """
    from magi_agent.tools.truncation import cap_text  # noqa: PLC0415
    from magi_agent.tools.web_search_tools import (  # noqa: PLC0415
        _FIRECRAWL_MAX_CHARS,
        web_fetch_raw,
    )

    url = str(arguments.get("url") or "").strip()
    if not url:
        return ToolResult(
            status="error",
            error_code="missing_url",
            error_message="url is required",
            metadata={"tool": DIRECT_WEB_FETCH_TOOL_NAME},
        )
    data = web_fetch_raw(url)
    if isinstance(data, dict) and "error" in data:
        return _direct_error_result(DIRECT_WEB_FETCH_TOOL_NAME, str(data["error"]))
    markdown = ""
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict):
            markdown = str(inner.get("markdown") or "")
    text = cap_text(markdown, _FIRECRAWL_MAX_CHARS)[0] if markdown else "No content."
    return ToolResult(
        status="ok",
        output={"markdown": text},
        metadata={"tool": DIRECT_WEB_FETCH_TOOL_NAME},
    )


async def handle_research_fact(
    arguments: dict[str, object], context: ToolContext
) -> ToolResult:
    """Dispatcher-backed handler for the direct ``research_fact`` tool."""
    from magi_agent.tools.web_search_tools import research_fact  # noqa: PLC0415

    question = str(arguments.get("question") or "").strip()
    if not question:
        return ToolResult(
            status="error",
            error_code="missing_question",
            error_message="question is required",
            metadata={"tool": DIRECT_RESEARCH_FACT_TOOL_NAME},
        )
    brief = research_fact(question)
    return ToolResult(
        status="ok",
        output={"brief": brief},
        llm_output=brief,
        metadata={"tool": DIRECT_RESEARCH_FACT_TOOL_NAME},
    )


_DIRECT_WEB_HANDLERS = {
    DIRECT_WEB_SEARCH_TOOL_NAME: handle_web_search,
    DIRECT_WEB_FETCH_TOOL_NAME: handle_web_fetch,
    DIRECT_RESEARCH_FACT_TOOL_NAME: handle_research_fact,
}


def _direct_web_manifests() -> tuple[object, ...]:
    """Build the three direct-web ``ToolManifest`` objects (dispatcher-backed)."""
    from magi_agent.tools.manifest import ToolManifest, ToolSource  # noqa: PLC0415

    source = ToolSource(kind="native-plugin", package="magi_agent.plugins.native.web")
    common: dict[str, object] = {
        "kind": "native",
        "source": source,
        "permission": "net",
        "available_in_modes": ("plan", "act"),
        "side_effect_class": "external",
        "cost_class": "low",
        "latency_class": "interactive",
        "plugin_id": "openmagi.web",
    }
    return (
        ToolManifest(
            name=DIRECT_WEB_SEARCH_TOOL_NAME,
            description=(
                "Search the web (Brave or SerpAPI) and return the top results."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                },
                "required": ["query"],
            },
            timeout_ms=30_000,
            **common,
        ),
        ToolManifest(
            name=DIRECT_WEB_FETCH_TOOL_NAME,
            description=(
                "Fetch a web page via Firecrawl and return its readable markdown. "
                "Private/loopback/metadata/credential URLs are blocked."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL of the page to fetch.",
                    },
                },
                "required": ["url"],
            },
            timeout_ms=60_000,
            **common,
        ),
        ToolManifest(
            name=DIRECT_RESEARCH_FACT_TOOL_NAME,
            description=(
                "Research a factual question by reading multiple web sources in "
                "parallel and return a consolidated evidence brief."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The factual question to research.",
                    },
                },
                "required": ["question"],
            },
            timeout_ms=120_000,
            **common,
        ),
    )


def register_direct_web_tools(
    registry: object, *, env: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """Register the dispatcher-backed direct-web manifests on ``registry``.

    Key-gated to the SAME presence rule the old ``build_web_search_tools`` used
    (search provider + Firecrawl), so keyless installs are byte-identical: no
    manifest registered → tool not exposed.

    Returns the gated names. When a name already exists in the registry (e.g.
    the bundled ``openmagi.web`` provider-router ``web_search`` manifest), the
    manifest is left in place — :func:`bind_direct_web_handlers` rebinds its
    handler to the direct path, which is the capability the operator configured
    by supplying ``BRAVE_API_KEY``/``SERPAPI_API_KEY`` + ``FIRECRAWL_API_KEY``.
    Both paths cross ``ToolDispatcher.dispatch`` (A-2).
    """
    from magi_agent.tools.web_search_tools import (  # noqa: PLC0415
        direct_web_tools_available,
    )

    if not direct_web_tools_available(env):
        return ()
    gated: list[str] = []
    for manifest in _direct_web_manifests():
        if registry.resolve_registration(manifest.name) is None:  # type: ignore[attr-defined]
            registry.register(manifest)  # type: ignore[attr-defined]
        gated.append(manifest.name)
    return tuple(gated)


def bind_direct_web_handlers(
    registry: object, *, env: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """Bind the direct-web handlers to ``registry`` (additive + removable).

    Key-gated identically to :func:`register_direct_web_tools` so a keyless
    install never rebinds anything. For each gated name the direct handler is
    bound so every call crosses ``ToolDispatcher.dispatch`` (A-2). When a name
    is already bound to a different (non-protected) handler — e.g. the bundled
    ``openmagi.web`` provider-router ``web_search`` — it is rebound to the
    direct handler, because the operator-supplied provider keys make the direct
    Brave/SerpAPI+Firecrawl path the configured web capability. Protected
    handlers are never overridden (the registry enforces this).
    """
    from magi_agent.tools.web_search_tools import (  # noqa: PLC0415
        direct_web_tools_available,
    )

    if not direct_web_tools_available(env):
        return ()
    bound: list[str] = []
    for name, handler in _DIRECT_WEB_HANDLERS.items():
        registration = registry.resolve_registration(name)  # type: ignore[attr-defined]
        if registration is None:
            continue
        if registration.handler is handler:
            bound.append(name)
            continue
        if registration.handler is not None:
            # Rebind a non-direct handler to the direct path. ``replace`` keeps
            # the existing manifest but swaps the handler; the registry refuses
            # to downgrade protected handlers (fail-closed).
            try:
                registry.replace(  # type: ignore[attr-defined]
                    registration.manifest, handler=handler
                )
            except ValueError:
                continue
        else:
            registry.bind_handler(  # type: ignore[attr-defined]
                name, handler, enabled_by_registry_policy=True
            )
        bound.append(name)
    return tuple(bound)
