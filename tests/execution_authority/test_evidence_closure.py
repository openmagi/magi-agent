from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest
from pydantic import ValidationError

from magi_agent.execution_authority.contracts import (
    Requirement,
    ResearchClaimRequirement,
    ResearchProofObligation,
    TaskContractSnapshot,
    canonical_task_contract_digest,
)
from magi_agent.execution_authority.envelopes import (
    ActionResolution,
    ActionSnapshot,
    AttemptSnapshot,
    BackendObservation,
    CompletionVerdict,
    CoverageDescriptor,
    DependencyHealth,
    EvidenceEdge,
    EvidenceNode,
    FinalizationRequest,
    FreshnessBinding,
    ProjectionCursorBinding,
    RequirementResult,
    ResearchClaimResult,
    ResearchSourceBinding,
    RequiredProjection,
    ResponseClaim,
    ResponseClaimManifest,
    VerificationEvidenceBinding,
    canonical_evidence_node_digest,
    canonical_required_projections_digest,
)
from magi_agent.execution_authority.evidence_closure import (
    CompletionActionBinding,
    CompletionDependencyBinding,
    CompletionEvaluationBinding,
    EvidenceAttribution,
    EvidenceClosureBinding,
    EvidenceJournalAnchor,
    EvidenceProjectionSnapshot,
    RequirementEvidenceBinding,
    ResearchClaimEvidenceBinding,
    ResponseClaimEvidenceBinding,
    canonical_evidence_projection_root,
)
from magi_agent.execution_authority.state_machine import (
    ActionState,
    CompletionStatus,
    DependencyStatus,
    EvidenceSemanticClass,
    RequirementState,
)


NOW = datetime(2026, 7, 15, 4, 0, tzinfo=UTC)


def _digest(label: str) -> str:
    return "sha256:" + sha256(label.encode()).hexdigest()


def _research(
    *,
    primary_source_rule: str = "required",
    query_classes: tuple[str, ...] = (
        "official_primary",
        "reputable_secondary",
        "exploratory",
    ),
    limited_snippet_allowance: str = "discovery_only",
) -> ResearchProofObligation:
    return ResearchProofObligation(
        claims=(
            ResearchClaimRequirement(
                claimId="claim_alpha",
                claimClass="factual",
                proposition="Alpha is current.",
                freshness="same_retrieval_window",
            ),
            ResearchClaimRequirement(
                claimId="claim_beta",
                claimClass="factual",
                proposition="Beta is current.",
                freshness="same_retrieval_window",
            ),
        ),
        queryClasses=query_classes,
        primarySourceRule=primary_source_rule,
        conflictHandling="resolve_or_disclose",
        stoppingRules=("claim_coverage_met", "conflicts_resolved_or_disclosed"),
        limitedSnippetAllowance=limited_snippet_allowance,
    )


def _task(
    *,
    primary_source_rule: str = "required",
    query_classes: tuple[str, ...] = (
        "official_primary",
        "reputable_secondary",
        "exploratory",
    ),
    limited_snippet_allowance: str = "discovery_only",
) -> TaskContractSnapshot:
    return TaskContractSnapshot(
        taskContractId="task_01",
        version=1,
        completionEpochId="epoch_01",
        sourceMessageDigests=(_digest("message"),),
        intent="Verify Alpha and Beta.",
        inclusions=("verified answer",),
        exclusions=(),
        constraints=(),
        assumptions=(),
        dependencies=(),
        acceptableBlockedBehavior="report blocked",
        acceptableUnavailableBehavior="report unavailable",
        requirements=(
            Requirement(
                requirementId="req_research",
                text="Verify both research claims.",
                state=RequirementState.PENDING,
                proof={
                    "evidenceKinds": ("source_snapshot", "entailment_verdict"),
                    "freshness": "same_state_root",
                    "research": _research(
                        primary_source_rule=primary_source_rule,
                        query_classes=query_classes,
                        limited_snippet_allowance=limited_snippet_allowance,
                    ),
                },
            ),
        ),
    )


def _research_source(
    claim_id: str,
    *,
    source_class: str = "primary",
    trust_tier: str = "official",
    truncated: bool = False,
) -> ResearchSourceBinding:
    return ResearchSourceBinding(
        sourceSnapshotId=f"snapshot_{claim_id}",
        sourceSnapshotDigest=_digest(f"snapshot:{claim_id}"),
        sourceClass=source_class,
        trustTier=trust_tier,
        retrievedAt=NOW,
        sourceVersion="2026-07-15",
        truncated=truncated,
    )


def _coverage() -> CoverageDescriptor:
    return CoverageDescriptor(
        coverageKind="source_set",
        journalWindow=None,
        searchedResourceRefs=("https://example.test/research",),
    )


def _node(
    *,
    task: TaskContractSnapshot,
    evidence_id: str,
    kind: str,
    semantic_class: str,
    journal_sequence: int,
    partition_id: str,
    requirement_ids: tuple[str, ...] = (),
    claim_ids: tuple[str, ...] = (),
    action_id: str | None = None,
    attempt_id: str | None = None,
    parent_evidence_ids: tuple[str, ...] = (),
    research_source: ResearchSourceBinding | None = None,
    freshness_rule: str = "same_state_root",
    reason_codes: tuple[str, ...] = ("recorded",),
) -> EvidenceNode:
    digest = canonical_task_contract_digest(task)
    source_snapshot_id = research_source.source_snapshot_id if research_source is not None else None
    source_snapshot_digest = (
        research_source.source_snapshot_digest if research_source is not None else None
    )
    return EvidenceNode(
        evidenceId=evidence_id,
        kind=kind,
        semanticClass=semantic_class,
        sessionId="session_01",
        turnId="turn_01",
        runId="run_01",
        taskContractId=task.task_contract_id,
        taskVersion=task.version,
        taskContractDigest=digest,
        completionEpochId=task.completion_epoch_id,
        requirementIds=requirement_ids,
        claimIds=claim_ids,
        actionId=action_id,
        attemptId=attempt_id,
        requestDigest=_digest("request") if action_id else None,
        authorityDigest=_digest("authority") if action_id else None,
        policyDigest=_digest("policy"),
        producerId="research-fetch" if claim_ids else "workspace-verifier",
        producerVersion="1.0.0",
        producerAlive=True,
        producerStatus=DependencyStatus.CLEAN,
        producerSchemaVersion="1",
        producerInvocationEvidenceId=f"invoke_{evidence_id}",
        producerInvocationEvidenceDigest=_digest(f"invoke:{evidence_id}"),
        partitionId=partition_id,
        admissionSequence=min(journal_sequence, 10),
        workspaceGeneration=None,
        stateRoot=_digest("state-root"),
        sourceSnapshotId=source_snapshot_id,
        sourceSnapshotDigest=source_snapshot_digest,
        sourceSpans=(),
        researchSource=research_source,
        contentDigest=_digest(f"content:{evidence_id}"),
        toolInputDigest=_digest(f"input:{evidence_id}"),
        toolOutputDigest=_digest(f"output:{evidence_id}"),
        parentEvidenceIds=parent_evidence_ids,
        coverage=_coverage(),
        freshness=FreshnessBinding(
            rule=freshness_rule,
            stateRoot=_digest("state-root") if freshness_rule == "same_state_root" else None,
            observedAt=NOW,
        ),
        publicRedactionClass="public",
        reasonCodes=reason_codes,
        createdAt=NOW,
        producerPayloadDigest=_digest(f"payload:{evidence_id}"),
        journalSequence=journal_sequence,
        journalEventHash=_digest(f"event:{partition_id}:{journal_sequence}"),
    )


def _evidence_graph(
    task: TaskContractSnapshot,
    *,
    source_class: str = "primary",
    trust_tier: str = "official",
    truncated: bool = False,
    source_freshness: str = "same_retrieval_window",
    add_conflict: bool = False,
) -> tuple[tuple[EvidenceNode, ...], tuple[EvidenceEdge, ...]]:
    nodes: list[EvidenceNode] = []
    edges: list[EvidenceEdge] = []
    for index, claim_id in enumerate(("claim_alpha", "claim_beta"), start=1):
        source_id = f"evidence_source_{claim_id}"
        entailment_id = f"evidence_entail_{claim_id}"
        source = _research_source(
            claim_id,
            source_class=source_class,
            trust_tier=trust_tier,
            truncated=truncated,
        )
        nodes.append(
            _node(
                task=task,
                evidence_id=source_id,
                kind="source_snapshot",
                semantic_class=EvidenceSemanticClass.OBSERVATION,
                journal_sequence=index * 2 - 1,
                partition_id="research_01",
                requirement_ids=("req_research",),
                claim_ids=(claim_id,),
                research_source=source,
                freshness_rule=source_freshness,
                reason_codes=("retrieved",),
            )
        )
        nodes.append(
            _node(
                task=task,
                evidence_id=entailment_id,
                kind="entailment_verdict",
                semantic_class=EvidenceSemanticClass.VERDICT,
                journal_sequence=index * 2,
                partition_id="research_01",
                requirement_ids=("req_research",),
                claim_ids=(claim_id,),
                parent_evidence_ids=(source_id,),
                research_source=source,
                freshness_rule="same_state_root",
                reason_codes=("entailed",),
            )
        )
        edges.append(
            EvidenceEdge(
                edgeId=f"edge_support_{claim_id}",
                sourceEvidenceId=source_id,
                targetEvidenceId=entailment_id,
                kind="supports",
            )
        )
    nodes.append(
        _node(
            task=task,
            evidence_id="evidence_action_verified",
            kind="postcondition_verdict",
            semantic_class=EvidenceSemanticClass.VERDICT,
            journal_sequence=1,
            partition_id="workspace_01",
            action_id="act_01",
            attempt_id="try_01",
            freshness_rule="same_state_root",
            reason_codes=("verified",),
        )
    )
    if add_conflict:
        edges.append(
            EvidenceEdge(
                edgeId="edge_conflict_alpha",
                sourceEvidenceId="evidence_source_claim_alpha",
                targetEvidenceId="evidence_entail_claim_alpha",
                kind="contradicts",
            )
        )
    return tuple(sorted(nodes, key=lambda node: node.evidence_id)), tuple(
        sorted(edges, key=lambda edge: edge.edge_id)
    )


def _projection(
    task: TaskContractSnapshot,
    *,
    nodes: tuple[EvidenceNode, ...] | None = None,
    edges: tuple[EvidenceEdge, ...] | None = None,
) -> EvidenceProjectionSnapshot:
    if nodes is None or edges is None:
        nodes, edges = _evidence_graph(task)
    anchors = tuple(
        EvidenceJournalAnchor(
            evidenceId=node.evidence_id,
            partitionId=node.partition_id,
            journalSequence=node.journal_sequence,
            journalEventHash=node.journal_event_hash,
        )
        for node in nodes
    )
    digest = canonical_task_contract_digest(task)
    return EvidenceProjectionSnapshot(
        taskContractId=task.task_contract_id,
        taskVersion=task.version,
        taskContractDigest=digest,
        taskContractSnapshotRef=f"authority-task://{digest}",
        taskPartitionId=f"task:{task.task_contract_id}:{task.version}",
        completionEpochId=task.completion_epoch_id,
        stateRoot=_digest("state-root"),
        barrierAdmissionSequence=10,
        projectionId="evidence",
        projectionCompareVersion=7,
        nodes=nodes,
        edges=edges,
        sourceJournalAnchors=anchors,
    )


def _claim_binding(claim: ResearchClaimRequirement) -> ResearchClaimEvidenceBinding:
    return ResearchClaimEvidenceBinding(
        claimId=claim.claim_id,
        propositionDigest=claim.proposition_digest,
        state="satisfied",
        proofEvidenceIds=(
            f"evidence_entail_{claim.claim_id}",
            f"evidence_source_{claim.claim_id}",
        ),
        discoveryEvidenceIds=(),
        conflictDisposition="none",
        resolutionEvidenceIds=(),
        disclosureEvidenceIds=(),
        stoppingRulesSatisfied=(
            "claim_coverage_met",
            "conflicts_resolved_or_disclosed",
        ),
        stoppingEvidenceIds=(),
        snippetAcceptanceEvidenceIds=(),
    )


def _closure(
    task: TaskContractSnapshot,
    projection: EvidenceProjectionSnapshot,
) -> EvidenceClosureBinding:
    research = task.requirements[0].proof.research
    assert research is not None
    proof_ids = tuple(
        sorted(
            evidence_id
            for claim in research.claims
            for evidence_id in (
                f"evidence_entail_{claim.claim_id}",
                f"evidence_source_{claim.claim_id}",
            )
        )
    )
    return EvidenceClosureBinding(
        taskContract=task,
        taskContractDigest=canonical_task_contract_digest(task),
        projection=projection,
        requirements=(
            RequirementEvidenceBinding(
                requirementId="req_research",
                state=RequirementState.SATISFIED,
                proofEvidenceIds=proof_ids,
                researchClaims=tuple(_claim_binding(claim) for claim in research.claims),
            ),
        ),
    )


def _observation(task: TaskContractSnapshot) -> BackendObservation:
    return BackendObservation(
        actionId="act_01",
        attemptId="try_01",
        partitionId="workspace_01",
        taskContractDigest=canonical_task_contract_digest(task),
        actionIntentDigest=_digest("intent"),
        requestDigest=_digest("request"),
        authorityDigest=_digest("authority"),
        fencingToken=7,
        executorId="workspace-executor",
        executorVersion="1.0.0",
        sandboxProfileDigest=_digest("sandbox"),
        providerId=None,
        providerVersion=None,
        providerCapabilitiesDigest=None,
        attemptKind="execution",
        sourceAttemptId=None,
        reconcilesAttemptId=None,
        effectMayHaveStarted=True,
        observedOutcome="committed",
        transmissionState="proven_not_sent",
        providerRequestIdDigest=None,
        observedEffectRefs=(f"workspace://{_digest('workspace')}/result.txt",),
        reasonCodes=("committed",),
        processExitCode=0,
        stdoutDigest=_digest("stdout"),
        stderrDigest=_digest("stderr"),
        outputTruncated=False,
        privateWorkspaceDiffDigest=_digest("diff"),
        workspacePublicationDigest=_digest("publication"),
        providerReceiptDigest=None,
    )


def _action_binding(
    task: TaskContractSnapshot,
    projection: EvidenceProjectionSnapshot,
) -> CompletionActionBinding:
    action_node = next(
        node for node in projection.nodes if node.evidence_id == "evidence_action_verified"
    )
    cursor = ProjectionCursorBinding(
        partitionId="workspace_01",
        projectionId="workspace",
        requiredSequence=1,
        requiredEventHash=action_node.journal_event_hash,
        acknowledgedSequence=1,
        acknowledgedEventHash=action_node.journal_event_hash,
        stateRoot=projection.state_root,
        compareVersion=1,
    )
    verification = VerificationEvidenceBinding(
        evidenceId=action_node.evidence_id,
        evidenceDigest=canonical_evidence_node_digest(action_node),
        verificationOutcome="passed",
        sourcePartitionId="workspace_01",
        sourceEventId="event_action_observed",
        sourceEventSequence=1,
        sourceEventHash=action_node.journal_event_hash,
        sourceHeadSequence=1,
        sourceHeadHash=action_node.journal_event_hash,
        sourceHeadCompareVersion=1,
        projectionCursors=(cursor,),
        actionId="act_01",
        attemptId="try_01",
        taskContractDigest=canonical_task_contract_digest(task),
        requestDigest=_digest("request"),
        verifiedStateRoot=projection.state_root,
    )
    attempt = AttemptSnapshot(
        actionId="act_01",
        attemptId="try_01",
        partitionId="workspace_01",
        taskContractDigest=canonical_task_contract_digest(task),
        actionIntentDigest=_digest("intent"),
        requestDigest=_digest("request"),
        state=ActionState.VERIFIED,
        authorityDigest=_digest("authority"),
        fencingToken=7,
        observation=_observation(task),
        verification=verification,
        compareVersion=5,
    )
    action = ActionSnapshot(
        actionId="act_01",
        partitionId="workspace_01",
        taskContractDigest=canonical_task_contract_digest(task),
        completionEpochId=task.completion_epoch_id,
        admissionSequence=1,
        intentDigest=_digest("intent"),
        resolution=ActionResolution(
            actionId="act_01",
            taskContractDigest=canonical_task_contract_digest(task),
            sourceAttemptIds=("try_01",),
            resolutionAttemptId=None,
            logicalState=ActionState.VERIFIED,
            reasonCodes=("verified",),
        ),
        compareVersion=4,
    )
    return CompletionActionBinding(
        actionSnapshot=action,
        attemptSnapshot=attempt,
        verificationMode="snapshot_verified",
        terminalVerificationEvidenceIds=("evidence_action_verified",),
    )


def _request(
    task: TaskContractSnapshot, projection: EvidenceProjectionSnapshot
) -> FinalizationRequest:
    candidate = "Alpha verified."
    candidate_bytes = candidate.encode()
    response_evidence = "evidence_entail_claim_alpha"
    manifest = ResponseClaimManifest(
        candidateResponseDigest="sha256:" + sha256(candidate_bytes).hexdigest(),
        segments=(
            ResponseClaim(
                claimId="response_claim_alpha",
                claimClass="factual",
                textDigest="sha256:" + sha256(candidate_bytes).hexdigest(),
                codepointStart=0,
                codepointEnd=len(candidate),
                utf8Start=0,
                utf8End=len(candidate_bytes),
                evidenceIds=(response_evidence,),
            ),
        ),
    )
    digest = canonical_task_contract_digest(task)
    return FinalizationRequest(
        finalizationId="final_01",
        taskContract=task,
        taskContractDigest=digest,
        taskContractSnapshotRef=f"authority-task://{digest}",
        taskPartitionId=f"task:{task.task_contract_id}:{task.version}",
        stateRoot=projection.state_root,
        evidenceRoot=projection.evidence_root,
        completionEpochId=task.completion_epoch_id,
        barrierAdmissionSequence=projection.barrier_admission_sequence,
        dependencyHealth=(),
        candidateResponse=candidate,
        claimManifest=manifest,
    )


def _verdict(
    request: FinalizationRequest,
    closure: EvidenceClosureBinding,
) -> CompletionVerdict:
    required = (
        RequiredProjection(
            partitionId=request.task_partition_id,
            projectionId="task",
        ),
    )
    cursor = ProjectionCursorBinding(
        partitionId=request.task_partition_id,
        projectionId="task",
        requiredSequence=request.barrier_admission_sequence,
        requiredEventHash=_digest("barrier"),
        acknowledgedSequence=request.barrier_admission_sequence,
        acknowledgedEventHash=_digest("barrier"),
        stateRoot=request.state_root,
        compareVersion=2,
    )
    requirement = closure.requirements[0]
    return CompletionVerdict(
        completionId="completion_01",
        finalizationId=request.finalization_id,
        finalizationRequestDigest=request.finalization_request_digest,
        responseClaimManifestDigest=request.response_claim_manifest_digest,
        status=CompletionStatus.COMPLETE,
        taskContractId=request.task_contract.task_contract_id,
        taskVersion=request.task_contract.version,
        taskContractDigest=request.task_contract_digest,
        taskContractSnapshotRef=request.task_contract_snapshot_ref,
        taskPartitionId=request.task_partition_id,
        completionEpochId=request.completion_epoch_id,
        stateRoot=request.state_root,
        evidenceRoot=request.evidence_root,
        barrierAdmissionSequence=request.barrier_admission_sequence,
        requiredProjectionDigest=canonical_required_projections_digest(required),
        projectionCursors=(cursor,),
        requirements=(
            RequirementResult(
                requirementId=requirement.requirement_id,
                state=requirement.state,
                evidenceIds=requirement.proof_evidence_ids,
                researchClaims=tuple(
                    ResearchClaimResult(
                        claimId=claim.claim_id,
                        propositionDigest=claim.proposition_digest,
                        state=claim.state,
                        evidenceIds=claim.proof_evidence_ids,
                        reasonCodes=("verified",),
                    )
                    for claim in requirement.research_claims
                ),
                reasonCodes=("verified",),
            ),
        ),
        includedActionIds=("act_01",),
        responseDigest=request.claim_manifest.candidate_response_digest,
        reasonCodes=("verified",),
    )


def _evaluation() -> CompletionEvaluationBinding:
    task = _task()
    projection = _projection(task)
    closure = _closure(task, projection)
    request = _request(task, projection)
    verdict = _verdict(request, closure)
    return CompletionEvaluationBinding(
        request=request,
        evidenceClosure=closure,
        actionBindings=(_action_binding(task, projection),),
        dependencySnapshots=(),
        responseClaimBindings=(
            ResponseClaimEvidenceBinding(
                responseClaimId="response_claim_alpha",
                attributions=(
                    EvidenceAttribution(
                        evidenceId="evidence_entail_claim_alpha",
                        requirementId="req_research",
                        researchClaimId="claim_alpha",
                    ),
                ),
            ),
        ),
        verdict=verdict,
    )


def test_projection_derives_root_from_exact_nodes_edges_and_journal_anchors() -> None:
    task = _task()
    projection = _projection(task)

    assert projection.evidence_root == canonical_evidence_projection_root(projection)

    payload = projection.model_dump(by_alias=True, mode="json")
    payload["evidenceRoot"] = _digest("fabricated-root")
    with pytest.raises(ValidationError, match="evidenceRoot"):
        EvidenceProjectionSnapshot.model_validate(payload)

    payload = projection.model_dump(by_alias=True, mode="json")
    payload["sourceJournalAnchors"][0]["journalEventHash"] = _digest("wrong-anchor")
    with pytest.raises(ValidationError, match="sourceJournalAnchors"):
        EvidenceProjectionSnapshot.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("taskContractId", "task_other", "task"),
        ("completionEpochId", "epoch_other", "completionEpochId"),
        ("stateRoot", _digest("other-root"), "stateRoot"),
        ("admissionSequence", 11, "barrier"),
    ),
)
def test_projection_rejects_cross_coordinate_evidence(
    field: str,
    value: object,
    message: str,
) -> None:
    task = _task()
    nodes, edges = _evidence_graph(task)
    node_payload = nodes[0].model_dump(by_alias=True, mode="json")
    node_payload[field] = value
    if field == "stateRoot":
        node_payload["freshness"]["stateRoot"] = value
        node_payload["freshness"].pop("freshnessDigest")
    mutated = (EvidenceNode.model_validate(node_payload), *nodes[1:])

    with pytest.raises(ValidationError, match=message):
        _projection(
            task, nodes=tuple(sorted(mutated, key=lambda node: node.evidence_id)), edges=edges
        )


def test_projection_rejects_dangling_or_unanchored_edges() -> None:
    task = _task()
    nodes, edges = _evidence_graph(task)
    dangling = EvidenceEdge(
        edgeId="edge_dangling",
        sourceEvidenceId="evidence_missing",
        targetEvidenceId=nodes[0].evidence_id,
        kind="supports",
    )
    with pytest.raises(ValidationError, match="endpoint"):
        _projection(
            task, nodes=nodes, edges=tuple(sorted((*edges, dangling), key=lambda e: e.edge_id))
        )


def test_closure_rejects_fabricated_ids_missing_kinds_and_missing_claims() -> None:
    task = _task()
    projection = _projection(task)
    closure = _closure(task, projection)
    payload = closure.model_dump(by_alias=True, mode="json")

    fabricated = {**payload}
    fabricated["requirements"][0]["proofEvidenceIds"].append("evidence_fabricated")
    fabricated["requirements"][0]["proofEvidenceIds"].sort()
    with pytest.raises(ValidationError, match="does not exist"):
        EvidenceClosureBinding.model_validate(fabricated)

    missing_kind = closure.model_dump(by_alias=True, mode="json")
    missing_kind["requirements"][0]["proofEvidenceIds"] = [
        evidence_id
        for evidence_id in missing_kind["requirements"][0]["proofEvidenceIds"]
        if "entail" not in evidence_id
    ]
    with pytest.raises(ValidationError, match="evidenceKinds"):
        EvidenceClosureBinding.model_validate(missing_kind)

    missing_claim = closure.model_dump(by_alias=True, mode="json")
    missing_claim["requirements"][0]["researchClaims"].pop()
    with pytest.raises(ValidationError, match="research claims"):
        EvidenceClosureBinding.model_validate(missing_claim)


def test_closure_enforces_research_source_class_freshness_and_primary_rules() -> None:
    source_task = _task(query_classes=("reputable_secondary",))
    source_projection = _projection(source_task)
    with pytest.raises(ValidationError, match="queryClasses"):
        _closure(source_task, source_projection)

    freshness_task = _task()
    nodes, edges = _evidence_graph(freshness_task, source_freshness="same_state_root")
    freshness_projection = _projection(freshness_task, nodes=nodes, edges=edges)
    with pytest.raises(ValidationError, match="freshness"):
        _closure(freshness_task, freshness_projection)

    primary_task = _task(
        query_classes=("reputable_secondary",),
        primary_source_rule="required",
    )
    nodes, edges = _evidence_graph(
        primary_task,
        source_class="secondary",
        trust_tier="reputable_secondary",
    )
    primary_projection = _projection(primary_task, nodes=nodes, edges=edges)
    with pytest.raises(ValidationError, match="primary"):
        _closure(primary_task, primary_projection)


def test_closure_enforces_conflict_stopping_and_snippet_obligations() -> None:
    conflict_task = _task()
    nodes, edges = _evidence_graph(conflict_task, add_conflict=True)
    conflict_projection = _projection(conflict_task, nodes=nodes, edges=edges)
    with pytest.raises(ValidationError, match="conflictDisposition"):
        _closure(conflict_task, conflict_projection)

    task = _task()
    closure = _closure(task, _projection(task))
    missing_stopping = closure.model_dump(by_alias=True, mode="json")
    missing_stopping["requirements"][0]["researchClaims"][0]["stoppingRulesSatisfied"] = [
        "claim_coverage_met"
    ]
    with pytest.raises(ValidationError, match="stoppingRules"):
        EvidenceClosureBinding.model_validate(missing_stopping)

    snippet_task = _task(
        primary_source_rule="not_required",
        query_classes=("exploratory",),
        limited_snippet_allowance="discovery_only",
    )
    nodes, edges = _evidence_graph(
        snippet_task,
        source_class="snippet",
        trust_tier="untrusted_snippet",
        truncated=True,
    )
    snippet_projection = _projection(snippet_task, nodes=nodes, edges=edges)
    with pytest.raises(ValidationError, match="discovery_only"):
        _closure(snippet_task, snippet_projection)


def test_action_binding_embeds_exact_action_attempt_and_verified_evidence() -> None:
    task = _task()
    projection = _projection(task)
    binding = _action_binding(task, projection)
    assert binding.binding_digest is not None

    mismatched = binding.model_dump(by_alias=True, mode="json")
    mismatched["actionSnapshot"]["actionId"] = "act_fabricated"
    mismatched["actionSnapshot"]["resolution"]["actionId"] = "act_fabricated"
    mismatched.pop("bindingDigest")
    with pytest.raises(ValidationError, match="action"):
        CompletionActionBinding.model_validate(mismatched)

    missing_evidence = binding.model_dump(by_alias=True, mode="json")
    missing_evidence["terminalVerificationEvidenceIds"] = []
    with pytest.raises(ValidationError, match="verification evidence"):
        CompletionActionBinding.model_validate(missing_evidence)


def test_evaluation_binds_request_closure_actions_dependencies_and_verdict() -> None:
    evaluation = _evaluation()
    assert evaluation.evaluation_digest is not None

    fabricated_action = evaluation.model_dump(by_alias=True, mode="json")
    fabricated_action["verdict"]["includedActionIds"] = ["act_fabricated"]
    fabricated_action["verdict"].pop("verdictDigest")
    with pytest.raises(ValidationError, match="includedActionIds"):
        CompletionEvaluationBinding.model_validate(fabricated_action)

    fabricated_evidence = evaluation.model_dump(by_alias=True, mode="json")
    fabricated_evidence["verdict"]["requirements"][0]["evidenceIds"].append("evidence_fabricated")
    fabricated_evidence["verdict"].pop("verdictDigest")
    with pytest.raises(ValidationError, match="requirement evidence"):
        CompletionEvaluationBinding.model_validate(fabricated_evidence)

    dependency = DependencyHealth(
        dependencyId="dep_fabricated",
        status=DependencyStatus.UNAVAILABLE,
        producerVersion=None,
        schemaVersion=None,
        producerAlive=False,
        invocationEvidenceId=None,
        invocationEvidenceDigest=None,
        taskContractDigest=evaluation.request.task_contract_digest,
        completionEpochId=evaluation.request.completion_epoch_id,
        stateRoot=evaluation.request.state_root,
        observedAt=NOW,
        reasonCodes=("unavailable",),
    )
    fabricated_dependency = evaluation.model_dump(by_alias=True, mode="json")
    fabricated_dependency["dependencySnapshots"] = [
        CompletionDependencyBinding(
            health=dependency,
            sourceEvidenceId="evidence_fabricated",
        ).model_dump(by_alias=True, mode="json")
    ]
    with pytest.raises(ValidationError, match="dependencySnapshots"):
        CompletionEvaluationBinding.model_validate(fabricated_dependency)


def test_evaluation_rejects_global_evidence_laundering_across_claims() -> None:
    evaluation = _evaluation()
    payload = evaluation.model_dump(by_alias=True, mode="json")
    payload["responseClaimBindings"][0]["attributions"][0]["researchClaimId"] = "claim_beta"

    with pytest.raises(ValidationError, match="claim_beta"):
        CompletionEvaluationBinding.model_validate(payload)

    payload = evaluation.model_dump(by_alias=True, mode="json")
    alpha_result = payload["verdict"]["requirements"][0]["researchClaims"][0]
    beta_result = payload["verdict"]["requirements"][0]["researchClaims"][1]
    beta_result["evidenceIds"] = list(alpha_result["evidenceIds"])
    payload["verdict"].pop("verdictDigest")
    with pytest.raises(ValidationError, match="claim_beta evidence"):
        CompletionEvaluationBinding.model_validate(payload)
