from __future__ import annotations

import json
from hashlib import sha256

import pytest

import magi_agent.research.boundary_enforcement as boundary_module
from runtime_issuance_support import issue_test_runtime_authority
from magi_agent.research.acceptance_criteria import (
    ResearchAcceptanceCriterion,
    ResearchAcceptanceCriteriaSet,
    ResearchAcceptanceEvidenceRef,
)
from magi_agent.research.action_claims import (
    ResearchActionProofReceiptRef,
    ResearchActionProofRequirement,
    detect_research_action_claims,
    verify_research_action_claims,
)
from magi_agent.research.claim_graph import (
    ResearchClaimGraph,
    ResearchClaimSupportRef,
    build_research_claim_node,
)
from magi_agent.research.boundary_enforcement import (
    ResearchBoundaryRequest,
    ResearchBoundarySequenceRef,
    enforce_research_boundary,
)
from magi_agent.research.evidence_graph import ResearchEvidenceGraph
from magi_agent.research.final_projection_gate import (
    ResearchFinalProjectionGateRequest,
    evaluate_research_final_projection_gate,
)
from magi_agent.research.source_proof import (
    ResearchSourceOpenReceiptRef,
    ResearchSourceProofRequirement,
    ResearchSourceProofVerdict,
    verify_research_source_proof,
)


@pytest.fixture(autouse=True)
def _research_boundary_lifecycle():
    boundary_module.begin_research_boundary_execution("execution:final-projection-test")
    yield
    boundary_module.end_research_boundary_execution()


def _digest(char: str = "a") -> str:
    return "sha256:" + char * 64


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-final-projection",
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


def _windows_private_path() -> str:
    return "C:" + "\\Users\\kevin\\private\\notes.txt"


def _windows_forward_private_path() -> str:
    return "C:" + "/Temp/openmagi/notes.txt"


def _unc_private_path() -> str:
    return "\\\\" + "host\\share\\notes\\file.txt"


def _posix_private_path() -> str:
    return "/" + "Users/private/source.txt"


def _source_verdicts(
    *,
    opened: bool = True,
    not_before: str | None = "2026-05-26T10:00:00Z",
    not_after: str | None = "2026-05-26T13:00:00Z",
    span_refs: tuple[str, ...] = ("span:pricing",),
) -> tuple[ResearchSourceProofVerdict, ...]:
    source_ref = ResearchSourceOpenReceiptRef.issue_runtime_source_ref(
        runtime_authority=_runtime_authority("research_source_proof"),
        source_ref_id="src_1",
        source_kind="web_fetch",
        receipt_kind="opened_snapshot" if opened else "discovered_source",
        opened=opened,
        content_digest=_digest("1"),
        inspected_at="2026-05-26T12:00:00Z",
        span_refs=span_refs,
        redaction_status="redacted",
        public_label="Pricing source metadata",
    )
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
        (source_ref,),
    )


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
            public_label="Runtime action proof",
        )
        for claim in claims
    )
    requirements = tuple(
        ResearchActionProofRequirement(
            claimId=claim.claim_id,
            claimTextDigest=claim.claim_text_digest,
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


def _claim_graph(
    preview: str = "The service has current pricing.",
    *,
    support_verdict: str = "supported",
    claim_kind: str = "factual",
    freshness_verdict: str = "current",
    bind_support_to_claim_text: bool = True,
) -> ResearchClaimGraph:
    claim_text_digest = _candidate_claim_digest(preview)
    support_ref = ResearchClaimSupportRef.issue_verified_support_ref(
        runtime_authority=_runtime_authority("research_claim_support"),
        support_ref_id="support:pricing",
        source_ref_id="src_1",
        span_refs=("span:pricing",),
        source_digest=_digest("1"),
        evidence_digest=_digest("2"),
        evidence_kind="source_span",
        support_verdict=support_verdict,
        freshness_verdict=freshness_verdict,
        relevance_verdict="relevant",
        claim_text_digest=claim_text_digest if bind_support_to_claim_text else None,
        public_label="Verified source span",
    )
    claim = build_research_claim_node(
        claim_id="claim:pricing",
        claim_text_digest=claim_text_digest,
        claim_kind=claim_kind,
        claim_preview=preview,
        support_refs=(support_ref,),
    )
    return ResearchClaimGraph(claimGraphId="claim-graph:pricing", claims=(claim,))


def _empty_claim_graph() -> ResearchClaimGraph:
    return ResearchClaimGraph(claimGraphId="claim-graph:empty", claims=())


def _unsupported_claim_graph() -> ResearchClaimGraph:
    return _claim_graph(
        "The service has an unsupported pricing claim.",
        support_verdict="unsupported",
    )


def _mixed_support_claim_graph() -> ResearchClaimGraph:
    preview = "The service has current pricing."
    claim_text_digest = _candidate_claim_digest(preview)
    supported_ref = ResearchClaimSupportRef.issue_verified_support_ref(
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
        claim_text_digest=claim_text_digest,
        public_label="Supported source span",
    )
    unsupported_ref = ResearchClaimSupportRef.issue_verified_support_ref(
        runtime_authority=_runtime_authority("research_claim_support"),
        support_ref_id="support:unsupported",
        source_ref_id="src_1",
        span_refs=("span:unsupported",),
        source_digest=_digest("1"),
        evidence_digest=_digest("4"),
        evidence_kind="source_span",
        support_verdict="unsupported",
        freshness_verdict="current",
        relevance_verdict="relevant",
        claim_text_digest=claim_text_digest,
        public_label="Unsupported source span",
    )
    claim = build_research_claim_node(
        claim_id="claim:pricing",
        claim_text_digest=claim_text_digest,
        claim_kind="factual",
        claim_preview=preview,
        support_refs=(supported_ref, unsupported_ref),
    )
    return ResearchClaimGraph(claimGraphId="claim-graph:mixed-support", claims=(claim,))


def _acceptance_criteria(
    *,
    missing_required: bool = False,
) -> ResearchAcceptanceCriteriaSet:
    criteria: list[ResearchAcceptanceCriterion] = [
        ResearchAcceptanceCriterion(
            criteriaId="criteria:pricing-source",
            description="Pricing answer requires current source inspection evidence.",
            requiredEvidenceTypes=("source_inspection",),
            optionalEvidenceTypes=(),
            sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
            completionMode="required",
            evidenceRefs=(
                ResearchAcceptanceEvidenceRef(
                    evidenceRefId="src_1",
                    evidenceType="source_inspection",
                    supportVerdict="supports",
                    freshnessVerdict="current",
                    digest=_digest("1"),
                    spanRefs=("span:pricing",),
                    publicLabel="Source proof metadata",
                ),
            ),
        )
    ]
    if missing_required:
        criteria.append(
            ResearchAcceptanceCriterion(
                criteriaId="criteria:competitor-comparison",
                description="Competitor comparison requires inspected source evidence.",
                requiredEvidenceTypes=("competitor_context",),
                optionalEvidenceTypes=(),
                sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
                completionMode="required",
                evidenceRefs=(),
            )
        )
    return ResearchAcceptanceCriteriaSet(
        criteriaSetId="criteria-set:pricing",
        targetLabel="Pricing research",
        criteria=tuple(criteria),
    )


def _not_applicable_criteria() -> ResearchAcceptanceCriteriaSet:
    return ResearchAcceptanceCriteriaSet(
        criteriaSetId="criteria-set:not-applicable",
        targetLabel="Projection smoke",
        criteria=(
            ResearchAcceptanceCriterion(
                criteriaId="criteria:not-applicable",
                description="No task evidence is required for this projection fixture.",
                requiredEvidenceTypes=(),
                optionalEvidenceTypes=(),
                completionMode="not_applicable",
            ),
        ),
    )


def _graph(
    *,
    claim_graph: ResearchClaimGraph | None = None,
    action_text: str | None = None,
    source_verdicts: tuple[ResearchSourceProofVerdict, ...] | None = None,
    acceptance_criteria: ResearchAcceptanceCriteriaSet | None = None,
) -> ResearchEvidenceGraph:
    return ResearchEvidenceGraph.from_runtime_evidence(
        evidence_graph_id="evidence-graph:projection",
        action_proof_verdicts=_action_verdicts(action_text) if action_text else (),
        source_proof_verdicts=source_verdicts or _source_verdicts(),
        claim_graph=claim_graph or _claim_graph(),
        acceptance_criteria=acceptance_criteria or _acceptance_criteria(),
    )


def _evaluate(candidate: str, graph: ResearchEvidenceGraph):
    sequence_ref, boundary_decisions = _passing_boundary_history(candidate, graph)
    return evaluate_research_final_projection_gate(
        ResearchFinalProjectionGateRequest(
            gateId="gate:projection",
            mode="local_only",
            candidateFinalAnswer=candidate,
            evidenceGraph=graph,
            boundarySequenceRef=sequence_ref,
            boundaryDecisions=boundary_decisions,
        )
    )


def _evaluate_without_boundary_history(candidate: str, graph: ResearchEvidenceGraph):
    return evaluate_research_final_projection_gate(
        ResearchFinalProjectionGateRequest(
            gateId="gate:projection",
            mode="local_only",
            candidateFinalAnswer=candidate,
            evidenceGraph=graph,
        )
    )


def _passing_boundary_history(
    candidate: str,
    graph: ResearchEvidenceGraph,
):
    sequence_id = "sequence:final-projection:" + _candidate_claim_digest(candidate)[7:23]
    sequence_ref = ResearchBoundarySequenceRef.issue_runtime_sequence_ref(
        runtime_authority=_runtime_authority("research_boundary"),
        sequence_id=sequence_id,
    )
    source_summary = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId=f"boundary:source-summary:{sequence_id}",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )
    synthesis = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId=f"boundary:synthesis:{sequence_id}",
            boundarySequenceId=sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=graph,
        )
    )
    return sequence_ref, (source_summary, synthesis)


def test_supported_factual_claim_renders_as_fact() -> None:
    result = _evaluate(
        "The service has current pricing. [src_1]",
        _graph(),
    )

    projection = result.public_projection()

    assert result.status == "passed"
    assert result.ok is True
    assert projection["renderedFacts"] == (
        {
            "claimId": "claim:pricing",
            "text": "The service has current pricing.",
            "sourceRefs": ("src_1",),
            "spanRefs": ("span:pricing",),
            "renderAsFact": True,
        },
    )
    assert projection["authorityFlags"]["channelDeliveryPerformed"] is False


def test_supported_password_reset_claim_renders_as_public_fact() -> None:
    claim_text = "The docs include a password reset flow."

    result = _evaluate(
        f"{claim_text} [src_1]",
        _graph(
            claim_graph=_claim_graph(claim_text),
            acceptance_criteria=_acceptance_criteria(),
        ),
    )

    assert result.status == "passed"
    assert result.rendered_facts[0].text == claim_text


def test_local_only_final_projection_requires_boundary_history() -> None:
    result = _evaluate_without_boundary_history(
        "The service has current pricing. [src_1]",
        _graph(),
    )

    assert result.status == "repair_required"
    assert result.ok is False
    assert "missing_boundary_history" in result.reason_codes
    assert result.rendered_facts == ()


def test_final_projection_rejects_failed_prior_boundary_history() -> None:
    graph = _graph(claim_graph=_unsupported_claim_graph())
    sequence_ref, boundary_decisions = _passing_boundary_history(
        "The service has an unsupported pricing claim. [src_1]",
        graph,
    )

    result = evaluate_research_final_projection_gate(
        ResearchFinalProjectionGateRequest(
            gateId="gate:failed-prior-boundary",
            mode="local_only",
            candidateFinalAnswer="The service has an unsupported pricing claim. [src_1]",
            evidenceGraph=graph,
            boundarySequenceRef=sequence_ref,
            boundaryDecisions=boundary_decisions,
        )
    )

    assert result.status == "repair_required"
    assert result.ok is False
    assert "prior_boundary_failed" in result.reason_codes
    assert result.rendered_facts == ()


def test_final_projection_rejects_omitted_failed_prior_boundary_decision() -> None:
    candidate = "The service has current pricing. [src_1]"
    graph = _graph()
    sequence_ref, passing_decisions = _passing_boundary_history(candidate, graph)
    failed = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId=f"boundary:failed-synthesis:{sequence_ref.sequence_id}",
            boundarySequenceId=sequence_ref.sequence_id,
            boundarySequenceRef=sequence_ref,
            stage="before_intermediate_synthesis",
            harnessKind="research",
            evidenceGraph=_graph(claim_graph=_unsupported_claim_graph()),
        )
    )

    result = evaluate_research_final_projection_gate(
        ResearchFinalProjectionGateRequest(
            gateId="gate:omitted-failed-prior-boundary",
            mode="local_only",
            candidateFinalAnswer=candidate,
            evidenceGraph=graph,
            boundarySequenceRef=sequence_ref,
            boundaryDecisions=passing_decisions,
        )
    )

    assert failed.status == "repair_required"
    assert result.status == "repair_required"
    assert result.ok is False
    assert "prior_boundary_failed" in result.reason_codes
    assert result.rendered_facts == ()


def test_final_projection_rejects_fresh_sequence_after_same_task_scope_failed() -> None:
    candidate = "The service has current pricing. [src_1]"
    graph = _graph()
    failed_sequence_ref = ResearchBoundarySequenceRef.issue_runtime_sequence_ref(
        runtime_authority=_runtime_authority("research_boundary"),
        sequence_id="sequence:failed-scope",
    )
    failed = enforce_research_boundary(
        ResearchBoundaryRequest(
            boundaryId="boundary:failed-scope-action",
            boundarySequenceId=failed_sequence_ref.sequence_id,
            boundarySequenceRef=failed_sequence_ref,
            stage="after_source_summary",
            harnessKind="research",
            candidateText="I reviewed the pricing source.",
            evidenceGraph=graph,
        )
    )
    sequence_ref, passing_decisions = _passing_boundary_history(candidate, graph)

    result = evaluate_research_final_projection_gate(
        ResearchFinalProjectionGateRequest(
            gateId="gate:fresh-sequence-after-failed-scope",
            mode="local_only",
            candidateFinalAnswer=candidate,
            evidenceGraph=graph,
            boundarySequenceRef=sequence_ref,
            boundaryDecisions=passing_decisions,
        )
    )

    assert failed.status == "blocked"
    assert result.status == "repair_required"
    assert result.ok is False
    assert "prior_boundary_failed" in result.reason_codes
    assert result.rendered_facts == ()


def test_final_projection_rejects_boundary_history_for_different_task_scope() -> None:
    candidate = "The service has current pricing. [src_1]"
    sequence_ref, boundary_decisions = _passing_boundary_history(candidate, _graph())
    graph = _graph(acceptance_criteria=_not_applicable_criteria())

    result = evaluate_research_final_projection_gate(
        ResearchFinalProjectionGateRequest(
            gateId="gate:mismatched-task-scope",
            mode="local_only",
            candidateFinalAnswer=candidate,
            evidenceGraph=graph,
            boundarySequenceRef=sequence_ref,
            boundaryDecisions=boundary_decisions,
        )
    )

    assert result.status == "repair_required"
    assert result.ok is False
    assert "missing_boundary_history" in result.reason_codes
    assert result.rendered_facts == ()


def test_final_projection_rejects_forged_boundary_decision_objects() -> None:
    candidate = "The service has current pricing. [src_1]"
    graph = _graph()
    sequence_ref, boundary_decisions = _passing_boundary_history(candidate, graph)
    forged = type(boundary_decisions[0]).model_validate(
        boundary_decisions[0].model_dump(by_alias=True, mode="python", warnings=False)
    )

    result = evaluate_research_final_projection_gate(
        ResearchFinalProjectionGateRequest(
            gateId="gate:forged-prior-boundary",
            mode="local_only",
            candidateFinalAnswer=candidate,
            evidenceGraph=graph,
            boundarySequenceRef=sequence_ref,
            boundaryDecisions=(forged, boundary_decisions[1]),
        )
    )

    assert result.status == "repair_required"
    assert result.ok is False
    assert "missing_boundary_history" in result.reason_codes
    assert result.rendered_facts == ()


def test_not_applicable_acceptance_criteria_cannot_prove_requested_task_completion() -> None:
    result = _evaluate(
        "Summary ready.",
        _graph(claim_graph=_empty_claim_graph(), acceptance_criteria=_not_applicable_criteria()),
    )

    assert result.status == "partial"
    assert result.ok is False
    assert "missing_task_proof" in result.reason_codes
    assert result.public_projection()["missingWorkReport"] == (
        {
            "criteriaId": "task-proof",
            "status": "missing",
            "description": "User-requested work requires acceptance criteria evidence.",
        },
    )


def test_url_only_citation_fails_without_leaking_raw_url() -> None:
    result = _evaluate(
        "The service has current pricing. https://example.test/pricing",
        _graph(),
    )

    projection = result.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert result.status == "repair_required"
    assert result.ok is False
    assert "url_only_citation" in result.reason_codes
    assert "https://example.test/pricing" not in dumped
    assert projection["outputLinkDigests"]


def test_unopened_source_fails_final_projection() -> None:
    result = _evaluate(
        "The service has current pricing. [src_1]",
        _graph(
            claim_graph=_empty_claim_graph(),
            source_verdicts=_source_verdicts(opened=False),
            acceptance_criteria=_not_applicable_criteria(),
        ),
    )

    assert result.status == "repair_required"
    assert "unopened_source" in result.reason_codes
    assert result.rendered_facts == ()


def test_unsupported_factual_claim_fails_and_is_omitted() -> None:
    unsupported = "The service guarantees unsupported discounts."

    result = _evaluate(
        f"{unsupported} [src_1]",
        _graph(claim_graph=_claim_graph(unsupported, support_verdict="unsupported")),
    )

    projection = result.public_projection()

    assert result.status == "repair_required"
    assert "unsupported_claim" in result.reason_codes
    assert result.rendered_facts == ()
    assert projection["omittedClaims"] == (
        {
            "claimId": "claim:pricing",
            "reasonCode": "unsupported_claim",
            "repairAction": "omit_unsupported_claim",
        },
    )


def test_unsupported_claim_with_citation_before_punctuation_still_fails() -> None:
    unsupported = "The service guarantees unsupported discounts."

    result = _evaluate(
        "The service guarantees unsupported discounts [src_1].",
        _graph(claim_graph=_claim_graph(unsupported, support_verdict="unsupported")),
    )

    projection = result.public_projection()

    assert result.status == "repair_required"
    assert "unsupported_claim" in result.reason_codes
    assert result.rendered_facts == ()
    assert projection["omittedClaims"] == (
        {
            "claimId": "claim:pricing",
            "reasonCode": "unsupported_claim",
            "repairAction": "omit_unsupported_claim",
        },
    )


def test_unmapped_factual_claim_cannot_pass_with_verified_source_ref() -> None:
    result = _evaluate(
        "The product is SOC 2 certified. [src_1]",
        _graph(claim_graph=_empty_claim_graph(), acceptance_criteria=_not_applicable_criteria()),
    )

    projection = result.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert result.status == "repair_required"
    assert "not_evaluated_claim" in result.reason_codes
    assert result.rendered_facts == ()
    assert "The product is SOC 2 certified" not in dumped


def test_uncued_unmapped_factual_claim_cannot_pass_with_verified_source_ref() -> None:
    result = _evaluate(
        "OpenMagi offers team dashboards. [src_1]",
        _graph(claim_graph=_empty_claim_graph(), acceptance_criteria=_not_applicable_criteria()),
    )

    projection = result.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert result.status == "repair_required"
    assert "not_evaluated_claim" in result.reason_codes
    assert result.rendered_facts == ()
    assert "OpenMagi offers team dashboards" not in dumped


@pytest.mark.parametrize("short_claim", [
    "HIPAA compliant.",
    "FedRAMP Moderate.",
    "PCI DSS.",
    "Profitable.",
    "Open source.",
    "Enterprise ready.",
])
def test_short_factual_claim_cannot_pass_without_claim_graph_support(short_claim: str) -> None:
    result = _evaluate(
        f"{short_claim} [src_1]",
        _graph(claim_graph=_empty_claim_graph(), acceptance_criteria=_acceptance_criteria()),
    )

    projection = result.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert result.status == "repair_required"
    assert "not_evaluated_claim" in result.reason_codes
    assert result.rendered_facts == ()
    assert short_claim.rstrip(".") not in dumped


def test_candidate_password_text_does_not_render_as_supported_fact() -> None:
    claim_text = "The password is hunter2."

    result = _evaluate(
        f"{claim_text} [src_1]",
        _graph(claim_graph=_claim_graph(claim_text), acceptance_criteria=_acceptance_criteria()),
    )

    dumped = json.dumps(result.public_projection(), sort_keys=True)

    assert result.status == "repair_required"
    assert "unsafe_candidate_projection" in result.reason_codes
    assert result.rendered_facts == ()
    assert "hunter2" not in dumped


def test_candidate_natural_language_secret_text_without_digits_does_not_render_as_supported_fact(
) -> None:
    claim_text = "The password is swordfish."

    result = _evaluate(
        f"{claim_text} [src_1]",
        _graph(claim_graph=_claim_graph(claim_text), acceptance_criteria=_acceptance_criteria()),
    )

    dumped = json.dumps(result.public_projection(), sort_keys=True)

    assert result.status == "repair_required"
    assert "unsafe_candidate_projection" in result.reason_codes
    assert result.rendered_facts == ()
    assert "swordfish" not in dumped


def test_candidate_short_bearer_text_does_not_render_as_supported_fact() -> None:
    claim_text = "Bearer abc123."

    result = _evaluate(
        f"{claim_text} [src_1]",
        _graph(claim_graph=_claim_graph(claim_text), acceptance_criteria=_acceptance_criteria()),
    )

    dumped = json.dumps(result.public_projection(), sort_keys=True)

    assert result.status == "repair_required"
    assert "unsafe_candidate_projection" in result.reason_codes
    assert result.rendered_facts == ()
    assert "abc123" not in dumped


def test_candidate_raw_child_output_text_does_not_render_as_supported_fact() -> None:
    claim_text = "raw child output leaked."

    result = _evaluate(
        f"{claim_text} [src_1]",
        _graph(claim_graph=_claim_graph(claim_text), acceptance_criteria=_acceptance_criteria()),
    )

    dumped = json.dumps(result.public_projection(), sort_keys=True)

    assert result.status == "repair_required"
    assert "unsafe_candidate_projection" in result.reason_codes
    assert result.rendered_facts == ()
    assert "raw child output" not in dumped


@pytest.mark.parametrize("claim_text, secret_fragment", [
    ("The callback code abc123 leaked.", "abc123"),
    ("The callback query contains code=abc123 and state=def456.", "def456"),
])
def test_candidate_callback_secret_text_does_not_render_as_supported_fact(
    claim_text: str,
    secret_fragment: str,
) -> None:
    result = _evaluate(
        f"{claim_text} [src_1]",
        _graph(claim_graph=_claim_graph(claim_text), acceptance_criteria=_acceptance_criteria()),
    )

    dumped = json.dumps(result.public_projection(), sort_keys=True)

    assert result.status == "repair_required"
    assert "unsafe_candidate_projection" in result.reason_codes
    assert result.rendered_facts == ()
    assert secret_fragment not in dumped


@pytest.mark.parametrize("short_claim", [
    "FedRAMP Moderate.",
    "PCI DSS.",
    "Profitable.",
    "Open source.",
    "Enterprise ready.",
])
def test_uncited_short_factual_claim_cannot_pass_without_claim_graph_support(
    short_claim: str,
) -> None:
    result = _evaluate(
        short_claim,
        _graph(claim_graph=_empty_claim_graph(), acceptance_criteria=_acceptance_criteria()),
    )

    projection = result.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert result.status == "repair_required"
    assert "not_evaluated_claim" in result.reason_codes
    assert result.rendered_facts == ()
    assert short_claim.rstrip(".") not in dumped


def test_non_english_unmapped_factual_claim_cannot_pass_with_verified_source_ref() -> None:
    result = _evaluate(
        "오픈마기는 팀 대시보드를 제공합니다. [src_1]",
        _graph(claim_graph=_empty_claim_graph(), acceptance_criteria=_not_applicable_criteria()),
    )

    projection = result.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert result.status == "repair_required"
    assert result.ok is False
    assert "not_evaluated_claim" in result.reason_codes
    assert result.rendered_facts == ()
    assert "오픈마기" not in dumped


@pytest.mark.parametrize("candidate", [
    "Here is the answer. The service has current pricing. [src_1]",
    "Here is a concise answer. The service has current pricing. [src_1]",
    "Below is the answer. The service has current pricing. [src_1]",
    "Below is the answer: The service has current pricing. [src_1]",
    "Below is the answer: The service has current pricing [src_1]",
    "The service has current pricing. [src_1] Thanks.",
])
def test_low_information_wrapper_prose_does_not_require_claim_support(candidate: str) -> None:
    result = _evaluate(candidate, _graph())

    assert result.status == "passed"
    assert result.rendered_facts[0].text == "The service has current pricing."


def test_low_information_wrapper_only_answer_cannot_pass_as_complete() -> None:
    result = _evaluate("Here is a concise answer.", _graph())

    projection = result.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert result.status == "repair_required"
    assert "not_evaluated_claim" in result.reason_codes
    assert result.rendered_facts == ()
    assert "Here is a concise answer" not in dumped


def test_non_english_action_claim_cannot_pass_without_runtime_receipt() -> None:
    result = _evaluate(
        "가격 페이지를 검토했습니다.",
        _graph(claim_graph=_empty_claim_graph(), acceptance_criteria=_not_applicable_criteria()),
    )

    projection = result.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert result.status == "repair_required"
    assert result.ok is False
    assert "not_evaluated_claim" in result.reason_codes
    assert result.rendered_facts == ()
    assert "검토했습니다" not in dumped


def test_fact_projection_requires_support_bound_to_candidate_claim_digest() -> None:
    result = _evaluate(
        "The service has current pricing. [src_1]",
        _graph(
            claim_graph=_claim_graph(
                "The service has current pricing.",
                bind_support_to_claim_text=False,
            ),
            acceptance_criteria=_not_applicable_criteria(),
        ),
    )

    assert result.status == "repair_required"
    assert "not_evaluated_claim" in result.reason_codes
    assert result.rendered_facts == ()


def test_fact_projection_renders_only_candidate_bound_supported_spans() -> None:
    result = _evaluate(
        "The service has current pricing. [src_1]",
        _graph(
            claim_graph=_mixed_support_claim_graph(),
            source_verdicts=_source_verdicts(
                span_refs=("span:pricing", "span:unsupported"),
            ),
            acceptance_criteria=_acceptance_criteria(),
        ),
    )

    assert result.status == "passed"
    assert result.rendered_facts[0].span_refs == ("span:pricing",)
    assert result.rendered_facts[0].source_refs == ("src_1",)


def test_narrower_candidate_claim_cannot_pass_by_substring_match() -> None:
    result = _evaluate(
        "The service has current pricing. [src_1]",
        _graph(
            claim_graph=_claim_graph(
                "The service has current pricing for enterprise accounts only."
            ),
            acceptance_criteria=_not_applicable_criteria(),
        ),
    )

    assert result.status == "repair_required"
    assert "not_evaluated_claim" in result.reason_codes
    assert result.rendered_facts == ()


def test_weak_claim_renders_as_qualified_language_not_fact() -> None:
    weak_claim = "The pricing page suggests enterprise positioning."

    result = _evaluate(
        f"{weak_claim} [src_1]",
        _graph(claim_graph=_claim_graph(weak_claim, support_verdict="weak")),
    )

    projection = result.public_projection()

    assert result.status == "passed"
    assert result.rendered_facts == ()
    assert projection["qualifiedClaims"] == (
        {
            "claimId": "claim:pricing",
            "text": "Evidence suggests: The pricing page suggests enterprise positioning.",
            "sourceRefs": ("src_1",),
            "spanRefs": ("span:pricing",),
            "renderAsFact": False,
        },
    )


def test_unproven_reviewed_claim_fails_with_not_verified_projection() -> None:
    result = _evaluate(
        "I reviewed the pricing page.",
        _graph(claim_graph=_empty_claim_graph(), acceptance_criteria=_not_applicable_criteria()),
    )

    projection = result.public_projection()

    assert result.status == "repair_required"
    assert "action_claim_without_receipt" in result.reason_codes
    assert projection["actionProjections"] == (
        {
            "claimId": "claim:1:reviewed",
            "actionVerb": "reviewed",
            "text": "not verified: reviewed",
            "verified": False,
        },
    )


def test_verified_action_claim_can_render_as_verified_metadata() -> None:
    candidate = "I reviewed the pricing page."

    result = _evaluate(
        candidate,
        _graph(
            claim_graph=_empty_claim_graph(),
            action_text=candidate,
            acceptance_criteria=_acceptance_criteria(),
        ),
    )

    assert result.status == "passed"
    assert result.public_projection()["actionProjections"] == (
        {
            "claimId": "claim:1:reviewed",
            "actionVerb": "reviewed",
            "text": "verified: reviewed",
            "verified": True,
        },
    )


def test_partial_answer_path_is_public_safe_for_incomplete_criteria() -> None:
    result = _evaluate(
        "The service has current pricing. [src_1]",
        _graph(acceptance_criteria=_acceptance_criteria(missing_required=True)),
    )

    projection = result.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert result.status == "partial"
    assert "incomplete_acceptance_criteria" in result.reason_codes
    assert projection["missingWorkReport"] == (
        {
            "criteriaId": "criteria:competitor-comparison",
            "status": "missing",
            "description": "Competitor comparison requires inspected source evidence.",
        },
    )
    for forbidden in ("raw", "authorization", "cookie", "token", "/Users/", "sourceBody"):
        assert forbidden not in dumped


def test_windows_private_path_claim_text_does_not_render_publicly() -> None:
    private_path = _windows_private_path()
    claim_text = f"The private fixture is at {private_path}."

    result = _evaluate(
        f"{claim_text} [src_1]",
        _graph(
            claim_graph=_claim_graph(claim_text),
            acceptance_criteria=_not_applicable_criteria(),
        ),
    )

    dumped = json.dumps(result.public_projection(), sort_keys=True)

    assert result.status == "repair_required"
    assert "unsafe_candidate_projection" in result.reason_codes
    assert result.rendered_facts == ()
    assert private_path not in dumped
    assert "private fixture" not in dumped


def test_forward_slash_windows_private_path_claim_text_does_not_render_publicly() -> None:
    private_path = _windows_forward_private_path()
    claim_text = f"The fixture lives at {private_path}."

    result = _evaluate(
        f"{claim_text} [src_1]",
        _graph(
            claim_graph=_claim_graph(claim_text),
            acceptance_criteria=_not_applicable_criteria(),
        ),
    )

    dumped = json.dumps(result.public_projection(), sort_keys=True)

    assert result.status == "repair_required"
    assert "unsafe_candidate_projection" in result.reason_codes
    assert result.rendered_facts == ()
    assert private_path not in dumped
    assert "fixture lives" not in dumped


def test_windows_private_path_missing_work_description_is_sanitized() -> None:
    criteria = ResearchAcceptanceCriteriaSet(
        criteriaSetId="criteria-set:windows-private",
        targetLabel="Windows private path fixture",
        criteria=(
            ResearchAcceptanceCriterion(
                criteriaId="criteria:windows-private",
                description=f"Compare notes from {_unc_private_path()}.",
                requiredEvidenceTypes=("competitor_context",),
                optionalEvidenceTypes=(),
                sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
                completionMode="required",
                evidenceRefs=(),
            ),
        ),
    )

    result = _evaluate(
        "The service has current pricing. [src_1]",
        _graph(acceptance_criteria=criteria),
    )

    projection = result.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert result.status == "partial"
    assert projection["missingWorkReport"] == (
        {
            "criteriaId": "criteria:windows-private",
            "status": "missing",
            "description": "Missing required research criterion.",
        },
    )
    assert _unc_private_path() not in dumped


def test_forward_slash_windows_private_path_missing_work_description_is_sanitized() -> None:
    criteria = ResearchAcceptanceCriteriaSet(
        criteriaSetId="criteria-set:windows-forward-private",
        targetLabel="Windows forward private path fixture",
        criteria=(
            ResearchAcceptanceCriterion(
                criteriaId="criteria:windows-forward-private",
                description=f"Compare notes from {_windows_forward_private_path()}.",
                requiredEvidenceTypes=("competitor_context",),
                optionalEvidenceTypes=(),
                sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
                completionMode="required",
                evidenceRefs=(),
            ),
        ),
    )

    result = _evaluate(
        "The service has current pricing. [src_1]",
        _graph(acceptance_criteria=criteria),
    )

    projection = result.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert result.status == "partial"
    assert projection["missingWorkReport"] == (
        {
            "criteriaId": "criteria:windows-forward-private",
            "status": "missing",
            "description": "Missing required research criterion.",
        },
    )
    assert _windows_forward_private_path() not in dumped


def test_raw_candidate_or_evidence_data_does_not_leak_from_public_projection() -> None:
    result = _evaluate(
        (
            "The service has current pricing. [src_1]\n"
            f"Raw source content from {_posix_private_path()} should not be public."
        ),
        _graph(),
    )

    dumped = json.dumps(result.public_projection(), sort_keys=True)

    assert result.ok is False
    assert "unsafe_candidate_projection" in result.reason_codes
    assert _posix_private_path() not in dumped
    assert "Raw source content" not in dumped
    assert "candidateFinalAnswer" not in dumped


def test_forged_projection_result_cannot_publicly_render_facts() -> None:
    issued = _evaluate(
        "The service has current pricing. [src_1]",
        _graph(),
    )
    forged = type(issued).model_validate(
        issued.model_dump(by_alias=True, mode="python", warnings=False)
    )

    with pytest.raises(ValueError, match="issued by the final projection gate"):
        forged.public_projection()
