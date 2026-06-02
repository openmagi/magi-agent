from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.shadow.workspace_adoption_preflight_contract import (
    WorkspaceAdoptionPreflightAttachmentFlags,
    WorkspaceAdoptionPreflightFixture,
    load_workspace_adoption_preflight_fixture,
    project_workspace_adoption_preflight_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "workspace_adoption_preflight"


def _case_payload(payload: dict[str, object], case_id: str) -> dict[str, object]:
    cases = payload["cases"]
    assert isinstance(cases, list)
    for case in cases:
        assert isinstance(case, dict)
        if case.get("caseId") == case_id:
            return case
    raise AssertionError(f"missing fixture case {case_id}")


def test_workspace_adoption_preflight_fixture_covers_workspace_safety_decisions() -> None:
    fixture = load_workspace_adoption_preflight_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_workspace_adoption_preflight_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "workspace_adoption_preflight_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.case_order == (
        "coding_no_worktree_fallback_scratch_shadow",
        "adoption_preview_records_diff_without_applying",
        "apply_intent_records_metadata_without_execution",
        "cherry_pick_intent_records_metadata_without_execution",
        "reject_records_disposition_without_cleanup_execution",
        "noop_apply_records_unapplied_metadata",
        "noop_cherry_pick_records_unapplied_metadata",
        "dirty_parent_conflict_denied",
        "cherry_pick_conflict_records_review_metadata",
        "rollback_active_mutation_denied",
        "child_proposal_does_not_satisfy_parent_adoption",
        "parent_verified_after_adoption_distinct",
        "external_sandbox_imports_artifact_metadata",
        "sealed_path_mutation_denied",
        "workspace_escape_mutation_denied",
    )
    assert projection.by_decision == {
        "metadata_only": 9,
        "preview_only": 1,
        "deny": 5,
    }
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_execution is True

    no_worktree = cases["coding_no_worktree_fallback_scratch_shadow"]
    assert no_worktree.isolation_policy is not None
    assert no_worktree.isolation_policy.primary_mode == "scratch_isolation"
    assert no_worktree.isolation_policy.fallback_modes == ("shadow_snapshot",)
    assert no_worktree.isolation_policy.worktree_available is False
    assert no_worktree.isolation_policy.scratch_available is True

    preview = cases["adoption_preview_records_diff_without_applying"]
    assert preview.preview is not None
    assert preview.preview.applied is False
    assert preview.preview.changed_files == ("src/feature.py",)
    assert preview.preview.diff.diff_ref == "artifact:child-proposal-diff-1"
    assert preview.worktree_operation is not None
    assert preview.worktree_operation.action == "preview"
    assert preview.worktree_operation.changed_files == (
        "README.md",
        "src/feature.py",
        "src/new.py",
        "src/old.py",
    )
    assert preview.worktree_operation.created_files == ("src/new.py",)
    assert preview.worktree_operation.modified_files == ("src/feature.py",)
    assert preview.worktree_operation.deleted_files == ("src/old.py",)
    assert preview.worktree_operation.renamed_files[0].from_path == "src/old.py"
    assert preview.worktree_operation.renamed_files[0].to_path == "src/new.py"
    assert preview.worktree_operation.applied is False
    assert preview.worktree_operation.cleanup_executed is False
    assert projection.case_snapshots[preview.case_id]["preview"]["applied"] is False
    assert projection.case_snapshots[preview.case_id]["worktreeOperation"][
        "cleanupExecuted"
    ] is False

    apply_intent = cases["apply_intent_records_metadata_without_execution"]
    assert apply_intent.worktree_operation is not None
    assert apply_intent.worktree_operation.action == "apply"
    assert apply_intent.worktree_operation.merge_strategy == "copy"
    assert apply_intent.worktree_operation.adoption_intent is True
    assert apply_intent.worktree_operation.applied is False
    assert apply_intent.worktree_operation.cleanup_executed is False

    cherry_intent = cases["cherry_pick_intent_records_metadata_without_execution"]
    assert cherry_intent.worktree_operation is not None
    assert cherry_intent.worktree_operation.action == "cherry_pick"
    assert cherry_intent.worktree_operation.merge_strategy == "cherry_pick"
    assert cherry_intent.worktree_operation.adopted_commit_ref == "commit:redacted-child-1"
    assert cherry_intent.worktree_operation.applied is False

    rejected = cases["reject_records_disposition_without_cleanup_execution"]
    assert rejected.worktree_operation is not None
    assert rejected.worktree_operation.action == "reject"
    assert rejected.worktree_operation.disposition == "rejected_metadata_only"
    assert rejected.worktree_operation.cleanup_executed is False

    noop_apply = cases["noop_apply_records_unapplied_metadata"]
    assert noop_apply.worktree_operation is not None
    assert noop_apply.worktree_operation.action == "apply"
    assert noop_apply.worktree_operation.changed_files == ()
    assert noop_apply.worktree_operation.applied is False

    noop_cherry = cases["noop_cherry_pick_records_unapplied_metadata"]
    assert noop_cherry.worktree_operation is not None
    assert noop_cherry.worktree_operation.action == "cherry_pick"
    assert noop_cherry.worktree_operation.changed_files == ()
    assert noop_cherry.worktree_operation.applied is False

    dirty_conflict = cases["dirty_parent_conflict_denied"]
    assert dirty_conflict.decision == "deny"
    assert dirty_conflict.hard_safety is True
    assert dirty_conflict.fail_closed is True
    assert dirty_conflict.explicit_conflict_path is False
    assert dirty_conflict.dirty_parent_files == ("src/feature.py",)
    assert dirty_conflict.reason_codes == ("dirty_parent_overwrite",)
    assert dirty_conflict.conflict_review is not None
    assert dirty_conflict.conflict_review.conflict_kind == "parent_dirty"
    assert dirty_conflict.conflict_review.conflicted_files == ("src/feature.py",)
    assert dirty_conflict.conflict_review.preserves_child_worktree is True
    assert dirty_conflict.conflict_review.resolver_prompt_ref == (
        "artifact:conflict-review-parent-dirty-1"
    )
    assert dirty_conflict.conflict_review.resolver_spawn.metadata.source_tool == (
        "SpawnWorktreeApply"
    )
    assert projection.case_snapshots[dirty_conflict.case_id]["conflictReview"][
        "preservesChildWorktree"
    ] is True

    cherry_conflict = cases["cherry_pick_conflict_records_review_metadata"]
    assert cherry_conflict.decision == "deny"
    assert cherry_conflict.worktree_operation is not None
    assert cherry_conflict.worktree_operation.action == "cherry_pick"
    assert cherry_conflict.worktree_operation.applied is False
    assert cherry_conflict.conflict_review is not None
    assert cherry_conflict.conflict_review.conflict_kind == "cherry_pick"
    assert cherry_conflict.conflict_review.resolver_spawn.metadata.merge_strategy == (
        "cherry_pick"
    )
    assert cherry_conflict.conflict_review.resolver_spawn.metadata.adopted_commit_ref == (
        "commit:redacted-conflict-1"
    )

    rollback = cases["rollback_active_mutation_denied"]
    assert rollback.active_mutation is True
    assert rollback.decision == "deny"
    assert rollback.reason_codes == ("rollback_blocked_active_mutation",)
    assert rollback.evidence[0].kind == "rollback"

    child_only = cases["child_proposal_does_not_satisfy_parent_adoption"]
    assert child_only.evidence[0].kind == "child_proposal"
    assert child_only.evidence[0].satisfies_parent_adopted is False
    assert projection.case_snapshots[child_only.case_id]["parentAdoptedSatisfied"] is False

    parent_verified = cases["parent_verified_after_adoption_distinct"]
    assert tuple(item.kind for item in parent_verified.evidence) == (
        "parent_adoption",
        "parent_verification_after_adoption",
    )
    assert parent_verified.evidence[0].satisfies_parent_adopted is True
    assert parent_verified.evidence[1].satisfies_parent_verified_after_adoption is True
    assert projection.case_snapshots[parent_verified.case_id][
        "parentVerifiedAfterAdoptionSatisfied"
    ] is True

    external = cases["external_sandbox_imports_artifact_metadata"]
    assert external.external_import is not None
    assert external.external_import.raw_parent_workspace_mutation is False
    assert external.external_import.imported_artifact_refs == ("artifact:external-diff-1",)

    sealed = cases["sealed_path_mutation_denied"]
    assert sealed.decision == "deny"
    assert sealed.path_classification == "sealed_file"
    assert sealed.reason_codes == ("sealed_file_mutation_blocked",)

    escape = cases["workspace_escape_mutation_denied"]
    assert escape.path_classification == "outside_workspace"
    assert escape.reason_codes == ("path_escapes_workspace",)

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "/data/bots",
        "/workspace",
        "/var/lib/kubelet",
        "Bearer unsafe",
        "ghp_workspacesecret",
        "sk-workspace-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "raw patch",
        "diff --git",
        "--- a/",
        "+++ b/",
        "adkRunnerInvoked\": true",
        "fileMutated\": true",
        "patchApplied\": true",
        "workspaceMutated\": true",
        "liveAdoptionAttached\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"workspaceMutated": True}),
            id="fixture-workspace-mutated-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["attachmentFlags"].update(
                {"patchApplied": True}
            ),
            id="case-patch-applied-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["preview"].update({"applied": True}),
            id="preview-applied",
        ),
        pytest.param(
            lambda payload: _case_payload(payload, "dirty_parent_conflict_denied").update(
                {"decision": "preview_only"}
            ),
            id="dirty-conflict-not-denied",
        ),
        pytest.param(
            lambda payload: _case_payload(payload, "rollback_active_mutation_denied").update(
                {"decision": "metadata_only"}
            ),
            id="active-rollback-not-denied",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "external_sandbox_imports_artifact_metadata",
            )["externalImport"].update(
                {"rawParentWorkspaceMutation": True}
            ),
            id="external-raw-parent-mutation",
        ),
        pytest.param(
            lambda payload: _case_payload(payload, "sealed_path_mutation_denied").update(
                {"pathPreview": "/data/bots/bot-secret/AGENTS.md"}
            ),
            id="unsafe-production-path-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["isolationPolicy"].update(
                {"primaryMode": "version_control_worktree"}
            ),
            id="fallback-without-worktree",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "child_proposal_does_not_satisfy_parent_adoption",
            )["evidence"][0].update(
                {"satisfiesParentAdopted": True}
            ),
            id="child-proposal-satisfies-parent-adoption",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["preview"]["diff"].update(
                {"summary": "raw patch sk-workspace-secret"}
            ),
            id="unsafe-secret-shaped-diff-summary",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["worktreeOperation"].update(
                {"diffRef": "diff --git a/src/feature.py b/src/feature.py"}
            ),
            id="unsafe-raw-diff-ref",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["worktreeOperation"].update(
                {"changedFiles": ["/workspace/src/feature.py"]}
            ),
            id="unsafe-absolute-worktree-path",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["worktreeOperation"].update(
                {"changedFiles": ["pvc/src/feature.py"]}
            ),
            id="unsafe-pvc-worktree-path",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "reject_records_disposition_without_cleanup_execution",
            )["worktreeOperation"].update(
                {"cleanupExecuted": True}
            ),
            id="reject-cleanup-executed",
        ),
        pytest.param(
            lambda payload: _case_payload(payload, "dirty_parent_conflict_denied")[
                "conflictReview"
            ].update(
                {"conflictedFiles": ["/data/bots/bot-secret/src/feature.py"]}
            ),
            id="unsafe-conflict-review-path",
        ),
    ),
)
def test_workspace_adoption_preflight_rejects_live_flags_and_bad_metadata(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        WorkspaceAdoptionPreflightFixture.model_validate(payload)


def test_workspace_adoption_preflight_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = WorkspaceAdoptionPreflightAttachmentFlags.model_construct(
        adkRunnerInvoked=True,
        fileMutated=True,
        patchApplied=True,
        workspaceMutated=True,
        liveAdoptionAttached=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"workspaceMutated": True})


def test_workspace_adoption_preflight_import_boundary_stays_runtime_free() -> None:
    module_name = "openmagi_core_agent.shadow.workspace_adoption_preflight_contract"
    forbidden = (
        "google.adk",
        "openmagi_core_agent.adk_bridge",
        "openmagi_core_agent.tools.dispatcher",
        "openmagi_core_agent.tools.registry",
        "openmagi_core_agent.plugins.agentmemory",
        "openmagi_core_agent.memory",
        "openmagi_core_agent.services.memory",
        "openmagi_core_agent.hipocampus",
        "openmagi_core_agent.qmd",
        "openmagi_core_agent.app",
        "openmagi_core_agent.transport.chat",
        "openmagi_core_agent.routes",
    )
    removed_modules: dict[str, object] = {}
    for loaded_name in tuple(sys.modules):
        if (
            loaded_name == "openmagi_core_agent"
            or loaded_name.startswith("openmagi_core_agent.")
            or loaded_name == "google.adk"
            or loaded_name.startswith("google.adk.")
        ):
            removed = sys.modules.pop(loaded_name, None)
            if removed is not None:
                removed_modules[loaded_name] = removed

    try:
        module = importlib.import_module(module_name)
        fixture = module.load_workspace_adoption_preflight_fixture(
            "policy_matrix.json",
            fixture_root=FIXTURES,
        )
        module.project_workspace_adoption_preflight_fixture(fixture)

        loaded = [
            loaded_name
            for loaded_name in sorted(sys.modules)
            for forbidden_name in forbidden
            if loaded_name == forbidden_name
            or loaded_name.startswith(f"{forbidden_name}.")
        ]
        assert loaded == []
    finally:
        for loaded_name in tuple(sys.modules):
            if (
                loaded_name == "openmagi_core_agent"
                or loaded_name.startswith("openmagi_core_agent.")
                or loaded_name == "google.adk"
                or loaded_name.startswith("google.adk.")
            ):
                sys.modules.pop(loaded_name, None)
        sys.modules.update(removed_modules)
