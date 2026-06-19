"""Derive a child TurnContext from a ChildTaskRequest (spawn = recursion)."""
from __future__ import annotations

from magi_agent.runtime.child_runner_live import _child_prompt
from magi_agent.runtime.turn_context import TurnContext


def _child_memory_mode(parent_memory_mode: str, *, memory_inherit_enabled: bool) -> str:
    if not memory_inherit_enabled:
        return "incognito"
    if parent_memory_mode == "normal":
        return "read_only"  # read parent memory, never write back; never propagate 'normal'
    return parent_memory_mode  # read_only / incognito propagate as-is


def derive(
    request: object,
    *,
    parent_memory_mode: str = "incognito",
    parent_depth: int = 0,
    memory_inherit_enabled: bool = False,
    child_session_id: str,
) -> TurnContext:
    return TurnContext(
        prompt=_child_prompt(request),
        session_id=child_session_id,
        turn_id=f"{child_session_id}-t1",
        recipe=None,
        permission_cap=None,
        memory_mode=_child_memory_mode(
            parent_memory_mode, memory_inherit_enabled=memory_inherit_enabled
        ),
        # A-8 fail-closed: a derived child defaults to the deny/ask enforcement
        # mode (NOT bypass). A parent must grant more authority explicitly; this
        # composes with — and is orthogonal to — ``permission_cap``.
        permission_mode="default",
        provider=getattr(request, "provider", None),
        model=getattr(request, "model", None),
        depth=parent_depth + 1,
        budget_ms=int(getattr(request, "budget_ms", 0) or 0),
    )
