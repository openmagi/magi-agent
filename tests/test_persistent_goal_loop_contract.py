from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.harness.goal_loop import (
    DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH,
    GoalLoopOptOutState,
    GoalLoopOwnershipScope,
    GoalLoopParticipantScope,
    GoalLoopPolicy,
    GoalLoopSpawnDepthPolicy,
    build_goal_loop_policy,
    validate_goal_loop_spawn_depth,
)


def test_default_goal_loop_policy_is_disabled_and_traffic_free() -> None:
    policy = build_goal_loop_policy()
    dumped = policy.model_dump(by_alias=True)

    assert dumped["featureKey"] == "persistent-goal-loop"
    assert dumped["enabled"] is False
    assert dumped["schedulingEnabled"] is False
    assert dumped["allowBackgroundResume"] is False
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False
    assert dumped["optOut"]["optedOut"] is False
    assert dumped["ownershipScope"]["persistenceOwner"] == "main"
    assert dumped["ownershipScope"]["schedulingOwner"] == "main"
    assert dumped["ownershipScope"]["childAgentsMayParticipate"] is True
    assert dumped["ownershipScope"]["hardSafetyScope"] == ("main", "child")
    assert dumped["spawnDepthPolicy"]["minDepth"] == 0
    assert dumped["spawnDepthPolicy"]["maxDepth"] == DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH


def test_goal_loop_policy_accepts_snake_and_camel_aliases_and_dumps_aliases() -> None:
    camel = GoalLoopPolicy.model_validate(
        {
            "featureKey": "persistent-goal-loop",
            "enabled": False,
            "allowBackgroundResume": False,
            "schedulingEnabled": False,
            "ownershipScope": {
                "persistenceOwner": "main",
                "schedulingOwner": "main",
                "childAgentsMayParticipate": True,
                "hardSafetyScope": ["main", "child"],
            },
            "spawnDepthPolicy": {"minDepth": 0, "maxDepth": 2},
            "optOut": {"optedOut": False},
        }
    )
    snake = GoalLoopPolicy(
        feature_key="persistent-goal-loop",
        enabled=False,
        allow_background_resume=False,
        scheduling_enabled=False,
        ownership_scope=GoalLoopOwnershipScope(
            persistence_owner="main",
            scheduling_owner="main",
            child_agents_may_participate=True,
            hard_safety_scope=("main", "child"),
        ),
        spawn_depth_policy=GoalLoopSpawnDepthPolicy(min_depth=0, max_depth=2),
        opt_out=GoalLoopOptOutState(opted_out=False),
    )

    assert camel == snake
    dumped = snake.model_dump(by_alias=True)
    assert dumped["allowBackgroundResume"] is False
    assert dumped["ownershipScope"]["hardSafetyScope"] == ("main", "child")
    assert dumped["spawnDepthPolicy"]["maxDepth"] == 2


@pytest.mark.parametrize("extra_field", ("runnerAttached", "route"))
def test_goal_loop_policy_rejects_unexpected_runtime_fields(extra_field: str) -> None:
    payload: dict[str, object] = {
        "featureKey": "persistent-goal-loop",
        "enabled": False,
        "schedulingEnabled": False,
        "allowBackgroundResume": False,
        extra_field: False,
    }

    with pytest.raises(ValidationError):
        GoalLoopPolicy.model_validate(payload)

    with pytest.raises(ValidationError):
        GoalLoopOwnershipScope.model_validate(
            {
                "persistenceOwner": "main",
                "schedulingOwner": "main",
                "childAgentsMayParticipate": True,
                "iterationParticipants": ["main", "child"],
                "hardSafetyScope": ["main", "child"],
                extra_field: False,
            }
        )


def test_goal_loop_policy_is_immutable_and_uses_defensive_nested_copies() -> None:
    scope = GoalLoopOwnershipScope()
    policy = build_goal_loop_policy(ownership_scope=scope)

    with pytest.raises(ValidationError):
        policy.enabled = True  # type: ignore[misc]

    mutated_scope = scope.model_copy(update={"hard_safety_scope": ("main",)})

    assert mutated_scope.hard_safety_scope == ("main",)
    assert policy.ownership_scope.hard_safety_scope == ("main", "child")


@pytest.mark.parametrize(
    "kwargs",
    (
        {
            "opt_out": GoalLoopOptOutState().model_copy(
                update={"opted_out": True, "disables_scheduling": False}
            )
        },
        {
            "ownership_scope": GoalLoopOwnershipScope().model_copy(
                update={"hard_safety_scope": ("main",)}
            )
        },
        {
            "spawn_depth_policy": GoalLoopSpawnDepthPolicy().model_copy(
                update={"min_depth": 2, "max_depth": 1}
            )
        },
    ),
)
def test_build_goal_loop_policy_revalidates_nested_model_copy_updates(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        build_goal_loop_policy(**kwargs)


def test_opt_out_forces_disabled_scheduling_and_background_resume_but_keeps_hard_safety() -> None:
    policy = build_goal_loop_policy(
        enabled=True,
        scheduling_enabled=True,
        allow_background_resume=True,
        opt_out=GoalLoopOptOutState(
            opted_out=True,
            disabled_reason="bot config disabled persistent goal loop",
        ),
    )
    dumped = policy.model_dump(by_alias=True)

    assert dumped["optOut"]["optedOut"] is True
    assert dumped["enabled"] is False
    assert dumped["schedulingEnabled"] is False
    assert dumped["allowBackgroundResume"] is False
    assert dumped["ownershipScope"]["hardSafetyScope"] == ("main", "child")


def test_child_agents_can_participate_but_cannot_own_persistence_or_scheduling() -> None:
    child = GoalLoopParticipantScope(agent_scope="child", spawn_depth=1)

    assert child.agent_scope == "child"
    assert child.spawn_depth == 1
    assert child.may_participate_in_iteration is True
    assert child.may_own_scheduling is False
    assert child.hard_safety_applies is True

    with pytest.raises(ValidationError, match="child agents cannot own scheduling"):
        GoalLoopParticipantScope(
            agentScope="child",
            spawnDepth=1,
            mayOwnScheduling=True,
        )

    with pytest.raises(ValidationError, match="main participants must use spawnDepth=0"):
        GoalLoopParticipantScope(agentScope="main", spawnDepth=1)

    with pytest.raises(ValidationError, match="child participants must use spawnDepth greater than 0"):
        GoalLoopParticipantScope(agentScope="child", spawnDepth=0)

    with pytest.raises(ValidationError, match="persistenceOwner must be main"):
        GoalLoopOwnershipScope(persistenceOwner="child")

    with pytest.raises(ValidationError, match="schedulingOwner must be main"):
        GoalLoopOwnershipScope(schedulingOwner="child")


def test_hard_safety_scope_must_cover_main_and_child_even_when_opted_out() -> None:
    with pytest.raises(ValidationError, match="hardSafetyScope must include main and child"):
        GoalLoopOwnershipScope(hardSafetyScope=("main",))

    with pytest.raises(ValidationError, match="hardSafetyScope must include main and child"):
        GoalLoopParticipantScope(
            agentScope="main",
            spawnDepth=0,
            hardSafetyApplies=False,
        )


def test_spawn_depth_policy_rejects_invalid_ranges_and_depths() -> None:
    policy = GoalLoopSpawnDepthPolicy()

    assert policy.min_depth == 0
    assert policy.max_depth == DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH
    assert validate_goal_loop_spawn_depth(0, policy=policy) == 0
    assert validate_goal_loop_spawn_depth(DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH, policy=policy) == (
        DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH
    )

    with pytest.raises(ValueError, match="spawn depth must be between 0 and 2"):
        validate_goal_loop_spawn_depth(3, policy=policy)

    with pytest.raises(ValueError, match="spawn depth must be between 0 and 2"):
        validate_goal_loop_spawn_depth(-1, policy=policy)

    with pytest.raises(ValidationError, match="minDepth must be non-negative"):
        GoalLoopSpawnDepthPolicy(minDepth=-1, maxDepth=2)

    with pytest.raises(ValidationError, match="maxDepth must be greater than or equal to minDepth"):
        GoalLoopSpawnDepthPolicy(minDepth=2, maxDepth=1)


def test_goal_loop_import_boundary_does_not_load_adk_runner_routes_dispatcher_or_hookbus() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.harness.goal_loop")
forbidden_prefixes = ("google.adk",)
forbidden_modules = (
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.tool_adapter",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.tools.dispatcher",
    "magi_agent.hooks.bus",
)
loaded = [
    module
    for module in sys.modules
    if module.startswith(forbidden_prefixes) or module in forbidden_modules
]
if loaded:
    raise AssertionError(f"goal_loop import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
