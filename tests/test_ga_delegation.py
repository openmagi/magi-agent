"""Track 19 PR10 — GA scoped delegation returns a receipt-backed child envelope.

These tests exercise the delegation seam that EXTENDS the existing child-
acceptance + child-runtime-envelope machinery:

* the envelope is a real runtime-issued
  :class:`magi_agent.evidence.child_runtime_envelope.ChildRuntimeEnvelope`,
* acceptance runs through the existing
  :func:`magi_agent.meta_orchestration.child_acceptance.accept_real_child_envelope`
  token-validated path (NOT a bare text return),
* the depth cap reuses
  :data:`magi_agent.harness.goal_loop.DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH` (=2),
* the flag is the existing ``MAGI_GA_LIVE_ENABLED`` master switch, and
* no child-execution authority flag is flipped to ``True``.
"""
from __future__ import annotations

import json

import pytest

from magi_agent.config.env import general_automation_live_enabled
from magi_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    ChildRuntimeEnvelopeAuthorityFlags,
)
from magi_agent.evidence.subagent import (
    DelegatedEvidenceRequirement,
    EvidenceBoundaryLedgerRef,
    ExecutionBoundaryIdentity,
)
from magi_agent.harness.general_automation.delegation import (
    GeneralAutomationDelegationOutcome,
    GeneralAutomationDelegationRequest,
    build_general_automation_delegation,
)
from magi_agent.harness.goal_loop import DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH
from magi_agent.meta_orchestration.child_acceptance import (
    ChildAcceptancePolicy,
    ChildAcceptanceVerdict,
)
from magi_agent.tools.context import ToolContext
from runtime_issuance_support import issue_test_runtime_authority


_FLAG = "MAGI_GA_LIVE_ENABLED"


def _on_env() -> dict[str, str]:
    return {_FLAG: "1"}


def _off_env() -> dict[str, str]:
    return {_FLAG: "0"}


def _general_context() -> ToolContext:
    return ToolContext.model_validate(
        {
            "botId": "bot-ga-delegation",
            "workspaceRoot": "/workspace",
            "executionContract": {"agentRole": "general"},
        }
    )


def _coding_context() -> ToolContext:
    return ToolContext.model_validate(
        {
            "botId": "bot-ga-delegation",
            "workspaceRoot": "/workspace",
            "executionContract": {"agentRole": "coding"},
        }
    )


def _parent_boundary() -> ExecutionBoundaryIdentity:
    return ExecutionBoundaryIdentity.model_validate(
        {
            "executionId": "ga-parent-exec-1",
            "agentId": "ga-parent-agent",
            "turnId": "turn-1",
            "policyScope": "general",
            "policySnapshotId": "policy-ga-parent-1",
            "agentRole": "general",
            "runOn": "main",
            "spawnDepth": 0,
        }
    )


def _child_boundary(spawn_depth: int = 1) -> ExecutionBoundaryIdentity:
    return ExecutionBoundaryIdentity.model_validate(
        {
            "executionId": "ga-child-exec-1",
            "agentId": "ga-child-agent",
            "parentExecutionId": "ga-parent-exec-1",
            "taskId": "ga-task-1",
            "turnId": "turn-1",
            "policyScope": "general",
            "policySnapshotId": "policy-ga-parent-1",
            "agentRole": "general",
            "runOn": "child",
            "spawnDepth": spawn_depth,
        }
    )


def _ledger_ref(child: ExecutionBoundaryIdentity) -> EvidenceBoundaryLedgerRef:
    return EvidenceBoundaryLedgerRef.model_validate(
        {
            "ledgerId": f"ledger:{child.execution_id}",
            "executionId": child.execution_id,
            "agentId": child.agent_id,
            "parentExecutionId": child.parent_execution_id,
            "taskId": child.task_id,
            "policySnapshotId": child.policy_snapshot_id,
            "childLedgerRefs": (),
        }
    )


def _runtime_envelope(spawn_depth: int = 1) -> ChildRuntimeEnvelope:
    parent = _parent_boundary()
    child = _child_boundary(spawn_depth)
    payload: dict[str, object] = {
        "issuer": "openmagi_runtime_boundary",
        "mode": "return",
        "status": "accepted",
        "parentBoundary": parent,
        "childBoundary": child,
        "task": {
            "taskId": "ga-task-1",
            "persona": "general",
            "role": "general",
            "spawnDepth": spawn_depth,
            "deliver": "return",
            "promptRef": "prompt:ga-task-1",
        },
        "policySnapshot": {
            "parentPolicySnapshotId": "policy-ga-parent-1",
            "childPolicySnapshotId": "policy-ga-parent-1",
            "taskLocalPolicyCompatibilityRefs": (),
            "allowedToolNames": ("FileRead",),
            "permissionRefs": ("permission:read-only",),
            "callbackHookRefs": ("callback:before-tool-policy",),
        },
        "ledgerRef": _ledger_ref(child),
        "delegatedEvidenceRequirements": (
            DelegatedEvidenceRequirement(type="TestRun", delegation="delegated_required"),
        ),
        "workspaceIsolation": {
            "workspacePolicy": "trusted",
            "isolationRef": "workspace-isolation:ga-task-1",
            "parentWorkspaceRef": "workspace:parent-redacted",
            "childWorkspaceRef": "workspace:child-redacted",
            "descriptiveOnly": True,
            "adoptionAttached": False,
            "workspaceMutated": False,
            "privateNotes": ("private /workspace/secret path and Bearer unsafe-token",),
        },
        "completionContract": {
            "requiredEvidence": "tool_call",
            "requiredFiles": (),
            "requireNonEmptyResult": True,
            "summaryIsEvidence": False,
            "acceptedEvidenceMetadataOnly": True,
        },
        "auditEventRefs": ("audit:ga-child-spawn-planned", "audit:ga-child-envelope-issued"),
        "adkPrimitiveOwnership": {
            "agentOwner": "adk_future_agent",
            "runnerOwner": "adk_future_runner",
            "eventOwner": "adk_event_bridge",
            "toolOwner": "adk_function_tool_future",
            "callbackOwner": "adk_callbacks_future",
            "runnerAttached": False,
            "childExecutionAttached": False,
            "allowedToolNames": ("FileRead",),
            "callbackHookRefs": ("callback:before-tool-policy",),
        },
        "authorityFlags": ChildRuntimeEnvelopeAuthorityFlags(),
        "rawTranscriptRef": "transcript:private-child-turn",
        "privateMetadata": {
            "rawTranscriptPreview": "raw child transcript with sk-child-secret",
            "toolArgs": {"authorization": "Bearer unsafe-token"},
            "workspacePath": "/workspace/private",
        },
    }
    return ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=issue_test_runtime_authority(
            authority_id="authority:test-ga-delegation",
            scopes=("child_runtime_envelope",),
        ),
        **payload,
    )


def _policy() -> ChildAcceptancePolicy:
    return ChildAcceptancePolicy.model_validate(
        {
            "parentExecutionId": "ga-parent-exec-1",
            "childExecutionId": "ga-child-exec-1",
            "taskId": "ga-task-1",
            "parentPolicySnapshotId": "policy-ga-parent-1",
            "childPolicySnapshotId": "policy-ga-parent-1",
            "runtimeReceiptRef": "receipt:ga-child-envelope-1",
            "requiredEvidenceRefs": (
                "ledger:ga-child-exec-1",
                "receipt:ga-child-envelope-1",
                "audit:ga-child-envelope-issued",
            ),
            "maxRetryBudget": 1,
            "currentAttempt": 0,
        }
    )


def _request(spawn_depth: int = 1) -> GeneralAutomationDelegationRequest:
    return GeneralAutomationDelegationRequest(
        taskId="ga-task-1",
        objectiveRef="objective:ga-scoped-subtask",
        spawnDepth=spawn_depth,
    )


# ---------------------------------------------------------------------------
# (a) general + flag ON: receipt-backed ChildRuntimeEnvelope verdict at depth 1
# ---------------------------------------------------------------------------


def test_general_flag_on_returns_receipt_backed_child_verdict() -> None:
    outcome = build_general_automation_delegation(
        request=_request(spawn_depth=1),
        accepted_envelope=_runtime_envelope(spawn_depth=1),
        receipt_ref="receipt:ga-child-envelope-1",
        policy=_policy(),
        context=_general_context(),
        env=_on_env(),
    )

    assert isinstance(outcome, GeneralAutomationDelegationOutcome)
    assert outcome.active is True
    assert outcome.reason == "delegation_accepted"
    # The result is a receipt-backed acceptance verdict (NOT a bare text blob).
    verdict = outcome.verdict
    assert isinstance(verdict, ChildAcceptanceVerdict)
    assert verdict.status == "accepted"
    assert verdict.reason_codes == ("accepted",)
    # Receipt/envelope linkage: the accepted refs include the runtime receipt ref
    # AND the child ledger ref derived from the envelope.
    assert outcome.receipt_ref == "receipt:ga-child-envelope-1"
    assert "receipt:ga-child-envelope-1" in verdict.accepted_evidence_refs
    assert "ledger:ga-child-exec-1" in verdict.accepted_evidence_refs


# ---------------------------------------------------------------------------
# (b) depth > 2 → rejected / denied
# ---------------------------------------------------------------------------


def test_depth_above_cap_is_denied() -> None:
    over_depth = DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH + 1
    outcome = build_general_automation_delegation(
        request=_request(spawn_depth=over_depth),
        accepted_envelope=_runtime_envelope(spawn_depth=1),
        receipt_ref="receipt:ga-child-envelope-1",
        policy=_policy(),
        context=_general_context(),
        env=_on_env(),
    )

    assert outcome.active is True
    assert outcome.reason == "spawn_depth_exceeded"
    assert outcome.verdict is None


def test_depth_at_cap_is_allowed() -> None:
    outcome = build_general_automation_delegation(
        request=_request(spawn_depth=DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH),
        accepted_envelope=_runtime_envelope(spawn_depth=DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH),
        receipt_ref="receipt:ga-child-envelope-1",
        policy=_policy(),
        context=_general_context(),
        env=_on_env(),
    )

    # depth == cap is permitted; the verdict is produced through acceptance.
    assert outcome.reason != "spawn_depth_exceeded"


# ---------------------------------------------------------------------------
# (c) flag-OFF / non-general → inert (not surfaced)
# ---------------------------------------------------------------------------


def test_flag_off_is_inert() -> None:
    outcome = build_general_automation_delegation(
        request=_request(),
        accepted_envelope=_runtime_envelope(),
        receipt_ref="receipt:ga-child-envelope-1",
        policy=_policy(),
        context=_general_context(),
        env=_off_env(),
    )

    assert outcome.active is False
    assert outcome.verdict is None
    assert outcome.reason == "delegation_inert"


def test_non_general_role_is_inert() -> None:
    outcome = build_general_automation_delegation(
        request=_request(),
        accepted_envelope=_runtime_envelope(),
        receipt_ref="receipt:ga-child-envelope-1",
        policy=_policy(),
        context=_coding_context(),
        env=_on_env(),
    )

    assert outcome.active is False
    assert outcome.verdict is None
    assert outcome.reason == "delegation_inert"


# ---------------------------------------------------------------------------
# (d) authority flags remain False — no real child execution enabled
# ---------------------------------------------------------------------------


def test_authority_flags_remain_false() -> None:
    envelope = _runtime_envelope()
    flags = envelope.authority_flags.model_dump(by_alias=True)
    assert set(flags.values()) == {False}
    # In particular the child-execution flags stay False.
    assert flags["runnerAttached"] is False
    assert flags["childExecutionAttached"] is False
    assert flags["productionAuthority"] is False

    outcome = build_general_automation_delegation(
        request=_request(),
        accepted_envelope=envelope,
        receipt_ref="receipt:ga-child-envelope-1",
        policy=_policy(),
        context=_general_context(),
        env=_on_env(),
    )
    # The delegation must NOT surface any "execution enabled" signal.
    assert outcome.real_child_runner_executed is False


# ---------------------------------------------------------------------------
# (e) no raw child transcript / secret leakage in the public projection
# ---------------------------------------------------------------------------


def test_public_projection_has_no_raw_transcript_or_secret() -> None:
    outcome = build_general_automation_delegation(
        request=_request(),
        accepted_envelope=_runtime_envelope(),
        receipt_ref="receipt:ga-child-envelope-1",
        policy=_policy(),
        context=_general_context(),
        env=_on_env(),
    )
    dumped = json.dumps(outcome.public_projection(), sort_keys=True)
    for unsafe in (
        "raw child transcript",
        "rawTranscriptRef",
        "privateMetadata",
        "toolArgs",
        "Bearer unsafe-token",
        "sk-child-secret",
        "/workspace",
    ):
        assert unsafe not in dumped


def test_env_flag_helper_default_off() -> None:
    # Master flag is the existing single-source helper; default OFF.
    assert general_automation_live_enabled({}) is False
    assert general_automation_live_enabled(_on_env()) is True
