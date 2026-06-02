from __future__ import annotations

import importlib
import inspect
import json

import pytest

import magi_agent.research.repair as repair_module
from magi_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    ChildRuntimeEnvelopeAuthorityFlags,
)
from runtime_issuance_support import issue_test_runtime_authority
from magi_agent.evidence.subagent import (
    OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
    DelegatedEvidenceRequirement,
    EvidenceBoundaryLedgerRef,
    ExecutionBoundaryIdentity,
)
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
from magi_agent.research.claim_graph import (
    ResearchClaimGraph,
    ResearchClaimSupportRef,
    build_research_claim_node,
)
from magi_agent.research.evidence_graph import (
    ResearchEvidenceGraph,
    ResearchMissingEvidenceReason,
)
from magi_agent.research.repair import (
    RESEARCH_REPAIR_ACTIONS,
    ResearchRepairPolicy,
    plan_research_repairs,
)
from magi_agent.research.source_proof import (
    ResearchSourceOpenReceiptRef,
    ResearchSourceProofRequirement,
    verify_research_source_proof,
)


def _digest(char: str = "a") -> str:
    return "sha256:" + char * 64


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-repair",
        scopes=scopes,
    )


def _criteria_not_applicable() -> ResearchAcceptanceCriteriaSet:
    return ResearchAcceptanceCriteriaSet(
        criteriaSetId="criteria-set:repair",
        targetLabel="Repair fixture",
        criteria=(
            ResearchAcceptanceCriterion(
                criteriaId="criteria:not-applicable",
                description="No task evidence is required for this repair fixture.",
                requiredEvidenceTypes=(),
                optionalEvidenceTypes=(),
                completionMode="not_applicable",
            ),
        ),
    )


def _incomplete_criteria() -> ResearchAcceptanceCriteriaSet:
    return ResearchAcceptanceCriteriaSet(
        criteriaSetId="criteria-set:incomplete",
        targetLabel="Incomplete repair fixture",
        criteria=(
            ResearchAcceptanceCriterion(
                criteriaId="criteria:required",
                description="Required source inspection evidence is still missing.",
                requiredEvidenceTypes=("source_inspection",),
                optionalEvidenceTypes=(),
                completionMode="required",
            ),
        ),
    )


def _empty_claim_graph() -> ResearchClaimGraph:
    return ResearchClaimGraph(claimGraphId="claim-graph:empty", claims=())


def _action_verdicts():
    claim = detect_research_action_claims("I checked the pricing source.")[0]
    receipt = ResearchActionProofReceiptRef.issue_runtime_receipt(
        runtime_authority=_runtime_authority("research_action_proof"),
        receipt_id="receipt:checked-1",
        action_verb="checked",
        receipt_kind="toolhost_receipt",
        tool_id="tool:web-search",
        source_id="source:pricing-page",
        observed_at="2026-05-27T12:00:00Z",
        public_label="Runtime ToolHost receipt",
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
                notBefore="2026-05-27T10:00:00Z",
                notAfter="2026-05-27T13:00:00Z",
            ),
        ),
    )


def _allowed_source_verdicts():
    source_ref = ResearchSourceOpenReceiptRef.issue_runtime_source_ref(
        runtime_authority=_runtime_authority("research_source_proof"),
        source_ref_id="src_1",
        source_kind="web_fetch",
        receipt_kind="opened_snapshot",
        opened=True,
        content_digest=_digest("1"),
        inspected_at="2026-05-27T12:00:00Z",
        span_refs=("span:pricing",),
        redaction_status="redacted",
        public_label="Pricing metadata",
    )
    requirement = ResearchSourceProofRequirement(
        sourceRefId="src_1",
        allowedSourceKinds=("web_fetch",),
        requiredReceiptKinds=("opened_snapshot",),
        requiredSpanRefs=("span:pricing",),
        notBefore="2026-05-27T10:00:00Z",
        notAfter="2026-05-27T13:00:00Z",
    )
    return verify_research_source_proof((requirement,), (source_ref,))


def _missing_source_verdicts():
    requirement = ResearchSourceProofRequirement(
        sourceRefId="src_1",
        allowedSourceKinds=("web_fetch",),
        requiredReceiptKinds=("opened_snapshot",),
        requiredSpanRefs=("span:pricing",),
        notBefore="2026-05-27T10:00:00Z",
        notAfter="2026-05-27T13:00:00Z",
    )
    return verify_research_source_proof((requirement,), ())


def _stale_source_verdicts():
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
        notBefore="2026-05-27T10:00:00Z",
        notAfter="2026-05-27T13:00:00Z",
    )
    return verify_research_source_proof((requirement,), (source_ref,))


def _missing_span_verdicts():
    source_ref = ResearchSourceOpenReceiptRef.issue_runtime_source_ref(
        runtime_authority=_runtime_authority("research_source_proof"),
        source_ref_id="src_1",
        source_kind="web_fetch",
        receipt_kind="opened_snapshot",
        opened=True,
        content_digest=_digest("1"),
        inspected_at="2026-05-27T12:00:00Z",
        span_refs=("span:summary",),
        redaction_status="redacted",
        public_label="Summary metadata",
    )
    requirement = ResearchSourceProofRequirement(
        sourceRefId="src_1",
        allowedSourceKinds=("web_fetch",),
        requiredReceiptKinds=("opened_snapshot",),
        requiredSpanRefs=("span:pricing",),
        notBefore="2026-05-27T10:00:00Z",
        notAfter="2026-05-27T13:00:00Z",
    )
    return verify_research_source_proof((requirement,), (source_ref,))


def _claim_graph_with_support(support_verdict: str) -> ResearchClaimGraph:
    support_ref = ResearchClaimSupportRef.issue_verified_support_ref(
        runtime_authority=_runtime_authority("research_claim_support"),
        support_ref_id=f"support:{support_verdict}",
        source_ref_id="src_1",
        span_refs=("span:pricing",),
        source_digest=_digest("1"),
        evidence_digest=_digest("2"),
        evidence_kind="source_span",
        support_verdict=support_verdict,
        freshness_verdict="current",
        relevance_verdict="relevant",
    )
    claim = build_research_claim_node(
        claim_id=f"claim:{support_verdict}",
        claim_text_digest=_digest("3"),
        claim_kind="factual",
        claim_preview="The service has a pricing claim.",
        support_refs=(support_ref,),
    )
    return ResearchClaimGraph(claimGraphId=f"claim-graph:{support_verdict}", claims=(claim,))


def _evidence_graph(
    *,
    action_verdicts=(),
    source_verdicts=(),
    claim_graph: ResearchClaimGraph | None = None,
    criteria: ResearchAcceptanceCriteriaSet | None = None,
    child_evidence_envelopes: tuple[ChildRuntimeEnvelope, ...] = (),
    missing_reasons: tuple[ResearchMissingEvidenceReason, ...] = (),
) -> ResearchEvidenceGraph:
    if child_evidence_envelopes:
        return ResearchEvidenceGraph.from_runtime_evidence(
            evidence_graph_id="evidence-graph:repair",
            action_proof_verdicts=tuple(action_verdicts),
            source_proof_verdicts=tuple(source_verdicts),
            claim_graph=claim_graph or _empty_claim_graph(),
            acceptance_criteria=criteria or _criteria_not_applicable(),
            child_evidence_envelopes=child_evidence_envelopes,
            missing_evidence_reasons=missing_reasons,
        )
    return ResearchEvidenceGraph(
        evidenceGraphId="evidence-graph:repair",
        actionProofVerdicts=tuple(action_verdicts),
        sourceProofVerdicts=tuple(source_verdicts),
        claimGraph=claim_graph or _empty_claim_graph(),
        acceptanceCriteria=criteria or _criteria_not_applicable(),
        missingEvidenceReasons=missing_reasons,
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
                DelegatedEvidenceRequirement(
                    type="SourceInspection",
                    delegation="delegated_required",
                ),
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
                "rawTranscriptPreview": "raw child transcript with sk-child-secret",
                "workspacePath": "/workspace/bot/private",
                "authorization": "Bearer unsafe-token",
            },
        },
    )


def _actions(result) -> tuple[str, ...]:
    return tuple(action.action for action in result.actions)


def test_repair_action_catalog_contains_required_pr7_actions() -> None:
    assert RESEARCH_REPAIR_ACTIONS == (
        "inspect_missing_source",
        "refresh_stale_source",
        "extract_missing_span",
        "downgrade_weak_claim",
        "omit_unsupported_claim",
        "request_user_clarification",
        "return_partial_with_missing_work_report",
    )


def test_missing_source_chooses_inspect_missing_source() -> None:
    result = plan_research_repairs(
        _evidence_graph(source_verdicts=_missing_source_verdicts())
    )

    assert result.status == "repair_planned"
    assert _actions(result) == ("inspect_missing_source",)
    assert result.actions[0].subject_ref_id == "src_1"


def test_stale_source_chooses_refresh_stale_source() -> None:
    result = plan_research_repairs(
        _evidence_graph(source_verdicts=_stale_source_verdicts())
    )

    assert result.status == "repair_planned"
    assert _actions(result) == ("refresh_stale_source",)
    assert result.actions[0].subject_ref_id == "src_1"


def test_missing_source_reason_chooses_inspect_missing_source() -> None:
    result = plan_research_repairs(
        _evidence_graph(
            missing_reasons=(
                ResearchMissingEvidenceReason(
                    reasonId="missing:source",
                    subjectRefId="criteria:not-applicable",
                    evidenceType="source_inspection",
                    reasonCode="missing_source_proof",
                ),
            )
        )
    )

    assert result.status == "repair_planned"
    assert _actions(result) == ("inspect_missing_source",)
    assert result.actions[0].subject_ref_id == "criteria:not-applicable"


def test_stale_source_reason_chooses_refresh_stale_source() -> None:
    result = plan_research_repairs(
        _evidence_graph(
            missing_reasons=(
                ResearchMissingEvidenceReason(
                    reasonId="missing:stale-source",
                    subjectRefId="criteria:not-applicable",
                    evidenceType="source_inspection",
                    reasonCode="stale_source",
                ),
            )
        )
    )

    assert result.status == "repair_planned"
    assert _actions(result) == ("refresh_stale_source",)
    assert result.actions[0].subject_ref_id == "criteria:not-applicable"


def test_source_span_mismatch_chooses_extract_missing_span() -> None:
    result = plan_research_repairs(
        _evidence_graph(source_verdicts=_missing_span_verdicts())
    )

    assert result.status == "repair_planned"
    assert _actions(result) == ("extract_missing_span",)
    assert result.actions[0].subject_ref_id == "src_1"


def test_weak_claim_chooses_downgrade_weak_claim() -> None:
    result = plan_research_repairs(
        _evidence_graph(
            source_verdicts=_allowed_source_verdicts(),
            claim_graph=_claim_graph_with_support("weak"),
        )
    )

    assert result.status == "repair_planned"
    assert _actions(result) == ("downgrade_weak_claim",)
    assert result.actions[0].subject_ref_id == "claim:weak"


def test_unsupported_claim_chooses_omit_by_default_policy() -> None:
    result = plan_research_repairs(
        _evidence_graph(
            source_verdicts=_allowed_source_verdicts(),
            claim_graph=_claim_graph_with_support("unsupported"),
        )
    )

    assert result.status == "repair_planned"
    assert _actions(result) == ("omit_unsupported_claim",)
    assert result.actions[0].subject_ref_id == "claim:unsupported"


def test_unsupported_claim_chooses_user_clarification_when_policy_repairs() -> None:
    result = plan_research_repairs(
        _evidence_graph(
            source_verdicts=_allowed_source_verdicts(),
            claim_graph=_claim_graph_with_support("unsupported"),
        ),
        policy=ResearchRepairPolicy(unsupportedClaimStrategy="repair"),
    )

    assert result.status == "clarification_required"
    assert _actions(result) == ("request_user_clarification",)
    assert result.actions[0].subject_ref_id == "claim:unsupported"


def test_repair_policy_mutation_cannot_change_unsupported_claim_strategy() -> None:
    policy = ResearchRepairPolicy(unsupportedClaimStrategy="omit")
    policy.__dict__["unsupported_claim_strategy"] = "repair"

    with pytest.raises(ValueError, match="repair policy"):
        plan_research_repairs(
            _evidence_graph(
                source_verdicts=_allowed_source_verdicts(),
                claim_graph=_claim_graph_with_support("unsupported"),
            ),
            policy=policy,
        )


def test_repair_policy_private_fingerprint_refresh_cannot_hide_mutation() -> None:
    policy = ResearchRepairPolicy(unsupportedClaimStrategy="omit")
    policy.__dict__["unsupported_claim_strategy"] = "repair"
    policy.__dict__["_created_fingerprint"] = "attacker-refreshed"

    with pytest.raises(ValueError, match="repair policy"):
        plan_research_repairs(
            _evidence_graph(
                source_verdicts=_allowed_source_verdicts(),
                claim_graph=_claim_graph_with_support("unsupported"),
            ),
            policy=policy,
        )


def test_incomplete_task_returns_partial_report_when_bounded_retries_exhausted() -> None:
    result = plan_research_repairs(
        _evidence_graph(criteria=_incomplete_criteria()),
        policy=ResearchRepairPolicy(maxRepairAttempts=2, repairAttempt=2),
    )

    assert result.status == "partial_report"
    assert _actions(result) == ("return_partial_with_missing_work_report",)
    assert result.missing_work_report == ("criteria:required:missing",)


def test_repair_policy_mutation_cannot_force_bounded_retry_exhaustion() -> None:
    policy = ResearchRepairPolicy(maxRepairAttempts=2, repairAttempt=0)
    policy.__dict__["repair_attempt"] = 2

    with pytest.raises(ValueError, match="repair policy"):
        plan_research_repairs(
            _evidence_graph(criteria=_incomplete_criteria()),
            policy=policy,
        )


def test_repair_result_projection_is_digest_safe_and_default_off() -> None:
    result = plan_research_repairs(
        _evidence_graph(source_verdicts=_missing_source_verdicts())
    )
    projection = result.public_projection()
    serialized = json.dumps(projection, sort_keys=True)

    assert projection["digest"].startswith("sha256:")
    assert projection["executionPosture"] == {
        "defaultOff": True,
        "localOnly": True,
        "fakeProviderOnly": True,
        "liveExecutionAllowed": False,
        "providerCallsAllowed": False,
        "browserExecutionAllowed": False,
        "toolExecutionAllowed": False,
        "modelCallsAllowed": False,
        "memoryWritesAllowed": False,
        "channelDeliveryAllowed": False,
        "adkRunnerAttached": False,
        "functionToolAttached": False,
    }
    assert projection["authorityFlags"] == {
        "liveToolDispatched": False,
        "providerCalled": False,
        "browserOpened": False,
        "modelCalled": False,
        "memoryWritten": False,
        "channelDeliveryPerformed": False,
        "adkRunnerAttached": False,
        "functionToolAttached": False,
    }
    assert "Metadata only" in projection["adkUsageNotes"]
    assert "no ADK Runner" in projection["adkUsageNotes"]
    assert "FunctionTool" in projection["adkUsageNotes"]
    forbidden_fragments = (
        "http://",
        "https://",
        "/Users/",
        "/workspace/",
        "raw_source",
        "raw output",
        "authorization",
        "secret",
        "token",
    )
    assert all(fragment not in serialized for fragment in forbidden_fragments)


def test_fake_provider_metadata_cannot_be_mutated_after_result_creation() -> None:
    result = plan_research_repairs(
        _evidence_graph(source_verdicts=_missing_source_verdicts())
    )

    with pytest.raises(TypeError):
        result.fake_provider_metadata["liveProvider"] = True
    with pytest.raises(TypeError):
        result.actions[0].fake_provider_metadata["liveProvider"] = True


def test_projection_rejects_post_creation_metadata_dict_mutation() -> None:
    result = plan_research_repairs(
        _evidence_graph(source_verdicts=_missing_source_verdicts())
    )
    result.fake_provider_metadata.__dict__["live_provider"] = True

    with pytest.raises(ValueError, match="modified after creation"):
        result.public_projection()


def test_projection_rejects_post_creation_action_metadata_dict_mutation() -> None:
    result = plan_research_repairs(
        _evidence_graph(source_verdicts=_missing_source_verdicts())
    )
    result.actions[0].fake_provider_metadata.__dict__["live_provider"] = True

    with pytest.raises(ValueError, match="modified after creation"):
        result.public_projection()


def test_projection_rejects_action_digest_recompute_after_nested_metadata_mutation() -> None:
    result = plan_research_repairs(
        _evidence_graph(source_verdicts=_missing_source_verdicts())
    )
    action = result.actions[0]
    action.fake_provider_metadata.__dict__["live_provider"] = True
    action.__dict__["digest"] = repair_module._digest_for(action._digest_payload())

    with pytest.raises(ValueError, match="repair action"):
        action.public_projection()


def test_projection_rejects_post_creation_authority_flag_mutation() -> None:
    result = plan_research_repairs(
        _evidence_graph(source_verdicts=_missing_source_verdicts())
    )
    result.authority_flags.__dict__["model_called"] = True

    with pytest.raises(ValueError, match="modified after creation"):
        result.public_projection()


def test_projection_rejects_result_digest_recompute_after_authority_mutation() -> None:
    result = plan_research_repairs(
        _evidence_graph(source_verdicts=_missing_source_verdicts())
    )
    result.authority_flags.__dict__["model_called"] = True
    result.__dict__["digest"] = repair_module._digest_for(result._digest_payload())

    with pytest.raises(ValueError, match="repair result"):
        result.public_projection()


def test_unhandled_missing_evidence_reason_requires_clarification_not_noop() -> None:
    result = plan_research_repairs(
        _evidence_graph(
            missing_reasons=(
                ResearchMissingEvidenceReason(
                    reasonId="missing:child",
                    subjectRefId="criteria:not-applicable",
                    evidenceType="child_evidence",
                    reasonCode="child_evidence_missing",
                ),
            )
        )
    )

    assert result.status == "clarification_required"
    assert _actions(result) == ("request_user_clarification",)
    assert result.missing_work_report == ("criteria:not-applicable:child_evidence_missing",)


def test_forged_source_verdict_like_object_cannot_drive_repair_planning() -> None:
    class ForgedSourceVerdict:
        source_ref_id = "src_1"
        reason_code = "missing_source"

    graph = _evidence_graph(source_verdicts=_allowed_source_verdicts())
    graph.__dict__["source_proof_verdicts"] = (ForgedSourceVerdict(),)

    with pytest.raises(TypeError, match="source proof verdict"):
        plan_research_repairs(graph)


def test_forged_source_verdict_is_rejected_before_partial_report_early_return() -> None:
    class ForgedSourceVerdict:
        source_ref_id = "src_1"
        reason_code = "missing_source"

    graph = _evidence_graph(
        source_verdicts=_allowed_source_verdicts(),
        criteria=_incomplete_criteria(),
    )
    graph.__dict__["source_proof_verdicts"] = (ForgedSourceVerdict(),)

    with pytest.raises(TypeError, match="source proof verdict"):
        plan_research_repairs(
            graph,
            policy=ResearchRepairPolicy(maxRepairAttempts=1, repairAttempt=1),
        )


def test_post_validation_same_class_source_verdict_mutation_cannot_drive_repair_planning() -> None:
    verdict = _allowed_source_verdicts()[0]
    graph = _evidence_graph(source_verdicts=(verdict,))
    verdict.__dict__["reason_code"] = "missing_source"
    verdict.__dict__["verdict"] = "denied"

    with pytest.raises(ValueError, match="source proof verdict"):
        plan_research_repairs(graph)


def test_post_validation_action_verdict_mutation_cannot_drive_repair_planning() -> None:
    verdict = _action_verdicts()[0]
    graph = _evidence_graph(action_verdicts=(verdict,))
    verdict.__dict__["claim_id"] = "claim:forged"

    with pytest.raises(ValueError, match="evidenceGraph"):
        plan_research_repairs(graph)


def test_post_validation_child_evidence_ref_mutation_cannot_drive_repair_planning() -> None:
    graph = _evidence_graph(child_evidence_envelopes=(_child_envelope(),))
    graph.child_evidence_refs[0].__dict__["status"] = "forged"

    with pytest.raises(ValueError, match="evidenceGraph"):
        plan_research_repairs(graph)


def test_post_validation_top_level_graph_replacements_cannot_drive_repair_planning() -> None:
    cases = (
        (
            "claim_graph",
            _evidence_graph(
                source_verdicts=_allowed_source_verdicts(),
                claim_graph=_claim_graph_with_support("unsupported"),
            ),
            _empty_claim_graph(),
        ),
        (
            "acceptance_criteria",
            _evidence_graph(criteria=_incomplete_criteria()),
            _criteria_not_applicable(),
        ),
        (
            "source_proof_verdicts",
            _evidence_graph(source_verdicts=_stale_source_verdicts()),
            _allowed_source_verdicts(),
        ),
        (
            "action_proof_verdicts",
            _evidence_graph(action_verdicts=_action_verdicts()),
            (),
        ),
        (
            "child_evidence_refs",
            _evidence_graph(child_evidence_envelopes=(_child_envelope(),)),
            (),
        ),
        (
            "missing_evidence_reasons",
            _evidence_graph(
                missing_reasons=(
                    ResearchMissingEvidenceReason(
                        reasonId="missing:source",
                        subjectRefId="criteria:not-applicable",
                        evidenceType="source_inspection",
                        reasonCode="missing_source_proof",
                    ),
                )
            ),
            (),
        ),
    )
    for field_name, graph, replacement in cases:
        graph.__dict__[field_name] = replacement
        with pytest.raises(ValueError, match="evidenceGraph"):
            plan_research_repairs(graph)


def test_evidence_graph_private_fingerprint_refresh_cannot_hide_replacement() -> None:
    graph = _evidence_graph(
        source_verdicts=_allowed_source_verdicts(),
        claim_graph=_claim_graph_with_support("unsupported"),
    )
    graph.__dict__["claim_graph"] = _empty_claim_graph()
    graph.__dict__["_created_fingerprint"] = "attacker-refreshed"

    with pytest.raises(ValueError, match="evidenceGraph"):
        plan_research_repairs(graph)


def test_forged_graph_like_object_cannot_drive_repair_planning() -> None:
    class ForgedGraph:
        evidence_graph_id = "evidence-graph:forged"
        source_proof_verdicts = ()
        claim_graph = _claim_graph_with_support("unsupported")
        acceptance_criteria = _criteria_not_applicable()
        missing_evidence_reasons = ()

    with pytest.raises(TypeError, match="ResearchEvidenceGraph"):
        plan_research_repairs(ForgedGraph())  # type: ignore[arg-type]


def test_post_validation_mutated_claim_graph_cannot_drive_repair_planning() -> None:
    class ForgedClaimGraph:
        claims = _claim_graph_with_support("unsupported").claims

    graph = _evidence_graph()
    graph.__dict__["claim_graph"] = ForgedClaimGraph()

    with pytest.raises(TypeError, match="claimGraph"):
        plan_research_repairs(graph)


def test_post_validation_mutated_claim_nodes_cannot_drive_repair_planning() -> None:
    class ForgedClaim:
        claim_id = "claim:forged"
        support_verdict = "unsupported"

    graph = _evidence_graph(
        source_verdicts=_allowed_source_verdicts(),
        claim_graph=_claim_graph_with_support("supported"),
    )
    graph.claim_graph.__dict__["claims"] = (ForgedClaim(),)

    with pytest.raises(TypeError, match="claim node"):
        plan_research_repairs(graph)


def test_post_validation_mutated_criteria_cannot_drive_repair_planning() -> None:
    class ForgedCriterion:
        criteria_id = "criteria:forged"
        status = "missing"

    graph = _evidence_graph(criteria=_criteria_not_applicable())
    graph.acceptance_criteria.__dict__["criteria"] = (ForgedCriterion(),)

    with pytest.raises(TypeError, match="acceptance criterion"):
        plan_research_repairs(graph)


def test_post_validation_mutated_missing_reason_cannot_drive_repair_planning() -> None:
    class ForgedMissingReason:
        subject_ref_id = "criteria:not-applicable"
        reason_code = "missing_source_proof"

    graph = _evidence_graph()
    graph.__dict__["missing_evidence_reasons"] = (ForgedMissingReason(),)

    with pytest.raises(TypeError, match="missing evidence"):
        plan_research_repairs(graph)


def test_post_validation_same_class_claim_mutation_cannot_drive_repair_planning() -> None:
    graph = _evidence_graph(
        source_verdicts=_allowed_source_verdicts(),
        claim_graph=_claim_graph_with_support("unsupported"),
    )
    graph.claim_graph.claims[0].__dict__["support_verdict"] = "supported"

    with pytest.raises(ValueError, match="modified"):
        plan_research_repairs(graph)


def test_post_validation_same_class_criterion_mutation_cannot_drive_repair_planning() -> None:
    graph = _evidence_graph(criteria=_incomplete_criteria())
    graph.acceptance_criteria.criteria[0].__dict__["status"] = "satisfied"

    with pytest.raises(ValueError, match="modified"):
        plan_research_repairs(graph)


def test_post_validation_same_class_missing_reason_mutation_cannot_drive_repair_planning() -> None:
    graph = _evidence_graph(
        missing_reasons=(
            ResearchMissingEvidenceReason(
                reasonId="missing:source",
                subjectRefId="criteria:not-applicable",
                evidenceType="source_inspection",
                reasonCode="missing_source_proof",
            ),
        )
    )
    graph.missing_evidence_reasons[0].__dict__["reason_code"] = "not_required_for_criterion"

    with pytest.raises(ValueError, match="modified"):
        plan_research_repairs(graph)


def test_repair_module_has_no_live_tool_or_adk_runner_attachment() -> None:
    module = importlib.import_module("magi_agent.research.repair")
    source = inspect.getsource(module)

    assert "Runner(" not in source
    assert "FunctionTool(" not in source
    assert "google.adk" not in source
    assert "requests." not in source
    assert "httpx." not in source
