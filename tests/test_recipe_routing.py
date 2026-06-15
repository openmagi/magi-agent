from magi_agent.recipes.compiler import PackRegistry, RecipePackManifest
from magi_agent.recipes.recipe_routing import (
    SELECT_RECIPE_TOOL_NAME,
    build_recipe_listing_section,
)


def _manifest(pack_id: str, *, hard_safety: bool, when_to_use: str) -> RecipePackManifest:
    # hard-safety packs carry a manifest invariant: they must be non-opt-out and
    # non-customizable (compiler._validate_safety_and_metadata_only). Set those
    # fields accordingly so the synthetic manifests validate without weakening
    # the invariant.
    return RecipePackManifest(
        packId=pack_id,
        displayName=pack_id,
        description=f"synthetic pack {pack_id}",
        whenToUse=when_to_use,
        hardSafety=hard_safety,
        optOutAllowed=not hard_safety,
        customizable=not hard_safety,
    )


def test_listing_lists_non_hard_packs_with_when_to_use_and_excludes_hard_safety():
    registry = PackRegistry.with_first_party_packs()
    section = build_recipe_listing_section(registry)
    for p in registry.values():
        if p.hard_safety:
            assert p.pack_id not in section            # hard-safety never routed
        else:
            assert p.pack_id in section                # routable pack listed
            assert p.when_to_use.split("\n")[0] in section
    assert SELECT_RECIPE_TOOL_NAME in section          # advertises the load tool


def test_listing_skips_packs_without_when_to_use():
    # a non-hard pack with empty when_to_use must not appear (defensive)
    registry = PackRegistry.with_first_party_packs()
    section = build_recipe_listing_section(registry)
    assert isinstance(section, str) and section.strip() != ""


def test_listing_excludes_hard_safety_even_with_when_to_use_and_skips_empty():
    # The two skip conditions (hard_safety, empty when_to_use) must be pinned
    # independently. A synthetic registry isolates each: the first-party packs
    # entangle them (the only hard_safety packs also have empty when_to_use).
    registry = PackRegistry((
        _manifest("test.hard", hard_safety=True, when_to_use="should never be routed"),
        _manifest("test.soft-full", hard_safety=False, when_to_use="pick me"),
        _manifest("test.soft-empty", hard_safety=False, when_to_use=""),
    ))
    section = build_recipe_listing_section(registry)
    assert "test.hard" not in section        # hard_safety excluded despite when_to_use
    assert "test.soft-full" in section
    assert "test.soft-empty" not in section
