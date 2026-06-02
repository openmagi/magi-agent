from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from magi_agent.harness.evidence_scope import (
    AgentRole,
    EvidenceContractScope,
    RunOn,
)
from magi_agent.harness.resolved import (
    EvidenceRolloutMode,
    ResolvedHarnessPresetState,
    build_default_resolved_harness_state,
    resolve_scoped_harness_hooks,
)
from magi_agent.telemetry.trace_context import get_trace

if TYPE_CHECKING:
    from magi_agent.hooks.manifest import HookManifest


class HarnessResolutionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    agent_role: AgentRole = Field(default="general", alias="agentRole")
    spawn_depth: int = Field(default=0, alias="spawnDepth", ge=0)
    run_on: RunOn | None = Field(default=None, alias="runOn")
    opted_out_evidence_contract_ids: tuple[str, ...] = Field(
        default=(),
        alias="optedOutEvidenceContractIds",
    )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)

    @model_validator(mode="after")
    def _validate_explicit_run_depth_pair(self) -> Self:
        if self.run_on == "main" and self.spawn_depth != 0:
            raise ValueError("main runs must use spawnDepth=0")
        if self.run_on == "child" and self.spawn_depth <= 0:
            raise ValueError("child runs must use spawnDepth greater than 0")
        return self


class HarnessEngine:
    def __init__(
        self,
        *,
        hooks: tuple[HookManifest, ...] = (),
        evidence_contracts: tuple[EvidenceContractScope, ...] = (),
        evidence_rollout_mode: EvidenceRolloutMode = "audit",
    ) -> None:
        self._hooks = hooks
        self._evidence_contracts = evidence_contracts
        self._evidence_rollout_mode = evidence_rollout_mode

    def resolve(
        self,
        request: HarnessResolutionRequest,
    ) -> tuple[tuple[HookManifest, ...], ResolvedHarnessPresetState]:
        state = build_default_resolved_harness_state(
            agent_role=request.agent_role,
            spawn_depth=request.spawn_depth,
            run_on=request.run_on,
            evidence_contracts=self._evidence_contracts,
            opted_out_evidence_contract_ids=request.opted_out_evidence_contract_ids,
            evidence_rollout_mode=self._evidence_rollout_mode,
        )
        selected, resolved_state = resolve_scoped_harness_hooks(self._hooks, state)
        trace = get_trace()
        if trace is not None:
            trace.record("harness", "HarnessEngine", "resolve", f"hooks={len(selected)}, role={request.agent_role}")
        return selected, resolved_state
