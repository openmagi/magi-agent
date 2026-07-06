"""U5 wiring: ambient factory DI + goal_nudge supersession (design 6.3 / 6.5).

``build_headless_runtime`` builds the ambient ``GoalLoopPolicy`` factory ONCE
(env-pure ctor) and passes ``goal_nudge=None`` whenever the goal loop resolves ON,
so the legacy per-turn nudge is structurally dead under the unified ladder and only
survives as the goal-loop-OFF escape hatch.

Covers the U5 row cases:
  (d) SpawnAgent child (``auto_continue_allowed=False``): NEVER synthesizes AND no
      longer receives a profile-ON nudge.
  (e) explicit goal-loop-OFF + nudge-ON: nudge escape hatch stays intact.
plus the ambient DI presence and the default factory behavior (it calls
``build_ambient_goal_loop_policy``, yielding the ambient ceiling).
"""
from __future__ import annotations

from typing import Any

import pytest

from magi_agent.cli.wiring import build_headless_runtime
from magi_agent.runtime.goal_loop_policy import (
    DEFAULT_AMBIENT_GOAL_LOOP_MAX_TURNS,
    GoalLoopPolicy,
)

from tests.cli.test_engine_goal_pause import FakeRunner

# Every MAGI_* knob these tests resolve is cleared so an exported shell env
# (Kevin's MAGI_* exports) cannot give a false green.
_HERMETIC_KEYS = (
    "MAGI_GOAL_LOOP_ENABLED",
    "MAGI_GOAL_NUDGE_ENABLED",
    "MAGI_GOAL_LOOP_AMBIENT_MAX_TURNS",
    "MAGI_RUNTIME_PROFILE",
    "MAGI_AGENT_LOCAL_FULL_RUNTIME_DEFAULTS",
)


@pytest.fixture
def hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _HERMETIC_KEYS:
        monkeypatch.delenv(key, raising=False)
    return None


def _build(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, **kwargs: Any) -> Any:
    rt = build_headless_runtime(
        runner=FakeRunner(),
        session_id="s1",
        cwd=str(tmp_path),
        **kwargs,
    )
    return rt.engine


# --------------------------------------------------------------------------- #
# DI presence + default factory behavior (design 6.3)                          #
# --------------------------------------------------------------------------- #


class TestAmbientFactoryDI:
    def test_factory_wired_and_nudge_superseded_when_loop_on(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, hermetic_env: None
    ) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        engine = _build(monkeypatch, tmp_path)
        assert engine._ambient_goal_policy_factory is not None
        # goal_nudge is superseded whenever the goal loop resolves ON (KD-6).
        assert engine._goal_nudge is None
        # The default factory calls build_ambient_goal_loop_policy -> a real
        # ambient policy at the AMBIENT ceiling, keyed on the captured objective.
        policy = engine._ambient_goal_policy_factory("Do the whole task")
        assert isinstance(policy, GoalLoopPolicy)
        assert policy.objective == "Do the whole task"
        assert policy.max_turns == DEFAULT_AMBIENT_GOAL_LOOP_MAX_TURNS
        # Empty objective -> None (behave exactly as today).
        assert engine._ambient_goal_policy_factory("   ") is None

    def test_ambient_ceiling_env_override_flows_through_default_factory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, hermetic_env: None
    ) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        monkeypatch.setenv("MAGI_GOAL_LOOP_AMBIENT_MAX_TURNS", "5")
        engine = _build(monkeypatch, tmp_path)
        policy = engine._ambient_goal_policy_factory("Do the whole task")
        assert policy is not None
        assert policy.max_turns == 5


# --------------------------------------------------------------------------- #
# (c-wiring) safe / explicit flag-0 -> no factory                              #
# --------------------------------------------------------------------------- #


class TestGoalLoopOffNoFactory:
    def test_explicit_flag_zero_leaves_factory_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, hermetic_env: None
    ) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "0")
        engine = _build(monkeypatch, tmp_path)
        assert engine._ambient_goal_policy_factory is None


# --------------------------------------------------------------------------- #
# (d) SpawnAgent child: never synthesizes AND never nudged                      #
# --------------------------------------------------------------------------- #


class TestChildContainment:
    def test_child_gets_no_ambient_factory_and_no_nudge(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, hermetic_env: None
    ) -> None:
        # Goal loop ON at the deployment, but this is a contained child
        # (auto_continue_allowed=False). It must NEITHER synthesize (factory None,
        # because auto_continue_enabled = is_goal_loop_enabled() AND
        # auto_continue_allowed) NOR receive a profile-ON nudge (goal_nudge None,
        # gated on the ENV master, not auto_continue_enabled).
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        monkeypatch.setenv("MAGI_GOAL_NUDGE_ENABLED", "1")
        engine = _build(monkeypatch, tmp_path, auto_continue_allowed=False)
        assert engine._ambient_goal_policy_factory is None
        assert engine._goal_nudge is None


# --------------------------------------------------------------------------- #
# (e) explicit goal-loop-OFF + nudge-ON: escape hatch intact                    #
# --------------------------------------------------------------------------- #


class TestNudgeEscapeHatch:
    def test_loop_off_nudge_on_keeps_the_nudge(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, hermetic_env: None
    ) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "0")
        monkeypatch.setenv("MAGI_GOAL_NUDGE_ENABLED", "1")
        engine = _build(monkeypatch, tmp_path)
        # No ambient synthesis authority, but the legacy nudge stays live.
        assert engine._ambient_goal_policy_factory is None
        assert engine._goal_nudge is not None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
