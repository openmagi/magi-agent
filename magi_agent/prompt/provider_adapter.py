"""Model-aware prompt adaptation per LLM provider.

Different providers respond differently to system prompts:
- Anthropic (Claude): follows long structured prompts with XML tags well.
- OpenAI (GPT): prefers shorter prompts; verbose system prompts reduce
  instruction following. XML tags add noise.
- Google (Gemini): handles full-length prompts; structured output format
  differs.
- Other providers: full prompt, no adaptation (safe default).

This module provides a transform layer on top of identity sections — the
identity content stays in its source files (e.g. SOUL.md); adapters adjust
formatting and length per provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# E-13: the canonical home for ``ProviderFamily`` + ``detect_provider_family``
# is ``magi_agent.shared.provider_family``. Re-exported here for back-compat
# with external importers (and the public ``magi_agent.prompt`` package
# surface). New call sites should import from the shared module directly.
from magi_agent.shared.provider_family import (
    ProviderFamily,
    detect_provider_family,
)


@runtime_checkable
class PromptAdapter(Protocol):
    """Protocol for provider-specific prompt adaptation."""

    def adapt_sections(self, sections: list[str]) -> list[str]:
        """Transform identity sections for a specific provider.

        Args:
            sections: Ordered identity section strings (e.g. SOUL, AGENTS).

        Returns:
            Adapted sections list. May be shorter (merged) or have modified
            content, but semantic meaning must be preserved.
        """
        ...

    @property
    def provider(self) -> ProviderFamily: ...

    @property
    def adaptations_applied(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class PromptRoutingConfig:
    """Configuration for model-aware prompt routing."""

    enabled: bool = False
    openai_compression_ratio: float = 0.6
    merge_short_section_threshold: int = 200


# E-11: ``_XML_TAG_RE`` removed (OpenAI XML-strip folklore retired).
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


class AnthropicAdapter:
    """No-op adapter for Claude models.

    Claude handles long, structured prompts with XML tags well.
    No compression or reformatting needed.
    """

    def adapt_sections(self, sections: list[str]) -> list[str]:
        return list(sections)

    @property
    def provider(self) -> ProviderFamily:
        return ProviderFamily.ANTHROPIC

    @property
    def adaptations_applied(self) -> tuple[str, ...]:
        return ()


class OpenAIAdapter:
    """No-op adapter for GPT models (E-11).

    Pre-E-11 this adapter stripped XML, merged short sections, and
    compressed whitespace on the premise "GPT follows shorter prompts;
    XML adds noise." It was dormant
    (``PromptRoutingConfig.enabled=False``) but is now an explicit
    identity transform to protect the static-prefix prompt cache (E-7
    family) from a future flip-on foot-gun: rewriting the static prefix
    produces a different byte string and defeats the cache. If a future
    per-provider knob is desired it must operate only on dynamic blocks
    (``cache_scope=None``), never the static prefix.

    The ``PromptRoutingConfig`` constructor argument is accepted for
    back-compat with the old call shape but its
    ``openai_compression_ratio``/``merge_short_section_threshold``
    fields are inert.
    """

    def __init__(self, config: PromptRoutingConfig | None = None) -> None:
        # Accept config for back-compat; the fields are deliberately
        # ignored — see class docstring.
        del config

    def adapt_sections(self, sections: list[str]) -> list[str]:
        return list(sections)

    @property
    def provider(self) -> ProviderFamily:
        return ProviderFamily.OPENAI

    @property
    def adaptations_applied(self) -> tuple[str, ...]:
        return ()


class GoogleAdapter:
    """Minimal adaptation for Gemini models.

    Gemini handles full prompts well with its larger context window.
    Only normalize formatting for consistency.
    """

    def adapt_sections(self, sections: list[str]) -> list[str]:
        return [_normalize_whitespace(s) for s in sections if s.strip()]

    @property
    def provider(self) -> ProviderFamily:
        return ProviderFamily.GOOGLE

    @property
    def adaptations_applied(self) -> tuple[str, ...]:
        return ("normalize_whitespace",)


class DefaultAdapter:
    """No-op passthrough for unknown providers."""

    def adapt_sections(self, sections: list[str]) -> list[str]:
        return list(sections)

    @property
    def provider(self) -> ProviderFamily:
        return ProviderFamily.DEFAULT

    @property
    def adaptations_applied(self) -> tuple[str, ...]:
        return ()


_ADAPTERS: dict[ProviderFamily, type] = {
    ProviderFamily.ANTHROPIC: AnthropicAdapter,
    ProviderFamily.OPENAI: OpenAIAdapter,
    ProviderFamily.GOOGLE: GoogleAdapter,
    ProviderFamily.FIREWORKS: DefaultAdapter,
    ProviderFamily.DEFAULT: DefaultAdapter,
}


def get_adapter(
    provider: ProviderFamily,
    config: PromptRoutingConfig | None = None,
) -> PromptAdapter:
    """Return a prompt adapter for the given provider family."""
    cls = _ADAPTERS.get(provider, DefaultAdapter)
    if cls is OpenAIAdapter:
        return cls(config)
    return cls()


def adapt_identity_sections(
    sections: list[str],
    *,
    model: str,
    config: PromptRoutingConfig | None = None,
) -> tuple[list[str], PromptAdapter]:
    """Convenience: detect provider from model and adapt sections.

    Returns the adapted sections and the adapter instance (for evidence).
    """
    family = detect_provider_family(model)
    adapter = get_adapter(family, config)
    return adapter.adapt_sections(sections), adapter


# E-12: per-provider tool-schema repair moved to ``adk_bridge/tool_schema_repair``
# (schema repair is a tool/adk-bridge concern, not a prompt-assembly
# concern). Re-exported here for back-compat with external importers
# and the public ``magi_agent.prompt`` package surface. New call sites
# should import from the canonical home directly.
from magi_agent.adk_bridge.tool_schema_repair import (  # noqa: E402
    repair_tool_schema_for_provider,
)


# E-11: ``_strip_xml_tags``, ``_compress_text``, ``_merge_short_sections``
# (and the unused ``_XML_TAG_RE``) were the OpenAI-folklore helpers. Now
# unused after ``OpenAIAdapter`` became a no-op. ``_normalize_whitespace``
# is still used by ``GoogleAdapter``.


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of 3+ newlines to 2."""
    return _MULTI_NEWLINE_RE.sub("\n\n", text).strip()
