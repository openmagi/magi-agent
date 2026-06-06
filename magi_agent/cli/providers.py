"""Provider/key resolution for the local ``magi`` CLI.

The installed CLI ships a model-free stub runner (:mod:`magi_agent.cli.local_runner`)
so ``magi`` launches without any configuration. This module is the bridge to a
*real* runner: it discovers which model provider the user has configured and the
API key to use, from either a config file (``~/.magi/config.toml``, overridable
via ``MAGI_CONFIG``) or environment variables.

Four providers are supported, all routed through ADK's ``LiteLlm``:
``openai``, ``anthropic``, ``gemini`` and ``fireworks``.

Resolution rules
----------------
1. If a provider is explicitly named (``[model].provider`` in the config file or
   ``MAGI_PROVIDER`` in the env), use it. Its key comes from ``[model].api_key``,
   then ``[providers.<name>].api_key``, then the provider's env var(s).
2. Otherwise auto-detect: walk :data:`SUPPORTED_PROVIDERS` in order and use the
   first provider that has a key available (config or env).
3. If nothing is configured, return ``None`` so the caller keeps the stub runner.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from magi_agent.config.env import LOCAL_DEV_MODEL_SENTINEL

# Auto-detect order. Anthropic first (magi's primary deployment posture), then
# the rest. Also the set of accepted ``provider`` values.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "gemini", "fireworks")

# Env var(s) carrying the API key for each provider, in lookup order.
_PROVIDER_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "fireworks": ("FIREWORKS_API_KEY",),
}

# Default model id per provider, overridable via config ``[model].model`` or the
# ``MAGI_MODEL`` env var. Model ids drift over time; treat these as a best-effort
# starting point and override when a provider retires a name.
_DEFAULT_MODEL: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-5.5",
    "gemini": "gemini-3.5-flash",
    "fireworks": "accounts/fireworks/models/kimi-k2-instruct",
}

# litellm provider prefix per provider (``<prefix>/<model>``).
_LITELLM_PREFIX: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "gemini",
    "fireworks": "fireworks_ai",
}


class UnknownProviderError(ValueError):
    """Raised when an explicitly-configured provider is not supported."""


@dataclass(frozen=True)
class ProviderConfig:
    """A fully-resolved provider selection ready to build a model from."""

    provider: str
    model: str
    api_key: str

    @property
    def litellm_model(self) -> str:
        return f"{_LITELLM_PREFIX[self.provider]}/{self.model}"


def _config_path() -> Path:
    override = os.environ.get("MAGI_CONFIG")
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / ".magi" / "config.toml"


def _load_config_file() -> dict[str, object]:
    path = _config_path()
    try:
        with open(path, "rb") as handle:
            loaded = tomllib.load(handle)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return {}
    except (OSError, tomllib.TOMLDecodeError):
        # A malformed or unreadable config must not crash the CLI; fall back to
        # env-only resolution (which may itself yield None -> stub runner).
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _clean(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _clean_model(value: object) -> str | None:
    cleaned = _clean(value)
    if cleaned is None or cleaned == LOCAL_DEV_MODEL_SENTINEL:
        return None
    return cleaned


def _section(config: Mapping[str, object], name: str) -> dict[str, object]:
    section = config.get(name)
    return section if isinstance(section, dict) else {}


def resolve_provider_config(
    *,
    model_override: str | None = None,
    env: Mapping[str, str] | None = None,
    config: Mapping[str, object] | None = None,
) -> ProviderConfig | None:
    """Resolve the active provider config, or ``None`` if none is configured.

    ``env`` and ``config`` are injectable for testing; they default to the real
    process environment and ``~/.magi/config.toml``.
    """

    env = os.environ if env is None else env
    config = _load_config_file() if config is None else config

    model_section = _section(config, "model")
    providers_section = _section(config, "providers")

    def key_for(provider: str) -> str | None:
        provider_block = providers_section.get(provider)
        if isinstance(provider_block, dict):
            configured = _clean(provider_block.get("api_key"))
            if configured:
                return configured
        for env_name in _PROVIDER_ENV_KEYS[provider]:
            from_env = _clean(env.get(env_name))
            if from_env:
                return from_env
        return None

    def model_for(provider: str) -> str:
        return (
            _clean_model(model_override)
            or _clean_model(env.get("MAGI_MODEL"))
            or _clean_model(model_section.get("model"))
            or _DEFAULT_MODEL[provider]
        )

    explicit = _clean(env.get("MAGI_PROVIDER")) or _clean(model_section.get("provider"))
    if explicit:
        provider = explicit.lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise UnknownProviderError(
                f"Unsupported provider {provider!r}. "
                f"Supported: {', '.join(SUPPORTED_PROVIDERS)}."
            )
        api_key = _clean(model_section.get("api_key")) or key_for(provider)
        if not api_key:
            # Provider named but no key found -> fall back to the stub runner
            # rather than crashing, so ``magi`` still launches.
            return None
        return ProviderConfig(provider=provider, model=model_for(provider), api_key=api_key)

    for provider in SUPPORTED_PROVIDERS:
        api_key = key_for(provider)
        if api_key:
            return ProviderConfig(provider=provider, model=model_for(provider), api_key=api_key)
    return None


__all__ = [
    "SUPPORTED_PROVIDERS",
    "ProviderConfig",
    "UnknownProviderError",
    "resolve_provider_config",
]
