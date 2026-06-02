from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from openmagi_core_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    ChildRuntimeEnvelopeAuthorityFlags,
)
from runtime_issuance_support import issue_test_runtime_authority
from openmagi_core_agent.evidence.subagent import (
    OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
    DelegatedEvidenceRequirement,
    EvidenceBoundaryLedgerRef,
    ExecutionBoundaryIdentity,
)
from openmagi_core_agent.research.child_roles import (
    RESEARCH_CHILD_ROLE_NAMES,
    ResearchChildEnvelopeEvidenceRef,
    ResearchChildEvidenceAdmissionDecision,
    ResearchChildProofRef,
    ResearchChildToolGrant,
    admit_research_child_evidence,
    build_default_research_child_role_policies,
    issue_runtime_research_child_proof_ref,
    research_child_role_policy,
)
from openmagi_core_agent.research.action_claims import (
    ResearchActionProofReceiptRef,
    ResearchActionProofRequirement,
    detect_research_action_claims,
    verify_research_action_claims,
)
from openmagi_core_agent.research.claim_graph import ResearchClaimSupportRef
from openmagi_core_agent.research.source_proof import (
    ResearchSourceOpenReceiptRef,
    ResearchSourceProofRequirement,
    verify_research_source_proof,
)


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-research-child-roles",
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
        turnId="turn:research-child",
        policyScope="research",
        policySnapshotId="policy:research-child",
        agentRole="research",
        runOn=run_on,
        spawnDepth=spawn_depth,
    )


def _child_envelope(
    *,
    role_name: str = "source_inspector",
    evidence_types: tuple[str, ...] = ("SourceInspection",),
    allowed_tool_names: tuple[str, ...] | None = None,
    status: str = "accepted",
    mode: str = "return",
    raw_transcript_ref: str | None = None,
    private_metadata: dict[str, object] | None = None,
    evidence_delegation: str = "delegated_required",
    required_evidence: str = "tool_call",
    permission_refs: tuple[str, ...] | None = None,
) -> ChildRuntimeEnvelope:
    parent = _boundary(execution_id="parent:research-child", run_on="main", spawn_depth=0)
    child = _boundary(
        execution_id=f"child:{role_name}",
        run_on="child",
        spawn_depth=1,
        parent_execution_id=parent.execution_id,
        task_id=f"task:{role_name}",
    )
    role_policy = research_child_role_policy(role_name)
    tools = (
        allowed_tool_names
        if allowed_tool_names is not None
        else tuple(grant.tool_name for grant in role_policy.tool_grants)
    )
    return ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_runtime_envelope"),
        **{
            "issuer": OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
            "mode": mode,
            "status": status,
            "parentBoundary": parent,
            "childBoundary": child,
            "task": {
                "taskId": child.task_id,
                "persona": f"{role_name} local fixture child",
                "role": "research",
                "spawnDepth": 1,
                "deliver": "return",
                "promptRef": f"prompt:{role_name}",
            },
            "policySnapshot": {
                "parentPolicySnapshotId": parent.policy_snapshot_id,
                "childPolicySnapshotId": child.policy_snapshot_id,
                "taskLocalPolicyCompatibilityRefs": (),
                "allowedToolNames": tools,
                "permissionRefs": (
                    permission_refs if permission_refs is not None else (role_policy.role_ref,)
                ),
                "callbackHookRefs": ("callback:research-child-envelope",),
            },
            "ledgerRef": EvidenceBoundaryLedgerRef(
                ledgerId=f"ledger:{role_name}",
                executionId=child.execution_id,
                agentId=child.agent_id,
                parentExecutionId=parent.execution_id,
                taskId=child.task_id,
                policySnapshotId=child.policy_snapshot_id,
                childLedgerRefs=(),
            ),
            "delegatedEvidenceRequirements": tuple(
                DelegatedEvidenceRequirement(type=evidence_type, delegation=evidence_delegation)
                for evidence_type in evidence_types
            ),
            "workspaceIsolation": {
                "workspacePolicy": "isolated",
                "isolationRef": f"workspace-isolation:{role_name}",
                "parentWorkspaceRef": "workspace:parent-redacted",
                "childWorkspaceRef": "workspace:child-redacted",
                "descriptiveOnly": True,
                "adoptionAttached": False,
                "workspaceMutated": False,
                "privateNotes": ("local fake child role only",),
            },
            "completionContract": {
                "requiredEvidence": required_evidence,
                "requiredFiles": (),
                "requireNonEmptyResult": True,
                "summaryIsEvidence": False,
                "acceptedEvidenceMetadataOnly": True,
            },
            "auditEventRefs": (f"audit:{role_name}:issued",),
            "adkPrimitiveOwnership": {
                "agentOwner": "adk_future_agent",
                "runnerOwner": "adk_future_runner",
                "eventOwner": "adk_event_bridge",
                "toolOwner": "adk_function_tool_future",
                "callbackOwner": "adk_callbacks_future",
                "runnerAttached": False,
                "childExecutionAttached": False,
                "allowedToolNames": tools,
                "callbackHookRefs": ("callback:research-child-envelope",),
            },
            "authorityFlags": ChildRuntimeEnvelopeAuthorityFlags(),
            "rawTranscriptRef": raw_transcript_ref,
            "privateMetadata": private_metadata or {},
        },
    )


def _proof_ref(
    envelope: ChildRuntimeEnvelope,
    *,
    expected_role: str,
    proof_kind: str,
    evidence_type: str,
) -> ResearchChildProofRef:
    return issue_runtime_research_child_proof_ref(
        envelope=envelope,
        expected_role=expected_role,
        proof_kind=proof_kind,
        delegated_evidence_type=evidence_type,
        proof_evidence=_proof_evidence(proof_kind),
    )


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def _proof_evidence(proof_kind: str) -> object:
    if proof_kind == "source_proof":
        source_ref = ResearchSourceOpenReceiptRef.issue_runtime_source_ref(
            runtime_authority=_runtime_authority("research_source_proof"),
            source_ref_id="src_1",
            source_kind="web_fetch",
            receipt_kind="opened_snapshot",
            opened=True,
            content_digest=_digest("1"),
            inspected_at="2026-05-26T12:00:00Z",
            span_refs=("span:pricing",),
            redaction_status="redacted",
            public_label="Source proof metadata",
        )
        return verify_research_source_proof(
            (
                ResearchSourceProofRequirement(
                    sourceRefId="src_1",
                    allowedSourceKinds=("web_fetch",),
                    requiredReceiptKinds=("opened_snapshot",),
                    requiredSpanRefs=("span:pricing",),
                    notBefore="2026-05-26T10:00:00Z",
                    notAfter="2026-05-26T13:00:00Z",
                ),
            ),
            (source_ref,),
        )[0]
    if proof_kind == "action_proof":
        action_claim = detect_research_action_claims("I checked the source.")[0]
        receipt = ResearchActionProofReceiptRef.issue_runtime_receipt(
            runtime_authority=_runtime_authority("research_action_proof"),
            receipt_id="receipt:checked-source",
            action_verb="checked",
            receipt_kind="toolhost_receipt",
            tool_id="tool:fixture-search",
            source_id="source:fixture",
            observed_at="2026-05-26T12:00:00Z",
            public_label="Runtime proof metadata",
        )
        return verify_research_action_claims(
            (action_claim,),
            (receipt,),
            requirements=(
                ResearchActionProofRequirement(
                    claimId=action_claim.claim_id,
                    requiredActionVerb="checked",
                    requiredReceiptKinds=("toolhost_receipt",),
                    requiredToolIds=("tool:fixture-search",),
                    requiredSourceIds=("source:fixture",),
                    notBefore="2026-05-26T10:00:00Z",
                    notAfter="2026-05-26T13:00:00Z",
                ),
            ),
        )[0]
    if proof_kind == "claim_proof":
        return ResearchClaimSupportRef.issue_verified_support_ref(
            runtime_authority=_runtime_authority("research_claim_support"),
            support_ref_id="support:pricing",
            source_ref_id="src_1",
            span_refs=("span:pricing",),
            source_digest=_digest("1"),
            evidence_digest=_digest("2"),
            evidence_kind="source_span",
            support_verdict="supported",
            freshness_verdict="current",
            relevance_verdict="relevant",
            public_label="Verified source span",
        )
    raise AssertionError(f"unsupported test proof kind: {proof_kind}")


def test_default_research_child_roles_are_read_only_fixture_only() -> None:
    policies = build_default_research_child_role_policies()

    assert tuple(policy.role_name for policy in policies) == RESEARCH_CHILD_ROLE_NAMES
    for policy in policies:
        assert policy.default_off is True
        assert policy.local_only is True
        assert policy.fake_provider_only is True
        assert policy.live_execution_allowed is False
        assert policy.adk_runner_attached is False
        assert policy.function_tool_attached is False
        assert policy.role_ref == f"research_child_role:{policy.role_name}"
        assert policy.tool_grants
        for grant in policy.tool_grants:
            assert grant.access == "read_only"
            assert grant.fixture_only is True
            assert grant.live_execution_allowed is False
            assert grant.tool_host_execution_allowed is False
            assert grant.tool_name.startswith("Fixture")


def test_write_or_live_child_tool_grants_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ResearchChildToolGrant(
            toolName="FixtureSourceWrite",
            access="write",
            fixtureOnly=True,
            liveExecutionAllowed=False,
            toolHostExecutionAllowed=False,
        )

    with pytest.raises(ValidationError):
        ResearchChildToolGrant(
            toolName="LiveWebSearch",
            access="read_only",
            fixtureOnly=True,
            liveExecutionAllowed=False,
            toolHostExecutionAllowed=False,
        )

    with pytest.raises(ValidationError):
        ResearchChildToolGrant(
            toolName="FixtureRawTranscriptRead",
            access="read_only",
            fixtureOnly=True,
            liveExecutionAllowed=False,
            toolHostExecutionAllowed=False,
        )


def test_child_raw_text_is_not_research_evidence() -> None:
    decision = admit_research_child_evidence(
        {"summary": "The child says it searched and verified the source."},
        expected_role="research_searcher",
        required_proof_kinds=("action_proof",),
    )

    assert decision.decision == "reject"
    assert decision.reason_codes == ("child_raw_text_not_evidence",)
    public = decision.public_projection()
    dumped = json.dumps(public, sort_keys=True)
    assert "searched and verified" not in dumped
    assert "summary" not in dumped


def test_child_raw_hidden_reasoning_mapping_is_not_research_evidence() -> None:
    decision = admit_research_child_evidence(
        {"hiddenReasoning": "chain of thought should not be evidence"},
        expected_role="research_verifier",
        required_proof_kinds=("claim_proof",),
    )

    assert decision.decision == "reject"
    assert decision.reason_codes == ("child_raw_private_payload_rejected",)
    assert "chain of thought" not in json.dumps(decision.public_projection(), sort_keys=True)


def test_child_role_cannot_be_used_for_undeclared_proof_kind() -> None:
    with pytest.raises(ValueError, match="allowed by the research child role"):
        admit_research_child_evidence(
            _child_envelope(role_name="research_searcher", evidence_types=("SourceInspection",)),
            expected_role="research_searcher",
            required_proof_kinds=("source_proof",),
        )


def test_invalid_required_proof_kind_fails_closed() -> None:
    with pytest.raises(ValueError, match="known research proof kinds"):
        admit_research_child_evidence(
            _child_envelope(role_name="research_searcher", evidence_types=("SourceInspection",)),
            expected_role="research_searcher",
            required_proof_kinds=("bogus",),
        )


def test_delegated_requirement_names_alone_do_not_satisfy_child_proof() -> None:
    decision = admit_research_child_evidence(
        _child_envelope(role_name="source_inspector"),
        expected_role="source_inspector",
        required_proof_kinds=("source_proof",),
    )

    assert decision.decision == "retry"
    assert decision.reason_codes == ("missing_required_child_proof",)


def test_child_proof_refs_require_delegated_required_metadata() -> None:
    envelope = _child_envelope(
        role_name="source_inspector",
        evidence_delegation="local_only",
    )

    with pytest.raises(ValueError, match="delegated_required"):
        _proof_ref(
            envelope,
            expected_role="source_inspector",
            proof_kind="source_proof",
            evidence_type="SourceInspection",
        )


def test_text_completion_child_envelope_is_rejected() -> None:
    envelope = _child_envelope(
        role_name="source_inspector",
        required_evidence="text",
    )

    decision = admit_research_child_evidence(
        envelope,
        expected_role="source_inspector",
        required_proof_kinds=("source_proof",),
    )

    assert decision.decision == "reject"
    assert decision.reason_codes == ("child_summary_not_evidence",)


def test_extra_child_permission_refs_are_rejected() -> None:
    envelope = _child_envelope(
        role_name="source_inspector",
        permission_refs=("research_child_role:source_inspector", "permission:live-web"),
    )

    decision = admit_research_child_evidence(
        envelope,
        expected_role="source_inspector",
        required_proof_kinds=("source_proof",),
    )

    assert decision.decision == "reject"
    assert decision.reason_codes == ("child_role_ref_missing",)


def test_runtime_child_envelope_can_satisfy_source_claim_and_action_proof() -> None:
    envelope = _child_envelope(
        role_name="research_verifier",
        evidence_types=(
            "SourceInspection",
            "custom:ResearchClaimProof",
            "custom:ResearchActionProof",
        ),
    )

    decision = admit_research_child_evidence(
        envelope,
        expected_role="research_verifier",
        required_proof_kinds=("source_proof", "claim_proof", "action_proof"),
        child_proof_refs=(
            _proof_ref(
                envelope,
                expected_role="research_verifier",
                proof_kind="source_proof",
                evidence_type="SourceInspection",
            ),
            _proof_ref(
                envelope,
                expected_role="research_verifier",
                proof_kind="claim_proof",
                evidence_type="custom:ResearchClaimProof",
            ),
            _proof_ref(
                envelope,
                expected_role="research_verifier",
                proof_kind="action_proof",
                evidence_type="custom:ResearchActionProof",
            ),
        ),
    )

    assert decision.decision == "accept"
    assert decision.satisfied_proof_kinds == (
        "action_proof",
        "claim_proof",
        "source_proof",
    )
    assert decision.child_evidence_ref is not None
    assert decision.child_evidence_ref.issuer == OPENMAGI_RUNTIME_ENVELOPE_ISSUER
    assert decision.child_evidence_ref.completion_summary_is_evidence is False
    public = decision.public_projection()
    assert public["decision"] == "accept"
    assert "childEvidenceRef" in public


def test_parent_accept_retry_reject_decisions_are_deterministic() -> None:
    accepted = _child_envelope(role_name="source_inspector")
    incomplete = _child_envelope(role_name="source_inspector", evidence_types=())
    rejected = _child_envelope(
        role_name="source_inspector",
        evidence_types=("SourceInspection",),
        status="blocked",
        mode="blocked",
    )

    first_accept = admit_research_child_evidence(
        accepted,
        expected_role="source_inspector",
        required_proof_kinds=("source_proof",),
        child_proof_refs=(
            _proof_ref(
                accepted,
                expected_role="source_inspector",
                proof_kind="source_proof",
                evidence_type="SourceInspection",
            ),
        ),
    )
    second_accept = admit_research_child_evidence(
        accepted,
        expected_role="source_inspector",
        required_proof_kinds=("source_proof",),
        child_proof_refs=(
            _proof_ref(
                accepted,
                expected_role="source_inspector",
                proof_kind="source_proof",
                evidence_type="SourceInspection",
            ),
        ),
    )
    retry = admit_research_child_evidence(
        incomplete,
        expected_role="source_inspector",
        required_proof_kinds=("source_proof",),
    )
    reject = admit_research_child_evidence(
        rejected,
        expected_role="source_inspector",
        required_proof_kinds=("source_proof",),
    )

    assert first_accept.public_projection() == second_accept.public_projection()
    assert first_accept.decision == "accept"
    assert retry.decision == "retry"
    assert retry.reason_codes == ("missing_required_child_proof",)
    assert reject.decision == "reject"
    assert reject.reason_codes == ("child_envelope_not_accepted",)


def test_child_hidden_reasoning_raw_transcripts_and_raw_tool_logs_are_rejected() -> None:
    hidden = _child_envelope(
        private_metadata={"hiddenReasoning": "chain of thought should not be evidence"},
    )
    raw_logs = _child_envelope(private_metadata={"rawToolLogs": "tool output with TOKEN=abc123"})
    transcript_ref = _child_envelope(raw_transcript_ref="transcript:private-child-turn")

    for envelope in (hidden, raw_logs, transcript_ref):
        decision = admit_research_child_evidence(
            envelope,
            expected_role="source_inspector",
            required_proof_kinds=("source_proof",),
        )
        dumped = json.dumps(decision.public_projection(), sort_keys=True)
        assert decision.decision == "reject"
        assert decision.reason_codes == ("child_raw_private_payload_rejected",)
        assert "chain of thought" not in dumped
        assert "rawToolLogs" not in dumped
        assert "TOKEN" not in dumped
        assert "transcript:private-child-turn" not in dumped


def test_undeclared_child_tool_grants_are_rejected() -> None:
    decision = admit_research_child_evidence(
        _child_envelope(allowed_tool_names=("FixtureSourceRead", "Bash")),
        expected_role="source_inspector",
        required_proof_kinds=("source_proof",),
    )

    assert decision.decision == "reject"
    assert decision.reason_codes == ("undeclared_child_tool_grant",)


def test_forged_child_decisions_and_refs_do_not_project_as_evidence() -> None:
    envelope = _child_envelope(role_name="source_inspector")
    accepted = admit_research_child_evidence(
        envelope,
        expected_role="source_inspector",
        required_proof_kinds=("source_proof",),
        child_proof_refs=(
            _proof_ref(
                envelope,
                expected_role="source_inspector",
                proof_kind="source_proof",
                evidence_type="SourceInspection",
            ),
        ),
    )
    assert accepted.child_evidence_ref is not None
    forged_ref = ResearchChildEnvelopeEvidenceRef.model_validate(
        accepted.child_evidence_ref.model_dump(by_alias=True)
    )

    with pytest.raises(ValueError, match="issued by research child role admission"):
        forged_ref.public_projection()

    rejected = admit_research_child_evidence(
        "plain child text",
        expected_role="research_searcher",
        required_proof_kinds=("action_proof",),
    )
    forged_decision = ResearchChildEvidenceAdmissionDecision.model_validate(
        rejected.model_dump(by_alias=True)
    )

    with pytest.raises(ValueError, match="issued by child role admission"):
        forged_decision.public_projection()


def test_forged_child_proof_refs_are_rejected() -> None:
    envelope = _child_envelope(role_name="source_inspector")
    proof_ref = _proof_ref(
        envelope,
        expected_role="source_inspector",
        proof_kind="source_proof",
        evidence_type="SourceInspection",
    )
    forged = ResearchChildProofRef.model_validate(proof_ref.model_dump(by_alias=True))

    decision = admit_research_child_evidence(
        envelope,
        expected_role="source_inspector",
        required_proof_kinds=("source_proof",),
        child_proof_refs=(forged,),
    )

    assert decision.decision == "reject"
    assert decision.reason_codes == ("child_proof_ref_invalid",)
