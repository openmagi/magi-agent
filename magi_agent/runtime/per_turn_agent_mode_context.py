"""Per-turn ``ContextVar`` carrying the request-selected agent MODE id.

Mirrors ``per_turn_goal_loop_context`` / the ``magi_per_turn_reasoning_effort``
ContextVar: the chat-routes parser publishes the per-send mode ONCE and
``runtime.message_builder._agent_mode_block`` reads it without threading the
value through every builder in between.

Precedence (see the mode design doc): an explicit per-turn selection WINS over
the operator's stored sticky default (``customize.active_agent_mode``). Default
is ``None`` ⇒ fall back to the stored active mode ⇒ byte-identical to PR-4b.

Callers MUST pair every ``set_*`` with a ``reset_*`` in a ``finally`` block so
concurrent or back-to-back turns never leak state.
"""
from __future__ import annotations

from contextvars import ContextVar, Token

_per_turn_agent_mode: ContextVar[str | None] = ContextVar(
    "magi_per_turn_agent_mode", default=None
)


def current_per_turn_agent_mode() -> str | None:
    """Return the request-selected mode id for this turn, or ``None`` if unset."""
    return _per_turn_agent_mode.get()


def set_per_turn_agent_mode(mode_id: str | None) -> Token[str | None]:
    """Publish *mode_id* for the current async task. Falsy ⇒ clears (``None``).

    Returns the reset token; callers MUST pass it to
    :func:`reset_per_turn_agent_mode` (typically in a ``finally`` block) to
    restore the prior value so back-to-back turns do not leak state.
    """
    return _per_turn_agent_mode.set(mode_id or None)


def reset_per_turn_agent_mode(token: Token[str | None]) -> None:
    """Restore the per-turn mode to its prior value."""
    _per_turn_agent_mode.reset(token)


__all__ = [
    "current_per_turn_agent_mode",
    "reset_per_turn_agent_mode",
    "set_per_turn_agent_mode",
]
