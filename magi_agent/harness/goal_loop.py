from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


GOAL_LOOP_FEATURE_KEY = "persistent-goal-loop"
DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH = 2

GoalLoopAgentScope = Literal["main", "child"]


class GoalLoopOptOutState(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    opted_out: bool = Field(default=False, alias="optedOut")
    disabled_reason: str | None = Field(default=None, alias="disabledReason")
    disables_scheduling: bool = Field(default=True, alias="disablesScheduling")
    disables_background_resume: bool = Field(
        default=True,
        alias="disablesBackgroundResume",
    )

    @model_validator(mode="after")
    def _validate_opt_out_disables_runtime_policy(self) -> GoalLoopOptOutState:
        if self.opted_out and not (
            self.disables_scheduling and self.disables_background_resume
        ):
            raise ValueError("goal loop opt-out must disable scheduling and background resume")
        return self


class GoalLoopOwnershipScope(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    persistence_owner: GoalLoopAgentScope = Field(default="main", alias="persistenceOwner")
    scheduling_owner: GoalLoopAgentScope = Field(default="main", alias="schedulingOwner")
    child_agents_may_participate: bool = Field(
        default=True,
        alias="childAgentsMayParticipate",
    )
    iteration_participants: tuple[GoalLoopAgentScope, ...] = Field(
        default=("main", "child"),
        alias="iterationParticipants",
    )
    hard_safety_scope: tuple[GoalLoopAgentScope, ...] = Field(
        default=("main", "child"),
        alias="hardSafetyScope",
    )

    @model_validator(mode="after")
    def _validate_goal_loop_ownership(self) -> GoalLoopOwnershipScope:
        if self.persistence_owner != "main":
            raise ValueError("persistenceOwner must be main")
        if self.scheduling_owner != "main":
            raise ValueError("schedulingOwner must be main")
        if not self.child_agents_may_participate:
            raise ValueError("childAgentsMayParticipate must be true")
        if {"main", "child"} - set(self.iteration_participants):
            raise ValueError("iterationParticipants must include main and child")
        if {"main", "child"} - set(self.hard_safety_scope):
            raise ValueError("hardSafetyScope must include main and child")
        return self


class GoalLoopSpawnDepthPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    min_depth: int = Field(default=0, alias="minDepth")
    max_depth: int = Field(default=DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH, alias="maxDepth")
    default_depth: int = Field(default=0, alias="defaultDepth")

    @model_validator(mode="after")
    def _validate_depth_range(self) -> GoalLoopSpawnDepthPolicy:
        if self.min_depth < 0:
            raise ValueError("minDepth must be non-negative")
        if self.max_depth < self.min_depth:
            raise ValueError("maxDepth must be greater than or equal to minDepth")
        if not self.min_depth <= self.default_depth <= self.max_depth:
            raise ValueError("defaultDepth must be between minDepth and maxDepth")
        return self


class GoalLoopParticipantScope(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    agent_scope: GoalLoopAgentScope = Field(alias="agentScope")
    spawn_depth: int = Field(alias="spawnDepth")
    may_participate_in_iteration: bool = Field(
        default=True,
        alias="mayParticipateInIteration",
    )
    may_own_scheduling: bool = Field(default=False, alias="mayOwnScheduling")
    hard_safety_applies: bool = Field(default=True, alias="hardSafetyApplies")

    @model_validator(mode="after")
    def _validate_participant_scope(self) -> GoalLoopParticipantScope:
        if self.spawn_depth < 0:
            raise ValueError("spawnDepth must be non-negative")
        if self.agent_scope == "main" and self.spawn_depth != 0:
            raise ValueError("main participants must use spawnDepth=0")
        if self.agent_scope == "child" and self.spawn_depth <= 0:
            raise ValueError("child participants must use spawnDepth greater than 0")
        if not self.may_participate_in_iteration:
            raise ValueError("goal loop participants must be iteration-scoped")
        if self.agent_scope == "child" and self.may_own_scheduling:
            raise ValueError("child agents cannot own scheduling")
        if not self.hard_safety_applies:
            raise ValueError("hardSafetyScope must include main and child")
        return self


class GoalLoopPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    feature_key: Literal["persistent-goal-loop"] = Field(
        default=GOAL_LOOP_FEATURE_KEY,
        alias="featureKey",
    )
    enabled: bool = False
    scheduling_enabled: bool = Field(default=False, alias="schedulingEnabled")
    allow_background_resume: bool = Field(default=False, alias="allowBackgroundResume")
    opt_out: GoalLoopOptOutState = Field(
        default_factory=GoalLoopOptOutState,
        alias="optOut",
    )
    ownership_scope: GoalLoopOwnershipScope = Field(
        default_factory=GoalLoopOwnershipScope,
        alias="ownershipScope",
    )
    spawn_depth_policy: GoalLoopSpawnDepthPolicy = Field(
        default_factory=GoalLoopSpawnDepthPolicy,
        alias="spawnDepthPolicy",
    )
    #: LOCKED authority — model_copy(update={"traffic_attached": True}) is coerced to
    #: False by the field_validator below, so forged upgrades via model_copy are blocked.
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    #: LOCKED authority — same enforcement as traffic_attached (see field_validator).
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @field_validator("traffic_attached", "execution_attached", mode="before")
    @classmethod
    def _force_authority_flags_false(cls, _value: object) -> bool:
        # Any forged truthy value (including via model_copy) is coerced to False.
        # Authority is gate-derived (B5 readiness), never field-set.
        return False

    @field_serializer("traffic_attached", "execution_attached")
    def _serialize_authority_false(self, _value: object) -> bool:
        return False

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> "GoalLoopPolicy":
        """Override model_copy to prevent forged authority flags.

        Pydantic v2's model_copy bypasses field validators, so a caller could
        forge ``traffic_attached=True`` or ``execution_attached=True`` via
        ``model_copy(update={...})``.  This override intercepts the update dict
        and clears any attempt to set the locked authority flags before delegating
        to the base implementation.
        """
        _LOCKED = frozenset({"traffic_attached", "execution_attached"})
        if update:
            update = {k: (False if k in _LOCKED else v) for k, v in update.items()}
        return super().model_copy(update=update, deep=deep)

    @model_validator(mode="after")
    def _validate_traffic_free_policy(self) -> GoalLoopPolicy:
        if self.traffic_attached or self.execution_attached:
            raise ValueError("persistent goal loop scaffold must remain traffic-free")
        if self.opt_out.opted_out and (
            self.enabled or self.scheduling_enabled or self.allow_background_resume
        ):
            raise ValueError("goal loop opt-out must disable scheduling and background resume")
        if not self.enabled and (self.scheduling_enabled or self.allow_background_resume):
            raise ValueError("disabled goal loop cannot schedule or resume in background")
        if self.allow_background_resume and not self.scheduling_enabled:
            raise ValueError("allowBackgroundResume requires schedulingEnabled")
        return self


def build_goal_loop_policy(
    *,
    enabled: bool = False,
    scheduling_enabled: bool = False,
    allow_background_resume: bool = False,
    opt_out: GoalLoopOptOutState | None = None,
    ownership_scope: GoalLoopOwnershipScope | None = None,
    spawn_depth_policy: GoalLoopSpawnDepthPolicy | None = None,
) -> GoalLoopPolicy:
    resolved_opt_out = (
        GoalLoopOptOutState.model_validate(opt_out.model_dump())
        if opt_out is not None
        else GoalLoopOptOutState()
    )
    resolved_ownership_scope = (
        GoalLoopOwnershipScope.model_validate(ownership_scope.model_dump())
        if ownership_scope is not None
        else GoalLoopOwnershipScope()
    )
    resolved_spawn_depth_policy = (
        GoalLoopSpawnDepthPolicy.model_validate(spawn_depth_policy.model_dump())
        if spawn_depth_policy is not None
        else GoalLoopSpawnDepthPolicy()
    )

    if resolved_opt_out.opted_out:
        enabled = False
        scheduling_enabled = False
        allow_background_resume = False

    return GoalLoopPolicy(
        feature_key=GOAL_LOOP_FEATURE_KEY,
        enabled=enabled,
        scheduling_enabled=scheduling_enabled,
        allow_background_resume=allow_background_resume,
        opt_out=resolved_opt_out,
        ownership_scope=resolved_ownership_scope,
        spawn_depth_policy=resolved_spawn_depth_policy,
        traffic_attached=False,
        execution_attached=False,
    )


def validate_goal_loop_spawn_depth(
    spawn_depth: int,
    *,
    policy: GoalLoopSpawnDepthPolicy | None = None,
) -> int:
    resolved_policy = policy or GoalLoopSpawnDepthPolicy()
    if isinstance(spawn_depth, bool) or not isinstance(spawn_depth, int):
        raise ValueError("spawn depth must be an integer")
    if not resolved_policy.min_depth <= spawn_depth <= resolved_policy.max_depth:
        raise ValueError(
            "spawn depth must be between "
            f"{resolved_policy.min_depth} and {resolved_policy.max_depth}"
        )
    return spawn_depth


__all__ = [
    "DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH",
    "GOAL_LOOP_FEATURE_KEY",
    "GoalLoopAgentScope",
    "GoalLoopOptOutState",
    "GoalLoopOwnershipScope",
    "GoalLoopParticipantScope",
    "GoalLoopPolicy",
    "GoalLoopSpawnDepthPolicy",
    "build_goal_loop_policy",
    "validate_goal_loop_spawn_depth",
]
