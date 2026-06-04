"""Cache control injection: convert PromptBlocks to provider-formatted dicts.

This module is the entry point for PR 3 of the prompt cache track.  It
bridges the typed :class:`~magi_agent.prompt.types.PromptBlock`
objects produced by the splitter (PR 1/2) with provider-specific cache marker
logic (providers.py).

Usage example::

    from magi_agent.prompt import CacheControlInjector, split_system_prompt

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

    # Anthropic accepts at most 4 cache breakpoints per request. The system
    # prefix already reserves up to 2 (see ``build_system_prompt_blocks``), so
    # the rolling conversation tail may use at most 2 more.
    _MESSAGE_TAIL_MAX_BREAKPOINTS = 2

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
                :class:`~magi_agent.prompt.types.PromptSplitResult`.

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

    def mark_message_tail(
        self,
        messages: list[dict],
        *,
        tail_size: int = 2,
    ) -> list[dict]:
        """Mark the last *tail_size* non-system messages with cache markers.

        Mirrors OpenCode's rolling-tail prompt caching: the growing
        conversation tail is cached (in addition to the system prefix) so that
        per-turn input cost shrinks as the conversation grows.

        Behaviour by provider:

        - **Anthropic** (``claude``): each of the last *tail_size* non-system
          messages gets an ``cache_control: {type: ephemeral}`` marker placed
          on its last content block.
        - **OpenAI / Google / unknown**: no-op — those providers auto-cache
          common prefixes, so explicit markers are unnecessary (and Anthropic
          markers would be invalid on their payloads).

        The number of new breakpoints is capped at
        :attr:`_MESSAGE_TAIL_MAX_BREAKPOINTS` (2) so that, combined with the
        up-to-2 breakpoints the system prefix may already carry, the request
        never exceeds Anthropic's 4-breakpoint limit (rule 3).

        Args:
            messages: Ordered conversation messages in Anthropic/Messages shape
                (``{"role": ..., "content": [...] | str}``). System messages
                (``role == "system"``) are never marked.
            tail_size: How many trailing non-system messages to mark. Capped to
                :attr:`_MESSAGE_TAIL_MAX_BREAKPOINTS`.

        Returns:
            A new list of messages (deep-copied for any message that is
            modified). The input list and its messages are never mutated.
        """
        result = [dict(message) for message in messages]
        if self._resolved_provider != "anthropic":
            return result

        capped = min(max(tail_size, 0), self._MESSAGE_TAIL_MAX_BREAKPOINTS)
        if capped == 0:
            return result

        non_system_indices = [
            index
            for index, message in enumerate(result)
            if message.get("role") != "system"
        ]
        for index in non_system_indices[-capped:]:
            result[index] = self._mark_message(result[index])
        return result

    def _mark_message(self, message: dict) -> dict:
        marked = dict(message)
        content = marked.get("content")
        if isinstance(content, list):
            blocks = [dict(block) if isinstance(block, dict) else block for block in content]
            for position in range(len(blocks) - 1, -1, -1):
                block = blocks[position]
                if isinstance(block, dict):
                    blocks[position] = self._strategy.apply_cache_control(block, "global")
                    break
            marked["content"] = blocks
        else:
            text = "" if content is None else str(content)
            marked["content"] = [
                self._strategy.apply_cache_control(
                    {"type": "text", "text": text},
                    "global",
                )
            ]
        return marked

    @property
    def resolved_provider(self) -> str:
        """The provider string that was actually used to select a strategy.

        When *provider* was ``"auto"``, this reflects the result of
        :func:`detect_provider`.  When an explicit provider was given, this
        mirrors that value unchanged.
        """
        return self._resolved_provider
