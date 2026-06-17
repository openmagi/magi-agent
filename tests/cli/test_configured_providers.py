"""TDD tests for configured_providers() — Task C1.

All tests are hermetic (no real env, no real config file).
"""
from __future__ import annotations

import pytest

from magi_agent.cli.providers import configured_providers, SUPPORTED_PROVIDERS


# ---------------------------------------------------------------------------
# Test 1: env-only — single provider
# ---------------------------------------------------------------------------


def test_env_only_fireworks() -> None:
    """FIREWORKS_API_KEY set → ['fireworks']."""
    result = configured_providers(
        env={"FIREWORKS_API_KEY": "fw-key-abc"},
        config={},
    )
    assert result == ["fireworks"]


# ---------------------------------------------------------------------------
# Test 2: config-only — single provider
# ---------------------------------------------------------------------------


def test_config_only_anthropic() -> None:
    """[providers.anthropic].api_key in config → ['anthropic']."""
    result = configured_providers(
        env={},
        config={"providers": {"anthropic": {"api_key": "sk-ant-xyz"}}},
    )
    assert result == ["anthropic"]


# ---------------------------------------------------------------------------
# Test 3: multiple providers — order follows SUPPORTED_PROVIDERS
# ---------------------------------------------------------------------------


def test_multiple_providers_ordered() -> None:
    """anthropic+openai env + fireworks config → ordered ['anthropic', 'openai', 'fireworks']."""
    result = configured_providers(
        env={
            "ANTHROPIC_API_KEY": "sk-ant-abc",
            "OPENAI_API_KEY": "sk-openai-abc",
        },
        config={"providers": {"fireworks": {"api_key": "fw-key-xyz"}}},
    )
    assert result == ["anthropic", "openai", "fireworks"]


def test_result_order_follows_supported_providers_not_input_order() -> None:
    """Even when config lists fireworks before anthropic, SUPPORTED_PROVIDERS order wins."""
    result = configured_providers(
        env={"ANTHROPIC_API_KEY": "sk-ant-abc"},
        config={
            "providers": {
                "fireworks": {"api_key": "fw-key-xyz"},
                "openai": {"api_key": "sk-openai-abc"},
            }
        },
    )
    # anthropic < openai < fireworks in SUPPORTED_PROVIDERS
    assert result == ["anthropic", "openai", "fireworks"]


# ---------------------------------------------------------------------------
# Test 4: gemini dual-key support
# ---------------------------------------------------------------------------


def test_gemini_via_gemini_api_key() -> None:
    """GEMINI_API_KEY alone → ['gemini']."""
    result = configured_providers(
        env={"GEMINI_API_KEY": "gemini-key-123"},
        config={},
    )
    assert result == ["gemini"]


def test_gemini_via_google_api_key() -> None:
    """GOOGLE_API_KEY alone → ['gemini'] (fallback env var)."""
    result = configured_providers(
        env={"GOOGLE_API_KEY": "google-key-456"},
        config={},
    )
    assert result == ["gemini"]


def test_gemini_not_duplicated_when_both_vars_set() -> None:
    """Both GEMINI_API_KEY and GOOGLE_API_KEY set → gemini appears exactly once."""
    result = configured_providers(
        env={
            "GEMINI_API_KEY": "gemini-key-123",
            "GOOGLE_API_KEY": "google-key-456",
        },
        config={},
    )
    assert result.count("gemini") == 1
    assert result == ["gemini"]


# ---------------------------------------------------------------------------
# Test 5: none configured → empty list
# ---------------------------------------------------------------------------


def test_none_configured_returns_empty_list() -> None:
    """No keys anywhere → []."""
    result = configured_providers(env={}, config={})
    assert result == []


# ---------------------------------------------------------------------------
# Test 6: whitespace-only key → treated as not configured
# ---------------------------------------------------------------------------


def test_whitespace_only_env_key_not_configured() -> None:
    """Whitespace-only ANTHROPIC_API_KEY → not configured."""
    result = configured_providers(
        env={"ANTHROPIC_API_KEY": "   "},
        config={},
    )
    assert result == []


def test_whitespace_only_config_key_not_configured() -> None:
    """Whitespace-only api_key in config → not configured."""
    result = configured_providers(
        env={},
        config={"providers": {"anthropic": {"api_key": "  \t  "}}},
    )
    assert result == []


def test_empty_string_env_key_not_configured() -> None:
    """Empty string ANTHROPIC_API_KEY → not configured."""
    result = configured_providers(
        env={"ANTHROPIC_API_KEY": ""},
        config={},
    )
    assert result == []


# ---------------------------------------------------------------------------
# Test 7: config key precedence — agrees with resolve_provider_config
# ---------------------------------------------------------------------------


def test_config_key_wins_over_env() -> None:
    """Config api_key takes precedence over env var (same as resolve_provider_config)."""
    from magi_agent.cli.providers import resolve_provider_config

    env = {"ANTHROPIC_API_KEY": "env-key"}
    config = {"providers": {"anthropic": {"api_key": "config-key"}}}

    # configured_providers should see anthropic as configured
    result = configured_providers(env=env, config=config)
    assert "anthropic" in result

    # resolve_provider_config should pick config-key (config wins)
    resolved = resolve_provider_config(env=env, config=config)
    assert resolved is not None
    assert resolved.api_key == "config-key"


def test_configured_providers_and_resolve_agree_on_fireworks_env() -> None:
    """Both functions agree that fireworks with env key is configured."""
    from magi_agent.cli.providers import resolve_provider_config

    env = {"FIREWORKS_API_KEY": "fw-key-abc"}
    config: dict = {}

    providers = configured_providers(env=env, config=config)
    assert "fireworks" in providers

    resolved = resolve_provider_config(env=env, config=config)
    assert resolved is not None
    assert resolved.provider == "fireworks"


def test_configured_providers_result_is_subset_of_supported_providers() -> None:
    """Return value is always a subset of SUPPORTED_PROVIDERS."""
    env = {
        "ANTHROPIC_API_KEY": "sk-ant",
        "OPENAI_API_KEY": "sk-oai",
        "FIREWORKS_API_KEY": "fw-key",
        "OPENROUTER_API_KEY": "or-key",
        "GEMINI_API_KEY": "gm-key",
    }
    result = configured_providers(env=env, config={})
    assert set(result).issubset(set(SUPPORTED_PROVIDERS))
    assert len(result) == len(set(result)), "No duplicates"


# ---------------------------------------------------------------------------
# Test 8: malformed/missing config does not raise
# ---------------------------------------------------------------------------


def test_malformed_providers_section_does_not_raise() -> None:
    """Malformed providers section (not a dict) → no raise, treats as no config."""
    result = configured_providers(
        env={"FIREWORKS_API_KEY": "fw-key"},
        config={"providers": "not-a-dict"},
    )
    # providers section malformed → falls back to env
    assert result == ["fireworks"]


def test_missing_providers_section_does_not_raise() -> None:
    """No providers section at all → no raise, env still works."""
    result = configured_providers(
        env={"OPENAI_API_KEY": "sk-oai"},
        config={},
    )
    assert result == ["openai"]


def test_empty_config_does_not_raise() -> None:
    """Completely empty config → no raise."""
    result = configured_providers(env={}, config={})
    assert result == []


# ---------------------------------------------------------------------------
# Test 9: all five providers configured
# ---------------------------------------------------------------------------


def test_all_five_providers_in_supported_order() -> None:
    """All five providers configured → returned in SUPPORTED_PROVIDERS order."""
    env = {
        "ANTHROPIC_API_KEY": "sk-ant",
        "OPENAI_API_KEY": "sk-oai",
        "GEMINI_API_KEY": "gm-key",
        "FIREWORKS_API_KEY": "fw-key",
        "OPENROUTER_API_KEY": "or-key",
    }
    result = configured_providers(env=env, config={})
    assert result == list(SUPPORTED_PROVIDERS)


# ---------------------------------------------------------------------------
# Test 10: openrouter provider
# ---------------------------------------------------------------------------


def test_openrouter_via_env() -> None:
    """OPENROUTER_API_KEY set → ['openrouter']."""
    result = configured_providers(
        env={"OPENROUTER_API_KEY": "or-key-xyz"},
        config={},
    )
    assert result == ["openrouter"]
