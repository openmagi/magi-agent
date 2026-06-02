"""Cache control injection: convert PromptBlocks to provider-formatted dicts.

This module is the entry point for PR 3 of the prompt cache track.  It
bridges the typed :class:`~openmagi_core_agent.prompt.types.PromptBlock`
objects produced by the splitter (PR 1/2) with provider-specific cache marker
logic (providers.py).

Usage example::

    from openmagi_core_agent.prompt import CacheControlInjector, split_system_prompt

    split = split_system_prompt(parts, static_indices=frozenset({2, 5, 6}))
    injector = CacheControlInjector(provider="auto", model="claude-sonnet-4-6")
    api_blocks = injector.inject(split.blocks)
    # api_blocks is a list of dicts ready to pass to the Anthropic Messages API
"""

from __future__ import annotations

from .providers import ProviderCacheStrategy, get_strategy
from .types import PromptBlock


def detect_provider(model: str) -> str:
    """Auto-detect the LLM provider from a model identifier string.

    Detection priority:
    1. Exact prefix match (``claude-``, ``anthropic/``, ``gpt-``, etc.)
    2. Substring match as fallback for router-wrapped model strings
       (e.g. ``"some-router/claude-flex"`` → ``"anthropic"``).
    3. Falls back to ``"unknown"`` when no pattern matches.

    Args:
        model: Model identifier as it appears in the request (e.g.
            ``"claude-sonnet-4-6"``, ``"openai/gpt-5.5"``).

    Returns:
        One of ``"anthropic"``, ``"openai"``, ``"google"``, or ``"unknown"``.
    """
    model_lower = model.lower()

    # Prefix-based detection — highest priority
    if model_lower.startswith(("claude-", "anthropic/")):
        return "anthropic"
    if model_lower.startswith(("gpt-", "openai/", "openai-codex/")):
        return "openai"
    if model_lower.startswith(("gemini-", "google/")):
        return "google"

    # Substring fallback for router-wrapped model strings
    if "claude" in model_lower:
        return "anthropic"
    if "gpt" in model_lower:
        return "openai"
    if "gemini" in model_lower:
        return "google"

    return "unknown"


class CacheControlInjector:
    """Adds provider-specific cache markers to prompt blocks.

    The injector accepts an ordered tuple of :class:`PromptBlock` instances
    and converts them to plain dicts suitable for inclusion in an LLM API
    request.  Blocks with ``cache_scope`` set receive provider-appropriate
    cache markers; blocks with ``cache_scope=None`` are emitted as-is.

    Args:
        provider: Explicit provider name (``"anthropic"``, ``"openai"``,
            ``"google"``), or ``"auto"`` to auto-detect from *model*.
            Defaults to ``"auto"``.
        model: Model identifier used only when *provider* is ``"auto"``.
            Ignored when an explicit provider is given.
    """

    def __init__(self, provider: str = "auto", model: str = "") -> None:
        resolved = provider if provider != "auto" else detect_provider(model)
        self._strategy: ProviderCacheStrategy = get_strategy(resolved)
        self._resolved_provider: str = resolved

    def inject(self, blocks: tuple[PromptBlock, ...]) -> list[dict]:
        """Convert *blocks* to provider-formatted dicts with cache markers.

        Each block becomes a ``{"type": "text", "text": ...}`` dict.
        If the block has ``cache_scope`` set (not ``None``), the provider
        strategy's :meth:`~ProviderCacheStrategy.apply_cache_control` is
        called to add appropriate cache markers.  Dynamic blocks
        (``cache_scope=None``) pass through unchanged.

        Args:
            blocks: Ordered tuple of :class:`PromptBlock` instances,
                typically the ``blocks`` field of a
                :class:`~openmagi_core_agent.prompt.types.PromptSplitResult`.

        Returns:
            Ordered list of dicts ready for inclusion in an LLM API request.
            Each dict has at minimum ``"type"`` and ``"text"`` keys;
            Anthropic blocks may additionally carry ``"cache_control"``.
        """
        result: list[dict] = []
        for block in blocks:
            entry: dict = {"type": "text", "text": block.text}
            if block.cache_scope is not None:
                entry = self._strategy.apply_cache_control(entry, block.cache_scope)
            result.append(entry)
        return result

    @property
    def resolved_provider(self) -> str:
        """The provider string that was actually used to select a strategy.

        When *provider* was ``"auto"``, this reflects the result of
        :func:`detect_provider`.  When an explicit provider was given, this
        mirrors that value unchanged.
        """
        return self._resolved_provider
