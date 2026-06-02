from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    ChildRuntimeEnvelopeAuthorityFlags,
    PublicChildRuntimeEnvelopeProjection,
    load_child_runtime_envelope_fixture,
    project_child_runtime_envelope,
    runtime_envelope_satisfies_delegated_evidence_metadata,
)
from runtime_issuance_support import issue_test_runtime_authority
from openmagi_core_agent.evidence.subagent import (
    DelegatedEvidenceRequirement,
    EvidenceBoundaryLedgerRef,
    ExecutionBoundaryIdentity,
    PolicySnapshotCompatibility,
    natural_language_summary_as_evidence,
)


FIXTURES = Path(__file__).parent / "fixtures" / "child_runtime_envelope"


def _parent_boundary(**overrides: object) -> ExecutionBoundaryIdentity:
    payload = {
        "executionId": "parent-exec-1",
        "agentId": "parent-agent",
        "turnId": "turn-1",
        "policyScope": "coding",
        "policySnapshotId": "policy-parent-1",
        "agentRole": "coding",
        "runOn": "main",
        "spawnDepth": 0,
    }
    payload.update(overrides)
    return ExecutionBoundaryIdentity.model_validate(payload)


def _child_boundary(**overrides: object) -> ExecutionBoundaryIdentity:
    payload = {
        "executionId": "child-exec-1",
        "agentId": "child-agent",
        "parentExecutionId": "parent-exec-1",
        "taskId": "task-1",
        "turnId": "turn-1",
        "policyScope": "coding",
        "policySnapshotId": "policy-parent-1",
        "agentRole": "coding",
        "runOn": "child",
        "spawnDepth": 1,
    }
    payload.update(overrides)
    return ExecutionBoundaryIdentity.model_validate(payload)


def _ledger_ref(
    child: ExecutionBoundaryIdentity | None = None,
    **overrides: object,
) -> EvidenceBoundaryLedgerRef:
    boundary = child or _child_boundary()
    payload = {
        "ledgerId": f"ledger:{boundary.execution_id}",
        "executionId": boundary.execution_id,
        "agentId": boundary.agent_id,
        "parentExecutionId": boundary.parent_execution_id,
        "taskId": boundary.task_id,
        "policySnapshotId": boundary.policy_snapshot_id,
        "childLedgerRefs": (),
    }
    payload.update(overrides)
    return EvidenceBoundaryLedgerRef.model_validate(payload)


def _payload(**overrides: object) -> dict[str, object]:
    parent = _parent_boundary()
    child = _child_boundary()
    payload: dict[str, object] = {
        "issuer": "openmagi_runtime_boundary",
        "mode": "return",
        "status": "accepted",
        "parentBoundary": parent,
        "childBoundary": child,
        "task": {
            "taskId": "task-1",
            "persona": "coding",
            "role": "coding",
            "spawnDepth": 1,
            "deliver": "return",
            "promptRef": "prompt:task-1",
        },
        "policySnapshot": {
            "parentPolicySnapshotId": "policy-parent-1",
            "childPolicySnapshotId": "policy-parent-1",
            "taskLocalPolicyCompatibilityRefs": (),
            "allowedToolNames": ("FileRead", "Bash"),
            "permissionRefs": ("permission:read-only",),
            "callbackHookRefs": ("callback:before-tool-policy",),
        },
        "ledgerRef": _ledger_ref(child),
        "delegatedEvidenceRequirements": (
            DelegatedEvidenceRequirement(type="TestRun", delegation="delegated_required"),
        ),
        "workspaceIsolation": {
            "workspacePolicy": "trusted",
            "isolationRef": "workspace-isolation:task-1",
            "parentWorkspaceRef": "workspace:parent-redacted",
            "childWorkspaceRef": "workspace:child-redacted",
            "descriptiveOnly": True,
            "adoptionAttached": False,
            "workspaceMutated": False,
            "privateNotes": (
                "internal path /data/bots/bot-1/workspace and Bearer unsafe-token",
            ),
        },
        "completionContract": {
            "requiredEvidence": "tool_call",
            "requiredFiles": (),
            "requireNonEmptyResult": True,
            "summaryIsEvidence": False,
            "acceptedEvidenceMetadataOnly": True,
        },
        "auditEventRefs": ("audit:child-spawn-planned", "audit:child-envelope-issued"),
        "adkPrimitiveOwnership": {
            "agentOwner": "adk_future_agent",
            "runnerOwner": "adk_future_runner",
            "eventOwner": "adk_event_bridge",
            "toolOwner": "adk_function_tool_future",
            "callbackOwner": "adk_callbacks_future",
            "runnerAttached": False,
            "childExecutionAttached": False,
            "allowedToolNames": ("FileRead", "Bash"),
            "callbackHookRefs": ("callback:before-tool-policy",),
        },
        "authorityFlags": ChildRuntimeEnvelopeAuthorityFlags(),
        "rawTranscriptRef": "transcript:private-child-turn",
        "privateMetadata": {
            "rawTranscriptPreview": "raw child transcript with sk-child-secret",
            "workspacePath": "/workspace/bot/private",
            "authorization": "Bearer unsafe-token",
        },
    }
    payload.update(overrides)
    return payload


def _envelope(**overrides: object) -> ChildRuntimeEnvelope:
    return ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=issue_test_runtime_authority(
            authority_id="authority:test-child-envelope",
            scopes=("child_runtime_envelope",),
        ),
        **_payload(**overrides),
    )


def test_structural_child_envelope_payload_is_not_runtime_issued_evidence() -> None:
    envelope = ChildRuntimeEnvelope.model_validate(_payload())

    assert runtime_envelope_satisfies_delegated_evidence_metadata(envelope) is False


def test_accepts_runtime_issued_metadata_only_child_envelope() -> None:
    envelope = _envelope()

    assert envelope.issuer == "openmagi_runtime_boundary"
    assert envelope.parent_boundary.execution_id == "parent-exec-1"
    assert envelope.child_boundary.parent_execution_id == "parent-exec-1"
    assert envelope.task.task_id == "task-1"
    assert envelope.adk_primitive_ownership.runner_attached is False
    assert envelope.authority_flags.child_execution_attached is False
    assert runtime_envelope_satisfies_delegated_evidence_metadata(envelope) is True


@pytest.mark.parametrize(
    "flag_name",
    (
        "runnerAttached",
        "childExecutionAttached",
        "toolHostDispatched",
        "workspaceMutated",
        "missionStoreWritten",
        "backgroundRuntimeAttached",
        "memoryProviderCalled",
        "routeAttached",
        "productionAuthority",
        "evidenceBlockEnabled",
    ),
)
def test_rejects_any_true_live_authority_flag(flag_name: str) -> None:
    payload = _payload()
    payload["authorityFlags"] = {flag_name: True}

    with pytest.raises(ValidationError):
        ChildRuntimeEnvelope.model_validate(payload)


def test_authority_flags_model_construct_cannot_forge_internal_true_state() -> None:
    flags = ChildRuntimeEnvelopeAuthorityFlags.model_construct(
        runner_attached=True,
        child_execution_attached=True,
        tool_host_dispatched=True,
        workspace_mutated=True,
        mission_store_written=True,
        background_runtime_attached=True,
        memory_provider_called=True,
        route_attached=True,
        production_authority=True,
        evidence_block_enabled=True,
    )

    assert flags.runner_attached is False
    assert flags.child_execution_attached is False
    assert flags.tool_host_dispatched is False
    assert flags.workspace_mutated is False
    assert flags.mission_store_written is False
    assert flags.background_runtime_attached is False
    assert flags.memory_provider_called is False
    assert flags.route_attached is False
    assert flags.production_authority is False
    assert flags.evidence_block_enabled is False
    assert set(flags.model_dump(by_alias=True).values()) == {False}


def test_rejects_child_authored_json_and_natural_language_as_delegated_evidence() -> None:
    with pytest.raises(ValidationError, match="runtime-issued"):
        _envelope(issuer="child_authored_json")

    match = natural_language_summary_as_evidence("The child says tests passed.")
    assert match.satisfied is False
    assert runtime_envelope_satisfies_delegated_evidence_metadata(
        {"summary": "The child says tests passed.", "status": "ok"}
    ) is False


@pytest.mark.parametrize(
    "override",
    (
        {"childBoundary": _child_boundary(parentExecutionId="other-parent")},
        {"childBoundary": _child_boundary(taskId="other-task")},
        {"childBoundary": _child_boundary(executionId="other-child")},
    ),
)
def test_rejects_parent_child_boundary_mismatch(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChildRuntimeEnvelope.model_validate(_payload(**override))


@pytest.mark.parametrize(
    "override",
    (
        {"ledgerRef": _ledger_ref(executionId="forged-child")},
        {"ledgerRef": _ledger_ref(taskId="forged-task")},
        {"ledgerRef": _ledger_ref(policySnapshotId="forged-policy")},
        {
            "policySnapshot": {
                "parentPolicySnapshotId": "policy-parent-1",
                "childPolicySnapshotId": "forged-child-policy",
                "taskLocalPolicyCompatibilityRefs": (),
                "allowedToolNames": ("FileRead",),
                "permissionRefs": (),
                "callbackHookRefs": (),
            }
        },
    ),
)
def test_rejects_forged_policy_snapshot_ledger_ref_task_id_or_execution_id(
    override: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ChildRuntimeEnvelope.model_validate(_payload(**override))


def test_accepts_task_local_policy_snapshot_only_with_matching_compatibility_ref() -> None:
    compatibility = PolicySnapshotCompatibility(
        parentPolicySnapshotId="policy-parent-1",
        childPolicySnapshotId="policy-child-task-local",
        childExecutionId="child-exec-1",
        taskId="task-1",
        reason="task_local_contracts",
    )
    child = _child_boundary(policySnapshotId="policy-child-task-local")
    envelope = ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=issue_test_runtime_authority(
            authority_id="authority:test-child-envelope",
            scopes=("child_runtime_envelope",),
        ),
        **_payload(
            childBoundary=child,
            ledgerRef=_ledger_ref(child),
            policySnapshot={
                "parentPolicySnapshotId": "policy-parent-1",
                "childPolicySnapshotId": "policy-child-task-local",
                "taskLocalPolicyCompatibilityRefs": (compatibility,),
                "allowedToolNames": ("FileRead",),
                "permissionRefs": (),
                "callbackHookRefs": (),
            },
        ),
    )

    assert envelope.policy_snapshot.child_policy_snapshot_id == "policy-child-task-local"
    assert envelope.policy_snapshot.task_local_policy_compatibility_refs == (compatibility,)


def test_workspace_isolation_metadata_is_descriptive_only() -> None:
    envelope = _envelope(
        workspaceIsolation={
            "workspacePolicy": "git_worktree",
            "isolationRef": "workspace-isolation:task-1",
            "parentWorkspaceRef": "workspace:parent-redacted",
            "childWorkspaceRef": "workspace:child-redacted",
            "descriptiveOnly": True,
            "adoptionAttached": False,
            "workspaceMutated": False,
            "privateNotes": ("would use git worktree later",),
        }
    )

    assert envelope.workspace_isolation.workspace_policy == "git_worktree"
    assert envelope.workspace_isolation.descriptive_only is True
    assert envelope.workspace_isolation.workspace_mutated is False

    with pytest.raises(ValidationError):
        _envelope(
            workspaceIsolation={
                "workspacePolicy": "git_worktree",
                "isolationRef": "workspace-isolation:task-1",
                "parentWorkspaceRef": "workspace:parent-redacted",
                "childWorkspaceRef": "workspace:child-redacted",
                "descriptiveOnly": False,
                "adoptionAttached": False,
                "workspaceMutated": False,
            }
        )


def test_background_child_envelope_has_audit_refs_without_runtime_attachment() -> None:
    child = _child_boundary(agentRole="research")
    envelope = _envelope(
        mode="background",
        status="accepted",
        childBoundary=child,
        ledgerRef=_ledger_ref(child),
        task={
            "taskId": "task-1",
            "persona": "research",
            "role": "research",
            "spawnDepth": 1,
            "deliver": "background",
            "promptRef": "prompt:task-1",
        },
        auditEventRefs=("audit:background-child-planned", "audit:background-envelope-issued"),
    )

    assert envelope.mode == "background"
    assert envelope.audit_event_refs == (
        "audit:background-child-planned",
        "audit:background-envelope-issued",
    )
    assert envelope.authority_flags.mission_store_written is False
    assert envelope.authority_flags.background_runtime_attached is False

    with pytest.raises(ValidationError, match="auditEventRefs"):
        _envelope(
            mode="background",
            childBoundary=child,
            ledgerRef=_ledger_ref(child),
            task={
                "taskId": "task-1",
                "persona": "research",
                "role": "research",
                "spawnDepth": 1,
                "deliver": "background",
                "promptRef": "prompt:task-1",
            },
            auditEventRefs=(),
        )


def test_public_projection_strips_unsafe_paths_secrets_and_raw_transcript() -> None:
    projection = project_child_runtime_envelope(_envelope())

    assert isinstance(projection, PublicChildRuntimeEnvelopeProjection)
    dumped = json.dumps(projection.model_dump(by_alias=True), sort_keys=True)
    unsafe_fragments = (
        "/data/bots",
        "/workspace",
        "Bearer unsafe-token",
        "sk-child-secret",
        "raw child transcript",
        "rawTranscriptRef",
        "privateMetadata",
        "rawTranscriptPreview",
    )
    for fragment in unsafe_fragments:
        assert fragment not in dumped
    assert projection.authority_flags.runner_attached is False
    assert projection.workspace_isolation["workspacePolicy"] == "trusted"


def test_public_projection_redacts_private_path_identifiers() -> None:
    private_parent = "/" + "Users/kevin/private/parent-exec"
    private_child = "/" + "Users/kevin/private/child-exec"
    private_ledger = "/" + "Users/kevin/private/ledger"
    parent = _parent_boundary(executionId=private_parent)
    child = _child_boundary(
        executionId=private_child,
        parentExecutionId=private_parent,
    )
    envelope = _envelope(
        parentBoundary=parent,
        childBoundary=child,
        ledgerRef=_ledger_ref(
            child,
            ledgerId=private_ledger,
            childLedgerRefs=(private_ledger + "/nested",),
        ),
    )

    dumped = json.dumps(
        project_child_runtime_envelope(envelope).model_dump(by_alias=True),
        sort_keys=True,
    )

    assert private_parent not in dumped
    assert private_child not in dumped
    assert private_ledger not in dumped
    assert "/Users/" not in dumped


def test_public_projection_redacts_single_component_private_path_identifiers() -> None:
    parent = _parent_boundary(executionId="/workspace")
    child = _child_boundary(
        executionId="/data",
        parentExecutionId="/workspace",
        taskId="/etc",
    )
    envelope = _envelope(
        parentBoundary=parent,
        childBoundary=child,
        task={
            "taskId": "/etc",
            "persona": "coding",
            "role": "coding",
            "spawnDepth": 1,
            "deliver": "return",
            "promptRef": "prompt:task-1",
        },
        ledgerRef=_ledger_ref(
            child,
            ledgerId="/workspace",
            childLedgerRefs=("/data",),
        ),
    )

    dumped = json.dumps(
        project_child_runtime_envelope(envelope).model_dump(by_alias=True),
        sort_keys=True,
    )

    assert "/workspace" not in dumped
    assert "/data" not in dumped
    assert "/etc" not in dumped


def test_public_projection_redacts_private_path_audit_refs() -> None:
    envelope = _envelope(
        auditEventRefs=(
            "/" + "workspace/private/audit",
            "/" + "data",
            "audit:child-envelope-issued",
        ),
    )

    projection = project_child_runtime_envelope(envelope)
    dumped = json.dumps(projection.model_dump(by_alias=True), sort_keys=True)

    assert "/workspace" not in dumped
    assert "/data" not in dumped
    assert "audit:child-envelope-issued" in projection.audit_event_refs


def test_public_projection_redacts_windows_and_unc_persona_paths() -> None:
    windows_path = "C:" + "\\Users\\kevin\\secret\\persona.txt"
    unc_path = "\\\\" + "host\\share\\persona.txt"
    envelope = _envelope(
        task={
            "taskId": "task-1",
            "persona": f"reviewer {windows_path} {unc_path}",
            "role": "coding",
            "spawnDepth": 1,
            "deliver": "return",
            "promptRef": "prompt:task-1",
        }
    )

    dumped = json.dumps(
        project_child_runtime_envelope(envelope).model_dump(by_alias=True),
        sort_keys=True,
    )

    assert windows_path not in dumped
    assert unc_path not in dumped
    assert "C:" not in dumped
    assert "\\\\host" not in dumped


def test_fixture_covers_return_background_blocked_workspace_and_task_local_policy() -> None:
    fixture = load_child_runtime_envelope_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    assert fixture.fixture_id == "child_runtime_envelope_matrix_0001"
    assert fixture.local_diagnostic is True
    assert fixture.case_order == (
        "return_child_runtime_envelope",
        "background_child_runtime_envelope",
        "blocked_child_runtime_envelope",
        "workspace_isolated_child_runtime_envelope",
        "task_local_policy_snapshot_child_runtime_envelope",
    )
    assert fixture.by_mode == {"return": 3, "background": 1, "blocked": 1}
    assert fixture.no_live_execution is True
    assert set(fixture.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_import_boundary_has_no_adk_runner_routes_toolhost_memory_or_workspace_mutation() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.evidence.child_runtime_envelope")
assert hasattr(module, "ChildRuntimeEnvelope")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.tool_adapter",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.tools.registry",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.workspace.mutation",
    "openmagi_core_agent.workspace.adoption",
    "openmagi_core_agent.shadow.mission_lifecycle_contract",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"child runtime envelope import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_evidence_package_lazy_export_for_child_runtime_envelope() -> None:
    evidence = importlib.import_module("openmagi_core_agent.evidence")

    assert evidence.ChildRuntimeEnvelope is ChildRuntimeEnvelope
