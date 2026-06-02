"""Model-aware prompt adaptation per LLM provider.

Different providers respond differently to system prompts:
- Anthropic (Claude): follows long structured prompts with XML tags well.
- OpenAI (GPT): prefers shorter prompts; verbose system prompts reduce
  instruction following. XML tags add noise.
- Google (Gemini): handles full-length prompts; structured output format
  differs.
- Other providers: full prompt, no adaptation (safe default).

This module provides a transform layer on top of identity sections — the
identity content stays in SOUL.md/TOOLS.md; adapters adjust formatting and
length per provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from .injection import detect_provider


class ProviderFamily(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    FIREWORKS = "fireworks"
    DEFAULT = "default"


def detect_provider_family(model: str) -> ProviderFamily:
    """Map a model string to a ProviderFamily enum.

    Reuses the existing ``detect_provider`` logic and extends it with
    fireworks detection for Kimi/MiniMax models.
    """
    model_lower = model.lower()
    if model_lower.startswith(("fireworks/", "kimi-", "minimax-")):
        return ProviderFamily.FIREWORKS

    provider = detect_provider(model)
    try:
        return ProviderFamily(provider)
    except ValueError:
        return ProviderFamily.DEFAULT


@runtime_checkable
class PromptAdapter(Protocol):
    """Protocol for provider-specific prompt adaptation."""

    def adapt_sections(self, sections: list[str]) -> list[str]:
        """Transform identity sections for a specific provider.

        Args:
            sections: Ordered identity section strings (SOUL, TOOLS, etc.).

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


_XML_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9_-]*(?:\s[^>]*)?>")
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
    """Compress and simplify prompts for GPT models.

    GPT models follow shorter prompts more reliably. This adapter:
    1. Strips XML tags (GPT doesn't benefit from them).
    2. Merges short sections into fewer blocks.
    3. Trims excessive whitespace.
    """

    def __init__(self, config: PromptRoutingConfig | None = None) -> None:
        cfg = config or PromptRoutingConfig()
        self._compression_ratio = cfg.openai_compression_ratio
        self._merge_threshold = cfg.merge_short_section_threshold

    def adapt_sections(self, sections: list[str]) -> list[str]:
        stripped = [_strip_xml_tags(s) for s in sections]
        merged = _merge_short_sections(stripped, self._merge_threshold)
        compressed = [_compress_text(s, self._compression_ratio) for s in merged]
        return [s for s in compressed if s.strip()]

    @property
    def provider(self) -> ProviderFamily:
        return ProviderFamily.OPENAI

    @property
    def adaptations_applied(self) -> tuple[str, ...]:
        return ("strip_xml_tags", "merge_short_sections", "compress_whitespace")


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


def _strip_xml_tags(text: str) -> str:
    """Remove XML-style tags from text."""
    return _XML_TAG_RE.sub("", text)


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of 3+ newlines to 2."""
    return _MULTI_NEWLINE_RE.sub("\n\n", text).strip()


def _compress_text(text: str, ratio: float) -> str:
    """Compress text by removing redundant whitespace and blank lines.

    This is a conservative compression that preserves all words and
    sentence structure — it only removes excessive formatting.
    """
    text = _normalize_whitespace(text)
    lines = text.split("\n")
    compressed: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        if stripped or (compressed and compressed[-1] != ""):
            compressed.append(stripped)
    return "\n".join(compressed).strip()


def _merge_short_sections(
    sections: list[str],
    threshold: int,
) -> list[str]:
    """Merge consecutive short sections into larger blocks."""
    if not sections:
        return []
    merged: list[str] = []
    buffer = sections[0]
    for section in sections[1:]:
        if len(buffer) < threshold and len(section) < threshold:
            buffer = f"{buffer}\n\n{section}"
        else:
            merged.append(buffer)
            buffer = section
    merged.append(buffer)
    return merged
