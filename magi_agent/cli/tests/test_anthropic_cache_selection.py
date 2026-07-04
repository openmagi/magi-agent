"""Tests for _maybe_build_cache_aware_anthropic in real_runner — Task 1.

E-7 refactor (2026-06-22): the actual cache-aware decision now lives in
``runtime/model_factory.maybe_build_cache_aware_anthropic`` and the
real_runner function is a thin shim. The patch targets here are the
factory's bindings (``model_factory.build_cache_aware_claude`` and
``model_factory.is_message_cache_enabled``); the legacy
``real_runner.build_cache_aware_claude``/``is_message_cache_enabled``
imports are still present but no longer the call site.
"""

from __future__ import annotations

import pytest
from magi_agent.cli import real_runner
from magi_agent.cli.providers import ProviderConfig
from magi_agent.runtime import model_factory


_SENTINEL = object()


@pytest.fixture
def cache_on(monkeypatch):
    monkeypatch.setattr(
        model_factory, "is_message_cache_enabled", lambda env=None: True, raising=False
    )


def _fake_build(monkeypatch, *, raises=None):
    calls = {}

    def _build(model):
        calls["model"] = model
        if raises is not None:
            raise raises
        return _SENTINEL

    monkeypatch.setattr(
        model_factory, "build_cache_aware_claude", _build, raising=False
    )
    return calls


def _cfg(provider="anthropic", model="claude-sonnet-4-6", api_key="sk-test"):
    return ProviderConfig(provider=provider, model=model, api_key=api_key)


def test_anthropic_cache_on_no_base_returns_cache_aware(monkeypatch, cache_on):
    calls = _fake_build(monkeypatch)
    monkeypatch.setattr(real_runner, "_model_api_base_kwargs", lambda env=None: {})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = real_runner._maybe_build_cache_aware_anthropic(_cfg(), env={})
    assert out is _SENTINEL
    assert calls["model"] == "claude-sonnet-4-6"  # bare id, not prefixed


def test_non_anthropic_returns_none(monkeypatch, cache_on):
    _fake_build(monkeypatch)
    monkeypatch.setattr(real_runner, "_model_api_base_kwargs", lambda env=None: {})
    assert real_runner._maybe_build_cache_aware_anthropic(_cfg(provider="openai"), env={}) is None


def test_cache_off_returns_none(monkeypatch):
    # The CLI seam builds the cache-aware model when EITHER the message-cache OR
    # the prompt-cache flag is on (model_factory.maybe_build_cache_aware_anthropic:
    # "either flag ON builds the cache-aware model"). ``MAGI_PROMPT_CACHE_ENABLED``
    # is now a profile-aware default-ON flag, so an env with only the message cache
    # patched off still leaves prompt-cache ON under the default profile. To test
    # the genuine "all caches off -> None" contract, disable prompt caching
    # explicitly here (message cache is already forced off via the monkeypatch).
    monkeypatch.setattr(
        model_factory, "is_message_cache_enabled", lambda env=None: False, raising=False
    )
    _fake_build(monkeypatch)
    monkeypatch.setattr(real_runner, "_model_api_base_kwargs", lambda env=None: {})
    assert (
        real_runner._maybe_build_cache_aware_anthropic(
            _cfg(), env={"MAGI_PROMPT_CACHE_ENABLED": "0"}
        )
        is None
    )


def test_custom_base_returns_none(monkeypatch, cache_on):
    _fake_build(monkeypatch)
    monkeypatch.setattr(real_runner, "_model_api_base_kwargs", lambda env=None: {"api_base": "https://proxy.example"})
    assert real_runner._maybe_build_cache_aware_anthropic(_cfg(), env={}) is None


def test_build_raises_returns_none(monkeypatch, cache_on):
    _fake_build(monkeypatch, raises=ModuleNotFoundError("anthropic"))
    monkeypatch.setattr(real_runner, "_model_api_base_kwargs", lambda env=None: {})
    assert real_runner._maybe_build_cache_aware_anthropic(_cfg(), env={}) is None
    # any other exception also falls back
    _fake_build(monkeypatch, raises=RuntimeError("boom"))
    assert real_runner._maybe_build_cache_aware_anthropic(_cfg(), env={}) is None


def test_credential_set_when_absent_not_overwritten(monkeypatch, cache_on):
    _fake_build(monkeypatch)
    monkeypatch.setattr(real_runner, "_model_api_base_kwargs", lambda env=None: {})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    real_runner._maybe_build_cache_aware_anthropic(_cfg(api_key="sk-fromconfig"), env={})
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-fromconfig"
    # do not overwrite an existing value
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-preset")
    real_runner._maybe_build_cache_aware_anthropic(_cfg(api_key="sk-fromconfig"), env={})
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-preset"


def test_build_litellm_model_uses_cache_aware_for_anthropic(monkeypatch, cache_on):
    calls = _fake_build(monkeypatch)
    monkeypatch.setattr(real_runner, "_model_api_base_kwargs", lambda env=None: {})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = real_runner._build_litellm_model(_cfg(), env={})
    assert out is _SENTINEL  # cache-aware model chosen over LiteLlm
