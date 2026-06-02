from __future__ import annotations

import pytest
from pydantic import ValidationError

from openmagi_core_agent.evidence.contracts import evaluate_evidence_contract
from openmagi_core_agent.evidence.subagent import (
    REQUIRED_SUBAGENT_EVIDENCE_WARNINGS,
    ChildEvidenceEnvelope,
    ChildEvidenceStatus,
    CustomChildEvidenceSchema,
    DelegatedEvidenceRequirement,
    EvidenceBoundaryLedgerRef,
    EvidenceDelegationMode,
    EvidenceProvenance,
    ExecutionBoundaryIdentity,
    ParentEvidenceAggregation,
    PolicySnapshotCompatibility,
    PropagatedEvidenceRecord,
    aggregate_child_evidence,
    match_delegated_requirement,
    natural_language_summary_as_evidence,
    public_child_aggregate_report,
)
from openmagi_core_agent.evidence.types import (
    EvidenceContract,
    EvidenceContractFailure,
    EvidenceContractVerdict,
    EvidenceRecord,
)

def _boundary(**overrides: object) -> ExecutionBoundaryIdentity:
    payload = {
        "executionId": "root-exec-1",
        "agentId": "root-agent",
        "turnId": "turn-1",
        "policyScope": "coding",
        "policySnapshotId": "policy-snapshot-1",
        "agentRole": "coding",
        "runOn": "main",
        "spawnDepth": 0,
    }
    payload.update(overrides)
    return ExecutionBoundaryIdentity.model_validate(payload)


def _child_boundary(**overrides: object) -> ExecutionBoundaryIdentity:
    payload = {
        "executionId": "child-exec-1",
        "agentId": "child-agent",
        "parentExecutionId": "root-exec-1",
        "taskId": "task-1",
        "turnId": "turn-1",
        "policyScope": "coding",
        "policySnapshotId": "policy-snapshot-1",
        "agentRole": "coding",
        "runOn": "child",
        "spawnDepth": 1,
    }
    payload.update(overrides)
    return ExecutionBoundaryIdentity.model_validate(payload)


def _record(
    *,
    evidence_type: str = "TestRun",
    execution_id: str = "child-exec-1",
    agent_id: str = "child-agent",
    parent_execution_id: str | None = "root-exec-1",
    task_id: str | None = "task-1",
    policy_snapshot_id: str = "policy-snapshot-1",
    source_kind: str = "tool_trace",
    fields: dict[str, object] | None = None,
) -> EvidenceRecord:
    source = {
        "kind": source_kind,
        "toolName": "Bash",
        "toolCallId": "call-1",
        "metadata": {
            "executionId": execution_id,
            "agentId": agent_id,
            "parentExecutionId": parent_execution_id,
            "taskId": task_id,
            "policySnapshotId": policy_snapshot_id,
            "publicSafeFields": ["command", "exitCode", "status"],
        },
    }
    return EvidenceRecord.model_validate(
        {
            "type": evidence_type,
            "status": "ok",
            "observedAt": 1_779_999_999,
            "source": source,
            "fields": fields or {"command": "pytest", "exitCode": 0, "status": "passed"},
            "preview": "pytest passed",
            "metadata": {"publicSafeFields": ["command", "exitCode", "status"]},
        }
    )


def _record_with_source_metadata(
    record: EvidenceRecord,
    metadata: dict[str, object],
) -> EvidenceRecord:
    dumped = record.model_dump(by_alias=True)
    source = dict(dumped["source"])
    source["metadata"] = metadata
    dumped["source"] = source
    return EvidenceRecord.model_validate(dumped)


def _verdict(
    *,
    ok: bool = True,
    state: str = "pass",
    enforcement: str = "block_final_answer",
    record: EvidenceRecord | None = None,
    matched_records: tuple[EvidenceRecord, ...] | None = None,
    evidence_type: str = "TestRun",
    contract_id: str = "coding-basic",
    message: str = "TestRun evidence missing.",
    failure_metadata: dict[str, object] | None = None,
    retry_message: str | None = None,
) -> EvidenceContractVerdict:
    failures = []
    if not ok:
        failures.append(
            EvidenceContractFailure(
                code="EVIDENCE_CONTRACT_MISSING",
                contractId=contract_id,
                requirementType=evidence_type,
                message=message,
                metadata=failure_metadata or {},
            )
        )
    if matched_records is None and record is not None:
        matched_records = (record,)
    if matched_records is None and ok:
        matched_records = (_record(evidence_type=evidence_type),)
    return EvidenceContractVerdict.model_validate(
        {
            "contractId": contract_id,
            "ok": ok,
            "state": state,
            "enforcement": enforcement,
            "missingRequirements": [] if ok else [{"type": evidence_type}],
            "matchedEvidence": list(matched_records or ()),
            "failures": failures,
            "retryMessage": retry_message,
        }
    )


def _ledger_ref(
    boundary: ExecutionBoundaryIdentity,
    *,
    child_ledger_refs: tuple[str, ...] = (),
) -> EvidenceBoundaryLedgerRef:
    return EvidenceBoundaryLedgerRef(
        ledgerId=f"ledger-{boundary.execution_id}",
        executionId=boundary.execution_id,
        agentId=boundary.agent_id,
        parentExecutionId=boundary.parent_execution_id,
        taskId=boundary.task_id,
        policySnapshotId=boundary.policy_snapshot_id,
        childLedgerRefs=child_ledger_refs,
    )


def _envelope(
    *,
    boundary: ExecutionBoundaryIdentity | None = None,
    status: ChildEvidenceStatus = "completed",
    evidence_records: tuple[EvidenceRecord, ...] | None = None,
    contract_verdicts: tuple[EvidenceContractVerdict, ...] | None = None,
    contract_definitions: tuple[object, ...] = (),
    contracts_apply: bool = True,
    issued_by: str = "openmagi_runtime_boundary",
    report: dict[str, object] | None = None,
) -> ChildEvidenceEnvelope:
    child = boundary or _child_boundary()
    records = evidence_records if evidence_records is not None else (_record(),)
    verdicts = (
        contract_verdicts
        if contract_verdicts is not None
        else (_verdict(record=records[0]),)
    )
    return ChildEvidenceEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_evidence_envelope"),
        boundary=child,
        ledgerRef=_ledger_ref(child),
        status=status,
        evidenceRecords=records,
        contractVerdicts=verdicts,
        contractDefinitions=contract_definitions,
        contractsApply=contracts_apply,
        report=report or {
            "matchedTypes": ["TestRun"] if verdicts and verdicts[0].ok else [],
            "missingTypes": [] if verdicts and verdicts[0].ok else ["TestRun"],
            "blockingFailures": [] if status == "completed" else ["TestRun"],
            "auditFailures": [],
        },
        summary="I ran pytest.",
        issuedBy=issued_by,
    )


def test_root_and_child_execution_boundaries_validate_identity_and_depth_scope() -> None:
    root = _boundary()
    child = _child_boundary()

    assert root.parent_execution_id is None
    assert root.task_id is None
    assert root.spawn_depth == 0
    assert child.parent_execution_id == "root-exec-1"
    assert child.task_id == "task-1"
    assert child.policy_snapshot_id == "policy-snapshot-1"
    assert child.spawn_depth == 1

    invalid_payloads = [
        {"runOn": "main", "spawnDepth": 1},
        {"runOn": "main", "parentExecutionId": "parent"},
        {"runOn": "child", "spawnDepth": 0},
        {"runOn": "child", "parentExecutionId": None},
        {"runOn": "child", "taskId": None},
    ]
    for overrides in invalid_payloads:
        with pytest.raises(ValidationError):
            _boundary(**overrides)


def test_policy_snapshot_ref_is_required_and_must_match_ledger_and_records() -> None:
    missing_snapshot_payload = _child_boundary().model_dump(by_alias=True)
    missing_snapshot_payload.pop("policySnapshotId")
    with pytest.raises(ValidationError, match="policySnapshotId"):
        ExecutionBoundaryIdentity.model_validate(missing_snapshot_payload)

    child = _child_boundary()
    with pytest.raises(ValidationError, match="policySnapshotId"):
        _envelope(
            boundary=child,
            evidence_records=(_record(policy_snapshot_id="policy-snapshot-other"),),
        )

    missing_record_snapshot = _record_with_source_metadata(
        _record(),
        {
            "executionId": "child-exec-1",
            "agentId": "child-agent",
            "parentExecutionId": "root-exec-1",
            "taskId": "task-1",
        },
    )
    with pytest.raises(ValidationError, match="policySnapshotId"):
        _envelope(boundary=child, evidence_records=(missing_record_snapshot,))


def test_child_policy_snapshot_must_match_parent_policy_snapshot_for_aggregation() -> None:
    parent = _boundary(policySnapshotId="parent-policy-snapshot")
    child = _child_boundary(policySnapshotId="child-policy-snapshot")
    record = _record(policy_snapshot_id="child-policy-snapshot")
    envelope = _envelope(
        boundary=child,
        evidence_records=(record,),
        contract_verdicts=(_verdict(record=record),),
    )

    with pytest.raises(ValueError, match="policy snapshot"):
        aggregate_child_evidence(parent, (envelope,))


def test_parent_approved_child_task_local_policy_snapshot_can_aggregate() -> None:
    parent = _boundary(policySnapshotId="parent-policy-snapshot")
    child = _child_boundary(
        policySnapshotId="child-task-local-policy-snapshot",
        executionId="child-exec-task-local",
        taskId="task-local-1",
    )
    record = _record(
        policy_snapshot_id="child-task-local-policy-snapshot",
        execution_id="child-exec-task-local",
        task_id="task-local-1",
    )
    envelope = _envelope(
        boundary=child,
        evidence_records=(record,),
        contract_verdicts=(_verdict(record=record),),
    )
    compatibility = PolicySnapshotCompatibility(
        parentPolicySnapshotId="parent-policy-snapshot",
        childPolicySnapshotId="child-task-local-policy-snapshot",
        childExecutionId="child-exec-task-local",
        taskId="task-local-1",
        reason="task_local_contracts",
    )

    aggregation = aggregate_child_evidence(
        parent,
        (envelope,),
        compatible_policy_snapshots=(compatibility,),
    )

    assert aggregation.compatible_policy_snapshots == (compatibility,)
    result = match_delegated_requirement(
        parent,
        DelegatedEvidenceRequirement(type="TestRun", delegation="delegated_required"),
        local_records=(),
        child_aggregation=aggregation,
    )
    assert result.satisfied is True
    assert result.matched_evidence[0].provenance.policy_snapshot_id == (
        "child-task-local-policy-snapshot"
    )


def test_per_boundary_ledger_refs_preserve_execution_id_without_live_runtime_handles() -> None:
    child = _child_boundary()
    ref = _ledger_ref(child, child_ledger_refs=("grandchild-ledger-1",))
    dumped = ref.model_dump(by_alias=True)

    assert dumped["executionId"] == "child-exec-1"
    assert dumped["agentId"] == "child-agent"
    assert dumped["parentExecutionId"] == "root-exec-1"
    assert dumped["taskId"] == "task-1"
    assert dumped["policySnapshotId"] == "policy-snapshot-1"
    assert dumped["childLedgerRefs"] == ("grandchild-ledger-1",)
    for forbidden in ("runnerHandle", "sessionHandle", "artifactHandle", "runtimeHandle"):
        assert forbidden not in repr(dumped)
    for flag in (
        "trafficAttached",
        "executionAttached",
        "runnerAttached",
        "childExecutionAttached",
        "sessionRuntimeAttached",
        "artifactRuntimeAttached",
        "enforcementAttached",
        "routeAttached",
        "apiAttached",
        "dashboardAttached",
        "canaryAttached",
    ):
        assert dumped[flag] is False


def test_child_evidence_envelope_must_be_runtime_issued_not_model_or_child_authored_json() -> None:
    assert (
        "Evidence envelopes must be runtime-issued by OpenMagi compatibility/runtime boundary."
        in REQUIRED_SUBAGENT_EVIDENCE_WARNINGS
    )
    with pytest.raises(ValidationError, match="runtime-issued"):
        _envelope(issued_by="model_authored_json")
    with pytest.raises(ValidationError, match="Child-authored JSON is not trusted evidence"):
        _envelope(issued_by="child_authored_json")


def test_child_evidence_envelope_rejects_external_ack_records() -> None:
    with pytest.raises(ValidationError, match="external acknowledgement"):
        _envelope(evidence_records=(_record(source_kind="external_ack"),))


def test_child_verdict_matched_evidence_rejects_external_ack_records() -> None:
    child_record = _record()
    with pytest.raises(ValidationError, match="external acknowledgement"):
        _envelope(
            evidence_records=(child_record,),
            contract_verdicts=(
                _verdict(matched_records=(_record(source_kind="external_ack"),)),
            ),
        )


@pytest.mark.parametrize(
    ("matched_record", "message"),
    (
        (_record(parent_execution_id="other-root-exec"), "parentExecutionId"),
        (_record(policy_snapshot_id="other-policy-snapshot"), "policySnapshotId"),
    ),
)
def test_child_verdict_matched_evidence_must_match_child_boundary_and_policy(
    matched_record: EvidenceRecord,
    message: str,
) -> None:
    child_record = _record()
    with pytest.raises(ValidationError, match=message):
        _envelope(
            evidence_records=(child_record,),
            contract_verdicts=(_verdict(matched_records=(matched_record,)),),
        )


def test_child_ok_verdict_cannot_claim_matched_evidence_absent_from_envelope_records() -> None:
    child_record = _record()
    irrelevant_record = _record(evidence_type="GitDiff", fields={"status": "changed"})

    with pytest.raises(ValidationError, match="matched evidence"):
        _envelope(
            evidence_records=(child_record,),
            contract_verdicts=(_verdict(matched_records=(irrelevant_record,)),),
        )


def test_child_success_requires_local_contract_verdict_when_contracts_apply() -> None:
    with pytest.raises(ValidationError, match="child-local contract verdict"):
        _envelope(contract_verdicts=(), contracts_apply=True)

    blocking_verdict = _verdict(ok=False, state="failed", enforcement="block_final_answer")
    with pytest.raises(ValidationError, match="cannot report completed"):
        _envelope(status="completed", contract_verdicts=(blocking_verdict,))

    audit_verdict = _verdict(ok=False, state="audit", enforcement="audit")
    envelope = _envelope(status="completed", contract_verdicts=(audit_verdict,))
    assert envelope.status == "completed"


def test_parent_aggregation_preserves_child_provenance_without_rewriting_as_parent_evidence() -> None:
    parent = _boundary()
    child_record = _record()
    aggregation = aggregate_child_evidence(parent, (_envelope(evidence_records=(child_record,)),))

    assert aggregation.parent_boundary.execution_id == "root-exec-1"
    propagated = aggregation.propagated_evidence[0]
    assert propagated.provenance.execution_id == "child-exec-1"
    assert propagated.provenance.agent_id == "child-agent"
    assert propagated.provenance.task_id == "task-1"
    assert propagated.record.source.metadata["executionId"] == "child-exec-1"
    assert propagated.provenance.execution_id != parent.execution_id


def test_propagated_record_rejects_rewritten_parent_task_and_empty_optional_provenance() -> None:
    record = _record()
    base_provenance = {
        "executionId": "child-exec-1",
        "agentId": "child-agent",
        "parentExecutionId": "root-exec-1",
        "taskId": "task-1",
        "policySnapshotId": "policy-snapshot-1",
        "ledgerId": "ledger-child-exec-1",
    }

    with pytest.raises(ValidationError, match="parentExecutionId"):
        PropagatedEvidenceRecord(
            record=record,
            provenance=EvidenceProvenance(
                **{**base_provenance, "parentExecutionId": "other-root-exec"}
            ),
        )
    with pytest.raises(ValidationError, match="taskId"):
        PropagatedEvidenceRecord(
            record=record,
            provenance=EvidenceProvenance(**{**base_provenance, "taskId": "other-task"}),
        )
    with pytest.raises(ValidationError, match="policySnapshotId"):
        PropagatedEvidenceRecord(
            record=record,
            provenance=EvidenceProvenance(
                **{**base_provenance, "policySnapshotId": "other-policy-snapshot"}
            ),
        )
    with pytest.raises(ValidationError, match="evidence provenance identifiers"):
        EvidenceProvenance(**{**base_provenance, "parentExecutionId": " "})
    with pytest.raises(ValidationError, match="evidence provenance identifiers"):
        EvidenceProvenance(**{**base_provenance, "taskId": ""})


@pytest.mark.parametrize(
    ("mode", "expected"),
    (
        ("local_only", True),
        ("delegated_allowed", True),
        ("delegated_required", True),
        ("aggregate_required", True),
    ),
)
def test_delegation_matching_modes_with_local_parent_evidence(
    mode: EvidenceDelegationMode,
    expected: bool,
) -> None:
    parent = _boundary()
    local = _record(execution_id="root-exec-1", agent_id="root-agent", parent_execution_id=None, task_id=None)
    child_aggregation = aggregate_child_evidence(parent, (_envelope(),))

    result = match_delegated_requirement(
        parent,
        DelegatedEvidenceRequirement(type="TestRun", delegation=mode),
        local_records=(local,),
        child_aggregation=child_aggregation,
    )

    assert result.satisfied is expected


@pytest.mark.parametrize(
    ("mode", "expected"),
    (
        ("local_only", False),
        ("delegated_allowed", True),
        ("delegated_required", True),
        ("aggregate_required", False),
    ),
)
def test_delegation_matching_modes_with_child_only_evidence(
    mode: EvidenceDelegationMode,
    expected: bool,
) -> None:
    parent = _boundary()
    child_aggregation = aggregate_child_evidence(parent, (_envelope(),))

    result = match_delegated_requirement(
        parent,
        DelegatedEvidenceRequirement(type="TestRun", delegation=mode),
        local_records=(),
        child_aggregation=child_aggregation,
    )

    assert result.satisfied is expected


@pytest.mark.parametrize("status", ("blocked", "failed"))
@pytest.mark.parametrize("mode", ("delegated_allowed", "delegated_required"))
def test_blocked_or_failed_child_ok_records_do_not_satisfy_delegated_evidence(
    status: ChildEvidenceStatus,
    mode: EvidenceDelegationMode,
) -> None:
    parent = _boundary()
    child_record = _record()
    aggregation = aggregate_child_evidence(
        parent,
        (
            _envelope(
                status=status,
                evidence_records=(child_record,),
                contract_verdicts=(_verdict(record=child_record),),
            ),
        ),
    )

    result = match_delegated_requirement(
        parent,
        DelegatedEvidenceRequirement(type="TestRun", delegation=mode),
        local_records=(),
        child_aggregation=aggregation,
    )

    assert result.satisfied is False
    assert result.matched_evidence == ()


def test_child_propagation_requires_successful_local_verdict_coverage_for_each_record() -> None:
    parent = _boundary()
    covered_record = _record(evidence_type="TestRun")
    uncovered_record = _record(evidence_type="GitDiff", fields={"status": "changed"})
    aggregation = aggregate_child_evidence(
        parent,
        (
            _envelope(
                evidence_records=(covered_record, uncovered_record),
                contract_verdicts=(_verdict(matched_records=(covered_record,)),),
            ),
        ),
    )

    assert tuple(record.record.type for record in aggregation.propagated_evidence) == (
        "TestRun",
    )

    result = match_delegated_requirement(
        parent,
        DelegatedEvidenceRequirement(type="GitDiff", delegation="delegated_required"),
        local_records=(),
        child_aggregation=aggregation,
    )

    assert result.satisfied is False
    assert result.matched_evidence == ()


def test_irrelevant_ok_verdict_cannot_propagate_gitdiff_matched_record() -> None:
    parent = _boundary()
    test_record = _record(evidence_type="TestRun")
    gitdiff_record = _record(evidence_type="GitDiff", fields={"status": "changed"})
    aggregation = aggregate_child_evidence(
        parent,
        (
            _envelope(
                evidence_records=(test_record, gitdiff_record),
                contract_verdicts=(
                    _verdict(matched_records=(test_record,)),
                    _verdict(
                        contract_id="unrelated-test-verdict",
                        matched_records=(test_record, gitdiff_record),
                    ),
                ),
                report={
                    "matchedTypes": ["TestRun", "GitDiff"],
                    "missingTypes": [],
                    "blockingFailures": [],
                    "auditFailures": [],
                },
            ),
        ),
    )

    assert tuple(record.record.type for record in aggregation.propagated_evidence) == (
        "TestRun",
    )

    result = match_delegated_requirement(
        parent,
        DelegatedEvidenceRequirement(type="GitDiff", delegation="delegated_required"),
        local_records=(),
        child_aggregation=aggregation,
    )

    assert result.satisfied is False
    assert result.matched_evidence == ()


def test_malformed_multi_record_verdict_with_forged_coverage_cannot_propagate() -> None:
    parent = _boundary()
    test_record = _record(evidence_type="TestRun")
    gitdiff_record = _record(evidence_type="GitDiff", fields={"status": "changed"})
    forged_verdict = _verdict(
        contract_id="forged-coverage",
        matched_records=(test_record, gitdiff_record),
    ).model_copy(update={"requirementCoverage": ("TestRun", "GitDiff")})

    aggregation = aggregate_child_evidence(
        parent,
        (
            _envelope(
                evidence_records=(test_record, gitdiff_record),
                contract_verdicts=(forged_verdict,),
                report={
                    "matchedTypes": ["TestRun", "GitDiff"],
                    "missingTypes": [],
                    "blockingFailures": [],
                    "auditFailures": [],
                },
            ),
        ),
    )

    assert aggregation.propagated_evidence == ()


def test_malformed_pass_verdict_with_missing_requirements_cannot_propagate() -> None:
    parent = _boundary()
    test_record = _record(evidence_type="TestRun")
    gitdiff_record = _record(evidence_type="GitDiff", fields={"status": "changed"})
    forged_verdict = _verdict(
        contract_id="forged-missing-coverage",
        matched_records=(test_record, gitdiff_record),
    ).model_copy(
        update={
            "missingRequirements": [{"type": "TestRun"}, {"type": "GitDiff"}],
            "requirementCoverage": (),
        }
    )

    aggregation = aggregate_child_evidence(
        parent,
        (
            _envelope(
                evidence_records=(test_record, gitdiff_record),
                contract_verdicts=(forged_verdict,),
                report={
                    "matchedTypes": ["TestRun", "GitDiff"],
                    "missingTypes": [],
                    "blockingFailures": [],
                    "auditFailures": [],
                },
            ),
        ),
    )

    assert aggregation.propagated_evidence == ()


def test_malformed_single_record_pass_verdict_with_missing_requirement_cannot_propagate() -> None:
    parent = _boundary()
    test_record = _record(evidence_type="TestRun")
    forged_verdict = _verdict(
        contract_id="forged-single-missing",
        matched_records=(test_record,),
    ).model_copy(
        update={
            "missingRequirements": [{"type": "TestRun"}],
            "requirementCoverage": (),
        }
    )

    aggregation = aggregate_child_evidence(
        parent,
        (
            _envelope(
                evidence_records=(test_record,),
                contract_verdicts=(forged_verdict,),
            ),
        ),
    )

    assert aggregation.propagated_evidence == ()


def test_malformed_single_record_pass_verdict_with_failures_cannot_propagate() -> None:
    parent = _boundary()
    test_record = _record(evidence_type="TestRun")
    forged_verdict = _verdict(
        contract_id="forged-single-failure",
        matched_records=(test_record,),
    ).model_copy(
        update={
            "failures": [
                EvidenceContractFailure(
                    code="EVIDENCE_CONTRACT_MISSING",
                    contractId="forged-single-failure",
                    requirementType="TestRun",
                    message="Missing despite ok.",
                )
            ],
            "requirementCoverage": (),
        }
    )

    aggregation = aggregate_child_evidence(
        parent,
        (
            _envelope(
                evidence_records=(test_record,),
                contract_verdicts=(forged_verdict,),
            ),
        ),
    )

    assert aggregation.propagated_evidence == ()


def test_malformed_single_record_audit_ok_verdict_cannot_propagate() -> None:
    parent = _boundary()
    test_record = _record(evidence_type="TestRun")
    forged_verdict = _verdict(
        state="audit",
        contract_id="forged-single-audit",
        matched_records=(test_record,),
    )

    aggregation = aggregate_child_evidence(
        parent,
        (
            _envelope(
                evidence_records=(test_record,),
                contract_verdicts=(forged_verdict,),
            ),
        ),
    )

    assert aggregation.propagated_evidence == ()


def test_metadata_free_verdict_fallback_requires_single_matched_evidence_record() -> None:
    parent = _boundary()
    first_record = _record(
        evidence_type="TestRun",
        fields={"command": "pytest first", "exitCode": 0, "status": "passed"},
    )
    second_record = _record(
        evidence_type="TestRun",
        fields={"command": "pytest second", "exitCode": 0, "status": "passed"},
    )

    aggregation = aggregate_child_evidence(
        parent,
        (
            _envelope(
                evidence_records=(first_record, second_record),
                contract_verdicts=(_verdict(matched_records=(first_record, second_record)),),
            ),
        ),
    )

    assert aggregation.propagated_evidence == ()


def test_real_multi_requirement_child_verdict_propagates_each_matched_record() -> None:
    parent = _boundary()
    gitdiff_record = _record(
        evidence_type="GitDiff",
        fields={"status": "changed", "changedFiles": ["app.py"]},
    )
    testrun_record = _record(
        evidence_type="TestRun",
        fields={"command": "pytest tests", "exitCode": 0, "status": "passed"},
    )
    contract = EvidenceContract.model_validate(
        {
            "id": "child-coding-contract",
            "triggers": ["afterToolUse"],
            "requirements": [
                {"type": "GitDiff"},
                {
                    "type": "TestRun",
                    "commandPattern": "^pytest",
                    "exitCode": 0,
                },
            ],
            "onMissing": "block_final_answer",
        }
    )
    verdict = evaluate_evidence_contract(contract, (gitdiff_record, testrun_record))

    assert verdict.ok is True
    assert verdict.requirement_coverage == ("GitDiff", "TestRun")
    assert tuple(record.type for record in verdict.matched_evidence) == (
        "GitDiff",
        "TestRun",
    )

    aggregation = aggregate_child_evidence(
        parent,
        (
            _envelope(
                evidence_records=(gitdiff_record, testrun_record),
                contract_verdicts=(verdict,),
                contract_definitions=(contract,),
                report={
                    "matchedTypes": ["GitDiff", "TestRun"],
                    "missingTypes": [],
                    "blockingFailures": [],
                    "auditFailures": [],
                },
            ),
        ),
    )

    assert tuple(record.record.type for record in aggregation.propagated_evidence) == (
        "GitDiff",
        "TestRun",
    )
    for evidence_type in ("GitDiff", "TestRun"):
        result = match_delegated_requirement(
            parent,
            DelegatedEvidenceRequirement(type=evidence_type, delegation="delegated_required"),
            local_records=(),
            child_aggregation=aggregation,
        )
        assert result.satisfied is True
        assert tuple(match.record.type for match in result.matched_evidence) == (
            evidence_type,
        )


def test_matching_fails_when_child_aggregation_belongs_to_different_parent_boundary() -> None:
    original_parent = _boundary()
    other_parent = _boundary(executionId="root-exec-2", agentId="root-agent-2")
    child_aggregation = aggregate_child_evidence(original_parent, (_envelope(),))

    result = match_delegated_requirement(
        other_parent,
        DelegatedEvidenceRequirement(type="TestRun", delegation="delegated_required"),
        local_records=(),
        child_aggregation=child_aggregation,
    )

    assert result.satisfied is False
    assert "parent boundary" in (result.reason or "")


def test_child_gitdiff_testrun_and_commitcheckpoint_satisfy_delegated_parent_requirements() -> None:
    parent = _boundary()
    records = (
        _record(evidence_type="GitDiff", fields={"status": "changed"}),
        _record(evidence_type="TestRun", fields={"command": "pytest", "exitCode": 0, "status": "passed"}),
        _record(evidence_type="CommitCheckpoint", fields={"status": "created"}),
    )
    aggregation = aggregate_child_evidence(
        parent,
        (
            _envelope(
                evidence_records=records,
                contract_verdicts=tuple(_verdict(record=record) for record in records),
            ),
        ),
    )

    for evidence_type in ("GitDiff", "TestRun", "CommitCheckpoint"):
        for mode in ("delegated_allowed", "delegated_required"):
            result = match_delegated_requirement(
                parent,
                DelegatedEvidenceRequirement(type=evidence_type, delegation=mode),
                local_records=(),
                child_aggregation=aggregation,
            )
            assert result.satisfied is True
            assert tuple(match.record.type for match in result.matched_evidence) == (
                evidence_type,
            )


def test_natural_language_child_summary_cannot_satisfy_evidence() -> None:
    assert (
        "Natural-language subagent summaries are never evidence."
        in REQUIRED_SUBAGENT_EVIDENCE_WARNINGS
    )
    rejected = natural_language_summary_as_evidence("I ran pytest and it passed.")

    assert rejected.satisfied is False
    assert rejected.reason == "Natural-language subagent summaries are never evidence."


def test_child_blocking_failures_propagate_but_audit_failures_can_remain_non_blocking() -> None:
    parent = _boundary()
    blocking = _envelope(
        status="blocked",
        contract_verdicts=(
            _verdict(ok=False, state="block_ready", enforcement="block_final_answer"),
        ),
    )
    audit = _envelope(
        boundary=_child_boundary(
            executionId="child-exec-2",
            agentId="child-agent-2",
            taskId="task-2",
        ),
        status="completed",
        evidence_records=(
            _record(
                execution_id="child-exec-2",
                agent_id="child-agent-2",
                task_id="task-2",
            ),
        ),
        contract_verdicts=(_verdict(ok=False, state="audit", enforcement="audit"),),
    )

    aggregation = aggregate_child_evidence(parent, (blocking, audit))

    assert aggregation.state == "blocked"
    assert len(aggregation.blocking_child_failures) == 1
    assert len(aggregation.audit_child_failures) == 1


@pytest.mark.parametrize(
    ("child", "record", "message"),
    (
        (
            _child_boundary(parentExecutionId="other-root-exec"),
            _record(parent_execution_id="other-root-exec"),
            "parentExecutionId",
        ),
        (
            _child_boundary(policySnapshotId="other-policy-snapshot"),
            _record(policy_snapshot_id="other-policy-snapshot"),
            "policy snapshot",
        ),
    ),
)
def test_parent_aggregation_direct_construction_rejects_child_boundary_mismatch(
    child: ExecutionBoundaryIdentity,
    record: EvidenceRecord,
    message: str,
) -> None:
    envelope = _envelope(
        boundary=child,
        evidence_records=(record,),
        contract_verdicts=(_verdict(record=record),),
    )

    with pytest.raises(ValidationError, match=message):
        ParentEvidenceAggregation(
            parentBoundary=_boundary(),
            childEnvelopes=(envelope,),
            propagatedEvidence=(),
            state="pass",
        )


def test_parent_aggregation_direct_construction_rejects_forged_pass_state_blocked_child() -> None:
    parent = _boundary()
    child_record = _record()
    blocked_verdict = _verdict(ok=False, state="block_ready", enforcement="block_final_answer")
    blocked_envelope = _envelope(
        status="blocked",
        evidence_records=(child_record,),
        contract_verdicts=(blocked_verdict,),
    )
    forged = PropagatedEvidenceRecord(
        record=child_record,
        provenance=EvidenceProvenance(
            executionId="child-exec-1",
            agentId="child-agent",
            parentExecutionId="root-exec-1",
            taskId="task-1",
            policySnapshotId="policy-snapshot-1",
            ledgerId="ledger-child-exec-1",
        ),
        producedByParent=False,
    )

    with pytest.raises(ValidationError, match="state"):
        ParentEvidenceAggregation(
            parentBoundary=parent,
            childEnvelopes=(blocked_envelope,),
            propagatedEvidence=(forged,),
            state="pass",
        )


def test_parent_aggregation_direct_construction_rejects_propagated_evidence_without_child() -> None:
    parent = _boundary()
    child_record = _record()
    forged = PropagatedEvidenceRecord(
        record=child_record,
        provenance=EvidenceProvenance(
            executionId="child-exec-1",
            agentId="child-agent",
            parentExecutionId="root-exec-1",
            taskId="task-1",
            policySnapshotId="policy-snapshot-1",
            ledgerId="ledger-child-exec-1",
        ),
        producedByParent=False,
    )

    with pytest.raises(ValidationError, match="propagated evidence"):
        ParentEvidenceAggregation(
            parentBoundary=parent,
            childEnvelopes=(),
            propagatedEvidence=(forged,),
            state="pass",
        )


def test_parent_aggregation_direct_construction_rejects_omitted_canonical_propagated_evidence() -> None:
    parent = _boundary()
    envelope = _envelope()
    canonical = aggregate_child_evidence(parent, (envelope,))

    assert len(canonical.propagated_evidence) == 1
    with pytest.raises(ValidationError, match="propagatedEvidence"):
        ParentEvidenceAggregation(
            parentBoundary=parent,
            childEnvelopes=(envelope,),
            propagatedEvidence=(),
            state=canonical.state,
            blockingChildFailures=canonical.blocking_child_failures,
            auditChildFailures=canonical.audit_child_failures,
        )


def test_parent_aggregation_direct_construction_rejects_duplicate_canonical_propagated_evidence() -> None:
    parent = _boundary()
    envelope = _envelope()
    canonical = aggregate_child_evidence(parent, (envelope,))
    propagated = canonical.propagated_evidence[0]

    with pytest.raises(ValidationError, match="propagatedEvidence"):
        ParentEvidenceAggregation(
            parentBoundary=parent,
            childEnvelopes=(envelope,),
            propagatedEvidence=(propagated, propagated),
            state=canonical.state,
            blockingChildFailures=canonical.blocking_child_failures,
            auditChildFailures=canonical.audit_child_failures,
        )


# Keep these fake redaction sentinel lines stable for .secrets.baseline.
# They are test fixtures, not live credentials.
def test_redacted_aggregate_report_includes_child_provenance_without_secret_values() -> None:
    parent = _boundary()
    secret_record = _record(
        fields={
            "command": "pytest",
            "status": "passed",
            "apiToken": "sk-live-secret",
            "password": "hunter2",
            "authorization": "Bearer live-token",
            "githubToken": "ghp_secret",
            "privateKey": "-----BEGIN PRIVATE KEY-----abc",
        }
    )
    envelope = _envelope(evidence_records=(secret_record,))

    report = public_child_aggregate_report(aggregate_child_evidence(parent, (envelope,)))
    dumped = report.model_dump(by_alias=True)
    blob = repr(dumped)

    child_public = dumped["children"][0]
    assert dumped["parentExecutionId"].startswith("exec:sha256:")
    assert all(child_public[key].startswith(prefix) for key, prefix in {"executionId": "exec:sha256:", "agentId": "agent:sha256:", "taskId": "task:sha256:", "parentExecutionId": "exec:sha256:", "policySnapshotId": "policy:sha256:"}.items())
    assert all(raw not in blob for raw in ("child-exec-1", "child-agent", "task-1", "root-exec-1", "policy-snapshot-1"))
    for secret in (
        "sk-live-secret",
        "hunter2",
        "Bearer live-token",
        "ghp_secret",
        "-----BEGIN PRIVATE KEY-----abc",
    ):
        assert secret not in blob


def test_aggregate_report_redacts_public_safe_credential_fields() -> None:
    parent = _boundary()
    secret_record = _record(
        fields={
            "authorization": "Bearer live-token",
            "cookie": "sessionid=secret-cookie",
            "credentials": "raw-credential",
            "status": "passed",
        }
    ).model_copy(
        update={
            "metadata": {
                "publicSafeFields": (
                    "authorization",
                    "cookie",
                    "credentials",
                    "status",
                )
            }
        }
    )
    envelope = _envelope(evidence_records=(secret_record,))

    report = public_child_aggregate_report(aggregate_child_evidence(parent, (envelope,)))
    dumped = report.model_dump(by_alias=True)
    fields = dumped["children"][0]["evidence"][0]["fields"]

    assert fields["authorization"] == "[redacted]"
    assert fields["cookie"] == "[redacted]"
    assert fields["credentials"] == "[redacted]"
    assert fields["status"] == "passed"
    assert "live-token" not in repr(dumped)
    assert "secret-cookie" not in repr(dumped)
    assert "raw-credential" not in repr(dumped)


def test_public_aggregate_report_rejects_constructed_aggregation_before_reading_children() -> None:
    parent = _boundary()
    child = _child_boundary()
    external_ack_record = _record(source_kind="external_ack")
    forged_verdict = _verdict(matched_records=(external_ack_record,))
    constructed_child = ChildEvidenceEnvelope.model_construct(
        boundary=child,
        ledger_ref=_ledger_ref(child),
        status="completed",
        evidence_records=(external_ack_record,),
        contract_verdicts=(forged_verdict,),
        contracts_apply=True,
        report={},
        summary="I received an external acknowledgement.",
        issued_by="openmagi_runtime_boundary",
    )
    constructed_aggregation = ParentEvidenceAggregation.model_construct(
        parent_boundary=parent,
        child_envelopes=(constructed_child,),
        propagated_evidence=(),
        state="pass",
        blocking_child_failures=(),
        audit_child_failures=(),
    )

    with pytest.raises(ValueError, match="runtime-issued parent evidence aggregation"):
        public_child_aggregate_report(constructed_aggregation)


def test_public_aggregate_report_includes_redacted_verdict_failure_and_status_coverage() -> None:
    parent = _boundary()
    raw_secret = "sk-live-secret"
    long_message = f"Command failed with Authorization: Bearer live-token {raw_secret} " + (
        "x" * 600
    )
    blocking_verdict = _verdict(
        ok=False,
        state="block_ready",
        enforcement="block_final_answer",
        message=long_message,
        failure_metadata={
            "field": "apiToken",
            "actual": raw_secret,
            "rawOutput": f"authorization=Bearer live-token token={raw_secret}",
        },
        retry_message=long_message,
    )
    report = public_child_aggregate_report(
        aggregate_child_evidence(
            parent,
            (
                _envelope(
                    status="blocked",
                    evidence_records=(_record(),),
                    contract_verdicts=(blocking_verdict,),
                ),
            ),
        )
    )
    dumped = report.model_dump(by_alias=True)
    child = dumped["children"][0]
    verdict = child["verdicts"][0]
    failure = child["blockingFailures"][0]
    blob = repr(dumped)

    assert child["status"] == "blocked"
    assert child["matchedTypes"] == ()
    assert child["missingTypes"] == ("TestRun",)
    assert verdict["contractId"] == "coding-basic"
    assert verdict["enforcement"] == "block_final_answer"
    assert verdict["ok"] is False
    assert failure["code"] == "EVIDENCE_CONTRACT_MISSING"
    assert failure["contractId"] == "coding-basic"
    assert failure["requirementType"] == "TestRun"
    assert len(failure["message"]) <= 400
    assert failure["metadata"]["actual"] == "[redacted]"
    assert raw_secret not in blob
    assert "Bearer live-token" not in blob
    assert "x" * 500 not in blob


def test_public_aggregate_report_redacts_evidence_preview_source_locators() -> None:
    parent = _boundary()
    source_record = _record().model_copy(
        update={
            "preview": (
                "opened /Users/kevin/private/source.txt from "
                "https://internal.example/customer/acme"
            )
        }
    )
    envelope = _envelope(evidence_records=(source_record,))

    dumped = public_child_aggregate_report(
        aggregate_child_evidence(parent, (envelope,))
    ).model_dump(by_alias=True)
    blob = repr(dumped)

    assert dumped["children"][0]["evidence"][0]["preview"] == "[redacted]"
    assert "/Users/kevin" not in blob
    assert "internal.example" not in blob


def _runtime_authority(*scopes: str):
    from runtime_issuance_support import issue_test_runtime_authority

    return issue_test_runtime_authority(
        authority_id="authority:test-subagent-evidence",
        scopes=scopes,
    )


def test_structural_child_evidence_envelope_cannot_aggregate_as_runtime_issued() -> None:
    issued = _envelope()
    envelope = ChildEvidenceEnvelope.model_validate(
        issued.model_dump(by_alias=True, mode="python", warnings=False)
    )

    with pytest.raises(ValueError, match="runtime-issued child evidence envelope"):
        aggregate_child_evidence(_boundary(), (envelope,))


def test_child_evidence_envelope_factory_requires_runtime_issue_authority() -> None:
    payload = _envelope().model_dump(by_alias=True, mode="python", warnings=False)

    with pytest.raises(RuntimeError, match="child_evidence_envelope"):
        ChildEvidenceEnvelope.issue_runtime_envelope(**payload)


def test_parent_aggregation_direct_construction_rejects_valid_structural_payload() -> None:
    parent = _boundary()
    canonical = aggregate_child_evidence(parent, (_envelope(),))
    payload = canonical.model_dump(by_alias=True, mode="python", warnings=False)

    with pytest.raises(ValidationError, match="runtime-issued parent evidence aggregation"):
        ParentEvidenceAggregation.model_validate(payload)


def test_structural_parent_aggregation_cannot_project_public_report() -> None:
    parent = _boundary()
    canonical = aggregate_child_evidence(parent, (_envelope(),))
    structural = ParentEvidenceAggregation.model_construct(
        parent_boundary=parent,
        child_envelopes=canonical.child_envelopes,
        propagated_evidence=canonical.propagated_evidence,
        state=canonical.state,
        blocking_child_failures=canonical.blocking_child_failures,
        audit_child_failures=canonical.audit_child_failures,
        compatible_policy_snapshots=canonical.compatible_policy_snapshots,
    )

    assert structural.is_runtime_boundary_issued is False
    with pytest.raises(ValueError, match="runtime-issued parent evidence aggregation"):
        public_child_aggregate_report(structural)


def test_structural_parent_aggregation_cannot_satisfy_delegated_requirement() -> None:
    parent = _boundary()
    canonical = aggregate_child_evidence(parent, (_envelope(),))
    structural = ParentEvidenceAggregation.model_construct(
        parent_boundary=parent,
        child_envelopes=canonical.child_envelopes,
        propagated_evidence=canonical.propagated_evidence,
        state=canonical.state,
        blocking_child_failures=canonical.blocking_child_failures,
        audit_child_failures=canonical.audit_child_failures,
        compatible_policy_snapshots=canonical.compatible_policy_snapshots,
    )

    with pytest.raises(ValueError, match="runtime-issued parent evidence aggregation"):
        match_delegated_requirement(
            parent,
            DelegatedEvidenceRequirement(type="TestRun", delegation="delegated_required"),
            local_records=(),
            child_aggregation=structural,
        )


def test_custom_child_evidence_is_declarative_only_and_not_hard_safety_or_external_ack() -> None:
    schema = CustomChildEvidenceSchema(
        type="custom:ReviewSignal",
        sourceKind="custom_extractor",
        fields={"score": {"type": "number"}},
    )

    assert schema.type == "custom:ReviewSignal"
    assert schema.live_extractor_execution_attached is False
    with pytest.raises(ValidationError):
        CustomChildEvidenceSchema(
            type="TestRun",
            sourceKind="custom_extractor",
            fields={"score": {"type": "number"}},
        )
    with pytest.raises(ValidationError):
        CustomChildEvidenceSchema(
            type="custom:ReviewSignal",
            sourceKind="external_ack",
            fields={"score": {"type": "number"}},
        )
    with pytest.raises(ValidationError):
        CustomChildEvidenceSchema(
            type="custom:ReviewSignal",
            sourceKind="custom_extractor",
            fields={"score": {"type": "number"}},
            hardSafety=True,
        )


def test_all_subagent_attachment_flags_remain_false_even_via_model_copy_update() -> None:
    boundary = _child_boundary()
    ref = _ledger_ref(boundary)
    envelope = _envelope(boundary=boundary)

    for model in (boundary, ref, envelope):
        dumped = model.model_copy(
            update={
                "trafficAttached": True,
                "executionAttached": True,
                "runnerAttached": True,
                "childExecutionAttached": True,
                "sessionRuntimeAttached": True,
                "artifactRuntimeAttached": True,
                "enforcementAttached": True,
                "routeAttached": True,
                "apiAttached": True,
                "dashboardAttached": True,
                "canaryAttached": True,
            }
        ).model_dump(by_alias=True)
        for key, value in dumped.items():
            if key.endswith("Attached"):
                assert value is False


@pytest.mark.parametrize(
    "update",
    (
        {"trafficAttached": True, "executionAttached": True, "runnerAttached": True},
        {"traffic_attached": True, "execution_attached": True, "runner_attached": True},
    ),
)
def test_public_aggregate_report_attachment_flags_remain_false_via_model_copy_update(
    update: dict[str, object],
) -> None:
    report = public_child_aggregate_report(
        aggregate_child_evidence(_boundary(), (_envelope(),))
    )

    copied = report.model_copy(update=update).model_dump(by_alias=True)

    for key, value in copied.items():
        if key.endswith("Attached"):
            assert value is False
