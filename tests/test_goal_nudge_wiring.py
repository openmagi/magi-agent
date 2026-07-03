"""Tests for PR4 (cluster 03 C4) — production wiring of GoalNudge (TDD).

The engine ``MagiEngineDriver`` already accepts a ``goal_nudge`` parameter and
its ``_drive`` state machine is covered by ``tests/cli/test_engine_goal_nudge``.
What was missing is the *production* wiring: ``cli.wiring`` never constructed a
``GoalNudge`` from env, so the serve/CLI engine always received ``goal_nudge=None``.

These tests pin the env→``GoalNudge | None`` builder and the injection into
``build_headless_runtime`` so the flag actually reaches the engine.

Design contract (per cluster-03 spec):
- ``MAGI_GOAL_NUDGE_ENABLED`` default OFF → builder returns ``None`` →
  engine receives ``goal_nudge=None`` (byte-identical to pre-PR4).
- When ON, default ``mode="goal"`` (NOT grind).
- ``MAGI_GOAL_NUDGE_MODE`` selects goal|grind.
- ``MAGI_GOAL_NUDGE_MAX`` caps re-invocations (anti-infinite-loop guard).
- ``MAGI_GOAL_NUDGE_GOAL`` supplies the objective text (sensible default).
"""

from __future__ import annotations

import pytest

from magi_agent.cli.goal_nudge_wiring import build_goal_nudge_from_env
from magi_agent.runtime.goal_nudge import GoalNudge


def test_disabled_when_off_returns_none() -> None:
    assert build_goal_nudge_from_env(env={"MAGI_GOAL_NUDGE_ENABLED": "0"}) is None


@pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "  Off  ", ""])
def test_explicit_falsy_returns_none(falsy: str) -> None:
    assert build_goal_nudge_from_env(env={"MAGI_GOAL_NUDGE_ENABLED": falsy}) is None


def test_enabled_builds_goal_nudge_default_mode_is_goal() -> None:
    nudge = build_goal_nudge_from_env(env={"MAGI_GOAL_NUDGE_ENABLED": "1"})
    assert isinstance(nudge, GoalNudge)
    # Open-decision: default mode is "goal" (conservative), NOT "grind".
    assert nudge.mode == "goal"
    assert nudge.goal  # non-empty default objective


def test_mode_grind_opt_in() -> None:
    nudge = build_goal_nudge_from_env(
        env={"MAGI_GOAL_NUDGE_ENABLED": "1", "MAGI_GOAL_NUDGE_MODE": "grind"}
    )
    assert nudge is not None
    assert nudge.mode == "grind"


def test_invalid_mode_falls_back_to_goal() -> None:
    nudge = build_goal_nudge_from_env(
        env={"MAGI_GOAL_NUDGE_ENABLED": "1", "MAGI_GOAL_NUDGE_MODE": "bogus"}
    )
    assert nudge is not None
    assert nudge.mode == "goal"


def test_max_nudges_from_env() -> None:
    nudge = build_goal_nudge_from_env(
        env={"MAGI_GOAL_NUDGE_ENABLED": "1", "MAGI_GOAL_NUDGE_MAX": "5"}
    )
    assert nudge is not None
    assert nudge.max_nudges == 5


def test_invalid_max_falls_back_to_default() -> None:
    nudge = build_goal_nudge_from_env(
        env={"MAGI_GOAL_NUDGE_ENABLED": "1", "MAGI_GOAL_NUDGE_MAX": "not-a-number"}
    )
    assert nudge is not None
    assert nudge.max_nudges == 3  # GoalNudge default


def test_negative_max_falls_back_to_default() -> None:
    nudge = build_goal_nudge_from_env(
        env={"MAGI_GOAL_NUDGE_ENABLED": "1", "MAGI_GOAL_NUDGE_MAX": "-2"}
    )
    assert nudge is not None
    assert nudge.max_nudges == 3


def test_goal_text_from_env() -> None:
    nudge = build_goal_nudge_from_env(
        env={
            "MAGI_GOAL_NUDGE_ENABLED": "1",
            "MAGI_GOAL_NUDGE_GOAL": "Ship the release",
        }
    )
    assert nudge is not None
    assert nudge.goal == "Ship the release"


def test_default_env_is_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_GOAL_NUDGE_ENABLED", "0")
    assert build_goal_nudge_from_env() is None
    monkeypatch.setenv("MAGI_GOAL_NUDGE_ENABLED", "1")
    nudge = build_goal_nudge_from_env()
    assert isinstance(nudge, GoalNudge)


def test_build_headless_runtime_injects_goal_nudge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flag must actually reach the constructed engine."""
    from magi_agent.cli.wiring import build_headless_runtime

    monkeypatch.setenv("MAGI_GOAL_NUDGE_ENABLED", "1")
    monkeypatch.setenv("MAGI_GOAL_NUDGE_MODE", "grind")

    rt = build_headless_runtime(runner=object())
    nudge = rt.engine._goal_nudge
    assert isinstance(nudge, GoalNudge)
    assert nudge.mode == "grind"


def test_build_headless_runtime_off_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from magi_agent.cli.wiring import build_headless_runtime

    monkeypatch.setenv("MAGI_GOAL_NUDGE_ENABLED", "0")

    rt = build_headless_runtime(runner=object())
    assert rt.engine._goal_nudge is None
