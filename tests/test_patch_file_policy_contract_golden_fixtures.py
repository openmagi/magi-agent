from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.shadow.patch_file_policy_contract import (
    PatchFilePolicyAttachmentFlags,
    PatchFilePolicyContractFixture,
    load_patch_file_policy_contract_fixture,
    project_patch_file_policy_contract_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "patch_file_policy"


def test_patch_file_policy_contract_fixture_covers_file_and_patch_decisions() -> None:
    fixture = load_patch_file_policy_contract_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_patch_file_policy_contract_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "patch_file_policy_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.case_order == (
        "file_read_allowed_inside_workspace",
        "file_read_workspace_escape_denied",
        "file_read_protected_memory_denied",
        "file_write_requires_approval",
        "file_write_sealed_denied",
        "file_edit_stale_version_mismatch",
        "file_edit_dry_run_preflight_success",
        "file_edit_workspace_escape_denied",
        "patch_apply_dry_run_preflight_success",
        "patch_apply_sealed_denied",
        "patch_apply_path_traversal_rejected",
        "patch_apply_requires_approval",
    )
    assert projection.by_decision == {
        "allow": 1,
        "deny": 5,
        "approval_required": 2,
        "dry_run_only": 2,
        "preflight_failed": 2,
    }
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_execution is True

    allowed_read = cases["file_read_allowed_inside_workspace"]
    assert allowed_read.tool.name == "FileRead"
    assert allowed_read.tool.kind == "core"
    assert allowed_read.tool.source.kind == "builtin"
    assert allowed_read.permission_class == "read"
    assert allowed_read.decision == "allow"
    assert allowed_read.path_classification == "workspace_safe"
    assert allowed_read.normalized_workspace_relative == "src/app.ts"
    assert allowed_read.result_status == "ok"
    assert allowed_read.mutates_workspace is False
    assert allowed_read.dangerous is False

    escape_read = cases["file_read_workspace_escape_denied"]
    assert escape_read.decision == "deny"
    assert escape_read.path_classification == "outside_workspace"
    assert escape_read.reason_codes == ("path_escapes_workspace",)
    assert escape_read.hard_safety is True
    assert escape_read.security_critical is True
    assert escape_read.fail_closed is True

    protected_read = cases["file_read_protected_memory_denied"]
    assert protected_read.protected_path is True
    assert protected_read.path_classification == "protected_memory"
    assert protected_read.reason_codes == ("protected_memory_path",)

    write_approval = cases["file_write_requires_approval"]
    assert write_approval.tool.name == "FileWrite"
    assert write_approval.decision == "approval_required"
    assert write_approval.control_request is not None
    assert projection.control_requests["file_write_requires_approval"] == {
        "requestId": "tool-permission:turn-patch-file-1:FileWrite",
        "turnId": "turn-patch-file-1",
        "toolName": "FileWrite",
        "reason": "workspace mutation requires approval",
    }

    sealed_write = cases["file_write_sealed_denied"]
    assert sealed_write.sealed_path is True
    assert sealed_write.decision == "deny"
    assert sealed_write.reason_codes == ("sealed_file_write_blocked",)

    stale_edit = cases["file_edit_stale_version_mismatch"]
    assert stale_edit.tool.name == "FileEdit"
    assert stale_edit.decision == "preflight_failed"
    assert stale_edit.result_status == "error"
    assert stale_edit.preflight is not None
    assert stale_edit.preflight.version_mismatch is True
    assert stale_edit.preflight.expected_sha256 == "0" * 64
    assert stale_edit.preflight.current_sha256 == "1" * 64

    edit_dry_run = cases["file_edit_dry_run_preflight_success"]
    assert edit_dry_run.decision == "dry_run_only"
    assert edit_dry_run.preflight is not None
    assert edit_dry_run.preflight.dry_run is True
    assert edit_dry_run.preflight.preflight_passed is True
    assert edit_dry_run.preflight.changed_files == ("src/app.ts",)
    assert edit_dry_run.attachment_flags.file_mutated is False

    edit_escape = cases["file_edit_workspace_escape_denied"]
    assert edit_escape.decision == "deny"
    assert edit_escape.path_classification == "outside_workspace"

    patch_dry_run = cases["patch_apply_dry_run_preflight_success"]
    assert patch_dry_run.tool.name == "PatchApply"
    assert patch_dry_run.decision == "dry_run_only"
    assert patch_dry_run.preflight is not None
    assert patch_dry_run.preflight.dry_run is True
    assert patch_dry_run.preflight.hunks == 1
    assert patch_dry_run.preflight.changed_files == ("src/app.ts",)

    patch_sealed = cases["patch_apply_sealed_denied"]
    assert patch_sealed.decision == "deny"
    assert patch_sealed.sealed_path is True
    assert patch_sealed.reason_codes == ("sealed_file_write_blocked",)

    patch_traversal = cases["patch_apply_path_traversal_rejected"]
    assert patch_traversal.decision == "preflight_failed"
    assert patch_traversal.path_classification == "absolute_production_path"
    assert patch_traversal.reason_codes == ("patch_path_traversal",)

    patch_approval = cases["patch_apply_requires_approval"]
    assert patch_approval.decision == "approval_required"
    assert patch_approval.control_request is not None
    assert projection.control_requests["patch_apply_requires_approval"] == {
        "requestId": "tool-permission:turn-patch-file-1:PatchApply",
        "turnId": "turn-patch-file-1",
        "toolName": "PatchApply",
        "reason": "patch workspace mutation requires approval",
    }

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "/data/bots",
        "/workspace",
        "/var/lib/kubelet",
        "Bearer unsafe",
        "ghp_patchsecret",
        "sk-patch-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "private tool args",
        "pythonResponseAuthority",
        "adkRunnerInvoked\": true",
        "fileMutated\": true",
        "patchApplied\": true",
        "workspaceWritten\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json

    assert projection.public_previews["file_read_allowed_inside_workspace"] == "path=src/app.ts"
    assert projection.public_previews["file_read_workspace_escape_denied"] == (
        "path=[outside-workspace]"
    )
    assert projection.public_previews["patch_apply_path_traversal_rejected"] == (
        "patch path=[redacted-path]"
    )
    assert projection.case_snapshots["file_edit_stale_version_mismatch"]["preflight"][
        "versionMismatch"
    ] is True
    assert projection.case_snapshots["patch_apply_dry_run_preflight_success"]["preflight"][
        "dryRun"
    ] is True


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"fileMutated": True}),
            id="file-mutated-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][8]["attachmentFlags"].update(
                {"patchApplied": True}
            ),
            id="patch-applied-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][1].update(
                {"requestedPathPreview": "/data/bots/bot-secret/workspace/secret.txt"}
            ),
            id="unsafe-production-path-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][3].pop("controlRequest"),
            id="approval-without-control-request",
        ),
        pytest.param(
            lambda payload: payload["cases"][5]["preflight"].update(
                {"expectedSha256": "not-a-sha"}
            ),
            id="bad-stale-sha",
        ),
    ),
)
def test_patch_file_policy_contract_rejects_live_flags_unsafe_paths_and_bad_metadata(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        PatchFilePolicyContractFixture.model_validate(payload)


def test_patch_file_policy_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = PatchFilePolicyAttachmentFlags.model_construct(
        fileMutated=True,
        patchApplied=True,
        adkRunnerInvoked=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"fileMutated": True})


def test_patch_file_policy_contract_import_boundary_stays_runtime_free() -> None:
    code = """
import sys
from pathlib import Path

from openmagi_core_agent.shadow.patch_file_policy_contract import (
    load_patch_file_policy_contract_fixture,
    project_patch_file_policy_contract_fixture,
)

fixture_root = Path('tests/fixtures/patch_file_policy')
fixture = load_patch_file_policy_contract_fixture('policy_matrix.json', fixture_root=fixture_root)
project_patch_file_policy_contract_fixture(fixture)

forbidden = (
    'google.adk.runners',
    'openmagi_core_agent.adk_bridge.local_runner',
    'openmagi_core_agent.adk_bridge.runner_adapter',
    'openmagi_core_agent.adk_bridge.tool_adapter',
    'openmagi_core_agent.tools.dispatcher',
    'openmagi_core_agent.tools.registry',
    'openmagi_core_agent.plugins.agentmemory',
    'openmagi_core_agent.memory',
    'openmagi_core_agent.services.memory',
    'openmagi_core_agent.hipocampus',
    'openmagi_core_agent.qmd',
    'openmagi_core_agent.app',
    'openmagi_core_agent.transport.chat',
    'openmagi_core_agent.routes',
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f'forbidden modules loaded: {loaded}')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
