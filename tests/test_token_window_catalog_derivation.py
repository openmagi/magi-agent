"""H1 (N-29) - ``_KNOWN_TOKEN_LIMITS`` is derived from ``ModelCatalog``.

The window table used to be a 41-key hand-maintained dict that had drifted
from the catalog single source (E-1). This locks the derivation: every
catalogued record fans out to its id-forms with the catalog window value,
the four drift pairs converge on the catalog value, and the small
non-catalog overlay (legacy/pseudo model ids) stays disjoint from the
derived keys so the catalog can never be silently shadowed.
"""

from __future__ import annotations

import pytest

from magi_agent.context._token_window_table import (
    _KNOWN_TOKEN_LIMITS,
    _NON_CATALOG_OVERLAY,
    _build_known_token_limits,
)
from magi_agent.models.catalog import ModelCatalog


# (bare id, prefixed id, expected catalog value) for each formerly-drifted pair.
_DRIFT_TRIPLES = [
    ("kimi-k2p6", "fireworks/kimi-k2p6", 196_608),
    ("minimax-m2p7", "fireworks/minimax-m2p7", 196_608),
    ("gemini-3.1-pro-preview", "google/gemini-3.1-pro-preview", 786_432),
    (
        "gemini-3.1-flash-lite-preview",
        "google/gemini-3.1-flash-lite-preview",
        786_432,
    ),
]


@pytest.mark.parametrize("bare,prefixed,catalog_value", _DRIFT_TRIPLES)
def test_drift_pairs_converge_on_catalog_value(
    bare: str, prefixed: str, catalog_value: int
) -> None:
    """The four historically-drifted pairs must all resolve to the single
    catalog value (bare == prefixed == catalog)."""

    catalog = ModelCatalog.builtin()
    assert catalog.context_window(bare) == catalog_value
    assert _KNOWN_TOKEN_LIMITS[bare] == catalog_value
    assert _KNOWN_TOKEN_LIMITS[prefixed] == catalog_value
    assert _KNOWN_TOKEN_LIMITS[bare] == _KNOWN_TOKEN_LIMITS[prefixed]


def test_every_catalog_record_id_form_is_in_the_table() -> None:
    """Every id-form the catalog answers for must appear in the derived
    table with a value equal to the record's ``context_window``."""

    catalog = ModelCatalog.builtin()
    for record in catalog.all_records():
        for form in catalog.id_forms(record):
            assert form in _KNOWN_TOKEN_LIMITS, (
                f"catalog id-form {form!r} missing from _KNOWN_TOKEN_LIMITS"
            )
            assert _KNOWN_TOKEN_LIMITS[form] == record.context_window, (
                f"{form!r} window {_KNOWN_TOKEN_LIMITS[form]} != catalog "
                f"{record.context_window}"
            )


def test_gemini_alias_forms_present() -> None:
    """The catalog alias (``google`` -> ``gemini``) must fan gemini records
    out under both the ``gemini/`` and ``google/`` prefixes."""

    for prefix in ("gemini/", "google/"):
        assert f"{prefix}gemini-3.5-flash" in _KNOWN_TOKEN_LIMITS


def test_overlay_is_disjoint_from_derived_keys() -> None:
    """The non-catalog overlay may never shadow a catalog-derived key."""

    derived = _build_known_token_limits()
    overlap = set(derived) & set(_NON_CATALOG_OVERLAY)
    assert overlap == set(), (
        f"overlay keys shadow catalog-derived keys: {sorted(overlap)}"
    )


def test_overlay_has_exactly_eleven_legacy_keys() -> None:
    """The overlay is a small, explicit list of legacy/pseudo ids that the
    catalog does not carry. Locking the count prevents silent growth."""

    assert len(_NON_CATALOG_OVERLAY) == 11


def test_result_is_a_plain_mutable_dict() -> None:
    """A single-source test mutates the table in place, so the derived
    result must stay a plain mutable ``dict`` (no MappingProxyType)."""

    assert type(_KNOWN_TOKEN_LIMITS) is dict
