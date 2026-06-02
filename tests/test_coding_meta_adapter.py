from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.coding.meta_adapter import (
    CODING_META_ROLE_NAMES,
    CodingMetaEvidencePolicy,
    CodingMetaHarnessPlan,
    accept_coding_child_result,
    build_coding_meta_harness_plan,
)
from magi_agent.evidence.child_runtime_envelope import ChildRuntimeEnvelope
from runtime_issuance_support import issue_test_runtime_authority
from magi_agent.evidence.subagent import OPENMAGI_RUNTIME_ENVELOPE_ISSUER
from magi_agent.meta_orchestration.child_acceptance import issue_runtime_child_result
from magi_agent.meta_orchestration.child_roles import MetaChildRoleRegistry


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-coding-meta-adapter",
        scopes=scopes,
    )


def _policy(**updates: object) -> CodingMetaEvidencePolicy:
    payload: dict[str, object] = {
        "parentExecutionId": "parent:coding",
        "childExecutionId": "child:code-editor",
        "taskId": "task:code-editor",
        "parentPolicySnapshotId": "policy:coding",
        "childPolicySnapshotId": "policy:coding",
        "runtimeReceiptRef": "receipt:coding-child",
        "readEvidenceRefs": ("read:src-app-py:rev-3",),
        "diffEvidenceRefs": ("diff:src-app-py:rev-3",),
        "testEvidenceRefs": ("test:pytest:pass",),
        "checkpointEvidenceRefs": ("checkpoint:local-review",),
        "lastReadRevisionRef": "rev:3",
        "latestMutationRevisionRef": "rev:3",
        "testStatus": "pass",
        "maxRetryBudget": 1,
        "currentAttempt": 0,
    }
    payload.update(updates)
    return CodingMetaEvidencePolicy.model_validate(payload)


def _child_envelope(
    *,
    ledger_id: str = "diff:src-app-py:rev-3",
    audit_event_refs: tuple[str, ...] = (
        "read:src-app-py:rev-3",
        "test:pytest:pass",
        "checkpoint:local-review",
    ),
) -> ChildRuntimeEnvelope:
    return ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_runtime_envelope"),
        **{
            "issuer": OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
            "mode": "return",
            "status": "accepted",
            "parentBoundary": {
                "executionId": "parent:coding",
                "agentId": "agent:parent",
                "turnId": "turn:coding",
                "policyScope": "coding",
                "policySnapshotId": "policy:coding",
                "agentRole": "coding",
                "runOn": "main",
                "spawnDepth": 0,
            },
            "childBoundary": {
                "executionId": "child:code-editor",
                "agentId": "agent:child",
                "parentExecutionId": "parent:coding",
                "taskId": "task:code-editor",
                "turnId": "turn:coding",
                "policyScope": "coding",
                "policySnapshotId": "policy:coding",
                "agentRole": "coding",
                "runOn": "child",
                "spawnDepth": 1,
            },
            "task": {
                "taskId": "task:code-editor",
                "persona": "persona:code-editor",
                "role": "coding",
                "spawnDepth": 1,
                "deliver": "return",
                "promptRef": "prompt:code-editor",
            },
            "policySnapshot": {
                "parentPolicySnapshotId": "policy:coding",
                "childPolicySnapshotId": "policy:coding",
                "allowedToolNames": (),
                "permissionRefs": (),
                "callbackHookRefs": (),
            },
            "ledgerRef": {
                "ledgerId": ledger_id,
                "executionId": "child:code-editor",
                "agentId": "agent:child",
                "parentExecutionId": "parent:coding",
                "taskId": "task:code-editor",
                "policySnapshotId": "policy:coding",
            },
            "delegatedEvidenceRequirements": (),
            "workspaceIsolation": {
                "workspacePolicy": "git_worktree",
                "isolationRef": "isolation:coding",
                "parentWorkspaceRef": "workspace:parent",
                "childWorkspaceRef": "workspace:child",
                "descriptiveOnly": True,
            },
            "completionContract": {
                "requiredEvidence": "tool_call",
                "requiredFiles": (),
                "requireNonEmptyResult": True,
                "summaryIsEvidence": False,
                "acceptedEvidenceMetadataOnly": True,
            },
            "auditEventRefs": audit_event_refs,
            "adkPrimitiveOwnership": {
                "agentOwner": "adk_future_agent",
                "runnerOwner": "adk_future_runner",
                "eventOwner": "adk_event_bridge",
                "toolOwner": "adk_function_tool_future",
                "callbackOwner": "adk_callbacks_future",
                "allowedToolNames": (),
                "callbackHookRefs": (),
            },
            "authorityFlags": {},
        },
    )


def test_coding_adapter_composes_roles_child_specs_and_default_off_plan() -> None:
    plan = build_coding_meta_harness_plan(
        plan_id="plan:coding-meta",
        parent_execution_id="parent:coding",
        objective_digest="sha256:" + "a" * 64,
        objective_preview="Review and verify a local coding change using evidence refs.",
        evidence_policy=_policy(),
    )
    registry = MetaChildRoleRegistry(plan.role_definitions)

    assert tuple(plan.role_names) == CODING_META_ROLE_NAMES
    assert set(registry.role_refs()) == {child.role_ref for child in plan.task_plan.child_task_specs}
    assert len(plan.task_plan.child_task_specs) == 4
    assert plan.default_off is True
    assert set(plan.task_plan.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert all(child.requires_evidence_envelope for child in plan.task_plan.child_task_specs)
    assert "verifier:coding-evidence-policy" in plan.task_plan.verifier_chain_refs

    projection = plan.public_projection()
    assert projection["roleCount"] == 4
    assert projection["childTaskCount"] == 4
    assert projection["readEvidenceRefCount"] == 1
    assert projection["diffEvidenceRefCount"] == 1
    assert projection["testEvidenceRefCount"] == 1
    assert projection["checkpointEvidenceRefCount"] == 1
    assert projection["defaultOff"] is True
    assert "allowedToolRefs" not in projection


def test_coding_policy_requires_read_diff_test_and_checkpoint_refs() -> None:
    for field_name in (
        "readEvidenceRefs",
        "diffEvidenceRefs",
        "testEvidenceRefs",
        "checkpointEvidenceRefs",
    ):
        with pytest.raises(ValidationError):
            _policy(**{field_name: ()})

    with pytest.raises(ValidationError):
        _policy(readEvidenceRefs=("summary:child",))
    with pytest.raises(ValidationError):
        _policy(diffEvidenceRefs=("raw-diff:patch",))
    with pytest.raises(ValidationError):
        _policy(testEvidenceRefs=("test:pytest:failed",))
    with pytest.raises(ValidationError):
        _policy(checkpointEvidenceRefs=("checkpoint:/workspace/private",))


def test_stale_read_cannot_satisfy_edit_acceptance() -> None:
    verdict = accept_coding_child_result(
        issue_runtime_child_result(_child_envelope(), receipt_ref="receipt:coding-child"),
        _policy(lastReadRevisionRef="rev:1", latestMutationRevisionRef="rev:3"),
    )

    assert verdict.status == "retry"
    assert verdict.reason_codes == ("missing_required_evidence",)
    assert verdict.missing_evidence_refs == ("read:fresh-after-rev-3",)


def test_stale_or_failed_policy_still_requires_runtime_issued_child_result() -> None:
    stale = accept_coding_child_result(
        object(),
        _policy(lastReadRevisionRef="rev:1", latestMutationRevisionRef="rev:3"),
    )
    failed = accept_coding_child_result(
        object(),
        _policy(testStatus="failed", testEvidenceRefs=("test:pytest:pass",)),
    )

    assert stale.status == "rejected"
    assert stale.reason_codes == ("invalid_child_envelope",)
    assert failed.status == "rejected"
    assert failed.reason_codes == ("invalid_child_envelope",)


@pytest.mark.parametrize(
    ("policy_update", "expected_reason"),
    (
        ({"runtimeReceiptRef": "receipt:other-child"}, "runtime_receipt_mismatch"),
        ({"childExecutionId": "child:other-editor"}, "child_execution_mismatch"),
        ({"taskId": "task:other-editor"}, "task_mismatch"),
    ),
)
def test_coding_gates_preserve_generic_child_boundary_mismatches(
    policy_update: dict[str, object],
    expected_reason: str,
) -> None:
    verdict = accept_coding_child_result(
        issue_runtime_child_result(_child_envelope(), receipt_ref="receipt:coding-child"),
        _policy(
            lastReadRevisionRef="rev:1",
            latestMutationRevisionRef="rev:3",
            **policy_update,
        ),
    )

    assert verdict.status == "rejected"
    assert verdict.reason_codes == (expected_reason,)


def test_failed_test_blocks_completion_before_child_acceptance() -> None:
    verdict = accept_coding_child_result(
        issue_runtime_child_result(
            _child_envelope(audit_event_refs=("read:src-app-py:rev-3", "checkpoint:local-review")),
            receipt_ref="receipt:coding-child",
        ),
        _policy(testStatus="failed", testEvidenceRefs=("test:pytest:pass",)),
    )

    assert verdict.status == "blocked"
    assert verdict.reason_codes == ("child_blocked",)
    assert verdict.missing_evidence_refs == ("test:passing-required",)


def test_coding_child_acceptance_uses_policy_for_accept_retry_and_reject() -> None:
    accepted = accept_coding_child_result(
        issue_runtime_child_result(_child_envelope(), receipt_ref="receipt:coding-child"),
        _policy(),
    )
    retry = accept_coding_child_result(
        issue_runtime_child_result(
            _child_envelope(audit_event_refs=("read:src-app-py:rev-3", "test:pytest:pass")),
            receipt_ref="receipt:coding-child",
        ),
        _policy(),
    )
    rejected = accept_coding_child_result(
        issue_runtime_child_result(
            _child_envelope(audit_event_refs=("read:src-app-py:rev-3", "test:pytest:pass")),
            receipt_ref="receipt:coding-child",
        ),
        _policy(currentAttempt=1),
    )

    assert accepted.status == "accepted"
    assert accepted.accepted_evidence_refs == (
        "read:src-app-py:rev-3",
        "diff:src-app-py:rev-3",
        "test:pytest:pass",
        "checkpoint:local-review",
    )
    assert retry.status == "retry"
    assert retry.missing_evidence_refs == ("checkpoint:local-review",)
    assert rejected.status == "rejected"
    assert rejected.reason_codes == ("missing_required_evidence", "retry_budget_exhausted")


def test_coding_harness_rejects_smuggled_roles_tasks_and_policy_targets() -> None:
    plan = build_coding_meta_harness_plan(
        plan_id="plan:coding-meta",
        parent_execution_id="parent:coding",
        objective_digest="sha256:" + "a" * 64,
        objective_preview="Review and verify a local coding change using evidence refs.",
        evidence_policy=_policy(),
    )

    with pytest.raises(ValidationError):
        build_coding_meta_harness_plan(
            plan_id="plan:coding-meta",
            parent_execution_id="parent:coding",
            objective_digest="sha256:" + "a" * 64,
            objective_preview="Review and verify a local coding change using evidence refs.",
            evidence_policy=_policy(taskId="task:outside-coding-plan"),
        )

    forged_role = plan.role_definitions[0].model_copy(
        update={"completionContractRef": "contract:forged-coding-role"},
    )
    with pytest.raises(ValidationError):
        CodingMetaHarnessPlan.model_validate(
            {
                **plan.model_dump(by_alias=True, mode="python", warnings=False),
                "roleDefinitions": (forged_role, *plan.role_definitions[1:]),
            }
        )

    forged_child = plan.task_plan.child_task_specs[0].model_copy(
        update={"scopeRef": "scope:smuggled"},
    )
    forged_task_plan = plan.task_plan.model_copy(
        update={"childTaskSpecs": (forged_child, *plan.task_plan.child_task_specs[1:])},
    )
    with pytest.raises(ValidationError):
        CodingMetaHarnessPlan.model_validate(
            {
                **plan.model_dump(by_alias=True, mode="python", warnings=False),
                "taskPlan": forged_task_plan,
            }
        )


def test_generic_meta_modules_do_not_import_coding_adapter() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    meta_dir = repo_root / "magi_agent" / "meta_orchestration"

    for path in meta_dir.glob("*.py"):
        source = path.read_text()
        assert "magi_agent.coding.meta_adapter" not in source, path
        assert "from magi_agent.coding import meta_adapter" not in source, path
        assert "code_reader" not in source, path
        assert "code_editor" not in source, path
        assert "test_runner" not in source, path
        assert "code_reviewer" not in source, path
