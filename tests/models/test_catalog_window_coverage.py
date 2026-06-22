"""E-3 — every registry/catalog model has a non-default context window.

Engine review H3 asks for a meta-test that no model the runtime can
actually return silently falls through ``_DEFAULT_CONTEXT_WINDOW``
(150_000 — the most conservative Anthropic-class window). The
flagship ``claude-opus-4-8`` was the cautionary example: it lived in
``ModelTierRegistry.with_defaults()`` but was absent from
``_KNOWN_TOKEN_LIMITS``, so the most-used model got the most-conservative
window, firing compaction early.

This test locks two invariants:

1. Every ``(provider, model)`` in ``ModelTierRegistry.with_defaults()``
   has an explicit entry in the canonical lookup table.
2. Every record in the ``ModelCatalog`` has a non-zero ``context_window``
   field (the structural source for window numbers — E-1).
"""

from __future__ import annotations

import pytest

from magi_agent.context._token_window_table import _KNOWN_TOKEN_LIMITS
from magi_agent.context.token_tracker import _DEFAULT_CONTEXT_WINDOW
from magi_agent.models.catalog import ModelCatalog
from magi_agent.runtime.model_tiers import ModelTierRegistry


def test_every_registry_model_has_explicit_window() -> None:
    """No (provider, model) the registry can emit may fall to
    ``_DEFAULT_CONTEXT_WINDOW`` silently. If the model genuinely shares
    the default value, it must still be enumerated explicitly so
    operators see WHY the window matches the default."""

    registry = ModelTierRegistry.with_defaults()
    gaps: list[tuple[str, str]] = []
    for (provider, model), _record in registry._records.items():
        if _KNOWN_TOKEN_LIMITS.get(model) is None:
            gaps.append((provider, model))
    assert gaps == [], (
        "Models registered in ModelTierRegistry.with_defaults() silently "
        f"fall to _DEFAULT_CONTEXT_WINDOW ({_DEFAULT_CONTEXT_WINDOW:,}): "
        f"{gaps}. Add explicit entries to "
        "magi_agent/context/_token_window_table.py."
    )


def test_flagship_opus_4_8_window_is_not_default() -> None:
    """Regression guard for the original engine-H3 finding:
    ``claude-opus-4-8`` MUST have an explicit window (it shares the same
    150k Opus-class window as 4-6, but the value must be cataloged, not
    inferred from the silent default)."""

    assert "claude-opus-4-8" in _KNOWN_TOKEN_LIMITS
    assert "anthropic/claude-opus-4-8" in _KNOWN_TOKEN_LIMITS
    assert _KNOWN_TOKEN_LIMITS["claude-opus-4-8"] == 150_000
    assert (
        _KNOWN_TOKEN_LIMITS["claude-opus-4-8"]
        == _KNOWN_TOKEN_LIMITS["claude-opus-4-6"]
    )


def test_every_catalog_record_has_a_context_window() -> None:
    """The structural source (E-1 catalog) must always expose a window —
    zero or missing windows mean a record was added without the field
    populated."""

    catalog = ModelCatalog.builtin()
    bad: list[str] = []
    for record in catalog.all_records():
        if record.context_window <= 0:
            bad.append(f"{record.provider}/{record.model}")
    assert bad == [], (
        "ModelCatalog records with zero/missing context_window: "
        f"{bad}. Update magi_agent/models/builtin_catalog.json."
    )


@pytest.mark.parametrize(
    "model,window",
    [
        ("haiku", 150_000),
        ("kimi-k2p5", 196_608),
        ("gpt-5.5-pro", 787_500),
    ],
)
def test_e3_closed_gaps_have_explicit_entries(model: str, window: int) -> None:
    """The three gaps surfaced by the E-3 audit
    (``haiku``/``kimi-k2p5``/``gpt-5.5-pro``) MUST stay explicitly
    cataloged — preventing a regression where future window edits drop
    them back to the default."""

    assert _KNOWN_TOKEN_LIMITS.get(model) == window
