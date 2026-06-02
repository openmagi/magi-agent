from __future__ import annotations

from datetime import UTC, datetime

from magi_agent.evals.regression_gates import (
    EvalGateThresholds,
    RecipeEvalMetrics,
    evaluate_recipe_promotion_gate,
)
from magi_agent.evidence.claim_grounding import (
    AtomicClaim,
    CitationRef,
    validate_claim_projection_eligibility,
)
from magi_agent.harness.guardrail_matrix import GuardrailDefinition, GuardrailResult
from magi_agent.runtime.activity_boundary import ActivityRequest, ActivityStore, evaluate_activity_request
from magi_agent.runtime.checkpointing import (
    ExecutionCheckpoint,
    ForkedRunLineage,
    ReplayModeDecision,
    verify_resume_request,
)
from magi_agent.runtime.context_projection import build_context_projection
from magi_agent.telemetry.deterministic_events import DeterministicRuntimeEvent, project_event_for_dashboard
from magi_agent.workflows.compiler import (
    WorkflowCompileInput,
    compile_governed_workflow,
    validate_compiled_workflow,
)
from magi_agent.workflows.dry_run import dry_run_governed_workflow
from magi_agent.workflows.registry import WorkflowRegistryEntry


def test_governed_research_workflow_contract_chain_is_default_off_and_auditable() -> None:
    registry_entry = WorkflowRegistryEntry(
        workflowId="openmagi.research.cited-market-brief",
        version="1.0.0",
        ownerRef="team-digest:research",
        status="staging",
        sourceDigest="sha256:" + "8" * 64,
        promotionHistory=("draft:2026-05-22", "staging:2026-05-22"),
        compatibleRuntimeContractVersion="programmable-determinism.v1",
    )
    workflow = compile_governed_workflow(
        WorkflowCompileInput.model_validate(
            {
                "workflowId": "openmagi.research.cited-market-brief",
                "version": "1.0.0",
                "selectedRecipes": ("openmagi.research.cited-market-brief.v1.0.0",),
                "registeredWorkflows": (registry_entry,),
                "toolAllowlist": ("SourceOpen", "CitationVerify"),
                "toolDenylist": ("Bash", "FileWrite"),
                "evidenceRequirements": ("openedSourceSnapshot", "spanRef", "quoteDigest"),
                "validatorRefs": ("validator:sourceOpened@1", "validator:quoteExactMatch@1"),
                "projectionPolicy": "structured_claims_only",
                "repairPolicy": "bounded_terminal",
                "approvalPolicy": "readonly_no_external_write",
                "contextProjectionPolicy": "explicit",
                "budgets": {"maxIterations": 3, "wallClockTimeoutMs": 30000},
                "hardInvariants": {
                    "rawDraftStreamingForbidden": True,
                    "toolhostOnlyExecution": True,
                    "validatorBeforeProjection": True,
                },
                "effectivePolicySnapshotDigest": "sha256:" + "1" * 64,
                "availableTools": ("SourceOpen", "CitationVerify"),
                "availableValidators": ("validator:sourceOpened@1", "validator:quoteExactMatch@1"),
                "availableRenderers": ("structured_claims_only",),
                "evidenceProducers": ("openedSourceSnapshot", "spanRef", "quoteDigest"),
                "routePrecedence": ("research", "general"),
                "noMatchTerminalState": "ask_user",
            }
        )
    )
    assert workflow.traffic_attached is False
    assert workflow.execution_attached is False
    assert validate_compiled_workflow(workflow).ok is True

    dry_run = dry_run_governed_workflow(workflow)
    assert dry_run.model_call_attempted is False
    assert dry_run.tool_call_attempted is False
    assert dry_run.network_attempted is False
    assert dry_run.filesystem_attempted is False

    projection = build_context_projection(
        projectionId="ctx-integrated",
        mode="explicit",
        includedContextRefs=("source:snapshot-1",),
        excludedContextClasses=("raw_transcript", "hidden_reasoning"),
        sourceDigests=("sha256:" + "2" * 64,),
        tokenBudget=2048,
        byteBudget=8192,
        redactionStatus="redacted",
    )
    assert projection.model_visible_digest.startswith("sha256:")

    checkpoint = ExecutionCheckpoint(
        runId="run-integrated",
        checkpointId="checkpoint-integrated",
        stepId="step-1",
        workflowVersion="1.0.0",
        stateDigest="sha256:" + "3" * 64,
        ledgerHeadDigest="sha256:" + "4" * 64,
        effectivePolicySnapshotDigest="sha256:" + "1" * 64,
        contextProjectionDigest=projection.model_visible_digest,
        pendingApprovalRefs=(),
        resumable=True,
        createdAt=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
    )
    assert (
        verify_resume_request(
            checkpoint,
            ledgerHeadDigest="sha256:" + "4" * 64,
            effectivePolicySnapshotDigest="sha256:" + "1" * 64,
            effectivePolicySnapshotAvailable=True,
            authorityScopeWouldExpand=False,
            pendingApprovalExpired=False,
            requiredEvidenceMissing=False,
        ).ok
        is True
    )

    activity = ActivityRequest(
        activityId="activity-integrated",
        kind="web_fetch",
        targetSystemRef="source:allowlisted",
        actionDigest="sha256:" + "5" * 64,
        sideEffecting=False,
        idempotencyKey=None,
        approvalReceiptDigest=None,
        timeoutMs=5000,
        retryPolicy="none",
        compensationPolicyRef=None,
        reversible=True,
    )
    assert evaluate_activity_request(activity, ActivityStore()).ok is True

    side_effect_store = ActivityStore()
    side_effect_activity = ActivityRequest(
        activityId="activity-side-effect-integrated",
        kind="channel_delivery",
        targetSystemRef="delivery:test-sink",
        actionDigest="sha256:" + "9" * 64,
        sideEffecting=True,
        idempotencyKey="idem-integrated-delivery",
        approvalReceiptDigest=None,
        timeoutMs=5000,
        retryPolicy="idempotent_retry",
        compensationPolicyRef="compensate:delivery-rollback",
        reversible=True,
    )
    first_side_effect = evaluate_activity_request(side_effect_activity, side_effect_store)
    second_side_effect = evaluate_activity_request(side_effect_activity, side_effect_store)
    assert first_side_effect.ok is True
    assert first_side_effect.status == "accepted"
    assert first_side_effect.receipt_digest is not None
    assert second_side_effect.ok is True
    assert second_side_effect.status == "deduped_existing_success"
    assert second_side_effect.receipt_digest == first_side_effect.receipt_digest

    replay = ReplayModeDecision(mode="replay", allowSideEffects=False, appendReplayObservation=True)
    fork = ForkedRunLineage(
        parentRunId=checkpoint.run_id,
        parentCheckpointId=checkpoint.checkpoint_id,
        parentLedgerHeadDigest=checkpoint.ledger_head_digest,
        forkReason="policy-dry-run",
        newRunId="run-integrated-fork",
        newEffectivePolicySnapshotDigest=checkpoint.effective_policy_snapshot_digest,
    )
    assert replay.allow_side_effects is False
    assert replay.append_replay_observation is True
    assert fork.parent_ledger_head_digest == checkpoint.ledger_head_digest

    guardrail = GuardrailDefinition(
        guardrailId="guardrail-before-output",
        stage="before_output_projection",
        failureMode="block",
        hardInvariant=True,
        validatorTrustClass="deterministic",
    )
    assert guardrail.stage == "before_output_projection"
    guardrail_result = GuardrailResult(
        guardrailId=guardrail.guardrail_id,
        stage=guardrail.stage,
        status="failed",
        reasonCodes=("governed_projection_blocked",),
        evidenceRefs=("evidence:quote-digest",),
        policyDecisionId="policy-decision-integrated",
        validatorTrustClass=guardrail.validator_trust_class,
        recommendedTransition="block",
        redactionStatus="redacted",
    )
    assert guardrail_result.recommended_transition == "block"
    assert guardrail_result.redaction_status == "redacted"

    citation = CitationRef(
        sourceRef="source-1",
        snapshotRef="snapshot-1",
        contentDigest="sha256:" + "6" * 64,
        spanRef="source-1-span-1",
        quoteDigest="sha256:" + "7" * 64,
        openedProof=True,
        fetchedAt="2026-05-22T12:00:00Z",
        sourceDate="2026-05-22",
    )
    claim = AtomicClaim(
        claimId="claim-integrated",
        text="Revenue grew 18 percent in 2025.",
        claimType="numeric_date",
        supportStatus="supported",
        citationRefs=(citation,),
    )
    assert validate_claim_projection_eligibility((claim,)).ok is True

    thresholds = EvalGateThresholds(
        minSelectorAccuracy=0.95,
        maxUnsupportedClaimRate=0.01,
        maxApprovalBypassCount=0,
        rawProjectionMustFail=True,
        pluginSandboxOverreachMustFail=True,
    )
    metrics = RecipeEvalMetrics(
        recipeId="openmagi.research.cited-market-brief",
        selectorAccuracy=0.99,
        unsupportedClaimRate=0.0,
        approvalBypassCount=0,
        rawProjectionFixturePassed=False,
        pluginSandboxOverreachFixturePassed=False,
    )
    assert evaluate_recipe_promotion_gate(metrics, thresholds).ok is True

    event = DeterministicRuntimeEvent(
        eventId="evt-integrated",
        runId="run-integrated",
        workflowId="openmagi.research.cited-market-brief",
        stepId="step-1",
        eventType="guardrail_result",
        routeDecision=guardrail_result.recommended_transition,
        effectivePolicySnapshotDigest="sha256:" + "1" * 64,
        ledgerHeadDigest="sha256:" + "4" * 64,
        checkpointId="checkpoint-integrated",
        validatorStatuses=(
            "validator:quoteExactMatch=pass",
            f"guardrail:{guardrail_result.status}",
            f"transition:{guardrail_result.recommended_transition}",
        ),
        approvalGateRefs=guardrail_result.evidence_refs,
        repairAttempt=0,
        projectionMode="structured_claims_only",
        terminalState=None,
        redactionStatus=guardrail_result.redaction_status,
    )
    projected_event = project_event_for_dashboard(event)
    assert event.activation_enabled is False
    assert projected_event["activationEnabled"] is False
    assert "metadata" not in projected_event
