"""LifecycleShellCommandControl — PR-F-EXEC1 per-turn shell budget plugin.

Mirrors :class:`magi_agent.adk_bridge.lifecycle_llm_call_control
.LifecycleLlmCallAuditControl` but tracks a per-(session, turn) budget for
operator-authored ``shell_command`` rule invocations rather than per-LLM-call
critic audits.

Why this plugin exists
----------------------
The shell_command consumer ships in two places (see F-EXEC1 spec):

* :mod:`magi_agent.facades` — ``before_tool_use`` + ``after_tool_use`` slots
  inside ``execute_tool_with_hooks``.
* :mod:`magi_agent.customize.lifecycle_audit` — 9 additional audit-only
  slots (pre_final / on_user_prompt_submit / on_subagent_stop /
  before_turn_start / after_turn_end / before_compaction /
  after_compaction / on_task_checkpoint / on_artifact_created).

Without a per-turn cap, a runaway rule fan-out across all 11 slots could
spawn dozens of subprocesses per turn. This module ships TWO surfaces that
share ONE state map (``_SHARED_BUDGET``) keyed by ``(session_id, turn_id)``:

1. The ADK plugin :class:`LifecycleShellCommandControl` — registers with the
   ControlPlane and warms up budget state on the first model call of a turn
   so the cap kicks in from the very first shell rule. Reads + decrements
   through the shared map.
2. A module-level :func:`shell_budget_for` accessor — the lifecycle_audit
   fan-out helpers (which do NOT have an ADK ``callback_context``) call this
   with explicit ``(session_id, turn_id)`` or fall back to the
   :data:`_ACTIVE_TURN_IDENTITY` ContextVar that
   :func:`magi_agent.runtime.governed_turn.run_governed_turn` publishes at
   the top of every governed turn. Returns ``(remaining, decrement_fn)`` so
   the SAME counter is read AND decremented across ALL 9 lifecycle slots in
   a turn — so the 6th spawn across slots short-circuits, not the 6th spawn
   within a single slot.

The facades.py before/after tool consumers (the OTHER 2 of the 11 advertised
slots) intentionally stay stateless: tool dispatch is naturally bounded by
the tool call rate AND the budget map is only meaningful when ALL slot wires
share it. The lifecycle_audit 9-slot share IS the operator-cost-ceiling
guarantee.

Honest-degrade contract
-----------------------
* When the master flag :func:`shell_command_enabled` is OFF the
  :func:`build_lifecycle_shell_command_control` builder returns ``None`` so
  the pack loader does not register a control; and :func:`shell_budget_for`
  returns ``(None, no_op_decrement)`` so the fan-out helpers behave
  byte-identically to today.
* Per-turn map is FIFO-bounded to
  :data:`MAX_TRACKED_TURNS_DEFAULT` so a long-running session never grows
  the map unboundedly. Mirrors the F-LIFE2 cap.
* Any exception in the hot path is silently logged and ignored
  (audit-only) — never raises out of the ADK callback boundary.
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from magi_agent.adk_bridge.control_plane import (
    BaseLoopControl,
    _latest_event_invocation_id,
    _non_empty_str,
)

SHELL_COMMAND_CONTROL_NAME = "magi_lifecycle_shell_command"

# Env knob for the per-turn shell command budget. Read raw (no flag_int
# wrapper) so operators can override per-turn cheaply without a new typed
# reader, mirroring the F-LIFE2 critic budget knob.
SHELL_COMMAND_BUDGET_ENV = "MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET"
DEFAULT_SHELL_COMMAND_BUDGET = 5
# FIFO cap on tracked turns so a long-running session does not grow the
# state map unboundedly. Matches the F-LIFE2 default.
MAX_TRACKED_TURNS_DEFAULT = 128

logger = logging.getLogger(__name__)


@dataclass
class _TurnBudgetState:
    """Mutable per-(session, turn) shell-command budget counter."""

    remaining: int


# Process-wide per-(session, turn) budget map. Owned by THIS module so the
# ADK plugin (registers with the ControlPlane) AND the
# lifecycle_audit fan-out helpers (no ADK callback_context available) can
# share ONE counter per turn. FIFO-bounded to MAX_TRACKED_TURNS_DEFAULT so
# a long-running session never grows the map unboundedly.
_SHARED_BUDGET: OrderedDict[tuple[str, str], _TurnBudgetState] = OrderedDict()


# ContextVar carrying the active (session_id, turn_id) for the current
# governed turn. Published by :func:`magi_agent.runtime.governed_turn
# .run_governed_turn` at the top of every turn (paired with a reset in a
# finally block); consumed by :func:`shell_budget_for` when the caller does
# not pass explicit identity. Default ``(None, None)`` ⇒ no identity ⇒
# fan-out helpers thread ``remaining_budget=None`` (no cap), preserving the
# OFF / no-identity contract.
_ACTIVE_TURN_IDENTITY: ContextVar[tuple[str | None, str | None]] = ContextVar(
    "magi_shell_budget_active_turn_identity",
    default=(None, None),
)


def set_active_turn_identity(
    session_id: str | None, turn_id: str | None
) -> Token[tuple[str | None, str | None]]:
    """Publish ``(session_id, turn_id)`` for the current async task.

    Paired with :func:`reset_active_turn_identity` (typically in a
    ``finally`` block in :func:`magi_agent.runtime.governed_turn
    .run_governed_turn`) so back-to-back turns never leak state.
    """
    return _ACTIVE_TURN_IDENTITY.set((session_id, turn_id))


def reset_active_turn_identity(
    token: Token[tuple[str | None, str | None]],
) -> None:
    """Restore the per-turn identity to its prior value."""
    _ACTIVE_TURN_IDENTITY.reset(token)


def current_active_turn_identity() -> tuple[str | None, str | None]:
    """Return the active ``(session_id, turn_id)`` for this task, or ``(None, None)``."""
    return _ACTIVE_TURN_IDENTITY.get()


def shell_budget_for(
    session_id: str | None = None,
    turn_id: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[int | None, Callable[[], None]]:
    """Resolve the per-(session, turn) shell budget and a decrement closure.

    Returns ``(remaining, decrement_fn)``:

    * ``remaining`` is the live remaining budget (the shared counter's
      current value) when the master flag is ON AND identity resolves;
      ``None`` otherwise (caller threads ``remaining_budget=None`` ⇒ no
      cap, byte-identical to today's behavior).
    * ``decrement_fn()`` decrements the shared counter by one (clamped at
      zero). When the OFF / no-identity path returns ``remaining=None``,
      the decrement_fn is a no-op so callers can call it unconditionally
      on each successful spawn.

    Identity resolution:

    1. Explicit ``session_id`` + ``turn_id`` kwargs win.
    2. Otherwise, falls back to the :func:`set_active_turn_identity`
       ContextVar that ``run_governed_turn`` publishes.
    3. If neither resolves, returns ``(None, _no_op)``.

    Fail-open: any exception returns ``(None, _no_op)``.
    """
    try:
        # Lazy import keeps an OFF-path call ~free (avoids the lifecycle_audit
        # transitive customize imports when the master flag is OFF).
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            shell_command_enabled,
        )

        if not shell_command_enabled(env=dict(env) if env is not None else None):
            return (None, _no_op_decrement)
    except Exception:
        return (None, _no_op_decrement)
    sid = session_id
    tid = turn_id
    if sid is None or tid is None:
        ctx_sid, ctx_tid = _ACTIVE_TURN_IDENTITY.get()
        sid = sid or ctx_sid
        tid = tid or ctx_tid
    if sid is None or tid is None:
        return (None, _no_op_decrement)
    key = (sid, tid)
    state = _SHARED_BUDGET.get(key)
    if state is None:
        # First observation of this (session, turn) — initialise from the
        # env knob so an operator override applied mid-session takes effect
        # on the next turn rather than the lifetime of the process.
        state = _TurnBudgetState(remaining=_parse_budget(env))
        _SHARED_BUDGET[key] = state
        # FIFO-bound to MAX_TRACKED_TURNS_DEFAULT.
        while len(_SHARED_BUDGET) > MAX_TRACKED_TURNS_DEFAULT:
            _SHARED_BUDGET.popitem(last=False)

    def _decrement() -> None:
        s = _SHARED_BUDGET.get(key)
        if s is None:
            return
        s.remaining = max(0, s.remaining - 1)

    return (state.remaining, _decrement)


def _no_op_decrement() -> None:
    """No-op decrement used on the OFF / no-identity path."""
    return None


def reset_shared_budget_for_tests() -> None:
    """Clear the shared budget map. Test-only helper.

    Used by the multi-slot integration test to ensure (session, turn)
    isolation across test cases. Never call from production code.
    """
    _SHARED_BUDGET.clear()


def _parse_budget(env: Mapping[str, str] | None = None) -> int:
    """Resolve the per-turn shell budget from the environment.

    Read raw with a fail-open ``int()`` conversion: an unparsable / negative
    value snaps to the default so a malformed env never widens the budget.
    """
    source = env if env is not None else os.environ
    raw = source.get(SHELL_COMMAND_BUDGET_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_SHELL_COMMAND_BUDGET
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_SHELL_COMMAND_BUDGET
    if parsed < 0:
        return DEFAULT_SHELL_COMMAND_BUDGET
    return parsed


class LifecycleShellCommandControl(BaseLoopControl):
    """Per-(session, turn) shell command budget tracker.

    The plugin's :meth:`remaining_budget_for` accessor returns the live
    budget for the active ``(session_id, turn_id)``; the
    :meth:`decrement_for` mutator subtracts one (clamped at zero). The
    lifecycle_audit fan-out helpers do NOT call these directly — they go
    through the module-level :func:`shell_budget_for` helper, which reads
    + decrements the SAME shared map (``_SHARED_BUDGET``). The plugin's
    own methods exist as a public surface for callers (tests, observability)
    that hold a plugin reference.

    The ADK callback hooks (:meth:`on_before_model` / :meth:`on_after_model`)
    do not invoke the runner directly; they merely warm up per-turn state on
    the first model call of a turn so the fan-out helpers see an initialised
    counter when they read it later in the turn.
    """

    name = SHELL_COMMAND_CONTROL_NAME

    def __init__(
        self,
        *,
        budget_default: int = DEFAULT_SHELL_COMMAND_BUDGET,
        max_tracked_turns: int = MAX_TRACKED_TURNS_DEFAULT,
    ) -> None:
        self._budget_default = budget_default
        self._max_tracked_turns = max_tracked_turns

    # -- public budget surface ------------------------------------------------

    def _state_for(self, session_id: str, turn_id: str) -> _TurnBudgetState:
        key = (session_id, turn_id)
        state = _SHARED_BUDGET.get(key)
        if state is None:
            state = _TurnBudgetState(remaining=self._budget_default)
            _SHARED_BUDGET[key] = state
            while len(_SHARED_BUDGET) > self._max_tracked_turns:
                _SHARED_BUDGET.popitem(last=False)
        return state

    def remaining_budget_for(
        self, session_id: str | None, turn_id: str | None
    ) -> int:
        """Return the remaining shell-command budget for this (session, turn).

        Returns the default budget when identity cannot be resolved
        (``session_id`` or ``turn_id`` is ``None``) so the caller can still
        invoke a bounded number of rules even on a path that does not
        thread ADK identity through.
        """
        if session_id is None or turn_id is None:
            return self._budget_default
        return self._state_for(session_id, turn_id).remaining

    def decrement_for(
        self, session_id: str | None, turn_id: str | None, *, by: int = 1
    ) -> int:
        """Decrement the per-turn budget by ``by`` (default 1); return remaining.

        Clamped at zero — a fan-out that over-runs the cap can still call
        decrement without producing a negative remaining (the fan-out
        helper's own ``budget_exhausted`` short-circuit applies once the
        accessor sees zero).
        """
        if session_id is None or turn_id is None:
            return self._budget_default
        state = self._state_for(session_id, turn_id)
        state.remaining = max(0, state.remaining - max(0, by))
        return state.remaining

    # -- ADK callback surface (NO-OPs) ---------------------------------------

    @staticmethod
    def _resolve_identity(
        callback_context: Any,
    ) -> tuple[str | None, str | None]:
        """Resolve ``(session_id, turn_id)`` off the ADK callback context.

        Identical to the F-LIFE2 resolver — falls back to the latest event's
        invocation_id when ``callback_context.invocation_id`` is unavailable.
        Returns ``(None, None)`` on any extraction failure.
        """
        try:
            session = getattr(callback_context, "session", None)
            session_id = _non_empty_str(getattr(session, "id", None))
            turn_id = _non_empty_str(
                getattr(callback_context, "invocation_id", None)
            )
            if turn_id is None:
                turn_id = _latest_event_invocation_id(session)
            return session_id, turn_id
        except Exception:
            return None, None

    async def on_before_model(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> Any:
        """Warm up per-turn budget state on the FIRST model call of a turn.

        NO-OP from the model's perspective (returns ``None``). The plugin
        exists primarily to keep per-turn state coherent for the
        lifecycle_audit fan-out helpers that read the counter inside the
        same turn. Reading ``_state_for`` lazily initialises the counter on
        first observation so the operator's per-turn cap kicks in from the
        very first shell rule of the turn.
        """
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                shell_command_enabled,
            )

            if not shell_command_enabled():
                return None
            session_id, turn_id = self._resolve_identity(callback_context)
            if session_id is None or turn_id is None:
                return None
            # Initialise the counter so a future remaining_budget_for() call
            # sees a stable per-turn cap (rather than re-parsing env every
            # time, which would let a mid-turn env mutation expand the cap).
            self._state_for(session_id, turn_id)
        except Exception:
            logger.debug(
                "lifecycle-shell-command before_model warmup failed; skipping",
                exc_info=True,
            )
        return None

    async def on_after_model(
        self,
        *,
        callback_context: Any,
        llm_response: Any,
    ) -> Any:
        """NO-OP — shell_command is not exposed at after_llm_call in v1."""
        return None


def build_lifecycle_shell_command_control(
    env: Mapping[str, str] | None = None,
) -> LifecycleShellCommandControl | None:
    """Build the control when F-EXEC1 master flag is ON; else ``None``.

    Returning ``None`` keeps the control plane byte-identical to today for
    OFF callers (the build helper in
    :mod:`magi_agent.adk_bridge.control_plane` skips registration). The
    per-call ``shell_command_enabled`` check is also performed inside the
    ``on_before_model`` hook so a runtime flip after registration also
    short-circuits cleanly.
    """
    try:
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            shell_command_enabled,
        )
    except Exception:
        return None
    if not shell_command_enabled(env=dict(env) if env is not None else None):
        return None
    return LifecycleShellCommandControl(budget_default=_parse_budget(env))


__all__ = [
    "DEFAULT_SHELL_COMMAND_BUDGET",
    "MAX_TRACKED_TURNS_DEFAULT",
    "SHELL_COMMAND_BUDGET_ENV",
    "SHELL_COMMAND_CONTROL_NAME",
    "LifecycleShellCommandControl",
    "build_lifecycle_shell_command_control",
    "current_active_turn_identity",
    "reset_active_turn_identity",
    "reset_shared_budget_for_tests",
    "set_active_turn_identity",
    "shell_budget_for",
]
