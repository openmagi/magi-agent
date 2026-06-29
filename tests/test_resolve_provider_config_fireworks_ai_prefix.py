"""PR-O TDD: ``resolve_provider_config`` must recognize the ``fireworks_ai/``
prefix so PR-M's provider-prefixed model strings round-trip correctly for
Fireworks (catalog provider name is ``"fireworks"`` but litellm uses
``"fireworks_ai/"`` as the prefix).

Repro (0.1.91, direct-debug by Kevin):

    >>> from magi_agent.cli.providers import resolve_provider_config
    >>> # PR-M (#1133) passes ``cfg.litellm_model`` as ``model_override`` to the
    >>> # child runner; for Fireworks that string is ``fireworks_ai/<model>``.
    >>> cfg = resolve_provider_config(model_override='fireworks_ai/kimi-k2p6')
    >>> cfg.provider, cfg.model
    ('openai', 'fireworks_ai/kimi-k2p6')   # WRONG: should be ('fireworks', 'kimi-k2p6')

Because the provider is mis-attributed to ``openai``, catalog lookup
``(openai, "fireworks_ai/kimi-k2p6")`` misses, PR-L (#1130) hits the
byte-identical pass-through, and ``reasoning_effort`` is sent to LiteLLM which
rejects it for OpenAI-routed calls.

Shape A fix: extend the prefix-alias table so ``fireworks_ai`` maps to the
``fireworks`` provider, mirroring the catalog's ``litellm_prefix`` for that
provider. The fix is data-driven (derived from ``_LITELLM_PREFIX``) so any
future catalog provider whose litellm_prefix differs from its provider name
also round-trips out of the box.
"""

from __future__ import annotations

import pytest

from magi_agent.cli.providers import (
    SUPPORTED_PROVIDERS,
    ProviderConfig,
    resolve_provider_config,
)


# All providers wired with stub keys + an unrelated config provider/model so
# the "no slug" branch would mis-attribute to openai (matching Kevin's repro
# environment as closely as possible while staying hermetic).
_CFG_ALL_KEYS: dict[str, object] = {
    "model": {"provider": "openai", "model": "gpt-5.5"},
    "providers": {
        "openai": {"api_key": "oa-key"},
        "anthropic": {"api_key": "an-key"},
        "gemini": {"api_key": "gm-key"},
        "fireworks": {"api_key": "fw-key"},
        "openrouter": {"api_key": "or-key"},
    },
}


def _resolve(model: str) -> ProviderConfig:
    cfg = resolve_provider_config(model_override=model, env={}, config=_CFG_ALL_KEYS)
    assert cfg is not None, f"expected a ProviderConfig for {model!r}, got None"
    return cfg


def test_fireworks_ai_prefix_resolves_to_fireworks_provider() -> None:
    """``fireworks_ai/<model>`` must route to the fireworks provider, not the
    config default. This is the exact failure mode Kevin hit on 0.1.91."""
    cfg = _resolve("fireworks_ai/kimi-k2p6")
    assert cfg.provider == "fireworks"
    assert cfg.api_key == "fw-key"


def test_fireworks_prefix_still_works_back_compat() -> None:
    """The historical ``fireworks/<model>`` prefix (matching SUPPORTED_PROVIDERS
    name) must keep resolving identically. Back-compat for any caller that
    builds the slug from the catalog provider name rather than ``litellm_prefix``."""
    cfg = _resolve("fireworks/kimi-k2p6")
    assert cfg.provider == "fireworks"
    assert cfg.model == "kimi-k2p6"
    assert cfg.api_key == "fw-key"


def test_fireworks_ai_prefix_strips_model_correctly() -> None:
    """Only the ``fireworks_ai/`` prefix is stripped; the model id (which may
    itself contain slashes, e.g. ``accounts/fireworks/models/...``) is returned
    intact so LiteLLM gets the raw upstream id."""
    cfg = _resolve("fireworks_ai/accounts/fireworks/models/kimi-k2-instruct-0905")
    assert cfg.provider == "fireworks"
    assert cfg.model == "accounts/fireworks/models/kimi-k2-instruct-0905"


@pytest.mark.parametrize(
    "slug, expected_provider, expected_model",
    [
        ("anthropic/claude-sonnet-4-6", "anthropic", "claude-sonnet-4-6"),
        ("openai/gpt-5.5-pro", "openai", "gpt-5.5-pro"),
        ("gemini/gemini-3.1-pro-preview", "gemini", "gemini-3.1-pro-preview"),
        # ``google`` alias (chat-core slug map writes this for Gemini).
        ("google/gemini-3.1-pro-preview", "gemini", "gemini-3.1-pro-preview"),
        # OpenRouter passes a nested ``<vendor>/<model>`` id, so rest may
        # contain another slash — must be returned verbatim.
        ("openrouter/openai/gpt-5.5", "openrouter", "openai/gpt-5.5"),
    ],
)
def test_other_providers_unchanged(slug: str, expected_provider: str, expected_model: str) -> None:
    """No regression for prefixes that already worked before the fix."""
    cfg = _resolve(slug)
    assert cfg.provider == expected_provider
    assert cfg.model == expected_model


@pytest.mark.parametrize("provider", SUPPORTED_PROVIDERS)
def test_round_trip_litellm_model_matches(provider: str) -> None:
    """The contract PR-M relies on: ``resolve_provider_config(cfg.litellm_model)``
    must yield an equivalent ``(provider, model)`` for every supported provider.

    Before the fix, fireworks fails this round-trip (litellm_model emits
    ``fireworks_ai/<model>``, which the resolver mis-attributes to openai).
    """
    seed = ProviderConfig(provider=provider, model="fake-model-id", api_key="k")
    wire = seed.litellm_model  # what PR-M passes to the child runner
    resolved = resolve_provider_config(model_override=wire, env={}, config=_CFG_ALL_KEYS)
    assert resolved is not None, f"round-trip lost provider config for {provider!r}"
    assert resolved.provider == provider, (
        f"round-trip mis-attributed provider for {provider!r}: "
        f"emitted {wire!r}, resolved as {resolved.provider!r}/{resolved.model!r}"
    )
    assert resolved.model == "fake-model-id"
