"""H1 (N-28) - provider detection consults the catalog before heuristics.

``detect_provider`` / ``detect_provider_family`` used to rely purely on
prefix/substring heuristics, so a catalogued id that did not match a prefix
(e.g. the ``haiku`` alias) was silently downgraded to ``"unknown"`` /
``ProviderFamily.DEFAULT``. This locks the catalog-first path and the
``provider_for_model`` query semantics (router-skip, alias-form, miss).
"""

from __future__ import annotations

from magi_agent.cli.providers import SUPPORTED_PROVIDERS
from magi_agent.models.catalog import ModelCatalog
from magi_agent.shared.provider_family import (
    ProviderFamily,
    detect_provider,
    detect_provider_family,
)


def test_haiku_alias_is_detected_via_catalog() -> None:
    """``haiku`` is a catalogued anthropic alias with no ``claude-`` prefix,
    so only a catalog-first lookup can classify it correctly."""

    assert detect_provider("haiku") == "anthropic"
    assert detect_provider_family("haiku") is ProviderFamily.ANTHROPIC


def test_every_supported_provider_default_model_is_classified() -> None:
    """The per-provider default model (except the openrouter meta-router)
    must map to a concrete family, never the DEFAULT fallback."""

    catalog = ModelCatalog.builtin()
    for provider in SUPPORTED_PROVIDERS:
        if provider == "openrouter":
            continue
        model = catalog.default_model_for(provider).model
        assert detect_provider_family(model) is not ProviderFamily.DEFAULT, (
            f"default model {model!r} for provider {provider!r} fell to "
            "ProviderFamily.DEFAULT"
        )


def test_existing_heuristic_contract_strings_unchanged() -> None:
    """Catalog-first must not change the four legacy return strings for the
    prefix-matched ids (regression guard)."""

    assert detect_provider("claude-sonnet-4-6") == "anthropic"
    assert detect_provider("openai/gpt-5.5") == "openai"
    assert detect_provider("gemini-3.5-flash") == "google"
    assert detect_provider("totally-unknown-model") == "unknown"


def _fake_payload() -> dict[str, object]:
    """A minimal catalog payload exercising alias + router records."""

    base = {
        "label": "x",
        "tier": "cheap",
        "capabilities": ["streaming"],
        "context_window": 100_000,
        "max_output_tokens": 8_000,
        "last_verified": "2026-06-21",
        "reasoning_style": "none",
    }
    return {
        "schema_version": 1,
        "provider_aliases": {"google": "gemini"},
        "records": [
            {
                "provider": "gemini",
                "model": "g-test",
                "source": "direct",
                "litellm_prefix": "gemini",
                **base,
            },
            {
                "provider": "openrouter",
                "model": "vendor/x-router",
                "source": "router",
                "litellm_prefix": "openrouter",
                **base,
            },
        ],
    }


def test_provider_for_model_bare_and_alias_forms() -> None:
    catalog = ModelCatalog.from_payload(_fake_payload())
    assert catalog.provider_for_model("g-test") == "gemini"
    assert catalog.provider_for_model("gemini/g-test") == "gemini"
    assert catalog.provider_for_model("google/g-test") == "gemini"


def test_provider_for_model_skips_router_records() -> None:
    catalog = ModelCatalog.from_payload(_fake_payload())
    assert catalog.provider_for_model("vendor/x-router") is None


def test_provider_for_model_returns_none_for_miss() -> None:
    catalog = ModelCatalog.from_payload(_fake_payload())
    assert catalog.provider_for_model("nope-not-here") is None


def test_provider_for_model_is_case_insensitive() -> None:
    catalog = ModelCatalog.from_payload(_fake_payload())
    assert catalog.provider_for_model("G-TEST") == "gemini"


def test_id_forms_include_bare_prefixed_and_alias() -> None:
    catalog = ModelCatalog.from_payload(_fake_payload())
    (gemini_record,) = [
        r for r in catalog.all_records() if r.model == "g-test"
    ]
    forms = set(catalog.id_forms(gemini_record))
    assert forms == {"g-test", "gemini/g-test", "google/g-test"}
