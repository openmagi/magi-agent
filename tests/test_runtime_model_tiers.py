from __future__ import annotations

import pytest
from pydantic import ValidationError

from openmagi_core_agent.runtime.model_tiers import (
    ModelTierPolicy,
    ModelTierRegistry,
)


def test_registry_classifies_cheap_flash_and_kimi_style_models_without_provider_lock_in() -> None:
    registry = ModelTierRegistry.with_defaults()

    flash = registry.resolve(provider="google", model="gemini-3.5-flash")
    kimi = registry.resolve(provider="moonshot", model="kimi-k2.6")

    assert flash.tier == "cheap"
    assert kimi.tier == "cheap"
    assert "streaming" in flash.capabilities
    assert "json_schema" in flash.capabilities
    assert flash.provider == "google"
    assert kimi.provider == "moonshot"


def test_forged_request_cannot_claim_sota_capability_for_cheap_model() -> None:
    registry = ModelTierRegistry.with_defaults()

    resolved = registry.resolve(
        provider="google",
        model="gemini-3.5-flash",
        requestedCapabilities=("sota_reasoning", "long_context"),
    )

    assert resolved.tier == "cheap"
    assert "sota_reasoning" not in resolved.capabilities
    assert resolved.dropped_requested_capabilities == ("sota_reasoning", "long_context")


def test_model_tier_policy_requires_explicit_reason_for_sota_floor() -> None:
    with pytest.raises(ValidationError):
        ModelTierPolicy(
            recipeId="openmagi.research",
            phase="source_extraction",
            minimumTier="sota",
            preferredTier="sota",
        )


@pytest.mark.parametrize(
    ("provider", "model"),
    (
        ("google/secret", "gemini-3.5-flash"),
        ("google", "../gemini-3.5-flash"),
        ("google", "sk-live-provider-token"),
        ("https://provider.example", "gemini-3.5-flash"),
        ("google", "gemini-3.5-flash; rm -rf /"),
        ("google", "api_key=unsafe"),
    ),
)
def test_provider_and_model_labels_reject_paths_urls_credentials_and_shell_tokens(
    provider: str,
    model: str,
) -> None:
    registry = ModelTierRegistry.with_defaults()

    with pytest.raises(ValidationError):
        registry.resolve(provider=provider, model=model)


def test_unknown_models_are_standard_without_elevated_capabilities() -> None:
    resolved = ModelTierRegistry.with_defaults().resolve(
        provider="example",
        model="unknown-model",
        requestedCapabilities=("tool_use", "reasoning"),
    )

    assert resolved.tier == "standard"
    assert resolved.capabilities == ()
    assert resolved.dropped_requested_capabilities == ("tool_use", "reasoning")
    assert resolved.reason_codes == ("unknown_model_standard_no_elevated_capabilities",)


def test_policy_serializes_aliases_and_allows_sota_with_reason() -> None:
    policy = ModelTierPolicy(
        recipeId="openmagi.research",
        phase="high_risk_review",
        minimumTier="sota",
        preferredTier="sota",
        sotaReason="requires independent high-risk verification",
    )

    assert policy.model_dump(by_alias=True)["minimumTier"] == "sota"
    assert policy.sota_reason == "requires independent high-risk verification"
