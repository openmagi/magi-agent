"""Cross-family description-based recipe routing — generalizes the GA-only
progressive-disclosure listing (harness/general_automation/recipe_disclosure.py)
to ALL non-hard_safety packs. A pure listing builder; the select tool + wiring
land in later tasks."""
from __future__ import annotations

from magi_agent.recipes.compiler import PackRegistry

SELECT_RECIPE_TOOL_NAME = "select_recipe"


def build_recipe_listing_section(registry: PackRegistry) -> str:
    lines = [
        "## Available recipes (load on demand)",
        (
            "The following recipes are available. Each line is name + when to use. "
            f"Call `{SELECT_RECIPE_TOOL_NAME}` with a `pack_id` to load and select a "
            "recipe before acting. You may select MULTIPLE (call it once per recipe) "
            "or NONE if none apply."
        ),
    ]
    for pack in registry.values():
        if pack.hard_safety or not pack.when_to_use.strip():
            continue
        lines.append(
            f"- **{pack.display_name}** (`{pack.pack_id}`) — When to use: {pack.when_to_use}"
        )
    return "\n".join(lines)
