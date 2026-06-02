from __future__ import annotations

import json
import subprocess
import sys

from magi_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    ChildRuntimeEnvelopeAuthorityFlags,
)
from magi_agent.evidence.subagent import (
    OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
    ChildEvidenceEnvelope,
    DelegatedEvidenceRequirement,
    EvidenceBoundaryLedgerRef,
    ExecutionBoundaryIdentity,
)
from magi_agent.evidence.types import EvidenceContractVerdict, EvidenceRecord
from magi_agent.research.child_roles import (
    ResearchChildProofRef,
    issue_runtime_research_child_proof_ref,
    research_child_role_policy,
)
from magi_agent.research.source_proof import (
    ResearchSourceOpenReceiptRef,
    ResearchSourceProofRequirement,
    verify_research_source_proof,
)
from runtime_issuance_support import issue_test_runtime_authority


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-opencode-child-lifecycle",
        scopes=scopes,
    )


def _boundary(
    *,
    execution_id: str,
    run_on: str,
    spawn_depth: int,
    parent_execution_id: str | None = None,
    task_id: str | None = None,
) -> ExecutionBoundaryIdentity:
    return ExecutionBoundaryIdentity(
        executionId=execution_id,
        agentId=f"agent:{execution_id}",
        parentExecutionId=parent_execution_id,
        taskId=task_id,
        turnId="turn:opencode-scout-child",
        policyScope="research",
        policySnapshotId="policy:opencode-scout-child",
        agentRole="research",
        runOn=run_on,
        spawnDepth=spawn_depth,
    )


def _parent_boundary() -> ExecutionBoundaryIdentity:
    return _boundary(
        execution_id="parent:opencode-scout",
        run_on="main",
        spawn_depth=0,
    )


def _child_boundary(
    *,
    role_name: str = "source_inspector",
    task_id: str | None = None,
) -> ExecutionBoundaryIdentity:
    parent = _parent_boundary()
    return _boundary(
        execution_id=f"child:{role_name}",
        run_on="child",
        spawn_depth=1,
        parent_execution_id=parent.execution_id,
        task_id=task_id or f"task:{role_name}",
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


def _runtime_child_envelope(*, role_name: str = "source_inspector") -> ChildRuntimeEnvelope:
    parent = _parent_boundary()
    child = _child_boundary(role_name=role_name)
    role_policy = research_child_role_policy(role_name)
    tools = tuple(grant.tool_name for grant in role_policy.tool_grants)
    return ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_runtime_envelope"),
        issuer=OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
        mode="return",
        status="accepted",
        parentBoundary=parent,
        childBoundary=child,
        task={
            "taskId": child.task_id,
            "persona": "OpenCode scout source inspector fixture child",
            "role": "research",
            "spawnDepth": 1,
            "deliver": "return",
            "promptRef": "prompt:opencode-scout-source-inspector",
        },
        policySnapshot={
            "parentPolicySnapshotId": parent.policy_snapshot_id,
            "childPolicySnapshotId": child.policy_snapshot_id,
            "taskLocalPolicyCompatibilityRefs": (),
            "allowedToolNames": tools,
            "permissionRefs": (role_policy.role_ref,),
            "callbackHookRefs": ("callback:opencode-scout-child-envelope",),
        },
        ledgerRef=_ledger_ref(child),
        delegatedEvidenceRequirements=(
            DelegatedEvidenceRequirement(
                type="SourceInspection",
                delegation="delegated_required",
            ),
        ),
        workspaceIsolation={
            "workspacePolicy": "isolated",
            "isolationRef": "workspace-isolation:opencode-scout-child",
            "parentWorkspaceRef": "workspace:parent-redacted",
            "childWorkspaceRef": "workspace:child-redacted",
            "descriptiveOnly": True,
            "adoptionAttached": False,
            "workspaceMutated": False,
            "privateNotes": ("local child envelope fixture only",),
        },
        completionContract={
            "requiredEvidence": "tool_call",
            "requiredFiles": (),
            "requireNonEmptyResult": True,
            "summaryIsEvidence": False,
            "acceptedEvidenceMetadataOnly": True,
        },
        auditEventRefs=("audit:opencode-scout-child-issued",),
        adkPrimitiveOwnership={
            "agentOwner": "adk_future_agent",
            "runnerOwner": "adk_future_runner",
            "eventOwner": "adk_event_bridge",
            "toolOwner": "adk_function_tool_future",
            "callbackOwner": "adk_callbacks_future",
            "runnerAttached": False,
            "childExecutionAttached": False,
            "allowedToolNames": tools,
            "callbackHookRefs": ("callback:opencode-scout-child-envelope",),
        },
        authorityFlags=ChildRuntimeEnvelopeAuthorityFlags(),
        rawTranscriptRef=None,
        privateMetadata={},
    )


def _child_evidence_envelope(
    *,
    boundary: ExecutionBoundaryIdentity | None = None,
    evidence_type: str = "SourceInspection",
    fields_extra: dict[str, object] | None = None,
    public_safe_fields_extra: tuple[str, ...] = (),
    preview: str = "OpenCode scout source inspection evidence.",
    source_metadata_extra: dict[str, object] | None = None,
    summary: str = "Raw child summary claims it searched /Users/private/raw.txt",
) -> ChildEvidenceEnvelope:
    child = boundary or _child_boundary()
    public_safe_fields = ["status", *public_safe_fields_extra]
    source_metadata = {
        "executionId": child.execution_id,
        "agentId": child.agent_id,
        "parentExecutionId": child.parent_execution_id,
        "taskId": child.task_id,
        "policySnapshotId": child.policy_snapshot_id,
        "publicSafeFields": public_safe_fields,
    }
    if source_metadata_extra:
        source_metadata.update(source_metadata_extra)
    record = EvidenceRecord.model_validate(
        {
            "type": evidence_type,
            "status": "ok",
            "observedAt": 1_780_000_000,
            "source": {
                "kind": "tool_trace",
                "toolName": "FixtureSourceSnapshotRead",
                "toolCallId": "call:opencode-source-inspection",
                "metadata": source_metadata,
            },
            "fields": {
                "status": "inspected",
                "rawPath": "/Users/kevin/private/source.txt",
                **(fields_extra or {}),
            },
            "preview": preview,
            "metadata": {"publicSafeFields": public_safe_fields},
        }
    )
    verdict = EvidenceContractVerdict.model_validate(
        {
            "contractId": f"opencode-scout-{evidence_type.casefold()}",
            "ok": True,
            "state": "pass",
            "enforcement": "block_final_answer",
            "missingRequirements": [],
            "matchedEvidence": [record],
            "failures": [],
            "retryMessage": None,
        }
    )
    return ChildEvidenceEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_evidence_envelope"),
        boundary=child,
        ledgerRef=_ledger_ref(child),
        status="completed",
        evidenceRecords=(record,),
        contractVerdicts=(verdict,),
        contractDefinitions=(),
        contractsApply=True,
        report={
            "matchedTypes": [evidence_type],
            "missingTypes": [],
            "blockingFailures": [],
            "auditFailures": [],
        },
        summary=summary,
        issuedBy=OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
    )


def _source_proof_ref(envelope: ChildRuntimeEnvelope) -> ResearchChildProofRef:
    source_ref = ResearchSourceOpenReceiptRef.issue_runtime_source_ref(
        runtime_authority=_runtime_authority("research_source_proof"),
        source_ref_id="src_1",
        source_kind="web_fetch",
        receipt_kind="opened_snapshot",
        opened=True,
        content_digest="sha256:" + "1" * 64,
        inspected_at="2026-05-26T12:00:00Z",
        span_refs=("span:opencode-source",),
        redaction_status="redacted",
        public_label="OpenCode scout source proof metadata",
    )
    proof = verify_research_source_proof(
        (
            ResearchSourceProofRequirement(
                sourceRefId="src_1",
                allowedSourceKinds=("web_fetch",),
                requiredReceiptKinds=("opened_snapshot",),
                requiredSpanRefs=("span:opencode-source",),
                notBefore="2026-05-26T10:00:00Z",
                notAfter="2026-05-26T13:00:00Z",
            ),
        ),
        (source_ref,),
    )[0]
    return issue_runtime_research_child_proof_ref(
        envelope=envelope,
        expected_role="source_inspector",
        proof_kind="source_proof",
        delegated_evidence_type="SourceInspection",
        proof_evidence=proof,
    )


def test_opencode_scout_child_lifecycle_is_default_off_and_metadata_only() -> None:
    from magi_agent.recipes.opencode_child_lifecycle import (
        materialize_opencode_scout_child_lifecycle,
    )

    decision = materialize_opencode_scout_child_lifecycle(profile_key="scout_repo_fixture")
    projection = decision.public_projection()

    assert decision.status == "disabled"
    assert decision.reason_codes == ("rollout_gate_disabled",)
    assert projection["defaultOff"] is True
    assert projection["localOnly"] is True
    assert projection["fixtureOnly"] is True
    assert projection["liveAuthorityAllowed"] is False
    assert projection["modelCallsAllowed"] is False
    assert projection["toolExecutionAllowed"] is False
    assert projection["childExecutionAttached"] is False
    assert projection["adkUsageNotes"].startswith("ADK Agent/Runner child role metadata")
    assert set(projection["attachmentFlags"].values()) == {False}


def test_opencode_scout_child_lifecycle_rejects_raw_child_text_and_summary_only_output() -> None:
    from magi_agent.recipes.opencode_child_lifecycle import (
        admit_opencode_scout_child_lifecycle,
    )

    for raw_output in (
        "raw child transcript says I searched and verified everything",
        {"summary": "The child says the repo was reviewed and all claims are confirmed."},
    ):
        decision = admit_opencode_scout_child_lifecycle(
            raw_output,
            child_evidence_envelope=None,
            expected_role="source_inspector",
            child_proof_refs=(),
            rollout_enabled=True,
        )
        rendered = json.dumps(decision.public_projection(), sort_keys=True)

        assert decision.status == "blocked"
        assert decision.reason_codes == ("runtime_child_envelope_required",)
        assert "searched and verified" not in rendered
        assert "all claims are confirmed" not in rendered
        assert "summary" not in rendered


def test_opencode_scout_child_lifecycle_requires_runtime_issued_child_evidence_envelope() -> None:
    from magi_agent.recipes.opencode_child_lifecycle import (
        admit_opencode_scout_child_lifecycle,
    )

    runtime_envelope = _runtime_child_envelope()
    forged_evidence = ChildEvidenceEnvelope.model_validate(
        _child_evidence_envelope().model_dump(by_alias=True)
    )

    missing = admit_opencode_scout_child_lifecycle(
        runtime_envelope,
        child_evidence_envelope=None,
        expected_role="source_inspector",
        child_proof_refs=(_source_proof_ref(runtime_envelope),),
        rollout_enabled=True,
    )
    forged = admit_opencode_scout_child_lifecycle(
        runtime_envelope,
        child_evidence_envelope=forged_evidence,
        expected_role="source_inspector",
        child_proof_refs=(_source_proof_ref(runtime_envelope),),
        rollout_enabled=True,
    )

    assert missing.status == "blocked"
    assert missing.reason_codes == ("runtime_child_evidence_envelope_required",)
    assert forged.status == "blocked"
    assert forged.reason_codes == ("runtime_child_evidence_envelope_required",)


def test_opencode_scout_child_lifecycle_rejects_subclass_spoofed_runtime_envelopes() -> None:
    from magi_agent.recipes.opencode_child_lifecycle import (
        admit_opencode_scout_child_lifecycle,
    )

    class ForgedRuntimeEnvelope(ChildRuntimeEnvelope):
        @property
        def is_runtime_boundary_issued(self) -> bool:
            return True

    class ForgedEvidenceEnvelope(ChildEvidenceEnvelope):
        @property
        def is_runtime_boundary_issued(self) -> bool:
            return True

    runtime_envelope = _runtime_child_envelope()
    child_evidence = _child_evidence_envelope()
    forged_runtime = ForgedRuntimeEnvelope.model_validate(
        runtime_envelope.model_dump(by_alias=True)
    )
    forged_evidence = ForgedEvidenceEnvelope.model_validate(
        child_evidence.model_dump(by_alias=True)
    )

    spoofed_runtime = admit_opencode_scout_child_lifecycle(
        forged_runtime,
        child_evidence_envelope=child_evidence,
        expected_role="source_inspector",
        child_proof_refs=(_source_proof_ref(runtime_envelope),),
        rollout_enabled=True,
    )
    spoofed_evidence = admit_opencode_scout_child_lifecycle(
        runtime_envelope,
        child_evidence_envelope=forged_evidence,
        expected_role="source_inspector",
        child_proof_refs=(_source_proof_ref(runtime_envelope),),
        rollout_enabled=True,
    )

    assert spoofed_runtime.status == "blocked"
    assert spoofed_runtime.reason_codes == ("runtime_child_envelope_required",)
    assert spoofed_evidence.status == "blocked"
    assert spoofed_evidence.reason_codes == ("runtime_child_evidence_envelope_required",)


def test_opencode_scout_child_lifecycle_rejects_mismatched_child_evidence_boundary() -> None:
    from magi_agent.recipes.opencode_child_lifecycle import (
        admit_opencode_scout_child_lifecycle,
    )

    runtime_envelope = _runtime_child_envelope()
    mismatched_evidence = _child_evidence_envelope(
        boundary=_child_boundary(task_id="task:other-source-inspector"),
    )

    decision = admit_opencode_scout_child_lifecycle(
        runtime_envelope,
        child_evidence_envelope=mismatched_evidence,
        expected_role="source_inspector",
        child_proof_refs=(_source_proof_ref(runtime_envelope),),
        rollout_enabled=True,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("child_boundary_mismatch",)


def test_opencode_scout_child_lifecycle_accepts_only_envelope_and_proof_backed_child_evidence() -> None:
    from magi_agent.recipes.opencode_child_lifecycle import (
        admit_opencode_scout_child_lifecycle,
    )

    runtime_envelope = _runtime_child_envelope()
    child_evidence = _child_evidence_envelope()
    decision = admit_opencode_scout_child_lifecycle(
        runtime_envelope,
        child_evidence_envelope=child_evidence,
        expected_role="source_inspector",
        child_proof_refs=(_source_proof_ref(runtime_envelope),),
        rollout_enabled=True,
    )
    projection = decision.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert decision.status == "accepted"
    assert decision.reason_codes == ("child_evidence_accepted",)
    assert projection["childAdmissionDecision"]["decision"] == "accept"
    assert projection["childAggregateReport"]["state"] == "pass"
    assert projection["childAggregateReport"]["sessionRuntimeAttached"] is False
    assert projection["childAggregateReport"]["children"][0]["matchedTypes"] == [
        "SourceInspection"
    ]
    assert "childEvidenceRef" in projection["childAdmissionDecision"]
    assert "Raw child summary claims" not in rendered
    assert "/Users/kevin" not in rendered
    assert "private/source.txt" not in rendered
    assert "rawPath" not in rendered


def test_opencode_scout_child_lifecycle_projection_drops_callback_query_and_session_metadata() -> None:
    from magi_agent.recipes.opencode_child_lifecycle import (
        admit_opencode_scout_child_lifecycle,
    )

    runtime_envelope = _runtime_child_envelope()
    child_evidence = _child_evidence_envelope(
        fields_extra={
            "authorityClaim": "modelCalled true and liveToolDispatched true",
            "toolStory": (
                "called OpenAI API, used GPT 5, ran WebSearch and WebFetch, "
                "wrote memory, posted channel message, and modified workspace files"
            ),
        },
        public_safe_fields_extra=("authorityClaim", "toolStory"),
        preview=(
            "memoryWritten true and channelDelivered true after calling OpenAI API "
            "and running WebSearch"
        ),
        source_metadata_extra={
            "adkUsageNotes": "callback-private-67890 code=abc123",
            "callbackRef": "callback-private-67890",
            "childExecutionAttached": True,
            "liveAuthorityAllowed": True,
            "live_authority_allowed": True,
            "adk-runner-invoked": True,
            "modelCallsAllowed": True,
            "model_calls_allowed": True,
            "note": "modelCallsAllowed true and productionAuthority true",
            "rawChildOutputProjectionAllowed": "raw-token=unsafe",
            "queryString": "code=abc123",
            "claim": "the child executed a live model and browser with workspace mutation",
            "runner_attached": True,
            "sessionId": "session-public-looking-12345",
            "sessionRuntimeAttached": "session-public-looking-12345",
            "sourceUrl": "https://internal.example/callback?code=abc123",
            "toolExecutionAllowed": True,
            "tool_execution_allowed": True,
        },
    )

    decision = admit_opencode_scout_child_lifecycle(
        runtime_envelope,
        child_evidence_envelope=child_evidence,
        expected_role="source_inspector",
        child_proof_refs=(_source_proof_ref(runtime_envelope),),
        rollout_enabled=True,
    )
    rendered = json.dumps(decision.public_projection(), sort_keys=True)

    assert decision.status == "accepted"
    assert decision.public_projection()["adkUsageNotes"].startswith("ADK Agent/Runner")
    assert decision.public_projection()["childAggregateReport"]["sessionRuntimeAttached"] is False
    assert '"adkUsageNotes": "callback-private-67890' not in rendered
    assert '"childExecutionAttached": true' not in rendered
    assert '"liveAuthorityAllowed": true' not in rendered
    assert '"live_authority_allowed": true' not in rendered
    assert '"adk-runner-invoked": true' not in rendered
    assert '"modelCallsAllowed": true' not in rendered
    assert '"model_calls_allowed": true' not in rendered
    assert "modelCallsAllowed true" not in rendered
    assert "productionAuthority true" not in rendered
    assert "modelCalled true" not in rendered
    assert "liveToolDispatched true" not in rendered
    assert '"rawChildOutputProjectionAllowed": "raw-token=unsafe"' not in rendered
    assert "executed a live model" not in rendered
    assert "workspace mutation" not in rendered
    assert "memoryWritten true" not in rendered
    assert "channelDelivered true" not in rendered
    assert "called OpenAI API" not in rendered
    assert "used GPT 5" not in rendered
    assert "WebSearch" not in rendered
    assert "WebFetch" not in rendered
    assert "wrote memory" not in rendered
    assert "posted channel message" not in rendered
    assert "modified workspace files" not in rendered
    assert '"evidence":' not in rendered
    assert '"preview":' not in rendered
    assert '"fields":' not in rendered
    assert '"verdicts":' not in rendered
    assert '"runner_attached": true' not in rendered
    assert '"sessionRuntimeAttached": "session-public-looking-12345"' not in rendered
    assert '"toolExecutionAllowed": true' not in rendered
    assert '"tool_execution_allowed": true' not in rendered
    assert "callbackRef" not in rendered
    assert "callback-private-67890" not in rendered
    assert "queryString" not in rendered
    assert "code=abc123" not in rendered
    assert "sessionId" not in rendered
    assert "session-public-looking-12345" not in rendered
    assert "sourceUrl" not in rendered
    assert "internal.example" not in rendered


def test_opencode_scout_child_lifecycle_rejects_unrelated_child_evidence_type() -> None:
    from magi_agent.recipes.opencode_child_lifecycle import (
        admit_opencode_scout_child_lifecycle,
    )

    runtime_envelope = _runtime_child_envelope()
    unrelated_child_evidence = _child_evidence_envelope(evidence_type="PlanVerifier")

    decision = admit_opencode_scout_child_lifecycle(
        runtime_envelope,
        child_evidence_envelope=unrelated_child_evidence,
        expected_role="source_inspector",
        child_proof_refs=(_source_proof_ref(runtime_envelope),),
        rollout_enabled=True,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("delegated_child_evidence_type_missing",)


def test_opencode_scout_child_lifecycle_missing_proof_is_retry_not_acceptance() -> None:
    from magi_agent.recipes.opencode_child_lifecycle import (
        admit_opencode_scout_child_lifecycle,
    )

    decision = admit_opencode_scout_child_lifecycle(
        _runtime_child_envelope(),
        child_evidence_envelope=_child_evidence_envelope(),
        expected_role="source_inspector",
        child_proof_refs=(),
        rollout_enabled=True,
    )

    assert decision.status == "retry"
    assert decision.reason_codes == ("missing_required_child_proof",)
    assert decision.public_projection()["childAdmissionDecision"]["decision"] == "retry"


def test_opencode_scout_child_lifecycle_import_boundary_has_no_live_runtime_surfaces() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.recipes.opencode_child_lifecycle")
forbidden = (
    "google.adk.runners",
    "google.adk.sessions",
    "google.adk.models",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.tools.dispatcher",
    "magi_agent.transport.chat",
    "magi_agent.memory.adapters",
    "socket",
    "subprocess",
    "requests",
    "httpx",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden)
]
if loaded:
    raise AssertionError(f"OpenCode child lifecycle loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
