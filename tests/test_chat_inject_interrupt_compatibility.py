from __future__ import annotations

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-test",
            user_id="user-test",
            gateway_token="gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
        )
    )


def test_python_inject_route_is_explicitly_unsupported_default_off(monkeypatch) -> None:
    monkeypatch.delenv("CORE_AGENT_PYTHON_CHAT_ROUTE", raising=False)

    response = TestClient(create_app(_runtime())).post(
        "/v1/chat/inject",
        headers={"authorization": "Bearer gateway-token"},
        json={"sessionKey": "agent:main:app:general", "text": "follow up"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": "chat_route_disabled",
        "reason": "python_inject_unsupported",
        "fallback": "queue_to_completions",
        "activeTurnCompatible": False,
        "responseAuthority": "typescript",
    }


def test_python_interrupt_route_is_explicitly_unsupported_when_route_enabled(monkeypatch) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")

    response = TestClient(create_app(_runtime())).post(
        "/v1/chat/interrupt",
        headers={"authorization": "Bearer gateway-token"},
        json={"sessionKey": "agent:main:app:general", "handoffRequested": True},
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": "no_active_turn",
        "reason": "python_interrupt_unsupported",
        "fallback": "typescript_interrupt_required",
        "activeTurnCompatible": False,
        "handoffRequested": True,
        "gateStateOpen": False,
        "responseAuthority": "typescript",
    }
