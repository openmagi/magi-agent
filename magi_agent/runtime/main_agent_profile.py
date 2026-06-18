"""Orchestrator main-agent profile — toolset definitions (Seam 1a).

Pure definition module.  Nothing here is wired to any entrypoint; Tasks 4 and
5 do that.  This module provides:

* The ``ORCHESTRATOR_PROFILE`` string constant.
* ``orchestrator_tool_names()`` — the restricted set the main agent may use
  when running as orchestrator (read-only tools + SpawnAgent).
* ``apply_orchestrator_filter()`` — pure function that splits a full tool-name
  bundle into the restricted main-agent set and the full grant ceiling
  (``spawn_cap``) it may pass to spawned children.
"""
from __future__ import annotations

from magi_agent.runtime.child_toolset import READONLY_TOOL_NAMES

#: Canonical profile string for the orchestrator mode.
ORCHESTRATOR_PROFILE = "orchestrator"

#: Registered name of the spawn-agent tool (native_catalog.py:704).
_SPAWN_AGENT_TOOL_NAME = "SpawnAgent"


def orchestrator_tool_names() -> tuple[str, ...]:
    """Return the restricted toolset for the orchestrator main-agent.

    The set is the union of the read-only tool allowlist (imported from the
    single canonical source in :mod:`magi_agent.runtime.child_toolset`) and
    the SpawnAgent tool name.  No other tools are included; mutation tools
    (FileWrite, FileEdit, Bash, …) and web tools (WebSearch, …) are
    intentionally absent so all non-read, non-spawn work is forced through a
    spawned child.
    """
    return READONLY_TOOL_NAMES + (_SPAWN_AGENT_TOOL_NAME,)


def apply_orchestrator_filter(
    full_tool_names: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split ``full_tool_names`` into the orchestrator restricted set and the spawn cap.

    Parameters
    ----------
    full_tool_names:
        The complete tool-name bundle for this session, in the order it was
        assembled.

    Returns
    -------
    (restricted_names, spawn_cap_names)
        * ``restricted_names`` — members of ``full_tool_names`` that are in
          ``orchestrator_tool_names()``, preserving ``full_tool_names`` order.
          This is the intersection kept for the main agent's own toolset.
        * ``spawn_cap_names`` — ``full_tool_names`` verbatim; the full bundle
          is the ceiling the orchestrator may grant to spawned children.
    """
    allowed = frozenset(orchestrator_tool_names())
    restricted = tuple(name for name in full_tool_names if name in allowed)
    return restricted, tuple(full_tool_names)
