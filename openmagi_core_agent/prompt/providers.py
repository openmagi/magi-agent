"""Provider-specific cache control strategies.

Each strategy knows how to annotate a prompt block dict with the appropriate
cache markers for its LLM provider API.

Design
------
- Anthropic: explicit ``cache_control`` block markers (type=ephemeral).
- OpenAI: automatic prompt caching — no explicit markers needed (no-op).
- Google: context caching works at a different API level (cached_content
  resource, not individual prompt blocks) — no-op at block level.

The :class:`ProviderCacheStrategy` Protocol defines the interface; callers
can type-hint against the Protocol without importing concrete classes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ProviderCacheStrategy(Protocol):
    """Protocol for provider-specific cache control injection."""

    def apply_cache_control(self, block: dict, cache_scope: str) -> dict:
        """Apply provider-specific cache markers to a prompt block.

        Args:
            block: A dict such as ``{"type": "text", "text": "..."}``.
            cache_scope: ``"global"`` or ``"org"``.  Passed for tracking;
                not all providers surface it in the API payload.

        Returns:
            A *copy* of *block* with provider-specific cache markers added.
            The original *block* is never mutated.
        """
        ...


class AnthropicCacheStrategy:
    """Anthropic API cache control using ``cache_control`` block markers.

    The Anthropic Messages API accepts an optional ``cache_control`` field on
    content blocks.  Setting ``{"type": "ephemeral"}`` marks the block as
    eligible for prompt caching.

    Note: Anthropic's current API does not support a ``scope`` field inside
    ``cache_control``; the ``cache_scope`` argument is accepted for interface
    consistency but is not forwarded to the payload.
    """

    def apply_cache_control(self, block: dict, cache_scope: str) -> dict:
        result = dict(block)
        result["cache_control"] = {"type": "ephemeral"}
        return result


class OpenAICacheStrategy:
    """OpenAI automatic prompt caching — no explicit markers required.

    OpenAI caches common prompt prefixes automatically on supported models.
    No block-level annotation is needed, so this strategy is a no-op that
    returns a shallow copy of the input block.
    """

    def apply_cache_control(self, block: dict, cache_scope: str) -> dict:
        return dict(block)


class GoogleCacheStrategy:
    """Google Gemini context caching — not applicable at prompt block level.

    Google's ``cached_content`` API works at the level of an entire context
    resource, not individual prompt blocks.  Block-level markers are not
    supported, so this strategy is a no-op that returns a shallow copy.
    """

    def apply_cache_control(self, block: dict, cache_scope: str) -> dict:
        return dict(block)


def get_strategy(provider: str) -> ProviderCacheStrategy:
    """Return the :class:`ProviderCacheStrategy` for *provider*.

    Args:
        provider: One of ``"anthropic"``, ``"openai"``, ``"google"``, or any
            other string.  Unknown providers fall back to the no-op
            :class:`OpenAICacheStrategy` so that unrecognised providers are
            safe by default (no spurious cache markers injected).

    Returns:
        A concrete strategy instance.
    """
    strategies: dict[str, ProviderCacheStrategy] = {
        "anthropic": AnthropicCacheStrategy(),
        "openai": OpenAICacheStrategy(),
        "google": GoogleCacheStrategy(),
    }
    return strategies.get(provider, OpenAICacheStrategy())
