"""U1: ``build_ambient_goal_loop_policy`` — the toggle-independent finish-the-job
baseline factory.

Ambient is the toggle-OFF path by definition, so this factory takes NO
``goal_mode_requested`` argument: existence is governed solely by the
profile-aware master flag ``MAGI_GOAL_LOOP_ENABLED`` plus a real objective. The
ONLY field that differs from the mission builder
(:func:`build_goal_loop_policy_from_request`) is ``max_turns`` (the ambient
ceiling, default 3, tunable via ``MAGI_GOAL_LOOP_AMBIENT_MAX_TURNS`` instead of
the mission ``MAGI_GOAL_LOOP_MAX_TURNS`` / 20).

This unit is INERT: nothing calls the factory yet. These tests set flags via the
``env=`` mapping the accessors accept (never mutating ``os.environ``), because
the goal-loop flags are profile-aware and a polluted shell would flip the
semantics under test (design section 14).
"""
from __future__ import annotations

import dataclasses

import pytest

from magi_agent.runtime.goal_loop_policy import (
    DEFAULT_AMBIENT_GOAL_LOOP_MAX_TURNS,
    DEFAULT_GOAL_LOOP_MAX_TURNS,
    GoalLoopPolicy,
    build_ambient_goal_loop_policy,
    build_goal_loop_policy_from_request,
)


# ---------------------------------------------------------------------------
# Existence WITHOUT any toggle input.
# ---------------------------------------------------------------------------


def test_ambient_factory_returns_policy_without_any_toggle_input() -> None:
    # There is no goal_mode_requested parameter: ambient is the toggle-off path.
    # With the master flag on and a real objective, a policy MUST be produced.
    policy = build_ambient_goal_loop_policy(
        objective="Refactor the parser and run the tests.",
        env={"MAGI_GOAL_LOOP_ENABLED": "1"},
    )
    assert isinstance(policy, GoalLoopPolicy)
    assert policy.enabled is True
    assert policy.objective == "Refactor the parser and run the tests."


def test_ambient_factory_has_no_goal_mode_parameter() -> None:
    # Guard the shape: the ambient factory must not accept goal_mode_requested,
    # so a caller cannot accidentally re-introduce the toggle gate here.
    import inspect

    params = inspect.signature(build_ambient_goal_loop_policy).parameters
    assert "goal_mode_requested" not in params
    assert set(params) == {"objective", "env"}


# ---------------------------------------------------------------------------
# None cases: flag-off and empty objective.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag_value", ["0", "false", "off", ""])
def test_ambient_factory_returns_none_when_flag_off(flag_value: str) -> None:
    # Same profile-aware master gate as the mission builder: an explicit off
    # value (or unrecognised falsey) means no ambient authority this turn.
    assert (
        build_ambient_goal_loop_policy(
            objective="do the whole task",
            env={"MAGI_GOAL_LOOP_ENABLED": flag_value},
        )
        is None
    )


def test_ambient_factory_returns_none_under_safe_profile() -> None:
    # The profile-aware accessor resolves OFF under the safe family even when the
    # flag itself is unset — a polluted-shell-proof way to prove the gate.
    assert (
        build_ambient_goal_loop_policy(
            objective="do the whole task",
            env={"MAGI_RUNTIME_PROFILE": "safe"},
        )
        is None
    )


@pytest.mark.parametrize("objective", ["", "   ", "\n\t  "])
def test_ambient_factory_returns_none_for_empty_objective(objective: str) -> None:
    # A turn with no capturable user text must behave exactly as today: no
    # ambient policy, so the driver falls through to its existing paths.
    assert (
        build_ambient_goal_loop_policy(
            objective=objective,
            env={"MAGI_GOAL_LOOP_ENABLED": "1"},
        )
        is None
    )


def test_ambient_factory_trims_objective() -> None:
    policy = build_ambient_goal_loop_policy(
        objective="  finish the migration  \n",
        env={"MAGI_GOAL_LOOP_ENABLED": "1"},
    )
    assert policy is not None
    assert policy.objective == "finish the migration"


# ---------------------------------------------------------------------------
# Ambient ceiling: default 3 and env override, distinct from the mission 20.
# ---------------------------------------------------------------------------


def test_ambient_max_turns_defaults_to_three() -> None:
    assert DEFAULT_AMBIENT_GOAL_LOOP_MAX_TURNS == 3
    policy = build_ambient_goal_loop_policy(
        objective="ok",
        env={"MAGI_GOAL_LOOP_ENABLED": "1"},
    )
    assert policy is not None
    assert policy.max_turns == 3
    # The ambient ceiling is NOT the mission ceiling.
    assert policy.max_turns != DEFAULT_GOAL_LOOP_MAX_TURNS


def test_ambient_max_turns_honors_env_override() -> None:
    policy = build_ambient_goal_loop_policy(
        objective="ok",
        env={
            "MAGI_GOAL_LOOP_ENABLED": "1",
            "MAGI_GOAL_LOOP_AMBIENT_MAX_TURNS": "5",
        },
    )
    assert policy is not None
    assert policy.max_turns == 5


def test_ambient_max_turns_ignores_the_mission_env_var() -> None:
    # The ambient ceiling reads its OWN env var only; the mission var must not
    # bleed into ambient (that would defeat the intensity separation).
    policy = build_ambient_goal_loop_policy(
        objective="ok",
        env={"MAGI_GOAL_LOOP_ENABLED": "1", "MAGI_GOAL_LOOP_MAX_TURNS": "17"},
    )
    assert policy is not None
    assert policy.max_turns == DEFAULT_AMBIENT_GOAL_LOOP_MAX_TURNS


@pytest.mark.parametrize("bad", ["0", "-3", "garbage", "", "  "])
def test_ambient_max_turns_clamps_invalid_to_default(bad: str) -> None:
    policy = build_ambient_goal_loop_policy(
        objective="ok",
        env={"MAGI_GOAL_LOOP_ENABLED": "1", "MAGI_GOAL_LOOP_AMBIENT_MAX_TURNS": bad},
    )
    assert policy is not None
    assert policy.max_turns == DEFAULT_AMBIENT_GOAL_LOOP_MAX_TURNS, bad


# ---------------------------------------------------------------------------
# Non-ceiling fields must MATCH the mission builder for the same objective.
# ---------------------------------------------------------------------------


def test_ambient_non_ceiling_fields_equal_mission_builder() -> None:
    # Drift guard: the ambient policy must equal the mission policy in every
    # field EXCEPT max_turns, for the same objective + env. If a future edit to
    # one builder forgets the other, this fails.
    env = {
        "MAGI_GOAL_LOOP_ENABLED": "1",
        "MAGI_GOAL_LOOP_JUDGE_PROVIDER": "openai",
        "MAGI_GOAL_LOOP_JUDGE_MODEL": "gpt-cheap",
        "MAGI_GOAL_LOOP_JUDGE_PARSE_FAILURES_BUDGET": "4",
    }
    objective = "Analyze the report and produce a summary."
    ambient = build_ambient_goal_loop_policy(objective=objective, env=env)
    mission = build_goal_loop_policy_from_request(
        goal_mode_requested=True, objective=objective, env=env
    )
    assert ambient is not None
    assert mission is not None

    ambient_fields = dataclasses.asdict(ambient)
    mission_fields = dataclasses.asdict(mission)
    # Only the ceiling differs.
    assert ambient_fields.pop("max_turns") == DEFAULT_AMBIENT_GOAL_LOOP_MAX_TURNS
    assert mission_fields.pop("max_turns") == DEFAULT_GOAL_LOOP_MAX_TURNS
    assert ambient_fields == mission_fields
    # Sanity: the shared judge fields actually resolved from env (not left None).
    assert ambient.judge_provider == "openai"
    assert ambient.judge_model == "gpt-cheap"
    assert ambient.judge_parse_failures_budget == 4
