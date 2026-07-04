from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from magi_agent.transport import chat
from magi_agent.transport import chat_routes


def _runtime() -> Any:
    return SimpleNamespace(
        config=SimpleNamespace(
            api_proxy_url="http://api-proxy.local:3001",
            gateway_token="gw-secret",
            bot_id="bot-1",
            user_id="user-1",
        )
    )


def test_scheduler_noop_when_disabled(monkeypatch: Any) -> None:
    monkeypatch.delenv("MAGI_RUNTIME_DIRECT_USAGE_RECEIPT_ENABLED", raising=False)
    monkeypatch.delenv("CORE_AGENT_PYTHON_USAGE_RECEIPT_ENABLED", raising=False)
    called: list[dict[str, Any]] = []

    async def _fake_emit(**kwargs: Any) -> str:
        called.append(kwargs)
        return "emitted"

    monkeypatch.setattr("magi_agent.transport.chat_routes.emit_runtime_direct_usage_receipt", _fake_emit)

    async def _run() -> None:
        chat._schedule_runtime_direct_usage_receipt(
            runtime=_runtime(),
            model="google/gemini",
            usage={"inputTokens": 10, "outputTokens": 2},
            turn_id="sha256:x",
        )
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert called == []


def test_scheduler_noop_without_usage(monkeypatch: Any) -> None:
    monkeypatch.setenv("MAGI_RUNTIME_DIRECT_USAGE_RECEIPT_ENABLED", "true")
    called: list[dict[str, Any]] = []

    async def _fake_emit(**kwargs: Any) -> str:
        called.append(kwargs)
        return "emitted"

    monkeypatch.setattr("magi_agent.transport.chat_routes.emit_runtime_direct_usage_receipt", _fake_emit)

    async def _run() -> None:
        chat._schedule_runtime_direct_usage_receipt(
            runtime=_runtime(),
            model="google/gemini",
            usage=None,
            turn_id="sha256:x",
        )
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert called == []


def test_scheduler_emits_when_enabled(monkeypatch: Any) -> None:
    monkeypatch.setenv("MAGI_RUNTIME_DIRECT_USAGE_RECEIPT_ENABLED", "true")
    captured: dict[str, Any] = {}

    async def _fake_emit(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "emitted"

    monkeypatch.setattr("magi_agent.transport.chat_routes.emit_runtime_direct_usage_receipt", _fake_emit)

    async def _run() -> None:
        chat._schedule_runtime_direct_usage_receipt(
            runtime=_runtime(),
            model="google/gemini-3.1-pro-preview",
            usage={"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 0},
            turn_id="sha256:turn",
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert captured["bot_id"] == "bot-1"
    assert captured["user_id"] == "user-1"
    assert captured["gateway_token"] == "gw-secret"
    assert captured["model"] == "google/gemini-3.1-pro-preview"
    assert captured["usage"] == {
        "inputTokens": 10,
        "outputTokens": 2,
        "cacheReadTokens": 0,
    }
    assert captured["turn_id"] == "sha256:turn"
