from __future__ import annotations

from typing import Any

from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationRequest,
)

BOT_DIGEST = "sha256:" + "a" * 64
OWNER_DIGEST = "sha256:" + "b" * 64
TURN_DIGEST = "sha256:" + "c" * 64
REQUEST_DIGEST = "sha256:" + "d" * 64
TRACE_DIGEST = "sha256:" + "e" * 64
SESSION_DIGEST = "sha256:" + "f" * 64
SANITIZED_DIGEST = "sha256:" + "1" * 64
ROUTER_DIGEST = "sha256:" + "2" * 64
PROFILE_DIGEST = "sha256:" + "3" * 64
BOT_CONFIG_DIGEST = "sha256:" + "4" * 64


def make_shadow_generation_request(
    *,
    sanitized_current_turn_text: str,
    sanitized_image_blocks: list[dict[str, Any]] | None = None,
    **kw: Any,
) -> Gate5B4C3ShadowGenerationRequest:
    """Build a minimal valid Gate5B4C3ShadowGenerationRequest for testing.

    ``sanitized_image_blocks`` is injected into the ``turn`` payload under
    the canonical alias ``sanitizedImageBlocks``.
    """
    turn: dict[str, Any] = {
        "turnId": "turn_opaque_001",
        "turnDigest": TURN_DIGEST,
        "sanitizedCurrentTurnText": sanitized_current_turn_text,
        "sanitizedInputTextDigest": SANITIZED_DIGEST,
        "channelName": "app_channel",
        "tsResponseCorrelationId": "ts_corr_001",
    }
    if sanitized_image_blocks is not None:
        turn["sanitizedImageBlocks"] = sanitized_image_blocks

    payload: dict[str, Any] = {
        "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
        "shadowGenerationId": "shadow_gen_001",
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
        "turn": turn,
        "modelRouting": {
            "routingSource": "per_turn_injected",
            "providerLabel": "anthropic",
            "modelLabel": "claude-3-5-sonnet-latest",
            "routerDecisionDigest": ROUTER_DIGEST,
            "routingProfileDigest": PROFILE_DIGEST,
            "botConfigModelDigest": BOT_CONFIG_DIGEST,
            "shadowCredentialRef": "server-shadow-ref",
            "credentialRefSource": "server_config",
            "temperature": 0.2,
            "maxOutputTokens": 512,
        },
        "recipeProfile": {
            "recipeId": "office-assistant",
            "recipeVersion": "2026-05-19",
            "profileId": "selected-bot-shadow",
            "profileVersion": "v1",
            "runtimeEngine": "adk-python",
            "toolsPolicy": "disabled",
            "memoryMode": "disabled",
            "sourceAuthority": "current_turn_only",
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
            "missionRuntimeAllowed": False,
            "evidenceBlockModeAllowed": False,
        },
        "budgets": {},
        "redaction": {
            "sanitizerId": "chat-proxy-sanitizer",
            "sanitizerVersion": "v1",
            "policyId": "gate5b4c3-redaction",
            "status": "passed",
            "redactedAt": 1779200000001,
            "redactedByteCount": 47,
            "forbiddenFieldScan": "passed",
            "sanitizedPayloadDigest": SANITIZED_DIGEST,
        },
        "authority": {},
    }
    payload.update(kw)
    return Gate5B4C3ShadowGenerationRequest.model_validate(payload)
