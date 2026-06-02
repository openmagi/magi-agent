from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


WorkspaceMode = Literal[
    "shared_workspace",
    "scratch_isolation",
    "version_control_worktree",
    "shadow_snapshot",
    "external_sandbox",
]
ChildWorkKind = Literal["coding", "non_coding"]
WorkspaceEvidenceKind = Literal[
    "child_proposal",
    "child_verification",
    "parent_adoption",
    "parent_verification_after_adoption",
    "rejection",
    "conflict",
    "rollback",
]

WORKSPACE_EVIDENCE_CATALOG: tuple[WorkspaceEvidenceKind, ...] = (
    "child_proposal",
    "child_verification",
    "parent_adoption",
    "parent_verification_after_adoption",
    "rejection",
    "conflict",
    "rollback",
)

_WORKSPACE_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
)
_ATTACHMENT_FLAG_NAMES = (
    "traffic_attached",
    "execution_attached",
    "live_adoption_attached",
    "canary_attached",
)


class WorkspaceMetadataModel(BaseModel):
    model_config = _WORKSPACE_MODEL_CONFIG

    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")
    live_adoption_attached: bool = Field(default=False, alias="liveAdoptionAttached")
    canary_attached: bool = Field(default=False, alias="canaryAttached")

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
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)

    @model_validator(mode="after")
    def _reject_runtime_attachment(self) -> Self:
        enabled_flags = [name for name in _ATTACHMENT_FLAG_NAMES if getattr(self, name)]
        if enabled_flags:
            raise ValueError("workspace metadata attachment flags must remain false")
        return self


class WorkspaceIsolationPolicy(WorkspaceMetadataModel):
    child_kind: ChildWorkKind = Field(alias="childKind")
    primary_mode: WorkspaceMode = Field(alias="primaryMode")
    fallback_modes: tuple[WorkspaceMode, ...] = Field(default=(), alias="fallbackModes")
    worktree_available: bool = Field(alias="worktreeAvailable")
    scratch_available: bool = Field(default=True, alias="scratchAvailable")
    mutates_important_state: bool = Field(default=False, alias="mutatesImportantState")
    policy_notes: tuple[str, ...] = Field(default=(), alias="policyNotes")

    @classmethod
    def for_child_work(
        cls,
        *,
        child_kind: ChildWorkKind,
        worktree_available: bool = True,
        scratch_available: bool = True,
        mutates_important_state: bool = False,
    ) -> "WorkspaceIsolationPolicy":
        if child_kind == "coding":
            if worktree_available:
                return cls(
                    child_kind=child_kind,
                    primary_mode="version_control_worktree",
                    worktree_available=True,
                    scratch_available=scratch_available,
                    mutates_important_state=mutates_important_state,
                    policy_notes=("coding child work defaults to version-control worktree",),
                )
            if scratch_available:
                return cls(
                    child_kind=child_kind,
                    primary_mode="scratch_isolation",
                    fallback_modes=("shadow_snapshot",),
                    worktree_available=False,
                    scratch_available=True,
                    mutates_important_state=mutates_important_state,
                    policy_notes=("no worktree fallback uses scratch isolation plus shadow snapshot",),
                )
            return cls(
                child_kind=child_kind,
                primary_mode="external_sandbox",
                fallback_modes=("shadow_snapshot",),
                worktree_available=False,
                scratch_available=False,
                mutates_important_state=mutates_important_state,
                policy_notes=("no worktree or scratch fallback requires external sandbox metadata",),
            )

        if mutates_important_state and not worktree_available and scratch_available:
            return cls(
                child_kind=child_kind,
                primary_mode="scratch_isolation",
                fallback_modes=("shadow_snapshot",),
                worktree_available=False,
                scratch_available=True,
                mutates_important_state=True,
                policy_notes=("important state mutation cannot stay in shared workspace",),
            )
        if mutates_important_state and not worktree_available:
            return cls(
                child_kind=child_kind,
                primary_mode="external_sandbox",
                fallback_modes=("shadow_snapshot",),
                worktree_available=False,
                scratch_available=False,
                mutates_important_state=True,
                policy_notes=("important state mutation requires external sandbox when scratch is unavailable",),
            )
        return cls(
            child_kind=child_kind,
            primary_mode="shared_workspace",
            worktree_available=worktree_available,
            scratch_available=scratch_available,
            mutates_important_state=mutates_important_state,
            policy_notes=("non-coding child work may stay shared when important state is not mutated",),
        )

    @model_validator(mode="after")
    def _validate_primary_mode_availability(self) -> Self:
        if self.primary_mode == "scratch_isolation" and not self.scratch_available:
            raise ValueError("scratch isolation requires scratch availability")
        if self.primary_mode == "version_control_worktree" and not self.worktree_available:
            raise ValueError("version control worktree requires worktree availability")
        if self.primary_mode == "external_sandbox" and (
            self.worktree_available or self.scratch_available
        ):
            raise ValueError("external sandbox requires no local isolation availability")
        return self


class WorkspaceVariantAllocation(WorkspaceMetadataModel):
    variant_id: str = Field(alias="variantId")
    workspace_key: str = Field(alias="workspaceKey")
    mode: WorkspaceMode

    @model_validator(mode="after")
    def _reject_shared_variant_workspace(self) -> Self:
        if self.mode == "shared_workspace":
            raise ValueError("tournament variants require isolated workspaces")
        return self


class WorkspaceVariantIsolationPlan(WorkspaceMetadataModel):
    allocations: tuple[WorkspaceVariantAllocation, ...]

    @classmethod
    def for_variants(
        cls,
        *,
        variant_ids: tuple[str, ...],
        worktree_available: bool,
    ) -> "WorkspaceVariantIsolationPlan":
        mode: WorkspaceMode = (
            "version_control_worktree" if worktree_available else "scratch_isolation"
        )
        return cls(
            allocations=tuple(
                WorkspaceVariantAllocation(
                    variant_id=variant_id,
                    workspace_key=f"variant:{variant_id}",
                    mode=mode,
                )
                for variant_id in variant_ids
            )
        )

    @model_validator(mode="after")
    def _require_one_workspace_per_variant(self) -> Self:
        variant_ids = tuple(allocation.variant_id for allocation in self.allocations)
        workspace_keys = tuple(allocation.workspace_key for allocation in self.allocations)
        if len(set(variant_ids)) != len(variant_ids):
            raise ValueError("variant ids must be unique")
        if len(set(workspace_keys)) != len(workspace_keys):
            raise ValueError("tournament variants require one isolated workspace per variant")
        return self


class WorkspaceDiffMetadata(WorkspaceMetadataModel):
    summary: str
    added_lines: int = Field(alias="addedLines")
    removed_lines: int = Field(alias="removedLines")
    diff_ref: str | None = Field(default=None, alias="diffRef")

    @model_validator(mode="after")
    def _validate_diff_counts(self) -> Self:
        if not self.summary.strip():
            raise ValueError("diff summary must be non-empty")
        if self.added_lines < 0 or self.removed_lines < 0:
            raise ValueError("diff line counts must be non-negative")
        return self


class WorkspaceChangePreview(WorkspaceMetadataModel):
    proposal_id: str = Field(alias="proposalId")
    changed_files: tuple[str, ...] = Field(alias="changedFiles")
    diff: WorkspaceDiffMetadata
    applied: bool = False

    @model_validator(mode="after")
    def _validate_preview(self) -> Self:
        if self.applied:
            raise ValueError("workspace adoption preview metadata must not apply changes")
        if not self.changed_files:
            raise ValueError("workspace adoption preview must record changed files")
        if any(not changed_file.strip() for changed_file in self.changed_files):
            raise ValueError("workspace changed files must be non-empty")
        return self


class WorkspaceAdoptionMetadata(WorkspaceMetadataModel):
    adoption_id: str = Field(alias="adoptionId")
    preview: WorkspaceChangePreview
    dirty_parent_files: tuple[str, ...] = Field(default=(), alias="dirtyParentFiles")
    explicit_conflict_path: bool = Field(default=False, alias="explicitConflictPath")
    explicit_adoption_metadata: bool = Field(alias="explicitAdoptionMetadata")
    evidence: "WorkspaceEvidenceMetadata | None" = None

    @model_validator(mode="after")
    def _validate_adoption(self) -> Self:
        if not self.explicit_adoption_metadata:
            raise ValueError("parent adoption requires explicit adoption metadata")
        dirty_overlap = set(self.preview.changed_files).intersection(self.dirty_parent_files)
        if dirty_overlap and not self.explicit_conflict_path:
            raise ValueError("dirty parent overwrite requires explicit conflict path")
        if self.evidence is not None and not self.evidence.satisfies_parent_adopted:
            raise ValueError("adoption evidence must satisfy parent adoption")
        if self.evidence is not None and self.evidence.adoption_id != self.adoption_id:
            raise ValueError("adoption evidence id must match adoption id")
        return self


class WorkspaceEvidenceMetadata(WorkspaceMetadataModel):
    kind: WorkspaceEvidenceKind
    proposal_id: str | None = Field(default=None, alias="proposalId")
    adoption_id: str | None = Field(default=None, alias="adoptionId")
    verification_id: str | None = Field(default=None, alias="verificationId")
    rollback_id: str | None = Field(default=None, alias="rollbackId")
    explicit_adoption_metadata: bool = Field(
        default=False,
        alias="explicitAdoptionMetadata",
    )
    satisfies_parent_adopted: bool = Field(
        default=False,
        alias="satisfiesParentAdopted",
    )
    satisfies_parent_verified_after_adoption: bool = Field(
        default=False,
        alias="satisfiesParentVerifiedAfterAdoption",
    )

    @classmethod
    def child_proposal(cls, *, proposal_id: str) -> "WorkspaceEvidenceMetadata":
        return cls(kind="child_proposal", proposal_id=proposal_id)

    @classmethod
    def child_verification(cls, *, proposal_id: str) -> "WorkspaceEvidenceMetadata":
        return cls(kind="child_verification", proposal_id=proposal_id)

    @classmethod
    def parent_adopted(
        cls,
        *,
        adoption_id: str,
        explicit_adoption_metadata: bool,
    ) -> "WorkspaceEvidenceMetadata":
        return cls(
            kind="parent_adoption",
            adoption_id=adoption_id,
            explicit_adoption_metadata=explicit_adoption_metadata,
            satisfies_parent_adopted=True,
        )

    @classmethod
    def parent_verified_after_adoption(
        cls,
        *,
        adoption_id: str,
        verification_id: str,
    ) -> "WorkspaceEvidenceMetadata":
        return cls(
            kind="parent_verification_after_adoption",
            adoption_id=adoption_id,
            verification_id=verification_id,
            satisfies_parent_verified_after_adoption=True,
        )

    @classmethod
    def rejection(cls, *, proposal_id: str) -> "WorkspaceEvidenceMetadata":
        return cls(kind="rejection", proposal_id=proposal_id)

    @classmethod
    def conflict(cls, *, adoption_id: str) -> "WorkspaceEvidenceMetadata":
        return cls(kind="conflict", adoption_id=adoption_id)

    @classmethod
    def rollback(cls, *, rollback_id: str) -> "WorkspaceEvidenceMetadata":
        return cls(kind="rollback", rollback_id=rollback_id)

    @model_validator(mode="after")
    def _validate_evidence_semantics(self) -> Self:
        if self.kind == "parent_adoption":
            if not self.explicit_adoption_metadata:
                raise ValueError("parent adopted evidence requires explicit adoption metadata")
            if not self.adoption_id:
                raise ValueError("parent adopted evidence requires adoption id")
            if not self.satisfies_parent_adopted:
                raise ValueError("parent adopted evidence must satisfy parent adoption")
        elif self.satisfies_parent_adopted:
            raise ValueError("only parent adoption evidence can satisfy parent adoption")

        if self.kind == "parent_verification_after_adoption":
            if not self.adoption_id or not self.verification_id:
                raise ValueError("parent verification after adoption requires adoption and verification ids")
            if not self.satisfies_parent_verified_after_adoption:
                raise ValueError(
                    "parent verification after adoption evidence must satisfy parent verification"
                )
        elif self.satisfies_parent_verified_after_adoption:
            raise ValueError(
                "only parent verification after adoption evidence can satisfy parent verification"
            )

        if self.kind in {"child_proposal", "child_verification"} and not self.proposal_id:
            raise ValueError("child workspace evidence requires proposal id")
        if self.kind == "rejection" and not self.proposal_id:
            raise ValueError("rejection evidence requires proposal id")
        if self.kind == "conflict" and not self.adoption_id:
            raise ValueError("conflict evidence requires adoption id")
        if self.kind == "rollback" and not self.rollback_id:
            raise ValueError("rollback evidence requires rollback id")
        return self


class WorkspaceRollbackMetadata(WorkspaceMetadataModel):
    rollback_id: str = Field(alias="rollbackId")
    adoption_id: str = Field(alias="adoptionId")
    active_mutation: bool = Field(default=False, alias="activeMutation")
    evidence: WorkspaceEvidenceMetadata

    @model_validator(mode="after")
    def _validate_rollback(self) -> Self:
        if self.active_mutation:
            raise ValueError("rollback is blocked during active mutation")
        if self.evidence.kind != "rollback":
            raise ValueError("rollback metadata requires rollback evidence")
        if self.evidence.rollback_id != self.rollback_id:
            raise ValueError("rollback evidence id must match rollback id")
        return self


class ExternalSandboxImportMetadata(WorkspaceMetadataModel):
    sandbox_id: str = Field(alias="sandboxId")
    output_refs: tuple[str, ...] = Field(alias="outputRefs")
    imported_artifact_refs: tuple[str, ...] = Field(default=(), alias="importedArtifactRefs")
    imported_evidence_refs: tuple[str, ...] = Field(default=(), alias="importedEvidenceRefs")
    raw_parent_workspace_mutation: bool = Field(
        default=False,
        alias="rawParentWorkspaceMutation",
    )

    @model_validator(mode="after")
    def _validate_external_import(self) -> Self:
        if self.raw_parent_workspace_mutation:
            raise ValueError(
                "external sandbox outputs must not use raw parent workspace mutation"
            )
        if self.output_refs and not (self.imported_artifact_refs or self.imported_evidence_refs):
            raise ValueError(
                "external sandbox outputs must import through artifact or evidence metadata"
            )
        return self
