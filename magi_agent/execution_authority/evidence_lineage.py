"""Dormant deterministic evidence-lineage projection.

The reducer in this module is deliberately pure: it consumes already
authenticated journal projection records and returns an immutable graph.  It
does not import an engine, route, provider SDK, model client, or persistence
implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import Field, field_validator, model_validator

from magi_agent.execution_authority.envelopes import (
    EnvelopeModel,
    EvidenceEdge,
    EvidenceNode,
    canonical_evidence_node_digest,
)
from magi_agent.execution_authority.state_machine import (
    DependencyStatus,
    EvidenceKind,
    VerificationState,
)


ZERO_DIGEST = "sha256:" + "0" * 64
_LINEAGE_PROJECTION_ID: Literal["evidence_lineage.v1"] = "evidence_lineage.v1"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest_payload(schema_id: str, payload: Mapping[str, object]) -> str:
    encoded = _canonical_json(
        {
            "schemaId": schema_id,
            "payload": payload,
        }
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


class ProducerInvocation(EnvelopeModel):
    """Exact liveness evidence for one producer invocation."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    invocation_evidence_id: str = Field(alias="invocationEvidenceId", min_length=1)
    invocation_evidence_digest: str | None = Field(
        default=None,
        alias="invocationEvidenceDigest",
    )
    producer_id: str = Field(alias="producerId", min_length=1)
    producer_version: str = Field(alias="producerVersion", min_length=1)
    producer_schema_version: str = Field(
        alias="producerSchemaVersion",
        min_length=1,
    )
    producer_status: DependencyStatus = Field(alias="producerStatus")
    producer_alive: bool = Field(alias="producerAlive", strict=True)
    task_contract_digest: str = Field(alias="taskContractDigest")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    state_root: str = Field(alias="stateRoot")
    admission_sequence: int = Field(alias="admissionSequence", ge=0, strict=True)
    observed_at: datetime = Field(alias="observedAt")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")

    @field_validator("reason_codes")
    @classmethod
    def _nonblank_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not reason.strip() for reason in value):
            raise ValueError("producer invocation reasonCodes must not be blank")
        if len(value) != len(set(value)):
            raise ValueError("producer invocation reasonCodes must be unique")
        return value

    @model_validator(mode="after")
    def _bind_invocation_and_liveness(self) -> Self:
        expected = _digest_payload(
            "magi.producer_invocation.v1",
            self.model_dump(
                by_alias=True,
                mode="json",
                exclude={"invocation_evidence_digest"},
            ),
        )
        if (
            self.invocation_evidence_digest is not None
            and self.invocation_evidence_digest != expected
        ):
            raise ValueError("invocationEvidenceDigest does not match producer invocation")
        object.__setattr__(self, "invocation_evidence_digest", expected)
        if self.producer_status is DependencyStatus.CLEAN and not self.producer_alive:
            raise ValueError("clean producer invocation requires a live producer")
        return self

    @property
    def is_live_and_compatible(self) -> bool:
        return self.producer_alive and self.producer_status is DependencyStatus.CLEAN


class LineageEvidenceRecord(EnvelopeModel):
    """A typed evidence projection payload committed by one journal event."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    node: EvidenceNode
    edges: tuple[EvidenceEdge, ...]
    producer_invocation: ProducerInvocation | None = Field(
        default=None,
        alias="producerInvocation",
    )
    verification_state: VerificationState | None = Field(
        default=None,
        alias="verificationState",
    )
    record_digest: str | None = Field(default=None, alias="recordDigest")

    @model_validator(mode="after")
    def _bind_exact_record(self) -> Self:
        node = self.node
        invocation = self.producer_invocation
        if invocation is None:
            if (
                node.producer_invocation_evidence_id is not None
                or node.producer_invocation_evidence_digest is not None
            ):
                raise ValueError("record must embed the producer invocation named by its node")
        else:
            bindings = (
                ("producerId", node.producer_id, invocation.producer_id),
                ("producerVersion", node.producer_version, invocation.producer_version),
                (
                    "producerSchemaVersion",
                    node.producer_schema_version,
                    invocation.producer_schema_version,
                ),
                ("producerStatus", node.producer_status, invocation.producer_status),
                ("producerAlive", node.producer_alive, invocation.producer_alive),
                (
                    "producerInvocationEvidenceId",
                    node.producer_invocation_evidence_id,
                    invocation.invocation_evidence_id,
                ),
                (
                    "producerInvocationEvidenceDigest",
                    node.producer_invocation_evidence_digest,
                    invocation.invocation_evidence_digest,
                ),
                (
                    "taskContractDigest",
                    node.task_contract_digest,
                    invocation.task_contract_digest,
                ),
                (
                    "completionEpochId",
                    node.completion_epoch_id,
                    invocation.completion_epoch_id,
                ),
                ("stateRoot", node.state_root, invocation.state_root),
            )
            for field_name, observed, expected in bindings:
                if observed != expected:
                    raise ValueError(
                        f"evidence node {field_name} does not match producer invocation"
                    )

        if tuple(edge.source_evidence_id for edge in self.edges) != node.parent_evidence_ids:
            raise ValueError("record edges must exactly cover parentEvidenceIds in order")
        if any(edge.target_evidence_id != node.evidence_id for edge in self.edges):
            raise ValueError("record edges must target the recorded evidence node")
        if len({edge.edge_id for edge in self.edges}) != len(self.edges):
            raise ValueError("record edge IDs must be unique")

        verdict_kinds = {
            EvidenceKind.ENTAILMENT_VERDICT,
            EvidenceKind.POSTCONDITION_VERDICT,
            EvidenceKind.REQUIREMENT_VERDICT,
            EvidenceKind.COMPLETION_VERDICT,
            EvidenceKind.WORKSPACE_POSTCONDITION,
        }
        if (node.kind in verdict_kinds) != (self.verification_state is not None):
            raise ValueError("verificationState is required exactly for verdict evidence")

        expected_digest = _digest_payload(
            "magi.lineage_evidence_record.v1",
            self.model_dump(
                by_alias=True,
                mode="json",
                exclude={"record_digest"},
            ),
        )
        if self.record_digest is not None and self.record_digest != expected_digest:
            raise ValueError("recordDigest does not match evidence record")
        object.__setattr__(self, "record_digest", expected_digest)
        return self


class StateMutation(EnvelopeModel):
    """Causal state-root transition used by freshness projection."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    mutation_id: str = Field(alias="mutationId", min_length=1)
    cause_evidence_id: str = Field(alias="causeEvidenceId", min_length=1)
    previous_state_root: str = Field(alias="previousStateRoot")
    resulting_state_root: str = Field(alias="resultingStateRoot")
    affected_resource_refs: tuple[str, ...] = Field(alias="affectedResourceRefs")
    affected_requirement_ids: tuple[str, ...] = Field(alias="affectedRequirementIds")
    mutation_digest: str | None = Field(default=None, alias="mutationDigest")

    @model_validator(mode="after")
    def _bind_transition(self) -> Self:
        if self.previous_state_root == self.resulting_state_root:
            raise ValueError("state mutation must advance the state root")
        if not self.affected_resource_refs and not self.affected_requirement_ids:
            raise ValueError("state mutation requires a declared causal effect")
        for values, label in (
            (self.affected_resource_refs, "affectedResourceRefs"),
            (self.affected_requirement_ids, "affectedRequirementIds"),
        ):
            if any(not value.strip() for value in values) or len(values) != len(set(values)):
                raise ValueError(f"{label} must contain unique nonblank values")
        expected = _digest_payload(
            "magi.state_mutation.v1",
            self.model_dump(by_alias=True, mode="json", exclude={"mutation_digest"}),
        )
        if self.mutation_digest is not None and self.mutation_digest != expected:
            raise ValueError("mutationDigest does not match state mutation")
        object.__setattr__(self, "mutation_digest", expected)
        return self


class LineageJournalEvent(EnvelopeModel):
    """Minimal authenticated journal projection input for the lineage reducer."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    event_id: str = Field(alias="eventId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    sequence: int = Field(ge=1, strict=True)
    previous_event_hash: str = Field(alias="previousEventHash")
    event_hash: str = Field(alias="eventHash")
    event_type: Literal[
        "evidence.recorded",
        "state.mutated",
        "projection.checkpoint",
    ] = Field(alias="eventType")
    evidence_record: LineageEvidenceRecord | None = Field(
        default=None,
        alias="evidenceRecord",
    )
    state_mutation: StateMutation | None = Field(default=None, alias="stateMutation")
    payload_digest: str | None = Field(default=None, alias="payloadDigest")

    @model_validator(mode="after")
    def _bind_event_payload(self) -> Self:
        if self.event_type == "evidence.recorded":
            if self.evidence_record is None or self.state_mutation is not None:
                raise ValueError("evidence.recorded requires only evidenceRecord")
            node = self.evidence_record.node
            if node.partition_id != self.partition_id:
                raise ValueError("evidence node partition does not match journal event")
            if node.journal_sequence != self.sequence:
                raise ValueError("evidence node sequence does not match journal event")
            if node.journal_event_hash != self.event_hash:
                raise ValueError("evidence node hash does not match journal event")
            payload: Mapping[str, object] = {
                "recordDigest": self.evidence_record.record_digest,
            }
        elif self.event_type == "state.mutated":
            if self.state_mutation is None or self.evidence_record is not None:
                raise ValueError("state.mutated requires only stateMutation")
            payload = {"mutationDigest": self.state_mutation.mutation_digest}
        else:
            if self.evidence_record is not None or self.state_mutation is not None:
                raise ValueError("projection.checkpoint cannot carry lineage payload")
            payload = {}
        expected = _digest_payload("magi.lineage_journal_payload.v1", payload)
        if self.payload_digest is not None and self.payload_digest != expected:
            raise ValueError("payloadDigest does not match lineage event payload")
        object.__setattr__(self, "payload_digest", expected)
        return self


class LineageProjectionCursor(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    partition_id: str = Field(alias="partitionId", min_length=1)
    projection_id: Literal["evidence_lineage.v1"] = Field(
        default=_LINEAGE_PROJECTION_ID,
        alias="projectionId",
    )
    acknowledged_sequence: int = Field(alias="acknowledgedSequence", ge=0, strict=True)
    acknowledged_event_hash: str = Field(alias="acknowledgedEventHash")
    graph_root: str = Field(alias="graphRoot")
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)


class EvidenceLineageGraph(EnvelopeModel):
    """Immutable authoritative graph plus a non-authoritative replay cursor."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    partition_id: str = Field(alias="partitionId", min_length=1)
    records: tuple[LineageEvidenceRecord, ...]
    edges: tuple[EvidenceEdge, ...]
    mutations: tuple[StateMutation, ...]
    root_digest: str = Field(alias="rootDigest")
    cursor: LineageProjectionCursor

    @property
    def nodes(self) -> tuple[EvidenceNode, ...]:
        return tuple(record.node for record in self.records)

    def _root_payload(self) -> dict[str, object]:
        return {
            "schemaId": "magi.evidence_lineage_graph.v1",
            "partitionId": self.partition_id,
            "records": [
                {
                    "recordDigest": record.record_digest,
                    "journalSequence": record.node.journal_sequence,
                    "journalEventHash": record.node.journal_event_hash,
                }
                for record in self.records
            ],
            "edges": [edge.model_dump(by_alias=True, mode="json") for edge in self.edges],
            "mutations": [mutation.mutation_digest for mutation in self.mutations],
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self._root_payload()).encode("utf-8")

    @model_validator(mode="after")
    def _validate_graph_root(self) -> Self:
        expected = "sha256:" + sha256(self.canonical_bytes()).hexdigest()
        if self.root_digest != expected:
            raise ValueError("rootDigest does not match canonical evidence graph")
        if self.cursor.partition_id != self.partition_id:
            raise ValueError("projection cursor partition does not match graph")
        if self.cursor.graph_root != self.root_digest:
            raise ValueError("projection cursor graphRoot does not match graph")
        return self


def _graph_root(
    *,
    partition_id: str,
    records: tuple[LineageEvidenceRecord, ...],
    edges: tuple[EvidenceEdge, ...],
    mutations: tuple[StateMutation, ...],
) -> str:
    payload = {
        "schemaId": "magi.evidence_lineage_graph.v1",
        "partitionId": partition_id,
        "records": [
            {
                "recordDigest": record.record_digest,
                "journalSequence": record.node.journal_sequence,
                "journalEventHash": record.node.journal_event_hash,
            }
            for record in records
        ],
        "edges": [edge.model_dump(by_alias=True, mode="json") for edge in edges],
        "mutations": [mutation.mutation_digest for mutation in mutations],
    }
    return "sha256:" + sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class EvidenceLineageReducer:
    """Pure deterministic reducer over a contiguous journal hash chain."""

    def reduce(
        self,
        events: tuple[LineageJournalEvent, ...],
        *,
        base: EvidenceLineageGraph | None = None,
    ) -> EvidenceLineageGraph:
        if type(events) is not tuple or any(
            type(event) is not LineageJournalEvent for event in events
        ):
            raise TypeError("events must be an exact tuple of LineageJournalEvent values")
        if any(
            current.sequence != previous.sequence + 1
            for previous, current in zip(events, events[1:], strict=False)
        ):
            raise ValueError("events are not in stored journal order")
        if any(
            current.previous_event_hash != previous.event_hash
            for previous, current in zip(events, events[1:], strict=False)
        ):
            raise ValueError("events do not form stored journal order")

        if base is None:
            if not events:
                raise ValueError("an initial projection requires at least one event")
            partition_id = events[0].partition_id
            expected_sequence = 1
            expected_previous_hash = ZERO_DIGEST
            records: list[LineageEvidenceRecord] = []
            edges: list[EvidenceEdge] = []
            mutations: list[StateMutation] = []
            compare_version = 0
        else:
            if type(base) is not EvidenceLineageGraph:
                raise TypeError("base must be an exact EvidenceLineageGraph")
            partition_id = base.partition_id
            expected_sequence = base.cursor.acknowledged_sequence + 1
            expected_previous_hash = base.cursor.acknowledged_event_hash
            records = list(base.records)
            edges = list(base.edges)
            mutations = list(base.mutations)
            compare_version = base.cursor.compare_version
            if not events:
                return base

        first = events[0]
        if (
            first.sequence != expected_sequence
            or first.previous_event_hash != expected_previous_hash
        ):
            raise ValueError("events do not continue the projection cursor")
        if any(event.partition_id != partition_id for event in events):
            raise ValueError("one projection batch cannot cross journal partitions")

        node_ids = {record.node.evidence_id for record in records}
        edge_ids = {edge.edge_id for edge in edges}
        for event in events:
            if event.event_type == "evidence.recorded":
                record = event.evidence_record
                if record is None:  # pragma: no cover - model invariant
                    raise AssertionError("validated evidence event lost its record")
                node = record.node
                if node.evidence_id in node_ids:
                    raise ValueError("duplicate evidence ID in journal projection")
                missing_parents = set(node.parent_evidence_ids) - node_ids
                if missing_parents:
                    raise ValueError("evidence record references a future or missing parent")
                for edge in record.edges:
                    if edge.edge_id in edge_ids:
                        raise ValueError("duplicate evidence edge ID in journal projection")
                    edge_ids.add(edge.edge_id)
                    edges.append(edge)
                node_ids.add(node.evidence_id)
                records.append(record)
            elif event.event_type == "state.mutated":
                mutation = event.state_mutation
                if mutation is None:  # pragma: no cover - model invariant
                    raise AssertionError("validated mutation event lost its payload")
                if mutation.cause_evidence_id not in node_ids:
                    raise ValueError("state mutation references missing cause evidence")
                mutations.append(mutation)

        immutable_records = tuple(records)
        immutable_edges = tuple(edges)
        immutable_mutations = tuple(mutations)
        root_digest = _graph_root(
            partition_id=partition_id,
            records=immutable_records,
            edges=immutable_edges,
            mutations=immutable_mutations,
        )
        last = events[-1]
        cursor = LineageProjectionCursor(
            partitionId=partition_id,
            acknowledgedSequence=last.sequence,
            acknowledgedEventHash=last.event_hash,
            graphRoot=root_digest,
            compareVersion=compare_version + len(events),
        )
        return EvidenceLineageGraph(
            partitionId=partition_id,
            records=immutable_records,
            edges=immutable_edges,
            mutations=immutable_mutations,
            rootDigest=root_digest,
            cursor=cursor,
        )


class EntailmentVerdict(StrEnum):
    SUPPORTS = "supports"
    QUALIFIES = "qualifies"
    CONTRADICTS = "contradicts"
    INSUFFICIENT = "insufficient"


@runtime_checkable
class BoundedEntailmentAdapterPort(Protocol):
    """The sole optional semantic adapter; implementations receive bounded input."""

    def evaluate(self, *, request: object) -> object: ...


__all__ = [
    "BoundedEntailmentAdapterPort",
    "EntailmentVerdict",
    "EvidenceLineageGraph",
    "EvidenceLineageReducer",
    "LineageEvidenceRecord",
    "LineageJournalEvent",
    "LineageProjectionCursor",
    "ProducerInvocation",
    "StateMutation",
    "ZERO_DIGEST",
]
