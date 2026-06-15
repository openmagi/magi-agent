"""Import-boundary-safe constant for cross-family recipe routing.

Mirrors ``harness/general_automation/constants.py`` (which holds
:data:`~magi_agent.harness.general_automation.constants.LOAD_GA_RECIPE_TOOL_NAME`
for the GA load tool): a primitive constant with NO heavy transitive imports.

It lives in ``magi_agent.context`` — the same import-light package as the
compaction engines (``context/microcompact.py`` / ``context/auto_compact.py``)
and :mod:`magi_agent.context.protected_tools` — so ``protected_tools.py`` can add
``select_recipe`` to ``PRUNE_PROTECTED_TOOLS`` WITHOUT importing
``magi_agent.recipes.recipe_routing`` (whose package ``__init__`` eagerly loads
``magi_agent.recipes.compiler`` and the rest of the recipe stack, and which in
turn imports ``magi_agent.tools.*``). ``recipes/recipe_routing.py`` re-exports
this constant so existing
``from magi_agent.recipes.recipe_routing import SELECT_RECIPE_TOOL_NAME`` callers
keep working unchanged.
"""
from __future__ import annotations

#: Name of the on-demand cross-family recipe select tool (``select_recipe``).
SELECT_RECIPE_TOOL_NAME: str = "select_recipe"

__all__ = ["SELECT_RECIPE_TOOL_NAME"]
