from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.evidence.contracts import evaluate_evidence_contract
from magi_agent.evidence.subagent import (
    ChildEvidenceEnvelope,
    DelegatedEvidenceRequirement,
    EvidenceBoundaryLedgerRef,
    ExecutionBoundaryIdentity,
    PolicySnapshotCompatibility,
    aggregate_child_evidence,
    match_delegated_requirement,
    natural_language_summary_as_evidence,
    public_child_aggregate_report,
)
from magi_agent.evidence.types import EvidenceContract, EvidenceRecord

from runtime_issuance_support import issue_test_runtime_authority


FIXTURE_PATH = Path(__file__).parent / "fixtures/gate1/child_envelope_aggregate.json"
ATTACHMENT_FLAGS = (
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
)
FORBIDDEN_LIVE_RUNTIME_KEYS = (
    "adkAgent",
    "agentHandle",
    "runnerHandle",
    "sessionHandle",
    "sessionService",
    "artifactHandle",
    "artifactService",
    "schedulerHandle",
    "runtimeHandle",
    "toolLoop",
    "childRunner",
    "childAgent",
)


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-gate1-child-envelope-fixtures",
        scopes=scopes,
    )


def _root_boundary() -> ExecutionBoundaryIdentity:
    return ExecutionBoundaryIdentity.model_validate(
        {
            "executionId": "gate1-root-exec-fixture",
            "agentId": "gate1-root-agent",
            "turnId": "gate1-turn-fixture",
            "policyScope": "gate1-local-synthetic",
            "policySnapshotId": "gate1-parent-policy-snapshot",
            "agentRole": "coding",
            "runOn": "main",
            "spawnDepth": 0,
        }
    )


def _child_boundary() -> ExecutionBoundaryIdentity:
    return ExecutionBoundaryIdentity.model_validate(
        {
            "executionId": "gate1-child-exec-fixture",
            "agentId": "gate1-child-agent",
            "parentExecutionId": "gate1-root-exec-fixture",
            "taskId": "gate1-task-local-child-envelope",
            "turnId": "gate1-turn-fixture",
            "policyScope": "gate1-local-synthetic",
            "policySnapshotId": "gate1-child-policy-snapshot",
            "agentRole": "coding",
            "runOn": "child",
            "spawnDepth": 1,
        }
    )


def _ledger_ref(boundary: ExecutionBoundaryIdentity) -> EvidenceBoundaryLedgerRef:
    return EvidenceBoundaryLedgerRef.model_validate(
        {
            "ledgerId": f"gate1-ledger-{boundary.execution_id}",
            "executionId": boundary.execution_id,
            "agentId": boundary.agent_id,
            "parentExecutionId": boundary.parent_execution_id,
            "taskId": boundary.task_id,
            "policySnapshotId": boundary.policy_snapshot_id,
            "childLedgerRefs": (),
        }
    )


def _child_record(
    evidence_type: str,
    fields: Mapping[str, object],
    preview: str,
) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "type": evidence_type,
            "status": "ok",
            "observedAt": 1_800_000_001,
            "source": {
                "kind": "tool_trace",
                "toolName": "SyntheticGate1Tool",
                "toolCallId": f"gate1-call-{evidence_type}",
                "metadata": {
                    "executionId": "gate1-child-exec-fixture",
                    "agentId": "gate1-child-agent",
                    "parentExecutionId": "gate1-root-exec-fixture",
                    "taskId": "gate1-task-local-child-envelope",
                    "policySnapshotId": "gate1-child-policy-snapshot",
                    "publicSafeFields": tuple(fields.keys()),
                },
            },
            "fields": fields,
            "preview": preview,
            "metadata": {"publicSafeFields": tuple(fields.keys())},
        }
    )


def _aggregate_fixture_payload() -> dict[str, object]:
    root = _root_boundary()
    child = _child_boundary()
    gitdiff_record = _child_record(
        "GitDiff",
        {"status": "changed", "changedFiles": ("synthetic_child.py",)},
        "synthetic child diff recorded",
    )
    testrun_record = _child_record(
        "TestRun",
        {"command": "pytest synthetic_child_test.py", "exitCode": 0, "status": "passed"},
        "synthetic child tests passed",
    )
    contract = EvidenceContract.model_validate(
        {
            "id": "gate1-child-local-contract",
            "triggers": ("afterToolUse",),
            "requirements": (
                {"type": "GitDiff"},
                {
                    "type": "TestRun",
                    "commandPattern": "^pytest synthetic_child_test\\.py$",
                    "exitCode": 0,
                },
            ),
            "onMissing": "block_final_answer",
        }
    )
    verdict = evaluate_evidence_contract(contract, (gitdiff_record, testrun_record))
    assert verdict.ok is True

    envelope = ChildEvidenceEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_evidence_envelope"),
        boundary=child,
        ledgerRef=_ledger_ref(child),
        status="completed",
        evidenceRecords=(gitdiff_record, testrun_record),
        contractVerdicts=(verdict,),
        contractDefinitions=(contract,),
        contractsApply=True,
        report={
            "matchedTypes": ("GitDiff", "TestRun"),
            "missingTypes": (),
            "blockingFailures": (),
            "auditFailures": (),
        },
        summary="Synthetic child reports task success after contract verdict.",
        issuedBy="openmagi_runtime_boundary",
    )
    compatibility = PolicySnapshotCompatibility.model_validate(
        {
            "parentPolicySnapshotId": "gate1-parent-policy-snapshot",
            "childPolicySnapshotId": "gate1-child-policy-snapshot",
            "childExecutionId": "gate1-child-exec-fixture",
            "taskId": "gate1-task-local-child-envelope",
            "reason": "task_local_contracts",
        }
    )
    aggregation = aggregate_child_evidence(
        root,
        (envelope,),
        compatible_policy_snapshots=(compatibility,),
    )
    delegated_required = match_delegated_requirement(
        root,
        DelegatedEvidenceRequirement(type="TestRun", delegation="delegated_required"),
        local_records=(),
        child_aggregation=aggregation,
    )
    aggregate_required = match_delegated_requirement(
        root,
        DelegatedEvidenceRequirement(type="TestRun", delegation="aggregate_required"),
        local_records=(),
        child_aggregation=aggregation,
    )
    natural_language_rejection = natural_language_summary_as_evidence(
        "Synthetic child says it passed."
    )
    public_report = public_child_aggregate_report(aggregation)

    payload = {
        "rootBoundary": root.model_dump(by_alias=True, mode="json", warnings=False),
        "rootLedgerRef": _ledger_ref(root).model_dump(by_alias=True, mode="json", warnings=False),
        "childEnvelope": envelope.model_dump(by_alias=True, mode="json", warnings=False),
        "parentApprovedPolicySnapshotCompatibility": compatibility.model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        ),
        "parentAggregation": aggregation.model_dump(by_alias=True, mode="json", warnings=False),
        "delegatedRequirementMatches": {
            "delegatedRequired": delegated_required.model_dump(
                by_alias=True,
                mode="json",
                warnings=False,
            ),
            "aggregateRequired": aggregate_required.model_dump(
                by_alias=True,
                mode="json",
                warnings=False,
            ),
        },
        "naturalLanguageSummaryAsEvidence": natural_language_rejection.model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        ),
        "publicAggregateReport": public_report.model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        ),
    }
    _assert_all_attachment_flags_false(payload)
    _assert_no_live_runtime_handles(payload)
    return payload


def _assert_all_attachment_flags_false(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ATTACHMENT_FLAGS:
                assert item is False
            _assert_all_attachment_flags_false(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _assert_all_attachment_flags_false(item)


def _assert_no_live_runtime_handles(value: object) -> None:
    blob = json.dumps(value, sort_keys=True)
    for forbidden in FORBIDDEN_LIVE_RUNTIME_KEYS:
        assert forbidden not in blob


def test_gate1_child_envelope_aggregate_matches_golden_fixture() -> None:
    generated = _aggregate_fixture_payload()
    expected = json.loads(FIXTURE_PATH.read_text())

    assert generated == expected
    child_envelope = generated["childEnvelope"]
    assert isinstance(child_envelope, dict)
    assert child_envelope["issuedBy"] == "openmagi_runtime_boundary"
    assert child_envelope["status"] == "completed"
    assert child_envelope["contractVerdicts"][0]["ok"] is True
    assert generated["parentAggregation"]["propagatedEvidence"][0]["provenance"][
        "executionId"
    ] == "gate1-child-exec-fixture"
    assert generated["delegatedRequirementMatches"]["delegatedRequired"]["satisfied"] is True
    assert generated["delegatedRequirementMatches"]["aggregateRequired"]["satisfied"] is False
    assert generated["naturalLanguageSummaryAsEvidence"]["satisfied"] is False
    assert generated["publicAggregateReport"]["children"][0]["executionId"] == (
        "exec:sha256:33d1992dcaf4f4bbd0fc0e30d2b9f1ef475a9d4f7dca3fa555c082bb07bfde27"
    )


def test_gate1_child_authored_json_issuer_is_rejected() -> None:
    payload = _aggregate_fixture_payload()["childEnvelope"]
    assert isinstance(payload, dict)
    payload["issuedBy"] = "child_authored_json"

    with pytest.raises(ValidationError, match="Child-authored JSON is not trusted evidence"):
        ChildEvidenceEnvelope.model_validate(payload)


@pytest.mark.parametrize(
    ("target_path", "forbidden_key"),
    (
        ((), "runnerHandle"),
        (("boundary",), "sessionHandle"),
        (("ledgerRef",), "childAgent"),
    ),
)
def test_gate1_child_envelope_rejects_live_runtime_handle_keys(
    target_path: tuple[str, ...],
    forbidden_key: str,
) -> None:
    payload = _aggregate_fixture_payload()["childEnvelope"]
    assert isinstance(payload, dict)
    target = payload
    for path_entry in target_path:
        target = target[path_entry]
        assert isinstance(target, dict)
    target[forbidden_key] = {"opaque": "live-runtime-handle"}

    with pytest.raises(ValidationError) as exc_info:
        ChildEvidenceEnvelope.model_validate(payload)

    assert forbidden_key in str(exc_info.value)


def test_gate1_fixture_represents_no_live_child_runtime_handles() -> None:
    payload = _aggregate_fixture_payload()

    _assert_no_live_runtime_handles(payload)
    _assert_all_attachment_flags_false(payload)
