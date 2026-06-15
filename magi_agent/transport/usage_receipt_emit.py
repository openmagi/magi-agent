"""Runtime-direct usage receipt emitter.

ADK bots call their LLM provider directly, so the hosted api-proxy LLM handler
does not observe those token counts. After a served turn, the runtime can POST a
usage receipt to ``{api_proxy_url}/v1/usage`` with provider token counts. The
api-proxy remains the pricing and billing authority. Auth uses the bot gateway
token, so a receipt can only bill its own bot.

This is fire-and-forget: failures are logged and never affect the user turn.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import logging
from typing import Any

logger = logging.getLogger("magi_agent.usage_receipt")

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_PRIMARY_FLAG = "MAGI_RUNTIME_DIRECT_USAGE_RECEIPT_ENABLED"
_LEGACY_FLAG = "CORE_AGENT_PYTHON_USAGE_RECEIPT_ENABLED"

UsageHttpPost = Callable[[str, dict[str, Any], dict[str, str], float], Awaitable[int]]


def usage_receipt_enabled(env: Mapping[str, str]) -> bool:
    for name in (_PRIMARY_FLAG, _LEGACY_FLAG):
        if str(env.get(name, "")).strip().lower() in _TRUE_VALUES:
            return True
    return False


async def _httpx_post(
    url: str,
    json_body: dict[str, Any],
    headers: dict[str, str],
    timeout_s: float,
) -> int:
    import httpx

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        response = await client.post(url, json=json_body, headers=headers)
        return response.status_code


async def emit_runtime_direct_usage_receipt(
    *,
    api_proxy_url: str,
    gateway_token: str,
    bot_id: str,
    user_id: str,
    model: str,
    usage: Mapping[str, int] | None,
    turn_id: str,
    timeout_s: float = 5.0,
    http_post: UsageHttpPost | None = None,
) -> str:
    if not usage or not model or not api_proxy_url or not gateway_token:
        return "skipped"
    input_tokens = max(0, int(usage.get("inputTokens", 0) or 0))
    output_tokens = max(0, int(usage.get("outputTokens", 0) or 0))
    cache_read_tokens = max(0, int(usage.get("cacheReadTokens", 0) or 0))
    if input_tokens <= 0 and output_tokens <= 0:
        return "skipped"

    url = api_proxy_url.rstrip("/") + "/v1/usage"
    body: dict[str, Any] = {
        "source": "runtime_direct",
        "botId": bot_id,
        "userId": user_id,
        "model": model,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": cache_read_tokens,
        "turnId": turn_id,
    }
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {gateway_token}",
        "x-api-key": gateway_token,
    }
    post = http_post or _httpx_post
    try:
        status = await post(url, body, headers, timeout_s)
    except Exception as exc:  # noqa: BLE001 - never break the user turn
        logger.warning("usage_receipt_emit_failed: %s", type(exc).__name__)
        return "error"
    if 200 <= status < 300:
        return "emitted"
    logger.warning("usage_receipt_emit_non_2xx: %s", status)
    return "error"
