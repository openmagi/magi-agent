"""Per-turn ``ContextVar`` carrying the active :class:`GoalLoopPolicy`.

Mirrors the ``magi_per_turn_reasoning_effort`` ContextVar in
:mod:`magi_agent.cli.real_runner` so the chat-routes parser can publish the
per-turn policy ONCE and the engine (PR-C) reads it without threading the
value through every builder in between.

Default is ``None`` ⇒ no policy ⇒ engine path is byte-identical to today
(PR-C will gate the clean-break judge call on this being non-None).

Callers are required to pair every ``set_*`` with a corresponding ``reset_*``
in a ``finally`` block so concurrent or sequential turns never leak state.
"""
from __future__ import annotations

from contextvars import ContextVar, Token

from magi_agent.runtime.goal_loop_policy import GoalLoopPolicy

_per_turn_goal_loop_policy: ContextVar[GoalLoopPolicy | None] = ContextVar(
    "magi_per_turn_goal_loop_policy", default=None
)


def current_per_turn_goal_loop_policy() -> GoalLoopPolicy | None:
    """Return the active per-turn policy, or ``None`` if not set."""
    return _per_turn_goal_loop_policy.get()


def set_per_turn_goal_loop_policy(
    policy: GoalLoopPolicy | None,
) -> Token[GoalLoopPolicy | None]:
    """Publish *policy* for the current async task. ``None`` clears.

    Returns the ContextVar reset token; callers MUST pass it to
    :func:`reset_per_turn_goal_loop_policy` (typically in a ``finally`` block)
    to restore the prior value so back-to-back turns do not leak state.
    """
    return _per_turn_goal_loop_policy.set(policy)


def reset_per_turn_goal_loop_policy(
    token: Token[GoalLoopPolicy | None],
) -> None:
    """Restore the per-turn policy to its prior value."""
    _per_turn_goal_loop_policy.reset(token)


__all__ = [
    "current_per_turn_goal_loop_policy",
    "set_per_turn_goal_loop_policy",
    "reset_per_turn_goal_loop_policy",
]
