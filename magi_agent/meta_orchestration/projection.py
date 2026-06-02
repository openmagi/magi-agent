from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from magi_agent.meta_orchestration.commit_adapter import (
    MetaBeforeCommitVerdict,
    RuntimeIssuedMetaVerifierResult,
)
from magi_agent.meta_orchestration.final_assembly import MetaFinalAssemblyPlan
from magi_agent.meta_orchestration.inspection_loop import (
    MetaInspectionLoopResult,
)
from magi_agent.meta_orchestration.task_plan import (
    MetaTaskPlan,
    _copy_update_alias,
    _validate_public_ref,
    _validate_ref_tuple,
)


MetaOrchestrationPlanStatus = Literal[
    "ready_for_projection",
    "needs_retry",
    "partial",
    "blocked",
]
MetaProjectionChildTaskStatusValue = Literal["accepted", "retry", "rejected", "blocked"]
MetaProjectionVerifierStatus = Literal["passed", "blocked"]

_PUBLIC_PROJECTION_TOKEN = object()
_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_TRUE_ONLY_FLAG_NAMES = (
    "default_off",
    "local_only",
    "fake_provider_only",
)
_FALSE_ONLY_FLAG_NAMES = (
    "tool_execution_allowed",
    "child_execution_allowed",
    "model_call_allowed",
    "workspace_write_allowed",
    "memory_write_allowed",
    "web_browser_attached",
    "channel_write_attached",
    "route_attached",
    "production_authority",
    "live_execution_allowed",
    "adk_runner_attached",
)


class _MetaProjectionModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        raise TypeError("model_construct is disabled for meta projection contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        return type(self).model_validate(data)


class MetaProjectionChildTaskStatus(_MetaProjectionModel):
    task_index: int = Field(alias="taskIndex", ge=0, strict=True)
    task_digest: str = Field(alias="taskDigest")
    status: MetaProjectionChildTaskStatusValue
    attempt: int = Field(ge=0, le=10, strict=True)
    accepted_evidence_ref_count: int = Field(alias="acceptedEvidenceRefCount", ge=0, strict=True)
    missing_evidence_ref_count: int = Field(alias="missingEvidenceRefCount", ge=0, strict=True)
    retryable: bool
    retry_budget_remaining: int = Field(alias="retryBudgetRemaining", ge=0, le=10, strict=True)
    required: bool

    @field_validator("task_digest")
    @classmethod
    def _validate_task_digest(cls, value: str) -> str:
        return _validate_digest_ref(value, "taskDigest")

    @field_validator("retryable", "required", mode="before")
    @classmethod
    def _validate_strict_bool(cls, value: object, info: Any) -> object:
        if value is not True and value is not False:
            raise ValueError(f"{info.field_name} must be a strict boolean")
        return value

    def public_projection(self) -> dict[str, object]:
        parsed = type(self).model_validate(self.model_dump(by_alias=True, mode="python"))
        return {
            "taskIndex": parsed.task_index,
            "taskDigest": parsed.task_digest,
            "status": parsed.status,
            "attempt": parsed.attempt,
            "acceptedEvidenceRefCount": parsed.accepted_evidence_ref_count,
            "missingEvidenceRefCount": parsed.missing_evidence_ref_count,
            "retryable": parsed.retryable,
            "retryBudgetRemaining": parsed.retry_budget_remaining,
            "required": parsed.required,
        }


class MetaProjectionEvidenceRefCounts(_MetaProjectionModel):
    accepted: int = Field(ge=0, strict=True)
    missing: int = Field(ge=0, strict=True)
    excluded_children: int = Field(alias="excludedChildren", ge=0, strict=True)
    retry_schedule_refs: int = Field(alias="retryScheduleRefs", ge=0, strict=True)
    required_verifiers: int = Field(alias="requiredVerifiers", ge=0, strict=True)
    verifier_results: int = Field(alias="verifierResults", ge=0, strict=True)

    def public_projection(self) -> dict[str, int]:
        parsed = type(self).model_validate(self.model_dump(by_alias=True, mode="python"))
        return {
            "accepted": parsed.accepted,
            "missing": parsed.missing,
            "excludedChildren": parsed.excluded_children,
            "retryScheduleRefs": parsed.retry_schedule_refs,
            "requiredVerifiers": parsed.required_verifiers,
            "verifierResults": parsed.verifier_results,
        }


class MetaProjectionActivationFlags(_MetaProjectionModel):
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")
    tool_execution_allowed: Literal[False] = Field(default=False, alias="toolExecutionAllowed")
    child_execution_allowed: Literal[False] = Field(default=False, alias="childExecutionAllowed")
    model_call_allowed: Literal[False] = Field(default=False, alias="modelCallAllowed")
    workspace_write_allowed: Literal[False] = Field(default=False, alias="workspaceWriteAllowed")
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    web_browser_attached: Literal[False] = Field(default=False, alias="webBrowserAttached")
    channel_write_attached: Literal[False] = Field(default=False, alias="channelWriteAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    live_execution_allowed: Literal[False] = Field(default=False, alias="liveExecutionAllowed")
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")

    @field_validator(*_TRUE_ONLY_FLAG_NAMES, mode="before")
    @classmethod
    def _validate_true_only_flags(cls, value: object, info: Any) -> object:
        if value is not True:
            raise ValueError(f"{info.field_name} must remain true")
        return value

    @field_validator(*_FALSE_ONLY_FLAG_NAMES, mode="before")
    @classmethod
    def _validate_false_only_flags(cls, value: object, info: Any) -> object:
        if value is not False:
            raise ValueError(f"{info.field_name} must remain false")
        return value

    def public_projection(self) -> dict[str, bool]:
        parsed = type(self).model_validate(self.model_dump(by_alias=True, mode="python"))
        return {
            "defaultOff": parsed.default_off,
            "localOnly": parsed.local_only,
            "fakeProviderOnly": parsed.fake_provider_only,
            "toolExecutionAllowed": parsed.tool_execution_allowed,
            "childExecutionAllowed": parsed.child_execution_allowed,
            "modelCallAllowed": parsed.model_call_allowed,
            "workspaceWriteAllowed": parsed.workspace_write_allowed,
            "memoryWriteAllowed": parsed.memory_write_allowed,
            "webBrowserAttached": parsed.web_browser_attached,
            "channelWriteAttached": parsed.channel_write_attached,
            "routeAttached": parsed.route_attached,
            "productionAuthority": parsed.production_authority,
            "liveExecutionAllowed": parsed.live_execution_allowed,
            "adkRunnerAttached": parsed.adk_runner_attached,
        }


class MetaOrchestrationPublicProjection(_MetaProjectionModel):
    projection_id: str = Field(alias="projectionId")
    plan_id: str = Field(alias="planId")
    parent_execution_id: str = Field(alias="parentExecutionId")
    assembly_id: str = Field(alias="assemblyId")
    before_commit_verdict_id: str = Field(alias="beforeCommitVerdictId")
    plan_digest: str = Field(alias="planDigest")
    inspection_digest: str = Field(alias="inspectionDigest")
    assembly_digest: str = Field(alias="assemblyDigest")
    before_commit_digest: str = Field(alias="beforeCommitDigest")
    plan_status: MetaOrchestrationPlanStatus = Field(alias="planStatus")
    child_task_statuses: tuple[MetaProjectionChildTaskStatus, ...] = Field(
        alias="childTaskStatuses",
    )
    accepted_child_count: int = Field(alias="acceptedChildCount", ge=0, strict=True)
    retried_child_count: int = Field(alias="retriedChildCount", ge=0, strict=True)
    rejected_child_count: int = Field(alias="rejectedChildCount", ge=0, strict=True)
    blocked_child_count: int = Field(alias="blockedChildCount", ge=0, strict=True)
    evidence_ref_counts: MetaProjectionEvidenceRefCounts = Field(alias="evidenceRefCounts")
    verifier_status: MetaProjectionVerifierStatus = Field(alias="verifierStatus")
    final_projection_eligible: bool = Field(alias="finalProjectionEligible")
    blocked_reasons: tuple[str, ...] = Field(alias="blockedReasons")
    activation_flags: MetaProjectionActivationFlags = Field(
        default_factory=MetaProjectionActivationFlags,
        alias="activationFlags",
    )
    projection_digest: str = Field(alias="projectionDigest")
    _canonical_payload_digest: str = PrivateAttr(default="")
    _source_artifact_binding_digest: str = PrivateAttr(default="")
    _source_plan: MetaTaskPlan | None = PrivateAttr(default=None)
    _source_inspection: MetaInspectionLoopResult | None = PrivateAttr(default=None)
    _source_assembly: MetaFinalAssemblyPlan | None = PrivateAttr(default=None)
    _source_before_commit: MetaBeforeCommitVerdict | None = PrivateAttr(default=None)
    _source_verifier_results: tuple[RuntimeIssuedMetaVerifierResult, ...] = PrivateAttr(
        default=(),
    )

    def __init__(self, **data: Any) -> None:
        token = data.pop("_projection_token", None)
        if token is not _PUBLIC_PROJECTION_TOKEN:
            raise TypeError("public projections must be produced by the meta harness adapter")
        data["projectionDigest"] = "sha256:" + "0" * 64
        super().__init__(**data)
        digest = _projection_payload_digest(self)
        object.__setattr__(self, "projection_digest", digest)
        object.__setattr__(self, "_canonical_payload_digest", digest)

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: str | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        _ = obj, strict, extra, from_attributes, context, by_alias, by_name
        raise TypeError("public projections must be produced by the meta harness adapter")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        if update:
            raise TypeError("public projections cannot be updated after issuance")
        return self

    @classmethod
    def _from_harness(
        cls,
        *,
        projection_id: str,
        plan: MetaTaskPlan,
        inspection: MetaInspectionLoopResult,
        assembly: MetaFinalAssemblyPlan,
        before_commit_verdict: MetaBeforeCommitVerdict,
        verifier_results: tuple[RuntimeIssuedMetaVerifierResult, ...],
    ) -> Self:
        child_statuses = _child_statuses_for_plan(plan, inspection)
        status_counts = _child_status_counts(child_statuses)
        projection = cls(
            _projection_token=_PUBLIC_PROJECTION_TOKEN,
            projectionId=_validate_public_ref(projection_id, "projectionId"),
            planId=plan.plan_id,
            parentExecutionId=plan.parent_execution_id,
            assemblyId=assembly.assembly_id,
            beforeCommitVerdictId=before_commit_verdict.verdict_id,
            planDigest=_plan_artifact_digest(plan),
            inspectionDigest=_inspection_artifact_digest(inspection),
            assemblyDigest=_assembly_artifact_digest(assembly),
            beforeCommitDigest=_before_commit_artifact_digest(before_commit_verdict),
            planStatus=_plan_status(inspection, assembly, before_commit_verdict),
            childTaskStatuses=child_statuses,
            acceptedChildCount=status_counts["accepted"],
            retriedChildCount=status_counts["retry"],
            rejectedChildCount=status_counts["rejected"],
            blockedChildCount=status_counts["blocked"],
            evidenceRefCounts=_evidence_ref_counts(inspection, assembly, before_commit_verdict),
            verifierStatus=before_commit_verdict.verifier_chain_result,
            finalProjectionEligible=_final_projection_eligible(assembly, before_commit_verdict),
            blockedReasons=_blocked_reasons(inspection, assembly, before_commit_verdict),
            activationFlags=MetaProjectionActivationFlags(),
            projectionDigest="sha256:" + "0" * 64,
        )
        object.__setattr__(
            projection,
            "_source_artifact_binding_digest",
            _artifact_binding_digest(projection),
        )
        object.__setattr__(projection, "_source_plan", plan)
        object.__setattr__(projection, "_source_inspection", inspection)
        object.__setattr__(projection, "_source_assembly", assembly)
        object.__setattr__(projection, "_source_before_commit", before_commit_verdict)
        object.__setattr__(projection, "_source_verifier_results", verifier_results)
        return projection

    @field_validator(
        "projection_id",
        "plan_id",
        "parent_execution_id",
        "assembly_id",
        "before_commit_verdict_id",
        "plan_digest",
        "inspection_digest",
        "assembly_digest",
        "before_commit_digest",
        "projection_digest",
    )
    @classmethod
    def _validate_refs(cls, value: str, info: Any) -> str:
        clean = _validate_public_ref(value, info.field_name)
        if info.field_name.endswith("digest"):
            return _validate_digest_ref(clean, info.field_name)
        return clean

    @field_validator("final_projection_eligible", mode="before")
    @classmethod
    def _validate_strict_bool(cls, value: object) -> object:
        if value is not True and value is not False:
            raise ValueError("finalProjectionEligible must be a strict boolean")
        return value

    @field_validator("blocked_reasons")
    @classmethod
    def _validate_blocked_reasons(cls, value: Sequence[str]) -> tuple[str, ...]:
        return _validate_ref_tuple(value, "blockedReasons")

    def public_projection(self) -> dict[str, object]:
        digest = _projection_payload_digest(self)
        if self._canonical_payload_digest != digest or self.projection_digest != digest:
            raise ValueError("public projection was mutated after issuance")
        if self._source_artifact_binding_digest != _artifact_binding_digest(self):
            raise ValueError("public projection is not bound to harness artifacts")
        _validate_projection_source_artifacts(self)
        return {
            "projectionRef": _digest_payload({"projectionId": self.projection_id}),
            "planRef": _digest_payload({"planId": self.plan_id}),
            "parentExecutionRef": _digest_payload(
                {"parentExecutionId": self.parent_execution_id}
            ),
            "assemblyRef": _digest_payload({"assemblyId": self.assembly_id}),
            "beforeCommitVerdictRef": _digest_payload(
                {"beforeCommitVerdictId": self.before_commit_verdict_id}
            ),
            "planDigest": self.plan_digest,
            "inspectionDigest": self.inspection_digest,
            "assemblyDigest": self.assembly_digest,
            "beforeCommitDigest": self.before_commit_digest,
            "planStatus": self.plan_status,
            "childTaskStatuses": tuple(
                child.public_projection() for child in self.child_task_statuses
            ),
            "acceptedChildCount": self.accepted_child_count,
            "retriedChildCount": self.retried_child_count,
            "rejectedChildCount": self.rejected_child_count,
            "blockedChildCount": self.blocked_child_count,
            "evidenceRefCounts": self.evidence_ref_counts.public_projection(),
            "verifierStatus": self.verifier_status,
            "finalProjectionEligible": self.final_projection_eligible,
            "blockedReasons": self.blocked_reasons,
            "activationFlags": self.activation_flags.public_projection(),
            "projectionDigest": self.projection_digest,
        }


def project_meta_orchestration_status(
    projection_id: str,
    *,
    plan: MetaTaskPlan,
    inspection: MetaInspectionLoopResult,
    assembly: MetaFinalAssemblyPlan,
    before_commit_verdict: MetaBeforeCommitVerdict,
    verifier_results: Sequence[RuntimeIssuedMetaVerifierResult] = (),
) -> MetaOrchestrationPublicProjection:
    parsed_plan = _parse_plan(plan)
    parsed_inspection = _parse_inspection(inspection)
    parsed_assembly = _parse_assembly(assembly)
    parsed_before_commit = _parse_before_commit(before_commit_verdict)
    parsed_verifier_results = _parse_runtime_verifier_results(
        parsed_assembly,
        verifier_results,
    )
    _validate_projection_bindings(
        parsed_plan,
        parsed_inspection,
        parsed_assembly,
        parsed_before_commit,
        parsed_verifier_results,
    )
    return MetaOrchestrationPublicProjection._from_harness(
        projection_id=projection_id,
        plan=parsed_plan,
        inspection=parsed_inspection,
        assembly=parsed_assembly,
        before_commit_verdict=parsed_before_commit,
        verifier_results=parsed_verifier_results,
    )


def meta_projection_loop_id_for_plan(plan: MetaTaskPlan) -> str:
    parsed_plan = _parse_plan(plan)
    return _validate_public_ref(
        f"loop:{_plan_artifact_digest(parsed_plan)}",
        "loopId",
    )


def meta_projection_assembly_id_for_inspection(
    inspection: MetaInspectionLoopResult,
) -> str:
    parsed_inspection = _parse_inspection(inspection)
    return _validate_public_ref(
        f"assembly:{_inspection_artifact_digest(parsed_inspection)}",
        "assemblyId",
    )


def _parse_plan(plan: MetaTaskPlan) -> MetaTaskPlan:
    if not isinstance(plan, MetaTaskPlan):
        raise ValueError("public projection requires a MetaTaskPlan")
    return MetaTaskPlan.model_validate(plan.model_dump(by_alias=True, mode="python"))


def _parse_inspection(inspection: MetaInspectionLoopResult) -> MetaInspectionLoopResult:
    if not isinstance(inspection, MetaInspectionLoopResult):
        raise ValueError("public projection requires a MetaInspectionLoopResult")
    inspection.public_projection()
    return MetaInspectionLoopResult.model_validate(
        {
            "loopId": inspection.loop_id,
            "childVerdicts": inspection.child_verdicts,
            "aggregateStatus": inspection.aggregate_status,
            "retryScheduleRefs": inspection.retry_schedule_refs,
            "exhaustedRetryReasons": inspection.exhausted_retry_reasons,
            "acceptedChildEvidenceRefsForAssembly": (
                inspection.accepted_child_evidence_refs_for_assembly
            ),
            "parentExecutedChildTools": inspection.parent_executed_child_tools,
            "defaultOff": inspection.default_off,
        }
    )


def _parse_assembly(assembly: MetaFinalAssemblyPlan) -> MetaFinalAssemblyPlan:
    if not isinstance(assembly, MetaFinalAssemblyPlan):
        raise ValueError("public projection requires a MetaFinalAssemblyPlan")
    assembly.public_projection()
    return assembly


def _parse_before_commit(verdict: MetaBeforeCommitVerdict) -> MetaBeforeCommitVerdict:
    if not isinstance(verdict, MetaBeforeCommitVerdict):
        raise ValueError("public projection requires a MetaBeforeCommitVerdict")
    verdict.public_projection()
    return verdict


def _parse_runtime_verifier_results(
    assembly: MetaFinalAssemblyPlan,
    verifier_results: Sequence[RuntimeIssuedMetaVerifierResult],
) -> tuple[RuntimeIssuedMetaVerifierResult, ...]:
    parsed: list[RuntimeIssuedMetaVerifierResult] = []
    seen_refs: set[str] = set()
    for result in verifier_results:
        if not isinstance(result, RuntimeIssuedMetaVerifierResult):
            raise ValueError("public projection requires runtime-issued verifier results")
        metadata = result.result_for_assembly(assembly)
        ref = _runtime_verifier_result_ref(metadata.verifier_id, metadata.status)
        if ref in seen_refs:
            raise ValueError("runtime verifier results must not contain duplicate refs")
        seen_refs.add(ref)
        parsed.append(result)
    return tuple(parsed)


def _validate_projection_bindings(
    plan: MetaTaskPlan,
    inspection: MetaInspectionLoopResult,
    assembly: MetaFinalAssemblyPlan,
    before_commit_verdict: MetaBeforeCommitVerdict,
    verifier_results: tuple[RuntimeIssuedMetaVerifierResult, ...],
) -> None:
    planned_task_ids = tuple(child.task_id for child in plan.child_task_specs)
    inspected_task_ids = tuple(child.task_id for child in inspection.child_verdicts)
    if inspection.loop_id != meta_projection_loop_id_for_plan(plan):
        raise ValueError("inspection loop is not bound to the current plan")
    if assembly.assembly_id != meta_projection_assembly_id_for_inspection(inspection):
        raise ValueError("assembly is not bound to the current inspection")
    if planned_task_ids != inspected_task_ids:
        raise ValueError("inspection child verdicts must match planned child task order")
    if plan.verifier_chain_refs != assembly.required_verifier_refs:
        raise ValueError("assembly verifier refs must match the parent plan verifier chain")
    if assembly.accepted_child_evidence_refs != inspection.accepted_child_evidence_refs_for_assembly:
        raise ValueError("assembly accepted evidence refs must match inspection output")
    if assembly.excluded_child_refs != _excluded_child_refs_from_inspection(inspection):
        raise ValueError("assembly excluded child refs must match inspection output")
    if before_commit_verdict.assembly_id != assembly.assembly_id:
        raise ValueError("beforeCommit verdict is not bound to the current assembly")
    if before_commit_verdict.assembly_digest != assembly.final_output_digest:
        raise ValueError("beforeCommit verdict digest does not match the current assembly")
    _validate_retry_budget_binding(plan, inspection)
    _validate_required_verifier_result_coverage(
        assembly,
        before_commit_verdict,
        verifier_results,
    )
    if before_commit_verdict.commit_executed is not False:
        raise ValueError("public projection cannot follow committed side effects")
    if before_commit_verdict.transcript_written is not False:
        raise ValueError("public projection cannot write transcripts")
    if before_commit_verdict.sse_written is not False:
        raise ValueError("public projection cannot write SSE events")
    if before_commit_verdict.control_written is not False:
        raise ValueError("public projection cannot write control requests")
    if before_commit_verdict.tool_execution_attached is not False:
        raise ValueError("public projection cannot attach tool execution")


def _validate_retry_budget_binding(
    plan: MetaTaskPlan,
    inspection: MetaInspectionLoopResult,
) -> None:
    for child in inspection.child_verdicts:
        if child.attempt > plan.max_retry_budget:
            raise ValueError("inspected child attempt exceeds parent retry budget")
        if child.verdict.status != "retry":
            continue
        remaining_budget = max(plan.max_retry_budget - child.attempt, 0)
        if child.attempt >= plan.max_retry_budget:
            raise ValueError("retry verdict exceeds parent retry budget")
        if child.verdict.retry_budget_remaining > remaining_budget:
            raise ValueError("retry verdict remaining budget exceeds parent retry budget")


def _validate_required_verifier_result_coverage(
    assembly: MetaFinalAssemblyPlan,
    before_commit_verdict: MetaBeforeCommitVerdict,
    verifier_results: tuple[RuntimeIssuedMetaVerifierResult, ...],
) -> None:
    expected_pass_refs = tuple(
        _runtime_verifier_result_ref(verifier_ref, "pass")
        for verifier_ref in assembly.required_verifier_refs
    )
    runtime_result_refs = tuple(
        _runtime_verifier_result_ref(
            result.result_for_assembly(assembly).verifier_id,
            result.result_for_assembly(assembly).status,
        )
        for result in verifier_results
    )
    if runtime_result_refs != before_commit_verdict.verifier_result_refs:
        raise ValueError("runtime verifier results do not match beforeCommit verdict refs")
    if (
        before_commit_verdict.final_projection_eligible is False
        and before_commit_verdict.verifier_chain_result == "blocked"
    ):
        return
    if assembly.projection_mode != "ready_for_projection":
        raise ValueError("passed beforeCommit verdict requires ready assembly projection")
    if before_commit_verdict.verifier_chain_result != "passed":
        raise ValueError("final eligible projection requires passed verifier chain")
    if before_commit_verdict.final_projection_eligible is not True:
        raise ValueError("passed verifier chain must be final projection eligible")
    if before_commit_verdict.blocked_reasons:
        raise ValueError("passed verifier chain cannot include blocked reasons")
    if before_commit_verdict.verifier_result_refs != expected_pass_refs:
        raise ValueError("passed verifier chain must exactly cover required verifier refs")
    if runtime_result_refs != expected_pass_refs:
        raise ValueError("passed verifier chain requires runtime-issued verifier results")


def _runtime_verifier_result_ref(verifier_id: str, status: str) -> str:
    return _validate_public_ref(
        f"verifier-result:{verifier_id}:{status}",
        "verifierResultRefs",
    )


def _excluded_child_refs_from_inspection(
    inspection: MetaInspectionLoopResult,
) -> tuple[str, ...]:
    return _validate_ref_tuple(
        (
            child.task_id
            for child in inspection.child_verdicts
            if child.verdict.status in {"blocked", "rejected"}
        ),
        "excludedChildRefs",
    )


def _child_statuses_for_plan(
    plan: MetaTaskPlan,
    inspection: MetaInspectionLoopResult,
) -> tuple[MetaProjectionChildTaskStatus, ...]:
    by_task_id = {child.task_id: child for child in inspection.child_verdicts}
    statuses: list[MetaProjectionChildTaskStatus] = []
    for index, child in enumerate(plan.child_task_specs):
        inspected = by_task_id[child.task_id]
        verdict = inspected.verdict
        statuses.append(
            MetaProjectionChildTaskStatus.model_validate(
                {
                    "taskIndex": index,
                    "taskDigest": _digest_payload(
                        {
                            "taskSpec": child.model_dump(
                                by_alias=True,
                                mode="python",
                                warnings=False,
                            )
                        }
                    ),
                    "status": verdict.status,
                    "attempt": inspected.attempt,
                    "acceptedEvidenceRefCount": len(
                        verdict.accepted_evidence_refs
                        if verdict.status == "accepted"
                        else ()
                    ),
                    "missingEvidenceRefCount": len(verdict.missing_evidence_refs),
                    "retryable": verdict.retryable,
                    "retryBudgetRemaining": verdict.retry_budget_remaining,
                    "required": inspected.required,
                }
            )
        )
    return tuple(statuses)


def _child_status_counts(
    statuses: Sequence[MetaProjectionChildTaskStatus],
) -> dict[str, int]:
    return {
        status: sum(1 for child in statuses if child.status == status)
        for status in ("accepted", "retry", "rejected", "blocked")
    }


def _evidence_ref_counts(
    inspection: MetaInspectionLoopResult,
    assembly: MetaFinalAssemblyPlan,
    before_commit_verdict: MetaBeforeCommitVerdict,
) -> MetaProjectionEvidenceRefCounts:
    missing_count = sum(
        len(child.verdict.missing_evidence_refs)
        for child in inspection.child_verdicts
        if child.verdict.status != "accepted"
    )
    return MetaProjectionEvidenceRefCounts.model_validate(
        {
            "accepted": len(assembly.accepted_child_evidence_refs),
            "missing": missing_count,
            "excludedChildren": len(assembly.excluded_child_refs),
            "retryScheduleRefs": len(inspection.retry_schedule_refs),
            "requiredVerifiers": len(assembly.required_verifier_refs),
            "verifierResults": len(before_commit_verdict.verifier_result_refs),
        }
    )


def _final_projection_eligible(
    assembly: MetaFinalAssemblyPlan,
    before_commit_verdict: MetaBeforeCommitVerdict,
) -> bool:
    return (
        assembly.projection_mode == "ready_for_projection"
        and before_commit_verdict.verifier_chain_result == "passed"
        and before_commit_verdict.final_projection_eligible is True
        and before_commit_verdict.commit_executed is False
    )


def _plan_status(
    inspection: MetaInspectionLoopResult,
    assembly: MetaFinalAssemblyPlan,
    before_commit_verdict: MetaBeforeCommitVerdict,
) -> MetaOrchestrationPlanStatus:
    if inspection.aggregate_status == "needs_retry":
        return "needs_retry"
    if _final_projection_eligible(assembly, before_commit_verdict):
        return "ready_for_projection"
    if inspection.aggregate_status == "partial" or assembly.projection_mode == "partial":
        return "partial"
    return "blocked"


def _blocked_reasons(
    inspection: MetaInspectionLoopResult,
    assembly: MetaFinalAssemblyPlan,
    before_commit_verdict: MetaBeforeCommitVerdict,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if inspection.aggregate_status == "needs_retry":
        reasons.append("inspection_needs_retry")
    elif inspection.aggregate_status == "partial":
        reasons.append("inspection_partial")
    elif inspection.aggregate_status == "blocked":
        reasons.append("inspection_blocked")
    if assembly.projection_mode != "ready_for_projection":
        reasons.append(f"assembly_projection_{assembly.projection_mode}")
    reasons.extend(_normalize_blocked_reason(reason) for reason in before_commit_verdict.blocked_reasons)
    return _dedupe_refs(reasons, "blockedReasons")


def _normalize_blocked_reason(reason: str) -> str:
    if reason in {
        "inspection_needs_retry",
        "inspection_partial",
        "inspection_blocked",
        "assembly_projection_blocked",
        "assembly_projection_partial",
    }:
        return reason
    if reason.startswith("verifier_failed:"):
        return "verifier_failed"
    if reason.startswith("verifier_missing:"):
        return "verifier_missing"
    if reason.startswith("verifier_approval_required:"):
        return "verifier_approval_required"
    if reason.startswith("verifier_audit:"):
        return "verifier_audit"
    if reason.startswith("verifier_"):
        return "verifier_blocked"
    return "blocked_other"


def _dedupe_refs(refs: Sequence[str], field_name: str) -> tuple[str, ...]:
    result: list[str] = []
    for ref in refs:
        if ref not in result:
            result.append(ref)
    return _validate_ref_tuple(result, field_name)


def _projection_payload_digest(projection: MetaOrchestrationPublicProjection) -> str:
    payload = json.dumps(
        {
            "projectionId": projection.projection_id,
            "planId": projection.plan_id,
            "parentExecutionId": projection.parent_execution_id,
            "assemblyId": projection.assembly_id,
            "beforeCommitVerdictId": projection.before_commit_verdict_id,
            "planDigest": projection.plan_digest,
            "inspectionDigest": projection.inspection_digest,
            "assemblyDigest": projection.assembly_digest,
            "beforeCommitDigest": projection.before_commit_digest,
            "planStatus": projection.plan_status,
            "childTaskStatuses": tuple(
                child.public_projection() for child in projection.child_task_statuses
            ),
            "acceptedChildCount": projection.accepted_child_count,
            "retriedChildCount": projection.retried_child_count,
            "rejectedChildCount": projection.rejected_child_count,
            "blockedChildCount": projection.blocked_child_count,
            "evidenceRefCounts": projection.evidence_ref_counts.public_projection(),
            "verifierStatus": projection.verifier_status,
            "finalProjectionEligible": projection.final_projection_eligible,
            "blockedReasons": projection.blocked_reasons,
            "activationFlags": projection.activation_flags.public_projection(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _artifact_binding_digest(projection: MetaOrchestrationPublicProjection) -> str:
    return _digest_payload(
        {
            "planDigest": projection.plan_digest,
            "inspectionDigest": projection.inspection_digest,
            "assemblyDigest": projection.assembly_digest,
            "beforeCommitDigest": projection.before_commit_digest,
        }
    )


def _validate_projection_source_artifacts(
    projection: MetaOrchestrationPublicProjection,
) -> None:
    if not isinstance(projection._source_plan, MetaTaskPlan):
        raise ValueError("public projection source plan is missing")
    if not isinstance(projection._source_inspection, MetaInspectionLoopResult):
        raise ValueError("public projection source inspection is missing")
    if not isinstance(projection._source_assembly, MetaFinalAssemblyPlan):
        raise ValueError("public projection source assembly is missing")
    if not isinstance(projection._source_before_commit, MetaBeforeCommitVerdict):
        raise ValueError("public projection source beforeCommit verdict is missing")
    _validate_projection_bindings(
        projection._source_plan,
        projection._source_inspection,
        projection._source_assembly,
        projection._source_before_commit,
        projection._source_verifier_results,
    )
    expected_digests = {
        "planDigest": _plan_artifact_digest(projection._source_plan),
        "inspectionDigest": _inspection_artifact_digest(projection._source_inspection),
        "assemblyDigest": _assembly_artifact_digest(projection._source_assembly),
        "beforeCommitDigest": _before_commit_artifact_digest(projection._source_before_commit),
    }
    actual_digests = {
        "planDigest": projection.plan_digest,
        "inspectionDigest": projection.inspection_digest,
        "assemblyDigest": projection.assembly_digest,
        "beforeCommitDigest": projection.before_commit_digest,
    }
    if actual_digests != expected_digests:
        raise ValueError("public projection artifact digests do not match source artifacts")
    if _stored_projection_values(projection) != _derived_projection_values(
        projection.projection_id,
        projection._source_plan,
        projection._source_inspection,
        projection._source_assembly,
        projection._source_before_commit,
    ):
        raise ValueError("public projection fields do not match source artifacts")


def _stored_projection_values(projection: MetaOrchestrationPublicProjection) -> dict[str, object]:
    return {
        "projectionId": projection.projection_id,
        "planId": projection.plan_id,
        "parentExecutionId": projection.parent_execution_id,
        "assemblyId": projection.assembly_id,
        "beforeCommitVerdictId": projection.before_commit_verdict_id,
        "planDigest": projection.plan_digest,
        "inspectionDigest": projection.inspection_digest,
        "assemblyDigest": projection.assembly_digest,
        "beforeCommitDigest": projection.before_commit_digest,
        "planStatus": projection.plan_status,
        "childTaskStatuses": tuple(
            child.public_projection() for child in projection.child_task_statuses
        ),
        "acceptedChildCount": projection.accepted_child_count,
        "retriedChildCount": projection.retried_child_count,
        "rejectedChildCount": projection.rejected_child_count,
        "blockedChildCount": projection.blocked_child_count,
        "evidenceRefCounts": projection.evidence_ref_counts.public_projection(),
        "verifierStatus": projection.verifier_status,
        "finalProjectionEligible": projection.final_projection_eligible,
        "blockedReasons": projection.blocked_reasons,
        "activationFlags": projection.activation_flags.public_projection(),
    }


def _derived_projection_values(
    projection_id: str,
    plan: MetaTaskPlan,
    inspection: MetaInspectionLoopResult,
    assembly: MetaFinalAssemblyPlan,
    before_commit_verdict: MetaBeforeCommitVerdict,
) -> dict[str, object]:
    child_statuses = _child_statuses_for_plan(plan, inspection)
    status_counts = _child_status_counts(child_statuses)
    return {
        "projectionId": projection_id,
        "planId": plan.plan_id,
        "parentExecutionId": plan.parent_execution_id,
        "assemblyId": assembly.assembly_id,
        "beforeCommitVerdictId": before_commit_verdict.verdict_id,
        "planDigest": _plan_artifact_digest(plan),
        "inspectionDigest": _inspection_artifact_digest(inspection),
        "assemblyDigest": _assembly_artifact_digest(assembly),
        "beforeCommitDigest": _before_commit_artifact_digest(before_commit_verdict),
        "planStatus": _plan_status(inspection, assembly, before_commit_verdict),
        "childTaskStatuses": tuple(child.public_projection() for child in child_statuses),
        "acceptedChildCount": status_counts["accepted"],
        "retriedChildCount": status_counts["retry"],
        "rejectedChildCount": status_counts["rejected"],
        "blockedChildCount": status_counts["blocked"],
        "evidenceRefCounts": _evidence_ref_counts(
            inspection,
            assembly,
            before_commit_verdict,
        ).public_projection(),
        "verifierStatus": before_commit_verdict.verifier_chain_result,
        "finalProjectionEligible": _final_projection_eligible(assembly, before_commit_verdict),
        "blockedReasons": _blocked_reasons(inspection, assembly, before_commit_verdict),
        "activationFlags": MetaProjectionActivationFlags().public_projection(),
    }


def _plan_artifact_digest(plan: MetaTaskPlan) -> str:
    return _digest_payload(plan.model_dump(by_alias=True, mode="python", warnings=False))


def _inspection_artifact_digest(inspection: MetaInspectionLoopResult) -> str:
    return _digest_payload(
        {
            "loopId": inspection.loop_id,
            "childVerdicts": tuple(
                {
                    "taskId": child.task_id,
                    "required": child.required,
                    "attempt": child.attempt,
                    "verdict": child.verdict.model_dump(
                        by_alias=True,
                        mode="python",
                        warnings=False,
                    ),
                }
                for child in inspection.child_verdicts
            ),
            "aggregateStatus": inspection.aggregate_status,
            "retryScheduleRefs": inspection.retry_schedule_refs,
            "exhaustedRetryReasons": inspection.exhausted_retry_reasons,
            "acceptedChildEvidenceRefsForAssembly": (
                inspection.accepted_child_evidence_refs_for_assembly
            ),
            "parentExecutedChildTools": inspection.parent_executed_child_tools,
            "defaultOff": inspection.default_off,
        }
    )


def _assembly_artifact_digest(assembly: MetaFinalAssemblyPlan) -> str:
    return _digest_payload(
        {
            "assemblyId": assembly.assembly_id,
            "acceptedChildEvidenceRefs": assembly.accepted_child_evidence_refs,
            "excludedChildRefs": assembly.excluded_child_refs,
            "requiredVerifierRefs": assembly.required_verifier_refs,
            "finalOutputDigest": assembly.final_output_digest,
            "projectionMode": assembly.projection_mode,
            "rawChildTranscriptUsed": assembly.raw_child_transcript_used,
            "defaultOff": assembly.default_off,
        }
    )


def _before_commit_artifact_digest(verdict: MetaBeforeCommitVerdict) -> str:
    return _digest_payload(
        {
            "verdictId": verdict.verdict_id,
            "assemblyId": verdict.assembly_id,
            "assemblyDigest": verdict.assembly_digest,
            "verifierChainResult": verdict.verifier_chain_result,
            "verifierResultRefs": verdict.verifier_result_refs,
            "blockedReasons": verdict.blocked_reasons,
            "retryableReasons": verdict.retryable_reasons,
            "finalProjectionEligible": verdict.final_projection_eligible,
            "commitIntentRefs": verdict.commit_intent_refs,
            "commitExecuted": verdict.commit_executed,
            "transcriptWritten": verdict.transcript_written,
            "sseWritten": verdict.sse_written,
            "controlWritten": verdict.control_written,
            "toolExecutionAttached": verdict.tool_execution_attached,
            "defaultOff": verdict.default_off,
        }
    )


def _digest_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _validate_digest_ref(value: str, field_name: str) -> str:
    clean = _validate_public_ref(value, field_name)
    if not clean.startswith("sha256:") or len(clean) != len("sha256:") + 64:
        raise ValueError(f"{field_name} must be a sha256 digest ref")
    return clean


__all__ = [
    "MetaOrchestrationPlanStatus",
    "MetaOrchestrationPublicProjection",
    "MetaProjectionActivationFlags",
    "MetaProjectionChildTaskStatus",
    "MetaProjectionEvidenceRefCounts",
    "meta_projection_assembly_id_for_inspection",
    "meta_projection_loop_id_for_plan",
    "project_meta_orchestration_status",
]
