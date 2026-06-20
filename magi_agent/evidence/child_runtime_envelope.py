from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self
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

from magi_agent.evidence.runtime_issuance import (
    RuntimeIssueAuthority,
    require_runtime_issue_authority,
)
from magi_agent.ops.authority import FalseOnlyAuthorityModel
from magi_agent.transport.tool_preview import sanitize_tool_preview

from .subagent import (
    OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
    DelegatedEvidenceRequirement,
    EvidenceBoundaryLedgerRef,
    ExecutionBoundaryIdentity,
    PolicySnapshotCompatibility,
)
from .types import EvidenceAgentRole, _freeze_mapping, _serialize_mapping, _validate_strict_bool


ChildRuntimeEnvelopeMode = Literal["return", "background", "blocked"]
ChildRuntimeEnvelopeStatus = Literal["accepted", "blocked"]
ChildRuntimeWorkspacePolicy = Literal["trusted", "isolated", "git_worktree"]
ChildRuntimeDeliveryMode = Literal["return", "background"]
ChildRuntimeCompletionEvidence = Literal["tool_call", "files", "artifact", "text", "none"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="never",
    hide_input_in_errors=True,
)
_UNSAFE_PATH_RE = re.compile(
    r"(?:"
    r"~[\\/][^,\s\"'{}\]\)]+|"
    r"(?<![A-Za-z0-9:/])/(?:[^/,\s\"'{}\]\)]+)(?:/[^,\s\"'{}\]\)]+)*|"
    r"[A-Za-z]:[\\/][^,\s\"'{}\]\)]+|"
    r"\\\\[^,\s\"'{}\]\)]+|"
    r"pvc-[A-Za-z0-9-]+"
    r")",
    re.IGNORECASE,
)
_RAW_TRANSCRIPT_RE = re.compile(r"raw\s+child\s+transcript|raw\s+transcript", re.IGNORECASE)
_SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:api_key|authorization|cookie|credentials?|password|passphrase|"
    r"private_key|client_secret|service_role|service_role_key|secret|secret_key|"
    r"token|access_token|auth_token|bearer_token|refresh_token|session_token)(?:_|$)",
    re.IGNORECASE,
)
_RUNTIME_ISSUED_ENVELOPE_OBJECT_IDS: set[int] = set()
_RUNTIME_ISSUED_ENVELOPE_FINGERPRINTS: dict[int, object] = {}
_RUNTIME_ISSUED_ENVELOPE_FINALIZERS: dict[int, object] = {}


class ChildRuntimeEnvelopeAuthorityFlags(FalseOnlyAuthorityModel):
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    child_execution_attached: Literal[False] = Field(
        default=False,
        alias="childExecutionAttached",
    )
    tool_host_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    mission_store_written: Literal[False] = Field(default=False, alias="missionStoreWritten")
    background_runtime_attached: Literal[False] = Field(
        default=False,
        alias="backgroundRuntimeAttached",
    )
    memory_provider_called: Literal[False] = Field(default=False, alias="memoryProviderCalled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")


class ChildRuntimeTaskMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    task_id: str = Field(alias="taskId")
    persona: str
    role: EvidenceAgentRole
    spawn_depth: int = Field(alias="spawnDepth")
    deliver: ChildRuntimeDeliveryMode
    prompt_ref: str = Field(alias="promptRef")

    @model_validator(mode="after")
    def _validate_task_metadata(self) -> Self:
        for value in (self.task_id, self.persona, self.prompt_ref):
            _validate_public_ref(value, "task metadata")
        if self.spawn_depth <= 0:
            raise ValueError("child runtime task spawnDepth must be positive")
        return self


class ChildRuntimePolicySnapshotMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    parent_policy_snapshot_id: str = Field(alias="parentPolicySnapshotId")
    child_policy_snapshot_id: str = Field(alias="childPolicySnapshotId")
    task_local_policy_compatibility_refs: tuple[PolicySnapshotCompatibility, ...] = Field(
        default=(),
        alias="taskLocalPolicyCompatibilityRefs",
    )
    allowed_tool_names: tuple[str, ...] = Field(default=(), alias="allowedToolNames")
    permission_refs: tuple[str, ...] = Field(default=(), alias="permissionRefs")
    callback_hook_refs: tuple[str, ...] = Field(default=(), alias="callbackHookRefs")

    @field_validator("task_local_policy_compatibility_refs")
    @classmethod
    def _revalidate_compatibility_refs(
        cls,
        value: tuple[PolicySnapshotCompatibility, ...],
    ) -> tuple[PolicySnapshotCompatibility, ...]:
        return tuple(
            PolicySnapshotCompatibility.model_validate(item.model_dump(by_alias=True))
            for item in value
        )

    @model_validator(mode="after")
    def _validate_policy_refs(self) -> Self:
        for value in (
            self.parent_policy_snapshot_id,
            self.child_policy_snapshot_id,
            *self.allowed_tool_names,
            *self.permission_refs,
            *self.callback_hook_refs,
        ):
            _validate_public_ref(value, "policy snapshot metadata")
        if len(set(self.allowed_tool_names)) != len(self.allowed_tool_names):
            raise ValueError("allowedToolNames must not contain duplicates")
        return self


class ChildRuntimeWorkspaceIsolationMetadata(FalseOnlyAuthorityModel):
    workspace_policy: ChildRuntimeWorkspacePolicy = Field(alias="workspacePolicy")
    isolation_ref: str = Field(alias="isolationRef")
    parent_workspace_ref: str = Field(alias="parentWorkspaceRef")
    child_workspace_ref: str = Field(alias="childWorkspaceRef")
    descriptive_only: Literal[True] = Field(alias="descriptiveOnly")
    adoption_attached: Literal[False] = Field(default=False, alias="adoptionAttached")
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    private_notes: tuple[str, ...] = Field(default=(), alias="privateNotes")

    @model_validator(mode="after")
    def _validate_workspace_refs(self) -> Self:
        for value in (self.isolation_ref, self.parent_workspace_ref, self.child_workspace_ref):
            _validate_public_ref(value, "workspace isolation metadata")
        for note in self.private_notes:
            if not note.strip():
                raise ValueError("workspace private notes must be non-empty")
        return self


class ChildRuntimeCompletionContractMetadata(FalseOnlyAuthorityModel):
    required_evidence: ChildRuntimeCompletionEvidence = Field(alias="requiredEvidence")
    required_files: tuple[str, ...] = Field(default=(), alias="requiredFiles")
    require_non_empty_result: bool = Field(alias="requireNonEmptyResult")
    summary_is_evidence: Literal[False] = Field(alias="summaryIsEvidence")
    accepted_evidence_metadata_only: Literal[True] = Field(alias="acceptedEvidenceMetadataOnly")

    @field_validator("require_non_empty_result", mode="before")
    @classmethod
    def _validate_require_non_empty_result(cls, value: object) -> object:
        return _validate_strict_bool(value, "requireNonEmptyResult")

    @model_validator(mode="after")
    def _validate_completion_contract(self) -> Self:
        for value in self.required_files:
            _validate_relative_ref(value, "requiredFiles")
        return self


class ChildRuntimeADKPrimitiveOwnershipMetadata(FalseOnlyAuthorityModel):
    agent_owner: Literal["adk_future_agent"] = Field(alias="agentOwner")
    runner_owner: Literal["adk_future_runner"] = Field(alias="runnerOwner")
    event_owner: Literal["adk_event_bridge"] = Field(alias="eventOwner")
    tool_owner: Literal["adk_function_tool_future"] = Field(alias="toolOwner")
    callback_owner: Literal["adk_callbacks_future"] = Field(alias="callbackOwner")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    child_execution_attached: Literal[False] = Field(
        default=False,
        alias="childExecutionAttached",
    )
    allowed_tool_names: tuple[str, ...] = Field(default=(), alias="allowedToolNames")
    callback_hook_refs: tuple[str, ...] = Field(default=(), alias="callbackHookRefs")

    @model_validator(mode="after")
    def _validate_ownership_metadata(self) -> Self:
        for value in (*self.allowed_tool_names, *self.callback_hook_refs):
            _validate_public_ref(value, "ADK primitive ownership metadata")
        return self


class ChildRuntimeEnvelope(BaseModel):
    model_config = _MODEL_CONFIG

    _issued_by_runtime_boundary: bool = PrivateAttr(default=False)

    issuer: Literal["openmagi_runtime_boundary"]
    mode: ChildRuntimeEnvelopeMode
    status: ChildRuntimeEnvelopeStatus
    parent_boundary: ExecutionBoundaryIdentity = Field(alias="parentBoundary")
    child_boundary: ExecutionBoundaryIdentity = Field(alias="childBoundary")
    task: ChildRuntimeTaskMetadata
    policy_snapshot: ChildRuntimePolicySnapshotMetadata = Field(alias="policySnapshot")
    ledger_ref: EvidenceBoundaryLedgerRef = Field(alias="ledgerRef")
    delegated_evidence_requirements: tuple[DelegatedEvidenceRequirement, ...] = Field(
        default=(),
        alias="delegatedEvidenceRequirements",
    )
    workspace_isolation: ChildRuntimeWorkspaceIsolationMetadata = Field(
        alias="workspaceIsolation",
    )
    completion_contract: ChildRuntimeCompletionContractMetadata = Field(
        alias="completionContract",
    )
    audit_event_refs: tuple[str, ...] = Field(alias="auditEventRefs")
    adk_primitive_ownership: ChildRuntimeADKPrimitiveOwnershipMetadata = Field(
        alias="adkPrimitiveOwnership",
    )
    authority_flags: ChildRuntimeEnvelopeAuthorityFlags = Field(alias="authorityFlags")
    raw_transcript_ref: str | None = Field(default=None, alias="rawTranscriptRef")
    private_metadata: Mapping[str, object] = Field(default_factory=dict, alias="privateMetadata")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        raise TypeError("model_construct is disabled for child runtime envelopes")

    @classmethod
    def issue_runtime_envelope(
        cls,
        *,
        runtime_authority: RuntimeIssueAuthority | None = None,
        **payload: object,
    ) -> Self:
        require_runtime_issue_authority(
            runtime_authority,
            scope="child_runtime_envelope",
        )
        envelope = cls.model_validate(payload)
        _mark_child_runtime_envelope_issued(envelope)
        return envelope

    @property
    def is_runtime_boundary_issued(self) -> bool:
        object_id = id(self)
        return (
            bool(self.__pydantic_private__.get("_issued_by_runtime_boundary"))
            and object_id in _RUNTIME_ISSUED_ENVELOPE_OBJECT_IDS
            and _RUNTIME_ISSUED_ENVELOPE_FINGERPRINTS.get(object_id)
            == _model_fingerprint(self)
        )

    @field_validator("issuer", mode="before")
    @classmethod
    def _validate_issuer(cls, value: object) -> object:
        if value != OPENMAGI_RUNTIME_ENVELOPE_ISSUER:
            raise ValueError("child runtime envelopes must be runtime-issued")
        return value

    @field_validator("parent_boundary")
    @classmethod
    def _revalidate_parent_boundary(cls, value: ExecutionBoundaryIdentity) -> ExecutionBoundaryIdentity:
        return ExecutionBoundaryIdentity.model_validate(value.model_dump(by_alias=True))

    @field_validator("child_boundary")
    @classmethod
    def _revalidate_child_boundary(cls, value: ExecutionBoundaryIdentity) -> ExecutionBoundaryIdentity:
        return ExecutionBoundaryIdentity.model_validate(value.model_dump(by_alias=True))

    @field_validator("ledger_ref")
    @classmethod
    def _revalidate_ledger_ref(cls, value: EvidenceBoundaryLedgerRef) -> EvidenceBoundaryLedgerRef:
        return EvidenceBoundaryLedgerRef.model_validate(value.model_dump(by_alias=True))

    @field_validator("delegated_evidence_requirements")
    @classmethod
    def _revalidate_requirements(
        cls,
        value: tuple[DelegatedEvidenceRequirement, ...],
    ) -> tuple[DelegatedEvidenceRequirement, ...]:
        return tuple(
            DelegatedEvidenceRequirement.model_validate(item.model_dump(by_alias=True))
            for item in value
        )

    @field_validator("audit_event_refs")
    @classmethod
    def _validate_audit_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("auditEventRefs must contain only non-empty refs")
        if len(set(value)) != len(value):
            raise ValueError("auditEventRefs must not contain duplicates")
        for item in value:
            _validate_public_ref(item, "auditEventRefs")
        return value

    @field_validator("raw_transcript_ref")
    @classmethod
    def _validate_raw_transcript_ref(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_public_ref(value, "rawTranscriptRef")
        return value

    @field_validator("private_metadata")
    @classmethod
    def _freeze_private_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _freeze_mapping(value, "privateMetadata")

    @field_serializer("private_metadata")
    def _serialize_private_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return _serialize_mapping(value) or {}

    @model_validator(mode="after")
    def _validate_envelope_contract(self) -> Self:
        if self.parent_boundary.run_on != "main":
            raise ValueError("child runtime envelope parentBoundary must be main")
        if self.child_boundary.run_on != "child":
            raise ValueError("child runtime envelope childBoundary must be child")
        if self.child_boundary.parent_execution_id != self.parent_boundary.execution_id:
            raise ValueError("child parentExecutionId must match parent executionId")
        if self.child_boundary.task_id != self.task.task_id:
            raise ValueError("child taskId must match task metadata")
        if self.child_boundary.spawn_depth != self.task.spawn_depth:
            raise ValueError("child spawnDepth must match task metadata")
        if self.child_boundary.agent_role != self.task.role:
            raise ValueError("child agentRole must match task role")
        if self.mode == "background" and self.task.deliver != "background":
            raise ValueError("background child runtime envelope requires background delivery")
        if self.mode == "return" and self.task.deliver != "return":
            raise ValueError("return child runtime envelope requires return delivery")
        if self.mode == "blocked" and self.status != "blocked":
            raise ValueError("blocked child runtime envelope requires blocked status")
        if self.mode == "background" and not self.audit_event_refs:
            raise ValueError("background child runtime envelope requires auditEventRefs")

        _validate_ledger_matches_child(self.ledger_ref, self.child_boundary)
        _validate_policy_snapshot_matches_boundaries(
            self.policy_snapshot,
            self.parent_boundary,
            self.child_boundary,
            self.task.task_id,
        )
        return self


class PublicChildRuntimeEnvelopeProjection(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    issuer: Literal["openmagi_runtime_boundary"]
    mode: ChildRuntimeEnvelopeMode
    status: ChildRuntimeEnvelopeStatus
    parent_execution_id: str = Field(alias="parentExecutionId")
    child_execution_id: str = Field(alias="childExecutionId")
    task_id: str = Field(alias="taskId")
    persona: str
    role: EvidenceAgentRole
    spawn_depth: int = Field(alias="spawnDepth")
    policy_snapshot: Mapping[str, object] = Field(alias="policySnapshot")
    ledger_ref: Mapping[str, object] = Field(alias="ledgerRef")
    delegated_evidence_requirements: tuple[Mapping[str, object], ...] = Field(
        alias="delegatedEvidenceRequirements",
    )
    workspace_isolation: Mapping[str, object] = Field(alias="workspaceIsolation")
    completion_contract: Mapping[str, object] = Field(alias="completionContract")
    audit_event_refs: tuple[str, ...] = Field(alias="auditEventRefs")
    adk_primitive_ownership: Mapping[str, object] = Field(alias="adkPrimitiveOwnership")
    authority_flags: ChildRuntimeEnvelopeAuthorityFlags = Field(alias="authorityFlags")

    @field_serializer(
        "policy_snapshot",
        "ledger_ref",
        "workspace_isolation",
        "completion_contract",
        "adk_primitive_ownership",
    )
    def _serialize_mapping_fields(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


class ChildRuntimeEnvelopeFixtureCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    mode: ChildRuntimeEnvelopeMode
    expected_public_status: ChildRuntimeEnvelopeStatus = Field(alias="expectedPublicStatus")
    workspace_policy: ChildRuntimeWorkspacePolicy | None = Field(
        default=None,
        alias="workspacePolicy",
    )
    child_policy_snapshot_id: str | None = Field(default=None, alias="childPolicySnapshotId")
    compatibility_ref: str | None = Field(default=None, alias="compatibilityRef")

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        _validate_public_ref(self.case_id, "caseId")
        if self.workspace_policy is not None and self.workspace_policy != "git_worktree":
            raise ValueError("workspace fixture case only records explicit isolated child policy")
        if self.child_policy_snapshot_id is not None:
            _validate_public_ref(self.child_policy_snapshot_id, "childPolicySnapshotId")
            if not self.compatibility_ref:
                raise ValueError("task-local policy fixture case requires compatibilityRef")
        if self.compatibility_ref is not None:
            _validate_public_ref(self.compatibility_ref, "compatibilityRef")
        return self


class ChildRuntimeEnvelopeFixture(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    attachment_flags: ChildRuntimeEnvelopeAuthorityFlags = Field(alias="attachmentFlags")
    cases: tuple[ChildRuntimeEnvelopeFixtureCase, ...]

    @property
    def case_order(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)

    @property
    def by_mode(self) -> dict[str, int]:
        return dict(Counter(case.mode for case in self.cases))

    @property
    def no_live_execution(self) -> bool:
        return set(self.attachment_flags.model_dump(by_alias=True).values()) == {False}

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        _validate_public_ref(self.fixture_id, "fixtureId")
        if not self.cases:
            raise ValueError("child runtime envelope fixture requires cases")
        if len(set(self.case_order)) != len(self.case_order):
            raise ValueError("child runtime envelope fixture case ids must be unique")
        required = {
            "return_child_runtime_envelope",
            "background_child_runtime_envelope",
            "blocked_child_runtime_envelope",
            "workspace_isolated_child_runtime_envelope",
            "task_local_policy_snapshot_child_runtime_envelope",
        }
        if not required.issubset(set(self.case_order)):
            raise ValueError("child runtime envelope fixture is missing required cases")
        return self


def project_child_runtime_envelope(
    envelope: ChildRuntimeEnvelope,
) -> PublicChildRuntimeEnvelopeProjection:
    if not envelope.is_runtime_boundary_issued:
        raise ValueError("child runtime envelope must be runtime-issued")
    parsed = envelope
    return PublicChildRuntimeEnvelopeProjection(
        issuer=parsed.issuer,
        mode=parsed.mode,
        status=parsed.status,
        parentExecutionId=_sanitize_public_identifier(parsed.parent_boundary.execution_id),
        childExecutionId=_sanitize_public_identifier(parsed.child_boundary.execution_id),
        taskId=_sanitize_public_identifier(parsed.task.task_id),
        persona=_sanitize_public_text(parsed.task.persona),
        role=parsed.task.role,
        spawnDepth=parsed.task.spawn_depth,
        policySnapshot=_public_policy_snapshot(parsed.policy_snapshot),
        ledgerRef=_public_ledger_ref(parsed.ledger_ref),
        delegatedEvidenceRequirements=tuple(
            _sanitize_public_mapping(
                requirement.model_dump(by_alias=True, mode="python", warnings=False)
            )
            for requirement in parsed.delegated_evidence_requirements
        ),
        workspaceIsolation=_public_workspace_isolation(parsed.workspace_isolation),
        completionContract=_sanitize_public_mapping(
            parsed.completion_contract.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            )
        ),
        auditEventRefs=tuple(
            _sanitize_public_identifier(ref) for ref in parsed.audit_event_refs
        ),
        adkPrimitiveOwnership=_public_adk_ownership(parsed.adk_primitive_ownership),
        authorityFlags=parsed.authority_flags,
    )


def runtime_envelope_satisfies_delegated_evidence_metadata(value: object) -> bool:
    return isinstance(value, ChildRuntimeEnvelope) and value.is_runtime_boundary_issued


def load_child_runtime_envelope_fixture(
    filename: str,
    *,
    fixture_root: Path,
) -> ChildRuntimeEnvelopeFixture:
    path = fixture_root / filename
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ChildRuntimeEnvelopeFixture.model_validate(payload)


def _public_policy_snapshot(
    policy_snapshot: ChildRuntimePolicySnapshotMetadata,
) -> dict[str, object]:
    return _sanitize_public_mapping(
        {
            "parentPolicySnapshotId": policy_snapshot.parent_policy_snapshot_id,
            "childPolicySnapshotId": policy_snapshot.child_policy_snapshot_id,
            "taskLocalPolicyCompatibilityRefs": tuple(
                item.model_dump(by_alias=True, mode="python", warnings=False)
                for item in policy_snapshot.task_local_policy_compatibility_refs
            ),
            "allowedToolNames": policy_snapshot.allowed_tool_names,
            "permissionRefs": policy_snapshot.permission_refs,
            "callbackHookRefs": policy_snapshot.callback_hook_refs,
        }
    )


def _public_ledger_ref(ledger_ref: EvidenceBoundaryLedgerRef) -> dict[str, object]:
    return {
        "ledgerId": _sanitize_public_identifier(ledger_ref.ledger_id),
        "executionId": _sanitize_public_identifier(ledger_ref.execution_id),
        "agentId": _sanitize_public_identifier(ledger_ref.agent_id),
        "parentExecutionId": (
            None
            if ledger_ref.parent_execution_id is None
            else _sanitize_public_identifier(ledger_ref.parent_execution_id)
        ),
        "taskId": (
            None
            if ledger_ref.task_id is None
            else _sanitize_public_identifier(ledger_ref.task_id)
        ),
        "policySnapshotId": _sanitize_public_identifier(ledger_ref.policy_snapshot_id),
        "childLedgerRefs": tuple(
            _sanitize_public_identifier(ref) for ref in ledger_ref.child_ledger_refs
        ),
    }


def _public_workspace_isolation(
    workspace_isolation: ChildRuntimeWorkspaceIsolationMetadata,
) -> dict[str, object]:
    return _sanitize_public_mapping(
        {
            "workspacePolicy": workspace_isolation.workspace_policy,
            "isolationRef": workspace_isolation.isolation_ref,
            "parentWorkspaceRef": workspace_isolation.parent_workspace_ref,
            "childWorkspaceRef": workspace_isolation.child_workspace_ref,
            "descriptiveOnly": workspace_isolation.descriptive_only,
            "adoptionAttached": workspace_isolation.adoption_attached,
            "workspaceMutated": workspace_isolation.workspace_mutated,
        }
    )


def _public_adk_ownership(
    ownership: ChildRuntimeADKPrimitiveOwnershipMetadata,
) -> dict[str, object]:
    return _sanitize_public_mapping(
        {
            "agentOwner": ownership.agent_owner,
            "runnerOwner": ownership.runner_owner,
            "eventOwner": ownership.event_owner,
            "toolOwner": ownership.tool_owner,
            "callbackOwner": ownership.callback_owner,
            "runnerAttached": ownership.runner_attached,
            "childExecutionAttached": ownership.child_execution_attached,
            "allowedToolNames": ownership.allowed_tool_names,
            "callbackHookRefs": ownership.callback_hook_refs,
        }
    )


def _sanitize_public_mapping(value: Mapping[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, item in value.items():
        if _SECRET_KEY_RE.search(key) or _RAW_TRANSCRIPT_RE.search(key):
            continue
        out[key] = _sanitize_public_value(item)
    return out


def _sanitize_public_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _sanitize_public_mapping(value)
    if isinstance(value, tuple | list):
        return tuple(_sanitize_public_value(item) for item in value)
    if isinstance(value, str):
        redacted = sanitize_tool_preview(value)
        redacted = _UNSAFE_PATH_RE.sub("[redacted-path]", redacted)
        redacted = _RAW_TRANSCRIPT_RE.sub("[redacted-transcript]", redacted)
        return redacted
    return value


def _sanitize_public_identifier(value: str) -> str:
    sanitized = _sanitize_public_value(value)
    if not isinstance(sanitized, str) or sanitized != value:
        return "[redacted]"
    return sanitized


def _sanitize_public_text(value: str) -> str:
    sanitized = _sanitize_public_value(value)
    if not isinstance(sanitized, str):
        return "[redacted]"
    return sanitized


def _validate_ledger_matches_child(
    ledger_ref: EvidenceBoundaryLedgerRef,
    child_boundary: ExecutionBoundaryIdentity,
) -> None:
    if ledger_ref.execution_id != child_boundary.execution_id:
        raise ValueError("ledgerRef executionId must match child boundary")
    if ledger_ref.agent_id != child_boundary.agent_id:
        raise ValueError("ledgerRef agentId must match child boundary")
    if ledger_ref.parent_execution_id != child_boundary.parent_execution_id:
        raise ValueError("ledgerRef parentExecutionId must match child boundary")
    if ledger_ref.task_id != child_boundary.task_id:
        raise ValueError("ledgerRef taskId must match child boundary")
    if ledger_ref.policy_snapshot_id != child_boundary.policy_snapshot_id:
        raise ValueError("ledgerRef policySnapshotId must match child boundary")


def _validate_policy_snapshot_matches_boundaries(
    policy_snapshot: ChildRuntimePolicySnapshotMetadata,
    parent_boundary: ExecutionBoundaryIdentity,
    child_boundary: ExecutionBoundaryIdentity,
    task_id: str,
) -> None:
    if policy_snapshot.parent_policy_snapshot_id != parent_boundary.policy_snapshot_id:
        raise ValueError("parentPolicySnapshotId must match parent boundary")
    if policy_snapshot.child_policy_snapshot_id != child_boundary.policy_snapshot_id:
        raise ValueError("childPolicySnapshotId must match child boundary")
    if child_boundary.policy_snapshot_id == parent_boundary.policy_snapshot_id:
        return

    for ref in policy_snapshot.task_local_policy_compatibility_refs:
        if (
            ref.parent_policy_snapshot_id == parent_boundary.policy_snapshot_id
            and ref.child_policy_snapshot_id == child_boundary.policy_snapshot_id
            and ref.child_execution_id == child_boundary.execution_id
            and ref.task_id == task_id
        ):
            return
    raise ValueError(
        "task-local child policy snapshot requires matching compatibility ref",
    )


def _validate_public_ref(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if _RAW_TRANSCRIPT_RE.search(value):
        raise ValueError(f"{field_name} must not contain raw transcript text")


def _validate_relative_ref(value: str, field_name: str) -> None:
    _validate_public_ref(value, field_name)
    if value.startswith("/") or ".." in Path(value).parts:
        raise ValueError(f"{field_name} must use workspace-relative safe refs")


def _mark_child_runtime_envelope_issued(envelope: ChildRuntimeEnvelope) -> None:
    object_id = id(envelope)
    envelope.__pydantic_private__["_issued_by_runtime_boundary"] = True
    _RUNTIME_ISSUED_ENVELOPE_OBJECT_IDS.add(object_id)
    _RUNTIME_ISSUED_ENVELOPE_FINGERPRINTS[object_id] = _model_fingerprint(envelope)
    _RUNTIME_ISSUED_ENVELOPE_FINALIZERS[object_id] = finalize(
        envelope,
        _discard_child_runtime_envelope_object_id,
        object_id,
    )


def _discard_child_runtime_envelope_object_id(object_id: int) -> None:
    _RUNTIME_ISSUED_ENVELOPE_OBJECT_IDS.discard(object_id)
    _RUNTIME_ISSUED_ENVELOPE_FINGERPRINTS.pop(object_id, None)
    _RUNTIME_ISSUED_ENVELOPE_FINALIZERS.pop(object_id, None)


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
    "ChildRuntimeADKPrimitiveOwnershipMetadata",
    "ChildRuntimeCompletionContractMetadata",
    "ChildRuntimeDeliveryMode",
    "ChildRuntimeEnvelope",
    "ChildRuntimeEnvelopeAuthorityFlags",
    "ChildRuntimeEnvelopeFixture",
    "ChildRuntimeEnvelopeFixtureCase",
    "ChildRuntimeEnvelopeMode",
    "ChildRuntimeEnvelopeStatus",
    "ChildRuntimePolicySnapshotMetadata",
    "ChildRuntimeTaskMetadata",
    "ChildRuntimeWorkspaceIsolationMetadata",
    "ChildRuntimeWorkspacePolicy",
    "PublicChildRuntimeEnvelopeProjection",
    "load_child_runtime_envelope_fixture",
    "project_child_runtime_envelope",
    "runtime_envelope_satisfies_delegated_evidence_metadata",
]
