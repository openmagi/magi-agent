"""F-LIFE5 — Self Improvement recipe → pack id mapping.

Before F-LIFE5 the catalog's ``RECIPE_ID_TO_PACK_IDS`` had no entry for
``self_improvement`` (an explicit ``# leave unmapped (no-op)`` comment),
so the dashboard Recipes tab showed the toggle as ``packIds: []`` and
disabling it had zero runtime effect.

This test pins the wire-up:

* the catalog entry resolves to at least one real first-party pack id;
* the resolved pack id exists in ``PackRegistry.with_first_party_packs()``
  (so ``cli.real_runner._disabled_recipe_pack_refs`` can subtract refs);
* the recipe row built into the dashboard payload carries the same
  non-empty ``packIds`` list (so the UI stops flagging it as a UI-only
  toggle).
"""

from __future__ import annotations

from magi_agent.customize.catalog import (
    RECIPE_ID_TO_PACK_IDS,
    RECIPES,
    pack_ids_for_recipe,
)
from magi_agent.recipes.compiler import PackRegistry


SELF_IMPROVEMENT_RECIPE_ID = "self_improvement"
EXPECTED_PACK_ID = "openmagi.self-improvement"


def test_self_improvement_recipe_id_is_mapped_to_non_empty_pack_ids() -> None:
    pack_ids = pack_ids_for_recipe(SELF_IMPROVEMENT_RECIPE_ID)

    assert isinstance(pack_ids, tuple)
    assert pack_ids, (
        "F-LIFE5: self_improvement recipe must map to at least one real "
        "RecipePackManifest id so the dashboard toggle has runtime effect."
    )
    assert EXPECTED_PACK_ID in pack_ids


def test_self_improvement_mapping_is_recorded_in_catalog_dict() -> None:
    # The dict-level lookup is the source of truth the loader uses; pin it
    # explicitly so a future refactor cannot silently re-strand the toggle.
    assert SELF_IMPROVEMENT_RECIPE_ID in RECIPE_ID_TO_PACK_IDS
    assert EXPECTED_PACK_ID in RECIPE_ID_TO_PACK_IDS[SELF_IMPROVEMENT_RECIPE_ID]


def test_self_improvement_pack_is_registered_in_first_party_pack_registry() -> None:
    # If the pack id is not registered, ``_disabled_recipe_pack_refs`` skips it
    # silently — toggling the recipe would still be a no-op. Pin both ends.
    registry = PackRegistry.with_first_party_packs()
    assert EXPECTED_PACK_ID in registry.pack_ids

    pack = registry.get(EXPECTED_PACK_ID)
    # Default-off — operator must explicitly enable.
    assert pack.default_enabled is False
    # The pack MUST expose validator/evidence refs so the opt-out subtraction
    # has something to remove; otherwise the toggle is wired but inert.
    assert pack.validator_refs, (
        "self-improvement pack must declare validator_refs so the runtime "
        "opt-out subtraction can drop them when the recipe is disabled."
    )


def test_self_improvement_recipe_row_carries_real_pack_ids() -> None:
    # The Recipes tab payload (``_recipe_entries``) attaches the pack id list
    # to each row so the frontend can disable the toggle / drop the "no live
    # effect" badge. We don't import _recipe_entries directly (it's a private
    # helper) — instead we re-derive the row the same way and assert the
    # mapping flows through.
    self_improvement_rows = [
        recipe for recipe in RECIPES if recipe["id"] == SELF_IMPROVEMENT_RECIPE_ID
    ]
    assert len(self_improvement_rows) == 1

    row = self_improvement_rows[0]
    pack_ids = list(pack_ids_for_recipe(row["id"]))
    assert pack_ids, (
        "Recipes tab row for self_improvement must carry non-empty packIds "
        "or the F-UX10 toggle stays a UI-only label."
    )
    assert EXPECTED_PACK_ID in pack_ids
