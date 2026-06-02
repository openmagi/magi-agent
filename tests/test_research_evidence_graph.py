from __future__ import annotations

import json
import re
from hashlib import sha256

import pytest

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
from openmagi_core_agent.research.acceptance_criteria import (
    ResearchAcceptanceCriterion,
    ResearchAcceptanceCriteriaSet,
    ResearchAcceptanceEvidenceRef,
)
from openmagi_core_agent.research.action_claims import (
    ResearchActionProofReceiptRef,
    ResearchActionProofRequirement,
    detect_research_action_claims,
    project_research_action_proof_verdicts,
    verify_research_action_claims,
)
from openmagi_core_agent.research.claim_graph import (
    ResearchClaimGraph,
    ResearchClaimSupportRef,
    build_research_claim_node,
)
from openmagi_core_agent.research.evidence_graph import (
    ResearchChildEvidenceRef,
    ResearchEvidenceGraph,
    ResearchMissingEvidenceReason,
)
from openmagi_core_agent.research.source_proof import (
    ResearchSourceOpenReceiptRef,
    ResearchSourceProofRequirement,
    verify_research_source_proof,
)


def _digest(char: str = "a") -> str:
    return "sha256:" + char * 64


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-evidence-graph",
        scopes=scopes,
    )


def _fixture_child_secret() -> str:
    return "sk" + "-child-secret"


def _fixture_child_transcript_preview() -> str:
    return "raw child transcript with " + _fixture_child_secret()


def _fixture_workspace_path() -> str:
    return "/workspace/" + "bot/private"


def _fixture_bearer_token() -> str:
    return "unsafe" + "-token"


def _fixture_auth_value() -> str:
    return "Bear" + "er " + _fixture_bearer_token()


def _public_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _source_receipt() -> ResearchSourceOpenReceiptRef:
    return ResearchSourceOpenReceiptRef.issue_runtime_source_ref(
        runtime_authority=_runtime_authority("research_source_proof"),
        source_ref_id="src_1",
        source_kind="web_fetch",
        receipt_kind="opened_snapshot",
        opened=True,
        content_digest=_digest("1"),
        inspected_at="2026-05-26T12:00:00Z",
        span_refs=("span:pricing",),
        redaction_status="redacted",
        public_label="Public pricing metadata",
    )


def _source_verdicts(
    *,
    not_before: str | None = "2026-05-26T10:00:00Z",
    not_after: str | None = "2026-05-26T13:00:00Z",
):
    return verify_research_source_proof(
        (
            ResearchSourceProofRequirement(
                sourceRefId="src_1",
                allowedSourceKinds=("web_fetch",),
                requiredReceiptKinds=("opened_snapshot",),
                requiredSpanRefs=("span:pricing",),
                notBefore=not_before,
                notAfter=not_after,
            ),
        ),
        (_source_receipt(),),
    )


def _action_verdicts():
    claim = detect_research_action_claims("I checked the pricing source.")[0]
    receipt = ResearchActionProofReceiptRef.issue_runtime_receipt(
        runtime_authority=_runtime_authority("research_action_proof"),
        receipt_id="receipt:checked-1",
        action_verb="checked",
        receipt_kind="toolhost_receipt",
        tool_id="tool:web-search",
        source_id="source:pricing-page",
        observed_at="2026-05-26T12:00:00Z",
        public_label="Runtime toolhost receipt",
    )
    return verify_research_action_claims(
        (claim,),
        (receipt,),
        requirements=(
            ResearchActionProofRequirement(
                claimId=claim.claim_id,
                requiredActionVerb="checked",
                requiredReceiptKinds=("toolhost_receipt",),
                requiredToolIds=("tool:web-search",),
                requiredSourceIds=("source:pricing-page",),
                notBefore="2026-05-26T10:00:00Z",
                notAfter="2026-05-26T13:00:00Z",
            ),
        ),
    )


def _claim_graph(
    *,
    support_ref_id: str = "support:pricing",
    source_digest: str | None = None,
    span_refs: tuple[str, ...] = ("span:pricing",),
) -> ResearchClaimGraph:
    support_ref = ResearchClaimSupportRef.issue_verified_support_ref(
        runtime_authority=_runtime_authority("research_claim_support"),
        support_ref_id=support_ref_id,
        source_ref_id="src_1",
        span_refs=span_refs,
        source_digest=source_digest or _digest("1"),
        evidence_digest=_digest("2"),
        evidence_kind="source_span",
        support_verdict="supported",
        freshness_verdict="current",
        relevance_verdict="relevant",
        public_label="Verified source span",
    )
    claim = build_research_claim_node(
        claim_id="claim:pricing-page",
        claim_text_digest=_digest("3"),
        claim_kind="factual",
        claim_preview="The service has a current public pricing page.",
        support_refs=(support_ref,),
    )
    return ResearchClaimGraph(claimGraphId="claim-graph:pricing", claims=(claim,))


def _acceptance_criteria(
    *,
    evidence_ref_id: str = "src_1",
    evidence_type: str = "source_inspection",
    required_evidence_types: tuple[str, ...] = ("source_inspection",),
    support_verdict: str = "supports",
    freshness_verdict: str = "current",
    digest: str | None = None,
    span_refs: tuple[str, ...] = ("span:pricing",),
) -> ResearchAcceptanceCriteriaSet:
    return ResearchAcceptanceCriteriaSet(
        criteriaSetId="criteria-set:pricing",
        targetLabel="OpenMagi pricing",
        criteria=(
            ResearchAcceptanceCriterion(
                criteriaId="criteria:pricing-source",
                description="Pricing answer requires current source inspection evidence.",
                requiredEvidenceTypes=required_evidence_types,
                optionalEvidenceTypes=(),
                sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
                completionMode="required",
                evidenceRefs=(
                    ResearchAcceptanceEvidenceRef(
                        evidenceRefId=evidence_ref_id,
                        evidenceType=evidence_type,
                        supportVerdict=support_verdict,
                        freshnessVerdict=freshness_verdict,
                        digest=digest or _digest("1"),
                        spanRefs=span_refs,
                        publicLabel="Source proof metadata",
                    ),
                ),
            ),
        ),
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
        turnId="turn:research",
        policyScope="research",
        policySnapshotId="policy:research",
        agentRole="research",
        runOn=run_on,
        spawnDepth=spawn_depth,
    )


def _child_envelope() -> ChildRuntimeEnvelope:
    parent = _boundary(execution_id="parent:research", run_on="main", spawn_depth=0)
    child = _boundary(
        execution_id="child:research-1",
        run_on="child",
        spawn_depth=1,
        parent_execution_id=parent.execution_id,
        task_id="task:pricing-child",
    )
    return ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_runtime_envelope"),
        **{
            "issuer": OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
            "mode": "return",
            "status": "accepted",
            "parentBoundary": parent,
            "childBoundary": child,
            "task": {
                "taskId": "task:pricing-child",
                "persona": "research child",
                "role": "research",
                "spawnDepth": 1,
                "deliver": "return",
                "promptRef": "prompt:pricing-child",
            },
            "policySnapshot": {
                "parentPolicySnapshotId": parent.policy_snapshot_id,
                "childPolicySnapshotId": child.policy_snapshot_id,
                "taskLocalPolicyCompatibilityRefs": (),
                "allowedToolNames": (),
                "permissionRefs": (),
                "callbackHookRefs": (),
            },
            "ledgerRef": EvidenceBoundaryLedgerRef(
                ledgerId="ledger:child-research-1",
                executionId=child.execution_id,
                agentId=child.agent_id,
                parentExecutionId=parent.execution_id,
                taskId=child.task_id,
                policySnapshotId=child.policy_snapshot_id,
                childLedgerRefs=(),
            ),
            "delegatedEvidenceRequirements": (
                DelegatedEvidenceRequirement(type="SourceInspection", delegation="delegated_required"),
            ),
            "workspaceIsolation": {
                "workspacePolicy": "isolated",
                "isolationRef": "workspace-isolation:pricing-child",
                "parentWorkspaceRef": "workspace:parent-redacted",
                "childWorkspaceRef": "workspace:child-redacted",
                "descriptiveOnly": True,
                "adoptionAttached": False,
                "workspaceMutated": False,
                "privateNotes": ("local fake child only",),
            },
            "completionContract": {
                "requiredEvidence": "tool_call",
                "requiredFiles": (),
                "requireNonEmptyResult": True,
                "summaryIsEvidence": False,
                "acceptedEvidenceMetadataOnly": True,
            },
            "auditEventRefs": ("audit:child-issued",),
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
                "rawTranscriptPreview": _fixture_child_transcript_preview(),
                "workspacePath": _fixture_workspace_path(),
                "authorization": _fixture_auth_value(),
            },
        },
    )


def _blocked_child_envelope() -> ChildRuntimeEnvelope:
    parent = _boundary(execution_id="parent:research", run_on="main", spawn_depth=0)
    child = _boundary(
        execution_id="child:research-blocked",
        run_on="child",
        spawn_depth=1,
        parent_execution_id=parent.execution_id,
        task_id="task:blocked-child",
    )
    return ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_runtime_envelope"),
        **{
            "issuer": OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
            "mode": "blocked",
            "status": "blocked",
            "parentBoundary": parent,
            "childBoundary": child,
            "task": {
                "taskId": "task:blocked-child",
                "persona": "research child",
                "role": "research",
                "spawnDepth": 1,
                "deliver": "return",
                "promptRef": "prompt:blocked-child",
            },
            "policySnapshot": {
                "parentPolicySnapshotId": parent.policy_snapshot_id,
                "childPolicySnapshotId": child.policy_snapshot_id,
                "taskLocalPolicyCompatibilityRefs": (),
                "allowedToolNames": (),
                "permissionRefs": (),
                "callbackHookRefs": (),
            },
            "ledgerRef": EvidenceBoundaryLedgerRef(
                ledgerId="ledger:child-research-blocked",
                executionId=child.execution_id,
                agentId=child.agent_id,
                parentExecutionId=parent.execution_id,
                taskId=child.task_id,
                policySnapshotId=child.policy_snapshot_id,
                childLedgerRefs=(),
            ),
            "delegatedEvidenceRequirements": (
                DelegatedEvidenceRequirement(type="SourceInspection", delegation="delegated_required"),
            ),
            "workspaceIsolation": {
                "workspacePolicy": "isolated",
                "isolationRef": "workspace-isolation:blocked-child",
                "parentWorkspaceRef": "workspace:parent-redacted",
                "childWorkspaceRef": "workspace:child-redacted",
                "descriptiveOnly": True,
                "adoptionAttached": False,
                "workspaceMutated": False,
                "privateNotes": ("blocked local fake child only",),
            },
            "completionContract": {
                "requiredEvidence": "tool_call",
                "requiredFiles": (),
                "requireNonEmptyResult": True,
                "summaryIsEvidence": False,
                "acceptedEvidenceMetadataOnly": True,
            },
            "auditEventRefs": ("audit:blocked-child-issued",),
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
            "rawTranscriptRef": "transcript:private-blocked-child-turn",
            "privateMetadata": {},
        },
    )


def _graph(**overrides: object) -> ResearchEvidenceGraph:
    kwargs: dict[str, object] = {
        "evidence_graph_id": "evidence-graph:pricing",
        "action_proof_verdicts": _action_verdicts(),
        "source_proof_verdicts": _source_verdicts(),
        "claim_graph": _claim_graph(),
        "acceptance_criteria": _acceptance_criteria(),
        "child_evidence_envelopes": (_child_envelope(),),
        "missing_evidence_reasons": (
            ResearchMissingEvidenceReason(
                reasonId="missing:competitor-comparison",
                subjectRefId="criteria:pricing-source",
                evidenceType="competitor_context",
                reasonCode="not_required_for_criterion",
            ),
        ),
    }
    kwargs.update(overrides)
    return ResearchEvidenceGraph.from_runtime_evidence(**kwargs)


def test_graph_rejects_dangling_acceptance_evidence_refs() -> None:
    with pytest.raises(ValueError, match="dangling evidenceRefId"):
        _graph(acceptance_criteria=_acceptance_criteria(evidence_ref_id="src_404"))


def test_acceptance_refs_cannot_self_assert_freshness_over_source_verifier() -> None:
    with pytest.raises(ValueError, match="freshnessVerdict"):
        _graph(
            source_proof_verdicts=_source_verdicts(not_before=None, not_after=None),
            claim_graph=ResearchClaimGraph(claimGraphId="claim-graph:no-claims", claims=()),
            acceptance_criteria=_acceptance_criteria(freshness_verdict="current"),
        )


def test_claim_support_must_match_verified_source_digest_and_spans() -> None:
    with pytest.raises(ValueError, match="sourceDigest"):
        _graph(claim_graph=_claim_graph(source_digest=_digest("9")))

    with pytest.raises(ValueError, match="spanRefs"):
        _graph(claim_graph=_claim_graph(span_refs=("span:unverified",)))


def test_acceptance_refs_must_match_verifier_evidence_type_namespace() -> None:
    child_ref = _graph().child_evidence_refs[0]

    with pytest.raises(ValueError, match="evidenceType"):
        _graph(
            acceptance_criteria=_acceptance_criteria(
                evidence_ref_id=child_ref.child_evidence_ref_id,
                digest=child_ref.digest,
                evidence_type="source_inspection",
                required_evidence_types=("source_inspection",),
                span_refs=(),
            )
        )


def test_evidence_ref_ids_cannot_collide_across_evidence_classes() -> None:
    with pytest.raises(ValueError, match="namespace collision"):
        _graph(
            claim_graph=_claim_graph(support_ref_id="src_1"),
            acceptance_criteria=_acceptance_criteria(digest=_digest("2")),
        )


def test_source_acceptance_refs_must_include_verified_support_spans() -> None:
    with pytest.raises(ValueError, match="spanRefs"):
        _graph(acceptance_criteria=_acceptance_criteria(span_refs=()))


def test_graph_rejects_child_raw_summaries_as_evidence() -> None:
    with pytest.raises(TypeError, match="runtime-issued child evidence envelopes"):
        _graph(
            child_evidence_envelopes=(
                {
                    "summary": "The child says the pricing source is current.",
                    "rawTranscript": _fixture_child_transcript_preview(),
                    "evidenceRefs": ("src_1",),
                },
            ),
        )


def test_graph_rejects_forged_child_evidence_ref_objects() -> None:
    issued = _graph().child_evidence_refs[0]
    forged = ResearchChildEvidenceRef(**issued.model_dump(by_alias=True))

    with pytest.raises(ValueError, match="runtime child envelopes"):
        ResearchEvidenceGraph(
            evidenceGraphId="evidence-graph:forged-child",
            actionProofVerdicts=_action_verdicts(),
            sourceProofVerdicts=_source_verdicts(),
            claimGraph=_claim_graph(),
            acceptanceCriteria=_acceptance_criteria(),
            childEvidenceRefs=(forged,),
            missingEvidenceReasons=(),
        )


def test_graph_rejects_unissued_action_or_source_verdict_objects() -> None:
    action_verdict = _action_verdicts()[0]
    source_verdict = _source_verdicts()[0]
    forged_action = type(action_verdict).model_validate(
        action_verdict.model_dump(by_alias=True, mode="python", warnings=False)
    )
    forged_source = type(source_verdict).model_validate(
        source_verdict.model_dump(by_alias=True, mode="python", warnings=False)
    )

    with pytest.raises(ValueError, match="issued by the verifier"):
        _graph(action_proof_verdicts=(forged_action,))

    with pytest.raises(ValueError, match="issued by the verifier"):
        _graph(source_proof_verdicts=(forged_source,))


def test_action_proof_evidence_refs_can_satisfy_acceptance_criteria() -> None:
    action_verdicts = _action_verdicts()
    action_digest = _public_digest(project_research_action_proof_verdicts(action_verdicts)[0])

    graph = _graph(
        acceptance_criteria=_acceptance_criteria(
            evidence_ref_id=action_verdicts[0].matched_receipt_refs[0],
            evidence_type="action_proof",
            required_evidence_types=("action_proof",),
            digest=action_digest,
            span_refs=(),
        )
    )

    assert graph.acceptance_criteria.criteria[0].status == "satisfied"


def test_graph_accepts_runtime_issued_child_evidence_envelopes() -> None:
    graph = _graph()

    assert len(graph.child_evidence_refs) == 1
    assert graph.child_evidence_refs[0].issuer == OPENMAGI_RUNTIME_ENVELOPE_ISSUER
    assert graph.child_evidence_refs[0].task_id == "task:pricing-child"
    assert graph.child_evidence_refs[0].completion_summary_is_evidence is False


def test_graph_rejects_structural_child_runtime_envelopes_as_research_evidence() -> None:
    with pytest.raises(TypeError, match="runtime-issued child evidence envelopes"):
        _graph(
            child_evidence_envelopes=(
                ChildRuntimeEnvelope.model_validate(
                    _child_envelope().model_dump(by_alias=True)
                ),
            )
        )


def test_blocked_child_envelope_cannot_be_used_as_research_evidence() -> None:
    with pytest.raises(ValueError, match="accepted child evidence"):
        _graph(child_evidence_envelopes=(_blocked_child_envelope(),))


def test_graph_public_projection_redacts_raw_source_tool_and_private_data() -> None:
    projection = _graph().public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    for forbidden in (
        "raw child transcript",
        "rawTranscript",
        "privateMetadata",
        "authorization",
        "Bearer ",
        _fixture_bearer_token(),
        _fixture_child_secret(),
        "/workspace",
        "/Users/",
        "source body",
        "tool output",
        "model summary",
    ):
        assert forbidden not in dumped


def test_graph_public_digest_is_deterministic_for_replay_regression() -> None:
    first = _graph()
    second = _graph()

    assert first.public_digest() == second.public_digest()
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", first.public_digest())
    assert first.public_digest_projection() == {
        "evidenceGraphId": "evidence-graph:pricing",
        "digest": first.public_digest(),
        "defaultOff": True,
        "localOnly": True,
        "fakeProviderOnly": True,
    }


def test_public_digest_binds_all_public_projection_fields() -> None:
    graph = _graph()
    changed_notes = ResearchEvidenceGraph(
        evidenceGraphId=graph.evidence_graph_id,
        actionProofVerdicts=graph.action_proof_verdicts,
        sourceProofVerdicts=graph.source_proof_verdicts,
        claimGraph=graph.claim_graph,
        acceptanceCriteria=graph.acceptance_criteria,
        childEvidenceRefs=graph.child_evidence_refs,
        missingEvidenceReasons=graph.missing_evidence_reasons,
        executionPosture=graph.execution_posture,
        adkUsageNotes=(
            "Research harness metadata only; no ADK Runner or FunctionTool is attached here."
        ),
    )

    assert changed_notes.public_projection()["adkUsageNotes"] != graph.public_projection()["adkUsageNotes"]
    assert changed_notes.public_digest() != graph.public_digest()


def test_model_copy_preserves_issued_nested_evidence_objects() -> None:
    graph = _graph()

    copied = graph.model_copy()

    assert copied.public_digest() == graph.public_digest()
