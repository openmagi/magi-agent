from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    ChildRuntimeEnvelopeAuthorityFlags,
)
from runtime_issuance_support import issue_test_runtime_authority
from magi_agent.evidence.subagent import (
    DelegatedEvidenceRequirement,
    EvidenceBoundaryLedgerRef,
    ExecutionBoundaryIdentity,
)
from magi_agent.harness.verifier_bus import VerifierResultMetadata
from magi_agent.meta_orchestration.child_acceptance import (
    ChildAcceptancePolicy,
    ChildAcceptanceVerdict,
    accept_child_result,
    issue_runtime_child_result,
)
from magi_agent.meta_orchestration.commit_adapter import (
    evaluate_before_commit_for_assembly,
    issue_runtime_verifier_result_for_assembly,
)
from magi_agent.meta_orchestration.final_assembly import (
    assemble_final_output_from_inspection,
)
from magi_agent.meta_orchestration.inspection_loop import (
    MetaInspectedChildVerdict,
    inspect_child_verdicts,
)
from magi_agent.meta_orchestration.projection import (
    MetaProjectionActivationFlags,
    meta_projection_assembly_id_for_inspection,
    meta_projection_loop_id_for_plan,
    project_meta_orchestration_status,
)
from magi_agent.meta_orchestration.task_plan import (
    MetaChildTaskSpec,
    MetaTaskPlan,
)


FIXTURES = Path(__file__).parent / "fixtures" / "meta_orchestration_harness"
REGRESSION_FIXTURE = FIXTURES / "blocked_regressions.json"

REQUIRED_FIXTURE_IDS = {
    "parent_executes_child_tool_directly",
    "raw_child_transcript_accepted",
    "forged_child_envelope_accepted",
    "wrong_parent_child_result_accepted",
    "retry_loops_unbounded",
    "rejected_child_enters_final_assembly",
    "final_assembly_bypasses_verifier",
    "before_commit_failure_ignored",
    "public_projection_leaks_raw_child_data",
    "authority_flag_forged_true",
    "domain_role_hard_coded_in_core_matrix_row",
}


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-meta-regression",
        scopes=scopes,
    )


def _load_regression_fixture() -> dict[str, object]:
    return json.loads(REGRESSION_FIXTURE.read_text())


def test_blocked_regression_fixture_catalog_is_complete_and_default_off() -> None:
    fixture = _load_regression_fixture()
    cases = fixture["cases"]

    assert fixture["schemaVersion"] == "metaOrchestrationHarnessBlockedRegressions.v1"
    assert {case["id"] for case in cases} == REQUIRED_FIXTURE_IDS
    for case in cases:
        assert case["expectedDisposition"] == "blocked"
        assert case["requiresLiveActivation"] is False
        assert case["trafficAttached"] is False
        assert case["defaultOff"] is True
        assert case["guard"] in CASE_GUARDS


@pytest.mark.parametrize("case", _load_regression_fixture()["cases"], ids=lambda case: case["id"])
def test_blocked_regression_fixtures_hard_fail(case: dict[str, object]) -> None:
    CASE_GUARDS[case["guard"]]()


def _parent_boundary(**overrides: object) -> ExecutionBoundaryIdentity:
    payload = {
        "executionId": "parent-exec-1",
        "agentId": "parent-agent",
        "turnId": "turn-1",
        "policyScope": "coding",
        "policySnapshotId": "policy-parent-1",
        "agentRole": "general",
        "runOn": "main",
        "spawnDepth": 0,
    }
    payload.update(overrides)
    return ExecutionBoundaryIdentity.model_validate(payload)


def _child_boundary(**overrides: object) -> ExecutionBoundaryIdentity:
    payload = {
        "executionId": "child-exec-1",
        "agentId": "child-agent",
        "parentExecutionId": "parent-exec-1",
        "taskId": "task-1",
        "turnId": "turn-1",
        "policyScope": "coding",
        "policySnapshotId": "policy-parent-1",
        "agentRole": "general",
        "runOn": "child",
        "spawnDepth": 1,
    }
    payload.update(overrides)
    return ExecutionBoundaryIdentity.model_validate(payload)


def _ledger_ref(
    child: ExecutionBoundaryIdentity | None = None,
    **overrides: object,
) -> EvidenceBoundaryLedgerRef:
    boundary = child or _child_boundary()
    payload = {
        "ledgerId": f"ledger:{boundary.execution_id}",
        "executionId": boundary.execution_id,
        "agentId": boundary.agent_id,
        "parentExecutionId": boundary.parent_execution_id,
        "taskId": boundary.task_id,
        "policySnapshotId": boundary.policy_snapshot_id,
        "childLedgerRefs": (),
    }
    payload.update(overrides)
    return EvidenceBoundaryLedgerRef.model_validate(payload)


def _envelope_payload(**overrides: object) -> dict[str, object]:
    parent = overrides.pop("parent_boundary", _parent_boundary())
    child = overrides.pop("child_boundary", _child_boundary())
    payload: dict[str, object] = {
        "issuer": "openmagi_runtime_boundary",
        "mode": "return",
        "status": "accepted",
        "parentBoundary": parent,
        "childBoundary": child,
        "task": {
            "taskId": child.task_id,
            "persona": "general",
            "role": "general",
            "spawnDepth": 1,
            "deliver": "return",
            "promptRef": "prompt:task-1",
        },
        "policySnapshot": {
            "parentPolicySnapshotId": parent.policy_snapshot_id,
            "childPolicySnapshotId": child.policy_snapshot_id,
            "taskLocalPolicyCompatibilityRefs": (),
            "allowedToolNames": ("FileRead",),
            "permissionRefs": ("permission:read-only",),
            "callbackHookRefs": ("callback:before-tool-policy",),
        },
        "ledgerRef": _ledger_ref(child),
        "delegatedEvidenceRequirements": (
            DelegatedEvidenceRequirement(type="TestRun", delegation="delegated_required"),
        ),
        "workspaceIsolation": {
            "workspacePolicy": "trusted",
            "isolationRef": "workspace-isolation:task-1",
            "parentWorkspaceRef": "workspace:parent-redacted",
            "childWorkspaceRef": "workspace:child-redacted",
            "descriptiveOnly": True,
            "adoptionAttached": False,
            "workspaceMutated": False,
        },
        "completionContract": {
            "requiredEvidence": "tool_call",
            "requiredFiles": (),
            "requireNonEmptyResult": True,
            "summaryIsEvidence": False,
            "acceptedEvidenceMetadataOnly": True,
        },
        "auditEventRefs": ("audit:child-spawn-planned", "audit:child-envelope-issued"),
        "adkPrimitiveOwnership": {
            "agentOwner": "adk_future_agent",
            "runnerOwner": "adk_future_runner",
            "eventOwner": "adk_event_bridge",
            "toolOwner": "adk_function_tool_future",
            "callbackOwner": "adk_callbacks_future",
            "runnerAttached": False,
            "childExecutionAttached": False,
            "allowedToolNames": ("FileRead",),
            "callbackHookRefs": ("callback:before-tool-policy",),
        },
        "authorityFlags": ChildRuntimeEnvelopeAuthorityFlags(),
        "rawTranscriptRef": "transcript:private-child-turn",
        "privateMetadata": {
            "rawTranscriptPreview": "raw child transcript with sk-child-secret",
            "toolArgs": {"authorization": "Bearer unsafe-token"},
            "workspacePath": "/workspace/private",
        },
    }
    payload.update(overrides)
    return payload


def _policy(**overrides: object) -> ChildAcceptancePolicy:
    payload = {
        "parentExecutionId": "parent-exec-1",
        "childExecutionId": "child-exec-1",
        "taskId": "task-1",
        "parentPolicySnapshotId": "policy-parent-1",
        "childPolicySnapshotId": "policy-parent-1",
        "runtimeReceiptRef": "receipt:child-envelope-1",
        "requiredEvidenceRefs": (
            "ledger:child-exec-1",
            "receipt:child-envelope-1",
            "audit:child-envelope-issued",
        ),
        "maxRetryBudget": 1,
        "currentAttempt": 0,
    }
    payload.update(overrides)
    return ChildAcceptancePolicy.model_validate(payload)


def _issued_child_result(
    payload: dict[str, object] | None = None,
    *,
    receipt_ref: str = "receipt:child-envelope-1",
):
    envelope = ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_runtime_envelope"),
        **(payload or _envelope_payload()),
    )
    return issue_runtime_child_result(envelope, receipt_ref=receipt_ref)


def _child(task_id: str = "task-1") -> MetaChildTaskSpec:
    return MetaChildTaskSpec.model_validate(
        {
            "taskId": task_id,
            "roleRef": "role:generic-worker",
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
    *,
    max_retry_budget: int = 1,
    verifier_refs: tuple[str, ...] = ("verifier:meta-before-commit",),
) -> MetaTaskPlan:
    return MetaTaskPlan.model_validate(
        {
            "planId": "plan:fixture",
            "parentExecutionId": "parent:fixture",
            "objectiveDigest": "sha256:" + "a" * 64,
            "objectivePreview": "Fixture objective.",
            "acceptanceCriteriaRefs": ("criteria:evidence-only",),
            "childTaskSpecs": (_child("task-a"), _child("task-b")),
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


def _retry(task_id: str, *, remaining: int = 1) -> MetaInspectedChildVerdict:
    return MetaInspectedChildVerdict.model_validate(
        {
            "taskId": task_id,
            "required": True,
            "attempt": 0,
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


def _verifier_result(
    verifier_id: str = "verifier:meta-before-commit",
    *,
    status: str = "pass",
    failure_message: str | None = None,
) -> VerifierResultMetadata:
    return VerifierResultMetadata(
        verifierId=verifier_id,
        status=status,
        failureMessage=failure_message,
    )


def _inspection_for_plan(
    plan: MetaTaskPlan,
    *children: MetaInspectedChildVerdict,
) -> object:
    return inspect_child_verdicts(
        meta_projection_loop_id_for_plan(plan),
        children or (_accepted("task-a", "ledger:a"), _accepted("task-b", "ledger:b")),
    )


def _assembly_for_plan(
    plan: MetaTaskPlan,
    inspection: object,
    *,
    satisfied_verifier_refs: tuple[str, ...] = (),
) -> object:
    return assemble_final_output_from_inspection(
        meta_projection_assembly_id_for_inspection(inspection),
        inspection,
        required_verifier_refs=plan.verifier_chain_refs,
        satisfied_verifier_refs=satisfied_verifier_refs,
    )


def _guard_parent_executes_child_tool_directly() -> None:
    with pytest.raises(ValueError):
        inspect_child_verdicts(
            "loop:direct-tool",
            (_accepted("task-a", "ledger:a"),),
            parent_executed_child_tools=True,
        )


def _guard_raw_child_transcript_accepted() -> None:
    verdict = accept_child_result(
        {"summary": "raw child transcript says tests passed", "status": "accepted"},
        _policy(),
    )
    transcript_verdict = accept_child_result(
        _issued_child_result(),
        _policy(requiredEvidenceRefs=("transcript:private-child-turn",)),
    )

    assert verdict.status != "accepted"
    assert verdict.reason_codes == ("invalid_child_envelope",)
    assert transcript_verdict.status != "accepted"
    assert "transcript:private-child-turn" not in transcript_verdict.accepted_evidence_refs


def _guard_forged_child_envelope_accepted() -> None:
    verdict = accept_child_result(_envelope_payload(), _policy())

    assert verdict.status != "accepted"
    assert verdict.reason_codes == ("invalid_child_envelope",)


def _guard_wrong_parent_child_result_accepted() -> None:
    parent = _parent_boundary(executionId="parent-exec-2")
    child = _child_boundary(parentExecutionId="parent-exec-2")
    result = _issued_child_result(
        _envelope_payload(parent_boundary=parent, child_boundary=child)
    )
    verdict = accept_child_result(result, _policy())

    assert verdict.status != "accepted"
    assert verdict.reason_codes == ("parent_execution_mismatch",)


def _guard_retry_loops_unbounded() -> None:
    plan = _plan(max_retry_budget=1)
    inspection = _inspection_for_plan(plan, _accepted("task-a", "ledger:a"), _retry("task-b", remaining=10))
    assembly = _assembly_for_plan(plan, inspection)
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


def _guard_rejected_child_enters_final_assembly() -> None:
    plan = _plan()
    inspection = _inspection_for_plan(plan, _accepted("task-a", "ledger:a"), _rejected("task-b"))
    assembly = _assembly_for_plan(
        plan,
        inspection,
        satisfied_verifier_refs=plan.verifier_chain_refs,
    )

    assert "ledger:rejected-partial" not in assembly.accepted_child_evidence_refs
    assert assembly.excluded_child_refs == ("task-b",)
    assert assembly.projection_mode == "blocked"


def _guard_final_assembly_bypasses_verifier() -> None:
    plan = _plan()
    inspection = _inspection_for_plan(plan)
    assembly = _assembly_for_plan(plan, inspection, satisfied_verifier_refs=())
    before_commit = evaluate_before_commit_for_assembly(
        "before-commit:missing-verifier",
        assembly,
        verifier_results=(),
    )

    assert assembly.projection_mode == "blocked"
    assert before_commit.final_projection_eligible is False
    assert before_commit.verifier_chain_result == "blocked"


def _guard_before_commit_failure_ignored() -> None:
    plan = _plan()
    inspection = _inspection_for_plan(plan)
    assembly = _assembly_for_plan(
        plan,
        inspection,
        satisfied_verifier_refs=plan.verifier_chain_refs,
    )
    issued = issue_runtime_verifier_result_for_assembly(
        assembly,
        _verifier_result(status="failed", failure_message="raw source text with token=sk-test-secret"),
        verifier_bus_run_id="verifier-run:fixture",
        policy_snapshot_id="policy:fixture",
    )
    before_commit = evaluate_before_commit_for_assembly(
        "before-commit:failed",
        assembly,
        verifier_results=(issued,),
    )
    projection = project_meta_orchestration_status(
        "projection:failed-verifier",
        plan=plan,
        inspection=inspection,
        assembly=assembly,
        before_commit_verdict=before_commit,
        verifier_results=(issued,),
    )

    assert projection.final_projection_eligible is False
    assert projection.plan_status == "blocked"
    assert projection.verifier_status == "blocked"


def _guard_public_projection_leaks_raw_child_data() -> None:
    plan = _plan()
    inspection = inspect_child_verdicts(
        meta_projection_loop_id_for_plan(plan),
        (_accepted("task-a", "ledger:a"), _accepted("task-b", "receipt:a")),
        private_notes=(
            "raw child transcript with hidden reasoning",
            "toolArgs={'authorization':'Bearer unsafe-token'}",
            "/workspace/private/source.txt",
        ),
    )
    assembly = _assembly_for_plan(
        plan,
        inspection,
        satisfied_verifier_refs=plan.verifier_chain_refs,
    )
    issued = issue_runtime_verifier_result_for_assembly(
        assembly,
        _verifier_result(),
        verifier_bus_run_id="verifier-run:fixture",
        policy_snapshot_id="policy:fixture",
    )
    before_commit = evaluate_before_commit_for_assembly(
        "before-commit:pass",
        assembly,
        verifier_results=(issued,),
    )
    projection = project_meta_orchestration_status(
        "projection:redaction",
        plan=plan,
        inspection=inspection,
        assembly=assembly,
        before_commit_verdict=before_commit,
        verifier_results=(issued,),
    )
    dumped = json.dumps(projection.public_projection(), sort_keys=True)

    for unsafe in (
        "raw child transcript",
        "hidden reasoning",
        "toolArgs",
        "Bearer unsafe-token",
        "/workspace/private",
        "ledger:a",
        "receipt:a",
    ):
        assert unsafe not in dumped


def _guard_authority_flag_forged_true() -> None:
    # MetaTaskPlan/MetaProjectionActivationFlags live in
    # ``meta_orchestration/*`` — out of C-4 PR-G1 scope (evidence/* only); they
    # keep their own ``raise on non-False`` field validator and their legacy
    # raise contract still applies.
    with pytest.raises(ValidationError):
        MetaTaskPlan.model_validate(
            {
                **_plan().model_dump(by_alias=True),
                "authorityFlags": {"toolExecutionAllowed": True},
            }
        )
    # ``ChildRuntimeEnvelope.authority_flags`` IS in C-4 PR-G1 scope (re-parented
    # onto ``FalseOnlyAuthorityModel``); the kernel coerces a forged True down
    # to False before the Literal validator runs — strictly stronger.
    envelope = ChildRuntimeEnvelope.model_validate(
        _envelope_payload(authorityFlags={"productionAuthority": True})
    )
    assert envelope.authority_flags.model_dump(by_alias=True)["productionAuthority"] is False

    with pytest.raises(ValidationError):
        MetaProjectionActivationFlags.model_validate({"productionAuthority": True})

    plan = _plan()
    inspection = _inspection_for_plan(plan)
    assembly = _assembly_for_plan(
        plan,
        inspection,
        satisfied_verifier_refs=plan.verifier_chain_refs,
    )
    issued = issue_runtime_verifier_result_for_assembly(
        assembly,
        _verifier_result(),
        verifier_bus_run_id="verifier-run:fixture",
        policy_snapshot_id="policy:fixture",
    )
    before_commit = evaluate_before_commit_for_assembly(
        "before-commit:pass",
        assembly,
        verifier_results=(issued,),
    )
    projection = project_meta_orchestration_status(
        "projection:authority-flags",
        plan=plan,
        inspection=inspection,
        assembly=assembly,
        before_commit_verdict=before_commit,
        verifier_results=(issued,),
    )

    assert projection.public_projection()["activationFlags"] == {
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


def _guard_domain_role_hard_coded_in_core_matrix_row() -> None:
    matrix = json.loads((FIXTURES / "matrix.json").read_text())
    forbidden_core_role_ids = (
        "research_searcher",
        "code_editor",
        "backoffice_operator",
    )
    for row in matrix["rows"]:
        assert row["requiresLiveActivation"] is False
        dumped = json.dumps(row, sort_keys=True)
        for forbidden in forbidden_core_role_ids:
            assert forbidden not in dumped
    role_row = next(row for row in matrix["rows"] if row["id"] == "child_role_registry")
    assert all(example["owner"] == "adapter" for example in role_row["roleExamples"])
    assert all(example["coreRequirement"] is False for example in role_row["roleExamples"])


CASE_GUARDS: dict[str, Callable[[], None]] = {
    "parent_executes_child_tool_directly": _guard_parent_executes_child_tool_directly,
    "raw_child_transcript_accepted": _guard_raw_child_transcript_accepted,
    "forged_child_envelope_accepted": _guard_forged_child_envelope_accepted,
    "wrong_parent_child_result_accepted": _guard_wrong_parent_child_result_accepted,
    "retry_loops_unbounded": _guard_retry_loops_unbounded,
    "rejected_child_enters_final_assembly": _guard_rejected_child_enters_final_assembly,
    "final_assembly_bypasses_verifier": _guard_final_assembly_bypasses_verifier,
    "before_commit_failure_ignored": _guard_before_commit_failure_ignored,
    "public_projection_leaks_raw_child_data": _guard_public_projection_leaks_raw_child_data,
    "authority_flag_forged_true": _guard_authority_flag_forged_true,
    "domain_role_hard_coded_in_core_matrix_row": (
        _guard_domain_role_hard_coded_in_core_matrix_row
    ),
}
