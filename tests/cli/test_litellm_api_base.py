"""LiteLlm model construction can be routed through an OpenAI/Anthropic-compatible
gateway (the in-cluster api-proxy) instead of going direct to provider endpoints.

When ``MAGI_LLM_API_BASE`` is set, every LiteLlm the runtime builds (the main turn
model and forked child/subagent models, which share ``_build_litellm_model``) must
target that base and carry the gateway token. The api-proxy authenticates on an
``x-api-key`` header, so the token is passed both as ``api_key`` and as an explicit
header so OpenAI-prefixed models (which would otherwise send ``Authorization: Bearer``)
still authenticate.

Default-OFF: with no env set, construction is unchanged (direct to provider).
"""
from __future__ import annotations

from magi_agent.cli.providers import ProviderConfig
from magi_agent.cli.real_runner import _build_litellm_model, _model_api_base_kwargs


def _cfg() -> ProviderConfig:
    return ProviderConfig(provider="anthropic", model="claude-opus-4-6", api_key="provider-key")


def test_api_base_kwargs_empty_when_unset():
    assert _model_api_base_kwargs({}) == {}


def test_api_base_kwargs_routes_through_proxy_when_set():
    kw = _model_api_base_kwargs(
        {
            "MAGI_LLM_API_BASE": "http://api-proxy:3001",
            "MAGI_LLM_API_KEY": "gw-token",
        }
    )
    assert kw["api_base"] == "http://api-proxy:3001"
    # token replaces the provider key as the litellm api_key...
    assert kw["api_key"] == "gw-token"
    # ...and is also presented as x-api-key so openai-format calls authenticate.
    assert kw["extra_headers"]["x-api-key"] == "gw-token"


def test_api_base_header_name_overridable():
    kw = _model_api_base_kwargs(
        {
            "MAGI_LLM_API_BASE": "http://api-proxy:3001",
            "MAGI_LLM_API_KEY": "gw-token",
            "MAGI_LLM_API_HEADER": "x-gateway-token",
        }
    )
    assert kw["extra_headers"] == {"x-gateway-token": "gw-token"}


def test_api_base_falls_back_to_provider_key_when_no_token():
    kw = _model_api_base_kwargs({"MAGI_LLM_API_BASE": "http://api-proxy:3001"})
    assert kw["api_base"] == "http://api-proxy:3001"
    assert "api_key" not in kw  # caller keeps config.api_key
    assert "extra_headers" not in kw


def _litellm_kwargs(model: object) -> dict:
    extra = dict(getattr(model, "_additional_args", {}) or {})
    api_key = getattr(model, "api_key", None)
    if api_key is not None and "api_key" not in extra:
        extra["api_key"] = api_key
    return extra


def test_build_litellm_model_unchanged_when_disabled():
    extra = _litellm_kwargs(_build_litellm_model(_cfg(), env={}))
    assert "api_base" not in extra
    assert extra.get("api_key") == "provider-key"


def test_build_litellm_model_routes_through_proxy_when_enabled():
    env = {"MAGI_LLM_API_BASE": "http://api-proxy:3001", "MAGI_LLM_API_KEY": "gw-token"}
    extra = _litellm_kwargs(_build_litellm_model(_cfg(), env=env))
    assert extra.get("api_base") == "http://api-proxy:3001"
    assert extra.get("extra_headers", {}).get("x-api-key") == "gw-token"
    # retry kwargs still present (not clobbered).
    assert extra.get("num_retries", 0) >= 1
    assert extra.get("api_key") == "gw-token"
