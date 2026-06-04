from __future__ import annotations

import pytest

from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationAuthorityFlags,
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationDiagnostic,
    Gate5B4C3ShadowGenerationRequest,
    MAX_USER_VISIBLE_OUTPUT_TOKENS,
    MAX_USER_VISIBLE_SANITIZED_INPUT_BYTES,
    build_gate5b4c3_shadow_generation_diagnostic,
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
            "sanitizedCurrentTurnText": "Please summarize the approved redacted note.",
            "sanitizedInputTextDigest": SANITIZED_DIGEST,
            "channelName": "app_channel",
            "tsResponseCorrelationId": "ts_corr_001",
            "attachmentMetadata": (
                {"kind": "image", "count": 1, "digest": "sha256:" + "5" * 64},
            ),
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
            "droppedFieldReasons": ("raw_auth_header",),
        },
        "comparison": {
            "typeScriptFinalAnswerDigest": "sha256:" + "6" * 64,
            "typeScriptTerminalStatus": "completed",
        },
        "authority": {},
    }
    base.update(overrides)
    return base


def _enabled_config() -> Gate5B4C3ShadowGenerationConfig:
    return Gate5B4C3ShadowGenerationConfig(
        enabled=True,
        killSwitchActive=False,
        capStateInitialized=True,
        providerProjectSpendControlsVerified=True,
        selectedBotDigest=BOT_DIGEST,
        trustedOwnerUserIdDigest=OWNER_DIGEST,
        environment="production",
        allowedProviderLabels=("anthropic", "openai"),
        allowedModelLabels=("claude-3-5-sonnet-latest", "gpt-5.2"),
        allowedModelRoutes=("anthropic:claude-3-5-sonnet-latest", "openai:gpt-5.2"),
        allowedShadowCredentialRefs=("server-shadow-ref",),
    )


def test_valid_generation_contract_and_accept_diagnostic_are_report_only() -> None:
    request = Gate5B4C3ShadowGenerationRequest.model_validate(_payload())
    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        request,
        config=_enabled_config(),
        latency_ms=14,
    )

    assert diagnostic.accepted is True
    assert diagnostic.status == "accepted"
    assert diagnostic.reason == "accepted"
    assert diagnostic.shadow_generation_id == "shadow_gen_001"
    assert diagnostic.response_authority == "typescript"
    assert diagnostic.diagnostic_only is True
    assert diagnostic.fail_open is True
    assert diagnostic.adk_invoked is False
    assert diagnostic.runner_attempted is False
    assert diagnostic.model_call_attempted is False
    assert diagnostic.provider == "anthropic"
    assert diagnostic.model == "claude-3-5-sonnet-latest"
    assert diagnostic.routing_source == "per_turn_injected"
    assert diagnostic.output_metadata.local_only is True
    assert diagnostic.output_metadata.output_preview_included is False
    assert diagnostic.output_metadata.output_hash_included is False
    assert diagnostic.authority.user_visible_output_allowed is False
    assert diagnostic.authority.db_writes_allowed is False


def test_generation_config_defaults_disable_and_use_first_slice_budgets() -> None:
    request = Gate5B4C3ShadowGenerationRequest.model_validate(_payload())
    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(request)

    assert diagnostic.accepted is False
    assert diagnostic.status == "skipped"
    assert diagnostic.reason == "disabled"
    assert request.budgets.chat_proxy_call_timeout_ms == 750
    assert request.budgets.python_runner_timeout_ms == 30_000
    assert request.budgets.max_sanitized_input_bytes == 8192
    assert request.budgets.max_estimated_input_tokens == 2048
    assert request.budgets.max_output_tokens == 512
    assert request.budgets.max_total_estimated_tokens == 2560
    assert request.budgets.max_diagnostic_output_preview_bytes == 2048
    assert request.budgets.max_diagnostic_artifact_bytes == 16_384
    assert request.budgets.max_concurrent_generation_runs == 1
    assert request.budgets.max_pending_generation_runs == 1
    assert request.budgets.max_daily_generation_runs == 10
    assert request.budgets.retry_policy == "none"
    assert request.budgets.max_cost_usd == pytest.approx(0.05)
    assert request.budgets.max_daily_generation_cost_usd == pytest.approx(0.50)


def test_generation_budget_allows_selected_full_toolhost_runner_timeout_ceiling() -> None:
    budget = {
        **_payload()["budgets"],
        "pythonRunnerTimeoutMs": 600_000,
    }

    request = Gate5B4C3ShadowGenerationRequest.model_validate(_payload(budgets=budget))

    assert request.budgets.python_runner_timeout_ms == 600_000


def test_generation_contract_accepts_selected_user_visible_cap_ceilings() -> None:
    sanitized_text = "x" * 9000
    payload = _payload(
        turn={
            **_payload()["turn"],
            "sanitizedCurrentTurnText": sanitized_text,
        },
        modelRouting={
            **_payload()["modelRouting"],
            "maxOutputTokens": MAX_USER_VISIBLE_OUTPUT_TOKENS,
        },
        budgets={
            "maxSanitizedInputBytes": MAX_USER_VISIBLE_SANITIZED_INPUT_BYTES,
            "maxEstimatedInputTokens": 1_000_000,
            "maxOutputTokens": MAX_USER_VISIBLE_OUTPUT_TOKENS,
            "maxTotalEstimatedTokens": 1_004_096,
            "maxDailyGenerationRuns": 100,
            "maxCostUsd": 5,
            "maxDailyGenerationCostUsd": 50,
        },
        redaction={
            **_payload()["redaction"],
            "redactedByteCount": len(sanitized_text.encode("utf-8")),
        },
    )

    request = Gate5B4C3ShadowGenerationRequest.model_validate(payload)

    assert len(request.turn.sanitized_current_turn_text.encode("utf-8")) > 8192
    assert request.budgets.max_sanitized_input_bytes == MAX_USER_VISIBLE_SANITIZED_INPUT_BYTES
    assert request.model_routing.max_output_tokens == MAX_USER_VISIBLE_OUTPUT_TOKENS
    assert request.budgets.max_output_tokens == MAX_USER_VISIBLE_OUTPUT_TOKENS


@pytest.mark.parametrize(
    ("config", "status", "reason"),
    (
        (Gate5B4C3ShadowGenerationConfig(enabled=True), "dropped", "selected_scope_missing"),
        (
            Gate5B4C3ShadowGenerationConfig(
                enabled=True,
                selectedBotDigest=BOT_DIGEST,
                environment="production",
            ),
            "dropped",
            "trusted_org_missing",
        ),
        (
            Gate5B4C3ShadowGenerationConfig(
                enabled=True,
                killSwitchActive=True,
                capStateInitialized=True,
                providerProjectSpendControlsVerified=True,
                selectedBotDigest=BOT_DIGEST,
                trustedOwnerUserIdDigest=OWNER_DIGEST,
                environment="production",
            ),
            "skipped",
            "kill_switch_active",
        ),
        (
            Gate5B4C3ShadowGenerationConfig(
                enabled=True,
                killSwitchActive=False,
                capStateInitialized=False,
                providerProjectSpendControlsVerified=True,
                selectedBotDigest=BOT_DIGEST,
                trustedOwnerUserIdDigest=OWNER_DIGEST,
                environment="production",
            ),
            "dropped",
            "cap_state_uninitialized",
        ),
    ),
)
def test_readiness_accept_skip_drop_gating(
    config: Gate5B4C3ShadowGenerationConfig,
    status: str,
    reason: str,
) -> None:
    request = Gate5B4C3ShadowGenerationRequest.model_validate(_payload())
    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(request, config=config)

    assert diagnostic.accepted is False
    assert diagnostic.status == status
    assert diagnostic.reason == reason
    assert diagnostic.adk_invoked is False


@pytest.mark.parametrize(
    "payload_update",
    (
        {"responseAuthority": "python"},
        {"redaction": {**_payload()["redaction"], "status": "failed"}},
        {"redaction": {**_payload()["redaction"], "forbiddenFieldScan": "failed"}},
        {"redaction": {**_payload()["redaction"], "sanitizedPayloadDigest": "sha256:" + "7" * 64}},
        {"redaction": {**_payload()["redaction"], "droppedFieldReasons": ("Bearer raw-token",)}},
        {"turn": {**_payload()["turn"], "sanitizedCurrentTurnText": "x" * 8193}},
    ),
)
def test_response_authority_sanitizer_proof_and_input_bounds_are_enforced(
    payload_update: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        Gate5B4C3ShadowGenerationRequest.model_validate(_payload(**payload_update))


@pytest.mark.parametrize(
    "forbidden_update",
    (
        {"authorization": "Bearer raw-secret"},
        {"cookie": "session=raw"},
        {"endpointUrl": "https://example.com/shadow"},
        {"outputPath": "/tmp/shadow-output.json"},
        {"messages": [{"role": "user", "content": "raw transcript"}]},
        {"rawUserText": "raw user text must not cross this boundary"},
        {"fullTranscript": "full transcript text"},
        {"privateMemory": {"qmd": "private memory"}},
        {"rawToolArgs": {"path": "/workspace/private"}},
        {"workspacePath": "/workspace/bot"},
        {"k8sPath": "/var/lib/kubelet/pods"},
        {"telegramToken": "123456:telegram-secret"},
        {"childPrompt": "spawn a child agent"},
        {"evidenceBlockMode": True},
        {"turn": {**_payload()["turn"], "attachmentMetadata": [{"rawToolOutput": "safe"}]}},
        {"comparison": {"nested": {"callerProvidedOutputPath": "/tmp/out"}}},
    ),
)
def test_forbidden_fields_are_rejected_recursively(
    forbidden_update: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        Gate5B4C3ShadowGenerationRequest.model_validate(_payload(**forbidden_update))


def test_turn_scoped_model_routing_requires_safe_allowlisted_server_config() -> None:
    per_turn = Gate5B4C3ShadowGenerationRequest.model_validate(_payload())
    router = Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            modelRouting={
                "routingSource": "router_resolved",
                "providerLabel": "openai",
                "modelLabel": "gpt-5.2",
                "routerDecisionDigest": ROUTER_DIGEST,
                "routingProfileDigest": PROFILE_DIGEST,
                "shadowCredentialRef": "server-shadow-ref",
                "credentialRefSource": "server_config",
            }
        )
    )
    bot_fallback = Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            modelRouting={
                "routingSource": "bot_config_fallback",
                "providerLabel": "openai",
                "modelLabel": "gpt-5.2",
                "fallbackReason": "per_turn_unresolved",
                "fallbackApproved": True,
                "botConfigModelDigest": BOT_CONFIG_DIGEST,
                "shadowCredentialRef": "server-shadow-ref",
                "credentialRefSource": "server_config",
            }
        )
    )

    assert per_turn.model_routing.routing_source == "per_turn_injected"
    assert router.model_routing.routing_source == "router_resolved"
    assert bot_fallback.model_routing.routing_source == "bot_config_fallback"

    for bad_model_routing in (
        {"routingSource": "per_turn_injected", "providerLabel": "openai", "modelLabel": "gpt-5.2"},
        {
            "routingSource": "default_fallback",
            "providerLabel": "openai",
            "modelLabel": "gpt-5.2",
            "fallbackReason": "no_turn_route",
            "fallbackApproved": False,
            "credentialRefSource": "server_config",
        },
        {
            "routingSource": "bot_config_fallback",
            "providerLabel": "openai",
            "modelLabel": "gpt-5.2",
            "botConfigModelDigest": BOT_CONFIG_DIGEST,
            "credentialRefSource": "server_config",
        },
        {
            "routingSource": "bot_config_fallback",
            "providerLabel": "openai",
            "modelLabel": "gpt-5.2",
            "fallbackReason": "per_turn_unresolved",
            "fallbackApproved": True,
            "credentialRefSource": "server_config",
        },
        {
            "routingSource": "invalid_or_missing",
            "providerLabel": "openai",
            "modelLabel": "gpt-5.2",
            "credentialRefSource": "server_config",
        },
        {
            "routingSource": "router_resolved",
            "providerLabel": "openai",
            "modelLabel": "gpt-5.2",
            "routerDecisionDigest": ROUTER_DIGEST,
            "shadowCredentialRef": "payload-selected-ref",
            "credentialRefSource": "payload",
        },
        {
            "routingSource": "router_resolved",
            "providerLabel": "openai",
            "modelLabel": "gpt-5.2",
            "routerDecisionDigest": ROUTER_DIGEST,
            "credentialRefSource": "server_config",
            "temperature": 3,
        },
    ):
        with pytest.raises(ValueError):
            Gate5B4C3ShadowGenerationRequest.model_validate(
                _payload(modelRouting=bad_model_routing)
            )


def test_model_allowlist_and_budget_exhaustion_report_drop_without_adk_invocation() -> None:
    request = Gate5B4C3ShadowGenerationRequest.model_validate(_payload())
    disallowed_model = build_gate5b4c3_shadow_generation_diagnostic(
        request,
        config=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=BOT_DIGEST,
            trustedOwnerUserIdDigest=OWNER_DIGEST,
            environment="production",
            allowedProviderLabels=("openai",),
            allowedModelLabels=("gpt-5.2",),
            allowedModelRoutes=("openai:gpt-5.2",),
            allowedShadowCredentialRefs=("server-shadow-ref",),
        ),
    )
    exhausted = build_gate5b4c3_shadow_generation_diagnostic(
        request,
        config=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            generationBudgetExhausted=True,
            selectedBotDigest=BOT_DIGEST,
            trustedOwnerUserIdDigest=OWNER_DIGEST,
            environment="production",
            allowedProviderLabels=("anthropic",),
            allowedModelLabels=("claude-3-5-sonnet-latest",),
            allowedModelRoutes=("anthropic:claude-3-5-sonnet-latest",),
            allowedShadowCredentialRefs=("server-shadow-ref",),
        ),
    )

    assert disallowed_model.status == "dropped"
    assert disallowed_model.reason == "model_routing_not_allowlisted"
    assert disallowed_model.adk_invoked is False
    assert exhausted.status == "skipped"
    assert exhausted.reason == "budget_exhausted"
    assert exhausted.adk_invoked is False


def test_model_and_credential_allowlists_fail_closed() -> None:
    request = Gate5B4C3ShadowGenerationRequest.model_validate(_payload())
    no_provider_allowlist = build_gate5b4c3_shadow_generation_diagnostic(
        request,
        config=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=BOT_DIGEST,
            trustedOwnerUserIdDigest=OWNER_DIGEST,
            environment="production",
            allowedModelLabels=("claude-3-5-sonnet-latest",),
            allowedModelRoutes=("anthropic:claude-3-5-sonnet-latest",),
            allowedShadowCredentialRefs=("server-shadow-ref",),
        ),
    )
    no_route_allowlist = build_gate5b4c3_shadow_generation_diagnostic(
        request,
        config=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=BOT_DIGEST,
            trustedOwnerUserIdDigest=OWNER_DIGEST,
            environment="production",
            allowedProviderLabels=("anthropic",),
            allowedModelLabels=("claude-3-5-sonnet-latest",),
            allowedShadowCredentialRefs=("server-shadow-ref",),
        ),
    )
    no_credential_allowlist = build_gate5b4c3_shadow_generation_diagnostic(
        request,
        config=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=BOT_DIGEST,
            trustedOwnerUserIdDigest=OWNER_DIGEST,
            environment="production",
            allowedProviderLabels=("anthropic",),
            allowedModelLabels=("claude-3-5-sonnet-latest",),
            allowedModelRoutes=("anthropic:claude-3-5-sonnet-latest",),
        ),
    )

    assert no_provider_allowlist.status == "dropped"
    assert no_provider_allowlist.reason == "model_routing_not_allowlisted"
    assert no_route_allowlist.status == "dropped"
    assert no_route_allowlist.reason == "model_routing_not_allowlisted"
    assert no_credential_allowlist.status == "dropped"
    assert no_credential_allowlist.reason == "shadow_credential_ref_not_allowlisted"


def test_model_route_allowlist_rejects_ambiguous_colon_components() -> None:
    with pytest.raises(ValueError):
        Gate5B4C3ShadowGenerationRequest.model_validate(
            _payload(
                modelRouting={
                    "routingSource": "per_turn_injected",
                    "providerLabel": "anthropic:prod",
                    "modelLabel": "claude-3-5-sonnet-latest",
                    "routerDecisionDigest": ROUTER_DIGEST,
                    "routingProfileDigest": PROFILE_DIGEST,
                    "shadowCredentialRef": "server-shadow-ref",
                    "credentialRefSource": "server_config",
                }
            )
        )

    with pytest.raises(ValueError):
        Gate5B4C3ShadowGenerationConfig(
            allowedProviderLabels=("anthropic:prod",),
            allowedModelLabels=("claude-3-5-sonnet-latest",),
            allowedModelRoutes=("anthropic:prod:claude-3-5-sonnet-latest",),
        )


def test_hard_budget_limits_cannot_be_raised_by_request_payload() -> None:
    for key, value in (
        ("pythonRunnerTimeoutMs", 600_001),
        ("maxSanitizedInputBytes", 1_000_001),
        ("maxEstimatedInputTokens", 1_000_001),
        ("maxOutputTokens", 4097),
        ("maxTotalEstimatedTokens", 1_004_097),
        ("maxDiagnosticOutputPreviewBytes", 2049),
        ("maxDiagnosticArtifactBytes", 16_385),
    ):
        budget = {
            **_payload()["budgets"],
            key: value,
        }
        with pytest.raises(ValueError):
            Gate5B4C3ShadowGenerationRequest.model_validate(_payload(budgets=budget))


def test_request_count_and_cost_budgets_are_soft_runtime_controls() -> None:
    request = Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            budgets={
                **_payload()["budgets"],
                "maxConcurrentGenerationRuns": 2,
                "maxPendingGenerationRuns": 2,
                "maxDailyGenerationRuns": 1_000,
                "maxCostUsd": 500,
                "maxDailyGenerationCostUsd": 5_000,
            }
        )
    )

    assert request.budgets.max_concurrent_generation_runs == 2
    assert request.budgets.max_pending_generation_runs == 2
    assert request.budgets.max_daily_generation_runs == 1_000
    assert request.budgets.max_cost_usd == pytest.approx(500)
    assert request.budgets.max_daily_generation_cost_usd == pytest.approx(5_000)


def test_budget_limits_accept_selected_user_visible_large_context_caps() -> None:
    request = Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            budgets={
                **_payload()["budgets"],
                "maxSanitizedInputBytes": 1_000_000,
                "maxEstimatedInputTokens": 1_000_000,
                "maxOutputTokens": 4096,
                "maxTotalEstimatedTokens": 1_004_096,
                "maxDailyGenerationRuns": 100,
                "maxCostUsd": 5,
                "maxDailyGenerationCostUsd": 50,
            }
        )
    )

    assert request.budgets.max_sanitized_input_bytes == 1_000_000
    assert request.budgets.max_estimated_input_tokens == 1_000_000
    assert request.budgets.max_output_tokens == 4096
    assert request.budgets.max_total_estimated_tokens == 1_004_096
    assert request.budgets.max_daily_generation_runs == 100
    assert request.budgets.max_cost_usd == pytest.approx(5)
    assert request.budgets.max_daily_generation_cost_usd == pytest.approx(50)


def test_spend_controls_or_cost_owner_waiver_are_required_before_acceptance() -> None:
    request = Gate5B4C3ShadowGenerationRequest.model_validate(_payload())
    missing_controls = build_gate5b4c3_shadow_generation_diagnostic(
        request,
        config=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            selectedBotDigest=BOT_DIGEST,
            trustedOwnerUserIdDigest=OWNER_DIGEST,
            environment="production",
            allowedProviderLabels=("anthropic",),
            allowedModelLabels=("claude-3-5-sonnet-latest",),
            allowedModelRoutes=("anthropic:claude-3-5-sonnet-latest",),
            allowedShadowCredentialRefs=("server-shadow-ref",),
        ),
    )
    waiver = build_gate5b4c3_shadow_generation_diagnostic(
        request,
        config=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            costOwnerWaiver=True,
            selectedBotDigest=BOT_DIGEST,
            trustedOwnerUserIdDigest=OWNER_DIGEST,
            environment="production",
            allowedProviderLabels=("anthropic",),
            allowedModelLabels=("claude-3-5-sonnet-latest",),
            allowedModelRoutes=("anthropic:claude-3-5-sonnet-latest",),
            allowedShadowCredentialRefs=("server-shadow-ref",),
        ),
    )

    assert missing_controls.status == "dropped"
    assert missing_controls.reason == "budget_exhausted"
    assert missing_controls.adk_invoked is False
    assert waiver.status == "accepted"
    assert waiver.reason == "accepted"
    assert waiver.adk_invoked is False


def test_authority_flags_cannot_be_set_true_via_validate_construct_or_copy() -> None:
    validated = Gate5B4C3ShadowGenerationAuthorityFlags.model_validate(
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
    constructed = Gate5B4C3ShadowGenerationAuthorityFlags.model_construct(
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


def test_diagnostic_status_cannot_conflict_with_accepted_flag() -> None:
    with pytest.raises(ValueError):
        Gate5B4C3ShadowGenerationDiagnostic.model_validate(
            {
                "accepted": True,
                "status": "dropped",
                "reason": "budget_exhausted",
                "shadowGenerationId": "shadow_gen_001",
                "latencyMs": 0,
                "provider": "anthropic",
                "model": "claude-3-5-sonnet-latest",
                "routingSource": "per_turn_injected",
            }
        )
