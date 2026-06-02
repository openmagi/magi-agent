from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from magi_agent.harness.verifier_bus import VerifierResultMetadata
from magi_agent.meta_orchestration.child_acceptance import ChildAcceptanceVerdict
from magi_agent.meta_orchestration.commit_adapter import (
    MetaBeforeCommitVerdict,
    RuntimeIssuedMetaVerifierResult,
    evaluate_before_commit_for_assembly,
    issue_runtime_verifier_result_for_assembly,
)
from magi_agent.meta_orchestration.final_assembly import (
    MetaFinalAssemblyPlan,
    assemble_final_output_from_inspection,
)
from magi_agent.meta_orchestration.inspection_loop import (
    MetaInspectedChildVerdict,
    MetaInspectionLoopResult,
    inspect_child_verdicts,
)
from magi_agent.meta_orchestration.projection import (
    MetaOrchestrationPublicProjection,
    meta_projection_assembly_id_for_inspection,
    meta_projection_loop_id_for_plan,
    project_meta_orchestration_status,
)
from magi_agent.meta_orchestration.task_plan import (
    MetaChildTaskSpec,
    MetaTaskPlan,
)


def _child(task_id: str, *, role_ref: str = "role:opaque-worker") -> MetaChildTaskSpec:
    return MetaChildTaskSpec.model_validate(
        {
            "taskId": task_id,
            "roleRef": role_ref,
            "scopeRef": f"scope:{task_id}",
            "allowedToolRefs": ("tool:readonly-evidence",),
            "contextBudget": {
                "maxInputTokens": 1000,
                "maxOutputTokens": 500,
                "reservedEvidenceTokens": 100,
            },
            "completionContractRef": "contract:runtime-evidence-envelope",
            "deliveryMode": "return",
        }
    )


def _plan(
    *children: MetaChildTaskSpec,
    plan_id: str = "plan:projection",
    parent_execution_id: str = "parent:projection",
    verifier_refs: tuple[str, ...] = ("verifier:meta-before-commit",),
    max_retry_budget: int = 1,
) -> MetaTaskPlan:
    return MetaTaskPlan.model_validate(
        {
            "planId": plan_id,
            "parentExecutionId": parent_execution_id,
            "objectiveDigest": "sha256:" + "a" * 64,
            "objectivePreview": "Assemble accepted child evidence behind local gates.",
            "acceptanceCriteriaRefs": ("criteria:accepted-evidence-only",),
            "childTaskSpecs": children or (_child("task-a"), _child("task-b")),
            "verifierChainRefs": verifier_refs,
            "maxRetryBudget": max_retry_budget,
        }
    )


def _accepted(task_id: str, *evidence_refs: str) -> MetaInspectedChildVerdict:
    return MetaInspectedChildVerdict.model_validate(
        {
            "taskId": task_id,
            "required": True,
            "attempt": 0,
            "verdict": ChildAcceptanceVerdict._from_evaluation(
                status="accepted",
                reason_codes=("accepted",),
                accepted_evidence_refs=evidence_refs,
                missing_evidence_refs=(),
                retryable=False,
                retry_budget_remaining=1,
            ),
        }
    )


def _retry(
    task_id: str,
    *,
    attempt: int = 0,
    remaining: int = 1,
) -> MetaInspectedChildVerdict:
    return MetaInspectedChildVerdict.model_validate(
        {
            "taskId": task_id,
            "required": True,
            "attempt": attempt,
            "verdict": ChildAcceptanceVerdict._from_evaluation(
                status="retry",
                reason_codes=("missing_required_evidence",),
                accepted_evidence_refs=("ledger:partial",),
                missing_evidence_refs=("audit:missing",),
                retryable=True,
                retry_budget_remaining=remaining,
            ),
        }
    )


def _rejected(task_id: str) -> MetaInspectedChildVerdict:
    return MetaInspectedChildVerdict.model_validate(
        {
            "taskId": task_id,
            "required": True,
            "attempt": 1,
            "verdict": ChildAcceptanceVerdict._from_evaluation(
                status="rejected",
                reason_codes=("missing_required_evidence", "retry_budget_exhausted"),
                accepted_evidence_refs=("ledger:rejected-partial",),
                missing_evidence_refs=("audit:missing",),
                retryable=False,
                retry_budget_remaining=0,
            ),
        }
    )


def _inspection(
    *children: MetaInspectedChildVerdict,
    plan: MetaTaskPlan | None = None,
    private_notes: tuple[str, ...] = (),
) -> MetaInspectionLoopResult:
    bound_plan = plan or _plan()
    return inspect_child_verdicts(
        meta_projection_loop_id_for_plan(bound_plan),
        children or (_accepted("task-a", "ledger:a", "receipt:a"), _accepted("task-b", "ledger:b")),
        private_notes=private_notes,
    )


def _ready_assembly(
    inspection: MetaInspectionLoopResult | None = None,
    *,
    plan: MetaTaskPlan | None = None,
) -> MetaFinalAssemblyPlan:
    bound_plan = plan or _plan()
    bound_inspection = inspection or _inspection(plan=bound_plan)
    return assemble_final_output_from_inspection(
        meta_projection_assembly_id_for_inspection(bound_inspection),
        bound_inspection,
        required_verifier_refs=bound_plan.verifier_chain_refs,
        satisfied_verifier_refs=bound_plan.verifier_chain_refs,
    )


def _verifier(
    verifier_id: str = "verifier:meta-before-commit",
    *,
    status: str = "pass",
    public_summary: str | None = None,
    failure_message: str | None = None,
) -> VerifierResultMetadata:
    return VerifierResultMetadata(
        verifierId=verifier_id,
        status=status,
        publicSummary=public_summary,
        failureMessage=failure_message,
    )


def _before_commit(
    assembly: MetaFinalAssemblyPlan,
    *,
    verdict_id: str = "before-commit:projection",
    verifier_id: str = "verifier:meta-before-commit",
    status: str = "pass",
    public_summary: str | None = None,
    failure_message: str | None = None,
) -> MetaBeforeCommitVerdict:
    verdict, _ = _before_commit_with_results(
        assembly,
        verdict_id=verdict_id,
        verifier_id=verifier_id,
        status=status,
        public_summary=public_summary,
        failure_message=failure_message,
    )
    return verdict


def _before_commit_with_results(
    assembly: MetaFinalAssemblyPlan,
    *,
    verdict_id: str = "before-commit:projection",
    verifier_id: str = "verifier:meta-before-commit",
    status: str = "pass",
    public_summary: str | None = None,
    failure_message: str | None = None,
) -> tuple[MetaBeforeCommitVerdict, tuple[RuntimeIssuedMetaVerifierResult, ...]]:
    issued = issue_runtime_verifier_result_for_assembly(
        assembly,
        _verifier(
            verifier_id,
            status=status,
            public_summary=public_summary,
            failure_message=failure_message,
        ),
        verifier_bus_run_id="verifier-run:projection",
        policy_snapshot_id="policy:projection",
    )
    return evaluate_before_commit_for_assembly(
        verdict_id,
        assembly,
        verifier_results=(issued,),
    ), (issued,)


def test_public_projection_reports_digest_safe_run_status_counts_and_activation_flags() -> None:
    plan = _plan()
    inspection = _inspection(plan=plan)
    assembly = _ready_assembly(inspection, plan=plan)
    before_commit, verifier_results = _before_commit_with_results(assembly)

    projection = project_meta_orchestration_status(
        "projection:ready",
        plan=plan,
        inspection=inspection,
        assembly=assembly,
        before_commit_verdict=before_commit,
        verifier_results=verifier_results,
    )

    assert projection.plan_status == "ready_for_projection"
    assert projection.verifier_status == "passed"
    assert projection.final_projection_eligible is True
    assert projection.accepted_child_count == 2
    assert projection.retried_child_count == 0
    assert projection.rejected_child_count == 0
    assert projection.evidence_ref_counts.accepted == 3
    assert projection.evidence_ref_counts.required_verifiers == 1
    assert projection.evidence_ref_counts.verifier_results == 1
    assert tuple(status.status for status in projection.child_task_statuses) == (
        "accepted",
        "accepted",
    )

    public = projection.public_projection()
    assert public["activationFlags"] == {
        "defaultOff": True,
        "localOnly": True,
        "fakeProviderOnly": True,
        "toolExecutionAllowed": False,
        "childExecutionAllowed": False,
        "modelCallAllowed": False,
        "workspaceWriteAllowed": False,
        "memoryWriteAllowed": False,
        "webBrowserAttached": False,
        "channelWriteAttached": False,
        "routeAttached": False,
        "productionAuthority": False,
        "liveExecutionAllowed": False,
        "adkRunnerAttached": False,
    }
    assert public["projectionDigest"].startswith("sha256:")

    dumped = json.dumps(public, sort_keys=True)
    for unsafe in ("ledger:a", "receipt:a", "acceptedChildEvidenceRefs", "verifierResultRefs"):
        assert unsafe not in dumped


def test_before_commit_failure_blocks_public_projection_without_leaking_raw_inputs() -> None:
    plan = _plan()
    inspection = _inspection(
        plan=plan,
        private_notes=(
            "raw child transcript with hidden reasoning",
            "toolArgs={'cookie':'session-secret'}",
        )
    )
    assembly = _ready_assembly(inspection, plan=plan)
    before_commit, verifier_results = _before_commit_with_results(
        assembly,
        status="failed",
        public_summary="raw source text from /Users/kevin/private with token=sk-test-secret",
        failure_message="tool result contained Authorization: Bearer unsafe-token",
    )

    projection = project_meta_orchestration_status(
        "projection:blocked",
        plan=plan,
        inspection=inspection,
        assembly=assembly,
        before_commit_verdict=before_commit,
        verifier_results=verifier_results,
    )

    assert projection.plan_status == "blocked"
    assert projection.verifier_status == "blocked"
    assert projection.final_projection_eligible is False
    assert projection.blocked_reasons == ("verifier_failed",)

    dumped = json.dumps(projection.public_projection(), sort_keys=True)
    for unsafe in (
        "raw child transcript",
        "hidden reasoning",
        "toolArgs",
        "cookie",
        "raw source text",
        "/Users/kevin/private",
        "sk-test-secret",
        "Authorization",
        "Bearer unsafe-token",
        "ledger:a",
        "receipt:a",
    ):
        assert unsafe not in dumped


def test_projection_requires_before_commit_verdict_bound_to_current_assembly() -> None:
    plan = _plan()
    inspection = _inspection(plan=plan)
    assembly = _ready_assembly(inspection, plan=plan)
    other_inspection = inspect_child_verdicts(
        meta_projection_loop_id_for_plan(plan),
        (_accepted("task-a", "ledger:other"), _accepted("task-b", "ledger:b")),
    )
    other_assembly = assemble_final_output_from_inspection(
        meta_projection_assembly_id_for_inspection(other_inspection),
        other_inspection,
        required_verifier_refs=("verifier:meta-before-commit",),
        satisfied_verifier_refs=("verifier:meta-before-commit",),
    )

    with pytest.raises(ValueError):
        project_meta_orchestration_status(
            "projection:wrong-before-commit",
            plan=plan,
            inspection=inspection,
            assembly=assembly,
            before_commit_verdict=_before_commit(other_assembly),
        )
    with pytest.raises(ValueError):
        project_meta_orchestration_status(
            "projection:raw-before-commit",
            plan=plan,
            inspection=inspection,
            assembly=assembly,
            before_commit_verdict=other_assembly.public_projection(),
        )


def test_projection_rejects_plan_inspection_and_assembly_mismatches() -> None:
    plan = _plan(_child("task-a"), _child("task-b"))
    incomplete_inspection = inspect_child_verdicts(
        meta_projection_loop_id_for_plan(plan),
        (_accepted("task-a", "ledger:a"),),
    )
    assembly = _ready_assembly(incomplete_inspection, plan=plan)

    with pytest.raises(ValueError):
        project_meta_orchestration_status(
            "projection:missing-child",
            plan=plan,
            inspection=incomplete_inspection,
            assembly=assembly,
            before_commit_verdict=_before_commit(assembly),
        )

    inspection = _inspection(plan=plan)
    mutated_assembly = _ready_assembly(inspection, plan=plan)
    object.__setattr__(mutated_assembly, "accepted_child_evidence_refs", ("ledger:forged",))

    with pytest.raises(ValueError):
        project_meta_orchestration_status(
            "projection:mutated-assembly",
            plan=plan,
            inspection=inspection,
            assembly=mutated_assembly,
            before_commit_verdict=_before_commit(_ready_assembly(inspection, plan=plan)),
        )


def test_projection_rejects_stale_plan_with_same_task_ids_and_verifier_refs() -> None:
    original_plan = _plan(_child("task-a"), _child("task-b"))
    stale_inspection = _inspection(plan=original_plan)
    stale_assembly = _ready_assembly(stale_inspection, plan=original_plan)
    stale_before_commit, stale_verifier_results = _before_commit_with_results(stale_assembly)
    different_plan_same_task_ids = _plan(
        _child("task-a", role_ref="role:different-worker"),
        _child("task-b"),
    )

    with pytest.raises(ValueError):
        project_meta_orchestration_status(
            "projection:stale-plan",
            plan=different_plan_same_task_ids,
            inspection=stale_inspection,
            assembly=stale_assembly,
            before_commit_verdict=stale_before_commit,
            verifier_results=stale_verifier_results,
        )


def test_passed_projection_requires_exact_required_verifier_result_refs() -> None:
    import magi_agent.meta_orchestration.commit_adapter as commit_adapter

    plan = _plan(verifier_refs=("verifier:meta-before-commit", "verifier:second"))
    inspection = _inspection(plan=plan)
    assembly = _ready_assembly(inspection, plan=plan)
    missing_second = evaluate_before_commit_for_assembly(
        "before-commit:missing-second",
        assembly,
        verifier_results=(
            issue_runtime_verifier_result_for_assembly(
                assembly,
                _verifier("verifier:meta-before-commit", status="pass"),
                verifier_bus_run_id="verifier-run:projection",
                policy_snapshot_id="policy:projection",
            ),
        ),
    )

    assert missing_second.final_projection_eligible is False

    forged_passed = MetaBeforeCommitVerdict(
        _before_commit_verdict_token=getattr(commit_adapter, "_BEFORE_COMMIT_VERDICT_TOKEN"),
        verdictId="before-commit:forged-pass",
        assemblyId=assembly.assembly_id,
        assemblyDigest=assembly.final_output_digest,
        verifierChainResult="passed",
        verifierResultRefs=("verifier-result:verifier:meta-before-commit:pass",),
        blockedReasons=(),
        retryableReasons=(),
        finalProjectionEligible=True,
        commitIntentRefs=(),
        commitExecuted=False,
        transcriptWritten=False,
        sseWritten=False,
        controlWritten=False,
        toolExecutionAttached=False,
        defaultOff=True,
    )

    with pytest.raises(ValueError):
        project_meta_orchestration_status(
            "projection:forced-pass",
            plan=plan,
            inspection=inspection,
            assembly=assembly,
            before_commit_verdict=forged_passed,
        )

    exact_ref_forged = MetaBeforeCommitVerdict(
        _before_commit_verdict_token=getattr(commit_adapter, "_BEFORE_COMMIT_VERDICT_TOKEN"),
        verdictId="before-commit:forged-exact-pass",
        assemblyId=assembly.assembly_id,
        assemblyDigest=assembly.final_output_digest,
        verifierChainResult="passed",
        verifierResultRefs=(
            "verifier-result:verifier:meta-before-commit:pass",
            "verifier-result:verifier:second:pass",
        ),
        blockedReasons=(),
        retryableReasons=(),
        finalProjectionEligible=True,
        commitIntentRefs=(),
        commitExecuted=False,
        transcriptWritten=False,
        sseWritten=False,
        controlWritten=False,
        toolExecutionAttached=False,
        defaultOff=True,
    )

    with pytest.raises(ValueError):
        project_meta_orchestration_status(
            "projection:forced-exact-pass",
            plan=plan,
            inspection=inspection,
            assembly=assembly,
            before_commit_verdict=exact_ref_forged,
        )


def test_retry_and_rejection_counts_are_public_without_accepting_rejected_evidence() -> None:
    plan = _plan()
    inspection = _inspection(_accepted("task-a", "ledger:a"), _rejected("task-b"), plan=plan)
    assembly = assemble_final_output_from_inspection(
        meta_projection_assembly_id_for_inspection(inspection),
        inspection,
        required_verifier_refs=("verifier:meta-before-commit",),
        satisfied_verifier_refs=("verifier:meta-before-commit",),
    )
    before_commit = evaluate_before_commit_for_assembly(
        "before-commit:blocked",
        assembly,
        verifier_results=(),
    )

    projection = project_meta_orchestration_status(
        "projection:rejected",
        plan=plan,
        inspection=inspection,
        assembly=assembly,
        before_commit_verdict=before_commit,
    )

    assert projection.plan_status == "blocked"
    assert projection.accepted_child_count == 1
    assert projection.rejected_child_count == 1
    assert projection.retried_child_count == 0
    assert projection.evidence_ref_counts.accepted == 1
    assert projection.evidence_ref_counts.excluded_children == 1

    dumped = json.dumps(projection.public_projection(), sort_keys=True)
    assert "ledger:rejected-partial" not in dumped
    assert "ledger:a" not in dumped

    retry_inspection = _inspection(_accepted("task-a", "ledger:a"), _retry("task-b"), plan=plan)
    retry_assembly = assemble_final_output_from_inspection(
        meta_projection_assembly_id_for_inspection(retry_inspection),
        retry_inspection,
        required_verifier_refs=("verifier:meta-before-commit",),
    )
    retry_before_commit = evaluate_before_commit_for_assembly(
        "before-commit:retry",
        retry_assembly,
        verifier_results=(),
    )
    retry_projection = project_meta_orchestration_status(
        "projection:retry",
        plan=plan,
        inspection=retry_inspection,
        assembly=retry_assembly,
        before_commit_verdict=retry_before_commit,
    )

    assert retry_projection.plan_status == "needs_retry"
    assert retry_projection.retried_child_count == 1
    assert retry_projection.evidence_ref_counts.retry_schedule_refs == 1


def test_projection_rejects_retry_state_beyond_parent_plan_budget() -> None:
    plan = _plan(max_retry_budget=1)
    inspection = _inspection(_accepted("task-a", "ledger:a"), _retry("task-b", remaining=10), plan=plan)
    assembly = assemble_final_output_from_inspection(
        meta_projection_assembly_id_for_inspection(inspection),
        inspection,
        required_verifier_refs=plan.verifier_chain_refs,
    )
    before_commit = evaluate_before_commit_for_assembly(
        "before-commit:retry-over-budget",
        assembly,
        verifier_results=(),
    )

    with pytest.raises(ValueError):
        project_meta_orchestration_status(
            "projection:retry-over-budget",
            plan=plan,
            inspection=inspection,
            assembly=assembly,
            before_commit_verdict=before_commit,
        )


def test_public_projection_normalizes_semantic_task_and_verifier_refs() -> None:
    plan = _plan(
        _child("task:research_searcher"),
        _child("task:code_editor"),
        plan_id="plan:research_searcher",
        parent_execution_id="parent:coding_review",
        verifier_refs=("verifier:research_verifier",),
    )
    inspection = _inspection(
        _accepted("task:research_searcher", "ledger:source"),
        _accepted("task:code_editor", "ledger:diff"),
        plan=plan,
    )
    assembly = _ready_assembly(inspection, plan=plan)
    before_commit, verifier_results = _before_commit_with_results(
        assembly,
        verdict_id="before-commit:research_verifier",
        verifier_id="verifier:research_verifier",
        status="failed",
    )
    projection = project_meta_orchestration_status(
        "projection:research_searcher",
        plan=plan,
        inspection=inspection,
        assembly=assembly,
        before_commit_verdict=before_commit,
        verifier_results=verifier_results,
    )

    dumped = json.dumps(projection.public_projection(), sort_keys=True)
    for unsafe in (
        "research_searcher",
        "code_editor",
        "research_verifier",
        "coding_review",
        "ledger:source",
        "ledger:diff",
    ):
        assert unsafe not in dumped
    assert projection.blocked_reasons == ("verifier_failed",)


def test_public_projection_normalizes_unknown_blocked_reason_refs() -> None:
    import magi_agent.meta_orchestration.commit_adapter as commit_adapter

    plan = _plan()
    inspection = _inspection(plan=plan)
    assembly = _ready_assembly(inspection, plan=plan)
    before_commit = MetaBeforeCommitVerdict(
        _before_commit_verdict_token=getattr(commit_adapter, "_BEFORE_COMMIT_VERDICT_TOKEN"),
        verdictId="before-commit:semantic-block",
        assemblyId=assembly.assembly_id,
        assemblyDigest=assembly.final_output_digest,
        verifierChainResult="blocked",
        verifierResultRefs=(),
        blockedReasons=("research_verifier",),
        retryableReasons=(),
        finalProjectionEligible=False,
        commitIntentRefs=(),
        commitExecuted=False,
        transcriptWritten=False,
        sseWritten=False,
        controlWritten=False,
        toolExecutionAttached=False,
        defaultOff=True,
    )

    projection = project_meta_orchestration_status(
        "projection:semantic-block",
        plan=plan,
        inspection=inspection,
        assembly=assembly,
        before_commit_verdict=before_commit,
    )

    dumped = json.dumps(projection.public_projection(), sort_keys=True)
    assert "research_verifier" not in dumped
    assert projection.blocked_reasons == ("blocked_other",)


def test_projection_model_cannot_be_forged_or_runtime_enabled_directly() -> None:
    with pytest.raises(TypeError):
        MetaOrchestrationPublicProjection.model_validate(
            {
                "projectionId": "projection:forged",
                "planId": "plan:projection",
                "parentExecutionId": "parent:projection",
                "assemblyId": "assembly:projection",
                "beforeCommitVerdictId": "before-commit:projection",
                "planDigest": "sha256:" + "c" * 64,
                "inspectionDigest": "sha256:" + "d" * 64,
                "assemblyDigest": "sha256:" + "e" * 64,
                "beforeCommitDigest": "sha256:" + "f" * 64,
                "planStatus": "ready_for_projection",
                "childTaskStatuses": (),
                "acceptedChildCount": 0,
                "retriedChildCount": 0,
                "rejectedChildCount": 0,
                "blockedChildCount": 0,
                "evidenceRefCounts": {
                    "accepted": 0,
                    "missing": 0,
                    "excludedChildren": 0,
                    "retryScheduleRefs": 0,
                    "requiredVerifiers": 0,
                    "verifierResults": 0,
                },
                "verifierStatus": "passed",
                "finalProjectionEligible": True,
                "blockedReasons": (),
                "activationFlags": {
                    "defaultOff": True,
                    "localOnly": True,
                    "fakeProviderOnly": True,
                    "toolExecutionAllowed": True,
                    "childExecutionAllowed": False,
                    "modelCallAllowed": False,
                    "workspaceWriteAllowed": False,
                    "memoryWriteAllowed": False,
                    "webBrowserAttached": False,
                    "channelWriteAttached": False,
                    "routeAttached": False,
                    "productionAuthority": False,
                    "liveExecutionAllowed": False,
                    "adkRunnerAttached": False,
                },
                "projectionDigest": "sha256:" + "b" * 64,
            }
        )
    import magi_agent.meta_orchestration.projection as projection_module

    forged = MetaOrchestrationPublicProjection(
        _projection_token=getattr(projection_module, "_PUBLIC_PROJECTION_TOKEN"),
        projectionId="projection:forged-direct",
        planId="plan:projection",
        parentExecutionId="parent:projection",
        assemblyId="assembly:projection",
        beforeCommitVerdictId="before-commit:projection",
        planDigest="sha256:" + "c" * 64,
        inspectionDigest="sha256:" + "d" * 64,
        assemblyDigest="sha256:" + "e" * 64,
        beforeCommitDigest="sha256:" + "f" * 64,
        planStatus="ready_for_projection",
        childTaskStatuses=(),
        acceptedChildCount=0,
        retriedChildCount=0,
        rejectedChildCount=0,
        blockedChildCount=0,
        evidenceRefCounts={
            "accepted": 0,
            "missing": 0,
            "excludedChildren": 0,
            "retryScheduleRefs": 0,
            "requiredVerifiers": 0,
            "verifierResults": 0,
        },
        verifierStatus="passed",
        finalProjectionEligible=True,
        blockedReasons=(),
        activationFlags={
            "defaultOff": True,
            "localOnly": True,
            "fakeProviderOnly": True,
            "toolExecutionAllowed": False,
            "childExecutionAllowed": False,
            "modelCallAllowed": False,
            "workspaceWriteAllowed": False,
            "memoryWriteAllowed": False,
            "webBrowserAttached": False,
            "channelWriteAttached": False,
            "routeAttached": False,
            "productionAuthority": False,
            "liveExecutionAllowed": False,
            "adkRunnerAttached": False,
        },
        projectionDigest="sha256:" + "b" * 64,
    )

    with pytest.raises(ValueError):
        forged.public_projection()
    object.__setattr__(
        forged,
        "_source_artifact_binding_digest",
        getattr(projection_module, "_artifact_binding_digest")(forged),
    )
    with pytest.raises(ValueError):
        forged.public_projection()

    plan = _plan()
    inspection = _inspection(_accepted("task-a", "ledger:a"), _rejected("task-b"), plan=plan)
    assembly = assemble_final_output_from_inspection(
        meta_projection_assembly_id_for_inspection(inspection),
        inspection,
        required_verifier_refs=plan.verifier_chain_refs,
    )
    before_commit = evaluate_before_commit_for_assembly(
        "before-commit:blocked-source",
        assembly,
        verifier_results=(),
    )
    forged_ready = MetaOrchestrationPublicProjection(
        _projection_token=getattr(projection_module, "_PUBLIC_PROJECTION_TOKEN"),
        projectionId="projection:forged-source",
        planId=plan.plan_id,
        parentExecutionId=plan.parent_execution_id,
        assemblyId=assembly.assembly_id,
        beforeCommitVerdictId=before_commit.verdict_id,
        planDigest=getattr(projection_module, "_plan_artifact_digest")(plan),
        inspectionDigest=getattr(projection_module, "_inspection_artifact_digest")(inspection),
        assemblyDigest=getattr(projection_module, "_assembly_artifact_digest")(assembly),
        beforeCommitDigest=getattr(projection_module, "_before_commit_artifact_digest")(
            before_commit
        ),
        planStatus="ready_for_projection",
        childTaskStatuses=(),
        acceptedChildCount=2,
        retriedChildCount=0,
        rejectedChildCount=0,
        blockedChildCount=0,
        evidenceRefCounts={
            "accepted": 2,
            "missing": 0,
            "excludedChildren": 0,
            "retryScheduleRefs": 0,
            "requiredVerifiers": 1,
            "verifierResults": 1,
        },
        verifierStatus="passed",
        finalProjectionEligible=True,
        blockedReasons=(),
        activationFlags={
            "defaultOff": True,
            "localOnly": True,
            "fakeProviderOnly": True,
            "toolExecutionAllowed": False,
            "childExecutionAllowed": False,
            "modelCallAllowed": False,
            "workspaceWriteAllowed": False,
            "memoryWriteAllowed": False,
            "webBrowserAttached": False,
            "channelWriteAttached": False,
            "routeAttached": False,
            "productionAuthority": False,
            "liveExecutionAllowed": False,
            "adkRunnerAttached": False,
        },
        projectionDigest="sha256:" + "b" * 64,
    )
    object.__setattr__(
        forged_ready,
        "_source_artifact_binding_digest",
        getattr(projection_module, "_artifact_binding_digest")(forged_ready),
    )
    object.__setattr__(forged_ready, "_source_plan", plan)
    object.__setattr__(forged_ready, "_source_inspection", inspection)
    object.__setattr__(forged_ready, "_source_assembly", assembly)
    object.__setattr__(forged_ready, "_source_before_commit", before_commit)
    object.__setattr__(forged_ready, "_source_verifier_results", ())

    with pytest.raises(ValueError):
        forged_ready.public_projection()


def test_public_projection_detects_post_issue_mutation() -> None:
    plan = _plan()
    inspection = _inspection(plan=plan)
    assembly = _ready_assembly(inspection, plan=plan)
    before_commit, verifier_results = _before_commit_with_results(assembly)
    projection = project_meta_orchestration_status(
        "projection:mutation",
        plan=plan,
        inspection=inspection,
        assembly=assembly,
        before_commit_verdict=before_commit,
        verifier_results=verifier_results,
    )

    object.__setattr__(projection, "final_projection_eligible", True)
    object.__setattr__(projection.activation_flags, "production_authority", True)

    with pytest.raises(ValueError):
        projection.public_projection()


def test_meta_projection_module_remains_domain_neutral() -> None:
    import magi_agent.meta_orchestration.projection as projection_module

    source = inspect.getsource(projection_module)
    package_root = Path(projection_module.__file__).resolve().parents[1]
    generic_sources = tuple((package_root / "meta_orchestration").rglob("*.py"))

    assert "magi_agent.research" not in source
    assert "magi_agent.coding" not in source
    for path in generic_sources:
        module_source = path.read_text()
        for forbidden in (
            "research_searcher",
            "source_inspector",
            "claim_mapper",
            "research_verifier",
            "synthesis_reviewer",
            "code_reader",
            "code_editor",
            "test_runner",
            "code_reviewer",
            "citation_policy",
        ):
            assert forbidden not in module_source, path
