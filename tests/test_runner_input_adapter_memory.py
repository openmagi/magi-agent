"""Tests for memory snapshot wiring in the gate5b4c3 runner input adapter.

Contract:
  1. _build_system_instruction(..., memory_snapshot_block=block) includes
     the block in the returned string for all tools_policy branches.
  2. _build_system_instruction without memory_snapshot_block has no
     <memory-context in the returned string.
  3. OpenMagiRuntime carries a memory_snapshot_cache attribute.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.shadow.gate5b4c3_runner_input_adapter import (
    _build_system_instruction,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationRequest,
)

MEMORY_PROJECTION_ENV = "MAGI_MEMORY_PROJECTION_ENABLED"

# Shared digests
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

_BLOCK = '<memory-context hidden="true">\n<!-- MEMORY.md -->\nRecall: ADK test user.\n</memory-context>'


def _base_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
        "mode": "shadow_generation_diagnostic",
        "responseAuthority": "typescript",
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
        "turn": {
            "turnId": "turn_opaque_001",
            "turnDigest": TURN_DIGEST,
            "sanitizedCurrentTurnText": "Please summarize.",
            "sanitizedInputTextDigest": SANITIZED_DIGEST,
            "channelName": "app_channel",
            "tsResponseCorrelationId": "ts_corr_001",
        },
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
    base.update(overrides)
    return base


def _request(tools_policy: str = "disabled") -> Gate5B4C3ShadowGenerationRequest:
    payload = _base_payload()
    payload["recipeProfile"] = {
        **payload["recipeProfile"],  # type: ignore[dict-item]
        "toolsPolicy": tools_policy,
    }
    if tools_policy == "selected_full_toolhost":
        payload["policy"] = {
            **payload["policy"],  # type: ignore[dict-item]
            "toolsDisabled": False,
            "toolHostDispatchAllowed": True,
        }
    return Gate5B4C3ShadowGenerationRequest.model_validate(payload)


# ---------------------------------------------------------------------------
# 1. memory_snapshot_block is included in the instruction
# ---------------------------------------------------------------------------


def test_build_system_instruction_disabled_policy_includes_block() -> None:
    """memory_snapshot_block appears in the instruction for disabled tools_policy."""
    req = _request("disabled")
    instruction = _build_system_instruction(req, memory_snapshot_block=_BLOCK)
    assert _BLOCK in instruction


def test_build_system_instruction_selected_full_toolhost_includes_block() -> None:
    """memory_snapshot_block appears in the instruction for selected_full_toolhost."""
    req = _request("selected_full_toolhost")
    instruction = _build_system_instruction(req, memory_snapshot_block=_BLOCK)
    assert _BLOCK in instruction


def test_build_system_instruction_shadow_readonly_includes_block() -> None:
    """memory_snapshot_block appears in the instruction for shadow_readonly."""
    payload = _base_payload()
    payload["recipeProfile"] = {
        **payload["recipeProfile"],  # type: ignore[dict-item]
        "toolsPolicy": "shadow_readonly",
    }
    payload["policy"] = {
        **payload["policy"],  # type: ignore[dict-item]
        "toolsDisabled": False,
        "toolHostDispatchAllowed": True,
    }
    req = Gate5B4C3ShadowGenerationRequest.model_validate(payload)
    instruction = _build_system_instruction(req, memory_snapshot_block=_BLOCK)
    assert _BLOCK in instruction


# ---------------------------------------------------------------------------
# 2. Without block → no <memory-context
# ---------------------------------------------------------------------------


def test_build_system_instruction_without_block_has_no_memory_context() -> None:
    """Omitting memory_snapshot_block yields no <memory-context in the instruction."""
    req = _request("disabled")
    instruction = _build_system_instruction(req)
    assert "<memory-context" not in instruction


# ---------------------------------------------------------------------------
# 3. OpenMagiRuntime carries memory_snapshot_cache
# ---------------------------------------------------------------------------


def test_openmagi_runtime_has_memory_snapshot_cache() -> None:
    """OpenMagiRuntime.__init__ attaches a memory_snapshot_cache attribute."""
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
    from magi_agent.config.models import RuntimeConfig
    from magi_agent.runtime.memory_snapshot_cache import MemorySnapshotCache

    config = RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
    )
    runtime = OpenMagiRuntime(config=config)
    assert hasattr(runtime, "memory_snapshot_cache"), (
        "OpenMagiRuntime must have a memory_snapshot_cache attribute"
    )
    assert isinstance(runtime.memory_snapshot_cache, MemorySnapshotCache)
