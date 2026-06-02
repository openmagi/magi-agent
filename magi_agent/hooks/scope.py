from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RunOn = Literal["main", "child"]
HookScopeName = Literal["all", "main", "child", "coding", "research", "general"]


class HookScopeContext(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    run_on: RunOn = Field(alias="runOn")
    agent_role: str = Field(alias="agentRole")
    spawn_depth: int = Field(alias="spawnDepth", ge=0)


class HookScope(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    scope: HookScopeName = "all"
    run_on: tuple[RunOn, ...] = Field(default=("main", "child"), alias="runOn")
    agent_roles: tuple[str, ...] = Field(default=(), alias="agentRoles")
    min_spawn_depth: int = Field(default=0, alias="minSpawnDepth", ge=0)
    max_spawn_depth: int | None = Field(default=None, alias="maxSpawnDepth", ge=0)
    hard_safety: bool = Field(default=False, alias="hardSafety")

    def applies_to(self, context: HookScopeContext) -> bool:
        if self.hard_safety:
            return True
        if context.run_on not in self.run_on:
            return False
        if context.spawn_depth < self.min_spawn_depth:
            return False
        if self.max_spawn_depth is not None and context.spawn_depth > self.max_spawn_depth:
            return False
        if self.agent_roles and context.agent_role not in self.agent_roles:
            return False
        if self.scope == "main" and context.run_on != "main":
            return False
        if self.scope == "child" and context.run_on != "child":
            return False
        if self.scope in {"coding", "research", "general"} and context.agent_role != self.scope:
            return False
        return True
