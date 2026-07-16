from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unicodedata

import pytest
from pydantic import ValidationError

from magi_agent.execution_authority.contracts import (
    AuthorityCapability,
    AuthorityContract,
    canonical_authority_contract_digest,
)
from magi_agent.execution_authority.envelopes import (
    EffectDeclarationBinding,
    NormalizedInputSnapshot,
    canonical_provider_guarantees_digest,
    canonical_resource_refs_digest,
)
from magi_agent.execution_authority.execution_material import (
    ExactByteMaterial,
    ExecutionGrant,
    ExecutionStartBinding,
    ExecutionTargetBinding,
    NormalizedInputMaterial,
    NormalizedInputSemanticSnapshot,
    bind_normalized_input_to_authority,
    consume_execution_grant,
    validate_execution_grant,
)
from magi_agent.execution_authority.state_machine import (
    EffectClass,
    IdempotencyCapability,
    ProviderGuarantee,
    RecoveryStrategy,
    ResourceSemantics,
)


NOW = datetime(2026, 7, 15, 1, 2, 3, tzinfo=UTC)


def _digest(character: str) -> str:
    return "sha256:" + (character * 64)


def _bytes(value: bytes, *, media_type: str = "application/octet-stream") -> ExactByteMaterial:
    return ExactByteMaterial.from_bytes(value, media_type=media_type)


def _declaration() -> EffectDeclarationBinding:
    guarantees = (ProviderGuarantee.NONE,)
    return EffectDeclarationBinding(
        effectName="process.run",
        effectClass=EffectClass.PROCESS_EXEC,
        resourceSemantics=ResourceSemantics.PRIVATE_WORKSPACE_PROCESS,
        handlerDigest=_digest("1"),
        normalizerDigest=_digest("2"),
        resourceDeriverDigest=_digest("3"),
        executorDigest=_digest("4"),
        recoveryAdapterDigest=_digest("5"),
        providerGuaranteesDigest=canonical_provider_guarantees_digest(guarantees),
        providerGuarantees=guarantees,
        idempotencyCapability=IdempotencyCapability.NONE,
        recoveryStrategy=RecoveryStrategy.NO_REPLAY,
    )


def _material(
    payload: bytes = b'{"command":"printf","arguments":["ok"]}\n',
) -> NormalizedInputMaterial:
    return NormalizedInputMaterial(
        payload=_bytes(payload, media_type="application/json"),
        command=_bytes(b"/usr/bin/printf", media_type="text/plain"),
        arguments=_bytes(b'["ok"]', media_type="application/json"),
        workingDirectory=_bytes(b"workspace://root", media_type="text/plain"),
        environment=_bytes(b"{}", media_type="application/json"),
        requestBody=None,
        credentialScope=None,
        network=None,
        disclosure=_bytes(b'{"stdout":"digest-only"}', media_type="application/json"),
    )


def _legacy_snapshot(
    material: NormalizedInputMaterial,
    *,
    declaration: EffectDeclarationBinding | None = None,
    normalizer_digest: str | None = None,
) -> NormalizedInputSnapshot:
    bound_declaration = declaration or _declaration()
    empty = canonical_resource_refs_digest(())
    return NormalizedInputSnapshot(
        effectDeclarationDigest=bound_declaration.effect_declaration_digest,
        normalizedInputDigest=material.normalized_input_digest,
        normalizedPayloadRef=material.normalized_payload_ref,
        readSet=(),
        absenceSet=(),
        writeSet=(),
        egressSet=(),
        readSetDigest=empty,
        absenceSetDigest=empty,
        writeSetDigest=empty,
        egressSetDigest=empty,
        workspaceViewBindingDigest=None,
        idempotencyKeyDigest=_digest("6"),
        snapshotRef=f"authority-input://{material.normalized_input_digest}",
        normalizerDigest=normalizer_digest or bound_declaration.normalizer_digest,
        resourceDeriverDigest=bound_declaration.resource_deriver_digest,
        storedAt=NOW,
        compareVersion=1,
    )


def _semantic_snapshot(
    material: NormalizedInputMaterial | None = None,
) -> NormalizedInputSemanticSnapshot:
    bound_material = material or _material()
    return NormalizedInputSemanticSnapshot(
        declaration=_declaration(),
        snapshot=_legacy_snapshot(bound_material),
        material=bound_material,
    )


def _authority(
    snapshot: NormalizedInputSemanticSnapshot,
    *,
    sandbox_profile_digest: str = _digest("7"),
    command_digest: str | None = None,
) -> AuthorityContract:
    material = snapshot.material
    capability = AuthorityCapability(
        effectClass=EffectClass.PROCESS_EXEC,
        resourceRef="binary:printf",
        networkRefs=(),
        credentialRefs=(),
        workspaceViewBindingDigest=None,
    )
    return AuthorityContract(
        authorityContractId="authority_01",
        issuerId="platform",
        principalId="actor_01",
        tenantId="tenant_01",
        sessionId="session_01",
        turnId="turn_01",
        childActorId=None,
        taskContractId="task_01",
        taskVersion=1,
        taskContractDigest=_digest("8"),
        completionEpochId="epoch_01",
        authorityPartitionId="partition_01",
        actionId="action_01",
        attemptId="attempt_01",
        policyDigest=_digest("9"),
        normalizedRequestDigest=material.normalized_input_digest,
        commandDigest=command_digest or material.command_digest,
        argumentsDigest=material.arguments_digest,
        workingDirectoryDigest=material.working_directory_digest,
        environmentDigest=material.environment_digest,
        requestBodyDigest=material.request_body_digest,
        credentialScopeDigest=material.credential_scope_digest,
        networkDigest=material.network_digest,
        disclosureDigest=material.disclosure_digest,
        capabilities=(capability,),
        workspaceViewBindingDigest=None,
        sandboxProfileDigest=sandbox_profile_digest,
        guardianCeilingDigest=_digest("a"),
        expiresAt=NOW + timedelta(minutes=10),
        revokedAt=None,
        revocationDigest=None,
        fencingToken=41,
        maximumUses=1,
        decisionRequestId=None,
        resumeBindingDigest=None,
        parentAuthorityDigest=None,
        delegationChain=(),
        schemaVersion=1,
    )


def _target(
    *,
    sandbox_profile_digest: str = _digest("7"),
    declaration: EffectDeclarationBinding | None = None,
) -> ExecutionTargetBinding:
    bound_declaration = declaration or _declaration()
    return ExecutionTargetBinding(
        declaration=bound_declaration,
        executorId="local-process-executor",
        executorVersion="2026-07-15",
        executableArtifactDigest=bound_declaration.executor_digest,
        sandboxProfileDigest=sandbox_profile_digest,
        providerId=None,
        providerVersion=None,
        providerCapabilitiesDigest=None,
        attesterId="platform-build-attestor",
        attestationEvidenceDigest=_digest("b"),
        attestedAt=NOW - timedelta(minutes=1),
        attestationExpiresAt=NOW + timedelta(minutes=20),
    )


def _start(
    snapshot: NormalizedInputSemanticSnapshot,
    authority: AuthorityContract,
    target: ExecutionTargetBinding,
    **updates: object,
) -> ExecutionStartBinding:
    payload: dict[str, object] = {
        "startId": "start_01",
        "actionId": authority.action_id,
        "attemptId": authority.attempt_id,
        "partitionId": authority.authority_partition_id,
        "taskContractDigest": authority.task_contract_digest,
        "actionIntentDigest": _digest("c"),
        "preparationDigest": _digest("d"),
        "normalizedInputDigest": snapshot.material.normalized_input_digest,
        "semanticSnapshotDigest": snapshot.semantic_snapshot_digest,
        "targetDigest": target.target_digest,
        "authorityContractId": authority.authority_contract_id,
        "authorityContractDigest": canonical_authority_contract_digest(authority),
        "fencingToken": authority.fencing_token,
        "requestedAt": NOW - timedelta(seconds=1),
        "startNonceDigest": _digest("e"),
    }
    payload.update(updates)
    return ExecutionStartBinding.model_validate(payload)


def _grant(
    snapshot: NormalizedInputSemanticSnapshot | None = None,
    authority: AuthorityContract | None = None,
    target: ExecutionTargetBinding | None = None,
    start: ExecutionStartBinding | None = None,
    **updates: object,
) -> ExecutionGrant:
    bound_snapshot = snapshot or _semantic_snapshot()
    bound_authority = authority or _authority(bound_snapshot)
    bound_target = target or _target(sandbox_profile_digest=bound_authority.sandbox_profile_digest)
    bound_start = start or _start(bound_snapshot, bound_authority, bound_target)
    payload: dict[str, object] = {
        "grantId": "grant_01",
        "grantNonceDigest": _digest("f"),
        "start": bound_start,
        "inputSnapshot": bound_snapshot,
        "target": bound_target,
        "authorityContract": bound_authority,
        "issuedAt": NOW,
        "expiresAt": NOW + timedelta(minutes=2),
        "maximumUses": 1,
    }
    payload.update(updates)
    return ExecutionGrant.model_validate(payload)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (b"line\n", b"line\r\n"),
        (b"a\x00b", b"a\\u0000b"),
        (
            unicodedata.normalize("NFC", "Cafe\N{COMBINING ACUTE ACCENT}").encode(),
            unicodedata.normalize("NFD", "Caf\N{LATIN SMALL LETTER E WITH ACUTE}").encode(),
        ),
    ],
)
def test_exact_byte_material_distinguishes_byte_sequences(first: bytes, second: bytes) -> None:
    first_material = _bytes(first)
    second_material = _bytes(second)

    assert first_material.payload_bytes == first
    assert second_material.payload_bytes == second
    assert first_material.content_digest != second_material.content_digest


def test_exact_byte_material_rejects_a_claimed_digest_or_noncanonical_base64() -> None:
    material = _bytes(b"A")
    payload = material.model_dump(by_alias=True, mode="python")
    payload["contentDigest"] = _digest("0")

    with pytest.raises(ValidationError, match="contentDigest"):
        ExactByteMaterial.model_validate(payload)

    payload = material.model_dump(by_alias=True, mode="python")
    payload["payloadBase64"] = payload["payloadBase64"].rstrip("=")
    with pytest.raises(ValidationError, match="canonical base64"):
        ExactByteMaterial.model_validate(payload)


def test_normalized_material_rejects_a_to_b_payload_substitution() -> None:
    material_a = _material(b"request A\n")
    material_b = _material(b"request B\n")
    snapshot_a = _legacy_snapshot(material_a)

    with pytest.raises(ValidationError, match="normalizedInputDigest"):
        NormalizedInputSemanticSnapshot(
            declaration=_declaration(),
            snapshot=snapshot_a,
            material=material_b,
        )


def test_semantic_snapshot_identity_covers_normalizer_and_component_bytes() -> None:
    baseline = _semantic_snapshot()
    changed_declaration = EffectDeclarationBinding.model_validate(
        {
            **_declaration().model_dump(by_alias=True, mode="python"),
            "normalizerDigest": _digest("0"),
            "effectDeclarationDigest": None,
        }
    )
    different_normalizer = NormalizedInputSemanticSnapshot(
        declaration=changed_declaration,
        snapshot=_legacy_snapshot(
            _material(),
            declaration=changed_declaration,
            normalizer_digest=_digest("0"),
        ),
        material=_material(),
    )
    changed_components = NormalizedInputSemanticSnapshot(
        declaration=_declaration(),
        snapshot=_legacy_snapshot(_material()),
        material=NormalizedInputMaterial(
            **{
                **_material().model_dump(by_alias=True, mode="python"),
                "command": _bytes(b"/opt/alternate/printf", media_type="text/plain"),
                "commandDigest": None,
                "materialDigest": None,
            }
        ),
    )

    assert baseline.semantic_snapshot_digest != different_normalizer.semantic_snapshot_digest
    assert baseline.semantic_snapshot_digest != changed_components.semantic_snapshot_digest
    assert baseline.snapshot_ref == f"authority-input://{baseline.semantic_snapshot_digest}"


def test_authority_component_binding_rejects_a_to_b_command_substitution() -> None:
    snapshot = _semantic_snapshot()
    authority = _authority(snapshot, command_digest=_digest("0"))

    with pytest.raises(ValueError, match="commandDigest"):
        bind_normalized_input_to_authority(snapshot, authority)


def test_target_binds_declared_executor_artifact_and_attestation_subject() -> None:
    target = _target()
    assert target.executable_artifact_digest == _declaration().executor_digest
    assert target.attestation_subject_digest is not None
    assert target.target_digest is not None

    payload = target.model_dump(by_alias=True, mode="python")
    payload["executableArtifactDigest"] = _digest("0")
    payload["attestationSubjectDigest"] = None
    payload["targetDigest"] = None
    with pytest.raises(ValidationError, match="executorDigest"):
        ExecutionTargetBinding.model_validate(payload)


def test_execution_grant_binds_exact_start_payload_target_authority_and_fence() -> None:
    grant = _grant()

    assert grant.maximum_uses == 1
    assert grant.grant_digest is not None
    assert (
        validate_execution_grant(
            grant,
            expected_start=grant.start,
            expected_input_snapshot=grant.input_snapshot,
            expected_target=grant.target,
            expected_authority=grant.authority_contract,
            at=NOW + timedelta(seconds=1),
        )
        is grant
    )

    mismatched_start = ExecutionStartBinding.model_validate(
        {
            **grant.start.model_dump(by_alias=True, mode="python"),
            "preparationDigest": _digest("0"),
            "startDigest": None,
        }
    )
    with pytest.raises(ValueError, match="start"):
        validate_execution_grant(
            grant,
            expected_start=mismatched_start,
            expected_input_snapshot=grant.input_snapshot,
            expected_target=grant.target,
            expected_authority=grant.authority_contract,
            at=NOW + timedelta(seconds=1),
        )

    substituted_snapshot = _semantic_snapshot(_material(b"request B\n"))
    with pytest.raises(ValueError, match="input snapshot"):
        validate_execution_grant(
            grant,
            expected_start=grant.start,
            expected_input_snapshot=substituted_snapshot,
            expected_target=grant.target,
            expected_authority=grant.authority_contract,
            at=NOW + timedelta(seconds=1),
        )

    changed_window = grant.model_dump(by_alias=True, mode="python")
    changed_window["expiresAt"] = grant.expires_at + timedelta(seconds=1)
    with pytest.raises(ValidationError, match="grantDigest"):
        ExecutionGrant.model_validate(changed_window)


def test_execution_grant_rejects_mismatched_target_fence_and_expiry() -> None:
    snapshot = _semantic_snapshot()
    authority = _authority(snapshot)
    target = _target()

    with pytest.raises(ValidationError, match="targetDigest"):
        _grant(
            snapshot=snapshot,
            authority=authority,
            target=target,
            start=_start(snapshot, authority, target, targetDigest=_digest("0")),
        )

    with pytest.raises(ValidationError, match="fencingToken"):
        _grant(
            snapshot=snapshot,
            authority=authority,
            target=target,
            start=_start(snapshot, authority, target, fencingToken=42),
        )

    grant = _grant(snapshot=snapshot, authority=authority, target=target)
    with pytest.raises(ValueError, match="expired"):
        validate_execution_grant(
            grant,
            expected_start=grant.start,
            expected_input_snapshot=grant.input_snapshot,
            expected_target=grant.target,
            expected_authority=grant.authority_contract,
            at=grant.expires_at,
        )


def test_execution_grant_rejects_target_for_a_different_effect_declaration() -> None:
    snapshot = _semantic_snapshot()
    authority = _authority(snapshot)
    different_declaration = EffectDeclarationBinding.model_validate(
        {
            **_declaration().model_dump(by_alias=True, mode="python"),
            "normalizerDigest": _digest("0"),
            "effectDeclarationDigest": None,
        }
    )
    target = _target(declaration=different_declaration)
    start = _start(snapshot, authority, target)

    with pytest.raises(ValidationError, match="effectDeclarationDigest"):
        _grant(
            snapshot=snapshot,
            authority=authority,
            target=target,
            start=start,
        )


def test_execution_grant_is_one_shot() -> None:
    grant = _grant()

    assert consume_execution_grant(grant, at=NOW + timedelta(seconds=1), prior_uses=0) is grant
    with pytest.raises(ValueError, match="already consumed"):
        consume_execution_grant(grant, at=NOW + timedelta(seconds=1), prior_uses=1)

    payload = grant.model_dump(by_alias=True, mode="python")
    payload["maximumUses"] = 2
    payload["grantDigest"] = None
    with pytest.raises(ValidationError, match="maximumUses"):
        ExecutionGrant.model_validate(payload)
