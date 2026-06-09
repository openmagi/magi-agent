import pytest
from magi_agent.browser.autonomous.provider_bridge import (
    BridgeError,
    chat_model_kwargs_for,
)


class _Cfg:
    def __init__(self, provider, model, api_key):
        self.provider = provider
        self.model = model
        self.api_key = api_key


def test_maps_anthropic():
    spec = chat_model_kwargs_for(_Cfg("anthropic", "claude-opus-4-7", "k"))
    assert spec.provider == "anthropic"
    assert spec.kwargs == {"model": "claude-opus-4-7", "api_key": "k"}


def test_maps_openai():
    spec = chat_model_kwargs_for(_Cfg("openai", "gpt-4o", "k"))
    assert spec.provider == "openai"


def test_maps_gemini():
    spec = chat_model_kwargs_for(_Cfg("gemini", "gemini-3.5-flash", "k"))
    assert spec.provider == "gemini"
    assert spec.chat_class_name == "ChatGoogle"


def test_fireworks_unsupported_raises():
    # magi supports "fireworks", but browser_use.llm has no dedicated Chat
    # class for it, so the bridge must reject it rather than silently pass.
    with pytest.raises(BridgeError):
        chat_model_kwargs_for(_Cfg("fireworks", "accounts/fireworks/models/x", "k"))


def test_unknown_provider_raises():
    with pytest.raises(BridgeError):
        chat_model_kwargs_for(_Cfg("mystery", "m", "k"))


def test_none_config_raises():
    with pytest.raises(BridgeError):
        chat_model_kwargs_for(None)
