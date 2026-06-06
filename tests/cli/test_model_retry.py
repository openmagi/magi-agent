"""The CLI's real model should retry transient provider errors.

A SWE-bench inference run died on a transient
``litellm.InternalServerError: Server disconnected`` with no retry, losing the
instance. LiteLlm forwards ``num_retries``/``timeout`` to litellm, which retries
retryable provider errors (5xx / connection drops / overloaded).
"""
from __future__ import annotations

from magi_agent.cli.providers import ProviderConfig
from magi_agent.cli.real_runner import _build_litellm_model, _model_retry_kwargs


def _cfg() -> ProviderConfig:
    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-5", api_key="x")


def test_litellm_model_built_with_retries_and_timeout():
    model = _build_litellm_model(_cfg())
    extra = getattr(model, "_additional_args", {}) or {}
    assert extra.get("num_retries", 0) >= 1
    assert extra.get("timeout")


def test_retry_kwargs_defaults():
    kw = _model_retry_kwargs({})
    assert kw["num_retries"] >= 1
    assert kw["timeout"] >= 1


def test_retry_kwargs_env_override():
    kw = _model_retry_kwargs({"MAGI_MODEL_NUM_RETRIES": "7", "MAGI_MODEL_TIMEOUT_S": "123"})
    assert kw["num_retries"] == 7
    assert kw["timeout"] == 123


def test_retry_kwargs_ignores_garbage():
    kw = _model_retry_kwargs({"MAGI_MODEL_NUM_RETRIES": "nope", "MAGI_MODEL_TIMEOUT_S": "-5"})
    assert kw["num_retries"] >= 1  # falls back to default
    assert kw["timeout"] >= 1  # negative clamped to default
