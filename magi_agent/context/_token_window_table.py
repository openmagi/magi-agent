"""E-4 / H1 - single canonical home for the model->context-window table.

This module is a stdlib + ``models.catalog`` leaf: the window values have a
single source, ``magi_agent/models/builtin_catalog.json``. The table is
DERIVED from the catalog (E-1) rather than hand-maintained. Both
``context/token_tracker.py`` (which adds the optional
``estimate_message_tokens`` backend) and ``runtime/message_builder.py``
(which adds OpenAI-compat fallback policy on top) re-export
``_KNOWN_TOKEN_LIMITS`` from here without redeclaring it.

Every catalogued record fans out to its :meth:`ModelCatalog.id_forms`
(bare, provider-prefixed, and alias-prefixed) with the record's
``context_window`` value. A small non-catalog overlay carries the handful of
legacy/pseudo model ids the catalog does not model. The overlay may never
shadow a catalog-derived key (that would defeat the single source), so a
collision raises loudly at import.
"""

from __future__ import annotations

# Legacy / pseudo model ids the catalog does not (yet) carry a record for.
# These must stay disjoint from the catalog-derived keys; a collision means a
# record was added to the catalog and the overlay entry is now redundant.
_NON_CATALOG_OVERLAY: dict[str, int] = {
    "claude-haiku-4-5-20251001": 150_000,
    "gpt-5-nano": 300_000,
    "gpt-5-mini": 300_000,
    "gpt-5.1": 300_000,
    "gpt-5.4": 300_000,
    "openai-codex/gpt-5.5": 750_000,
    "magi-smart-router/auto": 750_000,
    "big-dic-router/auto": 196_608,
    "local/gemma-fast": 98_304,
    "local/gemma-max": 98_304,
    "local/qwen-uncensored": 98_304,
}


def _build_known_token_limits() -> dict[str, int]:
    """Derive the window table from every catalogued record's id-forms.

    Includes deprecated and ``source="router"`` records (they still surface as
    model ids the runtime can resolve). A single id-form mapping to two
    different window values raises ``ValueError`` so catalog-internal drift is
    loud rather than silently resolved by iteration order.
    """
    # Lazy import: keeps ``context`` off a top-level edge into ``models`` so
    # ``models`` stays out of the cross-package import cycle (layering ratchet).
    from magi_agent.models.catalog import ModelCatalog  # noqa: PLC0415

    catalog = ModelCatalog.builtin()
    table: dict[str, int] = {}
    for record in catalog.all_records():
        for form in catalog.id_forms(record):
            existing = table.get(form)
            if existing is not None and existing != record.context_window:
                raise ValueError(
                    f"catalog drift: id-form {form!r} maps to both "
                    f"{existing} and {record.context_window}"
                )
            table[form] = record.context_window
    return table


def _compose_known_token_limits() -> dict[str, int]:
    table = _build_known_token_limits()
    for key, value in _NON_CATALOG_OVERLAY.items():
        if key in table:
            raise ValueError(
                f"overlay key {key!r} shadows a catalog-derived key; remove "
                "it from _NON_CATALOG_OVERLAY (the catalog is the source)"
            )
        table[key] = value
    return table


_KNOWN_TOKEN_LIMITS: dict[str, int] = _compose_known_token_limits()

__all__ = ["_KNOWN_TOKEN_LIMITS"]
