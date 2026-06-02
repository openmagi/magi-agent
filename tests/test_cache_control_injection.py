"""Tests for cache control injection: provider detection, strategies, and injector.

TDD approach: tests written before implementation (PR 3 of prompt-cache track).
PR 1 created prompt/types.py with PromptBlock.  This PR uses PromptBlock as
input and produces provider-formatted output dicts.
"""

from __future__ import annotations

import importlib
from types import ModuleType


# ---------------------------------------------------------------------------
# Lazy module helpers — fail with a clear message if the module is missing
# ---------------------------------------------------------------------------


def _injection_module() -> ModuleType:
    try:
        return importlib.import_module("magi_agent.prompt.injection")
    except ModuleNotFoundError as exc:
        import pytest

        pytest.fail(f"magi_agent.prompt.injection module is missing: {exc}")


def _providers_module() -> ModuleType:
    try:
        return importlib.import_module("magi_agent.prompt.providers")
    except ModuleNotFoundError as exc:
        import pytest

        pytest.fail(f"magi_agent.prompt.providers module is missing: {exc}")


def _prompt_module() -> ModuleType:
    try:
        return importlib.import_module("magi_agent.prompt")
    except ModuleNotFoundError as exc:
        import pytest

        pytest.fail(f"magi_agent.prompt module is missing: {exc}")


def _make_block(text: str, cache_scope: str | None):  # type: ignore[return]
    """Create a PromptBlock using the live types module."""
    types = importlib.import_module("magi_agent.prompt.types")
    return types.PromptBlock(text=text, cache_scope=cache_scope)


# ---------------------------------------------------------------------------
# Provider detection — detect_provider(model) -> str
# ---------------------------------------------------------------------------


class TestDetectProvider:
    def test_claude_sonnet_4_6_returns_anthropic(self) -> None:
        detect = _injection_module().detect_provider
        assert detect("claude-sonnet-4-6") == "anthropic"

    def test_anthropic_prefixed_model_returns_anthropic(self) -> None:
        detect = _injection_module().detect_provider
        assert detect("anthropic/claude-opus-4-6") == "anthropic"

    def test_gpt_5_5_returns_openai(self) -> None:
        detect = _injection_module().detect_provider
        assert detect("gpt-5.5") == "openai"

    def test_openai_prefixed_model_returns_openai(self) -> None:
        detect = _injection_module().detect_provider
        assert detect("openai/gpt-5.5") == "openai"

    def test_gemini_3_5_flash_returns_google(self) -> None:
        detect = _injection_module().detect_provider
        assert detect("gemini-3.5-flash") == "google"

    def test_google_prefixed_model_returns_google(self) -> None:
        detect = _injection_module().detect_provider
        assert detect("google/gemini-3.1-pro-preview") == "google"

    def test_kimi_k2p5_returns_unknown(self) -> None:
        detect = _injection_module().detect_provider
        assert detect("kimi-k2.5") == "unknown"

    def test_magi_smart_router_returns_unknown(self) -> None:
        detect = _injection_module().detect_provider
        assert detect("magi-smart-router/auto") == "unknown"

    def test_fireworks_kimi_returns_unknown(self) -> None:
        detect = _injection_module().detect_provider
        assert detect("fireworks/kimi-k2p6") == "unknown"

    def test_contains_claude_returns_anthropic(self) -> None:
        """Fallback: model string containing 'claude' maps to anthropic."""
        detect = _injection_module().detect_provider
        assert detect("some-router/claude-flex") == "anthropic"

    def test_contains_gpt_returns_openai(self) -> None:
        """Fallback: model string containing 'gpt' maps to openai."""
        detect = _injection_module().detect_provider
        assert detect("some-router/gpt-legacy") == "openai"

    def test_contains_gemini_returns_google(self) -> None:
        """Fallback: model string containing 'gemini' maps to google."""
        detect = _injection_module().detect_provider
        assert detect("some-router/gemini-v1") == "google"

    def test_detection_is_case_insensitive(self) -> None:
        detect = _injection_module().detect_provider
        assert detect("Claude-Sonnet-4-6") == "anthropic"
        assert detect("GPT-5.5") == "openai"
        assert detect("Gemini-3.5-Flash") == "google"


# ---------------------------------------------------------------------------
# AnthropicCacheStrategy
# ---------------------------------------------------------------------------


class TestAnthropicCacheStrategy:
    def test_static_block_gets_cache_control_marker(self) -> None:
        providers = _providers_module()
        strategy = providers.AnthropicCacheStrategy()
        block = {"type": "text", "text": "hello"}
        result = strategy.apply_cache_control(block, cache_scope="global")
        assert "cache_control" in result

    def test_cache_control_format_is_ephemeral(self) -> None:
        providers = _providers_module()
        strategy = providers.AnthropicCacheStrategy()
        block = {"type": "text", "text": "hello"}
        result = strategy.apply_cache_control(block, cache_scope="global")
        assert result["cache_control"] == {"type": "ephemeral"}

    def test_returns_copy_not_original(self) -> None:
        providers = _providers_module()
        strategy = providers.AnthropicCacheStrategy()
        block = {"type": "text", "text": "hello"}
        result = strategy.apply_cache_control(block, cache_scope="global")
        assert result is not block

    def test_original_block_text_preserved(self) -> None:
        providers = _providers_module()
        strategy = providers.AnthropicCacheStrategy()
        block = {"type": "text", "text": "static content"}
        result = strategy.apply_cache_control(block, cache_scope="global")
        assert result["text"] == "static content"
        assert result["type"] == "text"

    def test_org_scope_still_adds_ephemeral_marker(self) -> None:
        """Anthropic API doesn't support scope, but marker is always added."""
        providers = _providers_module()
        strategy = providers.AnthropicCacheStrategy()
        block = {"type": "text", "text": "org content"}
        result = strategy.apply_cache_control(block, cache_scope="org")
        assert result["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# OpenAICacheStrategy
# ---------------------------------------------------------------------------


class TestOpenAICacheStrategy:
    def test_static_block_has_no_cache_control_added(self) -> None:
        providers = _providers_module()
        strategy = providers.OpenAICacheStrategy()
        block = {"type": "text", "text": "hello"}
        result = strategy.apply_cache_control(block, cache_scope="global")
        assert "cache_control" not in result

    def test_returns_copy_not_original(self) -> None:
        providers = _providers_module()
        strategy = providers.OpenAICacheStrategy()
        block = {"type": "text", "text": "hello"}
        result = strategy.apply_cache_control(block, cache_scope="global")
        assert result is not block

    def test_text_and_type_preserved(self) -> None:
        providers = _providers_module()
        strategy = providers.OpenAICacheStrategy()
        block = {"type": "text", "text": "openai content"}
        result = strategy.apply_cache_control(block, cache_scope="global")
        assert result["text"] == "openai content"
        assert result["type"] == "text"


# ---------------------------------------------------------------------------
# GoogleCacheStrategy
# ---------------------------------------------------------------------------


class TestGoogleCacheStrategy:
    def test_static_block_has_no_cache_control_added(self) -> None:
        providers = _providers_module()
        strategy = providers.GoogleCacheStrategy()
        block = {"type": "text", "text": "hello"}
        result = strategy.apply_cache_control(block, cache_scope="global")
        assert "cache_control" not in result

    def test_returns_copy_not_original(self) -> None:
        providers = _providers_module()
        strategy = providers.GoogleCacheStrategy()
        block = {"type": "text", "text": "hello"}
        result = strategy.apply_cache_control(block, cache_scope="global")
        assert result is not block

    def test_text_and_type_preserved(self) -> None:
        providers = _providers_module()
        strategy = providers.GoogleCacheStrategy()
        block = {"type": "text", "text": "google content"}
        result = strategy.apply_cache_control(block, cache_scope="global")
        assert result["text"] == "google content"
        assert result["type"] == "text"


# ---------------------------------------------------------------------------
# get_strategy helper
# ---------------------------------------------------------------------------


class TestGetStrategy:
    def test_anthropic_returns_anthropic_strategy(self) -> None:
        providers = _providers_module()
        strategy = providers.get_strategy("anthropic")
        assert isinstance(strategy, providers.AnthropicCacheStrategy)

    def test_openai_returns_openai_strategy(self) -> None:
        providers = _providers_module()
        strategy = providers.get_strategy("openai")
        assert isinstance(strategy, providers.OpenAICacheStrategy)

    def test_google_returns_google_strategy(self) -> None:
        providers = _providers_module()
        strategy = providers.get_strategy("google")
        assert isinstance(strategy, providers.GoogleCacheStrategy)

    def test_unknown_provider_returns_noop_strategy(self) -> None:
        """Unknown providers default to no-op (OpenAI-style) strategy."""
        providers = _providers_module()
        strategy = providers.get_strategy("unknown")
        # Must not add cache_control (no-op)
        block = {"type": "text", "text": "x"}
        result = strategy.apply_cache_control(block, "global")
        assert "cache_control" not in result


# ---------------------------------------------------------------------------
# CacheControlInjector — integration tests
# ---------------------------------------------------------------------------


class TestCacheControlInjector:
    def test_auto_detect_with_claude_model_applies_anthropic_markers(self) -> None:
        injection = _injection_module()
        injector = injection.CacheControlInjector(provider="auto", model="claude-sonnet-4-6")
        blocks = (
            _make_block("static block", cache_scope="global"),
        )
        result = injector.inject(blocks)
        assert len(result) == 1
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_auto_detect_with_gpt_model_applies_no_markers(self) -> None:
        injection = _injection_module()
        injector = injection.CacheControlInjector(provider="auto", model="gpt-5.5")
        blocks = (
            _make_block("static block", cache_scope="global"),
        )
        result = injector.inject(blocks)
        assert len(result) == 1
        assert "cache_control" not in result[0]

    def test_explicit_provider_override_ignores_model(self) -> None:
        """Explicit provider='anthropic' takes precedence regardless of model."""
        injection = _injection_module()
        injector = injection.CacheControlInjector(provider="anthropic", model="gpt-5.5")
        blocks = (
            _make_block("static", cache_scope="global"),
        )
        result = injector.inject(blocks)
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_mixed_blocks_only_static_gets_cache_control(self) -> None:
        """Dynamic blocks (cache_scope=None) must never get cache_control."""
        injection = _injection_module()
        injector = injection.CacheControlInjector(provider="anthropic")
        blocks = (
            _make_block("static content", cache_scope="global"),
            _make_block("dynamic content", cache_scope=None),
            _make_block("also static", cache_scope="global"),
        )
        result = injector.inject(blocks)
        assert len(result) == 3
        assert result[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in result[1]
        assert result[2]["cache_control"] == {"type": "ephemeral"}

    def test_inject_empty_blocks_returns_empty_list(self) -> None:
        injection = _injection_module()
        injector = injection.CacheControlInjector(provider="anthropic")
        assert injector.inject(()) == []

    def test_inject_preserves_text_content(self) -> None:
        injection = _injection_module()
        injector = injection.CacheControlInjector(provider="openai", model="gpt-5.5")
        blocks = (
            _make_block("hello world", cache_scope="global"),
        )
        result = injector.inject(blocks)
        assert result[0]["text"] == "hello world"
        assert result[0]["type"] == "text"

    def test_resolved_provider_attribute_reflects_auto_detection(self) -> None:
        injection = _injection_module()
        injector = injection.CacheControlInjector(provider="auto", model="claude-opus-4-6")
        assert injector.resolved_provider == "anthropic"

    def test_resolved_provider_explicit_override(self) -> None:
        injection = _injection_module()
        injector = injection.CacheControlInjector(provider="google", model="gpt-5.5")
        assert injector.resolved_provider == "google"

    def test_no_model_defaults_to_unknown_provider(self) -> None:
        """provider='auto' with empty model → unknown → no-op strategy."""
        injection = _injection_module()
        injector = injection.CacheControlInjector(provider="auto", model="")
        blocks = (
            _make_block("static", cache_scope="global"),
        )
        result = injector.inject(blocks)
        # unknown provider: no-op, no cache_control
        assert "cache_control" not in result[0]

    def test_all_dynamic_blocks_with_anthropic_no_markers_added(self) -> None:
        """Even Anthropic strategy: dynamic blocks must not get cache_control."""
        injection = _injection_module()
        injector = injection.CacheControlInjector(provider="anthropic")
        blocks = (
            _make_block("turn-specific header", cache_scope=None),
            _make_block("timestamp", cache_scope=None),
        )
        result = injector.inject(blocks)
        for block in result:
            assert "cache_control" not in block


# ---------------------------------------------------------------------------
# Public __init__ re-exports
# ---------------------------------------------------------------------------


class TestPromptPackageExports:
    def test_cache_control_injector_exported(self) -> None:
        prompt = _prompt_module()
        assert hasattr(prompt, "CacheControlInjector")

    def test_detect_provider_exported(self) -> None:
        prompt = _prompt_module()
        assert hasattr(prompt, "detect_provider")

    def test_provider_cache_strategy_exported(self) -> None:
        prompt = _prompt_module()
        assert hasattr(prompt, "ProviderCacheStrategy")

    def test_anthropic_cache_strategy_exported(self) -> None:
        prompt = _prompt_module()
        assert hasattr(prompt, "AnthropicCacheStrategy")

    def test_openai_cache_strategy_exported(self) -> None:
        prompt = _prompt_module()
        assert hasattr(prompt, "OpenAICacheStrategy")

    def test_google_cache_strategy_exported(self) -> None:
        prompt = _prompt_module()
        assert hasattr(prompt, "GoogleCacheStrategy")

    def test_existing_exports_still_present(self) -> None:
        """PR 3 must not break PR 1 exports."""
        prompt = _prompt_module()
        assert hasattr(prompt, "PromptBlock")
        assert hasattr(prompt, "PromptCacheConfig")
        assert hasattr(prompt, "PromptSplitResult")
        assert hasattr(prompt, "split_system_prompt")
