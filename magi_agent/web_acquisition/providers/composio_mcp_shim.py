"""Composio MCP shim provider — wraps the existing toolset bundle as a LiveProvider.

The existing ``build_web_tools()`` in ``benchmarks/gaia/web_tools.py`` creates a
new Composio ``McpToolset`` per harness call, which means any ``ConnectionError``
during a question aborts that question entirely.

This shim instantiates the Composio session once per run and catches
``ConnectionError`` / ``TimeoutError``, mapping them to ``{"status": "timeout"}``
so the router can fall back to another provider rather than aborting the question.

The shim is intentionally thin: it does not reconnect mid-run.  Session recovery
is deferred to a follow-up.  If the session fails, the router falls back to
``PlatformEndpointProvider`` for the remainder of the run.

Usage::

    from magi_agent.composio.config import resolve_composio_config
    from magi_agent.composio.mcp import build_composio_toolset_bundle
    from magi_agent.web_acquisition.providers.composio_mcp_shim import (
        ComposioMcpShimProvider,
    )

    config = resolve_composio_config(env)
    bundle = build_composio_toolset_bundle(config)
    shim = ComposioMcpShimProvider(toolset_bundle=bundle)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal


class ComposioMcpShimProvider:
    """Live provider wrapping a ``ComposioToolsetBundle``.

    The shim carries ``openmagi_live_provider = True`` so it passes the
    live-gate check in ``LiveWebAcquisitionProviderPack._live_gate_error``.

    Parameters
    ----------
    toolset_bundle:
        A ``ComposioToolsetBundle`` returned by ``build_composio_toolset_bundle``.
        The bundle must be ``active`` (``bundle.active is True``) for any
        provider call to succeed; otherwise the shim returns ``"denied"``.
    """

    openmagi_live_provider: Literal[True] = True

    def __init__(self, *, toolset_bundle: object) -> None:
        self._bundle = toolset_bundle

    def search(self, request: object) -> Mapping[str, object]:
        """Execute a web search via Composio's MCP toolset.

        The toolset is expected to expose a ``composio_search`` action or
        equivalent.  Any ``ConnectionError`` / ``TimeoutError`` is caught and
        mapped to ``{"status": "timeout"}`` so the router treats it as a
        transient failure and can fall back.
        """
        if not self._is_active():
            return {"status": "denied"}
        query = _get_str(request, "query")
        if not query:
            return {"status": "denied"}
        try:
            result = self._call_action("composio_search", {"q": query})
            return _normalise_search(result)
        except (ConnectionError, TimeoutError, OSError):
            return {"status": "timeout"}
        except Exception:
            # Unexpected Composio failure — treat as transient.
            return {"status": "timeout"}

    def fetch(self, request: object) -> Mapping[str, object]:
        """Fetch a URL via Composio.

        Falls back to ``{"status": "denied"}`` if Composio does not expose a
        fetch/browse action, since the shim has no other way to retrieve raw
        page content.
        """
        if not self._is_active():
            return {"status": "denied"}
        url = _get_str(request, "url")
        if not url:
            return {"status": "denied"}
        try:
            result = self._call_action("composio_fetch", {"url": url})
            return _normalise_fetch(result, url)
        except AttributeError:
            # Action does not exist in this toolset version.
            return {"status": "denied"}
        except (ConnectionError, TimeoutError, OSError):
            return {"status": "timeout"}
        except Exception:
            return {"status": "timeout"}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_active(self) -> bool:
        return bool(getattr(self._bundle, "active", False))

    def _call_action(self, action_name: str, payload: Mapping[str, object]) -> object:
        """Invoke a Composio action by name.

        The ``ComposioToolsetBundle`` exposes a ``call_action`` method or
        equivalent.  The exact API depends on the composio version; we use
        getattr to avoid hard-coupling to specific method names.
        """
        call_action = getattr(self._bundle, "call_action", None)
        if call_action is None:
            # Fall back: try iterating toolsets and finding the action.
            toolsets = getattr(self._bundle, "toolsets", ()) or ()
            for toolset in toolsets:
                method = getattr(toolset, "call_action", None) or getattr(toolset, "execute_action", None)
                if method is not None:
                    return method(action_name, payload)
            raise AttributeError(f"ComposioToolsetBundle has no call_action for {action_name!r}")
        return call_action(action_name, payload)


# ------------------------------------------------------------------
# Response normalisation
# ------------------------------------------------------------------


def _normalise_search(result: object) -> Mapping[str, object]:
    """Map Composio search output to ``{"results": [...]}``.

    Composio returns heterogeneous shapes depending on the toolkit/action.
    We handle the most common patterns:
    - ``{"results": [...]}``
    - ``{"data": {"results": [...]}}``
    - A list of result items directly.
    """
    if isinstance(result, Mapping):
        raw = result.get("results") or result.get("data")
        if isinstance(raw, Mapping):
            raw = raw.get("results") or raw.get("items") or []
        if isinstance(raw, list):
            return {"results": [_normalise_search_item(item) for item in raw if isinstance(item, Mapping)]}
    if isinstance(result, list):
        return {"results": [_normalise_search_item(item) for item in result if isinstance(item, Mapping)]}
    return {"results": []}


def _normalise_search_item(item: Mapping[str, object]) -> dict[str, object]:
    return {
        "url": _str_or_empty(item.get("url") or item.get("link")),
        "title": _str_or_none(item.get("title")),
        "snippet": _str_or_empty(item.get("snippet") or item.get("description") or item.get("content")),
    }


def _normalise_fetch(result: object, original_url: str) -> Mapping[str, object]:
    if isinstance(result, Mapping):
        return {
            "url": _str_or_empty(result.get("url")) or original_url,
            "title": _str_or_none(result.get("title")),
            "content": _str_or_empty(result.get("content") or result.get("text") or result.get("body")),
        }
    if isinstance(result, str):
        return {"url": original_url, "content": result}
    return {"url": original_url, "content": ""}


# ------------------------------------------------------------------
# Attribute helpers
# ------------------------------------------------------------------


def _get_str(obj: object, *attrs: str) -> str | None:
    for attr in attrs:
        value = getattr(obj, attr, None) if not isinstance(obj, Mapping) else obj.get(attr)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _str_or_none(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _str_or_empty(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""


__all__ = ["ComposioMcpShimProvider"]
