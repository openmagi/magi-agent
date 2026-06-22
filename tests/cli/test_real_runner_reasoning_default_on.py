"""E-6: ``MAGI_MODEL_REASONING_DEFAULT_ON`` sources per-model reasoning kwargs
from the ModelCatalog when set, while OFF stays byte-identical to today.

Default-OFF for soak per AGENTS.md flag-promotion-verification — the flip to
default-ON happens in a follow-up PR after ON-path CI verification. These
tests pin the contract end-to-end so the soak flip is provably safe.

Precedence rules under test (highest first):

1. ``MAGI_MODEL_THINKING_TYPE=adaptive`` wins outright.
2. ``MAGI_MODEL_THINKING_BUDGET_TOKENS`` (positive int) wins over effort/catalog.
3. Per-turn ``reasoningEffort`` ContextVar wins over env effort/catalog.
4. ``MAGI_MODEL_REASONING_EFFORT=off`` (or none/0/false/disable/disabled) is an
   explicit kill switch that returns ``{}`` even when the flag is ON.
5. ``MAGI_MODEL_REASONING_EFFORT`` (truthy non-disable value) wins over catalog.
6. With the flag ON and nothing else set, the catalog default applies:
   - Opus 4.7/4.8 → ``thinking={"type":"adaptive"}``
   - Sonnet 4.6 / GPT-5.5 / Gemini 3.1 Pro → ``reasoning_effort="high"``
   - Haiku / Flash / Kimi → ``{}``
7. With the flag OFF, behavior is byte-identical to before E-6 — no env set ⇒
   ``{}`` regardless of which (provider, model) is built.
"""
from __future__ import annotations

import pytest

from magi_agent.cli.providers import ProviderConfig
from magi_agent.cli.real_runner import _model_reasoning_kwargs


def _cfg(provider: str, model: str) -> ProviderConfig:
    return ProviderConfig(provider=provider, model=model, api_key="x")


# -- Flag OFF -----------------------------------------------------------------
#
# Identical to the today-behavior table: no env, no config-side default ever.
# This is the "OFF is byte-identical" guarantee.


@pytest.mark.parametrize(
    "provider, model",
    [
        ("anthropic", "claude-opus-4-8"),
        ("anthropic", "claude-sonnet-4-6"),
        ("anthropic", "claude-haiku-4-5"),
        ("openai", "gpt-5.5"),
        ("openai", "gpt-5.4-mini"),
        ("gemini", "gemini-3.1-pro-preview"),
        ("gemini", "gemini-3.5-flash"),
        ("fireworks", "kimi-k2p6"),
    ],
)
def test_flag_off_is_byte_identical_no_env(provider: str, model: str) -> None:
    """With the flag OFF (default), no env set ⇒ ``{}`` for every provider/model."""
    config = _cfg(provider, model)
    assert (
        _model_reasoning_kwargs({}, provider=config.provider, config=config) == {}
    )


def test_flag_off_existing_thinking_type_still_works() -> None:
    """Existing env knobs keep working under the OFF default — they were the only
    path before E-6 and must remain so."""
    config = _cfg("anthropic", "claude-opus-4-8")
    assert _model_reasoning_kwargs(
        {"MAGI_MODEL_THINKING_TYPE": "adaptive"},
        provider=config.provider,
        config=config,
    ) == {"thinking": {"type": "adaptive"}}


def test_flag_off_existing_budget_tokens_still_works() -> None:
    config = _cfg("anthropic", "claude-sonnet-4-6")
    assert _model_reasoning_kwargs(
        {"MAGI_MODEL_THINKING_BUDGET_TOKENS": "8192"},
        provider=config.provider,
        config=config,
    ) == {"thinking": {"type": "enabled", "budget_tokens": 8192}}


def test_flag_off_existing_reasoning_effort_still_works() -> None:
    config = _cfg("openai", "gpt-5.5")
    assert _model_reasoning_kwargs(
        {"MAGI_MODEL_REASONING_EFFORT": "high"},
        provider=config.provider,
        config=config,
    ) == {"reasoning_effort": "high"}


# -- Flag ON ------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider, model, expected",
    [
        # Adaptive-only flagships → thinking adaptive shape.
        ("anthropic", "claude-opus-4-8", {"thinking": {"type": "adaptive"}}),
        ("anthropic", "claude-opus-4-6", {"thinking": {"type": "adaptive"}}),
        # Effort-style records → litellm cross-provider reasoning_effort=high.
        ("anthropic", "claude-sonnet-4-6", {"reasoning_effort": "high"}),
        ("openai", "gpt-5.5", {"reasoning_effort": "high"}),
        ("openai", "gpt-5.5-pro", {"reasoning_effort": "high"}),
        ("gemini", "gemini-3.1-pro-preview", {"reasoning_effort": "high"}),
        ("openrouter", "openai/gpt-5.5", {"reasoning_effort": "high"}),
        ("openrouter", "anthropic/claude-sonnet-4-6", {"reasoning_effort": "high"}),
        # Non-reasoning models → still ``{}`` (catalog records carry
        # reasoning_style="none" OR omit "reasoning" from capabilities).
        ("anthropic", "claude-haiku-4-5", {}),
        ("anthropic", "haiku", {}),
        ("gemini", "gemini-3.5-flash", {}),
        ("gemini", "gemini-3.1-flash-lite-preview", {}),
        ("openai", "gpt-5.4-mini", {}),
        ("openai", "gpt-5.4-nano", {}),
        ("fireworks", "kimi-k2p6", {}),
        ("fireworks", "kimi-k2p5", {}),
        ("fireworks", "minimax-m2p7", {}),
    ],
)
def test_flag_on_catalog_default_per_model(
    provider: str, model: str, expected: dict
) -> None:
    config = _cfg(provider, model)
    env = {"MAGI_MODEL_REASONING_DEFAULT_ON": "1"}
    assert (
        _model_reasoning_kwargs(env, provider=config.provider, config=config)
        == expected
    )


def test_flag_on_unknown_model_falls_back_to_empty() -> None:
    """The catalog returns ``{}`` for ids it does not know; that must NOT crash —
    callers may build ``ProviderConfig(model="some-custom-pin")``."""
    config = _cfg("anthropic", "no-such-model")
    env = {"MAGI_MODEL_REASONING_DEFAULT_ON": "1"}
    assert (
        _model_reasoning_kwargs(env, provider=config.provider, config=config) == {}
    )


# -- Env-var overrides on top of the catalog default --------------------------


def test_flag_on_with_explicit_disable_returns_empty() -> None:
    """``MAGI_MODEL_REASONING_EFFORT=off`` is an operator kill switch — it wins
    over the catalog default even when the flag is on."""
    config = _cfg("anthropic", "claude-opus-4-8")
    for disable in ("off", "none", "0", "false", "disable", "disabled"):
        env = {
            "MAGI_MODEL_REASONING_DEFAULT_ON": "1",
            "MAGI_MODEL_REASONING_EFFORT": disable,
        }
        assert (
            _model_reasoning_kwargs(env, provider=config.provider, config=config) == {}
        ), disable


def test_flag_on_with_explicit_effort_value_wins_over_catalog() -> None:
    """Operator-pinned ``reasoning_effort=medium`` should override the catalog's
    ``high`` default — env knobs are always overrides, never additive."""
    config = _cfg("openai", "gpt-5.5")
    env = {
        "MAGI_MODEL_REASONING_DEFAULT_ON": "1",
        "MAGI_MODEL_REASONING_EFFORT": "medium",
    }
    assert _model_reasoning_kwargs(
        env, provider=config.provider, config=config
    ) == {"reasoning_effort": "medium"}


def test_flag_on_with_explicit_thinking_type_wins_over_catalog() -> None:
    """``MAGI_MODEL_THINKING_TYPE=adaptive`` is the highest-precedence escape
    hatch; it must still win even when the catalog would have returned a
    different shape (e.g. effort=high for sonnet)."""
    config = _cfg("anthropic", "claude-sonnet-4-6")
    env = {
        "MAGI_MODEL_REASONING_DEFAULT_ON": "1",
        "MAGI_MODEL_THINKING_TYPE": "adaptive",
    }
    assert _model_reasoning_kwargs(
        env, provider=config.provider, config=config
    ) == {"thinking": {"type": "adaptive"}}


def test_flag_on_with_explicit_budget_wins_over_catalog() -> None:
    """A positive ``MAGI_MODEL_THINKING_BUDGET_TOKENS`` must beat the catalog
    default — operators pinning a budget should not get a surprise effort=high
    layered on top."""
    config = _cfg("anthropic", "claude-sonnet-4-6")
    env = {
        "MAGI_MODEL_REASONING_DEFAULT_ON": "1",
        "MAGI_MODEL_THINKING_BUDGET_TOKENS": "12288",
    }
    assert _model_reasoning_kwargs(
        env, provider=config.provider, config=config
    ) == {"thinking": {"type": "enabled", "budget_tokens": 12288}}


# -- Provider quirks preserved under the flag ---------------------------------


def test_flag_on_fireworks_drops_effort_even_if_catalog_authoring_drifted() -> None:
    """Fireworks rejects ``reasoning_effort`` for any value. The catalog already
    tags Kimi as ``reasoning_style="none"`` (the default ⇒ ``{}``), but if a
    future authoring slip tags it ``"effort"`` the runtime must still drop the
    parameter rather than ship a guaranteed-400 turn."""
    from magi_agent.models.types import ModelRecord
    from magi_agent.models import ModelCatalog

    # Build a one-off catalog with kimi marked as effort-style.
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
                    last_verified="2026-06-21",
                    reasoning_style="effort",
                ).model_dump()
            ],
        }
    )
    assert drifted.reasoning_default("fireworks", "kimi-k2p6") == {
        "reasoning_effort": "high"
    }
    # The runtime guardrail still drops the param for fireworks.
    # We exercise the guardrail by monkeypatching ``ModelCatalog.builtin``.
    import magi_agent.models as models_pkg

    real_builtin = models_pkg.ModelCatalog.builtin
    models_pkg.ModelCatalog.builtin = staticmethod(lambda: drifted)  # type: ignore[attr-defined]
    try:
        config = _cfg("fireworks", "kimi-k2p6")
        env = {"MAGI_MODEL_REASONING_DEFAULT_ON": "1"}
        assert (
            _model_reasoning_kwargs(env, provider="fireworks", config=config) == {}
        )
    finally:
        models_pkg.ModelCatalog.builtin = real_builtin  # type: ignore[attr-defined]
        # Reload to discard the staticmethod swap from the lru-cached singleton
        # (the catalog itself is unaffected; we only swapped the classmethod).


# -- Backward compatibility ---------------------------------------------------


def test_flag_on_without_config_falls_back_to_empty() -> None:
    """The flag is meaningful only when ``config`` is supplied. Callers that do
    not pass ``config`` (e.g. legacy tests) keep the byte-identical OFF path."""
    env = {"MAGI_MODEL_REASONING_DEFAULT_ON": "1"}
    assert _model_reasoning_kwargs(env) == {}
    assert _model_reasoning_kwargs(env, provider="anthropic") == {}
