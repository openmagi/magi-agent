from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec
from hashlib import sha256
import json

import pytest
from pydantic import ValidationError

from magi_agent.execution_authority.contracts import (
    AuthorityCapability,
    UserDecisionRequest,
    canonical_authority_contract_digest,
)
from magi_agent.execution_authority.envelopes import (
    ActionIntent,
    ActionReceipt,
    BackendObservation,
    ExecutionPreparation,
    ExecutionStart,
    ProjectionCursorBinding,
    VerificationEvidenceBinding,
    canonical_action_intent_digest,
    canonical_backend_observation_digest,
    canonical_resource_refs_digest,
    canonical_workspace_view_binding_digest,
)
from magi_agent.execution_authority.state_machine import ActionState
from tests.execution_authority.test_user_decision_envelopes import (
    D0,
    D1,
    D2,
    D3,
    D4,
    D5,
    D6,
    D7,
    D8,
    D9,
    NOW,
    _authority,
    _event,
    _intent,
    _request,
    _resume,
)


WORKSPACE_ROOT_REF = f"workspace://{D0}/"
WORKSPACE_A_REF = WORKSPACE_ROOT_REF + "a.txt"
WORKSPACE_B_REF = WORKSPACE_ROOT_REF + "b.txt"
WORKSPACE_GENERATION = 12
WORKSPACE_STATE_ROOT = D7
OBSERVATION_CONTRACTS = import_module("magi_agent.execution_authority.observation_contracts")


def test_observation_contract_module_is_available() -> None:
    assert find_spec("magi_agent.execution_authority.observation_contracts") is not None


def _replace_event_payload(event_payload: dict[str, object], **updates: object) -> None:
    parsed = json.loads(event_payload["payloadJson"])
    parsed.update(updates)
    payload_json = json.dumps(
        parsed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    event_payload["payloadJson"] = payload_json
    event_payload["payloadDigest"] = "sha256:" + sha256(payload_json.encode("utf-8")).hexdigest()


def _preparation() -> ExecutionPreparation:
    view_digest = canonical_workspace_view_binding_digest(
        workspace_id="workspace_01",
        workspace_ref=WORKSPACE_ROOT_REF,
        authority_partition_id="workspace_01",
        generation=WORKSPACE_GENERATION,
        state_root=WORKSPACE_STATE_ROOT,
    )
    request_payload = _request().model_dump(by_alias=True, mode="json")
    request_payload.pop("capabilitiesDigest")
    request_payload["workspaceViewBindingDigest"] = view_digest
    request_payload["capabilities"] = [
        AuthorityCapability(
            effectClass="workspace.write",
            resourceRef=WORKSPACE_ROOT_REF,
            networkRefs=(),
            credentialRefs=(),
            workspaceViewBindingDigest=view_digest,
        ).model_dump(by_alias=True, mode="json")
    ]
    request = UserDecisionRequest.model_validate(request_payload)
    resume = _resume(request)
    authority = _authority(request, resume)

    intent_payload = _intent(request, authority).model_dump(by_alias=True, mode="json")
    intent_payload.update(
        {
            "readSet": [WORKSPACE_A_REF],
            "absenceSet": [WORKSPACE_B_REF],
            "writeSet": [WORKSPACE_A_REF],
            "readSetDigest": canonical_resource_refs_digest((WORKSPACE_A_REF,)),
            "absenceSetDigest": canonical_resource_refs_digest((WORKSPACE_B_REF,)),
            "writeSetDigest": canonical_resource_refs_digest((WORKSPACE_A_REF,)),
        }
    )
    intent = ActionIntent.model_validate(intent_payload)
    intent_digest = canonical_action_intent_digest(intent)
    authority_digest = canonical_authority_contract_digest(authority)
    event_payload = {
        "actionIntentDigest": intent_digest,
        "authorityContractDigest": authority_digest,
        "decisionRequestId": request.decision_request_id,
        "resumeBindingDigest": authority.resume_binding_digest,
    }
    return ExecutionPreparation(
        schemaVersion=1,
        intent=intent,
        authorityContract=authority,
        actionId=intent.action_id,
        attemptId=intent.attempt_id,
        partitionId=intent.partition_id,
        taskContractDigest=intent.task_contract_digest,
        actionIntentDigest=intent_digest,
        requestDigest=intent.normalized_input_digest,
        authorityContractId=authority.authority_contract_id,
        authorityContractDigest=authority_digest,
        fencingToken=authority.fencing_token,
        actionCompareVersion=3,
        attemptCompareVersion=4,
        partitionCompareVersion=5,
        authorityEvent=_event(
            "action.authorized",
            request,
            event_id="event_authorized",
            authority_contract_id=authority.authority_contract_id,
            payload=event_payload,
        ),
        preparedEvent=_event(
            "action.prepared",
            request,
            event_id="event_prepared",
            authority_contract_id=authority.authority_contract_id,
            payload=event_payload,
            sequence=2,
            previous_hash=D1,
            event_hash=D2,
            row_checksum=D3,
        ),
    )


def _resource_observation(
    resource_ref: str,
    *,
    expected_presence: str,
):
    present = expected_presence == "present"
    return OBSERVATION_CONTRACTS.ResourceObservation(
        schemaVersion=1,
        resourceRef=resource_ref,
        expectedPresence=expected_presence,
        observedPresence=expected_presence,
        contentDigest=D4 if present else None,
        resourceStateDigest=D5 if present else D6,
        workspaceGeneration=WORKSPACE_GENERATION,
        workspaceStateRoot=WORKSPACE_STATE_ROOT,
        observedAt=NOW,
    )


def _workspace_observation(
    preparation: ExecutionPreparation | None = None,
):
    preparation = preparation or _preparation()
    return OBSERVATION_CONTRACTS.WorkspacePreconditionObservation(
        schemaVersion=1,
        observationId="workspace_precondition_01",
        actionIntentDigest=preparation.action_intent_digest,
        authorityPartitionId=preparation.partition_id,
        workspaceId="workspace_01",
        workspaceRef=WORKSPACE_ROOT_REF,
        workspaceViewBindingDigest=preparation.intent.workspace_view_binding_digest,
        workspaceGeneration=WORKSPACE_GENERATION,
        workspaceStateRoot=WORKSPACE_STATE_ROOT,
        observerId="workspace-observer",
        observerVersion="1.0.0",
        observerArtifactDigest=D8,
        observedAt=NOW,
        resources=(
            _resource_observation(WORKSPACE_A_REF, expected_presence="present"),
            _resource_observation(WORKSPACE_B_REF, expected_presence="absent"),
        ),
    )


def _start(preparation: ExecutionPreparation | None = None) -> ExecutionStart:
    preparation = preparation or _preparation()
    request = _request_for_preparation(preparation)
    event_payload = {
        "actionIntentDigest": preparation.action_intent_digest,
        "authorityContractDigest": preparation.authority_contract_digest,
        "preparedEventId": preparation.prepared_event.event_id,
        "preparedEventSequence": preparation.prepared_event.sequence,
        "preparedEventHash": preparation.prepared_event.event_hash,
        "executorId": "workspace-executor",
        "executorVersion": "1.0.0",
        "sandboxProfileDigest": preparation.authority_contract.sandbox_profile_digest,
        "providerId": None,
        "providerVersion": None,
        "providerCapabilitiesDigest": None,
        "executionGrantDigest": D7,
    }
    return ExecutionStart(
        schemaVersion=1,
        preparation=preparation,
        actionId=preparation.action_id,
        attemptId=preparation.attempt_id,
        partitionId=preparation.partition_id,
        taskContractDigest=preparation.task_contract_digest,
        actionIntentDigest=preparation.action_intent_digest,
        requestDigest=preparation.request_digest,
        authorityContractId=preparation.authority_contract_id,
        authorityContractDigest=preparation.authority_contract_digest,
        fencingToken=preparation.fencing_token,
        executorId="workspace-executor",
        executorVersion="1.0.0",
        sandboxProfileDigest=preparation.authority_contract.sandbox_profile_digest,
        providerId=None,
        providerVersion=None,
        providerCapabilitiesDigest=None,
        executionTokenDigest=D7,
        actionCompareVersion=preparation.action_compare_version + 1,
        attemptCompareVersion=preparation.attempt_compare_version + 1,
        partitionCompareVersion=preparation.partition_compare_version + 1,
        executingEvent=_event(
            "action.executing",
            request,
            event_id="event_executing",
            authority_contract_id=preparation.authority_contract_id,
            causation_id=preparation.prepared_event.event_id,
            payload=event_payload,
            sequence=3,
            previous_hash=preparation.prepared_event.event_hash,
            event_hash=D3,
            row_checksum=D4,
        ),
    )


def _request_for_preparation(preparation: ExecutionPreparation) -> UserDecisionRequest:
    request_payload = _request().model_dump(by_alias=True, mode="json")
    request_payload.pop("capabilitiesDigest")
    request_payload.update(
        {
            "workspaceViewBindingDigest": preparation.intent.workspace_view_binding_digest,
            "capabilities": [
                capability.model_dump(by_alias=True, mode="json")
                for capability in preparation.intent.capabilities
            ],
        }
    )
    return UserDecisionRequest.model_validate(request_payload)


def _backend_observation(
    start: ExecutionStart | None = None,
    *,
    executor_id: str | None = None,
    sandbox_profile_digest: str | None = None,
) -> BackendObservation:
    start = start or _start()
    return BackendObservation(
        schemaVersion=1,
        actionId=start.action_id,
        attemptId=start.attempt_id,
        partitionId=start.partition_id,
        taskContractDigest=start.task_contract_digest,
        actionIntentDigest=start.action_intent_digest,
        requestDigest=start.request_digest,
        authorityDigest=start.authority_contract_digest,
        fencingToken=start.fencing_token,
        executorId=executor_id or start.executor_id,
        executorVersion=start.executor_version,
        sandboxProfileDigest=sandbox_profile_digest or start.sandbox_profile_digest,
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
        observedEffectRefs=(WORKSPACE_A_REF,),
        reasonCodes=("published",),
        processExitCode=0,
        stdoutDigest=D4,
        stderrDigest=D5,
        outputTruncated=False,
        privateWorkspaceDiffDigest=D6,
        workspacePublicationDigest=D9,
        providerReceiptDigest=None,
    )


def _physical_receipt(
    start: ExecutionStart | None = None,
    *,
    observation: BackendObservation | None = None,
) -> ActionReceipt:
    start = start or _start()
    observation = observation or _backend_observation(start)
    return ActionReceipt(
        schemaId="magi.action_receipt.v1",
        observation=observation,
        state=ActionState.COMMITTED,
        reasonCodes=("published",),
        stateRootBefore=WORKSPACE_STATE_ROOT,
        stateRootAfter=D8,
    )


def _observation_binding(
    start: ExecutionStart | None = None,
    *,
    receipt: ActionReceipt | None = None,
):
    start = start or _start()
    receipt = receipt or _physical_receipt(start)
    observation = receipt.observation
    start_digest = OBSERVATION_CONTRACTS.canonical_execution_start_digest(start)
    target_digest = OBSERVATION_CONTRACTS.canonical_execution_target_digest(start)
    observation_digest = canonical_backend_observation_digest(observation)
    receipt_digest = OBSERVATION_CONTRACTS.canonical_action_receipt_digest(receipt)
    request = _request_for_preparation(start.preparation)
    observed_event = _event(
        "action.observed",
        request,
        event_id="event_observed",
        authority_contract_id=start.authority_contract_id,
        causation_id=start.executing_event.event_id,
        payload={
            "actorId": start.preparation.intent.actor_id,
            "authorityContractDigest": start.authority_contract_digest,
            "authorityContractId": start.authority_contract_id,
            "backendObservationDigest": observation_digest,
            "executingEventHash": start.executing_event.event_hash,
            "executingEventId": start.executing_event.event_id,
            "executingEventSequence": start.executing_event.sequence,
            "executionGrantDigest": start.execution_token_digest,
            "executionStartDigest": start_digest,
            "executionTargetDigest": target_digest,
            "identityDigest": start.preparation.intent.identity_digest,
            "policyDigest": start.preparation.intent.policy_digest,
        },
        sequence=4,
        previous_hash=start.executing_event.event_hash,
        event_hash=D4,
        row_checksum=D5,
    )
    terminal_event = _event(
        "action.committed",
        request,
        event_id="event_committed",
        authority_contract_id=start.authority_contract_id,
        causation_id=observed_event.event_id,
        payload={
            "actionReceiptDigest": receipt_digest,
            "backendObservationDigest": observation_digest,
            "observedEventHash": observed_event.event_hash,
            "observedEventId": observed_event.event_id,
            "observedEventSequence": observed_event.sequence,
            "state": receipt.state.value,
            "stateRootAfter": receipt.state_root_after,
            "stateRootBefore": receipt.state_root_before,
        },
        sequence=5,
        previous_hash=observed_event.event_hash,
        event_hash=D5,
        row_checksum=D6,
    )
    return OBSERVATION_CONTRACTS.ExecutionObservationBinding(
        schemaVersion=1,
        start=start,
        receipt=receipt,
        executionStartDigest=start_digest,
        executionTargetDigest=target_digest,
        backendObservationDigest=observation_digest,
        actionReceiptDigest=receipt_digest,
        expectedActionCompareVersion=start.action_compare_version,
        expectedAttemptCompareVersion=start.attempt_compare_version,
        expectedPartitionCompareVersion=start.partition_compare_version,
        actionCompareVersion=start.action_compare_version + 1,
        attemptCompareVersion=start.attempt_compare_version + 1,
        partitionCompareVersion=start.partition_compare_version + 1,
        observedEvent=observed_event,
        terminalEvent=terminal_event,
    )


def _verification_evidence(binding) -> VerificationEvidenceBinding:
    terminal = binding.terminal_event
    return VerificationEvidenceBinding(
        schemaVersion=1,
        evidenceId="evidence_verification_01",
        evidenceDigest=D9,
        verificationOutcome="passed",
        sourcePartitionId=terminal.partition_id,
        sourceEventId=terminal.event_id,
        sourceEventSequence=terminal.sequence,
        sourceEventHash=terminal.event_hash,
        sourceHeadSequence=terminal.sequence,
        sourceHeadHash=terminal.event_hash,
        sourceHeadCompareVersion=binding.partition_compare_version,
        projectionCursors=(
            ProjectionCursorBinding(
                schemaVersion=1,
                partitionId=terminal.partition_id,
                projectionId="evidence",
                requiredSequence=terminal.sequence,
                requiredEventHash=terminal.event_hash,
                acknowledgedSequence=terminal.sequence,
                acknowledgedEventHash=terminal.event_hash,
                stateRoot=binding.receipt.state_root_after,
                compareVersion=2,
            ),
        ),
        actionId=binding.start.action_id,
        attemptId=binding.start.attempt_id,
        taskContractDigest=binding.start.task_contract_digest,
        requestDigest=binding.start.request_digest,
        verifiedStateRoot=binding.receipt.state_root_after,
    )


def _verification_recording(binding=None):
    binding = binding or _observation_binding()
    evidence = _verification_evidence(binding)
    observation = binding.receipt.observation
    receipt = ActionReceipt(
        schemaId="magi.action_receipt.v1",
        observation=observation,
        state=ActionState.VERIFIED,
        reasonCodes=("postcondition_verified",),
        stateRootBefore=binding.receipt.state_root_before,
        stateRootAfter=binding.receipt.state_root_after,
    )
    evidence_digest = OBSERVATION_CONTRACTS.canonical_verification_evidence_digest(evidence)
    receipt_digest = OBSERVATION_CONTRACTS.canonical_action_receipt_digest(receipt)
    binding_digest = binding.binding_digest
    assert binding_digest is not None
    request = _request_for_preparation(binding.start.preparation)
    event = _event(
        "action.verified",
        request,
        event_id="event_verified",
        authority_contract_id=binding.start.authority_contract_id,
        causation_id=binding.terminal_event.event_id,
        payload={
            "backendObservationDigest": binding.backend_observation_digest,
            "observationBindingDigest": binding_digest,
            "sourceTerminalEventHash": binding.terminal_event.event_hash,
            "sourceTerminalEventId": binding.terminal_event.event_id,
            "sourceTerminalEventSequence": binding.terminal_event.sequence,
            "stateRootAfter": receipt.state_root_after,
            "verificationEvidenceDigest": evidence_digest,
            "verifiedReceiptDigest": receipt_digest,
        },
        sequence=6,
        previous_hash=binding.terminal_event.event_hash,
        event_hash=D6,
        row_checksum=D7,
    )
    return OBSERVATION_CONTRACTS.VerificationRecording(
        schemaVersion=1,
        observationBinding=binding,
        verifiedReceipt=receipt,
        verifiedReceiptDigest=receipt_digest,
        verificationEvidence=evidence,
        verificationEvidenceDigest=evidence_digest,
        expectedActionCompareVersion=binding.action_compare_version,
        expectedAttemptCompareVersion=binding.attempt_compare_version,
        expectedPartitionCompareVersion=binding.partition_compare_version,
        actionCompareVersion=binding.action_compare_version + 1,
        attemptCompareVersion=binding.attempt_compare_version + 1,
        partitionCompareVersion=binding.partition_compare_version + 1,
        verificationEvent=event,
    )


def test_workspace_preconditions_bind_every_read_and_absence_before_prepare() -> None:
    preparation = _preparation()
    observation = _workspace_observation(preparation)

    validated = OBSERVATION_CONTRACTS.validate_workspace_precondition_observation(
        preparation,
        observation,
    )

    assert validated.observation_digest is not None
    assert tuple(item.expected_presence for item in validated.resources) == (
        "present",
        "absent",
    )


@pytest.mark.parametrize("missing_ref", [WORKSPACE_A_REF, WORKSPACE_B_REF])
def test_workspace_preconditions_reject_missing_read_or_absence(
    missing_ref: str,
) -> None:
    preparation = _preparation()
    payload = _workspace_observation(preparation).model_dump(by_alias=True, mode="json")
    payload.pop("observationDigest")
    payload["resources"] = [
        item for item in payload["resources"] if item["resourceRef"] != missing_ref
    ]
    incomplete = OBSERVATION_CONTRACTS.WorkspacePreconditionObservation.model_validate(payload)

    with pytest.raises(ValueError, match="exactly cover"):
        OBSERVATION_CONTRACTS.validate_workspace_precondition_observation(
            preparation,
            incomplete,
        )


@pytest.mark.parametrize(
    "alias_ref",
    [
        f"workspace://{D0}/directory/../a.txt",
        f"workspace://{D0}/symlink/../b.txt",
        f"workspace://{D0}/a//b.txt",
    ],
)
def test_resource_observation_rejects_path_and_symlink_style_aliases(
    alias_ref: str,
) -> None:
    with pytest.raises(ValidationError, match="canonical"):
        _resource_observation(alias_ref, expected_presence="present")


def test_workspace_preconditions_reject_substituted_view_generation() -> None:
    preparation = _preparation()
    payload = _workspace_observation(preparation).model_dump(by_alias=True, mode="json")
    payload.pop("observationDigest")
    payload["workspaceGeneration"] += 1

    with pytest.raises(ValidationError, match="workspace view"):
        OBSERVATION_CONTRACTS.WorkspacePreconditionObservation.model_validate(payload)


def test_execution_observation_binds_exact_start_and_terminal_lineage() -> None:
    binding = _observation_binding()

    assert binding.observed_event.previous_hash == binding.start.executing_event.event_hash
    assert binding.terminal_event.previous_hash == binding.observed_event.event_hash
    assert binding.action_compare_version == binding.expected_action_compare_version + 1
    assert binding.binding_digest is not None
    observed_payload = json.loads(binding.observed_event.payload_json)
    assert observed_payload["actorId"] == binding.start.preparation.intent.actor_id
    assert observed_payload["identityDigest"] == (binding.start.preparation.intent.identity_digest)
    assert observed_payload["policyDigest"] == binding.start.preparation.intent.policy_digest
    assert observed_payload["authorityContractId"] == binding.start.authority_contract_id
    assert observed_payload["authorityContractDigest"] == (binding.start.authority_contract_digest)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("start_digest", "executionStartDigest"),
        ("executor", "executorId"),
        ("sandbox", "sandboxProfileDigest"),
    ],
)
def test_execution_observation_rejects_substituted_start_executor_or_sandbox(
    mutation: str,
    error: str,
) -> None:
    binding = _observation_binding()
    payload = binding.model_dump(by_alias=True, mode="json")
    payload.pop("bindingDigest")
    if mutation == "start_digest":
        payload["executionStartDigest"] = D9
    else:
        observation_payload = payload["receipt"]["observation"]
        observation_payload.pop("observationDigest")
        if mutation == "executor":
            observation_payload["executorId"] = "substituted-executor"
        else:
            observation_payload["sandboxProfileDigest"] = D9
        observation = BackendObservation.model_validate(observation_payload)
        payload["receipt"]["observation"] = observation.model_dump(by_alias=True, mode="json")
        receipt = ActionReceipt.model_validate(payload["receipt"])
        observation_digest = canonical_backend_observation_digest(observation)
        receipt_digest = OBSERVATION_CONTRACTS.canonical_action_receipt_digest(receipt)
        payload["backendObservationDigest"] = observation_digest
        payload["actionReceiptDigest"] = receipt_digest
        _replace_event_payload(
            payload["observedEvent"],
            backendObservationDigest=observation_digest,
        )
        _replace_event_payload(
            payload["terminalEvent"],
            backendObservationDigest=observation_digest,
            actionReceiptDigest=receipt_digest,
        )

    with pytest.raises(ValidationError, match=error):
        OBSERVATION_CONTRACTS.ExecutionObservationBinding.model_validate(payload)


def test_execution_observation_rejects_stale_cas() -> None:
    payload = _observation_binding().model_dump(by_alias=True, mode="json")
    payload.pop("bindingDigest")
    payload["actionCompareVersion"] = payload["expectedActionCompareVersion"]

    with pytest.raises(ValidationError, match="actionCompareVersion"):
        OBSERVATION_CONTRACTS.ExecutionObservationBinding.model_validate(payload)


def test_execution_observation_rejects_wrong_executing_lineage() -> None:
    payload = _observation_binding().model_dump(by_alias=True, mode="json")
    payload.pop("bindingDigest")
    payload["observedEvent"]["previousHash"] = D9

    with pytest.raises(ValidationError, match="previousHash"):
        OBSERVATION_CONTRACTS.ExecutionObservationBinding.model_validate(payload)


def test_verification_recording_binds_terminal_observation_and_cas() -> None:
    recording = _verification_recording()

    assert recording.verification_event.previous_hash == (
        recording.observation_binding.terminal_event.event_hash
    )
    assert recording.action_compare_version == (recording.expected_action_compare_version + 1)
    assert recording.recording_digest is not None


def test_verification_recording_rejects_substituted_terminal_source() -> None:
    payload = _verification_recording().model_dump(by_alias=True, mode="json")
    payload.pop("recordingDigest")
    payload["verificationEvidence"]["sourceEventId"] = "event_other"
    evidence = VerificationEvidenceBinding.model_validate(payload["verificationEvidence"])
    evidence_digest = OBSERVATION_CONTRACTS.canonical_verification_evidence_digest(evidence)
    payload["verificationEvidenceDigest"] = evidence_digest
    _replace_event_payload(
        payload["verificationEvent"],
        verificationEvidenceDigest=evidence_digest,
    )

    with pytest.raises(ValidationError, match="sourceEventId"):
        OBSERVATION_CONTRACTS.VerificationRecording.model_validate(payload)


def test_verification_recording_rejects_stale_cas_and_wrong_lineage() -> None:
    stale = _verification_recording().model_dump(by_alias=True, mode="json")
    stale.pop("recordingDigest")
    stale["attemptCompareVersion"] = stale["expectedAttemptCompareVersion"]
    with pytest.raises(ValidationError, match="attemptCompareVersion"):
        OBSERVATION_CONTRACTS.VerificationRecording.model_validate(stale)

    wrong_lineage = _verification_recording().model_dump(by_alias=True, mode="json")
    wrong_lineage.pop("recordingDigest")
    wrong_lineage["verificationEvent"]["previousHash"] = D9
    with pytest.raises(ValidationError, match="previousHash"):
        OBSERVATION_CONTRACTS.VerificationRecording.model_validate(wrong_lineage)
