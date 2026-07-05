"""Per-turn ``ContextVar`` carrying the auto-continue INTENSITY for a turn.

The ledger-first auto-continue authority (``MAGI_GOAL_LOOP_ENABLED``, profile-
aware default-ON) is ambient for EVERY turn. The composer Goal-mission toggle no
longer switches the loop on/off; it raises the budget ceiling. That intensity
signal is per-turn (it comes off the chat-completions payload), so it rides a
ContextVar the same way the per-turn reasoning-effort and goal-loop-policy
signals do, letting the transport publish it once and the engine read it without
threading a value through every builder in between.

Default is ``False`` (ambient). Callers MUST pair every ``set_*`` with a
``reset_*`` in a ``finally`` block so back-to-back / concurrent turns never leak
state.
"""
from __future__ import annotations

from contextvars import ContextVar, Token

_per_turn_goal_mission: ContextVar[bool] = ContextVar(
    "magi_per_turn_goal_mission", default=False
)


def current_per_turn_goal_mission() -> bool:
    """Return the active per-turn mission intensity (``False`` = ambient)."""
    return _per_turn_goal_mission.get()


def set_per_turn_goal_mission(mission: bool) -> Token[bool]:
    """Publish *mission* intensity for the current async task.

    Returns the reset token; callers MUST pass it to
    :func:`reset_per_turn_goal_mission` (typically in a ``finally`` block).
    """
    return _per_turn_goal_mission.set(bool(mission))


def reset_per_turn_goal_mission(token: Token[bool]) -> None:
    """Restore the per-turn mission intensity to its prior value."""
    _per_turn_goal_mission.reset(token)


__all__ = [
    "current_per_turn_goal_mission",
    "set_per_turn_goal_mission",
    "reset_per_turn_goal_mission",
]
