import pytest
from magi_agent.browser.autonomous.provider_bridge import (
    BROWSER_USE_VISION_ENV,
    BridgeError,
    chat_model_kwargs_for,
    resolve_use_vision,
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


def test_fireworks_maps_to_chatopenai_with_base_url():
    # Fireworks is OpenAI-compatible: browser_use has no ChatFireworks class, so
    # it is bridged through ChatOpenAI pointed at the Fireworks base_url.
    spec = chat_model_kwargs_for(_Cfg("fireworks", "accounts/fireworks/models/x", "k"))
    assert spec.provider == "fireworks"
    assert spec.chat_class_name == "ChatOpenAI"
    assert spec.kwargs == {
        "model": "accounts/fireworks/models/x",
        "api_key": "k",
        "base_url": "https://api.fireworks.ai/inference/v1",
    }


def test_openrouter_maps_to_chatopenai_with_base_url():
    spec = chat_model_kwargs_for(_Cfg("openrouter", "openai/gpt-4o", "k"))
    assert spec.provider == "openrouter"
    assert spec.chat_class_name == "ChatOpenAI"
    assert spec.kwargs == {
        "model": "openai/gpt-4o",
        "api_key": "k",
        "base_url": "https://openrouter.ai/api/v1",
    }


def test_native_openai_has_no_base_url_override():
    # Native OpenAI uses the library default endpoint (no base_url injected).
    spec = chat_model_kwargs_for(_Cfg("openai", "gpt-4o", "k"))
    assert "base_url" not in spec.kwargs


def test_unknown_provider_raises():
    with pytest.raises(BridgeError):
        chat_model_kwargs_for(_Cfg("mystery", "m", "k"))


def test_none_config_raises():
    with pytest.raises(BridgeError):
        chat_model_kwargs_for(None)


class _PartialCfg:
    """A known provider but with no model/api_key attributes at all."""

    def __init__(self, provider):
        self.provider = provider


def test_missing_model_and_api_key_raises_bridge_error_not_attribute_error():
    # A malformed config (right provider, but no model/api_key) must raise the
    # module's BridgeError -- the taxonomy the handler catches -- not a bare
    # AttributeError leaking out.
    with pytest.raises(BridgeError):
        chat_model_kwargs_for(_PartialCfg("anthropic"))


def test_falsy_model_raises_bridge_error():
    with pytest.raises(BridgeError):
        chat_model_kwargs_for(_Cfg("anthropic", "", "k"))


def test_falsy_api_key_raises_bridge_error():
    with pytest.raises(BridgeError):
        chat_model_kwargs_for(_Cfg("anthropic", "claude-opus-4-7", ""))


# --- resolve_use_vision -----------------------------------------------------
#
# Vision (screenshots) is ON for providers whose default models are multimodal
# (anthropic/openai/gemini) and OFF (DOM-only) for generic OpenAI-compatible
# providers (fireworks/openrouter) where we cannot assume the configured model
# accepts images -- so a text-only model still drives the browser headlessly.


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_vision_on_for_multimodal_default_providers(provider):
    assert resolve_use_vision(_Cfg(provider, "m", "k"), env={}) is True


@pytest.mark.parametrize("provider", ["fireworks", "openrouter"])
def test_vision_off_for_generic_openai_compatible_providers(provider):
    assert resolve_use_vision(_Cfg(provider, "m", "k"), env={}) is False


def test_env_override_forces_vision_on():
    assert resolve_use_vision(_Cfg("fireworks", "m", "k"), env={BROWSER_USE_VISION_ENV: "1"}) is True


def test_env_override_forces_vision_off():
    assert resolve_use_vision(_Cfg("anthropic", "m", "k"), env={BROWSER_USE_VISION_ENV: "off"}) is False


def test_vision_off_for_none_config():
    # No provider configured -> conservative DOM-only.
    assert resolve_use_vision(None, env={}) is False
