from __future__ import annotations

import asyncio
from typing import Any

from magi_agent.transport.usage_receipt_emit import (
    emit_runtime_direct_usage_receipt,
    usage_receipt_enabled,
)


class _Recorder:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[tuple[str, dict[str, Any], dict[str, str], float]] = []

    async def post(
        self,
        url: str,
        json_body: dict[str, Any],
        headers: dict[str, str],
        timeout_s: float,
    ) -> int:
        self.calls.append((url, json_body, headers, timeout_s))
        return self.status


def _emit(**kwargs: Any) -> str:
    return asyncio.run(emit_runtime_direct_usage_receipt(**kwargs))


def test_usage_receipt_enabled_flag_prefers_magi_name_and_accepts_legacy_alias() -> None:
    assert usage_receipt_enabled({}) is False
    assert (
        usage_receipt_enabled({"MAGI_RUNTIME_DIRECT_USAGE_RECEIPT_ENABLED": "off"})
        is False
    )
    assert (
        usage_receipt_enabled({"MAGI_RUNTIME_DIRECT_USAGE_RECEIPT_ENABLED": "true"})
        is True
    )
    assert (
        usage_receipt_enabled({"MAGI_RUNTIME_DIRECT_USAGE_RECEIPT_ENABLED": "1"})
        is True
    )
    assert (
        usage_receipt_enabled({"CORE_AGENT_PYTHON_USAGE_RECEIPT_ENABLED": "true"})
        is True
    )


def test_emit_posts_receipt_with_token_and_payload() -> None:
    rec = _Recorder(status=200)
    result = _emit(
        api_proxy_url="http://api-proxy.local:3001/",
        gateway_token="gw-secret",
        bot_id="bot-1",
        user_id="user-1",
        model="google/gemini-3.1-pro-preview",
        usage={"inputTokens": 1234, "outputTokens": 56, "cacheReadTokens": 7},
        turn_id="sha256:abc",
        http_post=rec.post,
    )
    assert result == "emitted"
    assert len(rec.calls) == 1
    url, body, headers, _timeout = rec.calls[0]
    assert url == "http://api-proxy.local:3001/v1/usage"
    assert body == {
        "source": "runtime_direct",
        "botId": "bot-1",
        "userId": "user-1",
        "model": "google/gemini-3.1-pro-preview",
        "inputTokens": 1234,
        "outputTokens": 56,
        "cacheReadTokens": 7,
        "turnId": "sha256:abc",
    }
    assert headers["authorization"] == "Bearer gw-secret"
    assert headers["x-api-key"] == "gw-secret"


def test_emit_skips_when_no_usage_or_zero_tokens() -> None:
    rec = _Recorder()
    common = dict(
        api_proxy_url="http://api-proxy.local:3001",
        gateway_token="gw",
        bot_id="b",
        user_id="u",
        model="m",
        turn_id="t",
        http_post=rec.post,
    )
    assert _emit(usage=None, **common) == "skipped"
    assert _emit(usage={"inputTokens": 0, "outputTokens": 0}, **common) == "skipped"
    assert rec.calls == []


def test_emit_skips_without_token_or_model() -> None:
    rec = _Recorder()
    assert _emit(
        api_proxy_url="http://api-proxy.local:3001",
        gateway_token="",
        bot_id="b",
        user_id="u",
        model="m",
        usage={"inputTokens": 10, "outputTokens": 1},
        turn_id="t",
        http_post=rec.post,
    ) == "skipped"
    assert rec.calls == []


def test_emit_reports_error_on_non_2xx() -> None:
    rec = _Recorder(status=401)
    assert _emit(
        api_proxy_url="http://api-proxy.local:3001",
        gateway_token="gw",
        bot_id="b",
        user_id="u",
        model="m",
        usage={"inputTokens": 10, "outputTokens": 1},
        turn_id="t",
        http_post=rec.post,
    ) == "error"


def test_emit_swallows_transport_exception() -> None:
    async def _boom(*_args: Any) -> int:
        raise RuntimeError("connection refused")

    assert _emit(
        api_proxy_url="http://api-proxy.local:3001",
        gateway_token="gw",
        bot_id="b",
        user_id="u",
        model="m",
        usage={"inputTokens": 10, "outputTokens": 1},
        turn_id="t",
        http_post=_boom,
    ) == "error"
