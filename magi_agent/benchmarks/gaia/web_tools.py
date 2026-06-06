"""Default-off web-tool builder for the GAIA harness.

PR-D: Extends the original Composio-only builder to prefer the general
``build_live_research_boundary`` path (provider router + platform endpoints)
when ``MAGI_PLATFORM_BASE_URL`` / ``MAGI_PLATFORM_API_KEY`` are configured,
falling back to the original Composio MCP path when they are not.

Environment variables:
    MAGI_PLATFORM_BASE_URL          Platform API proxy base URL.
    MAGI_PLATFORM_API_KEY           Bearer token for the platform API.
    CORE_AGENT_PYTHON_WEB_PROVIDER_ROUTER_ENABLED
                                    Set to "1" to enable provider router.
    CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED
                                    Set to "1" to enable live web acquisition.

    MAGI_COMPOSIO_ENABLED           Set to "1" / "true" / "on" / "auto" to
                                    activate Composio (legacy path).
    COMPOSIO_API_KEY                Composio API key. Required when enabled.
    MAGI_COMPOSIO_TOOLKITS          Comma-separated toolkit names.

When neither path is configured, ``build_web_tools`` returns ``[]`` so the
harness remains runnable and testable offline — never raises.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from magi_agent.composio.config import resolve_composio_config
from magi_agent.composio.mcp import build_composio_toolset_bundle

# Re-export under private names so tests can monkeypatch cleanly.
_resolve_composio_config = resolve_composio_config
_build_composio_toolset_bundle = build_composio_toolset_bundle

_TRUE_VALUES = frozenset({"1", "on", "true", "yes"})


def _is_true(value: object) -> bool:
    return str(value or "").strip().casefold() in _TRUE_VALUES


def build_web_tools(env: Mapping[str, str] | None = None) -> list[object]:
    """Return web tool objects for the GAIA harness.

    Tries the platform-endpoint router path first; falls back to Composio MCP.

    Parameters
    ----------
    env:
        Environment mapping.  Defaults to ``os.environ`` when ``None``.

    Returns
    -------
    list[object]
        A (possibly empty) list of tool objects.  Returns ``[]`` on any
        failure — never raises.
    """
    if env is None:
        env = os.environ

    # --- Path A: platform endpoint router (PR-D primary path) ---
    platform_tools = _build_platform_tools(env)
    if platform_tools:
        return platform_tools

    # --- Path B: legacy Composio MCP (original path, preserved as fallback) ---
    return _build_composio_tools(env)


def _build_platform_tools(env: Mapping[str, str]) -> list[object]:
    """Attempt to build a ``LocalWebResearchToolBoundary`` via the router.

    Returns a non-empty list only when both ``MAGI_PLATFORM_BASE_URL`` and
    ``MAGI_PLATFORM_API_KEY`` are present.  The returned object is a thin
    adapter that exposes ``WebSearch``, ``WebFetch``, and ``WebReader`` as
    callable tool objects the harness can pass to the agent runner.
    """
    base_url = env.get("MAGI_PLATFORM_BASE_URL", "").strip()
    api_key = env.get("MAGI_PLATFORM_API_KEY", "").strip()
    if not base_url or not api_key:
        return []

    try:
        from magi_agent.web_acquisition.research_tools import build_live_research_boundary

        boundary = build_live_research_boundary(env)
        return [_WebResearchBoundaryAdapter(boundary)]
    except Exception:
        return []


def _build_composio_tools(env: Mapping[str, str]) -> list[object]:
    """Legacy Composio MCP path — preserved from the original ``build_web_tools``."""
    try:
        config = _resolve_composio_config(env)
    except Exception:
        return []

    if not config.active:
        return []

    try:
        bundle = _build_composio_toolset_bundle(config)
    except Exception:
        return []

    if bundle.active:
        return list(bundle.toolsets)
    return []


class _WebResearchBoundaryAdapter:
    """Thin adapter that wraps ``LocalWebResearchToolBoundary`` for the GAIA harness.

    The GAIA harness calls ``build_web_tools()`` and passes the returned objects
    as ADK-compatible tool objects to the agent runner.  This adapter exposes the
    boundary in the shape the harness expects while keeping the wiring contained
    here.

    The adapter is intentionally minimal: it stores the boundary reference and
    delegates any ``execute_tool`` call to it.  The harness can introspect the
    ``boundary`` attribute for testing.
    """

    def __init__(self, boundary: object) -> None:
        self.boundary = boundary

    def __repr__(self) -> str:
        return f"_WebResearchBoundaryAdapter(boundary={self.boundary!r})"
