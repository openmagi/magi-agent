"""Frozen ``ModelRecord`` shape consumed by :mod:`magi_agent.models.catalog`.

Lives in its own module so ``runtime/model_tiers.py`` can import
:class:`ModelRecord` without triggering a catalog load (the registry only needs
the type for its ``from_catalog`` classmethod).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from magi_agent.runtime.model_tiers import ModelCapability, ModelTier

#: Distinguishes provider-native ids from routed/product aliases.
#:
#: - ``direct``: the provider's own id (``claude-sonnet-4-6``).
#: - ``router``: a meta-router slug (``openai/gpt-5.5`` via OpenRouter).
#: - ``product``: a product-side label (e.g. ``magi-smart-router/auto``); kept
#:   as a future-proofing slot — not yet populated by the builtin catalog.
#: - ``custom``: user-pinned id surfaced by ``MAGI_MODEL`` overrides that the
#:   catalog has no record of; never appears in ``builtin_catalog.json``.
ModelSource = Literal["direct", "router", "product", "custom"]

#: Per-model "what shape of reasoning kwargs does this model accept" tag, used
#: by ``ModelCatalog.reasoning_default`` to source per-model default reasoning
#: kwargs for the LiteLlm build (E-6).
#:
#: - ``adaptive``: Anthropic adaptive-thinking-only models (Opus 4.7/4.8) — they
#:   require ``thinking={"type": "adaptive"}`` and REJECT the budget-enabled
#:   shape with a 400.
#: - ``effort``: cross-provider ``reasoning_effort`` knob (Sonnet, GPT-5.5,
#:   Gemini 3.1 Pro) — litellm maps it per-model into the right wire shape.
#: - ``budget``: budget-thinking-only (no shipped default — too provider
#:   specific; reserved for future records that need explicit budgets).
#: - ``none``: model has no reasoning surface (haiku, flash, cheap router
#:   tiers) — default reasoning kwargs are ``{}``.
ReasoningStyle = Literal["adaptive", "effort", "budget", "none"]


class ModelRecord(BaseModel):
    """Immutable record for a single provider/model entry.

    Field order mirrors the JSON schema in ``builtin_catalog.json`` so a
    grep-diff between the two stays readable. ``deprecated`` is opt-in (default
    ``False``); the TS exporter excludes deprecated records so the dashboard
    only ever offers current ids.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_default=True,
    )

    provider: str
    model: str
    label: str
    source: ModelSource
    tier: ModelTier
    capabilities: tuple[ModelCapability, ...]
    context_window: int
    max_output_tokens: int
    litellm_prefix: str
    deprecated: bool = False
    replacement: str | None = None
    last_verified: str
    # Per-model reasoning shape; default ``"none"`` keeps existing records that
    # predate the field back-compatible (the catalog still ships a value per
    # record so the meta-test below stays honest).
    reasoning_style: ReasoningStyle = "none"


__all__ = ["ModelRecord", "ModelSource", "ReasoningStyle"]
