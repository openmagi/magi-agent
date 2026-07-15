from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import (
    BuildInfo,
    PythonContextContinuityConfig,
    PythonRuntimeAuthorityConfig,
    RuntimeConfig,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.chat import Gate5BUserVisibleChatRouteConfig
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterStore,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
)
from magi_agent.transport.shadow_generations import (
    Gate5B4C3ShadowGenerationRouteConfig,
)


def _sha256(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _runtime_with_continuity(
    *,
    mocked_runner,
    continuity: PythonContextContinuityConfig | None = None,
) -> OpenMagiRuntime:
    runtime = OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-test",
            user_id="user-test",
            gateway_token="gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
            authority=PythonRuntimeAuthorityConfig(
                userVisibleOutputAllowed=True,
                canaryRoutingAllowed=True,
            ),
            contextContinuity=continuity or PythonContextContinuityConfig(),
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        mockedRunner=mocked_runner,
    )
    return runtime


def _continuity_config() -> PythonContextContinuityConfig:
    return PythonContextContinuityConfig(
        enabled=True,
        mode="local_diagnostic",
        importedEventCount=3,
        rejectedEntryCount=1,
        compactionApplied=True,
        projectionDigestPresent=True,
        modelVisibleDigestPresent=True,
        sourceTranscriptHeadDigestPresent=True,
        fallbackStatus="closed",
        reasonCodes=("committed_history_imported", "private_payload_rejected"),
    )


def _continuity_config_with_secret_like_reason_codes() -> PythonContextContinuityConfig:
    return PythonContextContinuityConfig(
        enabled=True,
        mode="local_diagnostic",
        importedEventCount=3,
        rejectedEntryCount=1,
        compactionApplied=True,
        projectionDigestPresent=True,
        modelVisibleDigestPresent=True,
        sourceTranscriptHeadDigestPresent=True,
        fallbackStatus="closed",
        reasonCodes=(
            "committed_history_imported",
            "private_payload_rejected",
            "token",
            "session_key",
            "opaque_repeated_dummy_label_0000",
        ),
    )


def test_default_off_omits_continuity_metadata_from_runner_and_fallback(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    captured: dict[str, object] = {}

    def mocked_runner(request: Mapping[str, object]) -> Mapping[str, object]:
        captured.update(request)
        raise RuntimeError("provider failed")

    client = TestClient(create_app(_runtime_with_continuity(mocked_runner=mocked_runner)))

    response = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"messages": [{"role": "user", "content": "Hi."}]},
    )

    assert response.status_code == 502
    assert "contextContinuity" not in captured
    assert "contextContinuity" not in response.json()


def test_runner_continuity_reason_codes_drop_secret_like_labels(monkeypatch) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    captured: dict[str, object] = {}

    def mocked_runner(request: Mapping[str, object]) -> Mapping[str, object]:
        captured.update(request)
        return {"content": "ok", "eventCount": 1}

    response = TestClient(
        create_app(
            _runtime_with_continuity(
                mocked_runner=mocked_runner,
                continuity=_continuity_config_with_secret_like_reason_codes(),
            )
        )
    ).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"messages": [{"role": "user", "content": "Hi."}]},
    )

    assert response.status_code == 200
    continuity = captured["contextContinuity"]
    assert isinstance(continuity, dict)
    assert continuity["reasonCodes"] == [
        "committed_history_imported",
    ]
    serialized = json.dumps(captured, sort_keys=True)
    for forbidden in (
        "private_payload_rejected",
        '"token"',
        "session_key",
        "opaque_repeated_dummy_label_0000",
    ):
        assert forbidden not in serialized


def test_mocked_runner_receives_server_continuity_metadata_not_client_spoof(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    captured: dict[str, object] = {}

    def mocked_runner(request: Mapping[str, object]) -> Mapping[str, object]:
        captured.update(request)
        return {"content": "ok", "eventCount": 1}

    client = TestClient(
        create_app(
            _runtime_with_continuity(
                mocked_runner=mocked_runner,
                continuity=_continuity_config(),
            )
        )
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "messages": [{"role": "user", "content": "Hi. Please answer briefly."}],
            "contextContinuity": {
                "rawTranscript": "Bearer raw-token /Users/kevin/private",
                "importedEventCount": 999,
                "projectionDigest": "sha256:" + "a" * 64,
            },
        },
    )

    assert response.status_code == 200
    continuity = captured["contextContinuity"]
    assert isinstance(continuity, dict)
    assert continuity["schemaVersion"] == "pregate8.contextContinuityChatDiagnostic.v1"
    assert continuity["source"] == "server_runtime_config"
    assert continuity["continuityEnabled"] is True
    assert continuity["importedEventCount"] == 3
    assert continuity["rejectedEntryCount"] == 1
    assert continuity["compactionApplied"] is True
    assert continuity["projectionDigestPresent"] is True
    assert continuity["modelVisibleDigestPresent"] is True
    assert continuity["sourceTranscriptHeadDigestPresent"] is True
    assert continuity["responseAuthority"] == "none"
    assert continuity["clientMessagesTrustedForContinuity"] is False
    assert continuity["reasonCodes"] == [
        "committed_history_imported",
    ]
    serialized = json.dumps(captured, sort_keys=True)
    assert "rawTranscript" not in serialized
    assert "Bearer raw-token" not in serialized
    assert "/Users/kevin/private" not in serialized
    assert "sha256:" + "a" * 64 not in serialized
    assert "private_payload_rejected" not in serialized


def test_mocked_runner_error_fallback_includes_sanitized_continuity_diagnostics(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")

    def mocked_runner(_request: Mapping[str, object]) -> Mapping[str, object]:
        raise RuntimeError("provider failed with Authorization: Bearer raw-token")

    client = TestClient(
        create_app(
            _runtime_with_continuity(
                mocked_runner=mocked_runner,
                continuity=_continuity_config(),
            )
        )
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "messages": [{"role": "user", "content": "Please answer in one sentence."}],
            "contextContinuity": {
                "rawTranscript": "private tool output /workspace/secrets.env",
            },
        },
    )

    assert response.status_code == 502
    body = response.json()
    assert body["responseAuthority"] == "typescript"
    assert body["fallbackStatus"] == "fallback_to_typescript"
    continuity = body["contextContinuity"]
    assert continuity["schemaVersion"] == "pregate8.contextContinuityChatDiagnostic.v1"
    assert continuity["continuityEnabled"] is True
    assert continuity["continuityCanaryReady"] is False
    assert continuity["responseAuthority"] == "none"
    assert continuity["productionAuthorityAllowed"] is False
    assert continuity["transcriptWriteAllowed"] is False
    assert continuity["sseWriteAllowed"] is False
    assert continuity["dbWriteAllowed"] is False
    serialized = json.dumps(body, sort_keys=True)
    for forbidden in (
        "Authorization:",
        "Bearer raw-token",
        "rawTranscript",
        "/workspace/secrets.env",
        "private tool output",
    ):
        assert forbidden not in serialized


def test_malformed_json_fallback_does_not_echo_raw_body_with_continuity_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    client = TestClient(
        create_app(
            _runtime_with_continuity(
                mocked_runner=lambda _request: {"content": "must not run", "eventCount": 1},
                continuity=_continuity_config(),
            )
        )
    )

    response = client.post(
        "/v1/chat/completions",
        headers={
            "authorization": "Bearer gateway-token",
            "content-type": "application/json",
        },
        content=(
            '{"messages":[{"role":"user","content":"hello"}],'
            '"contextContinuity":{"rawTranscript":"Bearer raw-token /Users/kevin/private"}'
        ),
    )

    assert response.status_code == 400
    body = response.json()
    assert body["reason"] == "malformed_json"
    assert body["contextContinuity"]["continuityEnabled"] is True
    serialized = json.dumps(body, sort_keys=True)
    assert "Bearer raw-token" not in serialized
    assert "/Users/kevin/private" not in serialized
    assert "rawTranscript" not in serialized


