"""E-13 - single source of truth for provider-family detection.

``ProviderFamily`` + ``detect_provider_family`` used to live in
``prompt/provider_adapter.py`` (the prompt-layer package). That was the
wrong home: tool-schema repair (E-12) and the cache injector (E-7) also
consume them. Keeping the definition in ``prompt/`` either forced an
import cycle or invited a copy. This module is the canonical home and
``prompt/provider_adapter`` re-exports for back-compat.

The detection string form (``detect_provider``) was moved here (rem2/F1)
because the previous arrangement (``shared`` top-level importing
``prompt/injection``) was a real two-node top-level cycle that crashed a
fresh interpreter. ``prompt/injection`` now re-exports ``detect_provider``
for back-compat. ``detect_provider_family`` delegates to it and extends
the result with the FIREWORKS family. ``shared`` no longer imports any
``magi_agent`` module, so it is a true leaf.
"""

from __future__ import annotations

from enum import Enum


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

    # Prefix-based detection - highest priority
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


class ProviderFamily(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    FIREWORKS = "fireworks"
    DEFAULT = "default"


def detect_provider_family(model: str) -> ProviderFamily:
    """Map a model id to a :class:`ProviderFamily`.

    Reuses ``detect_provider`` (the prefix/substring matcher) and extends
    it with fireworks detection for the Kimi/MiniMax families. Unknown ids
    fall through to :attr:`ProviderFamily.DEFAULT`.
    """

    model_lower = model.lower()
    if model_lower.startswith(("fireworks/", "kimi-", "minimax-")):
        return ProviderFamily.FIREWORKS
    provider = detect_provider(model)
    try:
        return ProviderFamily(provider)
    except ValueError:
        return ProviderFamily.DEFAULT


__all__ = ["ProviderFamily", "detect_provider", "detect_provider_family"]
