from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    ChildRuntimeEnvelopeAuthorityFlags,
    ChildRuntimeWorkspacePolicy,
    project_child_runtime_envelope,
    runtime_envelope_satisfies_delegated_evidence_metadata,
)
from runtime_issuance_support import issue_test_runtime_authority
from magi_agent.evidence.subagent import (
    OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
    DelegatedEvidenceRequirement,
    EvidenceBoundaryLedgerRef,
    ExecutionBoundaryIdentity,
)
from magi_agent.evidence.types import EvidenceAgentRole
from magi_agent.runtime import (
    ChildRunnerConfig,
    ChildTaskRequest,
    LocalChildRunnerBoundary,
)


def _is_runtime_ref(value: object, namespace: str) -> bool:
    return isinstance(value, str) and re.fullmatch(rf"{namespace}:[a-f0-9]{{16}}", value) is not None


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-pr18-child-runner",
        scopes=scopes,
    )


class GenericFakeChildRunner:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls = 0

    async def run_child(self, request: ChildTaskRequest) -> dict[str, object]:
        self.calls += 1
        return {
            "childExecutionId": f"child-{request.role}-exec-1",
            "status": "completed",
            "summary": (
                f"{request.role} child completed metadata-only work.\n"
                "raw_child_transcript: /workspace/bot/private.txt\n"
                "hidden_reasoning: do not project\n"
                "Authorization: Bearer unsafe-token"
            ),
            "evidenceRefs": (f"evidence:{request.role}-child-1", "/Users/kevin/private/raw.json"),
            "artifactRefs": (f"artifact:{request.role}-child-1", "s3://private/raw-child-log"),
            "auditEventRefs": (f"audit:{request.role}-child-planned",),
            "rawTranscript": "raw child transcript with sk-child-secret",
            "toolLogs": "raw tool logs",
            "hiddenReasoning": "private reasoning",
        }


def _request(role: EvidenceAgentRole) -> ChildTaskRequest:
    return ChildTaskRequest(
        parentExecutionId=f"parent-{role}-exec-1",
        turnId=f"turn-{role}-1",
        taskId=f"task-{role}-1",
        objective=f"Run a bounded {role} child task without exposing raw child context.",
        role=role,
        delivery="return",
        budgetTokens=256,
        budgetMs=1000,
    )


def _parent_boundary(role: EvidenceAgentRole, request: ChildTaskRequest) -> ExecutionBoundaryIdentity:
    return ExecutionBoundaryIdentity(
        executionId=request.parent_execution_id,
        agentId=f"parent-{role}-agent",
        turnId=request.turn_id,
        policyScope=role,
        policySnapshotId=f"policy-{role}-snapshot",
        agentRole=role,
        runOn="main",
        spawnDepth=0,
    )


def _child_boundary(
    role: EvidenceAgentRole,
    request: ChildTaskRequest,
    child_execution_id: str,
) -> ExecutionBoundaryIdentity:
    return ExecutionBoundaryIdentity(
        executionId=child_execution_id,
        agentId=f"child-{role}-agent",
        parentExecutionId=request.parent_execution_id,
        taskId=request.task_id,
        turnId=request.turn_id,
        policyScope=role,
        policySnapshotId=f"policy-{role}-snapshot",
        agentRole=role,
        runOn="child",
        spawnDepth=1,
    )


def _ledger_ref(child: ExecutionBoundaryIdentity) -> EvidenceBoundaryLedgerRef:
    return EvidenceBoundaryLedgerRef(
        ledgerId=f"ledger:{child.execution_id}",
        executionId=child.execution_id,
        agentId=child.agent_id,
        parentExecutionId=child.parent_execution_id,
        taskId=child.task_id,
        policySnapshotId=child.policy_snapshot_id,
        childLedgerRefs=(),
    )


def _runtime_envelope(
    role: EvidenceAgentRole,
    request: ChildTaskRequest,
    result_envelope: object,
    prompt_ref: str,
    *,
    workspace_policy: ChildRuntimeWorkspacePolicy = "isolated",
) -> ChildRuntimeEnvelope:
    child_execution_id = getattr(result_envelope, "child_execution_id")
    parent = _parent_boundary(role, request)
    child = _child_boundary(role, request, child_execution_id)
    return ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_runtime_envelope"),
        **{
            "issuer": OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
            "mode": "return",
            "status": "accepted",
            "parentBoundary": parent,
            "childBoundary": child,
            "task": {
                "taskId": request.task_id,
                "persona": f"{role}-child",
                "role": role,
                "spawnDepth": 1,
                "deliver": "return",
                "promptRef": prompt_ref,
            },
            "policySnapshot": {
                "parentPolicySnapshotId": parent.policy_snapshot_id,
                "childPolicySnapshotId": child.policy_snapshot_id,
                "taskLocalPolicyCompatibilityRefs": (),
                "allowedToolNames": (),
                "permissionRefs": (),
                "callbackHookRefs": (),
            },
            "ledgerRef": _ledger_ref(child),
            "delegatedEvidenceRequirements": (
                DelegatedEvidenceRequirement(type="TestRun", delegation="delegated_required"),
            ),
            "workspaceIsolation": {
                "workspacePolicy": workspace_policy,
                "isolationRef": f"workspace-isolation:{request.task_id}",
                "parentWorkspaceRef": "workspace:parent-redacted",
                "childWorkspaceRef": f"workspace:child-{role}-redacted",
                "descriptiveOnly": True,
                "adoptionAttached": False,
                "workspaceMutated": False,
                "privateNotes": ("would use an isolated child workspace after activation",),
            },
            "completionContract": {
                "requiredEvidence": "tool_call",
                "requiredFiles": (),
                "requireNonEmptyResult": True,
                "summaryIsEvidence": False,
                "acceptedEvidenceMetadataOnly": True,
            },
            "auditEventRefs": getattr(result_envelope, "audit_event_refs"),
            "adkPrimitiveOwnership": {
                "agentOwner": "adk_future_agent",
                "runnerOwner": "adk_future_runner",
                "eventOwner": "adk_event_bridge",
                "toolOwner": "adk_function_tool_future",
                "callbackOwner": "adk_callbacks_future",
                "runnerAttached": False,
                "childExecutionAttached": False,
                "allowedToolNames": (),
                "callbackHookRefs": (),
            },
            "authorityFlags": ChildRuntimeEnvelopeAuthorityFlags(),
            "rawTranscriptRef": "transcript:private-child-turn",
            "privateMetadata": {
                "rawTranscriptPreview": "raw child transcript with sk-child-secret",
                "workspacePath": "/workspace/bot/private",
                "authorization": "Bearer unsafe-token",
            },
        },
    )


@pytest.mark.parametrize("role", ("coding", "research", "general"))
def test_pr18_child_runner_composes_generic_evidence_envelope_for_all_roles(
    role: EvidenceAgentRole,
) -> None:
    fake = GenericFakeChildRunner()
    request = _request(role)
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, localFakeChildRunnerEnabled=True),
        child_runner=fake,
    )

    result = asyncio.run(boundary.run(request))

    assert fake.calls == 1
    assert result.status == "ok"
    assert result.envelope is not None
    runtime_envelope = _runtime_envelope(role, request, result.envelope, result.prompt_ref)
    projection = project_child_runtime_envelope(runtime_envelope)
    result_projection = result.public_projection()
    encoded = json.dumps(
        {
            "result": result_projection,
            "runtimeEnvelope": projection.model_dump(by_alias=True, mode="python"),
        },
        sort_keys=True,
    )

    assert runtime_envelope_satisfies_delegated_evidence_metadata(runtime_envelope) is True
    assert projection.role == role
    assert projection.authority_flags.child_execution_attached is False
    assert projection.authority_flags.runner_attached is False
    assert projection.authority_flags.tool_host_dispatched is False
    assert projection.authority_flags.workspace_mutated is False
    assert projection.authority_flags.memory_provider_called is False
    assert projection.authority_flags.route_attached is False
    assert projection.authority_flags.production_authority is False
    assert result_projection["authorityFlags"]["childRunnerAttached"] is False
    assert result_projection["authorityFlags"]["realChildRunnerExecuted"] is False
    # The child's sanitized summary (its actual answer) MUST be surfaced to the
    # parent model as a top-level, human-readable field — not buried as
    # opaque refs only. Without this the parent cannot read what the child
    # produced and re-runs the same work (observed: SpawnAgent x3 repeated).
    assert "childSummary" in result_projection
    assert result_projection["childSummary"] == result_projection["childEnvelope"]["summary"]
    assert "child completed metadata-only work" in result_projection["childSummary"]
    assert result_projection["parentOutputRefs"][0] == result_projection["childEnvelope"]["childRef"]
    assert result_projection["parentOutputRefs"][0] != result.envelope.child_ref
    assert _is_runtime_ref(result_projection["parentOutputRefs"][0], "child")
    assert _is_runtime_ref(result_projection["parentOutputRefs"][1], "evidence")
    assert _is_runtime_ref(result_projection["parentOutputRefs"][2], "artifact")
    assert _is_runtime_ref(result_projection["parentOutputRefs"][3], "audit")
    assert f"evidence:{role}-child-1" not in result_projection["parentOutputRefs"]
    assert f"artifact:{role}-child-1" not in result_projection["parentOutputRefs"]
    assert f"audit:{role}-child-planned" not in result_projection["parentOutputRefs"]
    for forbidden in (
        "raw_child_transcript",
        "raw child transcript",
        "hidden_reasoning",
        "Authorization",
        "unsafe-token",
        "sk-child-secret",
        "/workspace",
        "/Users/kevin",
        "s3://private",
        "raw tool logs",
        "private reasoning",
    ):
        assert forbidden not in encoded


def test_pr18_child_runner_stays_default_off_and_rejects_child_authored_envelopes() -> None:
    fake = GenericFakeChildRunner()
    request = _request("coding")

    disabled = asyncio.run(LocalChildRunnerBoundary(ChildRunnerConfig(), child_runner=fake).run(request))

    assert disabled.status == "disabled"
    assert disabled.error_code == "child_runner_disabled"
    assert fake.calls == 0
    assert disabled.public_projection()["authorityFlags"]["realChildRunnerExecuted"] is False
    with pytest.raises(ValidationError, match="runtime-issued"):
        ChildRuntimeEnvelope.model_validate(
            {
                "issuer": "child_authored_json",
                "mode": "return",
                "status": "accepted",
            }
        )


def test_pr18_runtime_package_lazy_exports_child_runner_boundary_without_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

runtime = importlib.import_module("magi_agent.runtime")
assert runtime.LocalChildRunnerBoundary
forbidden_prefixes = (
    "google.adk.runners",
    "google.adk.models",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.local_runner",
    "magi_agent.tools.dispatcher",
    "magi_agent.transport.chat",
    "magi_agent.memory.adapters",
    "magi_agent.workspace",
    "socket",
    "subprocess",
    "requests",
    "httpx",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"child runner lazy export loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
