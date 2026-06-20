"""The CLI model build must support extended thinking / reasoning.

Published frontier coding-benchmark numbers are measured with adaptive
thinking enabled and thinking blocks preserved across tool turns; the CLI's
LiteLlm build previously set no reasoning parameters at all, benchmarking the
model in a strictly weaker mode. Default stays OFF (byte-identical build);
profiles opt in via env.
"""
from __future__ import annotations

from magi_agent.cli.providers import ProviderConfig
from magi_agent.cli.real_runner import _build_litellm_model, _model_reasoning_kwargs


def _cfg() -> ProviderConfig:
    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-5", api_key="x")


def test_reasoning_kwargs_default_off():
    assert _model_reasoning_kwargs({}) == {}


def test_reasoning_kwargs_effort():
    kw = _model_reasoning_kwargs({"MAGI_MODEL_REASONING_EFFORT": "high"})
    assert kw == {"reasoning_effort": "high"}


def test_reasoning_kwargs_explicit_budget_takes_precedence():
    kw = _model_reasoning_kwargs(
        {
            "MAGI_MODEL_REASONING_EFFORT": "high",
            "MAGI_MODEL_THINKING_BUDGET_TOKENS": "8192",
        }
    )
    assert kw == {"thinking": {"type": "enabled", "budget_tokens": 8192}}


def test_reasoning_kwargs_off_values_and_garbage():
    assert _model_reasoning_kwargs({"MAGI_MODEL_REASONING_EFFORT": "off"}) == {}
    assert _model_reasoning_kwargs({"MAGI_MODEL_REASONING_EFFORT": "none"}) == {}
    assert _model_reasoning_kwargs({"MAGI_MODEL_THINKING_BUDGET_TOKENS": "nope"}) == {}
    assert _model_reasoning_kwargs({"MAGI_MODEL_THINKING_BUDGET_TOKENS": "-5"}) == {}


def test_litellm_model_built_with_reasoning_effort(monkeypatch):
    monkeypatch.setenv("MAGI_MODEL_REASONING_EFFORT", "high")
    model = _build_litellm_model(_cfg())
    extra = getattr(model, "_additional_args", {}) or {}
    assert extra.get("reasoning_effort") == "high"


def test_litellm_model_default_has_no_reasoning(monkeypatch):
    monkeypatch.delenv("MAGI_MODEL_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("MAGI_MODEL_THINKING_BUDGET_TOKENS", raising=False)
    model = _build_litellm_model(_cfg())
    extra = getattr(model, "_additional_args", {}) or {}
    assert "reasoning_effort" not in extra
    assert "thinking" not in extra


def test_eval_profile_defaults_enable_reasoning_at_max():
    # Published numbers are measured at MAX effort (adaptive models map
    # reasoning_effort -> thinking={type:adaptive} + output_config.effort).
    from magi_agent.runtime.local_defaults import EVAL_RUNTIME_ENV_DEFAULTS

    assert EVAL_RUNTIME_ENV_DEFAULTS.get("MAGI_MODEL_REASONING_EFFORT") == "max"


def test_reasoning_kwargs_adaptive_thinking_type():
    # Adaptive-only models (Opus 4.7/4.8) reject {type:enabled, budget_tokens}
    # with a 400; MAGI_MODEL_THINKING_TYPE=adaptive sends the adaptive shape
    # directly and takes precedence over budget/effort.
    from magi_agent.cli.real_runner import _model_reasoning_kwargs

    kw = _model_reasoning_kwargs({"MAGI_MODEL_THINKING_TYPE": "adaptive"})
    assert kw == {"thinking": {"type": "adaptive"}}

    kw = _model_reasoning_kwargs(
        {
            "MAGI_MODEL_THINKING_TYPE": "adaptive",
            "MAGI_MODEL_THINKING_BUDGET_TOKENS": "8192",
            "MAGI_MODEL_REASONING_EFFORT": "high",
        }
    )
    assert kw == {"thinking": {"type": "adaptive"}}

    # Unknown/empty type values fall through to the other knobs.
    kw = _model_reasoning_kwargs(
        {"MAGI_MODEL_THINKING_TYPE": "", "MAGI_MODEL_REASONING_EFFORT": "high"}
    )
    assert kw == {"reasoning_effort": "high"}


def test_reasoning_effort_max_maps_to_xhigh_for_openai():
    # OpenAI rejects reasoning_effort="max" with a 400 — supported values are
    # none/low/medium/high/xhigh. Map "max" -> "xhigh" (the strongest OpenAI
    # value) so the lab-overlay default (MAGI_MODEL_REASONING_EFFORT=max) does
    # not silently break GPT-5 turns with a BadRequest after 4 retries.
    kw = _model_reasoning_kwargs(
        {"MAGI_MODEL_REASONING_EFFORT": "max"}, provider="openai"
    )
    assert kw == {"reasoning_effort": "xhigh"}

    # Same normalization for openrouter (also proxies OpenAI's API).
    kw = _model_reasoning_kwargs(
        {"MAGI_MODEL_REASONING_EFFORT": "max"}, provider="openrouter"
    )
    assert kw == {"reasoning_effort": "xhigh"}


def test_reasoning_effort_max_maps_to_high_for_gemini():
    # Gemini also rejects "max" ("Invalid reasoning effort: max"); map to
    # "high" (the strongest Gemini-accepted value short of provider-specific
    # adaptive shapes that don't fit the cross-provider effort knob).
    kw = _model_reasoning_kwargs(
        {"MAGI_MODEL_REASONING_EFFORT": "max"}, provider="gemini"
    )
    assert kw == {"reasoning_effort": "high"}


def test_reasoning_effort_max_passes_through_for_anthropic():
    # Anthropic IS the provider that accepts "max" (litellm maps it to adaptive
    # thinking). Keep it byte-identical for that provider.
    kw = _model_reasoning_kwargs(
        {"MAGI_MODEL_REASONING_EFFORT": "max"}, provider="anthropic"
    )
    assert kw == {"reasoning_effort": "max"}


def test_reasoning_kwargs_provider_param_is_optional():
    # Backward compatibility: callers that don't pass `provider` keep the
    # historical pass-through behavior.
    assert _model_reasoning_kwargs({"MAGI_MODEL_REASONING_EFFORT": "max"}) == {
        "reasoning_effort": "max"
    }


def test_reasoning_effort_dropped_entirely_for_fireworks():
    # Fireworks rejects `reasoning_effort` for ANY value
    # ("litellm.UnsupportedParamsError: fireworks_ai does not support
    # parameters: ['reasoning_effort'], for model=kimi-k2p6. LiteLLM Retried:
    # 4 times"). Unlike OpenAI/Gemini which accept a normalized value, fireworks
    # doesn't accept the parameter at all — drop it entirely so the lab-overlay
    # default (max) and the per-turn picker values (medium/high/etc) don't
    # silently break every Kimi turn with "no final answer text arrived".
    for effort in ("max", "high", "medium", "low", "minimal"):
        assert _model_reasoning_kwargs(
            {"MAGI_MODEL_REASONING_EFFORT": effort}, provider="fireworks"
        ) == {}, effort


def test_reasoning_effort_dropped_entirely_for_fireworks_does_not_affect_thinking_blocks():
    # Adaptive-thinking (MAGI_MODEL_THINKING_TYPE) is provider-independent on
    # the litellm side; fireworks isn't bypassed for thinking, just for the
    # `reasoning_effort` knob it rejects.
    kw = _model_reasoning_kwargs(
        {
            "MAGI_MODEL_THINKING_TYPE": "adaptive",
            "MAGI_MODEL_REASONING_EFFORT": "max",
        },
        provider="fireworks",
    )
    assert kw == {"thinking": {"type": "adaptive"}}
