from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any, ClassVar, Literal, Self
from weakref import finalize

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_serializer,
    field_validator,
    model_validator,
)

from .contracts import evaluate_evidence_contract
from .reports import (
    PublicEvidenceFailureReport,
    PublicEvidenceRecordReport,
    PublicEvidenceVerdictReport,
    public_evidence_record_report,
    public_evidence_verdict_report,
)
from .types import (
    EvidenceAgentRole,
    EvidenceContract,
    EvidenceContractFailure,
    EvidenceContractVerdict,
    EvidenceMetadataModel,
    EvidenceRecord,
    EvidenceRunOn,
    EvidenceSourceKind,
    _freeze_mapping,
    _reject_empty_optional_string,
    _serialize_mapping,
    _validate_strict_bool,
    validate_evidence_type_name,
)
from .runtime_issuance import RuntimeIssueAuthority, require_runtime_issue_authority


EvidenceDelegationMode = Literal[
    "local_only",
    "delegated_allowed",
    "delegated_required",
    "aggregate_required",
]
ChildEvidenceStatus = Literal["completed", "blocked", "failed"]
ParentEvidenceState = Literal["pass", "blocked", "failed", "audit"]

OPENMAGI_RUNTIME_ENVELOPE_ISSUER = "openmagi_runtime_boundary"
REQUIRED_SUBAGENT_EVIDENCE_WARNINGS = (
    "Natural-language subagent summaries are never evidence.",
    "Child-authored JSON is not trusted evidence.",
    "Evidence envelopes must be runtime-issued by OpenMagi compatibility/runtime boundary.",
    "Do not enable third-party blocking evidence contracts for delegated coding workflows "
    "until child-ledger propagation and parent aggregation are represented and tested.",
)

_ATTACHMENT_FLAG_NAMES = (
    "traffic_attached",
    "execution_attached",
    "runner_attached",
    "child_execution_attached",
    "session_runtime_attached",
    "artifact_runtime_attached",
    "enforcement_attached",
    "route_attached",
    "api_attached",
    "dashboard_attached",
    "canary_attached",
)
_ATTACHMENT_FLAG_ALIASES = (
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
_BLOCKING_STATES = frozenset(("missing", "failed", "block_ready"))
_CHILD_EVIDENCE_ENVELOPE_OBJECT_IDS: set[int] = set()
_CHILD_EVIDENCE_ENVELOPE_FINGERPRINTS: dict[int, object] = {}
_CHILD_EVIDENCE_ENVELOPE_FINALIZERS: dict[int, object] = {}
_RUNTIME_PARENT_AGGREGATION_TOKEN = object()
_PARENT_AGGREGATION_OBJECT_IDS: set[int] = set()
_PARENT_AGGREGATION_FINGERPRINTS: dict[int, object] = {}
_PARENT_AGGREGATION_FINALIZERS: dict[int, object] = {}


class SubagentEvidenceMetadataModel(EvidenceMetadataModel):
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
            normalized_update = {
                alias_to_name.get(key, key): value for key, value in update.items()
            }
            for flag_name in _ATTACHMENT_FLAG_NAMES:
                normalized_update.pop(flag_name, None)
            data.update(normalized_update)
        for flag_name in _ATTACHMENT_FLAG_NAMES:
            if flag_name in self.__class__.model_fields:
                data[flag_name] = False
        return self.__class__.model_validate(data)


class _AttachmentFlagMixin:
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    child_execution_attached: Literal[False] = Field(
        default=False,
        alias="childExecutionAttached",
    )
    session_runtime_attached: Literal[False] = Field(
        default=False,
        alias="sessionRuntimeAttached",
    )
    artifact_runtime_attached: Literal[False] = Field(
        default=False,
        alias="artifactRuntimeAttached",
    )
    enforcement_attached: Literal[False] = Field(default=False, alias="enforcementAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    api_attached: Literal[False] = Field(default=False, alias="apiAttached")
    dashboard_attached: Literal[False] = Field(default=False, alias="dashboardAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")

    @field_validator(*_ATTACHMENT_FLAG_NAMES, mode="before", check_fields=False)
    @classmethod
    def _validate_attachment_flags(cls, value: object, info: Any) -> object:
        return _validate_strict_bool(value, info.field_name)


class ExecutionBoundaryIdentity(_AttachmentFlagMixin, SubagentEvidenceMetadataModel):
    execution_id: str = Field(alias="executionId")
    agent_id: str = Field(alias="agentId")
    parent_execution_id: str | None = Field(default=None, alias="parentExecutionId")
    task_id: str | None = Field(default=None, alias="taskId")
    turn_id: str | None = Field(default=None, alias="turnId")
    policy_scope: str = Field(alias="policyScope")
    policy_snapshot_id: str = Field(alias="policySnapshotId")
    agent_role: EvidenceAgentRole = Field(alias="agentRole")
    run_on: EvidenceRunOn = Field(alias="runOn")
    spawn_depth: int = Field(alias="spawnDepth")

    @field_validator("execution_id", "agent_id", "policy_scope", "policy_snapshot_id")
    @classmethod
    def _reject_empty_required_ids(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("execution boundary identifiers must be non-empty")
        return value

    @field_validator("parent_execution_id", "task_id", "turn_id")
    @classmethod
    def _reject_empty_optional_ids(cls, value: str | None) -> str | None:
        return _reject_empty_optional_string(value, "execution boundary identifiers")

    @field_validator("spawn_depth", mode="before")
    @classmethod
    def _validate_spawn_depth(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("spawnDepth must be an integer")
        if value < 0:
            raise ValueError("spawnDepth must be non-negative")
        return value

    @model_validator(mode="after")
    def _validate_root_child_depth_rules(self) -> Self:
        if self.run_on == "main":
            if self.spawn_depth != 0:
                raise ValueError("main execution boundaries must use spawnDepth 0")
            if self.parent_execution_id is not None or self.task_id is not None:
                raise ValueError("main execution boundaries must not declare parent/task refs")
        if self.run_on == "child":
            if self.spawn_depth <= 0:
                raise ValueError("child execution boundaries must use positive spawnDepth")
            if self.parent_execution_id is None or self.task_id is None:
                raise ValueError("child execution boundaries require parent and task refs")
        return self


class EvidenceBoundaryLedgerRef(_AttachmentFlagMixin, SubagentEvidenceMetadataModel):
    ledger_id: str = Field(alias="ledgerId")
    execution_id: str = Field(alias="executionId")
    agent_id: str = Field(alias="agentId")
    parent_execution_id: str | None = Field(default=None, alias="parentExecutionId")
    task_id: str | None = Field(default=None, alias="taskId")
    policy_snapshot_id: str = Field(alias="policySnapshotId")
    child_ledger_refs: tuple[str, ...] = Field(default=(), alias="childLedgerRefs")

    @field_validator("ledger_id", "execution_id", "agent_id", "policy_snapshot_id")
    @classmethod
    def _reject_empty_required_ids(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ledger refs must be non-empty")
        return value

    @field_validator("parent_execution_id", "task_id")
    @classmethod
    def _reject_empty_optional_ids(cls, value: str | None) -> str | None:
        return _reject_empty_optional_string(value, "ledger refs")

    @field_validator("child_ledger_refs")
    @classmethod
    def _reject_empty_child_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not ref.strip() for ref in value):
            raise ValueError("childLedgerRefs must contain only non-empty refs")
        if len(set(value)) != len(value):
            raise ValueError("childLedgerRefs must not contain duplicates")
        return value


class PolicySnapshotCompatibility(_AttachmentFlagMixin, SubagentEvidenceMetadataModel):
    parent_policy_snapshot_id: str = Field(alias="parentPolicySnapshotId")
    child_policy_snapshot_id: str = Field(alias="childPolicySnapshotId")
    child_execution_id: str = Field(alias="childExecutionId")
    task_id: str = Field(alias="taskId")
    reason: Literal["task_local_contracts"]

    @field_validator(
        "parent_policy_snapshot_id",
        "child_policy_snapshot_id",
        "child_execution_id",
        "task_id",
    )
    @classmethod
    def _reject_empty_identifiers(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("policy snapshot compatibility identifiers must be non-empty")
        return value

    @model_validator(mode="after")
    def _validate_task_local_snapshot(self) -> Self:
        if self.parent_policy_snapshot_id == self.child_policy_snapshot_id:
            raise ValueError("task-local policy snapshot compatibility requires distinct snapshots")
        return self


class EvidenceProvenance(SubagentEvidenceMetadataModel):
    execution_id: str = Field(alias="executionId")
    agent_id: str = Field(alias="agentId")
    parent_execution_id: str | None = Field(default=None, alias="parentExecutionId")
    task_id: str | None = Field(default=None, alias="taskId")
    policy_snapshot_id: str = Field(alias="policySnapshotId")
    ledger_id: str = Field(alias="ledgerId")

    @field_validator("execution_id", "agent_id", "policy_snapshot_id", "ledger_id")
    @classmethod
    def _reject_empty_required_ids(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence provenance identifiers must be non-empty")
        return value

    @field_validator("parent_execution_id", "task_id")
    @classmethod
    def _reject_empty_optional_ids(cls, value: str | None) -> str | None:
        return _reject_empty_optional_string(value, "evidence provenance identifiers")


class PropagatedEvidenceRecord(SubagentEvidenceMetadataModel):
    record: EvidenceRecord
    provenance: EvidenceProvenance
    produced_by_parent: bool = Field(default=False, alias="producedByParent")

    @field_validator("produced_by_parent", mode="before")
    @classmethod
    def _validate_parent_flag(cls, value: object) -> object:
        return _validate_strict_bool(value, "producedByParent")

    @field_validator("record")
    @classmethod
    def _revalidate_record(cls, value: EvidenceRecord) -> EvidenceRecord:
        return EvidenceRecord.model_validate(value.model_dump(by_alias=True))

    @model_validator(mode="after")
    def _preserve_record_provenance(self) -> Self:
        metadata = self.record.source.metadata
        if metadata.get("executionId") != self.provenance.execution_id:
            raise ValueError("propagated evidence must preserve source executionId")
        if metadata.get("agentId") != self.provenance.agent_id:
            raise ValueError("propagated evidence must preserve source agentId")
        if metadata.get("parentExecutionId") != self.provenance.parent_execution_id:
            raise ValueError("propagated evidence must preserve source parentExecutionId")
        if metadata.get("taskId") != self.provenance.task_id:
            raise ValueError("propagated evidence must preserve source taskId")
        if metadata.get("policySnapshotId") != self.provenance.policy_snapshot_id:
            raise ValueError("propagated evidence must preserve source policySnapshotId")
        return self


class ChildEvidenceEnvelope(_AttachmentFlagMixin, SubagentEvidenceMetadataModel):
    _issued_by_runtime_boundary: bool = PrivateAttr(default=False)

    boundary: ExecutionBoundaryIdentity
    ledger_ref: EvidenceBoundaryLedgerRef = Field(alias="ledgerRef")
    status: ChildEvidenceStatus
    evidence_records: tuple[EvidenceRecord, ...] = Field(default=(), alias="evidenceRecords")
    contract_verdicts: tuple[EvidenceContractVerdict, ...] = Field(
        default=(),
        alias="contractVerdicts",
    )
    contract_definitions: tuple[EvidenceContract, ...] = Field(
        default=(),
        alias="contractDefinitions",
    )
    contracts_apply: bool = Field(default=True, alias="contractsApply")
    report: Mapping[str, object] = Field(default_factory=dict)
    summary: str | None = None
    issued_by: Literal["openmagi_runtime_boundary"] = Field(alias="issuedBy")

    @classmethod
    def issue_runtime_envelope(
        cls,
        *,
        runtime_authority: RuntimeIssueAuthority | None = None,
        **payload: object,
    ) -> Self:
        require_runtime_issue_authority(runtime_authority, scope="child_evidence_envelope")
        envelope = cls.model_validate(payload)
        _mark_child_evidence_envelope_issued(envelope)
        return envelope

    @property
    def is_runtime_boundary_issued(self) -> bool:
        object_id = id(self)
        return (
            bool(self.__pydantic_private__.get("_issued_by_runtime_boundary"))
            and object_id in _CHILD_EVIDENCE_ENVELOPE_OBJECT_IDS
            and _CHILD_EVIDENCE_ENVELOPE_FINGERPRINTS.get(object_id)
            == _model_fingerprint(self)
        )

    @field_validator("issued_by", mode="before")
    @classmethod
    def _require_runtime_issuer(cls, value: object) -> object:
        if value == "child_authored_json":
            raise ValueError("Child-authored JSON is not trusted evidence.")
        if value != OPENMAGI_RUNTIME_ENVELOPE_ISSUER:
            raise ValueError(
                "Evidence envelopes must be runtime-issued by OpenMagi "
                "compatibility/runtime boundary."
            )
        return value

    @field_validator("contracts_apply", mode="before")
    @classmethod
    def _validate_contracts_apply(cls, value: object) -> object:
        return _validate_strict_bool(value, "contractsApply")

    @field_validator("boundary")
    @classmethod
    def _revalidate_boundary(cls, value: ExecutionBoundaryIdentity) -> ExecutionBoundaryIdentity:
        return ExecutionBoundaryIdentity.model_validate(value.model_dump(by_alias=True))

    @field_validator("ledger_ref")
    @classmethod
    def _revalidate_ledger_ref(cls, value: EvidenceBoundaryLedgerRef) -> EvidenceBoundaryLedgerRef:
        return EvidenceBoundaryLedgerRef.model_validate(value.model_dump(by_alias=True))

    @field_validator("evidence_records")
    @classmethod
    def _revalidate_records(cls, value: tuple[EvidenceRecord, ...]) -> tuple[EvidenceRecord, ...]:
        return tuple(EvidenceRecord.model_validate(record.model_dump(by_alias=True)) for record in value)

    @field_validator("contract_verdicts")
    @classmethod
    def _revalidate_verdicts(
        cls,
        value: tuple[EvidenceContractVerdict, ...],
    ) -> tuple[EvidenceContractVerdict, ...]:
        return tuple(
            EvidenceContractVerdict.model_validate(verdict.model_dump(by_alias=True))
            for verdict in value
        )

    @field_validator("contract_definitions")
    @classmethod
    def _revalidate_contract_definitions(
        cls,
        value: tuple[EvidenceContract, ...],
    ) -> tuple[EvidenceContract, ...]:
        contracts = tuple(
            EvidenceContract.model_validate(contract.model_dump(by_alias=True))
            for contract in value
        )
        contract_ids = tuple(contract.id for contract in contracts)
        if len(set(contract_ids)) != len(contract_ids):
            raise ValueError("contractDefinitions must not contain duplicate contract ids")
        return contracts

    @field_validator("report")
    @classmethod
    def _freeze_report(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _freeze_mapping(value, "report")

    @field_validator("summary")
    @classmethod
    def _reject_empty_summary(cls, value: str | None) -> str | None:
        return _reject_empty_optional_string(value, "summary")

    @field_serializer("report")
    def _serialize_report(self, value: Mapping[str, object]) -> dict[str, object]:
        return _serialize_mapping(value) or {}

    @model_validator(mode="after")
    def _validate_child_evidence_contract(self) -> Self:
        if self.boundary.run_on != "child":
            raise ValueError("child evidence envelopes require a child execution boundary")
        if self.ledger_ref.execution_id != self.boundary.execution_id:
            raise ValueError("ledgerRef executionId must match child boundary")
        if self.ledger_ref.agent_id != self.boundary.agent_id:
            raise ValueError("ledgerRef agentId must match child boundary")
        if self.ledger_ref.parent_execution_id != self.boundary.parent_execution_id:
            raise ValueError("ledgerRef parentExecutionId must match child boundary")
        if self.ledger_ref.task_id != self.boundary.task_id:
            raise ValueError("ledgerRef taskId must match child boundary")
        if self.ledger_ref.policy_snapshot_id != self.boundary.policy_snapshot_id:
            raise ValueError("ledgerRef policySnapshotId must match child boundary")
        if self.status == "completed" and self.contracts_apply and not self.contract_verdicts:
            raise ValueError("child success requires at least one child-local contract verdict")
        if self.status == "completed" and _has_blocking_failure(self.contract_verdicts):
            raise ValueError("child with blocking evidence failure cannot report completed")
        for record in self.evidence_records:
            if record.source.kind == "external_ack":
                raise ValueError("external acknowledgement ingestion remains approval-gated")
            _validate_record_boundary(record, self.boundary)
        for verdict in self.contract_verdicts:
            for record in verdict.matched_evidence:
                if record.source.kind == "external_ack":
                    raise ValueError("external acknowledgement ingestion remains approval-gated")
                _validate_record_boundary(record, self.boundary)
                if record not in self.evidence_records:
                    raise ValueError(
                        "child verdict matched evidence must be present in envelope evidenceRecords"
                    )
        return self


class ParentEvidenceAggregation(_AttachmentFlagMixin, SubagentEvidenceMetadataModel):
    _issued_by_runtime_boundary: bool = PrivateAttr(default=False)

    parent_boundary: ExecutionBoundaryIdentity = Field(alias="parentBoundary")
    child_envelopes: tuple[ChildEvidenceEnvelope, ...] = Field(alias="childEnvelopes")
    propagated_evidence: tuple[PropagatedEvidenceRecord, ...] = Field(
        alias="propagatedEvidence",
    )
    state: ParentEvidenceState
    blocking_child_failures: tuple[EvidenceContractFailure, ...] = Field(
        default=(),
        alias="blockingChildFailures",
    )
    audit_child_failures: tuple[EvidenceContractFailure, ...] = Field(
        default=(),
        alias="auditChildFailures",
    )
    compatible_policy_snapshots: tuple[PolicySnapshotCompatibility, ...] = Field(
        default=(),
        alias="compatiblePolicySnapshots",
    )
    issuance_token: object | None = Field(
        default=None,
        alias="issuanceToken",
        exclude=True,
        repr=False,
    )
    _issuance_token: ClassVar[object] = _RUNTIME_PARENT_AGGREGATION_TOKEN

    @classmethod
    def _from_runtime(
        cls,
        *,
        parent_boundary: ExecutionBoundaryIdentity,
        child_envelopes: tuple[ChildEvidenceEnvelope, ...],
        propagated_evidence: tuple[PropagatedEvidenceRecord, ...],
        state: ParentEvidenceState,
        blocking_child_failures: tuple[EvidenceContractFailure, ...],
        audit_child_failures: tuple[EvidenceContractFailure, ...],
        compatible_policy_snapshots: tuple[PolicySnapshotCompatibility, ...],
    ) -> Self:
        aggregation = cls(
            parentBoundary=parent_boundary,
            childEnvelopes=child_envelopes,
            propagatedEvidence=propagated_evidence,
            state=state,
            blockingChildFailures=blocking_child_failures,
            auditChildFailures=audit_child_failures,
            compatiblePolicySnapshots=compatible_policy_snapshots,
            issuanceToken=cls._issuance_token,
            trafficAttached=False,
            executionAttached=False,
            runnerAttached=False,
            childExecutionAttached=False,
            sessionRuntimeAttached=False,
            artifactRuntimeAttached=False,
            enforcementAttached=False,
            routeAttached=False,
            apiAttached=False,
            dashboardAttached=False,
            canaryAttached=False,
        )
        _mark_parent_evidence_aggregation_issued(aggregation)
        return aggregation

    @property
    def is_runtime_boundary_issued(self) -> bool:
        object_id = id(self)
        return (
            bool(self.__pydantic_private__.get("_issued_by_runtime_boundary"))
            and object_id in _PARENT_AGGREGATION_OBJECT_IDS
            and _PARENT_AGGREGATION_FINGERPRINTS.get(object_id)
            == _model_fingerprint(self)
        )

    @field_validator("parent_boundary")
    @classmethod
    def _revalidate_parent_boundary(
        cls,
        value: ExecutionBoundaryIdentity,
    ) -> ExecutionBoundaryIdentity:
        return ExecutionBoundaryIdentity.model_validate(value.model_dump(by_alias=True))

    @field_validator("child_envelopes")
    @classmethod
    def _revalidate_child_envelopes(
        cls,
        value: tuple[ChildEvidenceEnvelope, ...],
    ) -> tuple[ChildEvidenceEnvelope, ...]:
        return tuple(
            ChildEvidenceEnvelope.model_validate(envelope.model_dump(by_alias=True))
            for envelope in value
        )

    @field_validator("propagated_evidence")
    @classmethod
    def _revalidate_propagated_evidence(
        cls,
        value: tuple[PropagatedEvidenceRecord, ...],
    ) -> tuple[PropagatedEvidenceRecord, ...]:
        return tuple(
            PropagatedEvidenceRecord.model_validate(record.model_dump(by_alias=True))
            for record in value
        )

    @field_validator("blocking_child_failures", "audit_child_failures")
    @classmethod
    def _revalidate_child_failures(
        cls,
        value: tuple[EvidenceContractFailure, ...],
    ) -> tuple[EvidenceContractFailure, ...]:
        return tuple(
            EvidenceContractFailure.model_validate(failure.model_dump(by_alias=True))
            for failure in value
        )

    @field_validator("compatible_policy_snapshots")
    @classmethod
    def _revalidate_policy_snapshot_compatibility(
        cls,
        value: tuple[PolicySnapshotCompatibility, ...],
    ) -> tuple[PolicySnapshotCompatibility, ...]:
        return tuple(
            PolicySnapshotCompatibility.model_validate(item.model_dump(by_alias=True))
            for item in value
        )

    @model_validator(mode="after")
    def _validate_parent_aggregation_contract(self) -> Self:
        propagated, state, blocking_failures, audit_failures = _parent_aggregation_components(
            self.parent_boundary,
            self.child_envelopes,
            self.compatible_policy_snapshots,
        )
        if self.state != state:
            raise ValueError("parent aggregation state must match child envelope state")
        if self.blocking_child_failures != blocking_failures:
            raise ValueError("blockingChildFailures must match child verdict failures")
        if self.audit_child_failures != audit_failures:
            raise ValueError("auditChildFailures must match child verdict failures")
        if _canonical_propagated_evidence(self.propagated_evidence) != (
            _canonical_propagated_evidence(propagated)
        ):
            raise ValueError(
                "propagatedEvidence must exactly match eligible completed child verdict "
                "coverage; propagated evidence cannot be omitted, duplicated, or forged"
            )
        if self.issuance_token is not self._issuance_token:
            raise ValueError("runtime-issued parent evidence aggregation required")
        return self


class DelegatedEvidenceRequirement(SubagentEvidenceMetadataModel):
    type: str
    delegation: EvidenceDelegationMode = "local_only"

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        return validate_evidence_type_name(value)


class DelegatedRequirementMatch(SubagentEvidenceMetadataModel):
    requirement: DelegatedEvidenceRequirement | None = None
    satisfied: bool
    mode: EvidenceDelegationMode | None = None
    matched_evidence: tuple[PropagatedEvidenceRecord, ...] = Field(
        default=(),
        alias="matchedEvidence",
    )
    reason: str | None = None

    @field_validator("satisfied", mode="before")
    @classmethod
    def _validate_satisfied(cls, value: object) -> object:
        return _validate_strict_bool(value, "satisfied")


class CustomChildEvidenceSchema(_AttachmentFlagMixin, SubagentEvidenceMetadataModel):
    type: str
    source_kind: EvidenceSourceKind = Field(alias="sourceKind")
    fields: Mapping[str, object]
    hard_safety: Literal[False] = Field(default=False, alias="hardSafety")
    external_ack_ingestion_attached: Literal[False] = Field(
        default=False,
        alias="externalAckIngestionAttached",
    )
    live_extractor_execution_attached: Literal[False] = Field(
        default=False,
        alias="liveExtractorExecutionAttached",
    )

    @field_validator("type")
    @classmethod
    def _validate_custom_type(cls, value: str) -> str:
        validated = validate_evidence_type_name(value)
        if not validated.startswith("custom:"):
            raise ValueError("custom child evidence must use custom:* declarative metadata")
        return validated

    @field_validator(
        "hard_safety",
        "external_ack_ingestion_attached",
        "live_extractor_execution_attached",
        mode="before",
    )
    @classmethod
    def _validate_false_flags(cls, value: object, info: Any) -> object:
        return _validate_strict_bool(value, info.field_name)

    @field_validator("fields")
    @classmethod
    def _freeze_fields(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        if not value:
            raise ValueError("custom child evidence fields must be non-empty")
        return _freeze_mapping(value, "fields")

    @field_serializer("fields")
    def _serialize_fields(self, value: Mapping[str, object]) -> dict[str, object]:
        return _serialize_mapping(value) or {}

    @model_validator(mode="after")
    def _validate_declarative_only(self) -> Self:
        if self.source_kind == "external_ack":
            raise ValueError("signed/external acknowledgement ingestion remains approval-gated")
        if self.source_kind != "custom_extractor":
            raise ValueError("custom child evidence is declarative custom_extractor metadata only")
        return self


class PublicChildEvidenceReport(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    execution_id: str = Field(alias="executionId")
    agent_id: str = Field(alias="agentId")
    task_id: str | None = Field(default=None, alias="taskId")
    parent_execution_id: str | None = Field(default=None, alias="parentExecutionId")
    policy_snapshot_id: str = Field(alias="policySnapshotId")
    status: ChildEvidenceStatus
    matched_types: tuple[str, ...] = Field(alias="matchedTypes")
    missing_types: tuple[str, ...] = Field(alias="missingTypes")
    blocking_failures: tuple[PublicEvidenceFailureReport, ...] = Field(
        alias="blockingFailures",
    )
    audit_failures: tuple[PublicEvidenceFailureReport, ...] = Field(alias="auditFailures")
    verdicts: tuple[PublicEvidenceVerdictReport, ...]
    evidence: tuple[PublicEvidenceRecordReport, ...]


class PublicChildAggregateReport(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    parent_execution_id: str = Field(alias="parentExecutionId")
    state: ParentEvidenceState
    children: tuple[PublicChildEvidenceReport, ...]
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    child_execution_attached: Literal[False] = Field(
        default=False,
        alias="childExecutionAttached",
    )
    session_runtime_attached: Literal[False] = Field(
        default=False,
        alias="sessionRuntimeAttached",
    )
    artifact_runtime_attached: Literal[False] = Field(
        default=False,
        alias="artifactRuntimeAttached",
    )
    enforcement_attached: Literal[False] = Field(default=False, alias="enforcementAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    api_attached: Literal[False] = Field(default=False, alias="apiAttached")
    dashboard_attached: Literal[False] = Field(default=False, alias="dashboardAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")

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
            normalized_update = {
                alias_to_name.get(key, key): value for key, value in update.items()
            }
            for flag_name in _ATTACHMENT_FLAG_NAMES:
                normalized_update.pop(flag_name, None)
            data.update(normalized_update)
        for flag_name in _ATTACHMENT_FLAG_NAMES:
            data[flag_name] = False
        return self.__class__.model_validate(data)


def aggregate_child_evidence(
    parent_boundary: ExecutionBoundaryIdentity,
    child_envelopes: tuple[ChildEvidenceEnvelope, ...],
    *,
    compatible_policy_snapshots: tuple[PolicySnapshotCompatibility, ...] = (),
) -> ParentEvidenceAggregation:
    parent = ExecutionBoundaryIdentity.model_validate(parent_boundary.model_dump(by_alias=True))
    if parent.run_on != "main":
        raise ValueError("parent aggregation requires a main execution boundary")
    if any(
        not isinstance(envelope, ChildEvidenceEnvelope) or not envelope.is_runtime_boundary_issued
        for envelope in child_envelopes
    ):
        raise ValueError("parent aggregation requires runtime-issued child evidence envelopes")

    envelopes = tuple(
        ChildEvidenceEnvelope.model_validate(envelope.model_dump(by_alias=True))
        for envelope in child_envelopes
    )
    compatibility = tuple(
        PolicySnapshotCompatibility.model_validate(item.model_dump(by_alias=True))
        for item in compatible_policy_snapshots
    )
    propagated, state, blocking_failures, audit_failures = _parent_aggregation_components(
        parent,
        envelopes,
        compatibility,
    )

    return ParentEvidenceAggregation._from_runtime(
        parent_boundary=parent,
        child_envelopes=envelopes,
        propagated_evidence=propagated,
        state=state,
        blocking_child_failures=blocking_failures,
        audit_child_failures=audit_failures,
        compatible_policy_snapshots=compatibility,
    )


def match_delegated_requirement(
    parent_boundary: ExecutionBoundaryIdentity,
    requirement: DelegatedEvidenceRequirement,
    *,
    local_records: tuple[EvidenceRecord, ...],
    child_aggregation: ParentEvidenceAggregation | None = None,
) -> DelegatedRequirementMatch:
    parent = ExecutionBoundaryIdentity.model_validate(parent_boundary.model_dump(by_alias=True))
    parsed_requirement = DelegatedEvidenceRequirement.model_validate(
        requirement.model_dump(by_alias=True)
    )
    local_matches = tuple(
        PropagatedEvidenceRecord(
            record=record,
            provenance=EvidenceProvenance(
                executionId=parent.execution_id,
                agentId=parent.agent_id,
                parentExecutionId=None,
                taskId=None,
                policySnapshotId=parent.policy_snapshot_id,
                ledgerId=f"local:{parent.execution_id}",
            ),
            producedByParent=True,
        )
        for record in local_records
        if _record_matches_requirement(
            record,
            parsed_requirement.type,
            parent.execution_id,
            parent.agent_id,
            parent.policy_snapshot_id,
            None,
            None,
        )
    )
    parsed_aggregation = None
    if child_aggregation is not None:
        parsed_aggregation = _require_runtime_parent_aggregation(child_aggregation)
        if parsed_aggregation.parent_boundary.execution_id != parent.execution_id:
            return DelegatedRequirementMatch(
                requirement=parsed_requirement,
                satisfied=False,
                mode=parsed_requirement.delegation,
                reason="child aggregation parent boundary does not match requirement parent boundary",
            )
        if parsed_aggregation.parent_boundary.policy_snapshot_id != parent.policy_snapshot_id:
            return DelegatedRequirementMatch(
                requirement=parsed_requirement,
                satisfied=False,
                mode=parsed_requirement.delegation,
                reason="child aggregation policy snapshot does not match requirement parent boundary",
            )
        if parsed_aggregation.state in ("blocked", "failed"):
            return DelegatedRequirementMatch(
                requirement=parsed_requirement,
                satisfied=False,
                mode=parsed_requirement.delegation,
                reason=f"child aggregation state is {parsed_aggregation.state}",
            )
    child_matches = (
        tuple(
            propagated
            for propagated in parsed_aggregation.propagated_evidence
            if propagated.record.type == parsed_requirement.type
            and propagated.record.status == "ok"
        )
        if parsed_aggregation is not None
        else ()
    )

    if parsed_requirement.delegation == "local_only":
        matched = local_matches
    elif parsed_requirement.delegation == "delegated_allowed":
        matched = (*local_matches, *child_matches)
    elif parsed_requirement.delegation == "delegated_required":
        matched = child_matches
    else:
        matched = local_matches

    return DelegatedRequirementMatch(
        requirement=parsed_requirement,
        satisfied=bool(matched),
        mode=parsed_requirement.delegation,
        matchedEvidence=matched,
        reason=None if matched else f"{parsed_requirement.delegation} requirement was not satisfied",
    )


def natural_language_summary_as_evidence(summary: str) -> DelegatedRequirementMatch:
    if not summary.strip():
        raise ValueError("summary must be non-empty")
    return DelegatedRequirementMatch(
        satisfied=False,
        reason="Natural-language subagent summaries are never evidence.",
    )


def public_child_aggregate_report(
    aggregation: ParentEvidenceAggregation,
) -> PublicChildAggregateReport:
    parsed_aggregation = _require_runtime_parent_aggregation(aggregation)
    children = []
    for envelope in parsed_aggregation.child_envelopes:
        verdicts = tuple(public_evidence_verdict_report(verdict) for verdict in envelope.contract_verdicts)
        children.append(
            PublicChildEvidenceReport(
                executionId=_public_boundary_identifier("exec", envelope.boundary.execution_id),
                agentId=_public_boundary_identifier("agent", envelope.boundary.agent_id),
                taskId=_public_optional_boundary_identifier("task", envelope.boundary.task_id),
                parentExecutionId=_public_optional_boundary_identifier(
                    "exec",
                    envelope.boundary.parent_execution_id,
                ),
                policySnapshotId=_public_boundary_identifier(
                    "policy",
                    envelope.boundary.policy_snapshot_id,
                ),
                status=envelope.status,
                matchedTypes=_public_child_matched_types(verdicts),
                missingTypes=_public_child_missing_types(verdicts),
                blockingFailures=_public_child_failures(verdicts, "block_final_answer"),
                auditFailures=_public_child_failures(verdicts, "audit"),
                verdicts=verdicts,
                evidence=tuple(
                    public_evidence_record_report(record) for record in envelope.evidence_records
                ),
            )
        )
    return PublicChildAggregateReport(
        parentExecutionId=_public_boundary_identifier(
            "exec",
            parsed_aggregation.parent_boundary.execution_id,
        ),
        state=parsed_aggregation.state,
        children=tuple(children),
        trafficAttached=False,
        executionAttached=False,
        runnerAttached=False,
        childExecutionAttached=False,
        sessionRuntimeAttached=False,
        artifactRuntimeAttached=False,
        enforcementAttached=False,
        routeAttached=False,
        apiAttached=False,
        dashboardAttached=False,
        canaryAttached=False,
    )


def _public_optional_boundary_identifier(prefix: str, value: str | None) -> str | None:
    if value is None:
        return None
    return _public_boundary_identifier(prefix, value)


def _public_boundary_identifier(prefix: str, value: str) -> str:
    return f"{prefix}:sha256:{sha256(value.encode('utf-8')).hexdigest()}"


def _parent_aggregation_components(
    parent: ExecutionBoundaryIdentity,
    envelopes: tuple[ChildEvidenceEnvelope, ...],
    compatible_policy_snapshots: tuple[PolicySnapshotCompatibility, ...] = (),
) -> tuple[
    tuple[PropagatedEvidenceRecord, ...],
    ParentEvidenceState,
    tuple[EvidenceContractFailure, ...],
    tuple[EvidenceContractFailure, ...],
]:
    if parent.run_on != "main":
        raise ValueError("parent aggregation requires a main execution boundary")

    propagated: list[PropagatedEvidenceRecord] = []
    blocking_failures: list[EvidenceContractFailure] = []
    audit_failures: list[EvidenceContractFailure] = []
    has_blocked_child = False
    has_failed_child = False

    for envelope in envelopes:
        if envelope.boundary.parent_execution_id != parent.execution_id:
            raise ValueError("child envelope parentExecutionId must match parent boundary")
        if not _policy_snapshot_is_compatible(
            parent,
            envelope,
            compatible_policy_snapshots,
        ):
            raise ValueError("child envelope policy snapshot must match parent boundary")
        if envelope.status == "blocked":
            has_blocked_child = True
        elif envelope.status == "failed":
            has_failed_child = True
        for verdict in envelope.contract_verdicts:
            if verdict.enforcement == "block_final_answer" and not verdict.ok:
                blocking_failures.extend(verdict.failures)
            elif verdict.enforcement == "audit" and not verdict.ok:
                audit_failures.extend(verdict.failures)
        for record in _propagatable_child_records(envelope):
            propagated.append(
                PropagatedEvidenceRecord(
                    record=record,
                    provenance=EvidenceProvenance(
                        executionId=envelope.boundary.execution_id,
                        agentId=envelope.boundary.agent_id,
                        parentExecutionId=envelope.boundary.parent_execution_id,
                        taskId=envelope.boundary.task_id,
                        policySnapshotId=envelope.boundary.policy_snapshot_id,
                        ledgerId=envelope.ledger_ref.ledger_id,
                    ),
                    producedByParent=False,
                )
            )

    if blocking_failures or has_blocked_child:
        state: ParentEvidenceState = "blocked"
    elif has_failed_child:
        state = "failed"
    elif audit_failures:
        state = "audit"
    else:
        state = "pass"

    return tuple(propagated), state, tuple(blocking_failures), tuple(audit_failures)


def _propagatable_child_records(envelope: ChildEvidenceEnvelope) -> tuple[EvidenceRecord, ...]:
    if envelope.status != "completed":
        return ()
    if not envelope.contracts_apply or not envelope.contract_verdicts:
        return ()
    return tuple(
        record
        for record in envelope.evidence_records
        if any(
            _verdict_can_propagate_record(envelope, verdict, record)
            and record in verdict.matched_evidence
            for verdict in envelope.contract_verdicts
        )
    )


def _verdict_can_propagate_evidence(verdict: EvidenceContractVerdict) -> bool:
    return (
        verdict.ok
        and verdict.state == "pass"
        and not verdict.missing_requirements
        and not verdict.failures
    )


def _verdict_can_propagate_record(
    envelope: ChildEvidenceEnvelope,
    verdict: EvidenceContractVerdict,
    record: EvidenceRecord,
) -> bool:
    if not _verdict_can_propagate_evidence(verdict):
        return False
    covered_types = _verdict_requirement_coverage_types(envelope, verdict)
    if covered_types:
        return record.type in covered_types
    return len(verdict.matched_evidence) == 1 and verdict.matched_evidence[0] == record


def _policy_snapshot_is_compatible(
    parent: ExecutionBoundaryIdentity,
    envelope: ChildEvidenceEnvelope,
    compatible_policy_snapshots: tuple[PolicySnapshotCompatibility, ...],
) -> bool:
    if envelope.boundary.policy_snapshot_id == parent.policy_snapshot_id:
        return True
    return any(
        compatibility.parent_policy_snapshot_id == parent.policy_snapshot_id
        and compatibility.child_policy_snapshot_id == envelope.boundary.policy_snapshot_id
        and compatibility.child_execution_id == envelope.boundary.execution_id
        and compatibility.task_id == envelope.boundary.task_id
        for compatibility in compatible_policy_snapshots
    )


def _verdict_requirement_coverage_types(
    envelope: ChildEvidenceEnvelope,
    verdict: EvidenceContractVerdict,
) -> frozenset[str]:
    if verdict.ok:
        return _verified_contract_requirement_coverage_types(envelope, verdict)
    covered: set[str] = set()
    covered.update(requirement.type for requirement in verdict.missing_requirements)
    covered.update(
        failure.requirement_type
        for failure in verdict.failures
        if failure.requirement_type is not None
    )
    if covered:
        return frozenset(covered)
    return _verified_contract_requirement_coverage_types(envelope, verdict)


def _verified_contract_requirement_coverage_types(
    envelope: ChildEvidenceEnvelope,
    verdict: EvidenceContractVerdict,
) -> frozenset[str]:
    for contract in envelope.contract_definitions:
        if contract.id != verdict.contract_id:
            continue
        evaluated = evaluate_evidence_contract(contract, envelope.evidence_records)
        if _canonical_verdict_payload(evaluated) == _canonical_verdict_payload(verdict):
            return frozenset(requirement.type for requirement in contract.requirements)
    return frozenset()


def _canonical_verdict_payload(verdict: EvidenceContractVerdict) -> dict[str, object]:
    return verdict.model_dump(by_alias=True, mode="json", warnings=False)


def _canonical_propagated_evidence(
    records: tuple[PropagatedEvidenceRecord, ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        record.model_dump(by_alias=True, mode="json", warnings=False)
        for record in records
    )


def _public_child_matched_types(
    verdicts: tuple[PublicEvidenceVerdictReport, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            record.type
            for verdict in verdicts
            for record in verdict.matched_evidence
        )
    )


def _public_child_missing_types(
    verdicts: tuple[PublicEvidenceVerdictReport, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            requirement.type
            for verdict in verdicts
            for requirement in verdict.missing_requirements
        )
    )


def _public_child_failures(
    verdicts: tuple[PublicEvidenceVerdictReport, ...],
    enforcement: Literal["audit", "block_final_answer"],
) -> tuple[PublicEvidenceFailureReport, ...]:
    return tuple(
        failure
        for verdict in verdicts
        if verdict.enforcement == enforcement and not verdict.ok
        for failure in verdict.failures
    )


def _has_blocking_failure(verdicts: tuple[EvidenceContractVerdict, ...]) -> bool:
    return any(
        verdict.enforcement == "block_final_answer"
        and (not verdict.ok or verdict.state in _BLOCKING_STATES)
        for verdict in verdicts
    )


def _validate_record_boundary(
    record: EvidenceRecord,
    boundary: ExecutionBoundaryIdentity,
) -> None:
    metadata = record.source.metadata
    if metadata.get("executionId") != boundary.execution_id:
        raise ValueError("child evidence record executionId must match child boundary")
    if metadata.get("agentId") != boundary.agent_id:
        raise ValueError("child evidence record agentId must match child boundary")
    if metadata.get("parentExecutionId") != boundary.parent_execution_id:
        raise ValueError("child evidence record parentExecutionId must match child boundary")
    if metadata.get("taskId") != boundary.task_id:
        raise ValueError("child evidence record taskId must match child boundary")
    if metadata.get("policySnapshotId") != boundary.policy_snapshot_id:
        raise ValueError("child evidence record policySnapshotId must match child boundary")


def _record_matches_requirement(
    record: EvidenceRecord,
    evidence_type: str,
    execution_id: str,
    agent_id: str,
    policy_snapshot_id: str,
    parent_execution_id: str | None,
    task_id: str | None,
) -> bool:
    return (
        record.type == evidence_type
        and record.status == "ok"
        and record.source.metadata.get("executionId") == execution_id
        and record.source.metadata.get("agentId") == agent_id
        and record.source.metadata.get("policySnapshotId") == policy_snapshot_id
        and record.source.metadata.get("parentExecutionId") == parent_execution_id
        and record.source.metadata.get("taskId") == task_id
    )


def _mark_child_evidence_envelope_issued(envelope: ChildEvidenceEnvelope) -> None:
    object_id = id(envelope)
    envelope.__pydantic_private__["_issued_by_runtime_boundary"] = True
    _CHILD_EVIDENCE_ENVELOPE_OBJECT_IDS.add(object_id)
    _CHILD_EVIDENCE_ENVELOPE_FINGERPRINTS[object_id] = _model_fingerprint(envelope)
    _CHILD_EVIDENCE_ENVELOPE_FINALIZERS[object_id] = finalize(
        envelope,
        _discard_child_evidence_envelope_object_id,
        object_id,
    )


def _discard_child_evidence_envelope_object_id(object_id: int) -> None:
    _CHILD_EVIDENCE_ENVELOPE_OBJECT_IDS.discard(object_id)
    _CHILD_EVIDENCE_ENVELOPE_FINGERPRINTS.pop(object_id, None)
    _CHILD_EVIDENCE_ENVELOPE_FINALIZERS.pop(object_id, None)


def _mark_parent_evidence_aggregation_issued(aggregation: ParentEvidenceAggregation) -> None:
    object_id = id(aggregation)
    aggregation.__pydantic_private__["_issued_by_runtime_boundary"] = True
    _PARENT_AGGREGATION_OBJECT_IDS.add(object_id)
    _PARENT_AGGREGATION_FINGERPRINTS[object_id] = _model_fingerprint(aggregation)
    _PARENT_AGGREGATION_FINALIZERS[object_id] = finalize(
        aggregation,
        _discard_parent_evidence_aggregation_object_id,
        object_id,
    )


def _discard_parent_evidence_aggregation_object_id(object_id: int) -> None:
    _PARENT_AGGREGATION_OBJECT_IDS.discard(object_id)
    _PARENT_AGGREGATION_FINGERPRINTS.pop(object_id, None)
    _PARENT_AGGREGATION_FINALIZERS.pop(object_id, None)


def _require_runtime_parent_aggregation(
    aggregation: ParentEvidenceAggregation,
) -> ParentEvidenceAggregation:
    if (
        not isinstance(aggregation, ParentEvidenceAggregation)
        or not aggregation.is_runtime_boundary_issued
    ):
        raise ValueError("runtime-issued parent evidence aggregation required")
    return aggregation


def _model_fingerprint(model: BaseModel) -> object:
    return _freeze_for_fingerprint(
        model.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _freeze_for_fingerprint(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple(
            (str(key), _freeze_for_fingerprint(item))
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        )
    if isinstance(value, tuple | list):
        return tuple(_freeze_for_fingerprint(item) for item in value)
    return value


__all__ = [
    "ChildEvidenceEnvelope",
    "ChildEvidenceStatus",
    "CustomChildEvidenceSchema",
    "DelegatedEvidenceRequirement",
    "DelegatedRequirementMatch",
    "EvidenceBoundaryLedgerRef",
    "EvidenceDelegationMode",
    "EvidenceProvenance",
    "ExecutionBoundaryIdentity",
    "OPENMAGI_RUNTIME_ENVELOPE_ISSUER",
    "ParentEvidenceAggregation",
    "ParentEvidenceState",
    "PolicySnapshotCompatibility",
    "PropagatedEvidenceRecord",
    "PublicChildAggregateReport",
    "PublicChildEvidenceReport",
    "REQUIRED_SUBAGENT_EVIDENCE_WARNINGS",
    "aggregate_child_evidence",
    "match_delegated_requirement",
    "natural_language_summary_as_evidence",
    "public_child_aggregate_report",
]
