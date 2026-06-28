"""PR-L root fix: ``MAGI_MODEL_REASONING_EFFORT`` and the per-turn ContextVar
override consult the ModelCatalog for the per-model reasoning shape.

Before this fix, both code paths returned ``{"reasoning_effort": <value>}``
blind to provider/model. Anthropic adaptive-only models (Opus 4.7/4.8) and
Gemini reject that wire shape; LiteLLM surfaces an UnsupportedParamsError
after 4 retries and the SpawnAgent child terminates as ``error/failed`` with
``summary_len=0``. Kevin's 0.1.89 sandbox log (line 233/243/278) captured the
exact failure for ``claude-opus-4-8``, ``claude-sonnet-4-6``,
``gemini-3.1-pro-preview`` and ``gemini-3.5-flash``; only the OpenAI children
(``gpt-5.5``, ``gpt-5.5-pro``, ``gpt-5.4-mini``) succeeded because they
natively accept ``reasoning_effort``.

The fix routes both the per-turn override and the env knob through
:meth:`magi_agent.models.ModelCatalog.reasoning_default`'s sibling
``reasoning_style`` field:

* ``adaptive`` -> ``{"thinking": {"type": "adaptive"}}``
* ``effort``   -> ``{"reasoning_effort": <normalized value>}``
* ``budget``   -> ``{}`` (use ``MAGI_MODEL_THINKING_BUDGET_TOKENS`` for these).
* ``none``     -> ``{}``

When ``config`` is missing or the catalog has no record (custom pin /
OpenRouter routed-only model), today's byte-identical pass-through is kept so
the wide swath of fixtures that build models without a typed config keep
working.

The escape hatches preserve their top precedence: ``MAGI_MODEL_THINKING_TYPE=
adaptive`` and ``MAGI_MODEL_THINKING_BUDGET_TOKENS`` still win, and
``MAGI_MODEL_REASONING_EFFORT=off`` (and the disable-token family) still kill
all reasoning kwargs.
"""

from __future__ import annotations

import pytest

from magi_agent.cli import real_runner
from magi_agent.cli.providers import ProviderConfig
from magi_agent.cli.real_runner import _model_reasoning_kwargs


def _cfg(provider: str, model: str) -> ProviderConfig:
    return ProviderConfig(provider=provider, model=model, api_key="x")


# ---------------------------------------------------------------------------
# Env-path branch: MAGI_MODEL_REASONING_EFFORT consults the catalog.
# ---------------------------------------------------------------------------


def test_env_effort_with_adaptive_model_returns_thinking_adaptive() -> None:
    """Kevin's lab env sets ``MAGI_MODEL_REASONING_EFFORT=medium``. For
    ``claude-opus-4-8`` (catalog style=adaptive) the wire shape must be
    ``thinking={type:adaptive}`` (operator's effort level is discarded because
    adaptive-only models do not accept ``reasoning_effort``). Without this
    branch LiteLLM 400s with ``UnsupportedParamsError`` after 4 retries."""
    config = _cfg("anthropic", "claude-opus-4-8")
    assert _model_reasoning_kwargs(
        {"MAGI_MODEL_REASONING_EFFORT": "medium"},
        provider=config.provider,
        config=config,
    ) == {"thinking": {"type": "adaptive"}}


def test_env_effort_with_effort_model_returns_reasoning_effort() -> None:
    """For ``gpt-5.5`` (catalog style=effort) the wire shape is the OpenAI
    cross-provider ``reasoning_effort`` knob; operator value is preserved."""
    config = _cfg("openai", "gpt-5.5")
    assert _model_reasoning_kwargs(
        {"MAGI_MODEL_REASONING_EFFORT": "high"},
        provider=config.provider,
        config=config,
    ) == {"reasoning_effort": "high"}


def test_env_effort_with_none_model_returns_empty() -> None:
    """``fireworks`` ``kimi-k2p6`` has ``reasoning_style="none"`` AND the
    provider rejects the ``reasoning_effort`` param outright; either signal
    drops the kwarg silently rather than ship a guaranteed-400 turn."""
    config = _cfg("fireworks", "kimi-k2p6")
    assert (
        _model_reasoning_kwargs(
            {"MAGI_MODEL_REASONING_EFFORT": "medium"},
            provider=config.provider,
            config=config,
        )
        == {}
    )


def test_env_effort_with_unknown_provider_falls_back_to_today() -> None:
    """Custom pin / OpenRouter-routed model the catalog does not know -> keep
    the byte-identical pass-through ``{"reasoning_effort": <value>}`` so the
    library of fixtures that build with exotic provider/model strings does not
    regress."""
    config = _cfg("custom-provider", "unknown-model")
    assert _model_reasoning_kwargs(
        {"MAGI_MODEL_REASONING_EFFORT": "medium"},
        provider=config.provider,
        config=config,
    ) == {"reasoning_effort": "medium"}


def test_env_effort_disable_token_still_wins() -> None:
    """The kill-switch family (``off``/``none``/``0``/``false``/...) returns
    ``{}`` even with a catalog record present; operators retain a hard escape."""
    config = _cfg("anthropic", "claude-opus-4-8")
    for token in ("off", "none", "0", "false", "disable", "disabled"):
        assert (
            _model_reasoning_kwargs(
                {"MAGI_MODEL_REASONING_EFFORT": token},
                provider=config.provider,
                config=config,
            )
            == {}
        ), token


# ---------------------------------------------------------------------------
# Per-turn ContextVar branch: dashboard's per-turn picker also consults the
# catalog (so flipping the picker to ``medium`` on an Opus turn does not 400).
# ---------------------------------------------------------------------------


def test_per_turn_override_with_adaptive_model_returns_thinking_adaptive() -> None:
    config = _cfg("anthropic", "claude-opus-4-8")
    token = real_runner.set_per_turn_reasoning_effort("medium")
    try:
        kwargs = _model_reasoning_kwargs(env={}, provider=config.provider, config=config)
    finally:
        real_runner.reset_per_turn_reasoning_effort(token)
    assert kwargs == {"thinking": {"type": "adaptive"}}


def test_per_turn_override_with_effort_model_normalizes_max_for_openai() -> None:
    """OpenAI rejects ``reasoning_effort=max``; the per-provider value map
    rewrites ``max -> xhigh``. The catalog routing must still apply that
    normalization on the effort branch."""
    config = _cfg("openai", "gpt-5.5")
    token = real_runner.set_per_turn_reasoning_effort("max")
    try:
        kwargs = _model_reasoning_kwargs(env={}, provider=config.provider, config=config)
    finally:
        real_runner.reset_per_turn_reasoning_effort(token)
    assert kwargs == {"reasoning_effort": "xhigh"}


def test_per_turn_override_with_none_model_returns_empty() -> None:
    """Dashboard picker on a fireworks Kimi turn must not break it."""
    config = _cfg("fireworks", "kimi-k2p6")
    token = real_runner.set_per_turn_reasoning_effort("high")
    try:
        kwargs = _model_reasoning_kwargs(env={}, provider=config.provider, config=config)
    finally:
        real_runner.reset_per_turn_reasoning_effort(token)
    assert kwargs == {}


# ---------------------------------------------------------------------------
# Provider reject set (fireworks) still wins after catalog classification.
# ---------------------------------------------------------------------------


def test_fireworks_rejected_even_when_catalog_says_effort_for_effort_model() -> None:
    """If a future authoring slip flips a fireworks record to
    ``reasoning_style="effort"``, the runtime guard still drops the kwarg
    rather than ship a guaranteed-400 turn. Built via a one-off catalog so the
    test does not depend on a real drifted record."""
    from magi_agent.models import ModelCatalog
    from magi_agent.models.types import ModelRecord

    drifted = ModelCatalog.from_payload(
        {
            "schema_version": 1,
            "provider_aliases": {},
            "records": [
                ModelRecord(
                    provider="fireworks",
                    model="kimi-k2p6",
                    label="Kimi K2.6",
                    source="direct",
                    tier="cheap",
                    capabilities=("streaming", "reasoning"),
                    context_window=196608,
                    max_output_tokens=16000,
                    litellm_prefix="fireworks_ai",
                    last_verified="2026-06-27",
                    reasoning_style="effort",
                ).model_dump()
            ],
        }
    )
    import magi_agent.models as models_pkg

    real_builtin = models_pkg.ModelCatalog.builtin
    models_pkg.ModelCatalog.builtin = staticmethod(lambda: drifted)  # type: ignore[attr-defined]
    try:
        config = _cfg("fireworks", "kimi-k2p6")
        assert (
            _model_reasoning_kwargs(
                {"MAGI_MODEL_REASONING_EFFORT": "high"},
                provider=config.provider,
                config=config,
            )
            == {}
        )
    finally:
        models_pkg.ModelCatalog.builtin = real_builtin  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Escape hatches still beat the catalog routing.
# ---------------------------------------------------------------------------


def test_thinking_type_adaptive_env_still_wins_over_catalog() -> None:
    """``MAGI_MODEL_THINKING_TYPE=adaptive`` is the top-precedence escape hatch
    and must still win even when the catalog would have classified differently
    (e.g. a sonnet effort-style record)."""
    config = _cfg("anthropic", "claude-sonnet-4-6")
    assert _model_reasoning_kwargs(
        {
            "MAGI_MODEL_THINKING_TYPE": "adaptive",
            "MAGI_MODEL_REASONING_EFFORT": "high",
        },
        provider=config.provider,
        config=config,
    ) == {"thinking": {"type": "adaptive"}}


def test_thinking_budget_tokens_still_wins_over_catalog() -> None:
    """A positive ``MAGI_MODEL_THINKING_BUDGET_TOKENS`` outranks the catalog
    routing — operators pinning a budget should not get an effort layered on."""
    config = _cfg("anthropic", "claude-sonnet-4-6")
    assert _model_reasoning_kwargs(
        {
            "MAGI_MODEL_THINKING_BUDGET_TOKENS": "12288",
            "MAGI_MODEL_REASONING_EFFORT": "high",
        },
        provider=config.provider,
        config=config,
    ) == {"thinking": {"type": "enabled", "budget_tokens": 12288}}


# ---------------------------------------------------------------------------
# Back-compat: callers that pass no ``config`` keep the today-byte-identical
# shape so legacy fixtures (and the parity sweeps in
# ``tests/cli/test_real_runner_model_knob_parity.py``) keep working.
# ---------------------------------------------------------------------------


def test_config_is_none_returns_today_behavior_for_back_compat() -> None:
    """Without ``config``, the routing has no (provider, model) to consult; the
    function must return the today-byte-identical ``{"reasoning_effort": x}``."""
    assert _model_reasoning_kwargs({"MAGI_MODEL_REASONING_EFFORT": "medium"}) == {
        "reasoning_effort": "medium"
    }
    # Provider-only (no config) also stays byte-identical (preserves
    # ``test_reasoning_effort_max_passes_through_for_anthropic`` semantics).
    assert _model_reasoning_kwargs(
        {"MAGI_MODEL_REASONING_EFFORT": "max"}, provider="anthropic"
    ) == {"reasoning_effort": "max"}
