from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.runtime.model_routing import (
    ModelRouteMetadata,
    ModelRoutingDecision,
    ModelRoutingPolicyConfig,
    ModelRoutingResolutionRequest,
    RequestControlledRoutingMetadata,
    ServerSideBotConfigFallback,
    build_turn_model_routing_decision,
)


def _route(**overrides: object) -> ModelRouteMetadata:
    data: dict[str, object] = {
        "routingSource": "per_turn_injected",
        "providerLabel": "openai",
        "modelLabel": "gpt-5.5",
        "credentialRef": "server-openai-primary",
        "credentialRefSource": "server_config",
        "routerDecisionDigest": "sha256:" + "1" * 64,
    }
    data.update(overrides)
    return ModelRouteMetadata.model_validate(data)


def _fallback(**overrides: object) -> ServerSideBotConfigFallback:
    data: dict[str, object] = {
        "providerLabel": "anthropic",
        "modelLabel": "claude-opus-4-6",
        "credentialRef": "server-anthropic-primary",
        "credentialRefSource": "server_config",
        "botConfigDigest": "sha256:" + "2" * 64,
    }
    data.update(overrides)
    return ServerSideBotConfigFallback.model_validate(data)


def _enabled_config(**overrides: object) -> ModelRoutingPolicyConfig:
    data: dict[str, object] = {
        "enabled": True,
        "allowedProviderLabels": ("anthropic", "openai", "google", "fireworks"),
        "allowedModelLabels": (
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "gpt-5.5",
            "gpt-5.5-pro",
            "gemini-3.5-flash",
            "kimi-k2p6",
        ),
        "allowedModelRoutes": (
            "anthropic:claude-opus-4-6",
            "anthropic:claude-sonnet-4-6",
            "openai:gpt-5.5",
            "openai:gpt-5.5-pro",
            "google:gemini-3.5-flash",
            "fireworks:kimi-k2p6",
        ),
        "allowedCredentialRefs": (
            "server-anthropic-primary",
            "server-openai-primary",
            "server-google-primary",
            "server-fireworks-primary",
        ),
    }
    data.update(overrides)
    return ModelRoutingPolicyConfig.model_validate(data)


def test_default_policy_is_off_and_metadata_only_even_for_valid_turn_route() -> None:
    request = ModelRoutingResolutionRequest(
        turnId="turn_001",
        routingMetadata=_route(),
    )

    decision = build_turn_model_routing_decision(request)

    assert decision.accepted is False
    assert decision.status == "skipped"
    assert decision.reason == "disabled"
    assert decision.selected_provider_label is None
    assert decision.selected_model_label is None
    assert decision.routing_source is None
    assert decision.metadata_only is True
    assert decision.route_activation_allowed is False
    assert decision.provider_call_allowed is False
    assert decision.adk_runner_invocation_allowed is False
    assert decision.adk_runner_invoked is False
    assert decision.production_write_allowed is False


def test_per_turn_injected_metadata_is_canonical_over_bot_config_fallback() -> None:
    request = ModelRoutingResolutionRequest(
        turnId="turn_002",
        routingMetadata=_route(),
        botConfigFallback=_fallback(),
    )

    decision = build_turn_model_routing_decision(
        request,
        config=_enabled_config(botConfigFallbackApproved=True),
    )

    assert decision.accepted is True
    assert decision.status == "accepted"
    assert decision.reason == "accepted"
    assert decision.selected_provider_label == "openai"
    assert decision.selected_model_label == "gpt-5.5"
    assert decision.selected_credential_ref == "server-openai-primary"
    assert decision.routing_source == "per_turn_injected"
    assert decision.used_bot_config_fallback is False
    assert decision.authoritative_runtime_model_header_required is True
    assert decision.future_invocation_surface == "adk_agent_runner_only"
    assert decision.route_activation_allowed is False
    assert decision.provider_call_allowed is False
    assert decision.adk_runner_invoked is False


def test_router_resolved_metadata_is_canonical_over_bot_config_fallback() -> None:
    request = ModelRoutingResolutionRequest(
        turnId="turn_003",
        routingMetadata=_route(
            routingSource="router_resolved",
            providerLabel="google",
            modelLabel="gemini-3.5-flash",
            credentialRef="server-google-primary",
        ),
        botConfigFallback=_fallback(),
    )

    decision = build_turn_model_routing_decision(
        request,
        config=_enabled_config(botConfigFallbackApproved=True),
    )

    assert decision.accepted is True
    assert decision.routing_source == "router_resolved"
    assert decision.model_dump(by_alias=True)["routingSource"] == "router_resolved"
    assert decision.selected_provider_label == "google"
    assert decision.selected_model_label == "gemini-3.5-flash"
    assert decision.used_bot_config_fallback is False


def test_legacy_router_decision_source_is_rejected_by_metadata_and_decision_models() -> None:
    with pytest.raises(ValidationError):
        _route(routingSource="router_decision")

    with pytest.raises(ValidationError):
        ModelRoutingDecision.model_validate(
            {
                "accepted": True,
                "status": "accepted",
                "reason": "accepted",
                "selectedProviderLabel": "google",
                "selectedModelLabel": "gemini-3.5-flash",
                "routingSource": "router_decision",
                "authoritativeRuntimeModelHeaderRequired": True,
            },
        )


def test_bot_config_fallback_requires_explicit_server_approval() -> None:
    request = ModelRoutingResolutionRequest(
        turnId="turn_004",
        botConfigFallback=_fallback(),
    )

    rejected = build_turn_model_routing_decision(request, config=_enabled_config())

    assert rejected.accepted is False
    assert rejected.status == "rejected"
    assert rejected.reason == "fallback_disabled"
    assert rejected.used_bot_config_fallback is False

    accepted = build_turn_model_routing_decision(
        request,
        config=_enabled_config(botConfigFallbackApproved=True),
    )

    assert accepted.accepted is True
    assert accepted.status == "accepted"
    assert accepted.reason == "accepted"
    assert accepted.routing_source == "bot_config_fallback"
    assert accepted.selected_provider_label == "anthropic"
    assert accepted.selected_model_label == "claude-opus-4-6"
    assert accepted.used_bot_config_fallback is True


def test_request_controlled_provider_model_and_credentials_are_rejected() -> None:
    request = ModelRoutingResolutionRequest(
        turnId="turn_005",
        requestControlledRouting=RequestControlledRoutingMetadata.model_validate(
            {
                "providerLabel": "openai",
                "modelLabel": "gpt-5.5-pro",
                "credentialRef": "server-openai-primary",
            },
        ),
        botConfigFallback=_fallback(),
    )

    decision = build_turn_model_routing_decision(
        request,
        config=_enabled_config(botConfigFallbackApproved=True),
    )

    assert decision.accepted is False
    assert decision.status == "rejected"
    assert decision.reason == "request_controlled_escalation"
    assert decision.selected_provider_label is None
    assert decision.selected_model_label is None
    assert decision.provider_call_allowed is False


@pytest.mark.parametrize(
    "turn_id",
    (
        "01KQDYPRMTY0VRVHFCFBHKABHJ",
        "turn-a::spawn::child-1",
        "01KQDYPRMTY0VRVHFCFBHKABHJ::spawn::01ARZ3NDEKTSV4RRFFQ69G5FAV",
    ),
)
def test_turn_id_accepts_safe_typescript_ulids_and_child_turn_ids(turn_id: str) -> None:
    request = ModelRoutingResolutionRequest(turnId=turn_id)

    assert request.turn_id == turn_id


@pytest.mark.parametrize(
    "turn_id",
    (
        "../turn",
        "turn/child",
        "turn::../child",
        "turn::spawn::sk-live-secret",
        "turn::spawn::Bearer raw-token",
        "turn::spawn::child=1",
    ),
)
def test_turn_id_rejects_path_or_secret_shaped_child_turn_ids(turn_id: str) -> None:
    with pytest.raises(ValidationError):
        ModelRoutingResolutionRequest(turnId=turn_id)


@pytest.mark.parametrize(
    "route_update",
    (
        {"providerLabel": "OpenAI"},
        {"providerLabel": "openai/prod"},
        {"providerLabel": "sk-provider-secret"},
        {"modelLabel": "openai/gpt-5.5"},
        {"modelLabel": "../gpt-5.5"},
        {"modelLabel": "sk-model-secret"},
        {"modelLabel": "gpt 5.5"},
        {"credentialRef": "sk-live-secret"},
        {"credentialRef": "/var/run/secrets/openai"},
        {"credentialRef": "Bearer raw-token"},
        {"credentialRefSource": "request"},
    ),
)
def test_invalid_secret_path_or_request_controlled_labels_fail_closed(
    route_update: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _route(**route_update)


def test_provider_mismatch_is_rejected_before_route_acceptance() -> None:
    request = ModelRoutingResolutionRequest(
        turnId="turn_006",
        routingMetadata=_route(providerLabel="anthropic", modelLabel="gpt-5.5"),
    )

    decision = build_turn_model_routing_decision(request, config=_enabled_config())

    assert decision.accepted is False
    assert decision.status == "rejected"
    assert decision.reason == "provider_mismatch"
    assert decision.selected_provider_label is None
    assert decision.route_activation_allowed is False


def test_unapproved_provider_model_route_or_credential_ref_rejects_fail_closed() -> None:
    route_not_allowed = ModelRoutingResolutionRequest(
        turnId="turn_007",
        routingMetadata=_route(providerLabel="fireworks", modelLabel="kimi-k2p6", credentialRef="server-fireworks-primary"),
    )
    credential_not_allowed = ModelRoutingResolutionRequest(
        turnId="turn_008",
        routingMetadata=_route(credentialRef="server-openai-secondary"),
    )

    rejected_route = build_turn_model_routing_decision(
        route_not_allowed,
        config=_enabled_config(
            allowedModelRoutes=("anthropic:claude-opus-4-6", "openai:gpt-5.5"),
        ),
    )
    rejected_credential = build_turn_model_routing_decision(
        credential_not_allowed,
        config=_enabled_config(),
    )

    assert rejected_route.accepted is False
    assert rejected_route.reason == "route_not_allowed"
    assert rejected_credential.accepted is False
    assert rejected_credential.reason == "credential_ref_not_allowed"


def test_decision_copy_cannot_enable_route_activation_or_runtime_side_effects() -> None:
    request = ModelRoutingResolutionRequest(
        turnId="turn_009",
        routingMetadata=_route(),
    )
    decision = build_turn_model_routing_decision(request, config=_enabled_config())

    assert decision.accepted is True
    for field_name in (
        "routeActivationAllowed",
        "providerCallAllowed",
        "adkRunnerInvocationAllowed",
        "adkRunnerInvoked",
        "productionWriteAllowed",
    ):
        with pytest.raises(ValidationError):
            decision.model_copy(update={field_name: True})
