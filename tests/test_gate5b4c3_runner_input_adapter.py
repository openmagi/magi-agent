from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping

from magi_agent.shadow.gate5b4c3_runner_input_adapter import (
    build_gate5b4c3_runner_input,
)
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


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
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
        "turn": {
            "turnId": "turn_opaque_001",
            "turnDigest": TURN_DIGEST,
            "sanitizedCurrentTurnText": "Please summarize the approved redacted note.",
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


def _request(**overrides: object) -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(_payload(**overrides))


def _selected_full_toolhost_history_request() -> Gate5B4C3ShadowGenerationRequest:
    payload = _payload()
    payload["turn"] = {
        **payload["turn"],
        "sanitizedRecentHistory": (
            {
                "role": "user",
                "sanitizedText": "What did you find in the prior report?",
                "sanitizedTextDigest": "sha256:" + "7" * 64,
            },
            {
                "role": "assistant",
                "sanitizedText": "The prior report found a redacted billing anomaly.",
                "sanitizedTextDigest": "sha256:" + "8" * 64,
            },
        ),
    }
    payload["recipeProfile"] = {
        **payload["recipeProfile"],
        "toolsPolicy": "selected_full_toolhost",
        "sourceAuthority": "bounded_sanitized_recent_history",
    }
    payload["policy"] = {
        **payload["policy"],
        "toolsDisabled": False,
        "toolHostDispatchAllowed": True,
    }
    payload["budgets"] = {
        **payload["budgets"],
        "maxSanitizedHistoryMessages": 2,
    }
    return Gate5B4C3ShadowGenerationRequest.model_validate(payload)


def _assert_no_forbidden_keys(value: object) -> None:
    forbidden = {
        "authorization",
        "cookie",
        "credentials",
        "sessionkey",
        "messages",
        "fulltranscript",
        "privatememory",
        "rawtoolargs",
        "workspacepath",
        "telegramtoken",
        "childprompt",
        "endpointurl",
        "outputpath",
        "productionwritedirective",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
            assert normalized not in forbidden
            _assert_no_forbidden_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_no_forbidden_keys(child)


def test_runner_input_adapter_preserves_turn_scoped_model_routing_and_false_authority() -> None:
    result = build_gate5b4c3_runner_input(_request())

    assert result.status == "accepted"
    assert result.reason == "accepted"
    assert result.response_authority == "typescript"
    assert result.runner_input is not None
    assert result.runner_input.provider_label == "anthropic"
    assert result.runner_input.model_label == "claude-3-5-sonnet-latest"
    assert result.runner_input.routing_source == "per_turn_injected"
    assert result.runner_input.router_decision_digest == ROUTER_DIGEST
    assert result.runner_input.routing_profile_digest == PROFILE_DIGEST
    assert result.runner_input.bot_config_model_digest == BOT_CONFIG_DIGEST
    assert result.runner_input.fallback_approved is False
    assert result.runner_input.shadow_credential_ref == "server-shadow-ref"
    assert result.runner_input.max_output_tokens == 512
    assert result.runner_input.estimated_input_tokens <= 2048
    assert result.runner_input.estimated_total_tokens <= 2560
    assert result.runner_input.tools_enabled is False
    assert result.runner_input.memory_enabled is False
    assert result.runner_input.response_authority == "typescript"
    assert result.authority.user_visible_output_allowed is False
    assert result.authority.tool_dispatch_allowed is False
    assert result.authority.memory_write_allowed is False
    _assert_no_forbidden_keys(result.model_dump(by_alias=True, mode="json"))


def test_runner_input_adapter_preserves_selected_sanitized_history_for_model_context() -> None:
    result = build_gate5b4c3_runner_input(_selected_full_toolhost_history_request())

    assert result.status == "accepted"
    assert result.runner_input is not None
    assert result.runner_input.source_authority == "bounded_sanitized_recent_history"
    assert [
        item["role"] for item in result.runner_input.sanitized_recent_history
    ] == ["user", "assistant"]
    assert result.runner_input.sanitized_recent_history[1]["content"] == (
        "The prior report found a redacted billing anomaly."
    )
    assert result.runner_input.tools_enabled is True
    assert result.runner_input.tool_host_dispatch_allowed is True
    assert result.runner_input.memory_enabled is False
    assert result.runner_input.response_authority == "typescript"
    assert result.authority.user_visible_output_allowed is False
    assert result.authority.tool_dispatch_allowed is False
    assert result.authority.memory_write_allowed is False
    _assert_no_forbidden_keys(result.model_dump(by_alias=True, mode="json"))


def test_runner_input_adapter_accepts_gate1a_shadow_readonly_tool_policy() -> None:
    request = _request(
        recipeProfile={
            **_payload()["recipeProfile"],  # type: ignore[arg-type]
            "toolsPolicy": "shadow_readonly",
        },
        policy={
            **_payload()["policy"],  # type: ignore[arg-type]
            "toolsDisabled": False,
            "toolHostDispatchAllowed": True,
        },
    )

    result = build_gate5b4c3_runner_input(request)

    assert result.status == "accepted"
    assert result.runner_input is not None
    assert result.runner_input.tools_enabled is True
    assert result.runner_input.memory_enabled is False
    assert result.runner_input.workspace_enabled is False
    instruction = result.runner_input.system_instruction
    assert "read-only tools" in instruction
    assert "no-tools" not in instruction.lower()
    assert "Do not request tools" not in instruction
    assert "Do not write state" in instruction
    assert "Do not use browser" in instruction
    assert "Do not write memory" in instruction


def test_runner_input_adapter_full_toolhost_instructs_direct_answers_before_tools() -> None:
    request = _request(
        recipeProfile={
            **_payload()["recipeProfile"],  # type: ignore[arg-type]
            "toolsPolicy": "selected_full_toolhost",
        },
        policy={
            **_payload()["policy"],  # type: ignore[arg-type]
            "toolsDisabled": False,
            "toolHostDispatchAllowed": True,
        },
    )

    result = build_gate5b4c3_runner_input(request)

    assert result.status == "accepted"
    assert result.runner_input is not None
    instruction = result.runner_input.system_instruction
    normalized_instruction = instruction.lower()
    assert "answer ordinary conversation directly without tools" in normalized_instruction
    assert "Only request a tool when the user explicitly asks" in instruction
    assert "For brief replies, do not call tools" in instruction
    assert "Every turn must end with a normal text answer" in instruction
    assert "non-text runner events alone are not a valid completion" in instruction
    assert "Do not finish by promising future or background work" in instruction
    assert "selected full toolhost" in normalized_instruction


def test_runner_input_adapter_rejects_incoherent_tool_policy_metadata() -> None:
    readonly_without_dispatch = build_gate5b4c3_runner_input(
        _request(
            recipeProfile={
                **_payload()["recipeProfile"],  # type: ignore[arg-type]
                "toolsPolicy": "shadow_readonly",
            },
            policy={
                **_payload()["policy"],  # type: ignore[arg-type]
                "toolsDisabled": True,
                "toolHostDispatchAllowed": False,
            },
        )
    )
    disabled_with_dispatch = build_gate5b4c3_runner_input(
        _request(
            policy={
                **_payload()["policy"],  # type: ignore[arg-type]
                "toolsDisabled": False,
                "toolHostDispatchAllowed": True,
            },
        )
    )

    assert readonly_without_dispatch.status == "dropped"
    assert readonly_without_dispatch.reason == "unsafe_policy"
    assert readonly_without_dispatch.runner_input is None
    assert disabled_with_dispatch.status == "dropped"
    assert disabled_with_dispatch.reason == "unsafe_policy"
    assert disabled_with_dispatch.runner_input is None


def test_runner_input_adapter_keeps_bot_config_model_as_explicit_fallback_only() -> None:
    request = _request(
        modelRouting={
            "routingSource": "bot_config_fallback",
            "providerLabel": "openai",
            "modelLabel": "gpt-5.2",
            "botConfigModelDigest": BOT_CONFIG_DIGEST,
            "fallbackReason": "per_turn_unresolved",
            "fallbackApproved": True,
            "shadowCredentialRef": "server-shadow-ref",
            "credentialRefSource": "server_config",
            "maxOutputTokens": 256,
        }
    )

    result = build_gate5b4c3_runner_input(request)

    assert result.status == "accepted"
    assert result.runner_input is not None
    assert result.runner_input.provider_label == "openai"
    assert result.runner_input.model_label == "gpt-5.2"
    assert result.runner_input.routing_source == "bot_config_fallback"
    assert result.runner_input.fallback_reason == "per_turn_unresolved"
    assert result.runner_input.fallback_approved is True
    assert result.runner_input.bot_config_model_digest == BOT_CONFIG_DIGEST
    assert result.runner_input.max_output_tokens == 256


def test_runner_input_adapter_uses_conservative_token_estimate_and_total_budget() -> None:
    input_token_drop = build_gate5b4c3_runner_input(
        _request(
            turn={
                **_payload()["turn"],  # type: ignore[arg-type]
                "sanitizedCurrentTurnText": "x" * 2049,
            },
            budgets={"maxEstimatedInputTokens": 2048},
        )
    )
    total_token_drop = build_gate5b4c3_runner_input(
        _request(
            turn={
                **_payload()["turn"],  # type: ignore[arg-type]
                "sanitizedCurrentTurnText": "x" * 80,
            },
            budgets={
                "maxEstimatedInputTokens": 2048,
                "maxOutputTokens": 512,
                "maxTotalEstimatedTokens": 90,
            },
        )
    )

    assert input_token_drop.status == "dropped"
    assert input_token_drop.reason == "input_token_budget_exceeded"
    assert input_token_drop.runner_input is None
    assert total_token_drop.status == "dropped"
    assert total_token_drop.reason == "total_token_budget_exceeded"
    assert total_token_drop.runner_input is None
    assert input_token_drop.response_authority == "typescript"
    assert total_token_drop.response_authority == "typescript"


def test_runner_input_adapter_runs_boundary_local_unsafe_text_scan() -> None:
    request = _request()
    object.__setattr__(
        request.turn,
        "sanitized_current_turn_text",
        "Authorization: Bearer raw-secret-token",
    )

    result = build_gate5b4c3_runner_input(request)

    assert result.status == "dropped"
    assert result.reason == "unsafe_input"
    assert result.runner_input is None
    assert result.response_authority == "typescript"


def test_runner_input_adapter_import_boundary_has_no_adk_or_runtime_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module(
    "magi_agent.shadow.gate5b4c3_runner_input_adapter"
)
assert module is not None

forbidden = (
    "google.adk",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.routing",
    "magi_agent.workspace",
    "magi_agent.children",
    "magi_agent.evidence",
    "magi_agent.memory",
    "openai",
    "anthropic",
)
loaded = [
    name for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden)
]
if loaded:
    raise AssertionError(f"adapter loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
