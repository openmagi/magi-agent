"""Default-off Composio web-tool builder for the GAIA harness.

Environment variables (all optional):
    MAGI_COMPOSIO_ENABLED   Set to "1" / "true" / "on" / "auto" to activate.
                            Defaults to "off" — Composio is disabled by default.
    COMPOSIO_API_KEY        Composio API key. Required when enabled.
    MAGI_COMPOSIO_TOOLKITS  Comma-separated list of Composio toolkit names to
                            expose (e.g. "tavily,serper").  Empty = all toolkits.

When Composio is not enabled or not fully configured, ``build_web_tools``
returns ``[]`` so the harness remains runnable and testable offline without
any external dependencies.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from magi_agent.composio.config import resolve_composio_config
from magi_agent.composio.mcp import build_composio_toolset_bundle

# Re-export under a private name so tests can monkeypatch it cleanly.
_resolve_composio_config = resolve_composio_config


def build_web_tools(env: Mapping[str, str] | None = None) -> list[Any]:
    """Return a list of live Composio toolset objects for the GAIA harness.

    Parameters
    ----------
    env:
        Environment mapping used to read configuration. Defaults to
        ``os.environ`` when *None*. Pass an explicit mapping in tests to
        keep the test suite hermetic.

    Returns
    -------
    list[object]
        A (possibly empty) list of toolset objects ready to be passed to the
        agent runner.  Returns ``[]`` whenever Composio is not enabled,
        misconfigured, or its package is not installed — never raises.
    """
    if env is None:
        env = os.environ

    try:
        config = _resolve_composio_config(env)
    except Exception:
        return []

    if not config.active:
        return []

    try:
        bundle = build_composio_toolset_bundle(config)
    except Exception:
        return []

    if bundle.active:
        return list(bundle.toolsets)
    return []
