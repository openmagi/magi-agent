from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError
import pytest

from magi_agent.coding.meta_adapter import (
    CODING_META_ROLE_NAMES,
    CodingMetaEvidencePolicy,
    accept_coding_child_result,
    build_coding_meta_harness_plan,
)
from magi_agent.evidence.child_runtime_envelope import ChildRuntimeEnvelope
from runtime_issuance_support import issue_test_runtime_authority
from magi_agent.evidence.subagent import OPENMAGI_RUNTIME_ENVELOPE_ISSUER
from magi_agent.harness.repair_policy import RepairPlan, next_repair_action
from magi_agent.harness.verifier_bus import VerifierResultMetadata
from magi_agent.meta_orchestration.child_acceptance import (
    ChildAcceptanceVerdict,
    issue_runtime_child_result,
)
from magi_agent.meta_orchestration.commit_adapter import (
    evaluate_before_commit_for_assembly,
    issue_runtime_verifier_result_for_assembly,
)
from magi_agent.meta_orchestration.final_assembly import (
    assemble_final_output_from_inspection,
)
from magi_agent.meta_orchestration.inspection_loop import (
    MetaInspectedChildVerdict,
    inspect_child_verdicts,
)
from magi_agent.recipes.coding_subagents import (
    CodingSubagentConfig,
    CodingSubagentModeRequest,
    CodingSubagentRecipe,
    CodingSubagentToolScope,
)
from magi_agent.tools.read_ledger import (
    ReadLedger,
    ReadLedgerConfig,
    workspace_content_digest,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = PYTHON_ROOT / "tests/fixtures/parity/coding_harness_consolidated_matrix.json"


class LocalEvidenceOnlyChildRunner:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls = 0
        self.seen_allowed_tools: tuple[str, ...] = ()

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        metadata = getattr(request, "metadata")
        allowed_tools = metadata.get("allowedTools")
        self.seen_allowed_tools = tuple(allowed_tools) if isinstance(allowed_tools, tuple) else ()
        return {
            "childExecutionId": "child:review-1",
            "status": "completed",
            "summary": (
                "Review evidence envelope ready.\n"
                "raw_child_transcript: /workspace/private.py\n"
                "Authorization: Bearer SHOULD_NOT_PROJECT"
            ),
            "evidenceRefs": ("evidence:review-1",),
            "artifactRefs": ("artifact:review-1",),
            "auditEventRefs": ("audit:review-1",),
            "rawTranscript": "private child transcript SHOULD_NOT_PROJECT",
        }


class NoChildRunner:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls = 0

    async def run_child(self, request: object) -> dict[str, object]:
        _ = request
        self.calls += 1
        raise AssertionError("implement_local must not start a child runner")


def _coding_request(mode: str, **metadata: object) -> CodingSubagentModeRequest:
    return CodingSubagentModeRequest(
        mode=mode,
        parentExecutionId="parent:coding-pr5",
        turnId="turn:coding-pr5",
        taskId=f"task:{mode.replace('_', '-')}",
        objective="Exercise PR5 coding subagent contracts with local fake evidence only.",
        sessionId="session:pr5",
        workspaceRef="workspace:repo-pr5",
        metadata=metadata,
    )


def _read_ledger_with_file(content: str = "alpha\n") -> tuple[ReadLedger, str]:
    ledger = ReadLedger(ReadLedgerConfig(enabled=True, localInMemoryEnabled=True))
    digest = workspace_content_digest(content)
    ledger.record_read(
        session_id="session:pr5",
        workspace_ref="workspace:repo-pr5",
        path="src/app.py",
        digest=digest,
        size_bytes=len(content.encode("utf-8")),
        mtime_ns=1,
        read_mode="full",
        turn_id="turn:coding-pr5",
        tool_use_id="read:pr5",
    )
    return ledger, digest


def _policy(**updates: object) -> CodingMetaEvidencePolicy:
    payload: dict[str, object] = {
        "parentExecutionId": "parent:coding-pr5",
        "childExecutionId": "child:code-editor-pr5",
        "taskId": "task:code-editor",
        "parentPolicySnapshotId": "policy:coding-pr5",
        "childPolicySnapshotId": "policy:coding-pr5",
        "runtimeReceiptRef": "receipt:coding-pr5",
        "readEvidenceRefs": ("read:src-app-py:rev-3",),
        "diffEvidenceRefs": ("diff:src-app-py:rev-3",),
        "testEvidenceRefs": ("test:pytest:pass",),
        "checkpointEvidenceRefs": ("checkpoint:review-pr5",),
        "lastReadRevisionRef": "rev:3",
        "latestMutationRevisionRef": "rev:3",
        "testStatus": "pass",
        "maxRetryBudget": 1,
        "currentAttempt": 0,
    }
    payload.update(updates)
    return CodingMetaEvidencePolicy.model_validate(payload)


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-coding-pr5",
        scopes=scopes,
    )


def _child_envelope(
    *,
    audit_event_refs: tuple[str, ...] = (
        "read:src-app-py:rev-3",
        "test:pytest:pass",
        "checkpoint:review-pr5",
    ),
) -> ChildRuntimeEnvelope:
    return ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_runtime_envelope"),
        **{
            "issuer": OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
            "mode": "return",
            "status": "accepted",
            "parentBoundary": {
                "executionId": "parent:coding-pr5",
                "agentId": "agent:parent",
                "turnId": "turn:coding-pr5",
                "policyScope": "coding",
                "policySnapshotId": "policy:coding-pr5",
                "agentRole": "coding",
                "runOn": "main",
                "spawnDepth": 0,
            },
            "childBoundary": {
                "executionId": "child:code-editor-pr5",
                "agentId": "agent:child",
                "parentExecutionId": "parent:coding-pr5",
                "taskId": "task:code-editor",
                "turnId": "turn:coding-pr5",
                "policyScope": "coding",
                "policySnapshotId": "policy:coding-pr5",
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
                "parentPolicySnapshotId": "policy:coding-pr5",
                "childPolicySnapshotId": "policy:coding-pr5",
                "allowedToolNames": (),
                "permissionRefs": (),
                "callbackHookRefs": (),
            },
            "ledgerRef": {
                "ledgerId": "diff:src-app-py:rev-3",
                "executionId": "child:code-editor-pr5",
                "agentId": "agent:child",
                "parentExecutionId": "parent:coding-pr5",
                "taskId": "task:code-editor",
                "policySnapshotId": "policy:coding-pr5",
            },
            "delegatedEvidenceRequirements": (),
            "workspaceIsolation": {
                "workspacePolicy": "git_worktree",
                "isolationRef": "isolation:coding-pr5",
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
                "runnerAttached": False,
                "childExecutionAttached": False,
                "allowedToolNames": (),
                "callbackHookRefs": (),
            },
            "authorityFlags": {},
        },
    )


def _accepted(task_id: str, *evidence_refs: str) -> MetaInspectedChildVerdict:
    return MetaInspectedChildVerdict.model_validate(
        {
            "taskId": task_id,
            "required": True,
            "attempt": 0,
            "verdict": ChildAcceptanceVerdict._from_evaluation(
                status="accepted",
                reason_codes=("accepted",),
                accepted_evidence_refs=evidence_refs,
                missing_evidence_refs=(),
                retryable=False,
                retry_budget_remaining=0,
            ),
        }
    )


def _rejected_with_smuggled_refs(task_id: str, *evidence_refs: str) -> MetaInspectedChildVerdict:
    return MetaInspectedChildVerdict.model_validate(
        {
            "taskId": task_id,
            "required": True,
            "attempt": 0,
            "verdict": ChildAcceptanceVerdict._from_evaluation(
                status="rejected",
                reason_codes=("runtime_receipt_mismatch",),
                accepted_evidence_refs=evidence_refs,
                missing_evidence_refs=(),
                retryable=False,
                retry_budget_remaining=0,
            ),
        }
    )


def _matrix_row() -> dict[str, object]:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    return next(row for row in matrix["rows"] if row["id"] == "coding_subagent_roles_and_repair_loop")


def test_pr5_plan_roles_map_to_existing_first_party_contracts_without_escalation() -> None:
    plan = build_coding_meta_harness_plan(
        plan_id="plan:coding-pr5",
        parent_execution_id="parent:coding-pr5",
        objective_digest="sha256:" + "a" * 64,
        objective_preview="Map PR5 role vocabulary onto existing first-party coding contracts.",
        evidence_policy=_policy(),
    )
    roles_by_name = {role.display_name: role for role in plan.role_definitions}
    specs_by_role = {spec.role_ref: spec for spec in plan.task_plan.child_task_specs}

    assert tuple(plan.role_names) == CODING_META_ROLE_NAMES
    plan_role_contracts = {
        "coding.explore": (
            roles_by_name["code_reader"],
            "contract:coding-read-evidence",
            ("tool:readonly-repo-files",),
        ),
        "coding.plan": (
            roles_by_name["code_reviewer"],
            "contract:coding-review-checkpoint",
            ("tool:local-review-digest",),
        ),
        "coding.implement": (
            roles_by_name["code_editor"],
            "contract:coding-diff-evidence",
            ("tool:local-diff-metadata",),
        ),
        "coding.verify": (
            roles_by_name["test_runner"],
            "contract:coding-test-evidence",
            ("tool:local-test-receipt",),
        ),
    }
    assert set(plan_role_contracts) == {
        "coding.explore",
        "coding.plan",
        "coding.implement",
        "coding.verify",
    }
    for role, completion_contract_ref, allowed_tool_refs in plan_role_contracts.values():
        assert role.completion_contract_ref == completion_contract_ref
        assert role.allowed_tool_refs == allowed_tool_refs

    for role in roles_by_name.values():
        child_spec = specs_by_role[role.role_ref]
        assert role.domain == "coding"
        assert role.default_off is True
        assert role.max_spawn_depth == 1
        assert role.allowed_tool_refs == child_spec.allowed_tool_refs
        assert child_spec.delivery_mode == "return"
        assert child_spec.requires_evidence_envelope is True
        assert role.completion_contract_ref == child_spec.completion_contract_ref
        assert "tool:workspace-write" not in role.allowed_tool_refs
        assert "tool:browser-live-web" not in role.allowed_tool_refs
        assert {"tool:workspace-write", "tool:memory-write", "tool:channel-send"} <= set(
            role.denied_tool_refs
        )

    inspect_scope = CodingSubagentToolScope.inspect(
        ("FileWrite", "PatchApply", "Bash", "ReadFile")
    )
    implement_scope = CodingSubagentToolScope.implement_local(("FileWrite", "PatchApply", "Bash"))
    verify_tools = roles_by_name["test_runner"].allowed_tool_refs

    assert inspect_scope.allowed_tools == (
        "ReadFile",
        "SearchFiles",
        "ListFiles",
        "InspectSymbols",
        "GitDiff",
    )
    assert inspect_scope.mutation_intent_allowed is False
    assert inspect_scope.denied_tools == ("Bash", "FileWrite", "PatchApply")
    assert implement_scope.allowed_tools == (
        "ReadFile",
        "SearchFiles",
        "ListFiles",
        "InspectSymbols",
        "GitDiff",
        "MutationIntent",
    )
    assert implement_scope.mutation_intent_allowed is True
    assert implement_scope.denied_tools == ("Bash", "FileWrite", "PatchApply")
    assert verify_tools == ("tool:local-test-receipt",)


def test_pr5_implement_role_requires_read_ledger_and_approval_without_child_runner_or_write() -> None:
    no_runner = NoChildRunner()
    ledger, digest = _read_ledger_with_file()
    recipe = CodingSubagentRecipe(
        CodingSubagentConfig(enabled=True, localFakeChildRunnerEnabled=True),
        child_runner=no_runner,
        read_ledger=ledger,
    )

    result = asyncio.run(
        recipe.run(
            _coding_request(
                "implement_local",
                mutationIntent={
                    "toolName": "FileEdit",
                    "path": "src/app.py",
                    "currentDigest": digest,
                    "currentText": "alpha\n",
                    "oldString": "alpha",
                    "newString": "beta",
                    "explicitApproval": True,
                },
            )
        )
    )

    assert no_runner.calls == 0
    assert result.status == "approval_required"
    assert result.child is None
    assert result.mutation_intent is not None
    projection = result.public_projection()
    assert projection["mutationIntent"]["readLedger"]["status"] == "ok"
    assert projection["authorityFlags"]["workspaceMutationEnabled"] is False
    assert projection["authorityFlags"]["workspaceMutated"] is False
    assert projection["authorityFlags"]["liveChildRunnerEnabled"] is False
    assert projection["authorityFlags"]["liveToolExecutionEnabled"] is False
    assert projection["authorityFlags"]["productionAuthority"] is False


def test_pr5_parent_receives_evidence_envelope_without_raw_child_transcript() -> None:
    child_runner = LocalEvidenceOnlyChildRunner()
    recipe = CodingSubagentRecipe(
        CodingSubagentConfig(enabled=True, localFakeChildRunnerEnabled=True),
        child_runner=child_runner,
    )

    result = asyncio.run(recipe.run(_coding_request("code_review")))
    projection = result.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert child_runner.calls == 1
    assert child_runner.seen_allowed_tools == CodingSubagentToolScope.code_review().allowed_tools
    assert result.status == "accepted"
    assert projection["child"]["childEnvelope"]["childExecutionId"] == "child:review-1"
    assert projection["findings"][0]["evidenceRefs"] == projection["child"]["parentOutputRefs"][1:2]
    assert projection["child"]["childEnvelope"]["evidenceRefs"] == projection["findings"][0][
        "evidenceRefs"
    ]
    assert projection["child"]["authorityFlags"]["realChildRunnerExecuted"] is False
    assert "rawTranscript" not in rendered
    assert "raw_child_transcript" not in rendered
    assert "SHOULD_NOT_PROJECT" not in rendered
    assert "/workspace/private.py" not in rendered


def test_pr5_repair_role_uses_bounded_retry_after_verifier_failure() -> None:
    retry = accept_coding_child_result(
        issue_runtime_child_result(
            _child_envelope(audit_event_refs=("read:src-app-py:rev-3", "test:pytest:pass")),
            receipt_ref="receipt:coding-pr5",
        ),
        _policy(),
    )
    exhausted = accept_coding_child_result(
        issue_runtime_child_result(
            _child_envelope(audit_event_refs=("read:src-app-py:rev-3", "test:pytest:pass")),
            receipt_ref="receipt:coding-pr5",
        ),
        _policy(currentAttempt=1),
    )
    verifier_failure = VerifierResultMetadata(
        verifierId="verifier:meta-before-commit",
        status="failed",
        retryMessage="repair with bounded local coding verifier retry",
        failureMessage="diff evidence checkpoint failed",
    )
    repair_plan = RepairPlan.model_validate(
        {
            "planId": "repair:pr5",
            "maxAttempts": 1,
            "actions": ("removeUnsupportedClaims",),
        }
    )
    ready_inspection = inspect_child_verdicts(
        "loop:repair-pr5",
        (_accepted("task:code-editor", "diff:src-app-py:rev-3", "test:pytest:pass"),),
    )
    ready_assembly = assemble_final_output_from_inspection(
        "assembly:repair-pr5",
        ready_inspection,
        required_verifier_refs=("verifier:meta-before-commit",),
        satisfied_verifier_refs=("verifier:meta-before-commit",),
    )
    before_commit = evaluate_before_commit_for_assembly(
        "before-commit:repair-pr5",
        ready_assembly,
        verifier_results=(
            issue_runtime_verifier_result_for_assembly(
                ready_assembly,
                verifier_failure,
                verifier_bus_run_id="verifier-bus:repair-pr5",
                policy_snapshot_id="policy:coding-pr5",
            ),
        ),
    )

    assert retry.status == "retry"
    assert retry.retryable is True
    assert retry.retry_budget_remaining == 1
    assert retry.missing_evidence_refs == ("checkpoint:review-pr5",)

    assert exhausted.status == "rejected"
    assert exhausted.retryable is False
    assert exhausted.retry_budget_remaining == 0
    assert exhausted.reason_codes == ("missing_required_evidence", "retry_budget_exhausted")

    assert before_commit.final_projection_eligible is False
    assert before_commit.blocked_reasons == ("verifier_failed:verifier:meta-before-commit",)
    assert before_commit.retryable_reasons == (
        "verifier_retryable:verifier:meta-before-commit",
    )
    assert next_repair_action(repair_plan, attempt_index=0).action == "removeUnsupportedClaims"
    assert next_repair_action(repair_plan, attempt_index=1).action == "block"

    with pytest.raises(ValidationError):
        _policy(currentAttempt=2)


def test_pr5_rejected_child_evidence_cannot_enter_final_assembly() -> None:
    inspection = inspect_child_verdicts(
        "loop:pr5",
        (
            _accepted("task:accepted", "diff:accepted-pr5", "test:accepted-pr5"),
            _rejected_with_smuggled_refs("task:rejected", "diff:rejected-pr5"),
        ),
    )
    assembly = assemble_final_output_from_inspection(
        "assembly:pr5",
        inspection,
        required_verifier_refs=("verifier:meta-before-commit",),
        satisfied_verifier_refs=(),
    )
    projection = assembly.public_projection()

    assert inspection.aggregate_status == "blocked"
    assert inspection.accepted_child_evidence_refs_for_assembly == (
        "diff:accepted-pr5",
        "test:accepted-pr5",
    )
    assert "diff:rejected-pr5" not in inspection.accepted_child_evidence_refs_for_assembly
    assert assembly.accepted_child_evidence_refs == ("diff:accepted-pr5", "test:accepted-pr5")
    assert "task:rejected" in assembly.excluded_child_refs
    assert "diff:rejected-pr5" not in assembly.accepted_child_evidence_refs
    assert projection["rawChildTranscriptUsed"] is False
    assert projection["projectionMode"] == "blocked"


def test_pr5_default_off_local_fake_only_metadata_and_matrix_contracts() -> None:
    plan = build_coding_meta_harness_plan(
        plan_id="plan:coding-pr5",
        parent_execution_id="parent:coding-pr5",
        objective_digest="sha256:" + "a" * 64,
        objective_preview="Keep PR5 ADK vocabulary descriptive and local-only.",
        evidence_policy=_policy(),
    )
    row = _matrix_row()

    assert plan.default_off is True
    assert plan.local_only is True
    assert plan.fake_provider_only is True
    assert plan.evidence_policy.default_off is True
    assert plan.evidence_policy.live_execution_allowed is False
    assert plan.evidence_policy.child_execution_allowed is False
    assert plan.evidence_policy.tool_execution_allowed is False
    assert plan.evidence_policy.model_call_allowed is False
    assert plan.evidence_policy.workspace_write_allowed is False
    assert plan.evidence_policy.adk_runner_attached is False
    assert "ADK Agent" in plan.adk_usage_notes
    assert "not attached" in plan.adk_usage_notes

    assert row["missingImplementation"] == ["complete"]
    assert row["activationGate"] == "PR5-coding-subagent-fixture-only"
    assert row["defaultOff"] is True
    assert row["liveAuthorityAllowed"] is False
    assert row["coreTouchAllowed"] is False
    assert row["coreGapIfBlocked"] == ""
    assert row["adkPrimitive"] == "ADK Agent metadata and LongRunningFunctionTool shape only"
    assert "tests/test_coding_subagent_roles_repair_loop.py" in row["coveredByTests"]

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

for name in (
    "magi_agent.coding.meta_adapter",
    "magi_agent.recipes.coding_subagents",
):
    importlib.import_module(name)

forbidden = (
    "google.adk.runners",
    "google.adk.sessions",
    "google.adk.models",
    "google.adk.tools.mcp_tool",
    "magi_agent.adk_bridge",
    "magi_agent.runtime.adk_turn_runner",
    "magi_agent.tools.dispatcher",
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f"PR5 coding imports activated live ADK/tool surfaces: {loaded}")
""",
        ],
        cwd=PYTHON_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
