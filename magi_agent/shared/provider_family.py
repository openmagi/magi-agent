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
the result with the FIREWORKS family. Both detectors consult
``ModelCatalog`` first (H1/N-28) via a function-local lazy import, so at
import time ``shared`` still pulls in no ``magi_agent`` module and stays a
true top-level leaf.
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
    # Catalog-first: a catalogued id (including aliases like ``haiku`` with no
    # ``claude-`` prefix) resolves via the single source before heuristics.
    # ``fireworks``/``openrouter`` fall through: they are not part of this
    # function's four-string return contract, so the heuristic decides.
    catalog_provider = _catalog_provider(model)
    if catalog_provider is not None:
        mapped = {
            "anthropic": "anthropic",
            "openai": "openai",
            "gemini": "google",
        }.get(catalog_provider)
        if mapped is not None:
            return mapped

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


def _catalog_provider(model: str) -> str | None:
    """Catalog lookup for ``model`` via a function-local lazy import.

    Kept lazy so importing ``shared`` never eagerly loads ``models.catalog``
    (cold-import cost and future layering ratchet). Returns the canonical
    provider string or ``None`` when the catalog has no record.
    """
    from magi_agent.models.catalog import ModelCatalog  # noqa: PLC0415

    return ModelCatalog.builtin().provider_for_model(model)


class ProviderFamily(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    FIREWORKS = "fireworks"
    DEFAULT = "default"


def detect_provider_family(model: str) -> ProviderFamily:
    """Map a model id to a :class:`ProviderFamily`.

    Catalog-first: a catalogued id resolves via the single source before the
    heuristic. Falls back to ``detect_provider`` (the prefix/substring
    matcher) extended with fireworks detection for the Kimi/MiniMax/GLM
    families.
    Unknown ids fall through to :attr:`ProviderFamily.DEFAULT`.
    """

    catalog_provider = _catalog_provider(model)
    if catalog_provider is not None:
        mapped = {
            "anthropic": ProviderFamily.ANTHROPIC,
            "openai": ProviderFamily.OPENAI,
            "gemini": ProviderFamily.GOOGLE,
            "fireworks": ProviderFamily.FIREWORKS,
        }.get(catalog_provider)
        if mapped is not None:
            return mapped

    model_lower = model.lower()
    if model_lower.startswith(("fireworks/", "kimi-", "minimax-", "glm-")):
        return ProviderFamily.FIREWORKS
    provider = detect_provider(model)
    try:
        return ProviderFamily(provider)
    except ValueError:
        return ProviderFamily.DEFAULT


__all__ = ["ProviderFamily", "detect_provider", "detect_provider_family"]
