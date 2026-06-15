"""Track 19 PR8 — compaction-protected tool-result detection.

Mirrors OpenCode's ``PRUNE_PROTECTED_TOOLS = ["skill"]``: tool results produced
by a protected tool are excluded from context compaction (Tier-4 microcompact
and Tier-5 auto-compact) so the loaded body (e.g. an on-demand GA playbook)
survives long tasks.

A message is recognized as a protected tool result when its tool *name* — read
from any of the common tool-result name fields (``name`` / ``tool_name`` /
``toolName``), or from a ``metadata.toolName`` carried by serialized
:class:`~magi_agent.tools.result.ToolResult` records — is in
:data:`PRUNE_PROTECTED_TOOLS`. Detection keys purely on the tool name, so it is
a harmless no-op for every non-protected tool result.
"""
from __future__ import annotations

from collections.abc import Mapping

from magi_agent.context.recipe_routing_constants import SELECT_RECIPE_TOOL_NAME
from magi_agent.harness.general_automation.constants import LOAD_GA_RECIPE_TOOL_NAME


#: Tool names whose results are preserved across compaction. The on-demand GA
#: playbook loader and the cross-family ``select_recipe`` loader are the PR8
#: analogs of OpenCode's ``skill`` tool — each loads a body on demand that must
#: survive long tasks. Both are imported from import-boundary-safe constants
#: modules so this module stays import-light for the compaction engines.
PRUNE_PROTECTED_TOOLS: frozenset[str] = frozenset(
    {LOAD_GA_RECIPE_TOOL_NAME, SELECT_RECIPE_TOOL_NAME}
)

_NAME_FIELDS = ("name", "tool_name", "toolName")


def protected_tool_name(msg: Mapping[str, object]) -> str | None:
    """Return the protected tool name carried by *msg*, or ``None``."""
    for field in _NAME_FIELDS:
        value = msg.get(field)
        if isinstance(value, str) and value in PRUNE_PROTECTED_TOOLS:
            return value
    metadata = msg.get("metadata")
    if isinstance(metadata, Mapping):
        value = metadata.get("toolName")
        if isinstance(value, str) and value in PRUNE_PROTECTED_TOOLS:
            return value
    return None


def is_compaction_protected_tool_result(msg: Mapping[str, object]) -> bool:
    """True when *msg* is a tool result produced by a protected tool.

    No-op (returns ``False``) for any non-protected tool result.
    """
    return protected_tool_name(msg) is not None


__all__ = [
    "PRUNE_PROTECTED_TOOLS",
    "is_compaction_protected_tool_result",
    "protected_tool_name",
]
