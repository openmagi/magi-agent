"""Group C.3 — a pack-provided recipe is CONSUMED by the live materializer, not
catalogued only. We compose a single-pack ``RecipeSnapshot`` naming the
pack-provided recipe and assert it reaches ``plan.selected_pack_ids`` and the live
materialization ordering."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries
from magi_agent.recipes.compiler import RecipeSnapshot, build_recipe_snapshot_id
from magi_agent.recipes.materializer import RecipeMaterializer

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def test_pack_recipe_materializes_into_live_plan() -> None:
    registries, _ = load_into_registries([_FIRST_PARTY_ROOT / "recipe_authoring_static"])
    manifest = registries.recipes.resolve("recipe:authoring-static@1")
    assert manifest is not None

    snapshot = RecipeSnapshot(
        snapshotId=build_recipe_snapshot_id((manifest.pack_id,)),
        resolvedProfile={"taskType": "authoring"},
        selectedPackIds=(manifest.pack_id,),
    )
    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )
    # The pack recipe's selected pack id reaches the live plan.
    assert manifest.pack_id in plan.selected_pack_ids
    # The validators stage is materialized into the live plan ordering.
    assert "order:06-validators" in plan.materialization_order_refs
