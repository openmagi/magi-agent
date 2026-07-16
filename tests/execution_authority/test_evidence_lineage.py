from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from magi_agent.execution_authority.contracts import (
    CoverageDescriptor,
    EvidenceNode,
    FreshnessBinding,
)
from magi_agent.execution_authority.evidence_lineage import (
    EvidenceLineageReducer,
    LineageEvidenceRecord,
    LineageJournalEvent,
    ProducerInvocation,
    ZERO_DIGEST,
)
from magi_agent.execution_authority.state_machine import (
    DependencyStatus,
    EvidenceKind,
    EvidenceSemanticClass,
    VerificationState,
)


NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
STATE_1 = "sha256:" + "1" * 64


def _digest(label: str) -> str:
    return "sha256:" + sha256(label.encode("utf-8")).hexdigest()


def _invocation(*, state_root: str = STATE_1) -> ProducerInvocation:
    return ProducerInvocation(
        invocationEvidenceId="invoke:verifier:1",
        producerId="tests.verifier",
        producerVersion="1.4.0",
        producerSchemaVersion="magi.test_verifier.v1",
        producerStatus=DependencyStatus.CLEAN,
        producerAlive=True,
        taskContractDigest=_digest("task"),
        completionEpochId="epoch_1",
        stateRoot=state_root,
        admissionSequence=1,
        observedAt=NOW,
        reasonCodes=(),
    )


def _node(
    *,
    evidence_id: str,
    sequence: int,
    event_hash: str,
    invocation: ProducerInvocation,
) -> EvidenceNode:
    return EvidenceNode(
        evidenceId=evidence_id,
        kind=EvidenceKind.POSTCONDITION_VERDICT,
        semanticClass=EvidenceSemanticClass.VERDICT,
        sessionId="session_1",
        turnId="turn_1",
        runId="run_1",
        taskContractId="task_1",
        taskVersion=1,
        taskContractDigest=_digest("task"),
        completionEpochId="epoch_1",
        requirementIds=("req_tests",),
        claimIds=(),
        actionId="action_1",
        attemptId="attempt_1",
        requestDigest=_digest("request"),
        authorityDigest=_digest("authority"),
        policyDigest=_digest("policy"),
        producerId=invocation.producer_id,
        producerVersion=invocation.producer_version,
        producerAlive=invocation.producer_alive,
        producerStatus=invocation.producer_status,
        producerSchemaVersion=invocation.producer_schema_version,
        producerInvocationEvidenceId=invocation.invocation_evidence_id,
        producerInvocationEvidenceDigest=invocation.invocation_evidence_digest,
        partitionId="task:task_1:1",
        admissionSequence=1,
        workspaceGeneration=1,
        stateRoot=STATE_1,
        sourceSnapshotId=None,
        sourceSnapshotDigest=None,
        sourceSpans=(),
        researchSource=None,
        contentDigest=_digest(evidence_id + ":content"),
        toolInputDigest=None,
        toolOutputDigest=None,
        parentEvidenceIds=(),
        coverage=CoverageDescriptor(
            coverageKind="resource_inventory",
            journalWindow=None,
            searchedResourceRefs=("workspace://sha256:" + "a" * 64 + "/src/app.py",),
        ),
        freshness=FreshnessBinding(
            rule="same_state_root",
            stateRoot=STATE_1,
            workspaceGeneration=None,
            observedAt=NOW,
        ),
        publicRedactionClass="public_summary",
        reasonCodes=("tests_passed",),
        createdAt=NOW,
        producerPayloadDigest=_digest(evidence_id + ":payload"),
        journalSequence=sequence,
        journalEventHash=event_hash,
    )


def _record_event(
    *,
    sequence: int = 1,
    previous_hash: str = ZERO_DIGEST,
    event_hash: str | None = None,
) -> LineageJournalEvent:
    resolved_hash = event_hash or _digest(f"event:{sequence}")
    invocation = _invocation()
    record = LineageEvidenceRecord(
        node=_node(
            evidence_id="evidence_tests_1",
            sequence=sequence,
            event_hash=resolved_hash,
            invocation=invocation,
        ),
        edges=(),
        producerInvocation=invocation,
        verificationState=VerificationState.PASSED,
    )
    return LineageJournalEvent(
        eventId=f"event_{sequence}",
        partitionId="task:task_1:1",
        sequence=sequence,
        previousEventHash=previous_hash,
        eventHash=resolved_hash,
        eventType="evidence.recorded",
        evidenceRecord=record,
        stateMutation=None,
    )


def test_replay_and_incremental_projection_have_one_canonical_root() -> None:
    recorded = _record_event()
    checkpoint = LineageJournalEvent(
        eventId="event_2",
        partitionId=recorded.partition_id,
        sequence=2,
        previousEventHash=recorded.event_hash,
        eventHash=_digest("event:2"),
        eventType="projection.checkpoint",
        evidenceRecord=None,
        stateMutation=None,
    )
    events = (recorded, checkpoint)

    replayed = EvidenceLineageReducer().reduce(events)
    again = EvidenceLineageReducer().reduce(events)
    partial = EvidenceLineageReducer().reduce(events[:1])
    resumed = EvidenceLineageReducer().reduce(events[1:], base=partial)

    assert replayed.root_digest == again.root_digest == resumed.root_digest
    assert replayed.canonical_bytes() == again.canonical_bytes()
    assert tuple(node.evidence_id for node in replayed.nodes) == ("evidence_tests_1",)
    assert partial.root_digest == replayed.root_digest
    assert replayed.cursor.acknowledged_sequence == 2
    assert resumed.cursor == replayed.cursor


def test_reducer_rejects_non_journal_order_and_cursor_gaps() -> None:
    first = _record_event()
    second = LineageJournalEvent(
        eventId="event_2",
        partitionId=first.partition_id,
        sequence=2,
        previousEventHash=first.event_hash,
        eventHash=_digest("event:2"),
        eventType="projection.checkpoint",
        evidenceRecord=None,
        stateMutation=None,
    )

    with pytest.raises(ValueError, match="journal order"):
        EvidenceLineageReducer().reduce((second, first))

    partial = EvidenceLineageReducer().reduce((first,))
    gap = second.model_dump(by_alias=True, mode="python")
    gap["sequence"] = 3
    with pytest.raises(ValueError, match="projection cursor"):
        EvidenceLineageReducer().reduce((LineageJournalEvent.model_validate(gap),), base=partial)
