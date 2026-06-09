from __future__ import annotations

from dataclasses import dataclass


class BridgeError(RuntimeError):
    """Raised when a provider config cannot be bridged to browser-use."""


# magi provider name -> browser_use.llm class name (confirmed in the API spike).
#
# magi's SUPPORTED_PROVIDERS = ("anthropic", "openai", "gemini", "fireworks").
# browser_use 0.11.x exposes ChatAnthropic / ChatOpenAI / ChatGoogle (Gemini is
# served by ChatGoogle). It has NO dedicated Chat class for Fireworks, so
# "fireworks" is intentionally omitted: a configured-but-unmappable provider
# falls through to the generic "not supported by the browser tool" error.
_PROVIDER_TO_CHAT_CLASS = {
    "anthropic": "ChatAnthropic",
    "openai": "ChatOpenAI",
    "gemini": "ChatGoogle",
}


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
    return ChatModelSpec(
        provider=str(provider),
        chat_class_name=chat_class,
        kwargs={
            "model": getattr(provider_config, "model"),
            "api_key": getattr(provider_config, "api_key"),
        },
    )


def build_chat_model(provider_config: object | None) -> object:
    """Instantiate the browser-use chat model. Lazy-imports the optional extra."""
    spec = chat_model_kwargs_for(provider_config)
    from browser_use import llm as _llm  # noqa: PLC0415

    chat_cls = getattr(_llm, spec.chat_class_name)
    return chat_cls(**spec.kwargs)
