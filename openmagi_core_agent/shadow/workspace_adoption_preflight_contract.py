from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from openmagi_core_agent.transport.tool_preview import sanitize_tool_preview
from openmagi_core_agent.workspace.isolation import (
    ExternalSandboxImportMetadata,
    WorkspaceChangePreview,
    WorkspaceEvidenceMetadata,
    WorkspaceIsolationPolicy,
)


WorkspaceAdoptionPreflightCategory = Literal[
    "no_worktree_fallback",
    "adoption_preview",
    "spawn_worktree_apply_intent",
    "spawn_worktree_cherry_pick_intent",
    "spawn_worktree_reject_disposition",
    "spawn_worktree_noop_apply",
    "spawn_worktree_noop_cherry_pick",
    "dirty_parent_conflict",
    "spawn_worktree_cherry_pick_conflict",
    "rollback_active_mutation",
    "child_parent_evidence_distinction",
    "parent_verified_after_adoption",
    "external_sandbox_import",
    "sealed_path_mutation_denied",
    "workspace_escape_mutation_denied",
]
WorkspaceAdoptionPreflightDecision = Literal["metadata_only", "preview_only", "deny"]
WorkspaceAdoptionPreflightPathClassification = Literal[
    "metadata_only",
    "workspace_safe",
    "outside_workspace",
    "sealed_file",
    "protected_path",
    "external_sandbox",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_FORBIDDEN_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_workspacesecret",
    "sk-workspace-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "raw patch",
    "raw diff",
    "diff --git",
    "--- a/",
    "+++ b/",
    "@@ -",
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
        "canary_traffic_attached",
        "child_execution_attached",
        "code_executed",
        "evidence_block_enabled",
        "file_mutated",
        "git_command_executed",
        "live_adoption_attached",
        "live_tool_dispatched",
        "memory_provider_called",
        "patch_applied",
        "production_authority",
        "production_storage_written",
        "route_or_api_attached",
        "shell_or_code_executed",
        "telegram_attached",
        "tool_host_dispatched",
        "traffic_attached",
        "workspace_mutated",
    }
)
_REQUIRED_CATEGORIES = set(
    WorkspaceAdoptionPreflightCategory.__args__  # type: ignore[attr-defined]
)

SpawnWorktreeAction = Literal["preview", "apply", "reject", "cherry_pick"]
SpawnWorktreeMergeStrategy = Literal["copy", "cherry_pick"]
SpawnWorktreeDisposition = Literal[
    "previewed_metadata_only",
    "adoption_intent_metadata_only",
    "rejected_metadata_only",
    "noop_unapplied",
    "conflict_review_required",
]
SpawnWorktreeConflictKind = Literal["parent_dirty", "cherry_pick"]


class SpawnWorktreeRenameMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    from_path: str = Field(alias="from")
    to_path: str = Field(alias="to")

    @model_validator(mode="after")
    def _validate_rename_paths(self) -> Self:
        _reject_unsafe_relative_path(self.from_path)
        _reject_unsafe_relative_path(self.to_path)
        return self


class SpawnWorktreeOperationMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    action: SpawnWorktreeAction
    merge_strategy: SpawnWorktreeMergeStrategy | None = Field(
        default=None,
        alias="mergeStrategy",
    )
    spawn_ref: str = Field(alias="spawnRef")
    worktree_ref: str = Field(alias="worktreeRef")
    changed_files: tuple[str, ...] = Field(alias="changedFiles")
    created_files: tuple[str, ...] = Field(default=(), alias="createdFiles")
    modified_files: tuple[str, ...] = Field(default=(), alias="modifiedFiles")
    deleted_files: tuple[str, ...] = Field(default=(), alias="deletedFiles")
    renamed_files: tuple[SpawnWorktreeRenameMetadata, ...] = Field(
        default=(),
        alias="renamedFiles",
    )
    diff_ref: str = Field(alias="diffRef")
    diff_summary: str = Field(alias="diffSummary")
    truncated: bool = False
    applied: Literal[False] = False
    cleanup_executed: Literal[False] = Field(default=False, alias="cleanupExecuted")
    adoption_intent: bool = Field(default=False, alias="adoptionIntent")
    disposition: SpawnWorktreeDisposition
    adopted_commit_ref: str | None = Field(default=None, alias="adoptedCommitRef")

    @model_validator(mode="after")
    def _validate_operation(self) -> Self:
        for rel_path in (
            *self.changed_files,
            *self.created_files,
            *self.modified_files,
            *self.deleted_files,
        ):
            _reject_unsafe_relative_path(rel_path)
        for value in (
            self.spawn_ref,
            self.worktree_ref,
            self.diff_ref,
            self.diff_summary,
        ):
            if not value.strip():
                raise ValueError("worktree operation metadata fields must be non-empty")
            _validate_public_value(value)
        _validate_ref_prefix(self.spawn_ref, "spawn:")
        _validate_ref_prefix(self.worktree_ref, "spawn-worktree:")
        _validate_ref_prefix(self.diff_ref, "artifact:")
        if self.adopted_commit_ref is not None:
            _validate_ref_prefix(self.adopted_commit_ref, "commit:")

        if self.action == "preview":
            if self.merge_strategy is not None or self.adoption_intent:
                raise ValueError("preview metadata cannot claim adoption intent")
            if self.disposition != "previewed_metadata_only":
                raise ValueError("preview metadata requires previewed disposition")
        elif self.action == "reject":
            if self.merge_strategy is not None or self.adoption_intent:
                raise ValueError("reject metadata cannot claim adoption intent")
            if self.disposition != "rejected_metadata_only":
                raise ValueError("reject metadata requires rejected disposition")
        elif self.action == "apply":
            if self.merge_strategy != "copy" or not self.adoption_intent:
                raise ValueError("apply metadata requires copy adoption intent")
            if self.disposition not in {
                "adoption_intent_metadata_only",
                "noop_unapplied",
                "conflict_review_required",
            }:
                raise ValueError("apply metadata has invalid disposition")
        elif self.action == "cherry_pick":
            if self.merge_strategy != "cherry_pick" or not self.adoption_intent:
                raise ValueError("cherry-pick metadata requires cherry-pick adoption intent")
            if self.disposition not in {
                "adoption_intent_metadata_only",
                "noop_unapplied",
                "conflict_review_required",
            }:
                raise ValueError("cherry-pick metadata has invalid disposition")

        if self.disposition == "noop_unapplied" and (
            self.changed_files
            or self.created_files
            or self.modified_files
            or self.deleted_files
            or self.renamed_files
        ):
            raise ValueError("noop adoption metadata cannot include changed files")
        if self.disposition != "noop_unapplied" and self.action in {"apply", "cherry_pick"}:
            if not self.changed_files:
                raise ValueError("non-noop adoption metadata must record changed files")
        if self.renamed_files:
            rename_sources = {rename.from_path for rename in self.renamed_files}
            rename_targets = {rename.to_path for rename in self.renamed_files}
            if not rename_sources.issubset(self.deleted_files):
                raise ValueError("renamed source paths must also be deleted files")
            if not rename_targets.issubset(self.created_files):
                raise ValueError("renamed target paths must also be created files")
        return self


class SpawnWorktreeConflictResolverMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    source_tool: Literal["SpawnWorktreeApply"] = Field(alias="sourceTool")
    conflict_kind: SpawnWorktreeConflictKind = Field(alias="conflictKind")
    merge_strategy: SpawnWorktreeMergeStrategy = Field(alias="mergeStrategy")
    spawn_ref: str = Field(alias="spawnRef")
    changed_files: tuple[str, ...] = Field(alias="changedFiles")
    conflicted_files: tuple[str, ...] = Field(alias="conflictedFiles")
    adopted_commit_ref: str | None = Field(default=None, alias="adoptedCommitRef")

    @model_validator(mode="after")
    def _validate_metadata(self) -> Self:
        _validate_ref_prefix(self.spawn_ref, "spawn:")
        for rel_path in (*self.changed_files, *self.conflicted_files):
            _reject_unsafe_relative_path(rel_path)
        if self.adopted_commit_ref is not None:
            _validate_ref_prefix(self.adopted_commit_ref, "commit:")
        if self.conflict_kind == "cherry_pick" and self.merge_strategy != "cherry_pick":
            raise ValueError("cherry-pick conflict metadata requires cherry-pick strategy")
        if self.conflict_kind == "parent_dirty" and self.merge_strategy != "copy":
            raise ValueError("parent dirty conflict metadata requires copy strategy")
        return self


class SpawnWorktreeConflictResolverSpawn(BaseModel):
    model_config = _MODEL_CONFIG

    persona: Literal["conflict_resolver"]
    deliver: Literal["return"]
    workspace_policy: Literal["trusted"] = Field(alias="workspacePolicy")
    write_set: tuple[str, ...] = Field(alias="writeSet")
    metadata: SpawnWorktreeConflictResolverMetadata

    @model_validator(mode="after")
    def _validate_resolver_spawn(self) -> Self:
        for rel_path in self.write_set:
            _reject_unsafe_relative_path(rel_path)
        if set(self.write_set) != set(self.metadata.conflicted_files):
            raise ValueError("resolver writeSet must match conflicted files")
        return self


class SpawnWorktreeConflictReviewMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    conflict_kind: SpawnWorktreeConflictKind = Field(alias="conflictKind")
    conflicted_files: tuple[str, ...] = Field(alias="conflictedFiles")
    changed_files: tuple[str, ...] = Field(alias="changedFiles")
    summary: str
    suggested_actions: tuple[str, ...] = Field(alias="suggestedActions")
    resolver_prompt_ref: str = Field(alias="resolverPromptRef")
    preserves_child_worktree: Literal[True] = Field(alias="preservesChildWorktree")
    resolver_spawn: SpawnWorktreeConflictResolverSpawn = Field(alias="resolverSpawn")

    @model_validator(mode="after")
    def _validate_conflict_review(self) -> Self:
        for rel_path in (*self.conflicted_files, *self.changed_files):
            _reject_unsafe_relative_path(rel_path)
        if not self.conflicted_files:
            raise ValueError("conflict review requires conflicted files")
        if not self.changed_files:
            raise ValueError("conflict review requires changed files")
        if not self.summary.strip() or not self.suggested_actions:
            raise ValueError("conflict review requires summary and suggested actions")
        for value in (self.summary, self.resolver_prompt_ref, *self.suggested_actions):
            _validate_public_value(value)
        _validate_ref_prefix(self.resolver_prompt_ref, "artifact:")
        if self.resolver_spawn.metadata.conflict_kind != self.conflict_kind:
            raise ValueError("resolver metadata conflict kind must match review")
        if set(self.resolver_spawn.metadata.conflicted_files) != set(self.conflicted_files):
            raise ValueError("resolver metadata conflicted files must match review")
        if set(self.resolver_spawn.metadata.changed_files) != set(self.changed_files):
            raise ValueError("resolver metadata changed files must match review")
        return self


class WorkspaceAdoptionPreflightAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    file_mutated: Literal[False] = Field(default=False, alias="fileMutated")
    patch_applied: Literal[False] = Field(default=False, alias="patchApplied")
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    live_adoption_attached: Literal[False] = Field(
        default=False,
        alias="liveAdoptionAttached",
    )
    git_command_executed: Literal[False] = Field(default=False, alias="gitCommandExecuted")
    child_execution_attached: Literal[False] = Field(
        default=False,
        alias="childExecutionAttached",
    )
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
        "shell_or_code_executed",
        "file_mutated",
        "patch_applied",
        "workspace_mutated",
        "live_adoption_attached",
        "git_command_executed",
        "child_execution_attached",
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


class WorkspaceAdoptionPreflightCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: WorkspaceAdoptionPreflightCategory
    decision: WorkspaceAdoptionPreflightDecision
    hard_safety: bool = Field(alias="hardSafety")
    security_critical: bool = Field(alias="securityCritical")
    blocking: bool
    fail_open: bool = Field(alias="failOpen")
    fail_closed: bool = Field(alias="failClosed")
    path_preview: str = Field(alias="pathPreview")
    path_classification: WorkspaceAdoptionPreflightPathClassification = Field(
        alias="pathClassification",
    )
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    isolation_policy: WorkspaceIsolationPolicy | None = Field(
        default=None,
        alias="isolationPolicy",
    )
    preview: WorkspaceChangePreview | None = None
    dirty_parent_files: tuple[str, ...] = Field(default=(), alias="dirtyParentFiles")
    explicit_conflict_path: bool = Field(default=False, alias="explicitConflictPath")
    active_mutation: bool = Field(default=False, alias="activeMutation")
    worktree_operation: SpawnWorktreeOperationMetadata | None = Field(
        default=None,
        alias="worktreeOperation",
    )
    conflict_review: SpawnWorktreeConflictReviewMetadata | None = Field(
        default=None,
        alias="conflictReview",
    )
    evidence: tuple[WorkspaceEvidenceMetadata, ...] = ()
    external_import: ExternalSandboxImportMetadata | None = Field(
        default=None,
        alias="externalImport",
    )
    attachment_flags: WorkspaceAdoptionPreflightAttachmentFlags = Field(
        alias="attachmentFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        _validate_public_value(self.model_dump(by_alias=True, mode="json", warnings=False))
        if not self.case_id.strip():
            raise ValueError("workspace adoption preflight caseId must be non-empty")
        if not self.reason_codes or any(not reason.strip() for reason in self.reason_codes):
            raise ValueError("workspace adoption preflight cases require reasonCodes")
        if self.decision == "deny":
            if not (self.blocking and self.fail_closed and not self.fail_open):
                raise ValueError("deny workspace preflight decisions must fail closed")
        elif self.blocking or self.fail_closed:
            raise ValueError("metadata and preview decisions must not block")
        for changed_file in self.dirty_parent_files:
            _reject_unsafe_relative_path(changed_file)
        if self.preview is not None:
            for changed_file in self.preview.changed_files:
                _reject_unsafe_relative_path(changed_file)
        if self.conflict_review is not None and self.worktree_operation is not None:
            if set(self.conflict_review.changed_files) != set(
                self.worktree_operation.changed_files
            ):
                raise ValueError("conflict review changed files must match worktree metadata")
        self._validate_category_contract()
        return self

    def _validate_category_contract(self) -> None:
        if self.category == "no_worktree_fallback":
            if self.isolation_policy is None:
                raise ValueError("no-worktree fallback requires isolationPolicy")
            if self.isolation_policy.primary_mode != "scratch_isolation":
                raise ValueError("no-worktree fallback must choose scratch isolation")
            if self.isolation_policy.fallback_modes != ("shadow_snapshot",):
                raise ValueError("no-worktree fallback must preserve shadow snapshot")
            if self.isolation_policy.worktree_available or not self.isolation_policy.scratch_available:
                raise ValueError("no-worktree fallback availability metadata is inconsistent")

        if self.category == "adoption_preview":
            if self.decision != "preview_only" or self.preview is None:
                raise ValueError("adoption preview cases require preview-only metadata")
            if self.preview.applied:
                raise ValueError("workspace adoption preview must not apply changes")
            if self.worktree_operation is not None:
                if self.worktree_operation.action != "preview":
                    raise ValueError("adoption preview worktree metadata must use preview action")
                if self.worktree_operation.applied:
                    raise ValueError("preview worktree metadata must not apply changes")

        if self.category == "spawn_worktree_apply_intent":
            if self.decision != "metadata_only" or self.worktree_operation is None:
                raise ValueError("apply intent requires metadata-only worktree operation")
            if self.worktree_operation.action != "apply":
                raise ValueError("apply intent requires apply action metadata")
            if self.worktree_operation.applied or self.worktree_operation.cleanup_executed:
                raise ValueError("apply intent metadata must not execute adoption")
            if self.worktree_operation.disposition != "adoption_intent_metadata_only":
                raise ValueError("apply intent requires adoption-intent disposition")

        if self.category == "spawn_worktree_cherry_pick_intent":
            if self.decision != "metadata_only" or self.worktree_operation is None:
                raise ValueError("cherry-pick intent requires metadata-only worktree operation")
            if self.worktree_operation.action != "cherry_pick":
                raise ValueError("cherry-pick intent requires cherry-pick action metadata")
            if self.worktree_operation.applied or self.worktree_operation.cleanup_executed:
                raise ValueError("cherry-pick intent metadata must not execute adoption")
            if self.worktree_operation.adopted_commit_ref is None:
                raise ValueError("cherry-pick intent records redacted adopted commit ref")

        if self.category == "spawn_worktree_reject_disposition":
            if self.decision != "metadata_only" or self.worktree_operation is None:
                raise ValueError("reject disposition requires metadata-only worktree operation")
            if self.worktree_operation.action != "reject":
                raise ValueError("reject disposition requires reject action metadata")
            if self.worktree_operation.cleanup_executed:
                raise ValueError("reject disposition must not execute cleanup")
            if not any(item.kind == "rejection" for item in self.evidence):
                raise ValueError("reject disposition requires rejection evidence metadata")

        if self.category == "spawn_worktree_noop_apply":
            if self.decision != "metadata_only" or self.worktree_operation is None:
                raise ValueError("noop apply requires metadata-only worktree operation")
            if self.worktree_operation.action != "apply":
                raise ValueError("noop apply requires apply action metadata")
            if self.worktree_operation.disposition != "noop_unapplied":
                raise ValueError("noop apply requires noop disposition")

        if self.category == "spawn_worktree_noop_cherry_pick":
            if self.decision != "metadata_only" or self.worktree_operation is None:
                raise ValueError("noop cherry-pick requires metadata-only worktree operation")
            if self.worktree_operation.action != "cherry_pick":
                raise ValueError("noop cherry-pick requires cherry-pick action metadata")
            if self.worktree_operation.disposition != "noop_unapplied":
                raise ValueError("noop cherry-pick requires noop disposition")

        if self.category == "dirty_parent_conflict":
            if self.preview is None:
                raise ValueError("dirty parent conflict requires preview metadata")
            dirty_overlap = set(self.preview.changed_files).intersection(self.dirty_parent_files)
            if not dirty_overlap:
                raise ValueError("dirty parent conflict requires changed/dirty overlap")
            if self.explicit_conflict_path:
                raise ValueError("dirty parent conflict fixture must record missing conflict path")
            if self.decision != "deny" or "dirty_parent_overwrite" not in self.reason_codes:
                raise ValueError("dirty parent overwrite must be denied")
            if self.conflict_review is None:
                raise ValueError("dirty parent conflict requires conflict review metadata")
            if self.conflict_review.conflict_kind != "parent_dirty":
                raise ValueError("dirty parent conflict requires parent_dirty review")

        if self.category == "spawn_worktree_cherry_pick_conflict":
            if self.decision != "deny" or self.worktree_operation is None:
                raise ValueError("cherry-pick conflict must be denied with worktree metadata")
            if self.worktree_operation.action != "cherry_pick":
                raise ValueError("cherry-pick conflict requires cherry-pick action metadata")
            if self.worktree_operation.applied or self.worktree_operation.cleanup_executed:
                raise ValueError("cherry-pick conflict metadata must not execute adoption")
            if self.conflict_review is None:
                raise ValueError("cherry-pick conflict requires conflict review metadata")
            if self.conflict_review.conflict_kind != "cherry_pick":
                raise ValueError("cherry-pick conflict requires cherry_pick review")
            if "cherry_pick_conflict_review_required" not in self.reason_codes:
                raise ValueError("cherry-pick conflict requires reason code")

        if self.category == "rollback_active_mutation":
            if not self.active_mutation:
                raise ValueError("active mutation rollback case requires activeMutation=true")
            if self.decision != "deny" or "rollback_blocked_active_mutation" not in self.reason_codes:
                raise ValueError("rollback during active mutation must be denied")
            if not any(item.kind == "rollback" for item in self.evidence):
                raise ValueError("rollback case requires rollback evidence metadata")

        if self.category == "child_parent_evidence_distinction":
            if any(item.satisfies_parent_adopted for item in self.evidence):
                raise ValueError("child proposal cannot satisfy parent adoption")
            if not any(item.kind == "child_proposal" for item in self.evidence):
                raise ValueError("child evidence distinction requires child proposal metadata")

        if self.category == "parent_verified_after_adoption":
            if not any(item.satisfies_parent_adopted for item in self.evidence):
                raise ValueError("parent adoption metadata is required")
            if not any(item.satisfies_parent_verified_after_adoption for item in self.evidence):
                raise ValueError("parent verification after adoption metadata is required")

        if self.category == "external_sandbox_import":
            if self.external_import is None:
                raise ValueError("external sandbox import requires import metadata")
            if self.external_import.raw_parent_workspace_mutation:
                raise ValueError("external sandbox import must not mutate parent workspace")

        if self.category == "sealed_path_mutation_denied":
            if self.decision != "deny" or self.path_classification != "sealed_file":
                raise ValueError("sealed path mutation must be denied")
            if "sealed_file_mutation_blocked" not in self.reason_codes:
                raise ValueError("sealed path mutation denial requires reason code")

        if self.category == "workspace_escape_mutation_denied":
            if self.decision != "deny" or self.path_classification != "outside_workspace":
                raise ValueError("workspace escape mutation must be denied")
            if "path_escapes_workspace" not in self.reason_codes:
                raise ValueError("workspace escape mutation denial requires reason code")


class WorkspaceAdoptionPreflightFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["workspaceAdoptionPreflightFixture.v1"] = Field(
        alias="schemaVersion",
    )
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    attachment_flags: WorkspaceAdoptionPreflightAttachmentFlags = Field(
        alias="attachmentFlags",
    )
    cases: tuple[WorkspaceAdoptionPreflightCase, ...]

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
            raise ValueError("workspace adoption preflight caseIds must be unique")
        categories = {case.category for case in self.cases}
        if not _REQUIRED_CATEGORIES.issubset(categories):
            raise ValueError("workspace adoption preflight fixture is missing categories")
        return self


class WorkspaceAdoptionPreflightProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    attachment_flags: WorkspaceAdoptionPreflightAttachmentFlags = Field(
        alias="attachmentFlags",
    )
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_decision: dict[str, int] = Field(alias="byDecision")
    by_category: dict[str, int] = Field(alias="byCategory")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


def load_workspace_adoption_preflight_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> WorkspaceAdoptionPreflightFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return WorkspaceAdoptionPreflightFixture.model_validate(payload)


def project_workspace_adoption_preflight_fixture(
    fixture: WorkspaceAdoptionPreflightFixture | Mapping[str, Any],
) -> WorkspaceAdoptionPreflightProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    snapshots = {case.case_id: _case_snapshot(case) for case in safe_fixture.cases}
    _validate_public_value(snapshots)
    return WorkspaceAdoptionPreflightProjection(
        fixtureId=safe_fixture.fixture_id,
        attachmentFlags=safe_fixture.attachment_flags,
        noLiveExecution=True,
        caseOrder=tuple(case.case_id for case in safe_fixture.cases),
        byDecision=dict(Counter(case.decision for case in safe_fixture.cases)),
        byCategory=dict(Counter(case.category for case in safe_fixture.cases)),
        caseSnapshots=snapshots,
    )


def _validated_fixture_snapshot(
    fixture: WorkspaceAdoptionPreflightFixture | Mapping[str, Any],
) -> WorkspaceAdoptionPreflightFixture:
    if isinstance(fixture, WorkspaceAdoptionPreflightFixture):
        return WorkspaceAdoptionPreflightFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return WorkspaceAdoptionPreflightFixture.model_validate(fixture)


def _case_snapshot(case: WorkspaceAdoptionPreflightCase) -> dict[str, object]:
    snapshot = {
        "caseId": case.case_id,
        "category": case.category,
        "decision": case.decision,
        "hardSafety": case.hard_safety,
        "securityCritical": case.security_critical,
        "blocking": case.blocking,
        "failOpen": case.fail_open,
        "failClosed": case.fail_closed,
        "pathPreview": case.path_preview,
        "pathClassification": case.path_classification,
        "reasonCodes": case.reason_codes,
        "isolationMode": (
            case.isolation_policy.primary_mode if case.isolation_policy is not None else None
        ),
        "fallbackModes": (
            case.isolation_policy.fallback_modes if case.isolation_policy is not None else ()
        ),
        "preview": (
            case.preview.model_dump(by_alias=True, mode="json", warnings=False)
            if case.preview is not None
            else None
        ),
        "dirtyParentFiles": case.dirty_parent_files,
        "explicitConflictPath": case.explicit_conflict_path,
        "activeMutation": case.active_mutation,
        "worktreeOperation": (
            case.worktree_operation.model_dump(by_alias=True, mode="json", warnings=False)
            if case.worktree_operation is not None
            else None
        ),
        "conflictReview": (
            case.conflict_review.model_dump(by_alias=True, mode="json", warnings=False)
            if case.conflict_review is not None
            else None
        ),
        "evidenceKinds": tuple(item.kind for item in case.evidence),
        "parentAdoptedSatisfied": any(
            item.satisfies_parent_adopted for item in case.evidence
        ),
        "parentVerifiedAfterAdoptionSatisfied": any(
            item.satisfies_parent_verified_after_adoption for item in case.evidence
        ),
        "externalImport": (
            case.external_import.model_dump(by_alias=True, mode="json", warnings=False)
            if case.external_import is not None
            else None
        ),
        "adkRunnerInvoked": False,
        "fileMutated": False,
        "patchApplied": False,
        "workspaceMutated": False,
        "liveAdoptionAttached": False,
        "gitCommandExecuted": False,
    }
    _validate_public_value(snapshot)
    return snapshot


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
        raise ValueError("workspace adoption preflight path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _FORBIDDEN_PATH_RE.search(path_text):
        raise ValueError("workspace adoption preflight fixtures must be local and non-production")


def _reject_unsafe_relative_path(path_text: str) -> None:
    if not path_text.strip():
        raise ValueError("workspace paths must be non-empty")
    if path_text.startswith(("/", "\\")) or ".." in Path(path_text).parts:
        raise ValueError("workspace paths must be relative to the workspace")
    _reject_unsafe_path_text(path_text)


def _validate_ref_prefix(value: str, prefix: str) -> None:
    if not value.startswith(prefix) or len(value) == len(prefix):
        raise ValueError(f"workspace adoption metadata refs must use {prefix} refs")
    if any(char.isspace() for char in value):
        raise ValueError("workspace adoption metadata refs must be compact redacted refs")
    _validate_public_value(value)


def _reject_unsafe_raw_value(value: object) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if _FORBIDDEN_PATH_RE.search(value):
            raise ValueError("workspace adoption preflight fixture contains unsafe path")
        if _has_forbidden_public_token(value) or _has_secret_shaped_value(value):
            raise ValueError("workspace adoption preflight fixture contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            _reject_unsafe_mapping_key(key)
            normalized = _normalize_key(key)
            if nested_value is True and normalized in _FORBIDDEN_RAW_KEY_TOKENS:
                raise ValueError("workspace adoption preflight cannot claim live behavior")
            _reject_unsafe_raw_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_raw_value(item)


def _validate_public_value(value: object) -> None:
    if isinstance(value, str):
        if _FORBIDDEN_PATH_RE.search(value):
            raise ValueError("workspace adoption preflight public snapshot has unsafe path")
        if _has_forbidden_public_token(value) or _has_secret_shaped_value(value):
            raise ValueError("workspace adoption preflight public snapshot has unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            _reject_unsafe_mapping_key(key)
            _validate_public_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _validate_public_value(item)
        return
    rendered = json.dumps(value, sort_keys=True)
    if _has_forbidden_public_token(rendered) or _has_secret_shaped_value(rendered):
        raise ValueError("workspace adoption preflight public snapshot has unsafe data")


def _reject_unsafe_mapping_key(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("workspace adoption preflight mappings must use string keys")
    normalized = _normalize_key(value)
    if _has_forbidden_public_token(value) or _SECRET_LIKE_KEY_RE.search(
        f"_{normalized}_"
    ):
        raise ValueError("workspace adoption preflight public snapshot has unsafe data")
    if re.search(
        r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
        r"supabase://|s3://|gs://|postgres(?:ql)?://",
        value,
        re.IGNORECASE,
    ):
        raise ValueError("workspace adoption preflight public snapshot has unsafe path")


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("workspace adoption preflight values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("workspace adoption preflight mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("workspace adoption preflight values must be JSON-compatible")


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


def _has_forbidden_public_token(value: str) -> bool:
    normalized = value.casefold()
    return any(token in normalized for token in _FORBIDDEN_PUBLIC_TOKENS_NORMALIZED)


def _has_secret_shaped_value(value: str) -> bool:
    redacted = sanitize_tool_preview(value)
    return "[redacted]" in redacted and redacted != value


__all__ = [
    "SpawnWorktreeConflictReviewMetadata",
    "SpawnWorktreeConflictResolverMetadata",
    "SpawnWorktreeConflictResolverSpawn",
    "SpawnWorktreeOperationMetadata",
    "SpawnWorktreeRenameMetadata",
    "WorkspaceAdoptionPreflightAttachmentFlags",
    "WorkspaceAdoptionPreflightCase",
    "WorkspaceAdoptionPreflightFixture",
    "WorkspaceAdoptionPreflightProjection",
    "load_workspace_adoption_preflight_fixture",
    "project_workspace_adoption_preflight_fixture",
]
