"""Frozen data models for the prompt caching split."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptBlock:
    """A single segment of the system prompt.

    Attributes:
        text: The raw text content of this block.
        cache_scope: ``"global"`` for static/cacheable blocks, ``None`` for
            dynamic blocks that change per-turn and must not be cached.
    """

    text: str
    cache_scope: str | None


@dataclass(frozen=True)
class PromptCacheConfig:
    """Configuration for prompt caching behaviour.

    Caching is **disabled by default** (``enabled=False``) so the feature is
    opt-in and cannot accidentally activate on already-deployed runtimes.

    Attributes:
        enabled: Whether prompt caching is active.
        provider: Provider hint for cache control header emission.  Defaults
            to ``"auto"`` which lets the runtime decide.
        static_section_keys: Ordered keys of identity sections considered
            static across turns.  Empty tuple means the caller controls the
            split via ``static_indices`` instead.
    """

    enabled: bool = False
    provider: str = "auto"
    static_section_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromptSplitResult:
    """The result of splitting a system prompt into static and dynamic blocks.

    Attributes:
        blocks: Ordered tuple of :class:`PromptBlock` instances representing
            the full system prompt in assembly order.
    """

    blocks: tuple[PromptBlock, ...]
