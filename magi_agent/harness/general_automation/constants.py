"""Track 19 PR8 — import-boundary-safe constants for general_automation harness.

This module holds only primitive constants that carry NO heavy transitive
imports (no magi_agent.transport, no magi_agent.recipes.*).  Both
``context/protected_tools.py`` and ``harness/general_automation/recipe_disclosure.py``
import from here so the compaction engines (``context/microcompact.py`` /
``context/auto_compact.py``) stay import-light.
"""
from __future__ import annotations

#: Name of the on-demand GA recipe / playbook load tool (``LoadGaPlaybook``).
#: Kept here so ``context/protected_tools.py`` can reference it without
#: transitively loading ``magi_agent.transport`` or ``magi_agent.recipes.*``.
LOAD_GA_RECIPE_TOOL_NAME: str = "LoadGaPlaybook"

__all__ = ["LOAD_GA_RECIPE_TOOL_NAME"]
