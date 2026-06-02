from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


AgentRole = Literal["general", "coding", "research"]
EvidenceEnforcement = Literal["off", "audit", "block_final_answer"]
FailureChannel = Literal["evidence_contract"]
RunOn = Literal["main", "child"]


class ThirdPartyEvidencePolicyDefaults(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    enforcement_default: Literal["off"] = Field(default="off", alias="enforcementDefault")
    audit_before_block: bool = Field(default=True, alias="auditBeforeBlock")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")

    @model_validator(mode="after")
    def _validate_traffic_free_defaults(self) -> Self:
        if self.traffic_attached or self.execution_attached:
            raise ValueError("third-party evidence scope defaults must stay traffic-free")
        return self


class SpawnDepthRange(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    min_depth: int = Field(default=0, alias="minDepth")
    max_depth: int | None = Field(default=None, alias="maxDepth")

    @model_validator(mode="after")
    def _validate_depth_range(self) -> Self:
        if self.min_depth < 0:
            raise ValueError("minDepth must be non-negative")
        if self.max_depth is not None and self.max_depth < self.min_depth:
            raise ValueError("maxDepth must be greater than or equal to minDepth")
        return self

    def includes(self, depth: int) -> bool:
        if depth < self.min_depth:
            return False
        if self.max_depth is not None and depth > self.max_depth:
            return False
        return True


class EvidenceScopeContext(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    agent_role: AgentRole = Field(alias="agentRole")
    run_on: RunOn = Field(alias="runOn")
    spawn_depth: int = Field(alias="spawnDepth")

    @model_validator(mode="after")
    def _validate_run_depth_pair(self) -> Self:
        if self.spawn_depth < 0:
            raise ValueError("spawnDepth must be non-negative")
        if self.run_on == "main" and self.spawn_depth != 0:
            raise ValueError("main runs must use spawnDepth=0")
        if self.run_on == "child" and self.spawn_depth <= 0:
            raise ValueError("child runs must use spawnDepth greater than 0")
        return self


class EvidenceContractScope(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    contract_id: str = Field(alias="contractId")
    agent_roles: tuple[AgentRole, ...] = Field(alias="agentRoles")
    run_on: tuple[RunOn, ...] = Field(alias="runOn")
    spawn_depth: SpawnDepthRange = Field(default_factory=SpawnDepthRange, alias="spawnDepth")
    enforcement: EvidenceEnforcement = "off"
    audit_before_block: bool = Field(default=True, alias="auditBeforeBlock")
    opt_out_allowed: bool = Field(default=True, alias="optOutAllowed")
    hard_safety: bool = Field(default=False, alias="hardSafety")
    failure_channel: FailureChannel = Field(default="evidence_contract", alias="failureChannel")
    research_citation_gate: bool = Field(default=False, alias="researchCitationGate")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")

    @field_validator("contract_id")
    @classmethod
    def _reject_empty_contract_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("contractId must be non-empty")
        return value

    @field_validator("agent_roles", "run_on")
    @classmethod
    def _reject_empty_or_duplicate_scope_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("scope tuple must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("scope tuple must not contain duplicate values")
        return value

    @model_validator(mode="after")
    def _validate_policy_scope(self) -> Self:
        if self.traffic_attached or self.execution_attached:
            raise ValueError("third-party evidence contract scope must stay traffic-free")
        if self.hard_safety and self.opt_out_allowed:
            raise ValueError("hard-safety evidence metadata cannot be opt-out allowed")
        if self.research_citation_gate:
            raise ValueError("evidence scope metadata must not attach research citation gates")
        if self.enforcement == "block_final_answer" and not self.audit_before_block:
            raise ValueError("block_final_answer evidence contracts require audit-before-block posture")
        return self

    def applies_to(self, context: EvidenceScopeContext) -> bool:
        return (
            context.agent_role in self.agent_roles
            and context.run_on in self.run_on
            and self.spawn_depth.includes(context.spawn_depth)
        )


class EvidenceScopeDecision(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    contract_id: str = Field(alias="contractId")
    applies: bool
    effective_enforcement: EvidenceEnforcement = Field(alias="effectiveEnforcement")
    enforcement_enabled: bool = Field(alias="enforcementEnabled")
    opt_out_applied: bool = Field(alias="optOutApplied")
    hard_safety: bool = Field(alias="hardSafety")
    failure_channel: FailureChannel = Field(default="evidence_contract", alias="failureChannel")
    research_citation_gate: bool = Field(default=False, alias="researchCitationGate")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")

    @model_validator(mode="after")
    def _validate_traffic_free_decision(self) -> Self:
        if not self.contract_id.strip():
            raise ValueError("contractId must be non-empty")
        if self.traffic_attached or self.execution_attached:
            raise ValueError("evidence scope decisions must stay traffic-free")
        if self.research_citation_gate:
            raise ValueError("evidence scope decisions must not attach research citation gates")
        if self.enforcement_enabled != (self.effective_enforcement != "off"):
            raise ValueError("enforcementEnabled must match effectiveEnforcement")
        if not self.applies and self.effective_enforcement != "off":
            raise ValueError("effectiveEnforcement must be off when applies=false")
        if self.opt_out_applied and not self.applies:
            raise ValueError("optOutApplied requires applies=true")
        if self.opt_out_applied and self.effective_enforcement != "off":
            raise ValueError("optOutApplied requires effectiveEnforcement=off")
        if self.opt_out_applied and self.hard_safety:
            raise ValueError("optOutApplied cannot be true for hard-safety evidence")
        return self


def third_party_evidence_policy_defaults() -> ThirdPartyEvidencePolicyDefaults:
    return ThirdPartyEvidencePolicyDefaults()


def resolve_evidence_scope(
    contract: EvidenceContractScope,
    context: EvidenceScopeContext,
    *,
    opted_out_contract_ids: tuple[str, ...] = (),
) -> EvidenceScopeDecision:
    validated_contract = EvidenceContractScope.model_validate(
        contract.model_dump(by_alias=True, warnings="none")
    )
    validated_context = EvidenceScopeContext.model_validate(
        context.model_dump(by_alias=True, warnings="none")
    )
    applies = validated_contract.applies_to(validated_context)
    opt_out_applied = (
        applies
        and validated_contract.contract_id in set(opted_out_contract_ids)
        and validated_contract.opt_out_allowed
        and not validated_contract.hard_safety
    )
    effective_enforcement: EvidenceEnforcement = (
        validated_contract.enforcement if applies else "off"
    )
    if opt_out_applied:
        effective_enforcement = "off"

    return EvidenceScopeDecision(
        contractId=validated_contract.contract_id,
        applies=applies,
        effectiveEnforcement=effective_enforcement,
        enforcementEnabled=effective_enforcement != "off",
        optOutApplied=opt_out_applied,
        hardSafety=validated_contract.hard_safety,
        failureChannel=validated_contract.failure_channel,
        researchCitationGate=False,
        trafficAttached=False,
        executionAttached=False,
    )


__all__ = [
    "AgentRole",
    "EvidenceContractScope",
    "EvidenceEnforcement",
    "EvidenceScopeContext",
    "EvidenceScopeDecision",
    "FailureChannel",
    "RunOn",
    "SpawnDepthRange",
    "ThirdPartyEvidencePolicyDefaults",
    "resolve_evidence_scope",
    "third_party_evidence_policy_defaults",
]
