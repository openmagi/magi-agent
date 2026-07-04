"""E-2 — fail-loud unknown built-in default.

A built-in default is the per-provider model id sourced from the catalog
(``_DEFAULT_MODEL[provider]``). If that id ever fails to resolve in the
catalog (catalog corruption, monkeypatch, package-data drift) the runner
silently builds a LiteLlm against a not-cataloged id and downstream tier
resolution returns a synthetic ``standard``-tier record with no
diagnostics. This test locks the contract that a built-in default which
fails to resolve in the catalog raises :class:`UnknownModelError` at
``resolve_provider_config`` time — and that an *explicit* user override
(MAGI_MODEL / [model].model / model_override) stays permissive so users
can pin a new id before the catalog learns about it.
"""

from __future__ import annotations

import pytest

from magi_agent.cli import providers
from magi_agent.models.catalog import UnknownModelError


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "MAGI_MODEL",
        "MAGI_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "FIREWORKS_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_catalog_known_default_resolves_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = providers.resolve_provider_config(env={"ANTHROPIC_API_KEY": "test-key"}, config={})
    assert cfg is not None
    assert cfg.provider == "anthropic"
    # Default sourced from the catalog; must be the Anthropic flagship sota.
    assert cfg.model == "claude-sonnet-5"


def test_built_in_default_unknown_id_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate catalog/default drift: ``_DEFAULT_MODEL`` carries an id the
    catalog does not know. The fail-loud guard must surface this at
    ``resolve_provider_config`` time so operators see WHY the runner won't
    start, instead of silently downgrading to a synthetic standard tier."""

    # Re-import providers to get a fresh cached dict, then mutate in-place.
    monkeypatch.setitem(
        providers._DEFAULT_MODEL, "anthropic", "claude-totally-fictional-model"
    )
    with pytest.raises(UnknownModelError) as excinfo:
        providers.resolve_provider_config(
            env={"ANTHROPIC_API_KEY": "test-key"}, config={}
        )
    msg = str(excinfo.value)
    assert "anthropic" in msg
    assert "claude-totally-fictional-model" in msg
    assert "catalog" in msg.lower()


def test_built_in_default_deprecated_id_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A built-in default whose catalog record is deprecated must also fail
    loud — silent use of a deprecated default is the staleness vector E-2
    closes. The catalog already carries ``claude-opus-4-6`` as deprecated
    (replacement=``claude-opus-4-8``), so simulate the drift by pointing
    Anthropic's built-in default at the deprecated id."""

    monkeypatch.setitem(providers._DEFAULT_MODEL, "anthropic", "claude-opus-4-6")
    with pytest.raises(UnknownModelError) as excinfo:
        providers.resolve_provider_config(
            env={"ANTHROPIC_API_KEY": "test-key"}, config={}
        )
    msg = str(excinfo.value)
    assert "deprecated" in msg.lower()
    # The fail-loud message should name the replacement so operators know
    # what to bump to.
    assert "claude-opus-4-8" in msg


def test_explicit_magi_model_unknown_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User-pinned MAGI_MODEL with an unknown id must stay permissive — the
    user is allowed to pin a new id before the catalog learns about it."""

    cfg = providers.resolve_provider_config(
        env={"ANTHROPIC_API_KEY": "test-key", "MAGI_MODEL": "my-org-custom-llm-v42"},
        config={},
    )
    assert cfg is not None
    assert cfg.model == "my-org-custom-llm-v42"


def test_explicit_config_model_unknown_does_not_raise() -> None:
    """``[model].model`` config override is permissive (same reason as
    MAGI_MODEL)."""

    cfg = providers.resolve_provider_config(
        env={"ANTHROPIC_API_KEY": "test-key"},
        config={"model": {"model": "in-house-tuned-llama-3-70b"}},
    )
    assert cfg is not None
    assert cfg.model == "in-house-tuned-llama-3-70b"


def test_explicit_model_override_argument_unknown_does_not_raise() -> None:
    """``model_override`` kwarg (TUI picker, chat selector) is permissive."""

    cfg = providers.resolve_provider_config(
        env={"ANTHROPIC_API_KEY": "test-key"},
        config={},
        model_override="my-experimental-model",
    )
    assert cfg is not None
    assert cfg.model == "my-experimental-model"


def test_explicit_slug_override_unknown_does_not_raise() -> None:
    """A slug override (``anthropic/some-unknown-id``) routes via the slug
    provider but keeps the model id permissive (user-pinned)."""

    cfg = providers.resolve_provider_config(
        env={"ANTHROPIC_API_KEY": "test-key"},
        config={},
        model_override="anthropic/some-not-yet-cataloged-id",
    )
    assert cfg is not None
    assert cfg.provider == "anthropic"
    assert cfg.model == "some-not-yet-cataloged-id"


def test_provider_level_default_model_for_function_raises_on_corruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``providers.default_model_for(provider)`` is the TUI/help-string call
    site for the per-provider default. Catalog corruption surfaces here
    too: an id missing from the catalog raises UnknownModelError, not the
    legacy ``UnknownProviderError`` (which is reserved for unsupported
    providers, e.g. ``mistral``)."""

    monkeypatch.setitem(providers._DEFAULT_MODEL, "anthropic", "claude-not-a-real-id")
    with pytest.raises(UnknownModelError):
        providers.default_model_for("anthropic")


def test_unsupported_provider_still_raises_unknown_provider_error() -> None:
    """Regression guard: ``UnknownProviderError`` continues to fire on
    unsupported providers (``mistral`` is not in SUPPORTED_PROVIDERS), it
    is NOT replaced by UnknownModelError."""

    with pytest.raises(providers.UnknownProviderError):
        providers.default_model_for("mistral")
