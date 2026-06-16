from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


class BridgeError(RuntimeError):
    """Raised when a provider config cannot be bridged to browser-use."""


BROWSER_USE_VISION_ENV = "MAGI_BROWSER_USE_VISION"

# magi provider name -> browser_use.llm class name (confirmed in the API spike).
#
# magi's SUPPORTED_PROVIDERS = ("anthropic", "openai", "gemini", "fireworks",
# "openrouter"). browser_use 0.11.x exposes ChatAnthropic / ChatOpenAI /
# ChatGoogle (Gemini is served by ChatGoogle). It has no dedicated Chat class
# for Fireworks or OpenRouter, but both expose an OpenAI-compatible REST surface,
# so they are bridged through ChatOpenAI pointed at a provider-specific base_url
# (see _OPENAI_COMPATIBLE_BASE_URLS). This is NOT a vision check: a non-vision
# model can still drive the browser headlessly (DOM-only); see resolve_use_vision.
_PROVIDER_TO_CHAT_CLASS = {
    "anthropic": "ChatAnthropic",
    "openai": "ChatOpenAI",
    "gemini": "ChatGoogle",
    "fireworks": "ChatOpenAI",
    "openrouter": "ChatOpenAI",
}

# OpenAI-compatible providers reached via ChatOpenAI need an explicit base_url
# (native "openai" uses the library default endpoint, so it is absent here).
_OPENAI_COMPATIBLE_BASE_URLS = {
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

# Providers whose default models are multimodal -> safe to send screenshots.
# Generic OpenAI-compatible providers front arbitrary models we cannot assume
# are vision-capable, so they default to DOM-only (use_vision=False).
_VISION_DEFAULT_PROVIDERS = frozenset({"anthropic", "openai", "gemini"})

_TRUE_VALUES = frozenset({"1", "on", "true", "yes"})
_FALSE_VALUES = frozenset({"0", "off", "false", "no"})


def _env_bool(value: object) -> bool | None:
    """Tri-state parse: True/False for recognized values, None when unset/blank."""
    text = str(value or "").strip().casefold()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return None


def resolve_use_vision(
    provider_config: object | None,
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Whether the browser Agent should run with vision (screenshots) on.

    The ``MAGI_BROWSER_USE_VISION`` env override wins. Otherwise vision is ON
    only for providers whose default models are multimodal; generic
    OpenAI-compatible providers (and an absent config) default to DOM-only so a
    text-only model never receives images it cannot read.
    """
    resolved: Mapping[str, str] = os.environ if env is None else env
    override = _env_bool(resolved.get(BROWSER_USE_VISION_ENV))
    if override is not None:
        return override
    provider = getattr(provider_config, "provider", None)
    return str(provider) in _VISION_DEFAULT_PROVIDERS


@dataclass(frozen=True)
class ChatModelSpec:
    provider: str
    chat_class_name: str
    kwargs: dict[str, object]


def chat_model_kwargs_for(provider_config: object | None) -> ChatModelSpec:
    """Pure mapping: ProviderConfig -> the browser-use chat class + kwargs.

    Separated from instantiation so the mapping is testable without the
    optional ``browser`` extra installed.
    """
    if provider_config is None:
        raise BridgeError("no provider configured; set a provider key for the browser tool")
    provider = getattr(provider_config, "provider", None)
    chat_class = _PROVIDER_TO_CHAT_CLASS.get(str(provider))
    if chat_class is None:
        raise BridgeError(f"provider {provider!r} not supported by the browser tool")
    # Read with defaults so a malformed/partial config raises BridgeError (the
    # taxonomy the handler catches) rather than a bare AttributeError.
    model = getattr(provider_config, "model", None)
    api_key = getattr(provider_config, "api_key", None)
    if not model or not api_key:
        raise BridgeError(
            f"provider {provider!r} config is missing a model and/or api_key "
            "for the browser tool"
        )
    kwargs: dict[str, object] = {"model": model, "api_key": api_key}
    base_url = _OPENAI_COMPATIBLE_BASE_URLS.get(str(provider))
    if base_url:
        kwargs["base_url"] = base_url
    return ChatModelSpec(
        provider=str(provider),
        chat_class_name=chat_class,
        kwargs=kwargs,
    )


def build_chat_model(provider_config: object | None) -> object:
    """Instantiate the browser-use chat model. Lazy-imports the optional extra."""
    spec = chat_model_kwargs_for(provider_config)
    from browser_use import llm as _llm  # noqa: PLC0415

    chat_cls = getattr(_llm, spec.chat_class_name)
    return chat_cls(**spec.kwargs)
