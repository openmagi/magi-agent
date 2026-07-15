"""Byte-exact normalized-input and one-shot execution-grant contracts.

This module is intentionally contract-only.  It does not normalize input,
verify attestations, persist grants, or invoke an executor.  Runtime adapters
must supply those trusted operations and may only cross the execution boundary
after these immutable bindings validate.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from magi_agent.execution_authority.contracts import (
    AuthorityContract,
    canonical_authority_contract_digest,
)
from magi_agent.execution_authority.envelopes import (
    EffectDeclarationBinding,
    EnvelopeModel,
    NormalizedInputSnapshot,
)
from magi_agent.execution_authority.state_machine import EffectClass


_MAX_EXACT_MATERIAL_BYTES = 1_048_576


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _domain_digest(domain: str, value: object) -> str:
    return "sha256:" + sha256(domain.encode("ascii") + b"\x00" + _canonical_json(value)).hexdigest()


def _raw_digest(value: bytes) -> str:
    return "sha256:" + sha256(value).hexdigest()


def _require_exact_utc(value: datetime, *, field_name: str) -> datetime:
    if type(value) is not datetime:
        raise ValueError(f"{field_name} must be an exact datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC")
    return value


class ExactByteMaterial(EnvelopeModel):
    """A canonical-base64 wire value whose identity is the decoded bytes."""

    schema_id: Literal["magi.exact_byte_material.v1"] = Field(
        default="magi.exact_byte_material.v1",
        alias="schemaId",
    )
    media_type: str = Field(alias="mediaType", min_length=1, max_length=255)
    payload_base64: str = Field(alias="payloadBase64")
    byte_length: int | None = Field(default=None, alias="byteLength", ge=0, strict=True)
    content_digest: str | None = Field(default=None, alias="contentDigest")

    @field_validator("media_type", "payload_base64", mode="before")
    @classmethod
    def _require_exact_strings(cls, value: object, info: ValidationInfo) -> object:
        if type(value) is not str:
            raise ValueError(f"{info.field_name} must be an exact string")
        return value

    @model_validator(mode="after")
    def _derive_byte_identity(self) -> Self:
        try:
            encoded = self.payload_base64.encode("ascii")
            decoded = base64.b64decode(encoded, validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise ValueError("payloadBase64 must use canonical base64") from exc
        if base64.b64encode(decoded) != encoded:
            raise ValueError("payloadBase64 must use canonical base64")
        if len(decoded) > _MAX_EXACT_MATERIAL_BYTES:
            raise ValueError("exact byte material exceeds the byte limit")

        expected_length = len(decoded)
        if self.byte_length is not None and self.byte_length != expected_length:
            raise ValueError("byteLength does not match decoded payload bytes")
        expected_digest = _raw_digest(decoded)
        if self.content_digest is not None and self.content_digest != expected_digest:
            raise ValueError("contentDigest does not match decoded payload bytes")
        object.__setattr__(self, "byte_length", expected_length)
        object.__setattr__(self, "content_digest", expected_digest)
        return self

    @classmethod
    def from_bytes(cls, payload: bytes, *, media_type: str) -> ExactByteMaterial:
        if type(payload) is not bytes:
            raise TypeError("payload must be exact bytes")
        if type(media_type) is not str or not media_type:
            raise TypeError("media_type must be a non-empty exact string")
        return cls(
            mediaType=media_type,
            payloadBase64=base64.b64encode(payload).decode("ascii"),
        )

    @property
    def payload_bytes(self) -> bytes:
        """Return a new immutable byte value after the base64 was validated."""

        return base64.b64decode(self.payload_base64.encode("ascii"), validate=True)


class NormalizedInputMaterial(EnvelopeModel):
    """Exact normalized payload and exact byte identities for executor components."""

    schema_id: Literal["magi.normalized_input_material.v1"] = Field(
        default="magi.normalized_input_material.v1",
        alias="schemaId",
    )
    payload: ExactByteMaterial
    command: ExactByteMaterial | None = None
    arguments: ExactByteMaterial
    working_directory: ExactByteMaterial = Field(alias="workingDirectory")
    environment: ExactByteMaterial
    request_body: ExactByteMaterial | None = Field(default=None, alias="requestBody")
    credential_scope: ExactByteMaterial | None = Field(default=None, alias="credentialScope")
    network: ExactByteMaterial | None = None
    disclosure: ExactByteMaterial

    normalized_input_digest: str | None = Field(
        default=None,
        alias="normalizedInputDigest",
    )
    normalized_payload_ref: str | None = Field(default=None, alias="normalizedPayloadRef")
    command_digest: str | None = Field(default=None, alias="commandDigest")
    arguments_digest: str | None = Field(default=None, alias="argumentsDigest")
    working_directory_digest: str | None = Field(
        default=None,
        alias="workingDirectoryDigest",
    )
    environment_digest: str | None = Field(default=None, alias="environmentDigest")
    request_body_digest: str | None = Field(default=None, alias="requestBodyDigest")
    credential_scope_digest: str | None = Field(
        default=None,
        alias="credentialScopeDigest",
    )
    network_digest: str | None = Field(default=None, alias="networkDigest")
    disclosure_digest: str | None = Field(default=None, alias="disclosureDigest")
    material_digest: str | None = Field(default=None, alias="materialDigest")

    @model_validator(mode="after")
    def _derive_all_component_bindings(self) -> Self:
        payload_digest = self.payload.content_digest
        assert payload_digest is not None
        expected_payload_ref = f"authority-input-payload://{payload_digest}"
        component_bindings: tuple[tuple[str, str | None], ...] = (
            ("command_digest", self.command.content_digest if self.command else None),
            ("arguments_digest", self.arguments.content_digest),
            ("working_directory_digest", self.working_directory.content_digest),
            ("environment_digest", self.environment.content_digest),
            (
                "request_body_digest",
                self.request_body.content_digest if self.request_body else None,
            ),
            (
                "credential_scope_digest",
                self.credential_scope.content_digest if self.credential_scope else None,
            ),
            ("network_digest", self.network.content_digest if self.network else None),
            ("disclosure_digest", self.disclosure.content_digest),
        )
        bindings: tuple[tuple[str, str | None], ...] = (
            ("normalized_input_digest", payload_digest),
            *component_bindings,
        )
        for field_name, expected in bindings:
            observed = getattr(self, field_name)
            if observed is not None and observed != expected:
                alias = type(self).model_fields[field_name].alias or field_name
                raise ValueError(f"{alias} does not match exact component bytes")
            object.__setattr__(self, field_name, expected)
        if (
            self.normalized_payload_ref is not None
            and self.normalized_payload_ref != expected_payload_ref
        ):
            raise ValueError("normalizedPayloadRef does not match exact payload bytes")
        object.__setattr__(self, "normalized_payload_ref", expected_payload_ref)

        preimage = self.model_dump(
            by_alias=True,
            mode="json",
            exclude={"material_digest"},
        )
        expected_material_digest = _domain_digest(
            "magi.normalized_input_material.v1",
            preimage,
        )
        if self.material_digest is not None and self.material_digest != expected_material_digest:
            raise ValueError("materialDigest does not match exact normalized input material")
        object.__setattr__(self, "material_digest", expected_material_digest)
        return self


class NormalizedInputSemanticSnapshot(EnvelopeModel):
    """Full semantic identity missing from the payload-only legacy snapshot ref."""

    schema_id: Literal["magi.normalized_input_semantic_snapshot.v1"] = Field(
        default="magi.normalized_input_semantic_snapshot.v1",
        alias="schemaId",
    )
    declaration: EffectDeclarationBinding
    snapshot: NormalizedInputSnapshot
    material: NormalizedInputMaterial
    semantic_snapshot_digest: str | None = Field(
        default=None,
        alias="semanticSnapshotDigest",
    )
    snapshot_ref: str | None = Field(default=None, alias="snapshotRef")

    @model_validator(mode="after")
    def _bind_full_semantic_identity(self) -> Self:
        declaration_digest = self.declaration.effect_declaration_digest
        assert declaration_digest is not None
        bindings: tuple[tuple[str, object, object], ...] = (
            (
                "effectDeclarationDigest",
                self.snapshot.effect_declaration_digest,
                declaration_digest,
            ),
            (
                "normalizerDigest",
                self.snapshot.normalizer_digest,
                self.declaration.normalizer_digest,
            ),
            (
                "resourceDeriverDigest",
                self.snapshot.resource_deriver_digest,
                self.declaration.resource_deriver_digest,
            ),
            (
                "normalizedInputDigest",
                self.snapshot.normalized_input_digest,
                self.material.normalized_input_digest,
            ),
            (
                "normalizedPayloadRef",
                self.snapshot.normalized_payload_ref,
                self.material.normalized_payload_ref,
            ),
        )
        for alias, observed, expected in bindings:
            if observed != expected:
                raise ValueError(f"{alias} does not match exact normalized input material")

        if (
            self.declaration.effect_class
            in {
                EffectClass.PROCESS_EXEC,
                EffectClass.PROCESS_EXECUTE,
            }
            and self.material.command is None
        ):
            raise ValueError("process execution requires exact command material")
        if self.snapshot.egress_set and self.material.network is None:
            raise ValueError("egressSet requires exact network component material")

        semantic_preimage = {
            "schemaId": self.schema_id,
            "effectDeclarationDigest": declaration_digest,
            "normalizerDigest": self.snapshot.normalizer_digest,
            "resourceDeriverDigest": self.snapshot.resource_deriver_digest,
            "material": self.material.model_dump(by_alias=True, mode="json"),
            "readSet": list(self.snapshot.read_set),
            "absenceSet": list(self.snapshot.absence_set),
            "writeSet": list(self.snapshot.write_set),
            "egressSet": list(self.snapshot.egress_set),
            "readSetDigest": self.snapshot.read_set_digest,
            "absenceSetDigest": self.snapshot.absence_set_digest,
            "writeSetDigest": self.snapshot.write_set_digest,
            "egressSetDigest": self.snapshot.egress_set_digest,
            "workspaceViewBindingDigest": self.snapshot.workspace_view_binding_digest,
            "idempotencyKeyDigest": self.snapshot.idempotency_key_digest,
        }
        expected_digest = _domain_digest(
            "magi.normalized_input_semantic_snapshot.v1",
            semantic_preimage,
        )
        if (
            self.semantic_snapshot_digest is not None
            and self.semantic_snapshot_digest != expected_digest
        ):
            raise ValueError("semanticSnapshotDigest does not match the full semantic snapshot")
        expected_ref = f"authority-input://{expected_digest}"
        if self.snapshot_ref is not None and self.snapshot_ref != expected_ref:
            raise ValueError("snapshotRef does not bind semanticSnapshotDigest")
        object.__setattr__(self, "semantic_snapshot_digest", expected_digest)
        object.__setattr__(self, "snapshot_ref", expected_ref)
        return self


def bind_normalized_input_to_authority(
    snapshot: NormalizedInputSemanticSnapshot,
    authority: AuthorityContract,
) -> AuthorityContract:
    """Reject an authority contract that names any substituted request component."""

    if type(snapshot) is not NormalizedInputSemanticSnapshot:
        raise TypeError("snapshot must be an exact NormalizedInputSemanticSnapshot")
    if type(authority) is not AuthorityContract:
        raise TypeError("authority must be an exact AuthorityContract")
    validated_snapshot = NormalizedInputSemanticSnapshot.model_validate(snapshot)
    validated_authority = AuthorityContract.model_validate(authority)
    material = validated_snapshot.material
    bindings: tuple[tuple[str, object, object], ...] = (
        (
            "normalizedRequestDigest",
            validated_authority.normalized_request_digest,
            material.normalized_input_digest,
        ),
        ("commandDigest", validated_authority.command_digest, material.command_digest),
        ("argumentsDigest", validated_authority.arguments_digest, material.arguments_digest),
        (
            "workingDirectoryDigest",
            validated_authority.working_directory_digest,
            material.working_directory_digest,
        ),
        ("environmentDigest", validated_authority.environment_digest, material.environment_digest),
        (
            "requestBodyDigest",
            validated_authority.request_body_digest,
            material.request_body_digest,
        ),
        (
            "credentialScopeDigest",
            validated_authority.credential_scope_digest,
            material.credential_scope_digest,
        ),
        ("networkDigest", validated_authority.network_digest, material.network_digest),
        (
            "disclosureDigest",
            validated_authority.disclosure_digest,
            material.disclosure_digest,
        ),
        (
            "workspaceViewBindingDigest",
            validated_authority.workspace_view_binding_digest,
            validated_snapshot.snapshot.workspace_view_binding_digest,
        ),
    )
    for alias, observed, expected in bindings:
        if observed != expected:
            raise ValueError(f"authority {alias} does not match normalized input material")
    return authority


class ExecutionTargetBinding(EnvelopeModel):
    """Attested executable target tied to the declaration and isolation/provider view."""

    schema_id: Literal["magi.execution_target_binding.v1"] = Field(
        default="magi.execution_target_binding.v1",
        alias="schemaId",
    )
    declaration: EffectDeclarationBinding
    executor_id: str = Field(alias="executorId", min_length=1)
    executor_version: str = Field(alias="executorVersion", min_length=1)
    executable_artifact_digest: str = Field(alias="executableArtifactDigest")
    sandbox_profile_digest: str = Field(alias="sandboxProfileDigest")
    provider_id: str | None = Field(default=None, alias="providerId", min_length=1)
    provider_version: str | None = Field(default=None, alias="providerVersion", min_length=1)
    provider_capabilities_digest: str | None = Field(
        default=None,
        alias="providerCapabilitiesDigest",
    )
    attester_id: str = Field(alias="attesterId", min_length=1)
    attestation_evidence_digest: str = Field(alias="attestationEvidenceDigest")
    attested_at: datetime = Field(alias="attestedAt")
    attestation_expires_at: datetime = Field(alias="attestationExpiresAt")
    attestation_subject_digest: str | None = Field(
        default=None,
        alias="attestationSubjectDigest",
    )
    target_digest: str | None = Field(default=None, alias="targetDigest")

    @model_validator(mode="after")
    def _bind_attested_target(self) -> Self:
        if self.executable_artifact_digest != self.declaration.executor_digest:
            raise ValueError("executableArtifactDigest must equal declaration.executorDigest")
        provider_fields = (
            self.provider_id,
            self.provider_version,
            self.provider_capabilities_digest,
        )
        if any(value is not None for value in provider_fields) and not all(
            value is not None for value in provider_fields
        ):
            raise ValueError("execution target provider identity is all-or-none")
        _require_exact_utc(self.attested_at, field_name="attestedAt")
        _require_exact_utc(
            self.attestation_expires_at,
            field_name="attestationExpiresAt",
        )
        if self.attestation_expires_at <= self.attested_at:
            raise ValueError("attestationExpiresAt must be later than attestedAt")

        declaration_digest = self.declaration.effect_declaration_digest
        assert declaration_digest is not None
        subject_preimage = {
            "effectDeclarationDigest": declaration_digest,
            "executorId": self.executor_id,
            "executorVersion": self.executor_version,
            "executableArtifactDigest": self.executable_artifact_digest,
            "sandboxProfileDigest": self.sandbox_profile_digest,
            "providerId": self.provider_id,
            "providerVersion": self.provider_version,
            "providerCapabilitiesDigest": self.provider_capabilities_digest,
        }
        expected_subject = _domain_digest(
            "magi.execution_target_attestation_subject.v1",
            subject_preimage,
        )
        if (
            self.attestation_subject_digest is not None
            and self.attestation_subject_digest != expected_subject
        ):
            raise ValueError("attestationSubjectDigest does not match execution target")
        object.__setattr__(self, "attestation_subject_digest", expected_subject)

        target_preimage = {
            **subject_preimage,
            "attestationSubjectDigest": expected_subject,
            "attesterId": self.attester_id,
            "attestationEvidenceDigest": self.attestation_evidence_digest,
            "attestedAt": self.attested_at.isoformat(),
            "attestationExpiresAt": self.attestation_expires_at.isoformat(),
        }
        expected_target = _domain_digest(
            "magi.execution_target_binding.v1",
            target_preimage,
        )
        if self.target_digest is not None and self.target_digest != expected_target:
            raise ValueError("targetDigest does not match attested execution target")
        object.__setattr__(self, "target_digest", expected_target)
        return self


class ExecutionStartBinding(EnvelopeModel):
    """Cycle-free semantic identity for the exact start authorized by a grant."""

    schema_id: Literal["magi.execution_start_binding.v1"] = Field(
        default="magi.execution_start_binding.v1",
        alias="schemaId",
    )
    start_id: str = Field(alias="startId", min_length=1)
    action_id: str = Field(alias="actionId", min_length=1)
    attempt_id: str = Field(alias="attemptId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    action_intent_digest: str = Field(alias="actionIntentDigest")
    preparation_digest: str = Field(alias="preparationDigest")
    normalized_input_digest: str = Field(alias="normalizedInputDigest")
    semantic_snapshot_digest: str = Field(alias="semanticSnapshotDigest")
    target_digest: str = Field(alias="targetDigest")
    authority_contract_id: str = Field(alias="authorityContractId", min_length=1)
    authority_contract_digest: str = Field(alias="authorityContractDigest")
    fencing_token: int = Field(alias="fencingToken", ge=0, strict=True)
    requested_at: datetime = Field(alias="requestedAt")
    start_nonce_digest: str = Field(alias="startNonceDigest")
    start_digest: str | None = Field(default=None, alias="startDigest")

    @model_validator(mode="after")
    def _derive_exact_start_identity(self) -> Self:
        _require_exact_utc(self.requested_at, field_name="requestedAt")
        preimage = self.model_dump(
            by_alias=True,
            mode="json",
            exclude={"start_digest"},
        )
        expected = _domain_digest("magi.execution_start_binding.v1", preimage)
        if self.start_digest is not None and self.start_digest != expected:
            raise ValueError("startDigest does not match exact execution start")
        object.__setattr__(self, "start_digest", expected)
        return self


class ExecutionGrant(EnvelopeModel):
    """One-use grant for one exact start, byte payload, target, authority, and fence."""

    schema_id: Literal["magi.execution_grant.v1"] = Field(
        default="magi.execution_grant.v1",
        alias="schemaId",
    )
    grant_id: str = Field(alias="grantId", min_length=1)
    grant_nonce_digest: str = Field(alias="grantNonceDigest")
    start: ExecutionStartBinding
    input_snapshot: NormalizedInputSemanticSnapshot = Field(alias="inputSnapshot")
    target: ExecutionTargetBinding
    authority_contract: AuthorityContract = Field(alias="authorityContract")
    issued_at: datetime = Field(alias="issuedAt")
    expires_at: datetime = Field(alias="expiresAt")
    maximum_uses: Literal[1] = Field(default=1, alias="maximumUses")
    grant_digest: str | None = Field(default=None, alias="grantDigest")

    @field_validator("maximum_uses", mode="before")
    @classmethod
    def _require_one_use(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("maximumUses must be the exact integer 1")
        return value

    @model_validator(mode="after")
    def _bind_one_exact_execution(self) -> Self:
        _require_exact_utc(self.issued_at, field_name="issuedAt")
        _require_exact_utc(self.expires_at, field_name="expiresAt")
        if self.expires_at <= self.issued_at:
            raise ValueError("expiresAt must be later than issuedAt")
        if self.start.requested_at > self.issued_at:
            raise ValueError("issuedAt cannot precede the exact execution start request")
        if self.target.attested_at > self.issued_at:
            raise ValueError("execution target attestation must exist before grant issuance")
        if self.expires_at > self.target.attestation_expires_at:
            raise ValueError("grant expires after the execution target attestation")
        if self.issued_at >= self.authority_contract.expires_at:
            raise ValueError("grant was issued after authority expiry")
        if self.expires_at > self.authority_contract.expires_at:
            raise ValueError("grant expires after authority expiry")
        if (
            self.authority_contract.revoked_at is not None
            and self.authority_contract.revoked_at <= self.expires_at
        ):
            raise ValueError("grant overlaps revoked authority")

        bind_normalized_input_to_authority(self.input_snapshot, self.authority_contract)
        authority_digest = canonical_authority_contract_digest(self.authority_contract)
        expected: tuple[tuple[str, object, object], ...] = (
            (
                "effectDeclarationDigest",
                self.target.declaration.effect_declaration_digest,
                self.input_snapshot.declaration.effect_declaration_digest,
            ),
            ("actionId", self.start.action_id, self.authority_contract.action_id),
            ("attemptId", self.start.attempt_id, self.authority_contract.attempt_id),
            (
                "partitionId",
                self.start.partition_id,
                self.authority_contract.authority_partition_id,
            ),
            (
                "taskContractDigest",
                self.start.task_contract_digest,
                self.authority_contract.task_contract_digest,
            ),
            (
                "normalizedInputDigest",
                self.start.normalized_input_digest,
                self.input_snapshot.material.normalized_input_digest,
            ),
            (
                "semanticSnapshotDigest",
                self.start.semantic_snapshot_digest,
                self.input_snapshot.semantic_snapshot_digest,
            ),
            ("targetDigest", self.start.target_digest, self.target.target_digest),
            (
                "authorityContractId",
                self.start.authority_contract_id,
                self.authority_contract.authority_contract_id,
            ),
            ("authorityContractDigest", self.start.authority_contract_digest, authority_digest),
            ("fencingToken", self.start.fencing_token, self.authority_contract.fencing_token),
            (
                "sandboxProfileDigest",
                self.target.sandbox_profile_digest,
                self.authority_contract.sandbox_profile_digest,
            ),
        )
        for alias, observed, bound in expected:
            if observed != bound:
                raise ValueError(f"ExecutionGrant {alias} does not match its exact binding")

        preimage = self.model_dump(
            by_alias=True,
            mode="json",
            exclude={"grant_digest"},
        )
        expected_digest = _domain_digest("magi.execution_grant.v1", preimage)
        if self.grant_digest is not None and self.grant_digest != expected_digest:
            raise ValueError("grantDigest does not match exact execution grant")
        object.__setattr__(self, "grant_digest", expected_digest)
        return self


def validate_execution_grant(
    grant: ExecutionGrant,
    *,
    expected_start: ExecutionStartBinding,
    expected_input_snapshot: NormalizedInputSemanticSnapshot,
    expected_target: ExecutionTargetBinding,
    expected_authority: AuthorityContract,
    at: datetime,
) -> ExecutionGrant:
    """Revalidate a grant and reject substitution or use outside its time window."""

    exact_types: tuple[tuple[str, object, type[object]], ...] = (
        ("grant", grant, ExecutionGrant),
        ("expected_start", expected_start, ExecutionStartBinding),
        ("expected_input_snapshot", expected_input_snapshot, NormalizedInputSemanticSnapshot),
        ("expected_target", expected_target, ExecutionTargetBinding),
        ("expected_authority", expected_authority, AuthorityContract),
    )
    for name, value, expected_type in exact_types:
        if type(value) is not expected_type:
            raise TypeError(f"{name} must be an exact {expected_type.__name__}")
    _require_exact_utc(at, field_name="at")

    validated = ExecutionGrant.model_validate(grant)
    comparisons = (
        ("start", validated.start.start_digest, expected_start.start_digest),
        (
            "input snapshot",
            validated.input_snapshot.semantic_snapshot_digest,
            expected_input_snapshot.semantic_snapshot_digest,
        ),
        ("target", validated.target.target_digest, expected_target.target_digest),
        (
            "authority",
            canonical_authority_contract_digest(validated.authority_contract),
            canonical_authority_contract_digest(expected_authority),
        ),
    )
    for label, observed, expected in comparisons:
        if observed != expected:
            raise ValueError(f"ExecutionGrant {label} binding mismatch")
    if at < validated.issued_at:
        raise ValueError("ExecutionGrant is not yet valid")
    if at >= validated.expires_at:
        raise ValueError("ExecutionGrant expired")
    return grant


def consume_execution_grant(
    grant: ExecutionGrant,
    *,
    at: datetime,
    prior_uses: int,
) -> ExecutionGrant:
    """Validate one consumption; durable compare-and-swap remains a runtime duty."""

    if type(prior_uses) is not int or prior_uses < 0:
        raise TypeError("prior_uses must be a non-negative exact integer")
    if prior_uses != 0:
        raise ValueError("ExecutionGrant was already consumed")
    return validate_execution_grant(
        grant,
        expected_start=grant.start,
        expected_input_snapshot=grant.input_snapshot,
        expected_target=grant.target,
        expected_authority=grant.authority_contract,
        at=at,
    )


__all__ = [
    "ExactByteMaterial",
    "ExecutionGrant",
    "ExecutionStartBinding",
    "ExecutionTargetBinding",
    "NormalizedInputMaterial",
    "NormalizedInputSemanticSnapshot",
    "bind_normalized_input_to_authority",
    "consume_execution_grant",
    "validate_execution_grant",
]
