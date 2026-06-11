"""Empty-response recovery — never end a turn with nothing (hermes mechanism 3).

Two failure modes leave the user with zero assistant text today:

1. *Tools-ran-but-silent*: the model executes tool calls and then returns an
   empty final response. The turn ends "ok" with no answer. The fix is one
   bounded corrective re-invocation telling the model to process the tool
   results it just produced.
2. *Iteration-budget exhaustion*: the per-turn ADK event budget is hit
   mid-task and the stream is force-completed without a final answer. The fix
   is one grace re-invocation ("produce your final answer now") with a small
   extra event allowance so the grace attempt itself is not immediately cut.

Both behaviors are one coherent feature behind one flag
(``MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED``), mirroring hermes where both live
in the same recovery block. This module holds the pure (model-free, env-free)
decision helpers so they can be unit-tested in isolation; the wiring lives at
the ``cli.engine`` re-invocation seam — the codebase-designated boundary where
goal-nudge / output-continuation / coding-repair already issue genuine second
model calls (plugin callbacks cannot re-invoke the model; see
``resilience_plugin.py``).

``config=None`` or ``enabled=False`` makes every decision ``False`` so the
engine's control flow stays byte-identical when the flag is OFF (fail-open by
construction: the helpers are pure and total).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "EmptyResponseRecoveryConfig",
    "should_recover_empty",
    "should_grace",
    "build_empty_response_message",
    "build_grace_message",
]

_DEFAULT_MAX_RECOVERIES = 1
# Extra ADK events granted to the single grace attempt after budget
# exhaustion. Added to the existing cap (event_count is cumulative across
# attempts), NOT a reset — a reset would re-break the grace attempt after one
# event.
_DEFAULT_GRACE_EVENT_ALLOWANCE = 64


@dataclass(frozen=True)
class EmptyResponseRecoveryConfig:
    """Resolved recovery policy. ``enabled=False`` makes the seam inert."""

    enabled: bool = False
    max_recoveries: int = _DEFAULT_MAX_RECOVERIES
    grace_event_allowance: int = _DEFAULT_GRACE_EVENT_ALLOWANCE


def should_recover_empty(
    config: EmptyResponseRecoveryConfig | None,
    *,
    tool_ran: bool,
    text_seen: bool,
    recoveries_used: int,
) -> bool:
    """Decide whether to re-invoke after a tools-ran-but-silent stop.

    * ``tool_ran`` — at least one tool executed during the just-finished
      attempt (a clean no-tool stop is the model's normal "nothing to add").
    * ``text_seen`` — the attempt emitted any user-visible output; if so the
      turn did not end empty and there is nothing to recover.
    * ``recoveries_used`` — corrective re-invocations already issued this turn
      (the budget guards against a tool→empty→nudge loop).
    """
    if config is None or not config.enabled:
        return False
    if not tool_ran or text_seen:
        return False
    return recoveries_used < config.max_recoveries


def should_grace(
    config: EmptyResponseRecoveryConfig | None,
    *,
    budget_exhausted: bool,
    text_seen: bool,
    graces_used: int,
) -> bool:
    """Decide whether to grant the single post-budget grace re-invocation.

    Fires at most once per turn (``graces_used == 0``), only when the event
    budget was actually hit and the cut attempt produced no user-visible
    output.
    """
    if config is None or not config.enabled:
        return False
    if not budget_exhausted or text_seen:
        return False
    return graces_used == 0


def build_empty_response_message() -> str:
    """The corrective user-role message for a tools-ran-but-silent stop."""
    return (
        "You just executed tool calls but your response contained no text. "
        "Process the tool results above and continue with your answer."
    )


def build_grace_message() -> str:
    """The user-role message for the single post-budget grace attempt."""
    return (
        "You have reached the step budget for this turn. Produce your final "
        "answer now from what you already have; do not call more tools."
    )
