"""Tap test — drive a real control decision through the plane and assert the
recorder mirrored exactly that decision, with control behavior unchanged.

Uses the loop-guard seam (MagiResiliencePlugin), copied from
tests/test_resilience_plugin_wiring.py: the loop guard fires at
``after_tool_callback`` (hard stop replaces the tool result), so the tap must
capture an ``after_tool`` override while returning the SAME override to ADK.
"""

from __future__ import annotations

import asyncio

from magi_agent.adk_bridge.control_plane import (
    ControlPlane,
    _ResilienceLoopControl,
)
from magi_agent.adk_bridge.resilience_plugin import (
    LOOP_GUARD_HARD_STATUS,
    build_resilience_plugin,
)

from tests.fixtures.neutral_runtime_golden.recorder import ControlPlaneRecorder
from tests.fixtures.neutral_runtime_golden.tap import recording_plane


class _Tool:
    name = "Search"


class _Ctx:
    invocation_id = "inv-tap"


def _build_plane() -> ControlPlane:
    plugin = build_resilience_plugin(
        loop_guard_enabled=True,
        loop_guard_soft_threshold=3,
        loop_guard_hard_threshold=5,
        error_recovery_enabled=False,
    )
    assert plugin is not None
    plane = ControlPlane()
    plane.register(_ResilienceLoopControl(plugin))
    return plane


def test_tap_mirrors_after_tool_hard_stop_without_changing_behavior() -> None:
    rec = ControlPlaneRecorder()
    plane = _build_plane()
    recording_plane(plane, rec)

    async def drive():
        outs = []
        # Five identical consecutive calls -> hard stop on the 5th.
        for _ in range(5):
            outs.append(
                await plane._after_tool(
                    tool=_Tool(),
                    args={"query": "same"},
                    tool_context=_Ctx(),
                    result={"status": "ok", "results": ["a", "b"]},
                )
            )
        return outs

    outs = asyncio.run(drive())

    # Behavior unchanged: the hard stop override is still returned to ADK.
    hard = [o for o in outs if isinstance(o, dict) and o.get("status") == LOOP_GUARD_HARD_STATUS]
    assert hard, f"expected a hard-stop override, saw {outs}"

    # The recorder captured after_tool events; at least one carries an override.
    after_tool_events = [e for e in rec.events if e["kind"] == "after_tool"]
    assert after_tool_events, rec.events
    with_override = [e for e in after_tool_events if e["override"] is not None]
    assert with_override, f"expected a captured override, saw {rec.events}"
    assert with_override[0]["tool"] == "Search"


def test_tap_is_pure_observe_returns_unchanged() -> None:
    # A plane with no controls returns None and records an after_tool with no override.
    rec = ControlPlaneRecorder()
    plane = ControlPlane()
    recording_plane(plane, rec)

    out = asyncio.run(
        plane._after_tool(
            tool=_Tool(), args={"q": "x"}, tool_context=_Ctx(), result={"status": "ok"}
        )
    )
    assert out is None
    assert rec.events == [{"kind": "after_tool", "tool": "Search", "override": None}]
