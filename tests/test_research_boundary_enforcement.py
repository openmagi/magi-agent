from __future__ import annotations

import importlib
import inspect
import json
from hashlib import sha256

import pytest

import magi_agent.research.boundary_enforcement as boundary_module
from runtime_issuance_support import issue_test_runtime_authority
from magi_agent.research.acceptance_criteria import (
    ResearchAcceptanceCriterion,
    ResearchAcceptanceCriteriaSet,
)
from magi_agent.research.action_claims import (
    ResearchActionProofReceiptRef,
    ResearchActionProofRequirement,
    detect_research_action_claims,
    verify_research_action_claims,
)
from magi_agent.research.boundary_enforcement import (
    ResearchBoundaryRequest,
    ResearchBoundarySequenceRef,
    enforce_research_boundary,
)
from magi_agent.research.claim_graph import (
    ResearchClaimGraph,
    ResearchClaimSupportRef,
    build_research_claim_node,
)
from magi_agent.research.evidence_graph import ResearchEvidenceGraph
from magi_agent.research.source_proof import (
    ResearchSourceOpenReceiptRef,
    ResearchSourceProofRequirement,
    ResearchSourceProofVerdict,
    verify_research_source_proof,
)


@pytest.fixture(autouse=True)
def _clear_boundary_sequence_state():
    boundary_module.begin_research_boundary_execution("execution:test")
    yield
    boundary_module.end_research_boundary_execution()


def _digest(char: str = "a") -> str:
    return "sha256:" + char * 64


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-boundary",
        scopes=scopes,
    )


def _candidate_claim_digest(text: str) -> str:
    material = "\n".join(
        (
            "openmagi-research-boundary-candidate-claim-v1",
            text.strip(),
        )
    )
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _sequence_ref(
    sequence_id: str,
) -> ResearchBoundarySequenceRef:
    return ResearchBoundarySequenceRef.issue_runtime_sequence_ref(
        runtime_authority=_runtime_authority("research_boundary"),
        sequence_id=sequence_id,
    )


def test_boundary_sequence_ref_factory_requires_runtime_issue_authority() -> None:
    with pytest.raises(RuntimeError, match="runtime issue authority"):
        ResearchBoundarySequenceRef.issue_runtime_sequence_ref(
            sequence_id="sequence:boundary",
        )


def _acceptance_criteria() -> ResearchAcceptanceCriteriaSet:
    return ResearchAcceptanceCriteriaSet(
        criteriaSetId="criteria-set:boundary",
        targetLabel="Boundary research",
        criteria=(
            ResearchAcceptanceCriterion(
                criteriaId="criteria:not-applicable",
                description="No task evidence is required for this boundary fixture.",
                requiredEvidenceTypes=(),
                optionalEvidenceTypes=(),
                completionMode="not_applicable",
            ),
        ),
    )


def _source_verdicts():
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
        public_label="Pricing metadata",
    )
    requirement = ResearchSourceProofRequirement(
        sourceRefId="src_1",
        allowedSourceKinds=("web_fetch",),
        requiredReceiptKinds=("opened_snapshot",),
        requiredSpanRefs=("span:pricing",),
        notBefore="2026-05-26T10:00:00Z",
        notAfter="2026-05-26T13:00:00Z",
    )
    return verify_research_source_proof((requirement,), (source_ref,))


def _stale_source_verdicts() -> tuple[ResearchSourceProofVerdict, ...]:
    source_ref = ResearchSourceOpenReceiptRef.issue_runtime_source_ref(
        runtime_authority=_runtime_authority("research_source_proof"),
        source_ref_id="src_1",
        source_kind="web_fetch",
        receipt_kind="opened_snapshot",
        opened=True,
        content_digest=_digest("1"),
        inspected_at="2026-05-25T12:00:00Z",
        span_refs=("span:pricing",),
        redaction_status="redacted",
        public_label="Pricing metadata",
    )
    requirement = ResearchSourceProofRequirement(
        sourceRefId="src_1",
        allowedSourceKinds=("web_fetch",),
        requiredReceiptKinds=("opened_snapshot",),
        requiredSpanRefs=("span:pricing",),
        notBefore="2026-05-26T10:00:00Z",
        notAfter="2026-05-26T13:00:00Z",
    )
    return verify_research_source_proof((requirement,), (source_ref,))


def _empty_claim_graph() -> ResearchClaimGraph:
    return ResearchClaimGraph(claimGraphId="claim-graph:empty", claims=())


def _unsupported_claim_graph() -> ResearchClaimGraph:
    support_ref = ResearchClaimSupportRef.issue_verified_support_ref(
        runtime_authority=_runtime_authority("research_claim_support"),
        support_ref_id="support:unsupported",
        source_ref_id="src_1",
        span_refs=("span:pricing",),
        source_digest=_digest("1"),
        evidence_digest=_digest("2"),
        evidence_kind="source_span",
        support_verdict="unsupported",
        freshness_verdict="current",
        relevance_verdict="relevant",
    )
    claim = build_research_claim_node(
        claim_id="claim:unsupported",
        claim_text_digest=_digest("3"),
        claim_kind="factual",
        claim_preview="The service has an unsupported pricing claim.",
        support_refs=(support_ref,),
    )
    return ResearchClaimGraph(claimGraphId="claim-graph:unsupported", claims=(claim,))


def _supported_claim_graph(
    candidate_sentence: str,
    *,
    bind_support_to_claim_text: bool = True,
) -> ResearchClaimGraph:
    claim_text_digest = _candidate_claim_digest(candidate_sentence)
    support_ref = ResearchClaimSupportRef.issue_verified_support_ref(
        runtime_authority=_runtime_authority("research_claim_support"),
        support_ref_id="support:supported",
        source_ref_id="src_1",
        span_refs=("span:pricing",),
        source_digest=_digest("1"),
        evidence_digest=_digest("2"),
        evidence_kind="source_span",
        support_verdict="supported",
        freshness_verdict="current",
        relevance_verdict="relevant",
        claim_text_digest=claim_text_digest if bind_support_to_claim_text else None,
    )
    claim = build_research_claim_node(
        claim_id="claim:supported",
        claim_text_digest=claim_text_digest,
        claim_kind="factual",
        claim_preview="The service has a supported pricing claim.",
        support_refs=(support_ref,),
    )
    return ResearchClaimGraph(claimGraphId="claim-graph:supported", claims=(claim,))


def _action_verdicts(text: str):
    claims = detect_research_action_claims(text)
    receipts = tuple(
        ResearchActionProofReceiptRef.issue_runtime_receipt(
            runtime_authority=_runtime_authority("research_action_proof"),
            receipt_id=f"receipt:{claim.action_verb}:1",
            action_verb=claim.action_verb,
            receipt_kind="opened_snapshot",
            tool_id="tool:research",
            source_id="src_1",
            observed_at="2026-05-26T12:00:00Z",
            public_label="Pricing metadata",
        )
        for claim in claims
    )
    requirements = tuple(
        ResearchActionProofRequirement(
            claimId=claim.claim_id,
            requiredActionVerb=claim.action_verb,
            requiredReceiptKinds=("opened_snapshot",),
            requiredToolIds=("tool:research",),
            requiredSourceIds=("src_1",),
            notBefore="2026-05-26T10:00:00Z",
            notAfter="2026-05-26T13:00:00Z",
        )
        for claim in claims
    )
    return verify_research_action_claims(claims, receipts, requirements=requirements)


def _evidence_graph(
    *,
    claim_graph: ResearchClaimGraph | None = None,
    action_text: str | None = None,
    source_verdicts: tuple[ResearchSourceProofVerdict, ...] | None = None,
) -> ResearchEvidenceGraph:
    graph = claim_graph or _empty_claim_graph()
    resolved_source_verdicts = source_verdicts
    if resolved_source_verdicts is None:
        resolved_source_verdicts = _source_verdicts()
    return ResearchEvidenceGraph.from_runtime_evidence(
        evidence_graph_id="evidence-graph:boundary",
        action_proof_verdicts=_action_verdicts(action_text) if action_text else (),
        source_proof_verdicts=resolved_source_verdicts,
        claim_graph=graph,
        acceptance_criteria=_acceptance_criteria(),
    )


def test_action_claim_without_receipt_blocks_intermediate_summary() -> None:
    decision = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:source-summary",
            stage="after_source_summary",
            harnessKind="research",
            candidateText="I reviewed the pricing page and summarized it.",
            evidenceGraph=_evidence_graph(),
        )
    )

    projection = decision.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert decision.status == "blocked"
    assert "action_claim_without_receipt" in decision.reason_codes
    assert "pricing page" not in dumped
    assert projection["authorityFlags"]["finalAnswerBlocked"] is False


def test_research_boundary_requires_active_execution_lifecycle() -> None:
    boundary_module.end_research_boundary_execution()

    decision = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:no-lifecycle",
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
        )
    )

    assert decision.status == "blocked"
    assert "missing_execution_lifecycle" in decision.reason_codes
    with pytest.raises(RuntimeError, match="execution lifecycle"):
        ResearchBoundarySequenceRef.issue_runtime_sequence_ref(
            runtime_authority=_runtime_authority("research_boundary"),
            sequence_id="sequence:no-lifecycle",
        )


def test_action_claim_with_matching_runtime_verdict_passes_intermediate_summary() -> None:
    candidate = "I reviewed the pricing page and summarized it."

    decision = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:source-summary-allowed",
            stage="after_source_summary",
            harnessKind="research",
            candidateText=candidate,
            evidenceGraph=_evidence_graph(action_text=candidate),
        )
    )

    assert decision.status == "pass"
    assert decision.action == "pass"
    assert decision.reason_codes == ("passed",)


def test_action_claim_verdict_bound_to_different_text_does_not_satisfy_candidate() -> None:
    verified_text = "I reviewed the pricing page and summarized it."
    spoofed_text = "I reviewed payroll records and summarized them."

    decision = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:source-summary-spoof",
            stage="after_source_summary",
            harnessKind="research",
            candidateText=spoofed_text,
            evidenceGraph=_evidence_graph(action_text=verified_text),
        )
    )

    assert decision.status == "blocked"
    assert "action_claim_without_receipt" in decision.reason_codes


def test_unsupported_factual_claim_requires_repair_before_intermediate_synthesis() -> None:
    decision = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:synthesis",
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=_evidence_graph(claim_graph=_unsupported_claim_graph()),
        )
    )

    assert decision.status == "repair_required"
    assert decision.action == "repair"
    assert "unsupported_claim" in decision.reason_codes
    assert decision.repair_actions == ("omit_unsupported_claim",)


def test_child_result_cannot_be_accepted_without_child_evidence_envelope() -> None:
    decision = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:child",
            stage="before_child_result_acceptance",
            harnessKind="research",
            childResultReceived=True,
            evidenceGraph=_evidence_graph(),
        )
    )

    assert decision.status == "blocked"
    assert decision.action == "block"
    assert "missing_child_evidence_envelope" in decision.reason_codes


def test_final_projection_cannot_bypass_failed_intermediate_boundary() -> None:
    sequence_id = "sequence:failed-final"
    sequence_ref = _sequence_ref(sequence_id)
    failed = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:failed",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            candidateText="I checked the source.",
            evidenceGraph=_evidence_graph(),
        )
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:final",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
            priorDecisions=(failed,),
        )
    )

    assert failed.status == "blocked"
    assert final.status == "blocked"
    assert "prior_boundary_failed" in final.reason_codes
    assert final.final_projection_allowed is False


def test_final_projection_uses_authoritative_sequence_ledger_not_supplied_prior_subset() -> None:
    candidate = "The company earned 900 billion dollars."
    sequence_id = "sequence:omitted-failure"
    sequence_ref = _sequence_ref(sequence_id)
    graph = _evidence_graph(claim_graph=_supported_claim_graph(candidate))
    source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:source-summary-pass-before-omitted-failure",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )
    synthesis = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:synthesis-pass-before-omitted-failure",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )
    failed = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:omitted-failure",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            candidateText="I checked the source.",
            evidenceGraph=graph,
        )
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:final-omitted-failure",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            candidateText=candidate,
            evidenceGraph=graph,
            priorDecisions=(source_summary, synthesis),
        )
    )

    assert failed.status == "blocked"
    assert final.status == "blocked"
    assert "prior_boundary_failed" in final.reason_codes


def test_final_projection_rejects_sequence_change_for_same_task_scope_after_failure() -> None:
    candidate = "The company earned 900 billion dollars."
    failed_sequence_ref = _sequence_ref("sequence:failed-task-scope")
    fresh_sequence_ref = _sequence_ref("sequence:fresh-task-scope")
    graph = _evidence_graph(claim_graph=_supported_claim_graph(candidate))
    failed = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:failed-task-scope",
            boundarySequenceRef=failed_sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            candidateText="I checked the source.",
            evidenceGraph=graph,
        )
    )
    source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:fresh-source-summary",
            boundarySequenceRef=fresh_sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )
    synthesis = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:fresh-synthesis",
            boundarySequenceRef=fresh_sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:fresh-final",
            boundarySequenceRef=fresh_sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            candidateText=candidate,
            evidenceGraph=graph,
            priorDecisions=(source_summary, synthesis),
        )
    )

    assert failed.status == "blocked"
    assert final.status == "blocked"
    assert "prior_boundary_failed" in final.reason_codes


def test_final_projection_sees_graphless_intermediate_failure_in_active_task_scope() -> None:
    candidate = "The company earned 900 billion dollars."
    sequence_ref = _sequence_ref("sequence:graphless-failure")
    graph = _evidence_graph(claim_graph=_supported_claim_graph(candidate))
    failed = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:graphless-failure",
            stage="after_source_summary",
            harnessKind="research",
            candidateText="I checked the source.",
        )
    )
    source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:graphless-source-summary",
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )
    synthesis = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:graphless-synthesis",
            boundarySequenceRef=sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:graphless-final",
            boundarySequenceRef=sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            candidateText=candidate,
            evidenceGraph=graph,
            priorDecisions=(source_summary, synthesis),
        )
    )

    assert failed.status == "blocked"
    assert final.status == "blocked"
    assert "prior_boundary_failed" in final.reason_codes


def test_execution_lifecycle_clears_task_scoped_failures_between_runs() -> None:
    candidate = "The company earned 900 billion dollars."
    graph = _evidence_graph(claim_graph=_supported_claim_graph(candidate))
    failed_sequence_ref = _sequence_ref("sequence:lifecycle-failed")
    failed = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:lifecycle-failed",
            boundarySequenceRef=failed_sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            candidateText="I checked the source.",
            evidenceGraph=graph,
        )
    )

    boundary_module.end_research_boundary_execution()
    boundary_module.begin_research_boundary_execution("execution:test-next")
    sequence_ref = _sequence_ref("sequence:lifecycle-clean")
    source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:lifecycle-source-summary",
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )
    synthesis = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:lifecycle-synthesis",
            boundarySequenceRef=sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:lifecycle-final",
            boundarySequenceRef=sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            candidateText=candidate,
            evidenceGraph=graph,
            priorDecisions=(source_summary, synthesis),
        )
    )

    assert failed.status == "blocked"
    assert final.status == "pass"


def test_final_projection_requires_issued_prior_boundary_history() -> None:
    sequence_id = "sequence:forged-history"
    sequence_ref = _sequence_ref(sequence_id)
    source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:source-summary-pass",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
        )
    )
    forged_synthesis = source_summary.model_copy(
        update={
            "boundaryId": "boundary:forged-synthesis",
            "stage": "before_intermediate_synthesis",
        }
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:final-missing-history",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
            priorDecisions=(source_summary, forged_synthesis),
        )
    )

    assert final.status == "blocked"
    assert "missing_boundary_history" in final.reason_codes


def test_final_projection_requires_candidate_projection_text() -> None:
    sequence_id = "sequence:missing-projection-text"
    sequence_ref = _sequence_ref(sequence_id)
    candidate = "The company earned 900 billion dollars."
    graph = _evidence_graph(claim_graph=_supported_claim_graph(candidate))
    source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:source-summary-pass-for-text",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )
    synthesis = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:synthesis-pass-for-text",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:final-missing-text",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            evidenceGraph=graph,
            priorDecisions=(source_summary, synthesis),
        )
    )

    assert final.status == "blocked"
    assert "missing_projection_text" in final.reason_codes


def test_non_research_skipped_boundaries_do_not_satisfy_research_final_history() -> None:
    sequence_id = "sequence:skipped-history"
    sequence_ref = _sequence_ref(sequence_id)
    skipped_source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:skipped-source-summary",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="coding",
        )
    )
    skipped_synthesis = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:skipped-synthesis",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="coding",
        )
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:final-skipped-history",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
            priorDecisions=(skipped_source_summary, skipped_synthesis),
        )
    )

    assert final.status == "blocked"
    assert "missing_boundary_history" in final.reason_codes


def test_final_projection_requires_evidence_graph_even_with_prior_history() -> None:
    sequence_id = "sequence:missing-graph"
    sequence_ref = _sequence_ref(sequence_id)
    source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:source-summary-pass-with-history",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
        )
    )
    synthesis = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:synthesis-pass-with-history",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
        )
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:final-no-graph",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            priorDecisions=(source_summary, synthesis),
        )
    )

    assert final.status == "blocked"
    assert "missing_evidence_graph" in final.reason_codes


def test_stale_source_verdict_blocks_final_projection_boundary() -> None:
    sequence_id = "sequence:stale-source"
    sequence_ref = _sequence_ref(sequence_id)
    source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:source-summary-pass-for-stale",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
        )
    )
    synthesis = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:synthesis-pass-for-stale",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
        )
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:final-stale-source",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            evidenceGraph=_evidence_graph(source_verdicts=_stale_source_verdicts()),
            priorDecisions=(source_summary, synthesis),
        )
    )

    assert final.status == "blocked"
    assert "stale_source" in final.reason_codes


def test_final_projection_rejects_candidate_fact_missing_from_claim_graph() -> None:
    candidate = "The company earned 900 billion dollars."
    sequence_id = "sequence:unmapped-claim"
    sequence_ref = _sequence_ref(sequence_id)
    source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:source-summary-pass-for-unmapped-claim",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
        )
    )
    synthesis = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:synthesis-pass-for-unmapped-claim",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
        )
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:final-unmapped-claim",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            candidateText=candidate,
            evidenceGraph=_evidence_graph(),
            priorDecisions=(source_summary, synthesis),
        )
    )

    assert final.status == "blocked"
    assert "unsupported_claim" in final.reason_codes


def test_final_projection_rejects_unmapped_fact_in_action_claim_sentence() -> None:
    candidate = "I reviewed the pricing page and the company earned 900 billion dollars."
    sequence_id = "sequence:action-fact"
    sequence_ref = _sequence_ref(sequence_id)
    source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:source-summary-pass-for-action-fact",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=_evidence_graph(action_text=candidate),
        )
    )
    synthesis = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:synthesis-pass-for-action-fact",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=_evidence_graph(action_text=candidate),
        )
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:final-action-fact",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            candidateText=candidate,
            evidenceGraph=_evidence_graph(action_text=candidate),
            priorDecisions=(source_summary, synthesis),
        )
    )

    assert final.status == "blocked"
    assert "unsupported_claim" in final.reason_codes


def test_final_projection_rejects_claim_node_without_support_bound_to_candidate_digest() -> None:
    candidate = "The company earned 900 billion dollars."
    sequence_id = "sequence:unbound-claim-support"
    sequence_ref = _sequence_ref(sequence_id)
    graph = _evidence_graph(
        claim_graph=_supported_claim_graph(candidate, bind_support_to_claim_text=False)
    )
    source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:source-summary-pass-for-unbound-support",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )
    synthesis = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:synthesis-pass-for-unbound-support",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:final-unbound-support",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            candidateText=candidate,
            evidenceGraph=graph,
            priorDecisions=(source_summary, synthesis),
        )
    )

    assert final.status == "blocked"
    assert "unsupported_claim" in final.reason_codes


def test_final_projection_allows_candidate_fact_mapped_to_supported_claim_graph() -> None:
    candidate = "The company earned 900 billion dollars."
    sequence_id = "sequence:mapped-claim"
    sequence_ref = _sequence_ref(sequence_id)
    source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:source-summary-pass-for-mapped-claim",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
        )
    )
    synthesis = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:synthesis-pass-for-mapped-claim",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
        )
    )

    final = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:final-mapped-claim",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_final_projection",
            harnessKind="research",
            candidateText=candidate,
            evidenceGraph=_evidence_graph(claim_graph=_supported_claim_graph(candidate)),
            priorDecisions=(source_summary, synthesis),
        )
    )

    assert final.status == "pass"
    assert final.reason_codes == ("passed",)


def test_before_commit_cannot_bypass_failed_intermediate_boundary() -> None:
    sequence_id = "sequence:failed-before-commit"
    sequence_ref = _sequence_ref(sequence_id)
    failed = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:failed-before-commit",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            candidateText="I checked the source.",
            evidenceGraph=_evidence_graph(),
        )
    )

    before_commit = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:commit",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_commit",
            harnessKind="research",
            evidenceGraph=_evidence_graph(),
            priorDecisions=(failed,),
        )
    )

    assert before_commit.status == "blocked"
    assert before_commit.action == "block"
    assert "prior_boundary_failed" in before_commit.reason_codes


def test_non_research_harnesses_are_unaffected_and_module_stays_research_local() -> None:
    module = importlib.import_module("magi_agent.research.boundary_enforcement")
    source = inspect.getsource(module)

    decision = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:non-research",
            stage="before_intermediate_synthesis",
            harnessKind="coding",
            candidateText="I reviewed the pricing page.",
        )
    )

    assert decision.status == "skipped"
    assert decision.action == "pass"
    assert decision.reason_codes == ("non_research_harness",)
    assert "from google.adk" not in source
    assert "from magi_agent.runtime" not in source
    assert "from magi_agent.harness" not in source
    assert "from magi_agent.tools" not in source
