"""PR-B wiring: ``GoalLoopPolicy`` runtime shape + factory + per-turn ContextVar.

PR-A (#835) restored the Goal-mission toggle on the composer; the chat-completions
payload now carries an explicit ``goalMode: true`` only when the user opted in.
This PR-B establishes the BACKEND plumbing the engine's clean-break judge call
(PR-C) will read:

  payload.goalMode  ─►  build_goal_loop_policy_from_request(...)
                    ─►  set_per_turn_goal_loop_policy(policy)
                    ─►  engine reads via ContextVar (PR-C)

PR-B is intentionally a NO-OP for the engine — the ContextVar is set and reset
around the turn but no clean-break judge call exists yet. That is PR-C.

Design reference: docs/plans/2026-06-21-magi-goal-loop-clean-break-judge-design.md (host repo)
"""
from __future__ import annotations

import pytest

from magi_agent.runtime.goal_loop_policy import (
    DEFAULT_GOAL_LOOP_MAX_TURNS,
    GoalLoopPolicy,
    build_goal_loop_policy_from_request,
)
from magi_agent.runtime.per_turn_goal_loop_context import (
    current_per_turn_goal_loop_policy,
    reset_per_turn_goal_loop_policy,
    set_per_turn_goal_loop_policy,
)


# ---------------------------------------------------------------------------
# Factory: build_goal_loop_policy_from_request
# ---------------------------------------------------------------------------


def test_factory_returns_none_when_goal_mode_not_requested() -> None:
    # Phase 1 opt-in: a request that did NOT set goalMode must never activate
    # the policy, even when the master flag is on. This is what makes the
    # toggle a true opt-in instead of always-on.
    assert (
        build_goal_loop_policy_from_request(
            goal_mode_requested=False,
            objective="anything",
            env={"MAGI_GOAL_LOOP_ENABLED": "1"},
        )
        is None
    )


def test_factory_returns_none_when_master_flag_disabled() -> None:
    # MAGI_GOAL_LOOP_ENABLED is the deployment kill-switch. It is now a
    # profile-aware default-ON flag, so exercise the OFF path with an explicit
    # "0" (or a safe profile): the policy never activates regardless of what the
    # request says.
    assert (
        build_goal_loop_policy_from_request(
            goal_mode_requested=True,
            objective="anything",
            env={"MAGI_GOAL_LOOP_ENABLED": "0"},
        )
        is None
    )


def test_factory_returns_none_for_empty_objective() -> None:
    # An empty objective is meaningless — the judge call (PR-C) cannot evaluate
    # "is this done?" against a blank string, and a continuation prompt anchored
    # on empty would just re-ask the agent for nothing. Treat as no-policy.
    assert (
        build_goal_loop_policy_from_request(
            goal_mode_requested=True,
            objective="   ",
            env={"MAGI_GOAL_LOOP_ENABLED": "1"},
        )
        is None
    )


def test_factory_returns_policy_when_both_request_and_flag_are_on() -> None:
    policy = build_goal_loop_policy_from_request(
        goal_mode_requested=True,
        objective="Analyze Tesla 10-K and produce a final report.",
        env={"MAGI_GOAL_LOOP_ENABLED": "1"},
    )
    assert isinstance(policy, GoalLoopPolicy)
    assert policy.enabled is True
    assert policy.objective == "Analyze Tesla 10-K and produce a final report."
    assert policy.max_turns == DEFAULT_GOAL_LOOP_MAX_TURNS
    # PR-B treats judge model selection as TBD (PR-C). The factory may leave
    # it unset on the policy; the engine fills it in from the resolved
    # provider config at judge-call time.
    assert hasattr(policy, "judge_provider")
    assert hasattr(policy, "judge_model")
    # Continuation prompt must be a non-empty string (used by PR-C re-invoke).
    assert isinstance(policy.continuation_template, str)
    assert len(policy.continuation_template.strip()) > 0


def test_factory_respects_max_turns_env_override() -> None:
    policy = build_goal_loop_policy_from_request(
        goal_mode_requested=True,
        objective="ok",
        env={"MAGI_GOAL_LOOP_ENABLED": "1", "MAGI_GOAL_LOOP_MAX_TURNS": "7"},
    )
    assert policy is not None
    assert policy.max_turns == 7


def test_factory_clamps_invalid_max_turns_to_default() -> None:
    # Garbage value, zero, or negative → fall back to default (never crash, never
    # set an unbounded loop).
    for bad in ("0", "-3", "garbage", ""):
        policy = build_goal_loop_policy_from_request(
            goal_mode_requested=True,
            objective="ok",
            env={"MAGI_GOAL_LOOP_ENABLED": "1", "MAGI_GOAL_LOOP_MAX_TURNS": bad},
        )
        assert policy is not None
        assert policy.max_turns == DEFAULT_GOAL_LOOP_MAX_TURNS, bad


def test_factory_objective_is_trimmed() -> None:
    policy = build_goal_loop_policy_from_request(
        goal_mode_requested=True,
        objective="  multi-step task  \n",
        env={"MAGI_GOAL_LOOP_ENABLED": "1"},
    )
    assert policy is not None
    assert policy.objective == "multi-step task"


# ---------------------------------------------------------------------------
# Per-turn ContextVar
# ---------------------------------------------------------------------------


def test_context_var_default_is_none() -> None:
    assert current_per_turn_goal_loop_policy() is None


def test_set_and_reset_context_var_round_trip() -> None:
    assert current_per_turn_goal_loop_policy() is None
    policy = build_goal_loop_policy_from_request(
        goal_mode_requested=True,
        objective="ok",
        env={"MAGI_GOAL_LOOP_ENABLED": "1"},
    )
    assert policy is not None
    token = set_per_turn_goal_loop_policy(policy)
    try:
        current = current_per_turn_goal_loop_policy()
        assert current is policy
        assert current.objective == "ok"
    finally:
        reset_per_turn_goal_loop_policy(token)
    # After reset, the prior None is restored — no cross-turn leak.
    assert current_per_turn_goal_loop_policy() is None


def test_set_none_clears_the_override() -> None:
    # An explicit None set is a valid "no policy this turn" — must not raise.
    token = set_per_turn_goal_loop_policy(None)
    try:
        assert current_per_turn_goal_loop_policy() is None
    finally:
        reset_per_turn_goal_loop_policy(token)


@pytest.mark.parametrize("flag_value", ["0", "false", "off", ""])
def test_factory_returns_none_for_explicit_off_flag_values(flag_value: str) -> None:
    # Mirrors the strict-truthy admission used by other MAGI_*_ENABLED flags:
    # anything outside the truthy allowlist is treated as off.
    assert (
        build_goal_loop_policy_from_request(
            goal_mode_requested=True,
            objective="ok",
            env={"MAGI_GOAL_LOOP_ENABLED": flag_value},
        )
        is None
    )
