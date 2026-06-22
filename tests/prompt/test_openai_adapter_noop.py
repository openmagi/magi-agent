"""E-11 — ``OpenAIAdapter`` is a no-op (cache-prefix invariance protection).

The pre-E-11 ``OpenAIAdapter.adapt_sections`` stripped XML tags, merged
short sections, and compressed whitespace on the premise "GPT follows
shorter prompts; XML adds noise." It was gated dormant by
``PromptRoutingConfig.enabled=False``, but a future flip-on would have
defeated the static-prefix prompt cache (E-7 family) — a rewritten
prefix is a different byte string → cache miss on every turn.

This test locks the new behavior: OpenAI adapter is identity, just like
``AnthropicAdapter`` and ``DefaultAdapter``. If a future per-provider
knob is desired, it must operate ONLY on dynamic blocks
(``cache_scope=None``), never the static prefix.
"""

from __future__ import annotations

import pytest

from magi_agent.prompt.provider_adapter import (
    AnthropicAdapter,
    DefaultAdapter,
    OpenAIAdapter,
    PromptRoutingConfig,
    ProviderFamily,
    get_adapter,
)


# ---------------------------------------------------------------------------
# Identity contract: every input shape returns the input list unchanged.
# ---------------------------------------------------------------------------


def test_openai_adapter_returns_input_identity() -> None:
    sections = ["<rules>Be safe</rules>", "Plain text", "Section three"]
    out = OpenAIAdapter().adapt_sections(sections)
    assert out == sections


def test_openai_adapter_preserves_xml_tags() -> None:
    sections = ["<rules>Be safe</rules>", "<tool-list>bash\npython</tool-list>"]
    out = OpenAIAdapter().adapt_sections(sections)
    assert out == sections
    for section in out:
        assert "<" in section  # XML preserved (would have been stripped pre-E-11)


def test_openai_adapter_does_not_merge_short_sections() -> None:
    sections = ["Short A", "Short B", "Short C"]
    out = OpenAIAdapter().adapt_sections(sections)
    assert out == sections
    assert len(out) == 3  # would have been merged pre-E-11


def test_openai_adapter_does_not_compress_whitespace() -> None:
    sections = ["Line 1\n\n\n\nLine 2"]
    out = OpenAIAdapter().adapt_sections(sections)
    assert out == sections  # whitespace preserved (would have been compressed)


def test_openai_adapter_does_not_drop_empty_sections() -> None:
    sections = ["", "Real content", "   "]
    out = OpenAIAdapter().adapt_sections(sections)
    assert out == sections  # empty preserved (would have been filtered)


# ---------------------------------------------------------------------------
# adaptations_applied = () — the adapter does not advertise any folklore.
# ---------------------------------------------------------------------------


def test_openai_adaptations_applied_is_empty() -> None:
    assert OpenAIAdapter().adaptations_applied == ()


def test_openai_provider_property() -> None:
    assert OpenAIAdapter().provider == ProviderFamily.OPENAI


def test_openai_adapter_ignores_routing_config() -> None:
    """Even with a non-default ``PromptRoutingConfig``, the adapter is
    identity. The config fields ``openai_compression_ratio`` and
    ``merge_short_section_threshold`` are inert."""

    sections = ["<rules>x</rules>", "<rules>y</rules>"]
    cfg = PromptRoutingConfig(
        enabled=True,
        openai_compression_ratio=0.1,  # would have been aggressive pre-E-11
        merge_short_section_threshold=10_000,  # would have merged everything
    )
    out = OpenAIAdapter(cfg).adapt_sections(sections)
    assert out == sections


def test_get_adapter_openai_is_identity() -> None:
    """End-to-end via the factory: ``get_adapter(ProviderFamily.OPENAI)``
    must return an instance that preserves sections."""

    sections = ["<a>1</a>", "<b>2</b>"]
    adapter = get_adapter(ProviderFamily.OPENAI)
    assert adapter.adapt_sections(sections) == sections


# ---------------------------------------------------------------------------
# Parity vs DefaultAdapter / AnthropicAdapter: same output on the same input.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sections",
    [
        ["<rules>Be safe</rules>"],
        ["# SOUL\n<identity>x</identity>", "# TOOLS\n<tool-list>bash</tool-list>"],
        [""],
        ["A", "B", "C"],
    ],
)
def test_openai_matches_default_and_anthropic_output(sections: list[str]) -> None:
    openai_out = OpenAIAdapter().adapt_sections(sections)
    default_out = DefaultAdapter().adapt_sections(sections)
    anthropic_out = AnthropicAdapter().adapt_sections(sections)
    assert openai_out == default_out == anthropic_out
