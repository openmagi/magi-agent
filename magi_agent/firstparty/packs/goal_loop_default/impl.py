"""First-party loop policy provider (no privilege, typed-ctx only)."""
from __future__ import annotations

from magi_agent.packs.context import LoopPolicyProvideContext


def provide_ralph_policy(context: LoopPolicyProvideContext) -> None:
    from magi_agent.harness.goal_loop_control import decide_loop_continuation

    context.register("loop_policy:ralph@1", decide_loop_continuation)
