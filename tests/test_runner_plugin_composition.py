"""Regression guard: independently-flagged controls must COMPOSE (union),
not silently drop one another, on the live local runner builder.

After PR2 (goose-parity control-plane), the 3 existing plugin-backed controls
(edit-retry reflection, resilience loop guard + recovery, context compaction) are
registered as LoopControl adapters inside a single ControlPlanePlugin. Composition
is guaranteed by the ControlPlane registry rather than the runner's plugin list.

These tests mirror the original intent:
- All flags ON → all three controls registered in the plane.
- Each flag individually → exactly that control registered.
- All flags OFF → empty plane (zero controls, same as zero regression).
"""

from __future__ import annotations

import pytest

from magi_agent.adk_bridge import local_runner as lr
from magi_agent.adk_bridge.control_plane import (
    CONTROL_PLANE_PLUGIN_NAME,
    _CompactionLoopControl,
    _EditRetryLoopControl,
    _ResilienceLoopControl,
)


def _controls(monkeypatch: pytest.MonkeyPatch, **flags: str) -> list[object]:
    """Build the local runner with given env flags and return the plane's controls."""
    monkeypatch.setenv(lr.LOCAL_ADK_RUNNER_FLAG, "1")
    for key, value in flags.items():
        monkeypatch.setenv(key, value)
    bundle = lr.build_local_adk_runner()
    plane_plugin = next(
        p for p in bundle.runner.plugin_manager.plugins if p.name == CONTROL_PLANE_PLUGIN_NAME
    )
    return list(plane_plugin._p._controls)


def _control_types(controls: list[object]) -> set[type]:
    return {type(c) for c in controls}


def test_all_flags_on_registers_all_three_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    controls = _controls(
        monkeypatch,
        MAGI_EDIT_RETRY_REFLECTION_ENABLED="1",
        MAGI_LOOP_GUARD_ENABLED="1",
        MAGI_ERROR_RECOVERY_ENABLED="1",
        MAGI_CONTEXT_COMPACTION_ENABLED="1",
    )
    types = _control_types(controls)
    assert _EditRetryLoopControl in types
    assert _ResilienceLoopControl in types
    assert _CompactionLoopControl in types
    assert len(controls) == 3


def test_only_compaction_on_registers_only_compaction(monkeypatch: pytest.MonkeyPatch) -> None:
    controls = _controls(monkeypatch, MAGI_CONTEXT_COMPACTION_ENABLED="1")
    assert _control_types(controls) == {_CompactionLoopControl}


def test_only_loop_guard_on_registers_only_resilience(monkeypatch: pytest.MonkeyPatch) -> None:
    controls = _controls(monkeypatch, MAGI_LOOP_GUARD_ENABLED="1")
    assert _control_types(controls) == {_ResilienceLoopControl}


def test_all_flags_off_empty_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    for flag in (
        "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
        "MAGI_LOOP_GUARD_ENABLED",
        "MAGI_ERROR_RECOVERY_ENABLED",
        "MAGI_CONTEXT_COMPACTION_ENABLED",
    ):
        monkeypatch.delenv(flag, raising=False)
    controls = _controls(monkeypatch)
    assert controls == []


def test_single_control_plane_plugin_regardless_of_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """With all flags on, there is still exactly ONE plugin in the runner."""
    monkeypatch.setenv(lr.LOCAL_ADK_RUNNER_FLAG, "1")
    monkeypatch.setenv("MAGI_EDIT_RETRY_REFLECTION_ENABLED", "1")
    monkeypatch.setenv("MAGI_LOOP_GUARD_ENABLED", "1")
    monkeypatch.setenv("MAGI_CONTEXT_COMPACTION_ENABLED", "1")
    bundle = lr.build_local_adk_runner()
    plugins = list(bundle.runner.plugin_manager.plugins)
    assert len(plugins) == 1
    assert plugins[0].name == CONTROL_PLANE_PLUGIN_NAME
