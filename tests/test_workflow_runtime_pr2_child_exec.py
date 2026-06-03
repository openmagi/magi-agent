"""PR2 — Real child execution surface (gated).

These tests lock the five PR2 behaviours:

1. Real child runs end-to-end through the REAL adk_turn_runner surface when the
   opt-in feature-pack is enabled (``real_child_runner_executed=True`` and
   ``adk_runner_surface`` is the real surface, not ``future_adk_runner``).  The
   real surface is exercised through an injected ``LocalAdkTurnRunnerBoundary``
   double driving a ``LocalAdkReplayRunner`` (so no live ADK/provider traffic),
   but the result proves the real ``AdkTurnRunner.run_turn`` code path was
   selected — NOT local-fake.
2. Envelope/token rejection on tamper — a forged/altered envelope or a wrong
   runtime receipt token is rejected by token-validated acceptance.
3. Depth-cap + total-agents-per-run cap (≤1000) are enforced/bounded.
4. Raw child transcript NEVER surfaces to the parent (only sanitised refs).
5. Default-OFF parity — with the pack disabled the boundary behaves
   byte-identically to PR1 local-fake (``real_child_runner_executed=False`` and
   ``adk_runner_surface == "future_adk_runner"``).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from magi_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    ChildRuntimeEnvelopeAuthorityFlags,
)
from magi_agent.evidence.subagent import (
    DelegatedEvidenceRequirement,
    EvidenceBoundaryLedgerRef,
    ExecutionBoundaryIdentity,
)
from magi_agent.meta_orchestration.child_acceptance import (
    ChildAcceptancePolicy,
    accept_real_child_envelope,
)
from magi_agent.runtime.adk_turn_runner import (
    LocalAdkReplayRunner,
    LocalAdkTurnRunnerBoundary,
)
from magi_agent.runtime.child_runner_boundary import (
    REAL_ADK_CHILD_RUNNER_SURFACE,
    ChildRunnerConfig,
    ChildTaskRequest,
    LocalChildRunnerBoundary,
)
from runtime_issuance_support import issue_test_runtime_authority


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------


class _LocalFakeRunner:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls = 0

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        task_id = getattr(request, "task_id", "task-x")
        return {
            "childExecutionId": f"child-{task_id}",
            "status": "completed",
            "summary": (
                "Local fake completed.\n"
                "raw_child_transcript: /workspace/bot/private.txt\n"
                "hidden_reasoning: do not project\n"
                "Authorization: Bearer unsafe-token"
            ),
            "evidenceRefs": (f"evidence:{task_id[:8]}-src1",),
            "artifactRefs": (),
            "auditEventRefs": (),
            "rawTranscript": "raw child transcript with sk-child-secret",
        }


def _request(task_id: str = "task-1", spawn_depth: int = 1) -> ChildTaskRequest:
    return ChildTaskRequest(
        parentExecutionId="parent-exec-1",
        turnId="turn-1",
        taskId=task_id,
        objective="Inspect the local source ledger without exposing raw logs.",
        role="research",
        delivery="return",
        metadata={"spawnDepth": spawn_depth},
    )


def _adk_boundary() -> LocalAdkTurnRunnerBoundary:
    # A trusted, local-only replay runner double — drives the REAL run_turn path
    # without any live provider/ADK traffic.
    return LocalAdkTurnRunnerBoundary.from_local_test_runner(LocalAdkReplayRunner())


# --- runtime-issued envelope construction (mirrors meta child acceptance) ----


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-pr2-child-exec",
        scopes=scopes,
    )


def _parent_boundary() -> ExecutionBoundaryIdentity:
    return ExecutionBoundaryIdentity.model_validate(
        {
            "executionId": "parent-exec-1",
            "agentId": "parent-agent",
            "turnId": "turn-1",
            "policyScope": "research",
            "policySnapshotId": "policy-parent-1",
            "agentRole": "research",
            "runOn": "main",
            "spawnDepth": 0,
        }
    )


def _child_boundary() -> ExecutionBoundaryIdentity:
    return ExecutionBoundaryIdentity.model_validate(
        {
            "executionId": "child-exec-1",
            "agentId": "child-agent",
            "parentExecutionId": "parent-exec-1",
            "taskId": "task-1",
            "turnId": "turn-1",
            "policyScope": "research",
            "policySnapshotId": "policy-parent-1",
            "agentRole": "research",
            "runOn": "child",
            "spawnDepth": 1,
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
            "childLedgerRefs": ("ledger:audit-child-proof",),
        }
    )


def _envelope_payload(**overrides: object) -> dict[str, object]:
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
            "persona": "research",
            "role": "research",
            "spawnDepth": 1,
            "deliver": "return",
            "promptRef": "prompt:task-1",
        },
        "policySnapshot": {
            "parentPolicySnapshotId": "policy-parent-1",
            "childPolicySnapshotId": "policy-parent-1",
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
            "isolationRef": "workspace-isolation:task-1",
            "parentWorkspaceRef": "workspace:parent-redacted",
            "childWorkspaceRef": "workspace:child-redacted",
            "descriptiveOnly": True,
            "adoptionAttached": False,
            "workspaceMutated": False,
            "privateNotes": ("private /workspace/path and Bearer unsafe-token",),
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
            "allowedToolNames": ("FileRead",),
            "callbackHookRefs": ("callback:before-tool-policy",),
        },
        "authorityFlags": ChildRuntimeEnvelopeAuthorityFlags(),
        "rawTranscriptRef": "transcript:private-child-turn",
        "privateMetadata": {
            "rawTranscriptPreview": "raw child transcript with sk-child-secret",
            "workspacePath": "/workspace/private",
        },
    }
    payload.update(overrides)
    return payload


def _issued_envelope(**overrides: object) -> ChildRuntimeEnvelope:
    return ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_runtime_envelope"),
        **_envelope_payload(**overrides),
    )


def _policy(**overrides: object) -> ChildAcceptancePolicy:
    payload = {
        "parentExecutionId": "parent-exec-1",
        "childExecutionId": "child-exec-1",
        "taskId": "task-1",
        "parentPolicySnapshotId": "policy-parent-1",
        "childPolicySnapshotId": "policy-parent-1",
        "runtimeReceiptRef": "receipt:child-envelope-1",
        "requiredEvidenceRefs": (
            "ledger:child-exec-1",
            "receipt:child-envelope-1",
            "audit:child-envelope-issued",
        ),
        "maxRetryBudget": 1,
        "currentAttempt": 0,
    }
    payload.update(overrides)
    return ChildAcceptancePolicy.model_validate(payload)


# ---------------------------------------------------------------------------
# Test 1 — real child runs through the REAL adk surface when the pack is on
# ---------------------------------------------------------------------------


def test_pack_enabled_routes_child_through_real_adk_surface() -> None:
    fake = _LocalFakeRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(
            enabled=True,
            localFakeChildRunnerEnabled=True,
            realChildExecutionPackEnabled=True,
        ),
        child_runner=fake,
        adk_turn_boundary=_adk_boundary(),
    )

    result = asyncio.run(boundary.run(_request()))
    projection = result.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert result.status == "ok"
    # The REAL surface was selected, NOT the local-fake placeholder.
    assert projection["diagnosticMetadata"]["adkRunnerSurface"] == REAL_ADK_CHILD_RUNNER_SURFACE
    assert projection["diagnosticMetadata"]["adkRunnerSurface"] != "future_adk_runner"
    assert projection["diagnosticMetadata"]["realChildRunnerExecuted"] is True
    assert projection["diagnosticMetadata"]["adkTurnRunnerInvoked"] is True
    # The local-fake run_child must NOT have been used for real execution.
    assert fake.calls == 0
    # Production kill-switch authority flags remain locked off.
    assert projection["authorityFlags"]["productionAuthority"] is False
    assert projection["authorityFlags"]["childRunnerAttached"] is False
    # No raw transcript / secret crosses the boundary.
    assert "raw child transcript" not in encoded
    assert "sk-child-secret" not in encoded
    assert "Authorization" not in encoded
    assert "/workspace" not in encoded


def test_real_child_surface_requires_token_validated_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real child ADK turn must not become parent-visible without acceptance."""
    from magi_agent.meta_orchestration.child_acceptance import ChildAcceptanceVerdict

    def reject_acceptance(*_args: object, **_kwargs: object) -> ChildAcceptanceVerdict:
        return ChildAcceptanceVerdict._from_evaluation(
            status="rejected",
            reason_codes=("runtime_receipt_mismatch",),
            accepted_evidence_refs=(),
            missing_evidence_refs=(),
            retryable=False,
            retry_budget_remaining=0,
        )

    monkeypatch.setattr(
        "magi_agent.runtime.child_runner_boundary.accept_real_child_envelope",
        reject_acceptance,
    )

    fake = _LocalFakeRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(
            enabled=True,
            localFakeChildRunnerEnabled=True,
            realChildExecutionPackEnabled=True,
        ),
        child_runner=fake,
        adk_turn_boundary=_adk_boundary(),
    )

    result = asyncio.run(boundary.run(_request()))
    projection = result.public_projection()

    assert result.status == "blocked"
    assert result.error_code == "real_child_acceptance_rejected"
    assert projection["diagnosticMetadata"]["realChildRunnerExecuted"] is True
    assert projection["diagnosticMetadata"]["childAcceptanceStatus"] == "rejected"
    assert projection["diagnosticMetadata"]["childAcceptanceReason"] == (
        "runtime_receipt_mismatch"
    )
    assert projection["childEnvelope"] is None
    assert fake.calls == 0


# ---------------------------------------------------------------------------
# Test 2 — token / envelope tamper rejection
# ---------------------------------------------------------------------------


def test_token_validated_acceptance_accepts_runtime_issued_envelope() -> None:
    verdict = accept_real_child_envelope(
        _issued_envelope(),
        receipt_ref="receipt:child-envelope-1",
        policy=_policy(),
    )
    assert verdict.status == "accepted"
    assert verdict.reason_codes == ("accepted",)


def test_token_validated_acceptance_rejects_wrong_receipt_token() -> None:
    # Structurally valid but MISMATCHED receipt — the runtime-issued token does
    # not match the policy's expected receipt, so acceptance must reject.
    verdict = accept_real_child_envelope(
        _issued_envelope(),
        receipt_ref="receipt:other-envelope-9",
        policy=_policy(),
    )
    assert verdict.status == "rejected"
    assert verdict.reason_codes == ("runtime_receipt_mismatch",)


def test_token_validated_acceptance_rejects_forged_child_authored_envelope() -> None:
    # A child-authored mapping is not a runtime-issued envelope and cannot be
    # promoted into one — acceptance must reject it.
    with pytest.raises(Exception):
        # forging the issuer fails validation at envelope construction time
        ChildRuntimeEnvelope.model_validate(_envelope_payload(issuer="child_authored_json"))

    # And a tampered (mutated-after-issue) envelope is rejected at acceptance.
    envelope = _issued_envelope()
    object.__setattr__(envelope, "audit_event_refs", ("audit:tampered",))
    verdict = accept_real_child_envelope(
        envelope,
        receipt_ref="receipt:child-envelope-1",
        policy=_policy(),
    )
    assert verdict.status == "rejected"
    assert verdict.reason_codes == ("invalid_child_envelope",)


# ---------------------------------------------------------------------------
# Test 3 — depth cap + total-agents-per-run cap
# ---------------------------------------------------------------------------


def test_spawn_depth_cap_blocks_over_depth_child() -> None:
    fake = _LocalFakeRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(
            enabled=True,
            localFakeChildRunnerEnabled=True,
            realChildExecutionPackEnabled=True,
            maxSpawnDepth=1,
        ),
        child_runner=fake,
        adk_turn_boundary=_adk_boundary(),
    )

    result = asyncio.run(boundary.run(_request(spawn_depth=2)))

    assert result.status == "blocked"
    assert result.error_code == "child_spawn_depth_exceeded"
    assert fake.calls == 0


def test_total_agents_per_run_cap_is_bounded_at_or_below_1000() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        MAX_TOTAL_AGENTS_PER_RUN,
        clamp_total_agents_per_run,
    )

    assert MAX_TOTAL_AGENTS_PER_RUN <= 1000
    assert clamp_total_agents_per_run(1) == 1
    assert clamp_total_agents_per_run(1000) == 1000
    assert clamp_total_agents_per_run(1001) == 1000
    assert clamp_total_agents_per_run(50_000) == 1000


def test_total_agents_per_run_cap_blocks_when_budget_exhausted() -> None:
    fake = _LocalFakeRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(
            enabled=True,
            localFakeChildRunnerEnabled=True,
            realChildExecutionPackEnabled=True,
        ),
        child_runner=fake,
        adk_turn_boundary=_adk_boundary(),
        agents_spawned_so_far=1000,
    )

    result = asyncio.run(boundary.run(_request()))

    assert result.status == "blocked"
    assert result.error_code == "total_agents_per_run_exceeded"
    assert fake.calls == 0


# ---------------------------------------------------------------------------
# Test 4 — raw transcript never surfaces (real surface path)
# ---------------------------------------------------------------------------


def test_real_surface_returns_only_sanitised_refs() -> None:
    fake = _LocalFakeRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(
            enabled=True,
            localFakeChildRunnerEnabled=True,
            realChildExecutionPackEnabled=True,
        ),
        child_runner=fake,
        adk_turn_boundary=_adk_boundary(),
    )

    result = asyncio.run(boundary.run(_request()))
    raw_encoded = json.dumps(result.model_dump(by_alias=True), sort_keys=True)
    public_encoded = json.dumps(result.public_projection(), sort_keys=True)

    for forbidden in (
        "raw child transcript",
        "sk-child-secret",
        "hidden_reasoning",
        "Authorization",
        "unsafe-token",
        "/workspace",
    ):
        assert forbidden not in raw_encoded
        assert forbidden not in public_encoded


# ---------------------------------------------------------------------------
# Test 5 — default-OFF parity with PR1 local-fake
# ---------------------------------------------------------------------------


def test_pack_disabled_is_byte_identical_to_pr1_local_fake() -> None:
    # PR1 baseline: pack flag off, no adk boundary.
    pr1_fake = _LocalFakeRunner()
    pr1 = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, localFakeChildRunnerEnabled=True),
        child_runner=pr1_fake,
    )
    pr1_result = asyncio.run(pr1.run(_request()))

    # PR2 with the pack DISABLED but an adk boundary present (must be ignored).
    pr2_fake = _LocalFakeRunner()
    pr2 = LocalChildRunnerBoundary(
        ChildRunnerConfig(
            enabled=True,
            localFakeChildRunnerEnabled=True,
            realChildExecutionPackEnabled=False,
        ),
        child_runner=pr2_fake,
        adk_turn_boundary=_adk_boundary(),
    )
    pr2_result = asyncio.run(pr2.run(_request()))

    pr1_projection = pr1_result.public_projection()
    pr2_projection = pr2_result.public_projection()

    assert pr1_result.status == "ok"
    assert pr2_result.status == "ok"
    assert pr1_fake.calls == 1
    assert pr2_fake.calls == 1
    # Byte-identical local-fake surface markers.
    assert pr2_projection["diagnosticMetadata"]["adkRunnerSurface"] == "future_adk_runner"
    assert pr2_projection["diagnosticMetadata"]["realChildRunnerExecuted"] is False
    assert pr1_projection["diagnosticMetadata"]["adkRunnerSurface"] == "future_adk_runner"
    assert pr1_projection["diagnosticMetadata"]["realChildRunnerExecuted"] is False
    assert pr2_projection["authorityFlags"] == pr1_projection["authorityFlags"]


# ---------------------------------------------------------------------------
# Test 6 — feature-pack wiring (default off, opt-in)
# ---------------------------------------------------------------------------


def test_real_child_execution_pack_is_default_off_and_not_opt_out() -> None:
    from magi_agent.harness.profiles import (
        REAL_CHILD_EXECUTION_PACK_NAME,
        build_default_profile,
        real_child_execution_pack_enabled,
    )

    profile = build_default_profile()
    pack = next(
        p for p in profile.harness_packs if p.name == REAL_CHILD_EXECUTION_PACK_NAME
    )
    assert pack.enabled_by_default is False
    assert pack.opt_out is False
    assert real_child_execution_pack_enabled(profile) is False
    assert real_child_execution_pack_enabled(profile, opted_in_packs=()) is False
    assert (
        real_child_execution_pack_enabled(
            profile, opted_in_packs=(REAL_CHILD_EXECUTION_PACK_NAME,)
        )
        is True
    )
