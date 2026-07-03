"""E-1 ModelCatalog: single source of truth for provider/model defaults.

These tests are the RED phase of the E-1 implementation. They assert the
catalog's basic contract (default lookup, registry consistency, capability
metadata, gemini alias collapse). The matching meta-test that forbids re-adding
hand-maintained dicts in ``cli/``/``runtime/``/``apps/web`` lives in
``test_no_adhoc_model_lists.py``.
"""

from __future__ import annotations

import pytest

from magi_agent.cli.providers import SUPPORTED_PROVIDERS
from magi_agent.models.catalog import ModelCatalog, UnknownModelError
from magi_agent.runtime.model_tiers import ModelTierRegistry


def test_anthropic_default_is_claude_sonnet_5() -> None:
    """Locked default: anthropic flagship for the CLI/serve path is sonnet-5.

    Catching drift here is the point — the catalog is the single source.
    """
    record = ModelCatalog.builtin().default_model_for("anthropic")
    assert record.model == "claude-sonnet-5"
    assert record.provider == "anthropic"
    assert record.litellm_prefix == "anthropic"


def test_default_model_for_every_supported_provider() -> None:
    """Every CLI ``SUPPORTED_PROVIDERS`` entry has a catalog default."""
    catalog = ModelCatalog.builtin()
    for provider in SUPPORTED_PROVIDERS:
        record = catalog.default_model_for(provider)
        assert record is not None
        assert record.provider == provider
        assert record.model
        assert record.litellm_prefix


def test_default_model_unknown_provider_raises() -> None:
    with pytest.raises(UnknownModelError):
        ModelCatalog.builtin().default_model_for("not-a-provider")


def test_cheap_model_for_returns_cheap_tier() -> None:
    """``_materializer_model`` queries the cheap tier; catalog must surface one."""
    catalog = ModelCatalog.builtin()
    # The four providers the legacy ``_materializer_model`` handled.
    for provider in ("anthropic", "openai", "fireworks", "gemini"):
        record = catalog.cheap_model_for(provider)
        assert record is not None
        assert record.provider == provider
        # Cheap tier is by definition the smallest / most-latency-oriented.
        assert record.tier in {"cheap", "standard"}


def test_record_lookup_returns_none_for_unknown() -> None:
    assert ModelCatalog.builtin().record("anthropic", "no-such-model") is None


def test_context_window_returns_int_for_known_records() -> None:
    catalog = ModelCatalog.builtin()
    # Every record carries a context_window; spot-check a flagship.
    cw = catalog.context_window("claude-opus-4-8")
    assert isinstance(cw, int)
    assert cw >= 100_000


def test_all_records_includes_flagships() -> None:
    """Catalog covers every model the registry / dashboard depends on."""
    catalog = ModelCatalog.builtin()
    models = {r.model for r in catalog.all_records()}
    # Anthropic frontier (PR12 user-visible bug fix: 4-8 NOT 4-6).
    assert "claude-opus-4-8" in models
    assert "claude-sonnet-5" in models
    assert "claude-sonnet-4-6" in models
    # Other provider flagships referenced across cli/providers + registry.
    assert "gpt-5.5" in models
    assert "gemini-3.5-flash" in models
    assert "gemini-3.1-pro-preview" in models
    assert "kimi-k2p6" in models
    assert "kimi-k2p7-code" in models
    assert "glm-5p2" in models


def test_new_fireworks_records_keep_provider_defaults_stable() -> None:
    """Adding kimi-k2p7-code / glm-5p2 must NOT flip the fireworks default
    (kimi-k2p6, JSON-order contract) or the cheap-tier pick."""
    catalog = ModelCatalog.builtin()
    assert catalog.default_model_for("fireworks").model == "kimi-k2p6"
    assert catalog.cheap_model_for("fireworks").model == "kimi-k2p6"
    k2p7 = catalog.record("fireworks", "kimi-k2p7-code")
    assert k2p7 is not None
    assert k2p7.context_window == 262_144
    assert k2p7.max_output_tokens == 32_768
    glm = catalog.record("fireworks", "glm-5p2")
    assert glm is not None
    assert glm.tier == "sota"
    assert glm.context_window == 1_000_000
    assert glm.max_output_tokens == 131_072


def test_is_router_alias_distinguishes_openrouter_from_direct() -> None:
    catalog = ModelCatalog.builtin()
    assert catalog.is_router_alias("openai/gpt-5.5") is True
    assert catalog.is_router_alias("claude-sonnet-4-6") is False


def test_builtin_is_cached_singleton() -> None:
    a = ModelCatalog.builtin()
    b = ModelCatalog.builtin()
    assert a is b


def test_every_provider_default_resolves_in_registry_without_unknown_model() -> None:
    """Defaults in the catalog must resolve cleanly in ``ModelTierRegistry``.

    The whole point of E-1 is that ``_DEFAULT_MODEL`` (catalog defaults) and
    ``ModelTierRegistry`` agree on canonical IDs. A drift in either direction
    silently degrades a route to ``unknown_model_standard_no_elevated_capabilities``.
    """
    catalog = ModelCatalog.builtin()
    registry = ModelTierRegistry.with_defaults()
    for provider in SUPPORTED_PROVIDERS:
        record = catalog.default_model_for(provider)
        # OpenRouter's default is a router slug ``openai/gpt-5.5``; the registry
        # does not store router-aliased IDs (the SpawnAgent path only cares
        # about direct ids), so skip router defaults here.
        if record.source == "router":
            continue
        resolved = registry.resolve(provider=record.provider, model=record.model)
        # The registry stamps an ``unknown_model_*`` reason code when the
        # (provider, model) is not catalogued. The default MUST be catalogued.
        for reason in resolved.reason_codes:
            assert "unknown_model" not in reason, (
                f"default {provider}:{record.model} resolved with reason {reason!r}"
            )


def test_gemini_canonical_provider_collapses_google_alias() -> None:
    """E-1 stores gemini ONCE under canonical ``gemini``.

    The legacy registry duplicated each gemini record under both ``google``
    and ``gemini`` providers; the catalog declares the alias via
    ``provider_aliases`` so the registry can expand at build time.
    """
    catalog = ModelCatalog.builtin()
    aliases = catalog.provider_aliases()
    # ``google`` should map to ``gemini`` so the runtime can resolve a record
    # under either label without two literal records.
    assert aliases.get("google") == "gemini"
    # And the catalog must store the canonical (non-aliased) gemini record.
    record = catalog.record("gemini", "gemini-3.5-flash")
    assert record is not None
    assert record.provider == "gemini"
