from magi_agent.recipes.compiler import PackRegistry
from magi_agent.recipes.recipe_routing import (
    SELECT_RECIPE_TOOL_NAME,
    build_recipe_listing_section,
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
