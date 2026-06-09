"""Tests for model-aware prompt adaptation (Track 14)."""

from __future__ import annotations

import pytest

from magi_agent.prompt.provider_adapter import (
    AnthropicAdapter,
    DefaultAdapter,
    GoogleAdapter,
    OpenAIAdapter,
    PromptRoutingConfig,
    ProviderFamily,
    adapt_identity_sections,
    detect_provider_family,
    get_adapter,
)


# ---------------------------------------------------------------------------
# detect_provider_family
# ---------------------------------------------------------------------------


class TestDetectProviderFamily:
    def test_claude_models(self) -> None:
        assert detect_provider_family("claude-sonnet-4-6") == ProviderFamily.ANTHROPIC
        assert detect_provider_family("claude-opus-4-6") == ProviderFamily.ANTHROPIC
        assert detect_provider_family("claude-haiku-4-5-20251001") == ProviderFamily.ANTHROPIC

    def test_anthropic_prefixed(self) -> None:
        assert detect_provider_family("anthropic/claude-sonnet-4-6") == ProviderFamily.ANTHROPIC

    def test_gpt_models(self) -> None:
        assert detect_provider_family("gpt-5.4") == ProviderFamily.OPENAI
        assert detect_provider_family("gpt-5.5") == ProviderFamily.OPENAI
        assert detect_provider_family("gpt-5.4-nano") == ProviderFamily.OPENAI

    def test_openai_prefixed(self) -> None:
        assert detect_provider_family("openai/gpt-5.5") == ProviderFamily.OPENAI
        assert detect_provider_family("openai-codex/gpt-5.5") == ProviderFamily.OPENAI

    def test_gemini_models(self) -> None:
        assert detect_provider_family("gemini-3.5-flash") == ProviderFamily.GOOGLE
        assert detect_provider_family("gemini-3.1-pro-preview") == ProviderFamily.GOOGLE

    def test_google_prefixed(self) -> None:
        assert detect_provider_family("google/gemini-3.5-flash") == ProviderFamily.GOOGLE

    def test_fireworks_models(self) -> None:
        assert detect_provider_family("fireworks/kimi-k2p6") == ProviderFamily.FIREWORKS
        assert detect_provider_family("kimi-k2p6") == ProviderFamily.FIREWORKS
        assert detect_provider_family("minimax-m2p7") == ProviderFamily.FIREWORKS

    def test_unknown_model_returns_default(self) -> None:
        assert detect_provider_family("some-custom-model") == ProviderFamily.DEFAULT
        assert detect_provider_family("local/gemma-fast") == ProviderFamily.DEFAULT

    def test_empty_string(self) -> None:
        assert detect_provider_family("") == ProviderFamily.DEFAULT

    def test_router_wrapped_claude(self) -> None:
        assert detect_provider_family("big-dic-router/claude-flex") == ProviderFamily.ANTHROPIC

    def test_router_wrapped_gpt(self) -> None:
        assert detect_provider_family("smart-router/gpt-5.5") == ProviderFamily.OPENAI


# ---------------------------------------------------------------------------
# get_adapter
# ---------------------------------------------------------------------------


class TestGetAdapter:
    def test_anthropic_returns_anthropic_adapter(self) -> None:
        adapter = get_adapter(ProviderFamily.ANTHROPIC)
        assert isinstance(adapter, AnthropicAdapter)

    def test_openai_returns_openai_adapter(self) -> None:
        adapter = get_adapter(ProviderFamily.OPENAI)
        assert isinstance(adapter, OpenAIAdapter)

    def test_google_returns_google_adapter(self) -> None:
        adapter = get_adapter(ProviderFamily.GOOGLE)
        assert isinstance(adapter, GoogleAdapter)

    def test_default_returns_default_adapter(self) -> None:
        adapter = get_adapter(ProviderFamily.DEFAULT)
        assert isinstance(adapter, DefaultAdapter)

    def test_fireworks_returns_default_adapter(self) -> None:
        adapter = get_adapter(ProviderFamily.FIREWORKS)
        assert isinstance(adapter, DefaultAdapter)

    def test_openai_adapter_accepts_config(self) -> None:
        config = PromptRoutingConfig(openai_compression_ratio=0.5)
        adapter = get_adapter(ProviderFamily.OPENAI, config)
        assert isinstance(adapter, OpenAIAdapter)


# ---------------------------------------------------------------------------
# AnthropicAdapter
# ---------------------------------------------------------------------------


class TestAnthropicAdapter:
    def test_passthrough(self) -> None:
        sections = ["# SOUL\n\nYou are helpful.", "# TOOLS\n\n<tool>bash</tool>"]
        adapter = AnthropicAdapter()
        result = adapter.adapt_sections(sections)
        assert result == sections

    def test_preserves_xml_tags(self) -> None:
        sections = ["<rules>Be safe</rules>"]
        result = AnthropicAdapter().adapt_sections(sections)
        assert "<rules>" in result[0]

    def test_provider_property(self) -> None:
        assert AnthropicAdapter().provider == ProviderFamily.ANTHROPIC

    def test_no_adaptations(self) -> None:
        assert AnthropicAdapter().adaptations_applied == ()

    def test_returns_copy(self) -> None:
        sections = ["section1"]
        result = AnthropicAdapter().adapt_sections(sections)
        assert result is not sections


# ---------------------------------------------------------------------------
# OpenAIAdapter
# ---------------------------------------------------------------------------


class TestOpenAIAdapter:
    def test_strips_xml_tags(self) -> None:
        sections = ["<rules>Be safe</rules>", "Plain text here"]
        result = OpenAIAdapter().adapt_sections(sections)
        for section in result:
            assert "<rules>" not in section
            assert "</rules>" not in section

    def test_output_shorter_than_input(self) -> None:
        sections = [
            "# SOUL\n\n<identity>You are a helpful assistant.</identity>\n\n<rules>\nBe concise.\nBe accurate.\n</rules>",
            "# TOOLS\n\n<tool-list>\nbash\npython\n</tool-list>",
        ]
        original_len = sum(len(s) for s in sections)
        result = OpenAIAdapter().adapt_sections(sections)
        result_len = sum(len(s) for s in result)
        assert result_len < original_len

    def test_preserves_content_words(self) -> None:
        sections = ["<rules>Be safe and helpful</rules>"]
        result = OpenAIAdapter().adapt_sections(sections)
        combined = " ".join(result)
        assert "safe" in combined
        assert "helpful" in combined

    def test_merges_short_sections(self) -> None:
        sections = ["Short A", "Short B", "Short C"]
        result = OpenAIAdapter().adapt_sections(sections)
        assert len(result) < len(sections)

    def test_does_not_merge_long_sections(self) -> None:
        long = "x" * 300
        sections = [long, long]
        result = OpenAIAdapter().adapt_sections(sections)
        assert len(result) == 2

    def test_removes_empty_after_strip(self) -> None:
        sections = ["<tag></tag>", "Real content"]
        result = OpenAIAdapter().adapt_sections(sections)
        for section in result:
            assert section.strip()

    def test_provider_property(self) -> None:
        assert OpenAIAdapter().provider == ProviderFamily.OPENAI

    def test_adaptations_applied(self) -> None:
        adaptations = OpenAIAdapter().adaptations_applied
        assert "strip_xml_tags" in adaptations
        assert "merge_short_sections" in adaptations


# ---------------------------------------------------------------------------
# GoogleAdapter
# ---------------------------------------------------------------------------


class TestGoogleAdapter:
    def test_normalizes_whitespace(self) -> None:
        sections = ["Line 1\n\n\n\n\nLine 2"]
        result = GoogleAdapter().adapt_sections(sections)
        assert "\n\n\n" not in result[0]
        assert "Line 1" in result[0]
        assert "Line 2" in result[0]

    def test_preserves_xml_tags(self) -> None:
        sections = ["<rules>Keep this</rules>"]
        result = GoogleAdapter().adapt_sections(sections)
        assert "<rules>" in result[0]

    def test_removes_empty_sections(self) -> None:
        sections = ["content", "", "  ", "more content"]
        result = GoogleAdapter().adapt_sections(sections)
        assert len(result) == 2

    def test_provider_property(self) -> None:
        assert GoogleAdapter().provider == ProviderFamily.GOOGLE


# ---------------------------------------------------------------------------
# DefaultAdapter
# ---------------------------------------------------------------------------


class TestDefaultAdapter:
    def test_passthrough(self) -> None:
        sections = ["anything", "<xml>tags</xml>", ""]
        result = DefaultAdapter().adapt_sections(sections)
        assert result == sections

    def test_provider_property(self) -> None:
        assert DefaultAdapter().provider == ProviderFamily.DEFAULT

    def test_no_adaptations(self) -> None:
        assert DefaultAdapter().adaptations_applied == ()


# ---------------------------------------------------------------------------
# adapt_identity_sections (convenience function)
# ---------------------------------------------------------------------------


class TestAdaptIdentitySections:
    def test_claude_uses_anthropic_adapter(self) -> None:
        sections = ["<rules>Be safe</rules>"]
        result, adapter = adapt_identity_sections(sections, model="claude-sonnet-4-6")
        assert isinstance(adapter, AnthropicAdapter)
        assert "<rules>" in result[0]

    def test_gpt_uses_openai_adapter(self) -> None:
        sections = ["<rules>Be safe</rules>"]
        result, adapter = adapt_identity_sections(sections, model="gpt-5.5")
        assert isinstance(adapter, OpenAIAdapter)
        assert "<rules>" not in result[0]

    def test_gemini_uses_google_adapter(self) -> None:
        sections = ["content\n\n\n\n\nmore"]
        result, adapter = adapt_identity_sections(sections, model="gemini-3.5-flash")
        assert isinstance(adapter, GoogleAdapter)
        assert "\n\n\n" not in result[0]

    def test_unknown_model_uses_default(self) -> None:
        sections = ["anything"]
        result, adapter = adapt_identity_sections(sections, model="custom-model")
        assert isinstance(adapter, DefaultAdapter)
        assert result == sections

    def test_empty_sections(self) -> None:
        result, _ = adapt_identity_sections([], model="gpt-5.5")
        assert result == []


# ---------------------------------------------------------------------------
# PromptRoutingConfig
# ---------------------------------------------------------------------------


class TestPromptRoutingConfig:
    def test_defaults(self) -> None:
        config = PromptRoutingConfig()
        assert config.enabled is False
        assert config.openai_compression_ratio == 0.6
        assert config.merge_short_section_threshold == 200

    def test_frozen(self) -> None:
        config = PromptRoutingConfig()
        with pytest.raises(AttributeError):
            config.enabled = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProviderFamily enum
# ---------------------------------------------------------------------------


class TestProviderFamily:
    def test_string_values(self) -> None:
        assert ProviderFamily.ANTHROPIC.value == "anthropic"
        assert ProviderFamily.OPENAI.value == "openai"
        assert ProviderFamily.GOOGLE.value == "google"
        assert ProviderFamily.FIREWORKS.value == "fireworks"
        assert ProviderFamily.DEFAULT.value == "default"

    def test_is_str(self) -> None:
        assert isinstance(ProviderFamily.ANTHROPIC, str)


# ---------------------------------------------------------------------------
# Integration: build_system_prompt with model-aware prompts
# ---------------------------------------------------------------------------


class TestBuildSystemPromptIntegration:
    def test_disabled_by_default(self) -> None:
        from magi_agent.runtime.message_builder import build_system_prompt

        identity = {"soul": "<rules>Be safe</rules>"}
        prompt = build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=identity,
            model="gpt-5.5",
        )
        assert "<rules>" in prompt

    def test_enabled_strips_xml_for_gpt(self) -> None:
        from magi_agent.runtime.message_builder import build_system_prompt

        identity = {"soul": "<rules>Be safe</rules>"}
        prompt = build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=identity,
            model="gpt-5.5",
            model_aware_prompts_enabled=True,
        )
        assert "<rules>" not in prompt
        assert "Be safe" in prompt

    def test_enabled_preserves_xml_for_claude(self) -> None:
        from magi_agent.runtime.message_builder import build_system_prompt

        identity = {"soul": "<rules>Be safe</rules>"}
        prompt = build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=identity,
            model="claude-sonnet-4-6",
            model_aware_prompts_enabled=True,
        )
        assert "<rules>" in prompt

    def test_no_model_string_skips_adaptation(self) -> None:
        from magi_agent.runtime.message_builder import build_system_prompt

        identity = {"soul": "<rules>Be safe</rules>"}
        prompt = build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=identity,
            model="",
            model_aware_prompts_enabled=True,
        )
        assert "<rules>" in prompt

    def test_same_identity_different_output_per_provider(self) -> None:
        from magi_agent.runtime.message_builder import build_system_prompt

        identity = {
            "soul": "<identity>You are helpful.</identity>\n<rules>Follow instructions carefully.</rules>",
            "tools": "<tool-list>bash, python</tool-list>",
        }
        claude_prompt = build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=identity,
            model="claude-opus-4-6",
            model_aware_prompts_enabled=True,
        )
        gpt_prompt = build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=identity,
            model="gpt-5.5",
            model_aware_prompts_enabled=True,
        )
        assert claude_prompt != gpt_prompt
        # Assert on the soul section's <rules> tag, not <identity>: the
        # hardcoded MAGI_BASE_PERSONA floor is a protected block wrapped in
        # <identity> that (like the other protected blocks) is intentionally
        # NOT run through the per-provider XML-stripping adapter, so its tag
        # survives for every provider. The adapter's identity-section stripping
        # is still verified here via the non-colliding <rules> tag.
        assert "<rules>" in claude_prompt
        assert "<rules>" not in gpt_prompt
