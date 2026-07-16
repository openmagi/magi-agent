from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest
from pydantic import ValidationError

from magi_agent.execution_authority.contracts import (
    Requirement,
    TaskContractBinding,
    TaskContractSnapshot,
    canonical_task_contract_digest,
)
from magi_agent.execution_authority.envelopes import (
    EpochSeal,
    EpochSnapshot,
    FinalizationRequest,
    JournalChainLink,
    RequiredProjection,
    ResponseClaim,
    ResponseClaimManifest,
    canonical_required_projections_digest,
)
from magi_agent.execution_authority.projection_registry import (
    MAX_PROJECTION_ANCESTRY_LINKS,
    EpochSealBinding,
    FinalizationEvaluationBinding,
    ProjectionCursorProof,
    ProjectionRegistryEntry,
    ProjectionRegistrySnapshot,
    RequiredProjectionCheckpoint,
)
from magi_agent.execution_authority.state_machine import (
    CompletionEpochState,
    RequirementState,
)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


D0 = _digest("0")
D1 = _digest("1")
D2 = _digest("2")
D3 = _digest("3")
D4 = _digest("4")
D5 = _digest("5")
D6 = _digest("6")
D7 = _digest("7")
D8 = _digest("8")
D9 = _digest("9")
NOW = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)


def _task() -> TaskContractSnapshot:
    return TaskContractSnapshot(
        taskContractId="task_01",
        version=1,
        completionEpochId="epoch_01",
        sourceMessageDigests=(D0,),
        intent="return only a verified result",
        inclusions=("verified result",),
        exclusions=(),
        constraints=(),
        assumptions=(),
        dependencies=(),
        acceptableBlockedBehavior="report blocked",
        acceptableUnavailableBehavior="report unavailable",
        requirements=(
            Requirement(
                requirementId="req_01",
                text="result is verified",
                state=RequirementState.PENDING,
                proof={"evidenceKinds": ("requirement_verdict",), "freshness": "current"},
            ),
        ),
    )


def _request() -> FinalizationRequest:
    task = _task()
    task_digest = canonical_task_contract_digest(task)
    candidate = "verified"
    candidate_bytes = candidate.encode("utf-8")
    manifest = ResponseClaimManifest(
        candidateResponseDigest="sha256:" + sha256(candidate_bytes).hexdigest(),
        segments=(
            ResponseClaim(
                claimId="claim_01",
                claimClass="result",
                textDigest="sha256:" + sha256(candidate_bytes).hexdigest(),
                codepointStart=0,
                codepointEnd=len(candidate),
                utf8Start=0,
                utf8End=len(candidate_bytes),
                evidenceIds=("evidence_01",),
            ),
        ),
    )
    return FinalizationRequest(
        finalizationId="finalization_01",
        taskContract=task,
        taskContractDigest=task_digest,
        taskContractSnapshotRef=f"authority-task://{task_digest}",
        taskPartitionId="task:task_01:1",
        stateRoot=D3,
        evidenceRoot=D4,
        completionEpochId="epoch_01",
        barrierAdmissionSequence=7,
        dependencyHealth=(),
        candidateResponse=candidate,
        claimManifest=manifest,
    )


def _registry(partition_id: str = "task:task_01:1") -> ProjectionRegistrySnapshot:
    return ProjectionRegistrySnapshot(
        registryId="completion-projections",
        registryVersion=4,
        policyDigest=D9,
        entries=(
            ProjectionRegistryEntry(
                projectionId="action",
                reducerExecutableDigest=D5,
                projectionSchemaVersion=2,
                required=True,
                partitionScope=(partition_id,),
            ),
            ProjectionRegistryEntry(
                projectionId="audit",
                reducerExecutableDigest=D7,
                projectionSchemaVersion=1,
                required=False,
                partitionScope=(partition_id,),
            ),
            ProjectionRegistryEntry(
                projectionId="task",
                reducerExecutableDigest=D6,
                projectionSchemaVersion=3,
                required=True,
                partitionScope=(partition_id,),
            ),
        ),
    )


def _checkpoints() -> tuple[RequiredProjectionCheckpoint, ...]:
    return (
        RequiredProjectionCheckpoint(
            partitionId="task:task_01:1",
            projectionId="action",
            requiredSequence=4,
            requiredEventHash=D1,
            requiredStateRoot=D3,
            reducerExecutableDigest=D5,
            projectionSchemaVersion=2,
        ),
        RequiredProjectionCheckpoint(
            partitionId="task:task_01:1",
            projectionId="task",
            requiredSequence=7,
            requiredEventHash=D2,
            requiredStateRoot=D3,
            reducerExecutableDigest=D6,
            projectionSchemaVersion=3,
        ),
    )


def _seal(request: FinalizationRequest | None = None) -> EpochSeal:
    request = request or _request()
    projections = tuple(
        RequiredProjection(partitionId=item.partition_id, projectionId=item.projection_id)
        for item in _checkpoints()
    )
    return EpochSeal(
        completionEpochId=request.completion_epoch_id,
        taskPartitionId=request.task_partition_id,
        taskContractDigest=request.task_contract_digest,
        taskContractSnapshotRef=request.task_contract_snapshot_ref,
        barrierAdmissionSequence=request.barrier_admission_sequence,
        epochCompareVersion=5,
        requiredProjectionDigest=canonical_required_projections_digest(projections),
        requiredProjections=projections,
        sealedAt=NOW,
    )


def _epoch(
    request: FinalizationRequest | None = None,
    *,
    compare_version: int = 5,
    state: CompletionEpochState = CompletionEpochState.SEALING,
) -> EpochSnapshot:
    request = request or _request()
    return EpochSnapshot(
        completionEpochId=request.completion_epoch_id,
        taskPartitionId=request.task_partition_id,
        taskContractBinding=TaskContractBinding(
            taskContractId=request.task_contract.task_contract_id,
            taskVersion=request.task_contract.version,
            taskContractDigest=request.task_contract_digest,
            taskContractSnapshotRef=request.task_contract_snapshot_ref,
        ),
        state=state,
        lastAdmissionSequence=request.barrier_admission_sequence,
        compareVersion=compare_version,
    )


def _seal_binding() -> EpochSealBinding:
    return EpochSealBinding(
        registry=_registry(),
        seal=_seal(),
        postSealEpoch=_epoch(),
        requiredCheckpoints=_checkpoints(),
    )


def _cursor_proofs() -> tuple[ProjectionCursorProof, ...]:
    action, task = _checkpoints()
    return (
        ProjectionCursorProof(
            checkpoint=action,
            acknowledgedSequence=6,
            acknowledgedEventHash=D8,
            acknowledgedStateRoot=D3,
            reducerExecutableDigest=D5,
            projectionSchemaVersion=2,
            compareVersion=3,
            successorAncestry=(
                JournalChainLink(sequence=5, previousHash=D1, eventHash=D7),
                JournalChainLink(sequence=6, previousHash=D7, eventHash=D8),
            ),
        ),
        ProjectionCursorProof(
            checkpoint=task,
            acknowledgedSequence=7,
            acknowledgedEventHash=D2,
            acknowledgedStateRoot=D3,
            reducerExecutableDigest=D6,
            projectionSchemaVersion=3,
            compareVersion=2,
            successorAncestry=(),
        ),
    )


def test_registry_drives_the_exact_sealed_checkpoint_domain() -> None:
    registry = _registry()
    binding = _seal_binding()

    assert registry.registry_digest is not None
    assert binding.registry.registry_digest == registry.registry_digest
    assert binding.post_seal_epoch.compare_version == binding.seal.epoch_compare_version
    assert tuple(
        (item.partition_id, item.projection_id) for item in binding.required_checkpoints
    ) == (
        ("task:task_01:1", "action"),
        ("task:task_01:1", "task"),
    )
    assert binding.checkpoint_vector_digest is not None
    assert binding.required_projection_domain_digest is not None
    assert binding.seal_binding_digest is not None

    round_trip = EpochSealBinding.model_validate(binding.model_dump(by_alias=True, mode="json"))
    assert round_trip == binding


@pytest.mark.parametrize("mode", ["omitted", "fake"])
def test_seal_binding_rejects_caller_selected_checkpoint_keys(mode: str) -> None:
    binding = _seal_binding()
    payload = binding.model_dump(by_alias=True, mode="json")
    payload.pop("checkpointVectorDigest")
    payload.pop("requiredProjectionDomainDigest")
    payload.pop("sealBindingDigest")
    if mode == "omitted":
        payload["requiredCheckpoints"] = payload["requiredCheckpoints"][:-1]
    else:
        fake = dict(payload["requiredCheckpoints"][-1])
        fake["projectionId"] = "caller-selected"
        payload["requiredCheckpoints"].append(fake)

    with pytest.raises(ValidationError, match="exactly cover|required projection"):
        EpochSealBinding.model_validate(payload)


def test_required_checkpoint_rejects_sequence_zero_downgrade() -> None:
    payload = _checkpoints()[0].model_dump(by_alias=True, mode="json")
    payload["requiredSequence"] = 0

    with pytest.raises(ValidationError, match="requiredSequence"):
        RequiredProjectionCheckpoint.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("reducerExecutableDigest", D8, "reducer"),
        ("projectionSchemaVersion", 99, "schema"),
        ("requiredStateRoot", D8, "state root"),
    ],
)
def test_seal_binding_rejects_wrong_checkpoint_identity_or_root(
    field: str,
    value: str | int,
    match: str,
) -> None:
    binding = _seal_binding()
    payload = binding.model_dump(by_alias=True, mode="json")
    payload.pop("checkpointVectorDigest")
    payload.pop("requiredProjectionDomainDigest")
    payload.pop("sealBindingDigest")
    payload["requiredCheckpoints"][0][field] = value

    with pytest.raises(ValidationError, match=match):
        EpochSealBinding.model_validate(payload)


@pytest.mark.parametrize(
    ("compare_version", "state", "match"),
    [
        (4, CompletionEpochState.SEALING, "post-seal.*compareVersion"),
        (5, CompletionEpochState.OPEN, "SEALING"),
    ],
)
def test_seal_binding_rejects_stale_or_pre_seal_epoch_snapshot(
    compare_version: int,
    state: CompletionEpochState,
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        EpochSealBinding(
            registry=_registry(),
            seal=_seal(),
            postSealEpoch=_epoch(compare_version=compare_version, state=state),
            requiredCheckpoints=_checkpoints(),
        )


def test_cursor_ancestry_is_successor_only_and_exact() -> None:
    checkpoint = _checkpoints()[0]
    valid = _cursor_proofs()[0]
    assert tuple(link.sequence for link in valid.successor_ancestry) == (5, 6)

    inclusive = valid.model_dump(by_alias=True, mode="json")
    inclusive["successorAncestry"] = [
        {"sequence": 4, "previousHash": D0, "eventHash": D1},
        *inclusive["successorAncestry"],
    ]
    with pytest.raises(ValidationError, match="successor-only|exactly"):
        ProjectionCursorProof.model_validate(inclusive)

    broken_hash = valid.model_dump(by_alias=True, mode="json")
    broken_hash["successorAncestry"][0]["previousHash"] = D9
    with pytest.raises(ValidationError, match="previousHash"):
        ProjectionCursorProof.model_validate(broken_hash)

    with pytest.raises(ValidationError, match="bounded"):
        ProjectionCursorProof(
            checkpoint=checkpoint,
            acknowledgedSequence=checkpoint.required_sequence + MAX_PROJECTION_ANCESTRY_LINKS + 1,
            acknowledgedEventHash=D8,
            acknowledgedStateRoot=D3,
            reducerExecutableDigest=D5,
            projectionSchemaVersion=2,
            compareVersion=3,
            successorAncestry=(),
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("reducerExecutableDigest", D8, "reducer"),
        ("projectionSchemaVersion", 99, "schema"),
        ("acknowledgedStateRoot", D8, "state root"),
    ],
)
def test_cursor_rejects_wrong_reducer_schema_or_root(
    field: str,
    value: str | int,
    match: str,
) -> None:
    payload = _cursor_proofs()[0].model_dump(by_alias=True, mode="json")
    payload[field] = value

    with pytest.raises(ValidationError, match=match):
        ProjectionCursorProof.model_validate(payload)


def test_evaluation_digest_commits_the_exact_cursor_vector() -> None:
    evaluation = FinalizationEvaluationBinding(
        sealBinding=_seal_binding(),
        request=_request(),
        projectionCursors=_cursor_proofs(),
    )

    assert evaluation.cursor_vector_digest is not None
    assert evaluation.evaluation_digest is not None

    missing = evaluation.model_dump(by_alias=True, mode="json")
    missing.pop("cursorVectorDigest")
    missing.pop("evaluationDigest")
    missing["projectionCursors"] = missing["projectionCursors"][:-1]
    with pytest.raises(ValidationError, match="exactly cover"):
        FinalizationEvaluationBinding.model_validate(missing)

    tampered_digest = evaluation.model_dump(by_alias=True, mode="json")
    tampered_digest["evaluationDigest"] = D9
    with pytest.raises(ValidationError, match="evaluationDigest"):
        FinalizationEvaluationBinding.model_validate(tampered_digest)


def test_evaluation_rejects_cursor_checkpoint_or_request_root_drift() -> None:
    evaluation = FinalizationEvaluationBinding(
        sealBinding=_seal_binding(),
        request=_request(),
        projectionCursors=_cursor_proofs(),
    )
    payload = evaluation.model_dump(by_alias=True, mode="json")
    payload.pop("cursorVectorDigest")
    payload.pop("evaluationDigest")
    payload["projectionCursors"][0]["checkpoint"]["requiredEventHash"] = D9
    payload["projectionCursors"][0]["successorAncestry"][0]["previousHash"] = D9
    with pytest.raises(ValidationError, match="sealed checkpoint"):
        FinalizationEvaluationBinding.model_validate(payload)

    payload = evaluation.model_dump(by_alias=True, mode="json")
    payload.pop("cursorVectorDigest")
    payload.pop("evaluationDigest")
    payload["request"].pop("finalizationRequestDigest")
    payload["request"]["stateRoot"] = D9
    with pytest.raises(ValidationError, match="state root"):
        FinalizationEvaluationBinding.model_validate(payload)
