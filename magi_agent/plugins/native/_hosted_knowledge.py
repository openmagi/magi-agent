"""Hosted egress path for the native ``KnowledgeSearch`` tool.

In the HOSTED environment a bot runtime container can reach the platform's
``chat-proxy`` knowledge endpoint, which fronts the real per-bot knowledge base.
This module provides a *separate* egress path that bypasses the default-off
``KnowledgeBoundary`` (the fake-provider-only safety boundary) entirely.

Activation is gated and **default-OFF**:

* ``MAGI_KNOWLEDGE_HOSTED_EGRESS_ENABLED`` must be truthy, AND
* ``BOT_ID`` and a gateway token (``GATEWAY_TOKEN`` or
  ``OPENCLAW_GATEWAY_TOKEN``) must be present.

When the gate is not satisfied, :func:`hosted_egress` returns ``None`` and the
caller falls through to the unchanged local fake/boundary path, byte-identical
to today's behaviour.

The egress is a direct in-cluster ``POST`` to::

    {CHAT_PROXY_URL}/v1/integrations/knowledge/search

with headers ``Authorization: Bearer {token}`` and ``X-Bot-Id: {BOT_ID}`` and a
JSON body ``{"query", "top_k", "collection"?, "scope"?}``. The response
``results`` are mapped into the same ToolResult output shape that
``LocalKnowledgeSourceToolBoundary`` produces so the agent sees a consistent
result regardless of which path served it.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from magi_agent.config._truthy import is_true
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.policy import redact_public_text


logger = logging.getLogger(__name__)

_FLAG_ENV = "MAGI_KNOWLEDGE_HOSTED_EGRESS_ENABLED"
_DEFAULT_CHAT_PROXY_URL = "http://chat-proxy.clawy-system.svc.cluster.local:3002"
_ENDPOINT_PATH = "/v1/integrations/knowledge/search"
_HOSTED_EGRESS_ERROR_CODE = "knowledge_hosted_egress_failed"
_DEFAULT_TOP_K = 10
_MIN_TOP_K = 1
_MAX_TOP_K = 20
_TIMEOUT_S = 30.0

HostedHandler = Callable[[Mapping[str, object], ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class _HostedConfig:
    base_url: str
    token: str
    bot_id: str


def _gateway_token() -> str:
    return (
        os.environ.get("GATEWAY_TOKEN")
        or os.environ.get("OPENCLAW_GATEWAY_TOKEN")
        or ""
    ).strip()


def _resolve_config() -> _HostedConfig | None:
    """Return hosted config when the gate is satisfied, else ``None``."""
    if not is_true(os.environ.get(_FLAG_ENV)):
        return None
    bot_id = (os.environ.get("BOT_ID") or "").strip()
    token = _gateway_token()
    if not bot_id or not token:
        return None
    base_url = (os.environ.get("CHAT_PROXY_URL") or _DEFAULT_CHAT_PROXY_URL).strip()
    base_url = base_url.rstrip("/") or _DEFAULT_CHAT_PROXY_URL
    return _HostedConfig(base_url=base_url, token=token, bot_id=bot_id)


def hosted_egress() -> HostedHandler | None:
    """Return the hosted handler when the gate is satisfied, else ``None``.

    The env is read at call time so that activation is dynamic (and testable).
    Returning ``None`` is the signal to fall through to the local fake path.
    """
    if _resolve_config() is None:
        return None
    return hosted_knowledge_search


@dataclass(frozen=True)
class _HttpResponse:
    status_code: int
    payload: object

    def json(self) -> object:
        return self.payload


async def _http_post(
    url: str,
    *,
    headers: Mapping[str, str],
    json_body: Mapping[str, object],
    timeout: float,
) -> _HttpResponse:
    """POST ``json_body`` to ``url`` and return a status/JSON envelope.

    Uses ``httpx.AsyncClient`` (the runtime's HTTP client). Isolated behind this
    seam so tests can monkeypatch it and assert on the URL / headers / body.
    """
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=dict(headers), json=dict(json_body))
    payload: object
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001 — non-JSON body is handled by the caller.
        payload = None
    return _HttpResponse(status_code=int(resp.status_code), payload=payload)


def _string_arg(arguments: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resolve_top_k(arguments: Mapping[str, object]) -> int:
    raw = arguments.get("top_k", arguments.get("topK"))
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = _DEFAULT_TOP_K
    return max(_MIN_TOP_K, min(_MAX_TOP_K, value))


def _build_body(arguments: Mapping[str, object]) -> dict[str, object]:
    body: dict[str, object] = {
        "query": _string_arg(arguments, "query", "q") or "",
        "top_k": _resolve_top_k(arguments),
    }
    collection = _string_arg(arguments, "collection")
    if collection:
        body["collection"] = collection
    scope = _string_arg(arguments, "scope")
    if scope in {"personal", "org"}:
        body["scope"] = scope
    return body


def _snippet_text(item: Mapping[str, object]) -> str:
    for key in ("snippet", "text", "content", "chunk", "body", "preview"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _content_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _visibility_for_scope(scope: object) -> str:
    if isinstance(scope, str) and scope.strip().casefold() == "org":
        return "org"
    return "private"


def _map_source(item: Mapping[str, object], index: int) -> dict[str, object]:
    collection = str(item.get("collection") or "default").strip() or "default"
    scope = item.get("scope")
    snippet = _snippet_text(item)
    safe_snippet = redact_public_text(snippet, max_chars=2000)
    source: dict[str, object] = {
        "sourceRef": f"knowledge:{collection}:{index}",
        "evidenceRef": f"evidence:knowledge:{index}",
        "title": _string_arg(item, "title") or f"{collection} result {index}",
        "contentDigest": _content_digest(snippet),
        "visibility": _visibility_for_scope(scope),
        "collection": collection,
    }
    if isinstance(scope, str) and scope.strip():
        source["scope"] = scope.strip()
    score = item.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        source["score"] = score
    if safe_snippet:
        source["text"] = safe_snippet
    return source


def _results_from_payload(payload: object) -> list[Mapping[str, object]]:
    if not isinstance(payload, Mapping):
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, Mapping)]


def _error_result(error_code: str, *, detail: str, status: int | None = None) -> ToolResult:
    metadata: dict[str, object] = {
        "toolName": "KnowledgeSearch",
        "handler": "hosted_egress",
        "reason": redact_public_text(detail, max_chars=240),
    }
    if status is not None:
        metadata["httpStatus"] = status
    return ToolResult(
        status="error",
        errorCode=error_code,
        errorMessage=redact_public_text(detail, max_chars=240),
        metadata=metadata,
    )


async def hosted_knowledge_search(
    arguments: Mapping[str, object],
    context: ToolContext,
) -> ToolResult:
    """Call the hosted KB endpoint and map the result into the tool shape.

    On any HTTP/network error or non-2xx response, returns a ``status="error"``
    ToolResult with ``errorCode=knowledge_hosted_egress_failed`` — it never
    falls back to the fake result (which would mask a real failure).
    """
    _ = context  # context not needed for the hosted call itself.
    config = _resolve_config()
    if config is None:
        # Defensive: the gate flipped between selection and execution.
        return _error_result(_HOSTED_EGRESS_ERROR_CODE, detail="hosted_egress_gate_not_satisfied")

    body = _build_body(arguments)
    if not body["query"]:
        return _error_result(_HOSTED_EGRESS_ERROR_CODE, detail="knowledge_query_required")

    url = f"{config.base_url}{_ENDPOINT_PATH}"
    headers = {
        "Authorization": f"Bearer {config.token}",
        "X-Bot-Id": config.bot_id,
        "Content-Type": "application/json",
    }

    try:
        response = await _http_post(url, headers=headers, json_body=body, timeout=_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — network/client errors map to a clean error result.
        logger.warning("hosted knowledge egress failed: %s", type(exc).__name__)
        return _error_result(_HOSTED_EGRESS_ERROR_CODE, detail=f"request_failed:{type(exc).__name__}")

    if response.status_code < 200 or response.status_code >= 300:
        logger.warning("hosted knowledge egress non-2xx: %s", response.status_code)
        return _error_result(
            _HOSTED_EGRESS_ERROR_CODE,
            detail=f"http_status_{response.status_code}",
            status=response.status_code,
        )

    items = _results_from_payload(response.json())
    sources = tuple(_map_source(item, index) for index, item in enumerate(items, start=1))
    query = redact_public_text(str(body["query"]), max_chars=512)
    output: dict[str, object] = {
        "toolName": "KnowledgeSearch",
        "query": query,
        "resultRefs": tuple(str(source["sourceRef"]) for source in sources),
        "evidenceRefs": tuple(str(source["evidenceRef"]) for source in sources),
        "sources": sources,
    }
    return ToolResult(
        status="ok",
        output=output,
        llmOutput=output,
        transcriptOutput={
            "toolName": "KnowledgeSearch",
            "resultRefs": output["resultRefs"],
            "evidenceRefs": output["evidenceRefs"],
        },
        metadata={
            "toolName": "KnowledgeSearch",
            "handler": "hosted_egress",
            "resultCount": len(sources),
        },
    )


__all__: list[str] = ["hosted_egress", "hosted_knowledge_search"]
