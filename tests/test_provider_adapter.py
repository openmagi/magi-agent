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
        assert detect_provider_family("kimi-k2p7-code") == ProviderFamily.FIREWORKS
        assert detect_provider_family("glm-5p2") == ProviderFamily.FIREWORKS
        # ``glm-`` is a heuristic prefix too, so uncatalogued GLM variants
        # still route to fireworks.
        assert detect_provider_family("glm-5p1") == ProviderFamily.FIREWORKS

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
    """E-11: OpenAIAdapter is now a no-op (identity transform).

    Pre-E-11 this adapter stripped XML, merged short sections, and
    compressed whitespace — defeating the static-prefix prompt cache.
    The current contract is identity, matching AnthropicAdapter /
    DefaultAdapter. See tests/prompt/test_openai_adapter_noop.py for
    the cache-prefix invariance assertions.
    """

    def test_identity_passthrough(self) -> None:
        sections = ["<rules>Be safe</rules>", "Plain text here"]
        result = OpenAIAdapter().adapt_sections(sections)
        assert result == sections

    def test_preserves_xml_tags(self) -> None:
        sections = ["<rules>Be safe</rules>"]
        result = OpenAIAdapter().adapt_sections(sections)
        assert "<rules>" in result[0]

    def test_does_not_merge_short_sections(self) -> None:
        sections = ["Short A", "Short B", "Short C"]
        result = OpenAIAdapter().adapt_sections(sections)
        assert result == sections

    def test_provider_property(self) -> None:
        assert OpenAIAdapter().provider == ProviderFamily.OPENAI

    def test_adaptations_applied_is_empty(self) -> None:
        assert OpenAIAdapter().adaptations_applied == ()

    def test_returns_copy_not_input_list(self) -> None:
        sections = ["section1"]
        result = OpenAIAdapter().adapt_sections(sections)
        assert result is not sections


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
        # E-11: OpenAIAdapter is now a no-op (identity), so XML tags are
        # preserved like every other adapter.
        sections = ["<rules>Be safe</rules>"]
        result, adapter = adapt_identity_sections(sections, model="gpt-5.5")
        assert isinstance(adapter, OpenAIAdapter)
        assert result == sections
        assert "<rules>" in result[0]

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

    def test_enabled_preserves_xml_for_gpt(self) -> None:
        # E-11: OpenAIAdapter is identity. XML survives the GPT path
        # just like the Anthropic path. This protects the static-prefix
        # prompt cache (E-7) from a future flip-on foot-gun.
        from magi_agent.runtime.message_builder import build_system_prompt

        identity = {"soul": "<rules>Be safe</rules>"}
        prompt = build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=identity,
            model="gpt-5.5",
            model_aware_prompts_enabled=True,
        )
        assert "<rules>" in prompt
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

    def test_same_identity_preserves_xml_for_every_provider(self) -> None:
        # E-11: cache-prefix invariance. The pre-E-11 test asserted that
        # the GPT prompt differed from the Claude prompt (XML stripped).
        # Now the user-supplied <rules> XML survives on BOTH paths —
        # protecting the static-prefix prompt cache (E-7 family) from a
        # future flip-on foot-gun. (The whole-prompt byte-equality
        # cannot be asserted directly because build_system_prompt
        # captures a current-time runtime context block; instead we
        # assert the XML invariant per provider, which is the substance
        # of the cache-prefix guarantee.)
        from magi_agent.runtime.message_builder import build_system_prompt

        identity = {
            "soul": "<identity>You are helpful.</identity>\n<rules>Follow instructions carefully.</rules>",
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
        assert "<rules>" in claude_prompt
        assert "<rules>" in gpt_prompt
        # Pre-E-11 the prompt LENGTHS differed sharply because GPT was
        # XML-stripped + section-merged. Post-E-11 the section list is
        # passed through identically — so the two prompts differ ONLY
        # in a small runtime context timestamp, not in the static
        # prefix content. Loose-bound the structural divergence.
        assert abs(len(claude_prompt) - len(gpt_prompt)) < 100
