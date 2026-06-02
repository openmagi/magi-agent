from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from openmagi_core_agent.evidence.subagent import (
    ChildEvidenceEnvelope,
    DelegatedEvidenceRequirement,
    DelegatedRequirementMatch,
    EvidenceProvenance,
    ExecutionBoundaryIdentity,
    PolicySnapshotCompatibility,
    PropagatedEvidenceRecord,
    _parent_aggregation_components,
    natural_language_summary_as_evidence,
)
from openmagi_core_agent.evidence.types import EvidenceContractFailure, EvidenceRecord
from openmagi_core_agent.transport.tool_preview import sanitize_tool_preview


DelegatedWorkflowCategory = Literal[
    "delegated_research_child_source_pass",
    "delegated_coding_child_verification_pass",
    "child_blocking_failure_propagates",
    "natural_language_summary_rejected",
    "task_local_policy_snapshot_compatible",
    "aggregate_required_parent_scope",
]
DelegatedParentState = Literal["pass", "blocked", "failed", "audit"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PRODUCTION_PATH_RE = re.compile(
    r"(?:/data/bots|/workspace|/var/lib/kubelet)(?:/[^\s\"',}]+)*",
    re.IGNORECASE,
)
_FORBIDDEN_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_childsecret",
    "sk-child-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "raw child transcript",
    "hidden reasoning",
    "pythonResponseAuthority",
)
_FORBIDDEN_PUBLIC_TOKENS_NORMALIZED = tuple(
    token.casefold() for token in _FORBIDDEN_PUBLIC_TOKENS
)
_SECRET_LIKE_KEY_RE = re.compile(
    r"(?:^|_)(?:api_key|authorization|cookie|credentials?|password|passphrase|"
    r"private_key|client_secret|service_role|service_role_key|secret|secret_key|"
    r"token|access_token|auth_token|bearer_token|refresh_token|session_token)(?:_|$)",
    re.IGNORECASE,
)
_FORBIDDEN_RAW_KEY_TOKENS = frozenset(
    {
        "adk_runner_invoked",
        "adk_runner_attached",
        "agent_memory_imported",
        "agent_memory_provider_called",
        "canary_attached",
        "canary_traffic_attached",
        "child_agent",
        "child_execution_attached",
        "child_runner",
        "code_executed",
        "evidence_block_enabled",
        "file_mutated",
        "hipocampus_qmd_live_called",
        "live_tool",
        "live_tool_dispatched",
        "memory_provider",
        "memory_provider_called",
        "production_authority",
        "production_storage_written",
        "route_attached",
        "route_or_api_attached",
        "shell_executed",
        "shell_or_code_executed",
        "telegram_attached",
        "tool_host_dispatched",
        "tool_dispatched_live",
        "traffic_attached",
        "workspace_mutated",
        "workspace_written",
    }
)
_REQUIRED_CATEGORIES = set(DelegatedWorkflowCategory.__args__)  # type: ignore[attr-defined]


class DelegatedWorkflowAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    child_execution_attached: Literal[False] = Field(
        default=False,
        alias="childExecutionAttached",
    )
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    memory_provider_called: Literal[False] = Field(
        default=False,
        alias="memoryProviderCalled",
    )
    agent_memory_imported: Literal[False] = Field(default=False, alias="agentMemoryImported")
    hipocampus_qmd_live_called: Literal[False] = Field(
        default=False,
        alias="hipocampusQmdLiveCalled",
    )
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    canary_traffic_attached: Literal[False] = Field(
        default=False,
        alias="canaryTrafficAttached",
    )
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{name: False for name in cls.model_fields})

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_serializer(
        "adk_runner_invoked",
        "live_tool_dispatched",
        "child_execution_attached",
        "shell_or_code_executed",
        "workspace_mutated",
        "memory_provider_called",
        "agent_memory_imported",
        "hipocampus_qmd_live_called",
        "production_storage_written",
        "production_authority",
        "route_or_api_attached",
        "telegram_attached",
        "canary_traffic_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class DelegatedWorkflowRequirementExpectation(BaseModel):
    model_config = _MODEL_CONFIG

    requirement: DelegatedEvidenceRequirement
    expected_satisfied: bool = Field(alias="expectedSatisfied")
    expected_matched_types: tuple[str, ...] = Field(alias="expectedMatchedTypes")
    expected_reason_contains: str | None = Field(
        default=None,
        alias="expectedReasonContains",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_expectation(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value


class DelegatedWorkflowEvidenceCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: DelegatedWorkflowCategory
    summary_claimed_as_evidence: Literal[False] = Field(alias="summaryClaimedAsEvidence")
    natural_language_summary: str | None = Field(default=None, alias="naturalLanguageSummary")
    parent_boundary: ExecutionBoundaryIdentity = Field(alias="parentBoundary")
    child_envelopes: tuple[ChildEvidenceEnvelope, ...] = Field(alias="childEnvelopes")
    compatible_policy_snapshots: tuple[PolicySnapshotCompatibility, ...] = Field(
        default=(),
        alias="compatiblePolicySnapshots",
    )
    local_records: tuple[EvidenceRecord, ...] = Field(default=(), alias="localRecords")
    requirement_expectations: tuple[DelegatedWorkflowRequirementExpectation, ...] = Field(
        alias="requirementExpectations",
    )
    expected_parent_state: DelegatedParentState = Field(alias="expectedParentState")
    expected_propagated_types: tuple[str, ...] = Field(alias="expectedPropagatedTypes")
    expected_blocking_failure_count: int = Field(alias="expectedBlockingFailureCount", ge=0)
    expected_audit_failure_count: int = Field(alias="expectedAuditFailureCount", ge=0)
    attachment_flags: DelegatedWorkflowAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if self.parent_boundary.run_on != "main":
            raise ValueError("delegated workflow fixtures require a main parent boundary")
        if self.parent_boundary.spawn_depth != 0:
            raise ValueError("delegated workflow parent boundary must use spawnDepth=0")
        if self.summary_claimed_as_evidence is not False:
            raise ValueError("natural-language child summaries are never evidence")
        if self.category == "natural_language_summary_rejected":
            if not self.natural_language_summary:
                raise ValueError("summary rejection case requires naturalLanguageSummary")
            if self.child_envelopes:
                raise ValueError("summary rejection case must not include child evidence envelopes")
        for envelope in self.child_envelopes:
            _validate_child_envelope_scope(self.parent_boundary, envelope)
        _validate_public_surface(self)
        aggregation = _local_diagnostic_child_aggregation(
            self.parent_boundary,
            self.child_envelopes,
            compatible_policy_snapshots=self.compatible_policy_snapshots,
        )
        if aggregation.state != self.expected_parent_state:
            raise ValueError("expectedParentState does not match delegated aggregation")
        propagated_types = tuple(record.record.type for record in aggregation.propagated_evidence)
        if propagated_types != self.expected_propagated_types:
            raise ValueError("expectedPropagatedTypes does not match aggregation")
        if len(aggregation.blocking_child_failures) != self.expected_blocking_failure_count:
            raise ValueError("expectedBlockingFailureCount does not match aggregation")
        if len(aggregation.audit_child_failures) != self.expected_audit_failure_count:
            raise ValueError("expectedAuditFailureCount does not match aggregation")
        for expectation in self.requirement_expectations:
            match = _local_diagnostic_match_delegated_requirement(
                self.parent_boundary,
                expectation.requirement,
                local_records=self.local_records,
                child_aggregation=aggregation,
            )
            _validate_requirement_expectation(expectation, match)
        return self


class DelegatedWorkflowEvidenceFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["delegatedWorkflowEvidenceFixture.v1"] = Field(
        alias="schemaVersion",
    )
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    attachment_flags: DelegatedWorkflowAttachmentFlags = Field(alias="attachmentFlags")
    cases: tuple[DelegatedWorkflowEvidenceCase, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("delegated workflow caseIds must be unique")
        categories = {case.category for case in self.cases}
        if not _REQUIRED_CATEGORIES.issubset(categories):
            raise ValueError("delegated workflow fixture is missing required categories")
        return self


class DelegatedWorkflowEvidenceProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    attachment_flags: DelegatedWorkflowAttachmentFlags = Field(alias="attachmentFlags")
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_category: dict[str, int] = Field(alias="byCategory")
    by_parent_state: dict[str, int] = Field(alias="byParentState")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


class _LocalDiagnosticParentEvidenceAggregation(BaseModel):
    model_config = _MODEL_CONFIG

    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    parent_boundary: ExecutionBoundaryIdentity = Field(alias="parentBoundary")
    child_envelopes: tuple[ChildEvidenceEnvelope, ...] = Field(alias="childEnvelopes")
    propagated_evidence: tuple[PropagatedEvidenceRecord, ...] = Field(
        alias="propagatedEvidence",
    )
    state: DelegatedParentState
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


def load_delegated_workflow_evidence_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> DelegatedWorkflowEvidenceFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return DelegatedWorkflowEvidenceFixture.model_validate(payload)


def project_delegated_workflow_evidence_fixture(
    fixture: DelegatedWorkflowEvidenceFixture | Mapping[str, Any],
) -> DelegatedWorkflowEvidenceProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    case_snapshots: dict[str, dict[str, object]] = {}
    parent_states: list[str] = []
    for case in safe_fixture.cases:
        aggregation = _local_diagnostic_child_aggregation(
            case.parent_boundary,
            case.child_envelopes,
            compatible_policy_snapshots=case.compatible_policy_snapshots,
        )
        parent_states.append(aggregation.state)
        snapshot = _case_snapshot(case)
        _reject_unsafe_public_snapshot(snapshot)
        case_snapshots[case.case_id] = snapshot
    return DelegatedWorkflowEvidenceProjection(
        fixtureId=safe_fixture.fixture_id,
        attachmentFlags=safe_fixture.attachment_flags,
        noLiveExecution=True,
        caseOrder=tuple(case.case_id for case in safe_fixture.cases),
        byCategory=dict(Counter(case.category for case in safe_fixture.cases)),
        byParentState=dict(Counter(parent_states)),
        caseSnapshots=case_snapshots,
    )


def _validated_fixture_snapshot(
    fixture: DelegatedWorkflowEvidenceFixture | Mapping[str, Any],
) -> DelegatedWorkflowEvidenceFixture:
    if isinstance(fixture, DelegatedWorkflowEvidenceFixture):
        return DelegatedWorkflowEvidenceFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return DelegatedWorkflowEvidenceFixture.model_validate(fixture)


def _case_snapshot(case: DelegatedWorkflowEvidenceCase) -> dict[str, object]:
    aggregation = _local_diagnostic_child_aggregation(
        case.parent_boundary,
        case.child_envelopes,
        compatible_policy_snapshots=case.compatible_policy_snapshots,
    )
    requirement_matches = _requirement_match_snapshots(case, aggregation)
    natural_language_rejection = (
        natural_language_summary_as_evidence(case.natural_language_summary)
        if case.natural_language_summary is not None
        else None
    )
    snapshot: dict[str, object] = {
        "caseId": case.case_id,
        "category": case.category,
        "parentScope": {
            "agentRole": case.parent_boundary.agent_role,
            "runOn": case.parent_boundary.run_on,
            "spawnDepth": case.parent_boundary.spawn_depth,
        },
        "childScopes": tuple(_child_scope(envelope) for envelope in case.child_envelopes),
        "parentState": aggregation.state,
        "propagatedEvidenceTypes": tuple(
            record.record.type for record in aggregation.propagated_evidence
        ),
        "blockingFailureCount": len(aggregation.blocking_child_failures),
        "auditFailureCount": len(aggregation.audit_child_failures),
        "compatiblePolicySnapshotIds": tuple(
            item.child_policy_snapshot_id for item in case.compatible_policy_snapshots
        ),
        "requirementMatches": requirement_matches,
        "naturalLanguageSummaryAcceptedAsEvidence": False,
        "naturalLanguageRejectionReason": (
            natural_language_rejection.reason if natural_language_rejection is not None else None
        ),
        "trafficAttached": False,
        "executionAttached": False,
        "runnerAttached": False,
        "childExecutionAttached": False,
    }
    return snapshot


def _child_scope(envelope: ChildEvidenceEnvelope) -> dict[str, object]:
    return {
        "agentRole": envelope.boundary.agent_role,
        "runOn": envelope.boundary.run_on,
        "spawnDepth": envelope.boundary.spawn_depth,
        "taskId": envelope.boundary.task_id,
    }


def _requirement_match_snapshots(
    case: DelegatedWorkflowEvidenceCase,
    aggregation: _LocalDiagnosticParentEvidenceAggregation,
) -> dict[str, dict[str, object]]:
    snapshots: dict[str, dict[str, object]] = {}
    for expectation in case.requirement_expectations:
        match = _local_diagnostic_match_delegated_requirement(
            case.parent_boundary,
            expectation.requirement,
            local_records=case.local_records,
            child_aggregation=aggregation,
        )
        key = f"{expectation.requirement.type}:{expectation.requirement.delegation}"
        snapshots[key] = {
            "satisfied": match.satisfied,
            "matchedEvidenceTypes": tuple(
                record.record.type for record in match.matched_evidence
            ),
            "reason": match.reason,
        }
    return snapshots


def _local_diagnostic_child_aggregation(
    parent_boundary: ExecutionBoundaryIdentity,
    child_envelopes: tuple[ChildEvidenceEnvelope, ...],
    *,
    compatible_policy_snapshots: tuple[PolicySnapshotCompatibility, ...] = (),
) -> _LocalDiagnosticParentEvidenceAggregation:
    """Build deterministic fixture projections without minting runtime evidence."""
    parent = ExecutionBoundaryIdentity.model_validate(parent_boundary.model_dump(by_alias=True))
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
    return _LocalDiagnosticParentEvidenceAggregation(
        localDiagnostic=True,
        parentBoundary=parent,
        childEnvelopes=envelopes,
        propagatedEvidence=propagated,
        state=state,
        blockingChildFailures=blocking_failures,
        auditChildFailures=audit_failures,
        compatiblePolicySnapshots=compatibility,
    )


def _local_diagnostic_match_delegated_requirement(
    parent_boundary: ExecutionBoundaryIdentity,
    requirement: DelegatedEvidenceRequirement,
    *,
    local_records: tuple[EvidenceRecord, ...],
    child_aggregation: _LocalDiagnosticParentEvidenceAggregation | None = None,
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
        if _local_record_matches_requirement(
            record,
            parsed_requirement.type,
            parent.execution_id,
            parent.agent_id,
            parent.policy_snapshot_id,
            None,
            None,
        )
    )
    if child_aggregation is not None:
        if child_aggregation.parent_boundary.execution_id != parent.execution_id:
            return DelegatedRequirementMatch(
                requirement=parsed_requirement,
                satisfied=False,
                mode=parsed_requirement.delegation,
                reason="child aggregation parent boundary does not match requirement parent boundary",
            )
        if child_aggregation.parent_boundary.policy_snapshot_id != parent.policy_snapshot_id:
            return DelegatedRequirementMatch(
                requirement=parsed_requirement,
                satisfied=False,
                mode=parsed_requirement.delegation,
                reason="child aggregation policy snapshot does not match requirement parent boundary",
            )
        if child_aggregation.state in ("blocked", "failed"):
            return DelegatedRequirementMatch(
                requirement=parsed_requirement,
                satisfied=False,
                mode=parsed_requirement.delegation,
                reason=f"child aggregation state is {child_aggregation.state}",
            )
    child_matches = (
        tuple(
            propagated
            for propagated in child_aggregation.propagated_evidence
            if propagated.record.type == parsed_requirement.type
            and propagated.record.status == "ok"
        )
        if child_aggregation is not None
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


def _validate_requirement_expectation(
    expectation: DelegatedWorkflowRequirementExpectation,
    match: DelegatedRequirementMatch,
) -> None:
    if match.satisfied != expectation.expected_satisfied:
        raise ValueError("delegated requirement expectedSatisfied does not match projection")
    matched_types = tuple(record.record.type for record in match.matched_evidence)
    if matched_types != expectation.expected_matched_types:
        raise ValueError("delegated requirement expectedMatchedTypes does not match projection")
    if expectation.expected_reason_contains is not None:
        if match.reason is None or expectation.expected_reason_contains not in match.reason:
            raise ValueError("delegated requirement expectedReasonContains does not match")


def _local_record_matches_requirement(
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


def _validate_child_envelope_scope(
    parent: ExecutionBoundaryIdentity,
    envelope: ChildEvidenceEnvelope,
) -> None:
    if envelope.boundary.run_on != "child":
        raise ValueError("delegated workflow child envelopes require runOn=child")
    if envelope.boundary.spawn_depth <= parent.spawn_depth:
        raise ValueError("delegated workflow child spawnDepth must exceed parent")
    if envelope.boundary.parent_execution_id != parent.execution_id:
        raise ValueError("delegated workflow child parentExecutionId must match parent")


def _validate_public_surface(case: DelegatedWorkflowEvidenceCase) -> None:
    snapshot = case.model_dump(by_alias=True, mode="json", warnings=False)
    _reject_unsafe_public_snapshot(snapshot)
    for envelope in case.child_envelopes:
        for record in envelope.evidence_records:
            preview = (
                _PRODUCTION_PATH_RE.sub("[redacted-path]", sanitize_tool_preview(record.preview))
                if record.preview is not None
                else None
            )
            _reject_unsafe_public_snapshot({"preview": preview, "fields": record.fields})


def _reject_unsafe_public_snapshot(value: Mapping[str, object]) -> None:
    _reject_unsafe_public_value(value)


def _reject_unsafe_public_value(value: object) -> None:
    if isinstance(value, str):
        if _FORBIDDEN_PATH_RE.search(value):
            raise ValueError("delegated workflow public snapshot contains production paths")
        if _has_forbidden_public_token(value) or _has_secret_shaped_value(value):
            raise ValueError("delegated workflow public snapshot contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            _reject_unsafe_mapping_key(key)
            _reject_unsafe_public_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_public_value(item)
        return
    rendered = json.dumps(value, sort_keys=True)
    if _has_forbidden_public_token(rendered) or _has_secret_shaped_value(rendered):
        raise ValueError("delegated workflow public snapshot contains unsafe data")


def _resolve_fixture_path(path: str | Path, *, fixture_root: str | Path | None) -> Path:
    _reject_unsafe_path_text(str(path))
    candidate = Path(path)
    if fixture_root is None:
        resolved = candidate.resolve(strict=True)
        _reject_unsafe_path_text(str(resolved))
        return resolved
    _reject_unsafe_path_text(str(fixture_root))
    resolved_root = Path(fixture_root).resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_root))
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved_candidate = candidate.resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_candidate))
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("delegated workflow fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _FORBIDDEN_PATH_RE.search(path_text):
        raise ValueError("delegated workflow fixtures must be local and non-production")


def _reject_unsafe_raw_value(value: object) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if _FORBIDDEN_PATH_RE.search(value):
            raise ValueError("delegated workflow fixture contains unsafe path")
        if _has_forbidden_public_token(value) or _has_secret_shaped_value(value):
            raise ValueError("delegated workflow fixture contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            _reject_unsafe_mapping_key(key)
            normalized = _normalize_key(key)
            if nested_value is True and normalized in _FORBIDDEN_RAW_KEY_TOKENS:
                raise ValueError("delegated workflow fixture cannot claim live behavior")
            _reject_unsafe_raw_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_raw_value(item)


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("delegated workflow fixture values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("delegated workflow mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("delegated workflow fixture values must be JSON-compatible")


def _normalize_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    chars: list[str] = []
    previous_was_separator = False
    for char in value:
        if char.isalnum():
            chars.append(char.lower())
            previous_was_separator = False
        elif not previous_was_separator:
            chars.append("_")
            previous_was_separator = True
    return "".join(chars).strip("_")


def _reject_unsafe_mapping_key(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("delegated workflow mappings must use string keys")
    normalized = _normalize_key(value)
    if _has_forbidden_public_token(value) or _SECRET_LIKE_KEY_RE.search(
        f"_{normalized}_"
    ):
        raise ValueError("delegated workflow public snapshot contains unsafe data")
    if re.search(
        r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
        r"supabase://|s3://|gs://|postgres(?:ql)?://",
        value,
        re.IGNORECASE,
    ):
        raise ValueError("delegated workflow public snapshot contains production paths")


def _has_forbidden_public_token(value: str) -> bool:
    normalized = value.casefold()
    return any(token in normalized for token in _FORBIDDEN_PUBLIC_TOKENS_NORMALIZED)


def _has_secret_shaped_value(value: str) -> bool:
    redacted = sanitize_tool_preview(value)
    return "[redacted]" in redacted and redacted != value


__all__ = [
    "DelegatedWorkflowAttachmentFlags",
    "DelegatedWorkflowEvidenceCase",
    "DelegatedWorkflowEvidenceFixture",
    "DelegatedWorkflowEvidenceProjection",
    "DelegatedWorkflowRequirementExpectation",
    "load_delegated_workflow_evidence_fixture",
    "project_delegated_workflow_evidence_fixture",
]
