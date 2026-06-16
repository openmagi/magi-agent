"""Tests for magi_agent.runtime.usage_cost.compute_cost_usd.

litellm is a runtime dependency but is NOT importable in the test environment,
so the real pricing path is exercised via an injected ``cost_per_token`` fake
(matching litellm's signature) and, when litellm happens to be present, an
``importorskip``-guarded integration test.
"""

from __future__ import annotations

import pytest

import magi_agent.runtime.usage_cost as usage_cost_mod
from magi_agent.runtime.usage_cost import compute_cost_usd


def _fake_pricer(*, model, prompt_tokens, completion_tokens):
    # $1 / 1k prompt, $2 / 1k completion — deterministic, model-agnostic.
    if model == "unknown":
        raise ValueError("unmapped model")
    return (prompt_tokens / 1000.0, completion_tokens / 1000.0 * 2)


def test_known_model_with_tokens_is_priced_positive():
    cost = compute_cost_usd(
        "claude-sonnet-4-5",
        {"input_tokens": 1000, "output_tokens": 500},
        cost_per_token=_fake_pricer,
    )
    assert cost == pytest.approx(1.0 + 1.0)


def test_unknown_model_is_unpriced_zero():
    cost = compute_cost_usd(
        "unknown",
        {"input_tokens": 1000, "output_tokens": 500},
        cost_per_token=_fake_pricer,
    )
    assert cost == 0.0


def test_missing_litellm_is_zero(monkeypatch):
    # When litellm cannot be resolved, the default path is unpriced (never raises).
    # Patch the module object directly: the magi_agent.runtime package is a lazy
    # __getattr__ boundary, so a dotted-string monkeypatch target would fail to
    # resolve the (non-lazy-exported) usage_cost submodule.
    monkeypatch.setattr(usage_cost_mod, "_litellm_cost_per_token", lambda: None)
    cost = compute_cost_usd(
        "claude-sonnet-4-5",
        {"input_tokens": 1000, "output_tokens": 500},
    )
    assert cost == 0.0


def test_no_model_is_zero():
    assert compute_cost_usd(None, {"input_tokens": 1}, cost_per_token=_fake_pricer) == 0.0
    assert compute_cost_usd("", {"input_tokens": 1}, cost_per_token=_fake_pricer) == 0.0


def test_no_usage_is_zero():
    assert compute_cost_usd("m", None, cost_per_token=_fake_pricer) == 0.0
    assert compute_cost_usd("m", {}, cost_per_token=_fake_pricer) == 0.0


def test_zero_tokens_is_zero():
    cost = compute_cost_usd(
        "m", {"input_tokens": 0, "output_tokens": 0}, cost_per_token=_fake_pricer
    )
    assert cost == 0.0


def test_negative_or_bool_token_counts_are_ignored():
    # bool is a subclass of int; must not be treated as a token count.
    assert compute_cost_usd("m", {"input_tokens": True}, cost_per_token=_fake_pricer) == 0.0
    assert compute_cost_usd("m", {"input_tokens": -5}, cost_per_token=_fake_pricer) == 0.0


def test_input_only_is_priced():
    cost = compute_cost_usd("m", {"input_tokens": 1000}, cost_per_token=_fake_pricer)
    assert cost == pytest.approx(1.0)


def test_real_litellm_when_available():
    litellm = pytest.importorskip("litellm")
    cost = compute_cost_usd(
        "claude-sonnet-4-5",
        {"input_tokens": 1000, "output_tokens": 500},
        cost_per_token=litellm.cost_per_token,
    )
    assert cost > 0.0
