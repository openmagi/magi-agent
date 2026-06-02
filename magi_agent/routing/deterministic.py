from __future__ import annotations

from typing import Literal, Self, TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator

from magi_agent.evidence.types import EvidenceAgentRole, EvidenceMetadataModel, EvidenceRunOn


DeterministicRouteMode = Literal["baseline_shadow", "audit", "enforce", "block_final_answer"]
ExactnessRequirement = Literal[
    "arithmetic",
    "relative_date",
    "current_public_fact",
    "source_claim",
    "file_state",
    "code_change",
    "clarification",
]
RiskLevel = Literal["low", "medium", "high"]
UncertaintyLevel = Literal["low", "medium", "high"]
RequiredEvidenceStatus = Literal["required_missing", "represented"]
FinalAnswerPolicyMode = Literal[
    "allow_model_final_answer",
    "require_clarification",
    "require_evidence_citation",
    "block_without_evidence",
]


class DeterministicRoutingScope(EvidenceMetadataModel):
    agent_role: EvidenceAgentRole = Field(alias="agentRole")
    run_on: EvidenceRunOn = Field(alias="runOn")
    spawn_depth: int = Field(alias="spawnDepth")

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        if self.spawn_depth < 0:
            raise ValueError("spawnDepth must be non-negative")
        if self.run_on == "main" and self.spawn_depth != 0:
            raise ValueError("main runs must use spawnDepth=0")
        if self.run_on == "child" and self.spawn_depth <= 0:
            raise ValueError("child runs must use spawnDepth greater than 0")
        return self


class DeterministicClassificationMetadata(EvidenceMetadataModel):
    exactness_requirements: tuple[ExactnessRequirement, ...] = Field(alias="exactnessRequirements")
    risk_level: RiskLevel = Field(default="low", alias="riskLevel")
    uncertainty: UncertaintyLevel = "low"
    clarification_required: bool = Field(default=False, alias="clarificationRequired")

    @field_validator("exactness_requirements")
    @classmethod
    def _reject_duplicate_requirements(
        cls,
        value: tuple[ExactnessRequirement, ...],
    ) -> tuple[ExactnessRequirement, ...]:
        if len(set(value)) != len(value):
            raise ValueError("exactnessRequirements must not contain duplicates")
        return value

    @model_validator(mode="after")
    def _validate_clarification_consistency(self) -> Self:
        has_clarification_requirement = "clarification" in self.exactness_requirements
        if has_clarification_requirement and not self.clarification_required:
            raise ValueError("clarification exactness requires clarificationRequired=True")
        if self.clarification_required and not has_clarification_requirement:
            raise ValueError("clarificationRequired=True requires clarification exactness requirement")
        if self.clarification_required and self.exactness_requirements != ("clarification",):
            raise ValueError("clarificationRequired=True requires clarification-only exactness")
        return self


class RequiredEvidenceMetadata(EvidenceMetadataModel):
    evidence_type: str = Field(alias="evidenceType")
    status: RequiredEvidenceStatus = "required_missing"

    @field_validator("evidence_type")
    @classmethod
    def _reject_empty_evidence_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidenceType must be non-empty")
        return value


class FinalAnswerPolicyMetadata(EvidenceMetadataModel):
    mode: FinalAnswerPolicyMode = "allow_model_final_answer"
    metadata_only: bool = Field(default=True, alias="metadataOnly")

    @field_validator("metadata_only")
    @classmethod
    def _require_metadata_only(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("deterministic routing final-answer policy is metadata-only in phase 0")
        return value


class RetryInstructionMetadata(EvidenceMetadataModel):
    reason: str
    missing_evidence_types: tuple[str, ...] = Field(default=(), alias="missingEvidenceTypes")
    instruction: str
    metadata_only: bool = Field(default=True, alias="metadataOnly")

    @field_validator("reason", "instruction")
    @classmethod
    def _reject_empty_strings(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("retry instruction fields must be non-empty")
        return value

    @field_validator("missing_evidence_types")
    @classmethod
    def _reject_invalid_missing_evidence_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique_non_empty_strings(value, "missingEvidenceTypes")

    @field_validator("metadata_only")
    @classmethod
    def _require_metadata_only(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("retry instruction metadata cannot attach runtime behavior")
        return value


class DeterministicGateSurfaceMetadata(EvidenceMetadataModel):
    classifier_gates_represented: Literal[False] = Field(
        default=False,
        alias="classifierGatesRepresented",
    )
    active_requirement_gates_represented: Literal[False] = Field(
        default=False,
        alias="activeRequirementGatesRepresented",
    )
    phase0_shadow_only: Literal[True] = Field(default=True, alias="phase0ShadowOnly")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @model_validator(mode="after")
    def _validate_gate_surface(self) -> Self:
        if self.classifier_gates_represented:
            raise ValueError("classifier gate enforcement is not represented in phase 0 metadata")
        if self.active_requirement_gates_represented:
            raise ValueError("active requirement gate enforcement is not represented in phase 0 metadata")
        return self


class BaselineShadowMeasurementMetadata(EvidenceMetadataModel):
    would_route: bool = Field(alias="wouldRoute")
    mode_observed: Literal["baseline_shadow"] = Field(default="baseline_shadow", alias="modeObserved")
    changed_final_action: Literal[False] = Field(default=False, alias="changedFinalAction")
    blocked_final_answer: Literal[False] = Field(default=False, alias="blockedFinalAnswer")
    routed_tool_names: tuple[str, ...] = Field(default=(), alias="routedToolNames")
    routed_evidence_types: tuple[str, ...] = Field(default=(), alias="routedEvidenceTypes")

    @field_validator("routed_tool_names")
    @classmethod
    def _reject_invalid_routed_tool_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique_non_empty_strings(value, "routedToolNames")

    @field_validator("routed_evidence_types")
    @classmethod
    def _reject_invalid_routed_evidence_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique_non_empty_strings(value, "routedEvidenceTypes")

    @model_validator(mode="after")
    def _validate_route_measurement(self) -> Self:
        if not self.would_route and (self.routed_tool_names or self.routed_evidence_types):
            raise ValueError("wouldRoute=False cannot include routed tools or evidence")
        has_routed_tools = bool(self.routed_tool_names)
        has_routed_evidence = bool(self.routed_evidence_types)
        if has_routed_tools != has_routed_evidence:
            raise ValueError("partial routed metadata requires both routed tools and evidence")
        return self


class DeterministicRolloutMetadata(EvidenceMetadataModel):
    mode: DeterministicRouteMode = "baseline_shadow"
    baseline_measurement: BaselineShadowMeasurementMetadata | None = Field(
        default=None,
        alias="baselineMeasurement",
    )
    audit_ready: bool = Field(default=False, alias="auditReady")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    enforcement_attached: Literal[False] = Field(default=False, alias="enforcementAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")

    @model_validator(mode="after")
    def _validate_rollout_sequencing(self) -> Self:
        if self.mode != "baseline_shadow" and self.baseline_measurement is None:
            raise ValueError("audit/enforce routing requires baseline shadow measurement metadata")
        if self.audit_ready and self.baseline_measurement is None:
            raise ValueError("auditReady requires baseline shadow measurement metadata")
        if self.mode == "audit" and not self.audit_ready:
            object.__setattr__(self, "audit_ready", True)
        if self.mode in {"enforce", "block_final_answer"} and self.audit_ready:
            raise ValueError("enforce/block metadata must not be marked auditReady")
        return self


class DeterministicRoutePlanMetadata(EvidenceMetadataModel):
    mode: DeterministicRouteMode
    reason: str
    scope: DeterministicRoutingScope
    classification: DeterministicClassificationMetadata
    required_tool_names: tuple[str, ...] = Field(alias="requiredToolNames")
    required_evidence: tuple[RequiredEvidenceMetadata, ...] = Field(alias="requiredEvidence")
    final_answer_policy: FinalAnswerPolicyMetadata = Field(alias="finalAnswerPolicy")
    retry_instruction: RetryInstructionMetadata = Field(alias="retryInstruction")
    baseline_measurement: BaselineShadowMeasurementMetadata = Field(alias="baselineMeasurement")
    gate_surface: DeterministicGateSurfaceMetadata = Field(
        default_factory=DeterministicGateSurfaceMetadata,
        alias="gateSurface",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    enforcement_attached: Literal[False] = Field(default=False, alias="enforcementAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")

    @field_validator("reason")
    @classmethod
    def _reject_empty_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must be non-empty")
        return value

    @field_validator("required_tool_names")
    @classmethod
    def _reject_invalid_required_tool_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique_non_empty_strings(value, "requiredToolNames")

    @field_validator("required_evidence")
    @classmethod
    def _revalidate_required_evidence(
        cls,
        value: tuple[RequiredEvidenceMetadata, ...],
    ) -> tuple[RequiredEvidenceMetadata, ...]:
        revalidated = tuple(_revalidate_nested_model(item, RequiredEvidenceMetadata) for item in value)
        if any(item.status != "required_missing" for item in revalidated):
            raise ValueError("route requiredEvidence status must be required_missing in phase 0")
        return revalidated

    @field_validator("classification")
    @classmethod
    def _revalidate_classification(
        cls,
        value: DeterministicClassificationMetadata,
    ) -> DeterministicClassificationMetadata:
        return _revalidate_nested_model(value, DeterministicClassificationMetadata)

    @field_validator("final_answer_policy")
    @classmethod
    def _revalidate_final_answer_policy(
        cls,
        value: FinalAnswerPolicyMetadata,
    ) -> FinalAnswerPolicyMetadata:
        return _revalidate_nested_model(value, FinalAnswerPolicyMetadata)

    @field_validator("retry_instruction")
    @classmethod
    def _revalidate_retry_instruction(
        cls,
        value: RetryInstructionMetadata,
    ) -> RetryInstructionMetadata:
        return _revalidate_nested_model(value, RetryInstructionMetadata)

    @field_validator("baseline_measurement")
    @classmethod
    def _revalidate_baseline_measurement(
        cls,
        value: BaselineShadowMeasurementMetadata,
    ) -> BaselineShadowMeasurementMetadata:
        return _revalidate_nested_model(value, BaselineShadowMeasurementMetadata)

    @field_validator("gate_surface")
    @classmethod
    def _revalidate_gate_surface(
        cls,
        value: DeterministicGateSurfaceMetadata,
    ) -> DeterministicGateSurfaceMetadata:
        return _revalidate_nested_model(value, DeterministicGateSurfaceMetadata)

    @model_validator(mode="after")
    def _validate_route_consistency(self) -> Self:
        required_evidence_types = tuple(item.evidence_type for item in self.required_evidence)
        expected_tool_names, expected_evidence_types = _route_requirements(
            self.classification.exactness_requirements
        )

        if self.mode in {"enforce", "block_final_answer"}:
            if self.final_answer_policy.mode != "block_without_evidence":
                raise ValueError(
                    "enforce/block routing requires finalAnswerPolicy.mode=block_without_evidence"
                )

        if self.classification.clarification_required and not self.baseline_measurement.would_route:
            raise ValueError("clarification routing requires baselineMeasurement.wouldRoute=True")

        if self.baseline_measurement.would_route:
            has_routed_metadata = bool(
                self.baseline_measurement.routed_tool_names
                and self.baseline_measurement.routed_evidence_types
            )
            if not has_routed_metadata and not self.classification.clarification_required:
                raise ValueError(
                    "wouldRoute=True requires routed tools/evidence unless clarification is required"
                )

        if self.required_tool_names != self.baseline_measurement.routed_tool_names:
            raise ValueError("requiredToolNames must match baselineMeasurement.routedToolNames")
        if required_evidence_types != self.baseline_measurement.routed_evidence_types:
            raise ValueError("requiredEvidence must match baselineMeasurement.routedEvidenceTypes")

        if self.required_tool_names != expected_tool_names:
            raise ValueError("requiredToolNames must match classification exactness requirements")
        if required_evidence_types != expected_evidence_types:
            raise ValueError("requiredEvidence must match classification exactness requirements")
        if self.baseline_measurement.routed_tool_names != expected_tool_names:
            raise ValueError(
                "baselineMeasurement.routedToolNames must match classification exactness requirements"
            )
        if self.baseline_measurement.routed_evidence_types != expected_evidence_types:
            raise ValueError(
                "baselineMeasurement.routedEvidenceTypes must match classification exactness requirements"
            )
        if self.retry_instruction.missing_evidence_types != required_evidence_types:
            raise ValueError("missingEvidenceTypes must match requiredEvidence")

        if self.classification.clarification_required and self.mode not in {
            "enforce",
            "block_final_answer",
        }:
            if self.final_answer_policy.mode != "require_clarification":
                raise ValueError("clarification routing requires finalAnswerPolicy.mode=require_clarification")
            return self

        if self.classification.exactness_requirements:
            if self.mode in {"baseline_shadow", "audit"}:
                if self.final_answer_policy.mode != "require_evidence_citation":
                    raise ValueError(
                        "baseline/audit exactness requires "
                        "finalAnswerPolicy.mode=require_evidence_citation"
                    )
            elif self.final_answer_policy.mode != "block_without_evidence":
                raise ValueError(
                    "enforce/block exactness requires finalAnswerPolicy.mode=block_without_evidence"
                )
        elif self.mode in {"baseline_shadow", "audit"}:
            if self.final_answer_policy.mode != "allow_model_final_answer":
                raise ValueError(
                    "baseline/audit routes without exactness or clarification require "
                    "finalAnswerPolicy.mode=allow_model_final_answer"
                )

        return self


def build_baseline_shadow_route(
    prompt: str,
    *,
    scope: DeterministicRoutingScope,
    mode: DeterministicRouteMode = "baseline_shadow",
) -> DeterministicRoutePlanMetadata:
    classification = _classify(prompt)
    tool_names, evidence_types = _route_requirements(classification.exactness_requirements)
    final_answer_policy = _final_answer_policy(classification, mode)
    retry_instruction = _retry_instruction(classification, evidence_types, mode)
    measurement = BaselineShadowMeasurementMetadata(
        wouldRoute=bool(tool_names or classification.clarification_required),
        routedToolNames=tool_names,
        routedEvidenceTypes=evidence_types,
    )

    return DeterministicRoutePlanMetadata(
        mode=mode,
        reason=_route_reason(classification),
        scope=scope,
        classification=classification,
        requiredToolNames=tool_names,
        requiredEvidence=tuple(
            RequiredEvidenceMetadata(evidenceType=evidence_type, status="required_missing")
            for evidence_type in evidence_types
        ),
        finalAnswerPolicy=final_answer_policy,
        retryInstruction=retry_instruction,
        baselineMeasurement=measurement,
    )


def _classify(prompt: str) -> DeterministicClassificationMetadata:
    text = prompt.lower()
    exactness: list[ExactnessRequirement] = []
    uncertainty: UncertaintyLevel = "low"
    risk_level: RiskLevel = "low"
    clarification_required = False

    if _looks_like_arithmetic(text):
        exactness.append("arithmetic")
    if any(token in text for token in ("current", "latest", "today", "now", "this week")):
        exactness.append("current_public_fact")
    if any(token in text for token in ("cite", "source", "according to", "public fact")):
        exactness.append("source_claim")
    if any(token in text for token in ("yesterday", "tomorrow", "last week", "next week")):
        exactness.append("relative_date")
    if any(token in text for token in ("edit", "change", "fix", "implement", "commit", "tests")):
        exactness.append("code_change")
        risk_level = "medium"
    if any(token in text for token in ("file", ".py", ".ts", ".tsx", ".md", "diff")):
        exactness.append("file_state")

    if _looks_underspecified(text, exactness):
        exactness = ["clarification"]
        uncertainty = "high"
        clarification_required = True

    return DeterministicClassificationMetadata(
        exactnessRequirements=_dedupe(exactness),
        riskLevel=risk_level,
        uncertainty=uncertainty,
        clarificationRequired=clarification_required,
    )


def _looks_like_arithmetic(text: str) -> bool:
    operators = ("+", "-", "*", "/", " plus ", " minus ", " times ", " divided ")
    return any(operator in text for operator in operators) and any(char.isdigit() for char in text)


def _looks_underspecified(text: str, exactness: list[ExactnessRequirement]) -> bool:
    vague_targets = (" it ", " there ", " usual thing", "that thing", "the thing")
    return any(target in f" {text} " for target in vague_targets) and not {
        "file_state",
        "current_public_fact",
        "source_claim",
        "arithmetic",
    }.intersection(exactness)


def _route_requirements(
    exactness: tuple[ExactnessRequirement, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tools: list[str] = []
    evidence: list[str] = []
    if "arithmetic" in exactness:
        tools.append("Calculation")
        evidence.append("Calculation")
    if "relative_date" in exactness:
        tools.append("Clock")
        evidence.extend(("Clock", "DateRange"))
    if "current_public_fact" in exactness:
        tools.append("Search")
        evidence.append("WebSearch")
    if "source_claim" in exactness:
        tools.append("SourceInspection")
        evidence.append("SourceInspection")
    if "file_state" in exactness or "code_change" in exactness:
        tools.extend(("IsolatedWorkspace", "FileRead", "Diff", "Diagnostics", "TestRunner", "Checkpoint"))
        evidence.extend(
            (
                "WorkspaceIsolation",
                "FileInspection",
                "GitDiff",
                "Diagnostics",
                "TestRun",
                "CommitCheckpoint",
            )
        )
    return _dedupe(tools), _dedupe(evidence)


def _final_answer_policy(
    classification: DeterministicClassificationMetadata,
    mode: DeterministicRouteMode,
) -> FinalAnswerPolicyMetadata:
    if mode in {"enforce", "block_final_answer"}:
        return FinalAnswerPolicyMetadata(mode="block_without_evidence")
    if classification.clarification_required:
        return FinalAnswerPolicyMetadata(mode="require_clarification")
    if classification.exactness_requirements:
        return FinalAnswerPolicyMetadata(mode="require_evidence_citation")
    return FinalAnswerPolicyMetadata(mode="allow_model_final_answer")


def _retry_instruction(
    classification: DeterministicClassificationMetadata,
    evidence_types: tuple[str, ...],
    mode: DeterministicRouteMode,
) -> RetryInstructionMetadata:
    if classification.clarification_required and mode not in {"enforce", "block_final_answer"}:
        return RetryInstructionMetadata(
            reason="clarify_missing_target",
            instruction="Ask a concise clarification question before claiming deterministic completion.",
        )
    return RetryInstructionMetadata(
        reason="missing_deterministic_evidence",
        missingEvidenceTypes=evidence_types,
        instruction="Collect the required deterministic evidence before citing exact facts.",
    )


def _route_reason(classification: DeterministicClassificationMetadata) -> str:
    if classification.clarification_required:
        return "high uncertainty requires clarification posture metadata"
    if classification.exactness_requirements:
        return "deterministic exactness requirements would route to required tools and evidence"
    return "no deterministic route requirement detected in phase 0 shadow metadata"


_T = TypeVar("_T")


def _dedupe(items: list[_T]) -> tuple[_T, ...]:
    return tuple(dict.fromkeys(items))


def _validate_unique_non_empty_strings(value: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if any(not item.strip() for item in value):
        raise ValueError(f"{field_name} must contain non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{field_name} must not contain duplicates")
    return value


_MetadataModelT = TypeVar("_MetadataModelT", bound=BaseModel)


def _revalidate_nested_model(
    value: _MetadataModelT,
    model_type: type[_MetadataModelT],
) -> _MetadataModelT:
    if isinstance(value, model_type):
        return model_type.model_validate(value.model_dump(by_alias=False, mode="python", warnings=False))
    return value


__all__ = [
    "BaselineShadowMeasurementMetadata",
    "DeterministicClassificationMetadata",
    "DeterministicGateSurfaceMetadata",
    "DeterministicRoutePlanMetadata",
    "DeterministicRolloutMetadata",
    "DeterministicRoutingScope",
    "FinalAnswerPolicyMetadata",
    "RequiredEvidenceMetadata",
    "RetryInstructionMetadata",
    "build_baseline_shadow_route",
]
