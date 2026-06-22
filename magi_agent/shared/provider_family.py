"""E-13 — single source of truth for provider-family detection.

``ProviderFamily`` + ``detect_provider_family`` used to live in
``prompt/provider_adapter.py`` (the prompt-layer package). That was the
wrong home: tool-schema repair (E-12) and the cache injector (E-7) also
consume them. Keeping the definition in ``prompt/`` either forced an
import cycle or invited a copy. This module is the canonical home and
``prompt/provider_adapter`` re-exports for back-compat.

The detection string form (``detect_provider`` in
``prompt/injection.py``) is kept where it is — the prefix/substring
matcher predates fireworks and only knows the three canonical wire
providers. ``detect_provider_family`` delegates to it and extends the
result with the FIREWORKS family.
"""

from __future__ import annotations

from enum import Enum

from magi_agent.prompt.injection import detect_provider


class ProviderFamily(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    FIREWORKS = "fireworks"
    DEFAULT = "default"


def detect_provider_family(model: str) -> ProviderFamily:
    """Map a model id to a :class:`ProviderFamily`.

    Reuses ``prompt.injection.detect_provider`` (the prefix/substring
    matcher) and extends it with fireworks detection for the Kimi/MiniMax
    families. Unknown ids fall through to :attr:`ProviderFamily.DEFAULT`.
    """

    model_lower = model.lower()
    if model_lower.startswith(("fireworks/", "kimi-", "minimax-")):
        return ProviderFamily.FIREWORKS
    provider = detect_provider(model)
    try:
        return ProviderFamily(provider)
    except ValueError:
        return ProviderFamily.DEFAULT


__all__ = ["ProviderFamily", "detect_provider_family"]
