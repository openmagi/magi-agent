from __future__ import annotations

import importlib
import subprocess
import sys

import pytest
from pydantic import ValidationError

from openmagi_core_agent.workspace.isolation import (
    ExternalSandboxImportMetadata,
    WorkspaceAdoptionMetadata,
    WorkspaceChangePreview,
    WorkspaceDiffMetadata,
    WorkspaceEvidenceMetadata,
    WorkspaceIsolationPolicy,
    WorkspaceRollbackMetadata,
    WorkspaceVariantIsolationPlan,
)


def test_coding_child_defaults_to_version_control_worktree_when_available() -> None:
    policy = WorkspaceIsolationPolicy.for_child_work(
        child_kind="coding",
        worktree_available=True,
    )

    assert policy.primary_mode == "version_control_worktree"
    assert policy.fallback_modes == ()
    assert policy.model_dump(by_alias=True)["primaryMode"] == "version_control_worktree"


def test_no_worktree_fallback_chooses_scratch_isolation_plus_shadow_snapshot() -> None:
    policy = WorkspaceIsolationPolicy.for_child_work(
        child_kind="coding",
        worktree_available=False,
        scratch_available=True,
    )

    assert policy.primary_mode == "scratch_isolation"
    assert policy.fallback_modes == ("shadow_snapshot",)


def test_policy_records_scratch_available_alias_and_dump() -> None:
    policy = WorkspaceIsolationPolicy(
        childKind="coding",
        primaryMode="scratch_isolation",
        fallbackModes=("shadow_snapshot",),
        worktreeAvailable=False,
        scratchAvailable=True,
    )

    assert policy.scratch_available is True
    assert policy.model_dump(by_alias=True)["scratchAvailable"] is True


@pytest.mark.parametrize(
    "primary_mode,worktree_available,scratch_available,match",
    (
        ("scratch_isolation", False, False, "scratch isolation requires scratch availability"),
        (
            "version_control_worktree",
            False,
            True,
            "version control worktree requires worktree availability",
        ),
        ("external_sandbox", True, False, "external sandbox requires no local isolation"),
        ("external_sandbox", False, True, "external sandbox requires no local isolation"),
    ),
)
def test_policy_direct_construction_rejects_primary_mode_availability_mismatches(
    primary_mode: str,
    worktree_available: bool,
    scratch_available: bool,
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        WorkspaceIsolationPolicy(
            childKind="coding",
            primaryMode=primary_mode,
            worktreeAvailable=worktree_available,
            scratchAvailable=scratch_available,
        )


@pytest.mark.parametrize(
    "update,match",
    (
        (
            {"primaryMode": "scratch_isolation", "scratchAvailable": False},
            "scratch isolation requires scratch availability",
        ),
        (
            {"primaryMode": "version_control_worktree", "worktreeAvailable": False},
            "version control worktree requires worktree availability",
        ),
        (
            {"primaryMode": "external_sandbox"},
            "external sandbox requires no local isolation",
        ),
    ),
)
def test_policy_model_copy_rejects_primary_mode_availability_mismatches(
    update: dict[str, object],
    match: str,
) -> None:
    policy = WorkspaceIsolationPolicy.for_child_work(child_kind="coding")

    with pytest.raises(ValidationError, match=match):
        policy.model_copy(update=update)


def test_coding_child_distinguishes_no_worktree_with_scratch_from_no_isolation() -> None:
    scratch_policy = WorkspaceIsolationPolicy.for_child_work(
        child_kind="coding",
        worktree_available=False,
        scratch_available=True,
    )
    external_policy = WorkspaceIsolationPolicy.for_child_work(
        child_kind="coding",
        worktree_available=False,
        scratch_available=False,
    )

    assert scratch_policy.primary_mode == "scratch_isolation"
    assert scratch_policy.fallback_modes == ("shadow_snapshot",)
    assert scratch_policy.scratch_available is True

    assert external_policy.primary_mode == "external_sandbox"
    assert external_policy.fallback_modes == ("shadow_snapshot",)
    assert external_policy.worktree_available is False
    assert external_policy.scratch_available is False


def test_non_coding_child_defaults_to_shared_or_scratch_unless_mutating_important_state() -> None:
    non_mutating = WorkspaceIsolationPolicy.for_child_work(
        child_kind="non_coding",
        worktree_available=True,
        mutates_important_state=False,
    )
    mutating = WorkspaceIsolationPolicy.for_child_work(
        child_kind="non_coding",
        worktree_available=False,
        mutates_important_state=True,
    )

    assert non_mutating.primary_mode in {"shared_workspace", "scratch_isolation"}
    assert mutating.primary_mode == "scratch_isolation"
    assert mutating.fallback_modes == ("shadow_snapshot",)


def test_non_coding_important_state_mutation_uses_external_sandbox_when_scratch_unavailable() -> None:
    policy = WorkspaceIsolationPolicy.for_child_work(
        child_kind="non_coding",
        worktree_available=False,
        scratch_available=False,
        mutates_important_state=True,
    )

    assert policy.primary_mode == "external_sandbox"
    assert policy.fallback_modes == ("shadow_snapshot",)
    assert policy.scratch_available is False


def test_tournament_variants_require_one_isolated_workspace_per_variant() -> None:
    plan = WorkspaceVariantIsolationPlan.for_variants(
        variant_ids=("a", "b", "c"),
        worktree_available=True,
    )

    assert tuple(allocation.variant_id for allocation in plan.allocations) == ("a", "b", "c")
    assert all(allocation.mode == "version_control_worktree" for allocation in plan.allocations)
    assert len({allocation.workspace_key for allocation in plan.allocations}) == 3
    assert all(allocation.mode != "shared_workspace" for allocation in plan.allocations)


def test_adoption_preview_records_changed_files_and_diff_metadata_without_applying_changes() -> None:
    preview = WorkspaceChangePreview(
        proposal_id="proposal-1",
        changed_files=("openmagi_core_agent/workspace/isolation.py",),
        diff=WorkspaceDiffMetadata(
            summary="adds metadata-only workspace policy models",
            added_lines=12,
            removed_lines=0,
        ),
    )

    assert preview.applied is False
    assert preview.changed_files == ("openmagi_core_agent/workspace/isolation.py",)
    assert preview.diff.added_lines == 12


def test_adoption_refuses_dirty_parent_overwrite_without_explicit_conflict_path() -> None:
    preview = WorkspaceChangePreview(
        proposal_id="proposal-1",
        changed_files=("openmagi_core_agent/workspace/isolation.py",),
        diff=WorkspaceDiffMetadata(summary="candidate", added_lines=1, removed_lines=0),
    )

    with pytest.raises(ValidationError, match="dirty parent overwrite"):
        WorkspaceAdoptionMetadata(
            adoption_id="adopt-1",
            preview=preview,
            dirty_parent_files=("openmagi_core_agent/workspace/isolation.py",),
            explicit_conflict_path=False,
            explicit_adoption_metadata=True,
        )


def test_adoption_rejects_parent_evidence_for_different_adoption_id() -> None:
    preview = WorkspaceChangePreview(
        proposal_id="proposal-1",
        changed_files=("openmagi_core_agent/workspace/isolation.py",),
        diff=WorkspaceDiffMetadata(summary="candidate", added_lines=1, removed_lines=0),
    )

    with pytest.raises(ValidationError, match="adoption evidence id must match adoption id"):
        WorkspaceAdoptionMetadata(
            adoption_id="adopt-2",
            preview=preview,
            explicit_adoption_metadata=True,
            evidence=WorkspaceEvidenceMetadata.parent_adopted(
                adoption_id="adopt-1",
                explicit_adoption_metadata=True,
            ),
        )


def test_rollback_is_blocked_during_active_mutation() -> None:
    with pytest.raises(ValidationError, match="active mutation"):
        WorkspaceRollbackMetadata(
            rollback_id="rollback-1",
            adoption_id="adopt-1",
            active_mutation=True,
            evidence=WorkspaceEvidenceMetadata.rollback(rollback_id="rollback-1"),
        )


def test_rollback_rejects_evidence_for_different_rollback_id() -> None:
    with pytest.raises(ValidationError, match="rollback evidence id must match rollback id"):
        WorkspaceRollbackMetadata(
            rollback_id="rollback-2",
            adoption_id="adopt-1",
            evidence=WorkspaceEvidenceMetadata.rollback(rollback_id="rollback-1"),
        )


def test_child_proposal_evidence_does_not_satisfy_parent_adopted_or_verified_evidence() -> None:
    proposal = WorkspaceEvidenceMetadata.child_proposal(proposal_id="proposal-1")

    assert proposal.kind == "child_proposal"
    assert proposal.satisfies_parent_adopted is False
    assert proposal.satisfies_parent_verified_after_adoption is False


def test_parent_adopted_evidence_requires_explicit_adoption_metadata() -> None:
    with pytest.raises(ValidationError, match="explicit adoption metadata"):
        WorkspaceEvidenceMetadata.parent_adopted(
            adoption_id="adopt-1",
            explicit_adoption_metadata=False,
        )

    adopted = WorkspaceEvidenceMetadata.parent_adopted(
        adoption_id="adopt-1",
        explicit_adoption_metadata=True,
    )
    assert adopted.satisfies_parent_adopted is True


def test_parent_verified_after_adoption_evidence_is_distinct_from_child_verification() -> None:
    child_verification = WorkspaceEvidenceMetadata.child_verification(proposal_id="proposal-1")
    parent_verification = WorkspaceEvidenceMetadata.parent_verified_after_adoption(
        adoption_id="adopt-1",
        verification_id="verify-1",
    )

    assert child_verification.kind == "child_verification"
    assert child_verification.satisfies_parent_verified_after_adoption is False
    assert parent_verification.kind == "parent_verification_after_adoption"
    assert parent_verification.satisfies_parent_verified_after_adoption is True


def test_workspace_evidence_catalog_includes_rejection_conflict_and_rollback_metadata() -> None:
    rejection = WorkspaceEvidenceMetadata.rejection(proposal_id="proposal-1")
    conflict = WorkspaceEvidenceMetadata.conflict(adoption_id="adopt-1")
    rollback = WorkspaceEvidenceMetadata.rollback(rollback_id="rollback-1")

    assert rejection.kind == "rejection"
    assert conflict.kind == "conflict"
    assert rollback.kind == "rollback"
    assert rejection.satisfies_parent_adopted is False
    assert conflict.satisfies_parent_verified_after_adoption is False


def test_external_sandbox_outputs_must_import_through_artifacts_or_evidence_metadata() -> None:
    with pytest.raises(ValidationError, match="artifact or evidence metadata"):
        ExternalSandboxImportMetadata(
            sandbox_id="sandbox-1",
            output_refs=("result.patch",),
            imported_artifact_refs=(),
            imported_evidence_refs=(),
        )

    with pytest.raises(ValidationError, match="raw parent workspace mutation"):
        ExternalSandboxImportMetadata(
            sandbox_id="sandbox-1",
            output_refs=("result.patch",),
            imported_artifact_refs=("artifact-1",),
            raw_parent_workspace_mutation=True,
        )

    imported = ExternalSandboxImportMetadata(
        sandbox_id="sandbox-1",
        output_refs=("result.patch",),
        imported_artifact_refs=("artifact-1",),
    )
    assert imported.raw_parent_workspace_mutation is False


@pytest.mark.parametrize(
    "model,flag",
    (
        (WorkspaceIsolationPolicy.for_child_work(child_kind="coding"), "trafficAttached"),
        (WorkspaceIsolationPolicy.for_child_work(child_kind="coding"), "execution_attached"),
        (WorkspaceIsolationPolicy.for_child_work(child_kind="coding"), "liveAdoptionAttached"),
        (WorkspaceIsolationPolicy.for_child_work(child_kind="coding"), "canary_attached"),
        (WorkspaceEvidenceMetadata.child_proposal(proposal_id="proposal-1"), "trafficAttached"),
        (WorkspaceEvidenceMetadata.child_proposal(proposal_id="proposal-1"), "liveAdoptionAttached"),
    ),
)
def test_attachment_flags_remain_false_and_model_copy_cannot_turn_them_on(
    model: object,
    flag: str,
) -> None:
    with pytest.raises(ValidationError):
        model.model_copy(update={flag: True})  # type: ignore[attr-defined]


def test_workspace_import_boundary_stays_adk_runner_runtime_route_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.workspace.isolation")
forbidden_modules = (
    "google.adk.runners",
    "google.adk.agents",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
)
loaded = [module for module in forbidden_modules if module in sys.modules]
if loaded:
    raise AssertionError(f"workspace isolation import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    module = importlib.import_module("openmagi_core_agent.workspace.isolation")
    assert hasattr(module, "WorkspaceIsolationPolicy")
