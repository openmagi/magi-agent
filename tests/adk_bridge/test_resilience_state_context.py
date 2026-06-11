"""S-C(2): resilience detectors + recovery state live in PerInvocationState.

The resilience plugin used to hold private ``self._detectors`` (one
``ToolCallLoopDetector`` per invocation scope) and ``self._recovery_state`` (one
``RecoveryAttemptState`` per scope), each swept on ``after_run`` and LRU-bounded.
Phase 5 / seam S-C moves both into the runtime-owned :class:`PerInvocationState`
carried on the typed control-plane context, so a user pack authoring an
equivalent loop guard gets the same state struct rather than hiding mutable
objects in its own instance.

These tests pin:
* ``guard_with_state`` stores the per-turn ``ToolCallLoopDetector`` as an opaque
  object on the SUPPLIED state and escalates identically (soft nudge -> hard stop),
* ``clear_invocation`` drops the detector (clear-on-turn-complete),
* the recovery classification state is recorded as an opaque object on the state,
* the LRU bound is preserved (max_scopes == _MAX_TRACKED_SCOPES).
"""

from __future__ import annotations

import asyncio

from magi_agent.adk_bridge.control_plane import _ResilienceLoopControl
from magi_agent.adk_bridge.resilience_plugin import (
    LOOP_GUARD_HARD_STATUS,
    LOOP_GUARD_RESPONSE_TYPE,
    LOOP_GUARD_SOFT_KEY,
    _MAX_TRACKED_SCOPES,
    build_resilience_plugin,
)
from magi_agent.packs.context import ControlPlaneContext, PerInvocationState


class _Tool:
    name = "Search"


class _ToolCtx:
    invocation_id = "inv-1"


def _loop_plugin():
    plugin = build_resilience_plugin(
        loop_guard_enabled=True,
        loop_guard_soft_threshold=3,
        loop_guard_hard_threshold=5,
        error_recovery_enabled=False,
    )
    assert plugin is not None
    return plugin


def test_loop_guard_detector_lives_in_per_invocation_state() -> None:
    plugin = _loop_plugin()
    state = PerInvocationState()
    args = {"query": "same"}
    last = None
    # soft_threshold=3 / hard_threshold=5: 5 identical consecutive calls -> the
    # 3rd/4th soft-nudge and the 5th hard-stops. Detector stored on the state.
    for _ in range(5):
        last = plugin.guard_with_state(
            state=state,
            tool=_Tool(),
            tool_args=args,
            tool_context=_ToolCtx(),
            result={"status": "ok", "results": ["a", "b"]},
        )
    assert last is not None
    assert last.get("response_type") == LOOP_GUARD_RESPONSE_TYPE
    assert last.get("status") == LOOP_GUARD_HARD_STATUS
    assert state.peek_object("inv-1", "loop_detector") is not None
    state.clear_invocation("inv-1")
    assert state.peek_object("inv-1", "loop_detector") is None


def test_loop_guard_soft_then_hard_via_state() -> None:
    plugin = _loop_plugin()
    state = PerInvocationState()
    args = {"query": "same"}
    outs = []
    for _ in range(5):
        outs.append(
            plugin.guard_with_state(
                state=state,
                tool=_Tool(),
                tool_args=args,
                tool_context=_ToolCtx(),
                result={"status": "ok", "results": ["a", "b"]},
            )
        )
    softs = [o for o in outs if o and o.get("loop_action") == "soft_warning"]
    hards = [o for o in outs if o and o.get("loop_action") == "hard_escalation"]
    assert softs, outs
    assert LOOP_GUARD_SOFT_KEY in softs[0]
    assert softs[0].get("status") == "ok"  # soft nudge preserves the real result
    assert hards and hards[0]["status"] == LOOP_GUARD_HARD_STATUS


def test_loop_guard_distinct_calls_never_trip_via_state() -> None:
    plugin = _loop_plugin()
    state = PerInvocationState()
    outs = []
    for i in range(6):
        outs.append(
            plugin.guard_with_state(
                state=state,
                tool=_Tool(),
                tool_args={"query": f"q{i}"},
                tool_context=_ToolCtx(),
                result={"status": "ok"},
            )
        )
    assert all(o is None for o in outs)


def test_guard_with_state_ignores_own_injected_response() -> None:
    plugin = _loop_plugin()
    state = PerInvocationState()
    out = plugin.guard_with_state(
        state=state,
        tool=_Tool(),
        tool_args={"query": "same"},
        tool_context=_ToolCtx(),
        result={"response_type": LOOP_GUARD_RESPONSE_TYPE, "status": "blocked"},
    )
    assert out is None
    # No detector created for our own response.
    assert state.peek_object("inv-1", "loop_detector") is None


def test_recovery_classification_recorded_as_object_on_default_state() -> None:
    plugin = build_resilience_plugin(
        loop_guard_enabled=False,
        error_recovery_enabled=True,
        recovery_max_attempts=3,
    )
    assert plugin is not None
    plugin._note_recovery_classification("inv-rec", _kind())
    rec = plugin._default_state.peek_object("inv-rec", "recovery_state")
    assert rec is not None
    assert rec.attempt_number == 1
    # Second classification increments the attempt number on the same object.
    plugin._note_recovery_classification("inv-rec", _kind())
    rec2 = plugin._default_state.peek_object("inv-rec", "recovery_state")
    assert rec2.attempt_number == 2


def test_resilience_default_state_lru_bound_matches_legacy_cap() -> None:
    plugin = _loop_plugin()
    assert plugin._default_state._max_scopes == _MAX_TRACKED_SCOPES


def test_resilience_control_apply_after_tool_uses_context_state() -> None:
    plugin = _loop_plugin()
    ctrl = _ResilienceLoopControl(plugin)
    state = PerInvocationState()
    ctx = ControlPlaneContext.minimal(per_invocation=state)
    args = {"query": "same"}
    last = None
    for _ in range(5):
        last = asyncio.run(
            ctrl.apply_after_tool(
                ctx,
                tool=_Tool(),
                args=args,
                tool_context=_ToolCtx(),
                result={"status": "ok"},
            )
        )
    assert last is not None and last.get("status") == LOOP_GUARD_HARD_STATUS
    # The shared context state owns the detector (not the plugin default state).
    assert state.peek_object("inv-1", "loop_detector") is not None
    assert plugin._default_state.peek_object("inv-1", "loop_detector") is None


def _kind():
    from magi_agent.runtime.error_recovery import ErrorKind

    return ErrorKind.RATE_LIMIT
