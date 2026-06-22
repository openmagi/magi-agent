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


__all__ = ["ModelRecord", "ModelSource"]
