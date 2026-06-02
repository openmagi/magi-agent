from __future__ import annotations

from pydantic import ValidationError
import pytest

from magi_agent.shadow.gate4c0_shadow_config import (
    Gate4C0AllowlistMetadata,
    Gate4C0AuthorityFlags,
    Gate4C0BudgetPolicy,
    Gate4C0InputEnvelopeMetadata,
    Gate4C0KillSwitchMetadata,
    Gate4C0MemoryPolicy,
    Gate4C0ModelRoutingMetadata,
    Gate4C0OutputIsolationPolicy,
    Gate4C0RecipeProfileMetadata,
    Gate4C0RedactionPolicy,
    Gate4C0ShadowConfig,
    Gate4C0ToolPolicy,
    resolve_gate4c0_shadow_config,
    resolve_gate4c0_turn_scoped_model_routing,
)


BOT_DIGEST = "sha256:" + "a" * 64
ORG_DIGEST = "sha256:" + "b" * 64
SESSION_DIGEST = "sha256:" + "c" * 64
BUNDLE_DIGEST = "sha256:" + "d" * 64
PROFILE_DIGEST = "sha256:" + "e" * 64


def _config(**overrides: object) -> Gate4C0ShadowConfig:
    base = {
        "enabled": True,
        "allowlist": Gate4C0AllowlistMetadata(
            selectedBotDigest=BOT_DIGEST,
            selectedOrgDigest=ORG_DIGEST,
            environment="staging",
            botAllowlistDigests=(BOT_DIGEST,),
            orgAllowlistDigests=(ORG_DIGEST,),
            environmentAllowlist=("staging",),
        ),
        "modelRouting": Gate4C0ModelRoutingMetadata(
            provider="openai",
            model="gpt-5.4",
            modelProfile="production-default",
            routingProfileId="prod-routing-shadow-equivalent",
            credentialRef="shadow-provider-credential-ref",
            modelSelectionSource="bot_config_fallback",
        ),
        "recipeProfile": Gate4C0RecipeProfileMetadata(
            recipeSnapshotId="recipe-snapshot:gate4c0",
            profileId="openmagi-opinionated",
            profileSnapshotDigest=PROFILE_DIGEST,
            selectedPackIds=("openmagi.research",),
        ),
        "inputEnvelope": Gate4C0InputEnvelopeMetadata(
            source="gate4b_local_shadow_handoff",
            bundleIdDigest=BUNDLE_DIGEST,
            sessionIdDigest=SESSION_DIGEST,
            turnId="turn-20260518-0001",
            schemaVersion="gate4.localShadowHandoff.v1",
            redactionVerified=True,
            inputSizeBytes=4096,
            eventCount=8,
        ),
        "redactionPolicy": Gate4C0RedactionPolicy(maxInputBytes=8192, maxEventCount=16),
        "toolPolicy": Gate4C0ToolPolicy(mode="disabled"),
        "memoryPolicy": Gate4C0MemoryPolicy(mode="read_only"),
        "outputIsolation": Gate4C0OutputIsolationPolicy(),
        "budget": Gate4C0BudgetPolicy(
            maxLatencyMs=30000,
            maxQueueDepth=25,
            maxDailyShadowRuns=100,
            maxCostUsd=1.25,
        ),
        "killSwitch": Gate4C0KillSwitchMetadata(killSwitchEnabled=False),
    }
    base.update(overrides)
    return Gate4C0ShadowConfig(**base)


def test_gate4c0_represents_production_equivalent_shadow_inputs_without_runner() -> None:
    config = _config()

    decision = resolve_gate4c0_shadow_config(config)
    payload = config.model_dump(by_alias=True, mode="json")

    assert decision.status == "accepted"
    assert decision.reason == "ready_for_gate4c1_runner_approval"
    assert decision.production_equivalent_inputs is True
    assert payload["schemaVersion"] == "gate4c0.productionEquivalentShadowConfig.v1"
    assert payload["modelRouting"]["productionEquivalent"] is True
    assert payload["modelRouting"]["provider"] == "openai"
    assert payload["modelRouting"]["model"] == "gpt-5.4"
    assert payload["recipeProfile"]["recipeSnapshotId"] == "recipe-snapshot:gate4c0"
    assert payload["inputEnvelope"]["source"] == "gate4b_local_shadow_handoff"
    assert payload["attachmentFlags"]["adkRunnerInvoked"] is False
    assert payload["attachmentFlags"]["modelCalled"] is False
    assert payload["attachmentFlags"]["userVisibleOutputAttached"] is False
    assert payload["attachmentFlags"]["productionTranscriptWritten"] is False
    assert payload["attachmentFlags"]["productionSseWritten"] is False
    assert payload["attachmentFlags"]["dbWritten"] is False
    assert payload["attachmentFlags"]["channelDelivered"] is False
    assert payload["attachmentFlags"]["toolHostDispatched"] is False
    assert payload["attachmentFlags"]["liveToolsExecuted"] is False
    assert payload["attachmentFlags"]["memoryWritten"] is False
    assert payload["attachmentFlags"]["workspaceMutated"] is False
    assert payload["attachmentFlags"]["canaryRouted"] is False


def test_gate4c0_turn_scoped_model_metadata_overrides_bot_config_fallback() -> None:
    routing = resolve_gate4c0_turn_scoped_model_routing(
        perTurnProvider="openai",
        perTurnModel="gpt-5.5-turn",
        routerProvider=None,
        routerModel=None,
        botConfigProvider="google-adk",
        botConfigModel="gemini-bot-default",
        defaultProvider="google-adk",
        defaultModel="gemini-default",
        modelProfile="production-default",
        routingProfileId="turn-router-profile",
        credentialRef="shadow-provider-credential-ref",
    )

    assert routing.provider == "openai"
    assert routing.model == "gpt-5.5-turn"
    assert routing.model_selection_source == "per_turn_injected"
    assert routing.bot_config_model == "gemini-bot-default"
    assert routing.default_model == "gemini-default"


def test_gate4c0_invalid_per_turn_metadata_falls_back_to_router_then_bot_config() -> None:
    router = resolve_gate4c0_turn_scoped_model_routing(
        perTurnProvider="openai",
        perTurnModel="Authorization: Bearer unsafe-token",
        routerProvider="anthropic",
        routerModel="claude-shadow-route",
        botConfigProvider="google-adk",
        botConfigModel="gemini-bot-default",
        defaultProvider="google-adk",
        defaultModel="gemini-default",
        modelProfile="production-default",
        routingProfileId="turn-router-profile",
        credentialRef="shadow-provider-credential-ref",
    )
    bot_fallback = resolve_gate4c0_turn_scoped_model_routing(
        perTurnProvider=None,
        perTurnModel=None,
        routerProvider=None,
        routerModel=None,
        botConfigProvider="google-adk",
        botConfigModel="gemini-bot-default",
        defaultProvider="google-adk",
        defaultModel="gemini-default",
        modelProfile="production-default",
        routingProfileId="turn-router-profile",
        credentialRef="shadow-provider-credential-ref",
    )

    assert router.provider == "anthropic"
    assert router.model == "claude-shadow-route"
    assert router.model_selection_source == "router_resolved"
    assert bot_fallback.provider == "google-adk"
    assert bot_fallback.model == "gemini-bot-default"
    assert bot_fallback.model_selection_source == "bot_config_fallback"


@pytest.mark.parametrize(
    "unsafe_value",
    (
        '{"api_key": "AIza' + "a" * 32 + '"}',
        "AIza" + "b" * 32,
        "xoxc-1234567890-unsafe",
        '{"client_secret": "unsafe-client-secret"}',
        '{"access_token": "unsafe-access-token"}',
    ),
)
def test_gate4c0_model_routing_rejects_structured_provider_secrets(
    unsafe_value: str,
) -> None:
    with pytest.raises(ValidationError):
        Gate4C0ModelRoutingMetadata(
            provider="openai",
            model="gpt-5.4",
            modelProfile="production-default",
            routingProfileId="prod-routing-shadow-equivalent",
            credentialRef=unsafe_value,
            modelSelectionSource="per_turn_injected",
        )


def test_gate4c0_model_routing_rejects_concrete_model_with_invalid_source() -> None:
    with pytest.raises(ValidationError):
        Gate4C0ModelRoutingMetadata(
            provider="openai",
            model="gpt-5.4",
            modelProfile="production-default",
            routingProfileId="prod-routing-shadow-equivalent",
            credentialRef="shadow-provider-credential-ref",
            modelSelectionSource="invalid_or_missing",
        )


@pytest.mark.parametrize(
    ("provider", "model"),
    (
        ("invalid_or_missing", "gpt-5.4"),
        ("openai", "invalid_or_missing"),
    ),
)
def test_gate4c0_model_routing_rejects_partial_invalid_missing_source(
    provider: str,
    model: str,
) -> None:
    with pytest.raises(ValidationError):
        Gate4C0ModelRoutingMetadata(
            provider=provider,
            model=model,
            modelProfile="production-default",
            routingProfileId="prod-routing-shadow-equivalent",
            credentialRef="shadow-provider-credential-ref",
            modelSelectionSource="invalid_or_missing",
        )


@pytest.mark.parametrize(
    ("provider", "model"),
    (
        ("invalid_or_missing", "gpt-5.4"),
        ("openai", "invalid_or_missing"),
    ),
)
def test_gate4c0_model_routing_rejects_partial_invalid_with_concrete_source(
    provider: str,
    model: str,
) -> None:
    with pytest.raises(ValidationError):
        Gate4C0ModelRoutingMetadata(
            provider=provider,
            model=model,
            modelProfile="production-default",
            routingProfileId="prod-routing-shadow-equivalent",
            credentialRef="shadow-provider-credential-ref",
            modelSelectionSource="bot_config_fallback",
        )


@pytest.mark.parametrize(
    ("provider", "model"),
    (
        ("", "gpt-5.4"),
        ("openai", ""),
        ("   ", "gpt-5.4"),
        ("openai", "   "),
    ),
)
def test_gate4c0_model_routing_rejects_blank_concrete_source_values(
    provider: str,
    model: str,
) -> None:
    with pytest.raises(ValidationError):
        Gate4C0ModelRoutingMetadata(
            provider=provider,
            model=model,
            modelProfile="production-default",
            routingProfileId="prod-routing-shadow-equivalent",
            credentialRef="shadow-provider-credential-ref",
            modelSelectionSource="bot_config_fallback",
        )


def test_gate4c0_missing_turn_and_bot_config_uses_default_fallback() -> None:
    routing = resolve_gate4c0_turn_scoped_model_routing(
        perTurnProvider=None,
        perTurnModel=None,
        routerProvider=None,
        routerModel=None,
        botConfigProvider=None,
        botConfigModel=None,
        defaultProvider="google-adk",
        defaultModel="gemini-default",
        modelProfile="production-default",
        routingProfileId="turn-router-profile",
        credentialRef="shadow-provider-credential-ref",
    )

    assert routing.provider == "google-adk"
    assert routing.model == "gemini-default"
    assert routing.model_selection_source == "default_fallback"


@pytest.mark.parametrize(
    ("allowlist", "reason"),
    (
        (
            Gate4C0AllowlistMetadata(
                selectedBotDigest=BOT_DIGEST,
                selectedOrgDigest=ORG_DIGEST,
                environment="staging",
                botAllowlistDigests=(),
                orgAllowlistDigests=(ORG_DIGEST,),
                environmentAllowlist=("staging",),
            ),
            "missing_bot_allowlist",
        ),
        (
            Gate4C0AllowlistMetadata(
                selectedBotDigest=BOT_DIGEST,
                selectedOrgDigest=ORG_DIGEST,
                environment="staging",
                botAllowlistDigests=(BOT_DIGEST,),
                orgAllowlistDigests=(),
                environmentAllowlist=("staging",),
            ),
            "missing_org_allowlist",
        ),
        (
            Gate4C0AllowlistMetadata(
                selectedBotDigest=BOT_DIGEST,
                selectedOrgDigest=ORG_DIGEST,
                environment="staging",
                botAllowlistDigests=(BOT_DIGEST,),
                orgAllowlistDigests=(ORG_DIGEST,),
                environmentAllowlist=(),
            ),
            "missing_environment_allowlist",
        ),
    ),
)
def test_gate4c0_missing_allowlist_skips(
    allowlist: Gate4C0AllowlistMetadata,
    reason: str,
) -> None:
    decision = resolve_gate4c0_shadow_config(_config(allowlist=allowlist))

    assert decision.status == "skipped"
    assert decision.reason == reason
    assert decision.attachment_flags.adk_runner_invoked is False
    assert decision.attachment_flags.model_called is False


def test_gate4c0_kill_switch_skips() -> None:
    decision = resolve_gate4c0_shadow_config(
        _config(killSwitch=Gate4C0KillSwitchMetadata(killSwitchEnabled=True))
    )

    assert decision.status == "skipped"
    assert decision.reason == "kill_switch_enabled"
    assert decision.attachment_flags.user_visible_output_attached is False


def test_gate4c0_redaction_failed_input_is_dropped() -> None:
    config = _config(
        inputEnvelope=Gate4C0InputEnvelopeMetadata(
            source="gate4b_local_shadow_handoff",
            bundleIdDigest=BUNDLE_DIGEST,
            sessionIdDigest=SESSION_DIGEST,
            turnId="turn-20260518-0002",
            schemaVersion="gate4.localShadowHandoff.v1",
            redactionVerified=False,
            inputSizeBytes=1024,
            eventCount=3,
        )
    )

    decision = resolve_gate4c0_shadow_config(config)

    assert decision.status == "dropped"
    assert decision.reason == "redaction_not_verified"
    assert decision.attachment_flags.model_called is False


def test_gate4c0_unsafe_input_metadata_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Gate4C0InputEnvelopeMetadata(
            source="gate4b_local_shadow_handoff",
            bundleIdDigest=BUNDLE_DIGEST,
            sessionIdDigest=SESSION_DIGEST,
            turnId="Authorization: Bearer unsafe-token",
            schemaVersion="gate4.localShadowHandoff.v1",
            redactionVerified=True,
            inputSizeBytes=1024,
            eventCount=3,
        )


@pytest.mark.parametrize("mode", ("disabled", "stubbed"))
def test_gate4c0_tools_are_disabled_or_stubbed_by_policy(mode: str) -> None:
    policy = Gate4C0ToolPolicy(mode=mode)

    assert policy.mode == mode
    assert policy.live_toolhost_dispatch_attached is False
    assert policy.function_tools_attached is False
    assert policy.long_running_tools_attached is False


def test_gate4c0_rejects_live_tool_policy() -> None:
    with pytest.raises(ValidationError):
        Gate4C0ToolPolicy(mode="live")  # type: ignore[arg-type]


def test_gate4c0_memory_writes_and_prompt_injection_are_disabled() -> None:
    policy = Gate4C0MemoryPolicy(mode="read_only")
    copied = policy.model_copy(
        update={
            "memoryWritesEnabled": True,
            "promptInjectionEnabled": True,
            "providerCallsEnabled": True,
        }
    )

    assert copied.memory_writes_enabled is False
    assert copied.prompt_injection_enabled is False
    assert copied.provider_calls_enabled is False


def test_gate4c0_authority_flags_cannot_be_enabled_by_copy_or_construct() -> None:
    flags = Gate4C0AuthorityFlags()

    copied = flags.model_copy(
        update={
            "adkRunnerInvoked": True,
            "modelCalled": True,
            "userVisibleOutputAttached": True,
            "productionTranscriptWritten": True,
            "productionSseWritten": True,
            "dbWritten": True,
            "channelDelivered": True,
            "workspaceMutated": True,
            "memoryWritten": True,
            "toolHostDispatched": True,
            "liveToolsExecuted": True,
            "canaryRouted": True,
        }
    )
    constructed = Gate4C0AuthorityFlags.model_construct(
        adk_runner_invoked=True,
        model_called=True,
        user_visible_output_attached=True,
        production_transcript_written=True,
        production_sse_written=True,
        db_written=True,
        channel_delivered=True,
        workspace_mutated=True,
        memory_written=True,
        toolhost_dispatched=True,
        live_tools_executed=True,
        canary_routed=True,
    )

    for payload in (
        copied.model_dump(by_alias=True, mode="json"),
        constructed.model_dump(by_alias=True, mode="json"),
    ):
        assert all(value is False for value in payload.values())


def test_gate4c0_input_caps_drop_oversized_input() -> None:
    decision = resolve_gate4c0_shadow_config(
        _config(
            inputEnvelope=Gate4C0InputEnvelopeMetadata(
                source="gate4b_local_shadow_handoff",
                bundleIdDigest=BUNDLE_DIGEST,
                sessionIdDigest=SESSION_DIGEST,
                turnId="turn-20260518-0003",
                schemaVersion="gate4.localShadowHandoff.v1",
                redactionVerified=True,
                inputSizeBytes=9000,
                eventCount=3,
            ),
            redactionPolicy=Gate4C0RedactionPolicy(maxInputBytes=8192, maxEventCount=16),
        )
    )

    assert decision.status == "dropped"
    assert decision.reason == "input_too_large"


def test_gate4c0_output_isolation_has_false_authority_flags() -> None:
    isolation = Gate4C0OutputIsolationPolicy()

    assert isolation.output_mode == "local_diagnostic_artifacts_only"
    assert isolation.user_visible_output_attached is False
    assert isolation.production_transcript_written is False
    assert isolation.production_sse_written is False
    assert isolation.db_written is False
    assert isolation.channel_delivered is False
    assert isolation.canary_routed is False
