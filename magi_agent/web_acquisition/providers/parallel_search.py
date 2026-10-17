"""Opt-in, anonymous Parallel Search MCP provider for native WebSearch.

The MCP SDK owns Streamable HTTP framing and session cleanup. A short-lived
AnyIO portal bridges the synchronous provider-pack contract to the async SDK,
even when WebSearch is called from an already-running event loop.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Literal

import httpx

from magi_agent import __version__
from magi_agent.web_acquisition.policy import url_policy_error

PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"
_USER_AGENT = f"magi-agent/{__version__}"


class ParallelSearchProvider:
    """Search without a Parallel key; fetch remains with existing providers."""

    # Required trust marker for the existing live-provider boundary.
    openmagi_live_provider: Literal[True] = True

    def __init__(
        self, *, timeout_s: float = 25.0, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._timeout_s = timeout_s
        self._transport = transport

    def search(self, request: object) -> Mapping[str, object]:
        query = getattr(request, "query", None)
        if not isinstance(query, str) or not query.strip():
            return {"status": "denied"}
        # Runtime refs may be readable identifiers; hash once, consistently across calls.
        session_ref = getattr(request, "session_key_digest", "session:unknown")
        session_id = hashlib.sha256(str(session_ref).encode()).hexdigest()
        try:
            from anyio.from_thread import start_blocking_portal

            with start_blocking_portal() as portal:
                return portal.call(self._search, query.strip(), session_id)
        except Exception:
            # SDK errors can contain provider output. Only fixed codes cross out.
            return {"status": "timeout", "reason": "parallel_search_failed"}

    async def _search(self, query: str, session_id: str) -> Mapping[str, object]:
        import anyio
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from mcp.types import Implementation

        # Includes initialization and dispatch, and cancels pending SDK work on expiry.
        with anyio.fail_after(self._timeout_s):
            async with httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT},
                timeout=self._timeout_s,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                async with streamable_http_client(PARALLEL_MCP_URL, http_client=client) as streams:
                    async with ClientSession(
                        streams[0],
                        streams[1],
                        client_info=Implementation(name="magi-agent", version=__version__),
                    ) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            "web_search",
                            {
                                "objective": query,
                                "search_queries": [query],
                                "session_id": session_id,
                            },
                        )
                        if result.isError:
                            return {"status": "timeout", "reason": "parallel_tool_error"}
                        data = result.structuredContent
                        if data is None:
                            text = "".join(
                                block.text for block in result.content if block.type == "text"
                            )
                            data = json.loads(text)
                        return _search_output(data)


def _search_output(data: object) -> Mapping[str, object]:
    if not isinstance(data, Mapping) or not isinstance(data.get("results"), list):
        raise ValueError("invalid Parallel search response")
    results: list[dict[str, str]] = []
    for item in data["results"][:5]:
        if not isinstance(item, Mapping):
            continue
        url = item.get("url")
        excerpts = item.get("excerpts")
        if not isinstance(url, str) or url_policy_error(url) is not None:
            continue
        if not isinstance(excerpts, list) or any(not isinstance(x, str) for x in excerpts):
            continue
        title = item.get("title")
        results.append(
            {
                "url": url,
                "title": title if isinstance(title, str) else "",
                "snippet": "\n".join(excerpts)[:4_000],
            }
        )
    if not results:
        return {"status": "denied"}
    # The existing tool projection reads top-level content as its public preview.
    preview = "\n\n".join(f"{r['title']}\n{r['url']}\n{r['snippet']}" for r in results)
    return {"results": results, "content": preview}
