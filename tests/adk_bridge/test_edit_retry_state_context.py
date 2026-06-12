"""S-C(1): edit-retry attempt state lives in the runtime-owned PerInvocationState.

The edit-retry reflection plugin used to hold a private ``self._attempts`` dict
(``dict[tuple[scope_key, tool_name], int]``). Phase 5 / seam S-C moves that
mutable per-invocation state into the runtime-owned :class:`PerInvocationState`
carried on the typed control-plane context, so a user pack authoring an
equivalent control gets the same state struct (the §1 "no privilege" keystone)
rather than hiding mutable counters in its own instance.

These tests pin:
* ``reflect_with_state`` mutates the SUPPLIED ``PerInvocationState`` (not
  plugin-private state) and returns the corrective reflection response,
* the recorded attempt is keyed (scope_key, tool_name) and cleared by
  ``clear_invocation`` (the clear-on-turn-complete hook),
* the budget (``max_attempts``) still fails closed exactly as before.
"""

from __future__ import annotations

import asyncio

from magi_agent.adk_bridge.control_plane import _EditRetryLoopControl
from magi_agent.adk_bridge.edit_retry_reflection import (
    EDIT_RETRY_REFLECTION_RESPONSE_TYPE,
    EDIT_RETRY_STATE_NAMESPACE,
    MagiEditRetryReflectionPlugin,
    scoped_state_name,
)
from magi_agent.packs.context import ControlPlaneContext, PerInvocationState

# Edit-retry namespaces its PerInvocationState scalar key by control identity so
# it never collides with the other S-C controls on a shared state; these
# direct-state assertions read the namespaced name.
_ER = scoped_state_name(EDIT_RETRY_STATE_NAMESPACE, "FileEdit")


class _Tool:
    name = "FileEdit"


class _ToolCtx:
    invocation_id = "inv-1"


def test_edit_retry_attempts_recorded_in_per_invocation_state() -> None:
    plugin = MagiEditRetryReflectionPlugin(max_attempts=2)
    state = PerInvocationState()
    # First failure -> reflection guidance, attempt=1 recorded in the SHARED state.
    out = plugin.reflect_with_state(
        state=state,
        tool=_Tool(),
        tool_args={"old_string": "x", "new_string": "y"},
        tool_context=_ToolCtx(),
        reason="old_text_not_found",
    )
    assert out is not None
    assert out["response_type"] == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
    assert out["retry_attempt"] == 1
    assert state.get_scoped("inv-1", _ER, default=0) == 1
    # Clearing the invocation drops the attempt counter (clear-on-turn-complete).
    state.clear_invocation("inv-1")
    assert state.get_scoped("inv-1", _ER, default=0) == 0


def test_edit_retry_reflect_with_state_fails_closed_at_budget() -> None:
    plugin = MagiEditRetryReflectionPlugin(max_attempts=2)
    state = PerInvocationState()
    first = plugin.reflect_with_state(
        state=state,
        tool=_Tool(),
        tool_args={"old_string": "x", "new_string": "y"},
        tool_context=_ToolCtx(),
        reason="old_text_not_found",
    )
    second = plugin.reflect_with_state(
        state=state,
        tool=_Tool(),
        tool_args={"old_string": "x", "new_string": "y"},
        tool_context=_ToolCtx(),
        reason="old_text_not_found",
    )
    # attempt 1 -> resample (inject); attempt 2 -> abort (fail closed -> None).
    assert first is not None and first["retry_attempt"] == 1
    assert second is None
    assert state.get_scoped("inv-1", _ER, default=0) == 2


def test_edit_retry_reflect_with_state_ignores_non_edit_tools() -> None:
    class _Bash:
        name = "Bash"

    plugin = MagiEditRetryReflectionPlugin(max_attempts=2)
    state = PerInvocationState()
    out = plugin.reflect_with_state(
        state=state,
        tool=_Bash(),
        tool_args={"command": "ls"},
        tool_context=_ToolCtx(),
        reason="nonzero_exit",
    )
    assert out is None
    # No counter recorded for a non-edit tool.
    assert state.get_scoped("inv-1", "Bash", default=0) == 0


def test_edit_retry_control_apply_after_tool_uses_context_state() -> None:
    """The control's typed-context entry point records attempts on
    ``ctx.per_invocation`` (the SHARED runtime state), not plugin-private state."""
    plugin = MagiEditRetryReflectionPlugin(max_attempts=2)
    ctrl = _EditRetryLoopControl(plugin)
    state = PerInvocationState()
    ctx = ControlPlaneContext.minimal(per_invocation=state)
    out = asyncio.run(
        ctrl.apply_after_tool(
            ctx,
            tool=_Tool(),
            args={"old_string": "x", "new_string": "y"},
            tool_context=_ToolCtx(),
            result={"status": "error", "error": "old_text_not_found"},
        )
    )
    assert out is not None
    assert out["response_type"] == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
    assert out["retry_attempt"] == 1
    # The shared context state owns the counter (not the plugin default state).
    assert state.get_scoped("inv-1", _ER, default=0) == 1
    assert plugin._default_state.get_scoped("inv-1", _ER, default=0) == 0


def test_edit_retry_control_apply_after_tool_resets_on_success() -> None:
    """A non-error result clears the per-tool counter on the shared state."""
    plugin = MagiEditRetryReflectionPlugin(max_attempts=3)
    ctrl = _EditRetryLoopControl(plugin)
    state = PerInvocationState()
    state.set_scoped("inv-1", _ER, 2)
    ctx = ControlPlaneContext.minimal(per_invocation=state)
    out = asyncio.run(
        ctrl.apply_after_tool(
            ctx,
            tool=_Tool(),
            args={"old_string": "x", "new_string": "y"},
            tool_context=_ToolCtx(),
            result={"status": "ok"},
        )
    )
    assert out is None
    assert state.get_scoped("inv-1", _ER, default=0) == 0


def test_edit_retry_control_apply_after_tool_falls_back_to_default_state() -> None:
    """With no per_invocation on the context, the control uses the plugin's
    default runtime state — byte-identical to the legacy after_tool path."""
    plugin = MagiEditRetryReflectionPlugin(max_attempts=2)
    ctrl = _EditRetryLoopControl(plugin)
    ctx = ControlPlaneContext.minimal()  # no per_invocation
    out = asyncio.run(
        ctrl.apply_after_tool(
            ctx,
            tool=_Tool(),
            args={"old_string": "x", "new_string": "y"},
            tool_context=_ToolCtx(),
            result={"status": "error", "error": "old_text_not_found"},
        )
    )
    assert out is not None and out["retry_attempt"] == 1
    assert plugin._default_state.get_scoped("inv-1", _ER, default=0) == 1
