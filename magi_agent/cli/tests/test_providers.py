from __future__ import annotations

import pytest

from magi_agent.cli.providers import (
    SUPPORTED_PROVIDERS,
    ProviderConfig,
    UnknownProviderError,
    resolve_provider_config,
)


def test_no_config_returns_none() -> None:
    assert resolve_provider_config(env={}, config={}) is None


def test_autodetect_from_env_anthropic() -> None:
    cfg = resolve_provider_config(env={"ANTHROPIC_API_KEY": "sk-a"}, config={})
    assert isinstance(cfg, ProviderConfig)
    assert cfg.provider == "anthropic"
    assert cfg.api_key == "sk-a"
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.litellm_model == "anthropic/claude-sonnet-4-6"


def test_autodetect_follows_supported_order() -> None:
    # anthropic precedes openai in SUPPORTED_PROVIDERS, so it wins when both keys
    # are present.
    assert SUPPORTED_PROVIDERS[0] == "anthropic"
    cfg = resolve_provider_config(
        env={"OPENAI_API_KEY": "o", "ANTHROPIC_API_KEY": "a"}, config={}
    )
    assert cfg is not None
    assert cfg.provider == "anthropic"


def test_gemini_accepts_google_api_key_alias() -> None:
    cfg = resolve_provider_config(env={"GOOGLE_API_KEY": "g"}, config={})
    assert cfg is not None
    assert cfg.provider == "gemini"
    assert cfg.api_key == "g"
    assert cfg.litellm_model == "gemini/gemini-3.5-flash"


def test_fireworks_uses_fireworks_ai_litellm_prefix() -> None:
    cfg = resolve_provider_config(env={"FIREWORKS_API_KEY": "f"}, config={})
    assert cfg is not None
    assert cfg.provider == "fireworks"
    assert cfg.model == "accounts/fireworks/models/kimi-k2-instruct"
    assert cfg.litellm_model == (
        "fireworks_ai/accounts/fireworks/models/kimi-k2-instruct"
    )
    assert cfg.litellm_model.startswith("fireworks_ai/")


def test_explicit_provider_in_config_overrides_autodetect() -> None:
    cfg = resolve_provider_config(
        env={"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o"},
        config={"model": {"provider": "openai"}},
    )
    assert cfg is not None
    assert cfg.provider == "openai"
    assert cfg.api_key == "o"


def test_explicit_provider_via_env() -> None:
    cfg = resolve_provider_config(
        env={"MAGI_PROVIDER": "gemini", "GEMINI_API_KEY": "g"}, config={}
    )
    assert cfg is not None
    assert cfg.provider == "gemini"


def test_explicit_provider_without_key_returns_none() -> None:
    # Named provider but no key anywhere -> stub fallback (None), not a crash.
    assert resolve_provider_config(env={"MAGI_PROVIDER": "openai"}, config={}) is None


def test_unknown_provider_raises() -> None:
    with pytest.raises(UnknownProviderError):
        resolve_provider_config(
            env={"MAGI_PROVIDER": "cohere", "OPENAI_API_KEY": "o"}, config={}
        )


def test_config_providers_section_supplies_key() -> None:
    cfg = resolve_provider_config(
        env={}, config={"providers": {"fireworks": {"api_key": "fw"}}}
    )
    assert cfg is not None
    assert cfg.provider == "fireworks"
    assert cfg.api_key == "fw"


def test_model_override_wins_over_default() -> None:
    cfg = resolve_provider_config(
        model_override="custom-model", env={"ANTHROPIC_API_KEY": "a"}, config={}
    )
    assert cfg is not None
    assert cfg.model == "custom-model"


def test_env_magi_model_overrides_default() -> None:
    cfg = resolve_provider_config(
        env={"ANTHROPIC_API_KEY": "a", "MAGI_MODEL": "m2"}, config={}
    )
    assert cfg is not None
    assert cfg.model == "m2"


def test_config_model_overrides_default() -> None:
    cfg = resolve_provider_config(
        env={"ANTHROPIC_API_KEY": "a"}, config={"model": {"model": "m3"}}
    )
    assert cfg is not None
    assert cfg.model == "m3"


def test_local_dev_model_in_config_falls_back_to_provider_default() -> None:
    cfg = resolve_provider_config(
        env={"ANTHROPIC_API_KEY": "a"},
        config={"model": {"model": "local-dev"}},
    )
    assert cfg is not None
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.litellm_model == "anthropic/claude-sonnet-4-6"


def test_local_dev_model_in_env_falls_back_to_provider_default() -> None:
    cfg = resolve_provider_config(
        env={"ANTHROPIC_API_KEY": "a", "MAGI_MODEL": "local-dev"},
        config={},
    )
    assert cfg is not None
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.litellm_model == "anthropic/claude-sonnet-4-6"


def test_loads_config_file_from_magi_config_env(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[model]\nprovider = "openai"\napi_key = "zz"\n')
    monkeypatch.setenv("MAGI_CONFIG", str(path))
    # config=None -> the resolver loads the file pointed at by MAGI_CONFIG.
    cfg = resolve_provider_config(env={})
    assert cfg is not None
    assert cfg.provider == "openai"
    assert cfg.api_key == "zz"


def test_missing_config_file_is_tolerated(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "does-not-exist.toml"))
    assert resolve_provider_config(env={}) is None
