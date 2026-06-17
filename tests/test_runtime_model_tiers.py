from __future__ import annotations

import os
import tempfile

import pytest
from pydantic import ValidationError

from magi_agent.runtime.model_tiers import (
    ModelTierPolicy,
    ModelTierRegistry,
    available_child_model_routes,
    resolve_child_route,
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


# ---------------------------------------------------------------------------
# C2/C3 key-aware model-route seam tests
# All tests use an isolated MAGI_CONFIG (tmpfile) so ~/.magi/config.toml
# does not bleed in.
# ---------------------------------------------------------------------------

_FLAG = "MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED"


def _isolated_env(**extra: str) -> dict[str, str]:
    """Return an env dict with MAGI_CONFIG pointing to an empty temp file."""
    tmp = tempfile.mktemp(suffix=".toml")  # noqa: S306 — test-only, no security concern
    open(tmp, "w").close()  # create empty file
    return {"MAGI_CONFIG": tmp, **extra}


# Test 1 — gate OFF parity: without flag, anthropic/openai sota routes still present
def test_gate_off_parity_advertised_routes_include_anthropic_and_openai() -> None:
    env = _isolated_env()  # no flag set at all
    routes = available_child_model_routes(env)
    route_names = [r.split(" ")[0] for r in routes]
    assert any(r.startswith("anthropic:") for r in route_names), (
        f"Expected anthropic route without flag; got: {routes}"
    )
    assert any(r.startswith("openai:") for r in route_names), (
        f"Expected openai route without flag; got: {routes}"
    )


def test_gate_off_parity_resolve_child_route_accepts_anthropic_even_with_fireworks_key() -> None:
    env = _isolated_env(FIREWORKS_API_KEY="fk-test")  # only fireworks key, gate OFF
    result = resolve_child_route("anthropic", "claude-sonnet-4-6", env)
    assert result is not None, (
        "Gate OFF: anthropic route must still be accepted even with only a fireworks key"
    )


# Test 2 — gate ON, fireworks-only: only fireworks routes advertised
def test_gate_on_fireworks_only_advertised_routes_contain_fireworks_not_anthropic_openai() -> None:
    env = _isolated_env(**{_FLAG: "1", "FIREWORKS_API_KEY": "fk-test"})
    routes = available_child_model_routes(env)
    route_names = [r.split(" ")[0] for r in routes]
    assert any(r.startswith("fireworks:") for r in route_names), (
        f"Expected fireworks route; got: {routes}"
    )
    assert not any(r.startswith("anthropic:") for r in route_names), (
        f"Expected NO anthropic route; got: {routes}"
    )
    assert not any(r.startswith("openai:") for r in route_names), (
        f"Expected NO openai route; got: {routes}"
    )


def test_gate_on_fireworks_only_anthropic_route_rejected() -> None:
    env = _isolated_env(**{_FLAG: "1", "FIREWORKS_API_KEY": "fk-test"})
    result = resolve_child_route("anthropic", "claude-sonnet-4-6", env)
    assert result is None, (
        f"Gate ON fireworks-only: anthropic route must be rejected; got {result}"
    )


def test_gate_on_fireworks_only_fireworks_route_accepted() -> None:
    env = _isolated_env(**{_FLAG: "1", "FIREWORKS_API_KEY": "fk-test"})
    result = resolve_child_route(
        "fireworks", "accounts/fireworks/models/kimi-k2-instruct", env
    )
    assert result is not None, (
        "Gate ON fireworks-only: fireworks route must be accepted"
    )


# Test 3 — gate ON, no keys: fail-open, anthropic/openai still advertised
def test_gate_on_no_keys_fails_open_to_legacy_routes() -> None:
    env = _isolated_env(**{_FLAG: "1"})  # gate ON but no API keys
    routes = available_child_model_routes(env)
    route_names = [r.split(" ")[0] for r in routes]
    assert any(r.startswith("anthropic:") for r in route_names), (
        f"Fail-open: no keys + gate ON should still show anthropic; got: {routes}"
    )
    assert any(r.startswith("openai:") for r in route_names), (
        f"Fail-open: no keys + gate ON should still show openai; got: {routes}"
    )


# Test 4 — gate ON, gemini key: gemini/google routes advertised, not anthropic/openai
def test_gate_on_gemini_key_advertises_gemini_not_anthropic_openai() -> None:
    env = _isolated_env(**{_FLAG: "1", "GEMINI_API_KEY": "gk-test"})
    routes = available_child_model_routes(env)
    route_names = [r.split(" ")[0] for r in routes]
    # gemini key covers both "gemini" and "google" registry labels
    has_gemini_or_google = any(
        r.startswith("gemini:") or r.startswith("google:") for r in route_names
    )
    assert has_gemini_or_google, (
        f"Expected gemini/google route with GEMINI_API_KEY; got: {routes}"
    )
    assert not any(r.startswith("anthropic:") for r in route_names), (
        f"Expected NO anthropic route; got: {routes}"
    )
    assert not any(r.startswith("openai:") for r in route_names), (
        f"Expected NO openai route; got: {routes}"
    )


# Test 5 — gate ON, custom fireworks model via MAGI_MODEL: that route is advertised+accepted
def test_gate_on_custom_fireworks_model_is_routable() -> None:
    custom_model = "accounts/fireworks/models/custom-model-v1"
    env = _isolated_env(
        **{_FLAG: "1", "FIREWORKS_API_KEY": "fk-test", "MAGI_MODEL": custom_model}
    )
    routes = available_child_model_routes(env)
    route_names = [r.split(" ")[0] for r in routes]
    custom_route = f"fireworks:{custom_model}"
    assert custom_route in route_names, (
        f"Custom fireworks model {custom_route!r} must be advertised; got: {routes}"
    )
    result = resolve_child_route("fireworks", custom_model, env)
    assert result is not None, (
        f"Custom fireworks model must be accepted by resolve_child_route; got None"
    )


# Test 6 — advertised == validated invariant
def test_advertised_routes_invariant_gate_on_fireworks() -> None:
    """Every advertised route must resolve; nothing extra resolves from the registry."""
    env = _isolated_env(**{_FLAG: "1", "FIREWORKS_API_KEY": "fk-test"})
    routes = available_child_model_routes(env)
    for route_entry in routes:
        route = route_entry.split(" ")[0]
        provider, _, model = route.partition(":")
        result = resolve_child_route(provider, model, env)
        assert result is not None, (
            f"Advertised route {route!r} must be accepted by resolve_child_route"
        )


def test_advertised_routes_invariant_gate_off() -> None:
    """Gate OFF: every advertised route must resolve."""
    env = _isolated_env()
    routes = available_child_model_routes(env)
    for route_entry in routes:
        route = route_entry.split(" ")[0]
        provider, _, model = route.partition(":")
        result = resolve_child_route(provider, model, env)
        assert result is not None, (
            f"Gate OFF: advertised route {route!r} must be accepted by resolve_child_route"
        )


# Test 7 — operator allowlist still appends under gate ON
def test_gate_on_operator_allowlist_appended_regardless_of_keys() -> None:
    env = _isolated_env(
        **{
            _FLAG: "1",
            "FIREWORKS_API_KEY": "fk-test",
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ALLOWED_MODEL_ROUTES": (
                "someoperator:custom-allowed-model"
            ),
        }
    )
    routes = available_child_model_routes(env)
    route_names = [r.split(" ")[0] for r in routes]
    assert "someoperator:custom-allowed-model" in route_names, (
        f"Operator-allowlist route must appear regardless of keys; got: {routes}"
    )
    result = resolve_child_route("someoperator", "custom-allowed-model", env)
    assert result is not None, (
        "Operator-allowlist route must be accepted by resolve_child_route"
    )
