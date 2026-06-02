from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.workspace.adoption_boundary import (
    WorkspaceChange,
    WorkspaceMutationBoundary,
    WorkspaceMutationConfig,
    WorkspaceMutationDecision,
    WorkspaceMutationRequest,
)


def _request(
    operation: str = "apply",
    *,
    changes: tuple[WorkspaceChange, ...] = (
        WorkspaceChange(path="src/app.py", action="modify"),
    ),
    base_revision: str = "rev-1",
    current_revision: str = "rev-1",
    dirty_parent_files: tuple[str, ...] = (),
    dry_run: bool = True,
    explicit_apply_approved: bool = False,
    explicit_conflict_resolution: bool = False,
    sealed_paths: tuple[str, ...] = (),
) -> WorkspaceMutationRequest:
    return WorkspaceMutationRequest(
        operation=operation,
        adoptionId="adoption-1",
        parentWorkspaceRef="workspace:parent",
        childWorkspaceRef="workspace:child",
        baseRevision=base_revision,
        currentRevision=current_revision,
        changes=changes,
        dirtyParentFiles=dirty_parent_files,
        dryRun=dry_run,
        explicitApplyApproved=explicit_apply_approved,
        explicitConflictResolution=explicit_conflict_resolution,
        sealedPaths=sealed_paths,
    )


def test_workspace_mutation_boundary_is_disabled_by_default() -> None:
    decision = WorkspaceMutationBoundary(WorkspaceMutationConfig()).evaluate(_request())

    assert decision.status == "disabled"
    assert decision.reason_codes == ("workspace_mutation_disabled",)
    projection = decision.public_projection()
    assert projection["authorityFlags"] == {
        "liveWorkspaceMutationAttached": False,
        "filesystemWriteAttempted": False,
        "gitApplyAttempted": False,
        "productionAuthority": False,
        "routeAttached": False,
    }
    assert projection["diagnosticMetadata"]["productionWorkspaceMutationEnabled"] is False
    assert projection["diagnosticMetadata"]["productionWritesEnabled"] is False


def test_workspace_mutation_boundary_records_preview_without_apply() -> None:
    decision = WorkspaceMutationBoundary(
        WorkspaceMutationConfig(enabled=True),
    ).evaluate(_request("preview"))

    assert decision.status == "preview"
    assert decision.changed_files == ("src/app.py",)
    assert decision.reason_codes == ("workspace_preview_only",)
    assert decision.public_projection()["authorityFlags"]["filesystemWriteAttempted"] is False


def test_workspace_mutation_boundary_requires_approval_before_apply() -> None:
    boundary = WorkspaceMutationBoundary(WorkspaceMutationConfig(enabled=True))

    dry_run = boundary.evaluate(_request("apply", dry_run=True))
    assert dry_run.status == "apply_intent"
    assert dry_run.reason_codes == ("workspace_dry_run_only",)

    unapproved = boundary.evaluate(_request("apply", dry_run=False))
    assert unapproved.status == "approval_required"
    assert unapproved.reason_codes == ("workspace_apply_requires_explicit_approval",)

    approved_without_fake = boundary.evaluate(
        _request("apply", dry_run=False, explicit_apply_approved=True),
    )
    assert approved_without_fake.status == "approval_required"
    assert approved_without_fake.reason_codes == ("live_workspace_apply_disabled",)


def test_workspace_mutation_boundary_can_issue_local_fake_apply_receipt_only() -> None:
    decision = WorkspaceMutationBoundary(
        WorkspaceMutationConfig(enabled=True, localFakeApplyEnabled=True),
    ).evaluate(_request("cherry_pick", dry_run=False, explicit_apply_approved=True))

    assert decision.status == "applied_local_fake"
    assert decision.reason_codes == ("local_fake_apply_receipt_only",)
    assert decision.receipt_ref.startswith("workspace-receipt:")
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_workspace_mutation_boundary_blocks_stale_dirty_and_sealed_paths() -> None:
    stale = WorkspaceMutationBoundary(WorkspaceMutationConfig(enabled=True)).evaluate(
        _request(base_revision="rev-1", current_revision="rev-2"),
    )
    assert stale.status == "conflict"
    assert "stale_workspace_revision" in stale.reason_codes

    dirty = WorkspaceMutationBoundary(WorkspaceMutationConfig(enabled=True)).evaluate(
        _request(dirty_parent_files=("src/app.py",)),
    )
    assert dirty.status == "conflict"
    assert dirty.conflict_paths == ("src/app.py",)
    assert "dirty_parent_overlap" in dirty.reason_codes

    explicit_conflict = WorkspaceMutationBoundary(
        WorkspaceMutationConfig(enabled=True),
    ).evaluate(
        _request(
            dirty_parent_files=("src/app.py",),
            explicit_conflict_resolution=True,
        ),
    )
    assert explicit_conflict.status == "conflict"
    assert "explicit_conflict_resolution_recorded" in explicit_conflict.reason_codes

    sealed = WorkspaceMutationBoundary(WorkspaceMutationConfig(enabled=True)).evaluate(
        _request(changes=(WorkspaceChange(path="TOOLS.md"),)),
    )
    assert sealed.status == "blocked"
    assert sealed.blocked_paths == ("TOOLS.md",)
    assert sealed.reason_codes == ("unsafe_or_sealed_path_blocked",)


def test_workspace_mutation_boundary_blocks_secret_like_paths() -> None:
    boundary = WorkspaceMutationBoundary(WorkspaceMutationConfig(enabled=True))

    for path in (
        ".env",
        "config/.env.local",
        "secrets/api-token.txt",
        "keys/private-key.pem",
        "certs/client.key",
        "credentials/service-account.json",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".kube/config",
        ".docker/config.json",
        "config/service-account.json",
    ):
        decision = boundary.evaluate(_request(changes=(WorkspaceChange(path=path),)))
        assert decision.status == "blocked"
        assert decision.blocked_paths == (path,)
        assert decision.reason_codes == ("unsafe_or_sealed_path_blocked",)


def test_workspace_mutation_boundary_validates_relative_paths() -> None:
    with pytest.raises(ValidationError):
        WorkspaceChange(path="../outside.py")
    with pytest.raises(ValidationError):
        WorkspaceChange(path="/Users/kevin/secret.py")
    with pytest.raises(ValidationError):
        _request(dirty_parent_files=("../escape.py",))


def test_workspace_mutation_decision_blocks_forged_authority_and_unsafe_paths() -> None:
    with pytest.raises(ValidationError):
        WorkspaceMutationDecision.model_construct(
            status="preview",
            operation="preview",
            adoptionId="adoption-1",
            changedFiles=("/Users/kevin/private.py",),
            reasonCodes=("forged",),
            receiptRef="workspace-receipt:forged",
            authorityFlags={
                "liveWorkspaceMutationAttached": True,
                "filesystemWriteAttempted": True,
                "gitApplyAttempted": True,
                "productionAuthority": True,
                "routeAttached": True,
            },
        )

    decision = WorkspaceMutationDecision(
        status="preview",
        operation="preview",
        adoptionId="adoption-1",
        changedFiles=("src/app.py",),
        reasonCodes=("workspace_preview_only",),
        receiptRef="workspace-receipt:safe",
        diagnosticMetadata={
            "rawPath": "/Users/kevin/private.py",
            "token": "ghp_workspaceSecret",
            "note": "public preview",
        },
    ).model_copy(
        update={
            "authorityFlags": {
                "filesystemWriteAttempted": True,
                "productionAuthority": True,
            },
        },
    )

    projection = decision.public_projection()
    assert projection["authorityFlags"]["filesystemWriteAttempted"] is False
    assert projection["authorityFlags"]["productionAuthority"] is False
    assert projection["diagnosticMetadata"] == {"note": "public preview"}
    assert "/Users/kevin" not in str(projection)
    assert "ghp_workspaceSecret" not in str(projection)


def test_workspace_mutation_boundary_has_no_live_runtime_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.workspace.adoption_boundary")
forbidden = (
    "subprocess",
    "git",
    "google.adk.runners",
    "google.adk.sessions",
    "magi_agent.runtime.runner",
    "magi_agent.toolhost.runtime",
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
