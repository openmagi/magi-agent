from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from openmagi_core_agent.app import create_app
from openmagi_core_agent.config.models import BuildInfo, RuntimeConfig
from openmagi_core_agent.runtime.openmagi_runtime import OpenMagiRuntime
from openmagi_core_agent.shadow.gate5b4c2_shadow_invocation_contract import (
    Gate5B4C2ShadowAuthorityFlags,
    Gate5B4C2ShadowGateConfig,
    Gate5B4C2ShadowInvocationRequest,
    build_gate5b4c2_shadow_invocation_receipt,
)


BOT_DIGEST = "sha256:" + "a" * 64
OWNER_DIGEST = "sha256:" + "b" * 64
TURN_DIGEST = "sha256:" + "c" * 64
REQUEST_DIGEST = "sha256:" + "d" * 64
TRACE_DIGEST = "sha256:" + "e" * 64
SESSION_DIGEST = "sha256:" + "f" * 64


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


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schemaVersion": "gate5b4c2.chatProxyShadowInvocation.v1",
        "mode": "shadow_diagnostic_only",
        "responseAuthority": "typescript",
        "shadowInvocationId": "shadow_opaque_001",
        "requestIdDigest": REQUEST_DIGEST,
        "traceIdDigest": TRACE_DIGEST,
        "createdAt": 1779200000000,
        "selection": {
            "botIdDigest": BOT_DIGEST,
            "ownerUserIdDigest": OWNER_DIGEST,
            "environment": "production",
            "selectedTarget": "gate5b_selected_bot",
            "sessionKeyDigest": SESSION_DIGEST,
        },
        "turn": {
            "turnId": "turn_opaque_001",
            "turnDigest": TURN_DIGEST,
            "channelName": "telegram",
            "tsResponseCorrelationId": "ts_corr_001",
        },
        "modelRouting": {
            "perTurnProvider": "anthropic",
            "perTurnModel": "claude-3-5-sonnet-latest",
            "botConfigProvider": "openai",
            "botConfigModel": "gpt-5.2",
            "defaultProvider": "openai",
            "defaultModel": "gpt-5.2-mini",
            "routingProfileId": "prod-shadow-equivalent",
            "credentialRef": "shadow-credential-ref",
            "temperature": 0.2,
            "maxOutputTokens": 512,
            "deadlineMs": 750,
        },
        "recipeProfile": {
            "recipeId": "office-assistant",
            "recipeVersion": "2026-05-19",
            "profileId": "selected-bot-shadow",
            "profileVersion": "v1",
            "runtimeEngine": "adk-python",
            "toolsPolicy": "disabled",
            "memoryMode": "disabled",
            "sourceAuthority": "current_turn_over_memory",
        },
        "policy": {
            "typeScriptResponseAuthority": True,
            "pythonDiagnosticOnly": True,
            "outputIsolation": "local_diagnostic_only",
            "toolsDisabled": True,
            "toolHostDispatchAllowed": False,
            "memoryProviderCallsAllowed": False,
            "memoryWritesAllowed": False,
            "promptMemoryInjectionAllowed": False,
            "workspaceMutationAllowed": False,
            "childExecutionAllowed": False,
            "evidenceBlockModeAllowed": False,
        },
        "budgets": {
            "maxInputBytes": 16384,
            "maxOutputPreviewBytes": 4096,
            "maxReceiptBytes": 8192,
            "chatProxyCallTimeoutMs": 750,
            "pythonRunnerTimeoutMs": 750,
            "maxConcurrentShadowInvocations": 1,
            "maxPendingShadowInvocations": 1,
            "maxDailyShadowInvocations": 100,
            "maxCostUsd": 0,
            "retryPolicy": "none",
        },
        "redaction": {
            "status": "verified",
            "sanitizerVersion": "shadow-redactor-v1",
            "droppedFieldReasons": ("raw_auth_header",),
            "unsafeInputAction": "drop_shadow_invocation",
        },
        "authority": {},
    }
    base.update(overrides)
    return base


def _enabled_config() -> Gate5B4C2ShadowGateConfig:
    return Gate5B4C2ShadowGateConfig(
        enabled=True,
        selectedBotDigest=BOT_DIGEST,
        trustedOwnerUserIdDigest=OWNER_DIGEST,
        environment="production",
    )


def test_valid_shadow_invocation_contract_and_receipt_can_be_represented() -> None:
    request = Gate5B4C2ShadowInvocationRequest.model_validate(_payload())
    receipt = build_gate5b4c2_shadow_invocation_receipt(
        request,
        config=_enabled_config(),
        latency_ms=12,
    )

    assert receipt.accepted is True
    assert receipt.status == "accepted_for_diagnostic_shadow"
    assert receipt.reason == "accepted"
    assert receipt.shadow_invocation_id == "shadow_opaque_001"
    assert receipt.response_authority == "typescript"
    assert receipt.diagnostic_only is True
    assert receipt.fail_open is True
    assert receipt.runner_attempted is False
    assert receipt.model_call_attempted is False
    assert receipt.provider == "anthropic"
    assert receipt.model == "claude-3-5-sonnet-latest"
    assert receipt.model_selection_source == "per_turn_injected"
    assert receipt.authority.user_visible_output_allowed is False
    assert receipt.authority.tool_dispatch_allowed is False
    assert receipt.authority.memory_write_allowed is False


def test_disabled_by_default_endpoint_returns_diagnostic_skip_receipt() -> None:
    client = TestClient(create_app(_runtime()))

    response = client.post("/v1/internal/gate5b/shadow-invocations", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "gate5b4c2.shadowInvocationReceipt.v1"
    assert body["accepted"] is False
    assert body["status"] == "skipped"
    assert body["reason"] == "disabled"
    assert body["responseAuthority"] == "typescript"
    assert body["diagnosticOnly"] is True
    assert body["runnerAttempted"] is False
    assert body["modelCallAttempted"] is False
    assert body["authority"]["userVisibleOutputAllowed"] is False


@pytest.mark.parametrize(
    ("payload_updates", "config", "reason"),
    (
        ({}, Gate5B4C2ShadowGateConfig(enabled=False), "disabled"),
        ({}, Gate5B4C2ShadowGateConfig(enabled=True), "selected_scope_missing"),
        (
            {},
            Gate5B4C2ShadowGateConfig(
                enabled=True,
                selectedBotDigest=BOT_DIGEST,
                environment="production",
            ),
            "trusted_org_missing",
        ),
        (
            {"selection": {"botIdDigest": "sha256:" + "1" * 64}},
            _enabled_config(),
            "selected_scope_mismatch",
        ),
    ),
)
def test_selected_bot_org_environment_gating_and_trusted_org_required(
    payload_updates: dict[str, object],
    config: Gate5B4C2ShadowGateConfig,
    reason: str,
) -> None:
    payload = _payload()
    if "selection" in payload_updates:
        payload["selection"] = {
            **payload["selection"],  # type: ignore[arg-type]
            **payload_updates["selection"],  # type: ignore[arg-type]
        }
    request = Gate5B4C2ShadowInvocationRequest.model_validate(payload)
    receipt = build_gate5b4c2_shadow_invocation_receipt(request, config=config)

    assert receipt.accepted is False
    assert receipt.status == "skipped"
    assert receipt.reason == reason
    assert receipt.authority.canary_routing_allowed is False


@pytest.mark.parametrize(
    ("field_path", "value"),
    (
        (("requestIdDigest",), "request-short-fingerprint"),
        (("traceIdDigest",), "sha256:" + "g" * 64),
        (("selection", "botIdDigest"), "bot-raw-id"),
        (("selection", "ownerUserIdDigest"), "owner-raw-id"),
        (("turn", "turnDigest"), "turn-short-fingerprint"),
    ),
)
def test_digest_correlation_requires_sha256_digest_fields(
    field_path: tuple[str, ...],
    value: str,
) -> None:
    payload = _payload()
    target = payload
    for field in field_path[:-1]:
        target = target[field]  # type: ignore[index,assignment]
    target[field_path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValueError):
        Gate5B4C2ShadowInvocationRequest.model_validate(payload)


def test_turn_scoped_model_routing_overrides_bot_fallback() -> None:
    request = Gate5B4C2ShadowInvocationRequest.model_validate(_payload())
    fallback_request = Gate5B4C2ShadowInvocationRequest.model_validate(
        _payload(
            modelRouting={
                "botConfigProvider": "openai",
                "botConfigModel": "gpt-5.2",
                "defaultProvider": "openai",
                "defaultModel": "gpt-5.2-mini",
            }
        )
    )

    assert request.model_routing.provider == "anthropic"
    assert request.model_routing.model == "claude-3-5-sonnet-latest"
    assert request.model_routing.model_selection_source == "per_turn_injected"
    assert fallback_request.model_routing.provider == "openai"
    assert fallback_request.model_routing.model == "gpt-5.2"
    assert fallback_request.model_routing.model_selection_source == "bot_config_fallback"


def test_model_routing_accepts_field_names_and_model_copy_revalidates() -> None:
    request = Gate5B4C2ShadowInvocationRequest.model_validate(
        _payload(
            modelRouting={
                "per_turn_provider": "anthropic",
                "per_turn_model": "claude-3-5-sonnet-latest",
                "bot_config_provider": "openai",
                "bot_config_model": "gpt-5.2",
            }
        )
    )

    copied_routing = request.model_routing.model_copy()
    copied_request = request.model_copy()

    assert request.model_routing.provider == "anthropic"
    assert request.model_routing.model_selection_source == "per_turn_injected"
    assert copied_routing.provider == "anthropic"
    assert copied_routing.model_selection_source == "per_turn_injected"
    assert copied_request.model_routing.provider == "anthropic"
    assert copied_request.model_routing.model_selection_source == "per_turn_injected"


@pytest.mark.parametrize(
    "forbidden_update",
    (
        {"authorization": "Bearer raw-secret"},
        {"cookie": "session=raw"},
        {"endpointUrl": "https://example.com/shadow"},
        {"outputPath": "/tmp/shadow-output.json"},
        {"messages": [{"role": "user", "content": "raw transcript"}]},
        {"rawUserText": "raw user text must not cross this boundary"},
        {"turn": {**_payload()["turn"], "sanitizedInputText": "redacted text"}},
        {"fullTranscript": "full transcript text"},
        {"privateMemory": {"qmd": "private memory"}},
        {"rawToolArgs": {"path": "/workspace/private"}},
        {"workspacePath": "/workspace/bot"},
        {"k8sPath": "/var/lib/kubelet/pods"},
        {"telegramToken": "123456:telegram-secret"},
        {"childPrompt": "spawn a child agent"},
        {"evidenceBlockMode": True},
        {
            "turn": {
                **_payload()["turn"],
                "attachmentMetadata": [{"rawUserText": "redacted"}],
            }
        },
        {
            "turn": {
                **_payload()["turn"],
                "attachmentMetadata": [{"Authorization: Bearer raw-secret": "safe"}],
            }
        },
    ),
)
def test_forbidden_fields_are_rejected(forbidden_update: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Gate5B4C2ShadowInvocationRequest.model_validate(
            _payload(**forbidden_update)
        )


def test_authority_flags_cannot_be_set_true_via_validate_construct_or_copy() -> None:
    validated = Gate5B4C2ShadowAuthorityFlags.model_validate(
        {
            "userVisibleOutputAllowed": True,
            "canaryRoutingAllowed": True,
            "transcriptWritesAllowed": True,
            "sseWritesAllowed": True,
            "channelWritesAllowed": True,
            "dbWritesAllowed": True,
            "workspaceMutationAllowed": True,
            "memoryWriteAllowed": True,
            "toolDispatchAllowed": True,
            "childExecutionAllowed": True,
            "missionRuntimeAllowed": True,
            "evidenceBlockModeAllowed": True,
        }
    )
    constructed = Gate5B4C2ShadowAuthorityFlags.model_construct(
        userVisibleOutputAllowed=True,
        toolDispatchAllowed=True,
    )
    copied = constructed.model_copy(
        update={"memoryWriteAllowed": True, "userVisibleOutputAllowed": True}
    )

    for flags in (validated, constructed, copied):
        assert flags.user_visible_output_allowed is False
        assert flags.canary_routing_allowed is False
        assert flags.transcript_writes_allowed is False
        assert flags.sse_writes_allowed is False
        assert flags.channel_writes_allowed is False
        assert flags.db_writes_allowed is False
        assert flags.workspace_mutation_allowed is False
        assert flags.memory_write_allowed is False
        assert flags.tool_dispatch_allowed is False
        assert flags.child_execution_allowed is False
        assert flags.mission_runtime_allowed is False
        assert flags.evidence_block_mode_allowed is False


@pytest.mark.parametrize(
    "authority",
    (
        {"Authorization: Bearer raw-secret": "safe"},
        {"rawUserText": "safe"},
        {"userVisibleOutputAllowed": False, "note": "Bearer raw-secret"},
    ),
)
def test_authority_flags_reject_forbidden_keys_and_values(
    authority: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        Gate5B4C2ShadowInvocationRequest.model_validate(
            _payload(authority=authority)
        )


def test_shadow_invocation_endpoint_rejects_malformed_json_with_diagnostic_receipt() -> None:
    client = TestClient(create_app(_runtime()))

    response = client.post(
        "/v1/internal/gate5b/shadow-invocations",
        content="not-json",
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": "invalid_shadow_invocation_contract",
        "responseAuthority": "typescript",
        "diagnosticOnly": True,
    }
