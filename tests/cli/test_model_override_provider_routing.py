"""TDD tests for `resolve_provider_config(model_override=...)` honoring the
provider implied by a `<provider>/<model>` slug.

Repro: with config.toml carrying `provider="openai"` + `model="gpt-5.5"` and
keys for anthropic/openai/gemini/fireworks all registered, a dashboard turn
that selects "Claude Sonnet 4.6" sends `model="anthropic/claude-sonnet-4-6"`
to the backend. The previous resolver kept the config's `openai` provider and
built a `ProviderConfig(provider="openai", model="anthropic/claude-sonnet-4-6",
api_key=<openai key>)`. LiteLLM then prefixed that to
`openai/anthropic/claude-sonnet-4-6`, called OpenAI's API, and failed with:

    litellm.UnsupportedParamsError: openai does not support parameters:
    ['reasoning_effort'], for model=anthropic/claude-sonnet-4-6 (LiteLLM
    Retried: 4 times)

— the user saw "no final answer text arrived" for every non-OpenAI model.
"""
from __future__ import annotations

from magi_agent.cli.providers import resolve_provider_config


_CFG = {
    "model": {"provider": "openai", "model": "gpt-5.5"},
    "providers": {
        "openai": {"api_key": "openai-key"},
        "anthropic": {"api_key": "anthropic-key"},
        "gemini": {"api_key": "gemini-key"},
        "fireworks": {"api_key": "fireworks-key"},
    },
}


def _resolve(model: str):
    return resolve_provider_config(model_override=model, env={}, config=_CFG)


def test_anthropic_slug_overrides_config_provider_and_key() -> None:
    cfg = _resolve("anthropic/claude-sonnet-4-6")
    assert cfg is not None
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.api_key == "anthropic-key"


def test_openai_slug_keeps_openai_provider_and_strips_prefix() -> None:
    cfg = _resolve("openai/gpt-5.5-pro")
    assert cfg is not None
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-5.5-pro"
    assert cfg.api_key == "openai-key"


def test_google_alias_routes_to_gemini_provider() -> None:
    # The chat-core slug map writes `google/...` for Gemini models; the
    # resolver must accept `google` as an alias for the gemini provider key.
    cfg = _resolve("google/gemini-3.1-pro-preview")
    assert cfg is not None
    assert cfg.provider == "gemini"
    assert cfg.model == "gemini-3.1-pro-preview"
    assert cfg.api_key == "gemini-key"


def test_fireworks_slug_overrides_config_provider() -> None:
    cfg = _resolve("fireworks/kimi-k2p6")
    assert cfg is not None
    assert cfg.provider == "fireworks"
    assert cfg.model == "kimi-k2p6"
    assert cfg.api_key == "fireworks-key"


def test_unprefixed_model_keeps_config_provider() -> None:
    # No `<provider>/` prefix → respect the config's `provider="openai"` as
    # before (no behavior change for the historical case).
    cfg = _resolve("gpt-5.5-pro")
    assert cfg is not None
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-5.5-pro"
    assert cfg.api_key == "openai-key"


def test_fireworks_accounts_path_is_not_treated_as_provider_slug() -> None:
    # The Fireworks raw model id `accounts/fireworks/models/...` is NOT a
    # provider/<model> slug; "accounts" must not be parsed as a provider name.
    cfg = _resolve("accounts/fireworks/models/kimi-k2-instruct")
    assert cfg is not None
    assert cfg.provider == "openai"  # config default — unchanged
    assert cfg.model == "accounts/fireworks/models/kimi-k2-instruct"


def test_slug_provider_without_key_falls_back_to_none() -> None:
    # If the slug names a provider that has NO configured key, return None so
    # the runtime falls back to the stub runner rather than calling the wrong
    # provider's API with a wrong key.
    cfg = resolve_provider_config(
        model_override="anthropic/claude-sonnet-4-6",
        env={},
        config={"model": {"provider": "openai", "model": "gpt-5.5"},
                "providers": {"openai": {"api_key": "openai-key"}}},
    )
    assert cfg is None
