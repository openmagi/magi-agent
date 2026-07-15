from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from magi_agent.execution_authority.envelopes import (
    BackendObservation,
    CompletionVerdict,
    DependencyHealth,
    EffectDeclarationBinding,
    ProjectionCursorBinding,
    RequiredProjection,
    ResearchClaimResult,
    RequirementResult,
    ResponseClaim,
    canonical_provider_guarantees_digest,
    canonical_required_projections_digest,
)
from magi_agent.execution_authority.state_machine import (
    CompletionStatus,
    DependencyStatus,
    EffectClass,
    IdempotencyCapability,
    ProviderGuarantee,
    RecoveryStrategy,
    RequirementState,
    ResourceSemantics,
)


DIGEST = "sha256:" + ("0" * 64)


def _declaration_payload() -> dict[str, object]:
    guarantees = (ProviderGuarantee.NONE,)
    return {
        "schemaVersion": 1,
        "effectName": "workspace.mutate",
        "effectClass": EffectClass.WORKSPACE_WRITE,
        "resourceSemantics": ResourceSemantics.READ_ONLY,
        "effectDeclarationDigest": DIGEST,
        "handlerDigest": DIGEST,
        "normalizerDigest": DIGEST,
        "resourceDeriverDigest": DIGEST,
        "executorDigest": DIGEST,
        "recoveryAdapterDigest": DIGEST,
        "providerGuaranteesDigest": canonical_provider_guarantees_digest(guarantees),
        "providerGuarantees": guarantees,
        "idempotencyCapability": IdempotencyCapability.NONE,
        "recoveryStrategy": RecoveryStrategy.NO_REPLAY,
    }


@pytest.mark.parametrize(
    ("effect_class", "resource_semantics"),
    [
        (EffectClass.WORKSPACE_WRITE, ResourceSemantics.READ_ONLY),
        (EffectClass.WORKSPACE_DELETE, ResourceSemantics.REMOTE_EFFECT),
        (EffectClass.WORKSPACE_READ, ResourceSemantics.WORKSPACE_TRANSACTION),
    ],
)
def test_workspace_effect_classes_require_matching_resource_semantics(
    effect_class: EffectClass,
    resource_semantics: ResourceSemantics,
) -> None:
    payload = _declaration_payload()
    payload["effectClass"] = effect_class
    payload["resourceSemantics"] = resource_semantics

    with pytest.raises(ValidationError, match="workspace effectClass"):
        EffectDeclarationBinding.model_validate(payload)


@pytest.mark.parametrize(
    "effect_class",
    [
        EffectClass.NETWORK_WRITE,
        EffectClass.DATABASE_WRITE,
        EffectClass.MESSAGE_SEND,
        EffectClass.ARTIFACT_DELIVER,
        EffectClass.MEMORY_WRITE,
        EffectClass.SCHEDULER_WRITE,
        EffectClass.MISSION_WRITE,
        EffectClass.INFRASTRUCTURE_WRITE,
        EffectClass.PLUGIN_WRITE,
    ],
)
def test_mutating_effect_classes_cannot_claim_read_only_replay(
    effect_class: EffectClass,
) -> None:
    payload = _declaration_payload()
    payload.pop("effectDeclarationDigest")
    payload.update(
        {
            "effectClass": effect_class,
            "resourceSemantics": ResourceSemantics.READ_ONLY,
            "recoveryStrategy": RecoveryStrategy.READ_ONLY_REPLAY,
        }
    )

    with pytest.raises(ValidationError, match="mutating effectClass"):
        EffectDeclarationBinding.model_validate(payload)


def _aborted_observation_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "actionId": "action_01",
        "attemptId": "attempt_01",
        "partitionId": "workspace_01",
        "taskContractDigest": DIGEST,
        "actionIntentDigest": DIGEST,
        "requestDigest": DIGEST,
        "authorityDigest": DIGEST,
        "fencingToken": 1,
        "executorId": "executor_01",
        "executorVersion": "1.0.0",
        "sandboxProfileDigest": DIGEST,
        "providerId": None,
        "providerVersion": None,
        "providerCapabilitiesDigest": None,
        "attemptKind": "execution",
        "sourceAttemptId": None,
        "reconcilesAttemptId": None,
        "effectMayHaveStarted": False,
        "observedOutcome": "aborted",
        "transmissionState": "proven_not_sent",
        "providerRequestIdDigest": None,
        "observedEffectRefs": (),
        "reasonCodes": ("pre_execution_abort",),
        "processExitCode": None,
        "stdoutDigest": None,
        "stderrDigest": None,
        "outputTruncated": False,
        "privateWorkspaceDiffDigest": None,
        "workspacePublicationDigest": None,
        "providerReceiptDigest": None,
    }


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("effectMayHaveStarted", True),
        ("observedEffectRefs", ("workspace://changed",)),
    ],
)
def test_aborted_observation_proves_no_effect_started(
    field: str,
    invalid: object,
) -> None:
    payload = _aborted_observation_payload()
    payload[field] = invalid

    with pytest.raises(ValidationError, match="aborted outcome proves no effect"):
        BackendObservation.model_validate(payload)


def _response_claim_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "claimId": "claim_01",
        "claimClass": "result",
        "textDigest": DIGEST,
        "codepointStart": 0,
        "codepointEnd": 1,
        "utf8Start": 0,
        "utf8End": 1,
        "evidenceIds": ("evidence_01",),
    }


def _research_claim_result_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "claimId": "claim_01",
        "propositionDigest": DIGEST,
        "state": "satisfied",
        "evidenceIds": ("evidence_01",),
        "reasonCodes": ("entailed",),
    }


def _requirement_result_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "requirementId": "requirement_01",
        "state": RequirementState.SATISFIED,
        "evidenceIds": ("evidence_01",),
        "reasonCodes": ("verified",),
    }


def _completion_verdict_payload() -> dict[str, object]:
    projection = RequiredProjection(
        schemaVersion=1,
        partitionId="task:task_01:1",
        projectionId="task",
    )
    cursor = ProjectionCursorBinding(
        schemaVersion=1,
        partitionId=projection.partition_id,
        projectionId=projection.projection_id,
        requiredSequence=1,
        requiredEventHash=DIGEST,
        acknowledgedSequence=1,
        acknowledgedEventHash=DIGEST,
        stateRoot=DIGEST,
        compareVersion=1,
    )
    projection_digest = canonical_required_projections_digest((projection,))
    return {
        "schemaId": "magi.completion_verdict.v1",
        "completionId": "completion_01",
        "finalizationId": "finalization_01",
        "finalizationRequestDigest": DIGEST,
        "responseClaimManifestDigest": DIGEST,
        "status": CompletionStatus.BLOCKED,
        "taskContractId": "task_01",
        "taskVersion": 1,
        "taskContractDigest": DIGEST,
        "taskContractSnapshotRef": f"authority-task://{DIGEST}",
        "taskPartitionId": projection.partition_id,
        "completionEpochId": "epoch_01",
        "stateRoot": DIGEST,
        "evidenceRoot": DIGEST,
        "barrierAdmissionSequence": 1,
        "requiredProjectionRegistryDigest": projection_digest,
        "requiredProjectionDigest": projection_digest,
        "projectionCursors": (cursor,),
        "requirements": (),
        "includedActionIds": ("action_01",),
        "responseDigest": DIGEST,
        "reasonCodes": ("blocked",),
    }


@pytest.mark.parametrize(
    ("model", "payload", "field"),
    [
        (ResponseClaim, _response_claim_payload, "evidenceIds"),
        (ResearchClaimResult, _research_claim_result_payload, "evidenceIds"),
        (RequirementResult, _requirement_result_payload, "evidenceIds"),
        (CompletionVerdict, _completion_verdict_payload, "includedActionIds"),
    ],
)
def test_completion_contracts_reject_blank_reference_ids(
    model: type[BaseModel],
    payload: Callable[[], dict[str, object]],
    field: str,
) -> None:
    invalid = payload()
    invalid[field] = ("",)

    with pytest.raises(ValidationError, match="must not contain empty strings"):
        model.model_validate(invalid)


def _dependency_health_payload() -> dict[str, object]:
    return {
        "schemaId": "magi.dependency_health.v1",
        "dependencyId": "source_fetcher",
        "status": DependencyStatus.FINDING,
        "producerVersion": None,
        "schemaVersion": None,
        "producerAlive": False,
        "invocationEvidenceId": None,
        "invocationEvidenceDigest": None,
        "taskContractDigest": DIGEST,
        "completionEpochId": "epoch_01",
        "stateRoot": DIGEST,
        "observedAt": datetime(2026, 7, 15, tzinfo=UTC),
        "reasonCodes": ("dependency_finding",),
    }


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (DependencyHealth, _dependency_health_payload),
        (ResearchClaimResult, _research_claim_result_payload),
        (RequirementResult, _requirement_result_payload),
        (CompletionVerdict, _completion_verdict_payload),
    ],
)
def test_completion_contracts_reject_blank_reason_codes(
    model: type[BaseModel],
    payload: Callable[[], dict[str, object]],
) -> None:
    invalid = payload()
    invalid["reasonCodes"] = ("",)

    with pytest.raises(ValidationError, match="must not contain empty strings"):
        model.model_validate(invalid)
