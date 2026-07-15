"""Dormant universal effect-admission broker.

The module is intentionally not attached to any runtime route.  It defines the
fail-closed orchestration seam used by later storage and executor adapters.
"""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
import hmac
import json
import secrets
from threading import Lock
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.execution_authority.contracts import (
    AuthorityCapability,
    AuthorityContract,
    AuthorityDecision,
    canonical_authority_contract_digest,
)
from magi_agent.execution_authority.envelopes import (
    ActionIntent,
    ActionProposal,
    BackendObservation,
    EffectDeclarationBinding,
    NormalizedInputSnapshot,
    canonical_action_intent_digest,
    canonical_action_proposal_digest,
    canonical_backend_observation_digest,
    canonical_resource_refs_digest,
)
from magi_agent.execution_authority.state_machine import EvidenceKind
from magi_agent.ops.safety import canonical_digest, require_digest


class BrokerError(RuntimeError):
    """Base class for fail-closed broker failures."""


class DuplicateEffectRegistration(BrokerError):
    """Raised when an exact effect name is registered more than once."""


class UndeclaredEffect(BrokerError):
    """Raised when a request names no reviewed effect declaration."""


class InvalidExecutionToken(BrokerError):
    """Raised before handoff when an execution token is not exact and live."""


class EffectRegistry:
    """Startup-time registry of immutable, reviewed effect declarations."""

    def __init__(
        self,
        declarations: Iterable[EffectDeclarationBinding] = (),
    ) -> None:
        self._declarations: dict[str, EffectDeclarationBinding] = {}
        for declaration in declarations:
            self.register(declaration)

    def register(self, declaration: EffectDeclarationBinding) -> None:
        if type(declaration) is not EffectDeclarationBinding:
            raise TypeError("effect declarations must be exact EffectDeclarationBinding values")
        validated = EffectDeclarationBinding.model_validate(
            declaration.model_dump(by_alias=True, mode="python")
        )
        if validated.effect_name in self._declarations:
            raise DuplicateEffectRegistration(validated.effect_name)
        self._declarations[validated.effect_name] = validated

    def require(self, effect_name: str) -> EffectDeclarationBinding:
        if type(effect_name) is not str or not effect_name:
            raise UndeclaredEffect("effect name must be a nonempty exact string")
        try:
            return self._declarations[effect_name]
        except KeyError as exc:
            raise UndeclaredEffect(effect_name) from exc

    def snapshot(self) -> Mapping[str, EffectDeclarationBinding]:
        return MappingProxyType(dict(self._declarations))


class ExecutionTokenClaims(BaseModel):
    """Authenticated, exact-request claims carried only to executor handoff."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1, le=1)
    action_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    request_digest: str
    authority_digest: str
    fencing_token: int = Field(ge=0)
    expires_unix_ms: int = Field(ge=0)
    executor_digest: str
    precondition_digest: str
    nonce: str = Field(min_length=1)

    @field_validator(
        "request_digest",
        "authority_digest",
        "executor_digest",
        "precondition_digest",
    )
    @classmethod
    def _require_digest(cls, value: str) -> str:
        return require_digest(value)


def _b64encode(value: bytes) -> str:
    return urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    if type(value) is not str or not value:
        raise InvalidExecutionToken("execution token segment is invalid")
    try:
        return urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise InvalidExecutionToken("execution token encoding is invalid") from exc


class ExecutionTokenIssuer:
    """HMAC issuer with an atomic in-process single-use handoff ledger.

    Durable single-use authority consumption belongs to the injected journal
    adapter.  This ledger independently prevents a token from being handed to
    an executor twice within one broker process.
    """

    def __init__(
        self,
        *,
        key: bytes,
        nonce_factory: Callable[[], bytes] = lambda: secrets.token_bytes(16),
    ) -> None:
        if type(key) is not bytes or len(key) < 32:
            raise ValueError("execution token key must be at least 32 exact bytes")
        if not callable(nonce_factory):
            raise TypeError("nonce_factory must be callable")
        self._key = key
        self._nonce_factory = nonce_factory
        self._consumed_nonces: set[str] = set()
        self._consume_lock = Lock()

    def issue(
        self,
        *,
        action_id: str,
        attempt_id: str,
        request_digest: str,
        authority_digest: str,
        fencing_token: int,
        expires_unix_ms: int,
        executor_digest: str,
        precondition_digest: str,
    ) -> str:
        nonce_bytes = self._nonce_factory()
        if type(nonce_bytes) is not bytes or len(nonce_bytes) < 16:
            raise ValueError("nonce_factory must return at least 16 exact bytes")
        claims = ExecutionTokenClaims(
            version=1,
            action_id=action_id,
            attempt_id=attempt_id,
            request_digest=request_digest,
            authority_digest=authority_digest,
            fencing_token=fencing_token,
            expires_unix_ms=expires_unix_ms,
            executor_digest=executor_digest,
            precondition_digest=precondition_digest,
            nonce=_b64encode(nonce_bytes),
        )
        payload = json.dumps(
            claims.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        signature = hmac.digest(self._key, payload, "sha256")
        return f"{_b64encode(payload)}.{_b64encode(signature)}"

    def verify(
        self,
        token: str,
        *,
        action_id: str,
        attempt_id: str,
        request_digest: str,
        authority_digest: str,
        fencing_token: int,
        now_unix_ms: int,
        executor_digest: str,
        precondition_digest: str,
    ) -> ExecutionTokenClaims:
        claims = self._decode_and_authenticate(token)
        expected = (
            ("actionId", claims.action_id, action_id),
            ("attemptId", claims.attempt_id, attempt_id),
            ("requestDigest", claims.request_digest, request_digest),
            ("authorityDigest", claims.authority_digest, authority_digest),
            ("fencingToken", claims.fencing_token, fencing_token),
            ("executorDigest", claims.executor_digest, executor_digest),
            (
                "preconditionDigest",
                claims.precondition_digest,
                precondition_digest,
            ),
        )
        if any(observed != wanted for _, observed, wanted in expected):
            raise InvalidExecutionToken("execution token binding mismatch")
        if type(now_unix_ms) is not int or type(now_unix_ms) is bool:
            raise InvalidExecutionToken("execution token clock is invalid")
        if now_unix_ms > claims.expires_unix_ms:
            raise InvalidExecutionToken("execution token expired")
        return claims

    def consume(self, token: str, **expected: Any) -> ExecutionTokenClaims:
        claims = self.verify(token, **expected)
        with self._consume_lock:
            if claims.nonce in self._consumed_nonces:
                raise InvalidExecutionToken("execution token already consumed")
            self._consumed_nonces.add(claims.nonce)
        return claims

    def token_digest(self, token: str) -> str:
        if type(token) is not str:
            raise TypeError("execution token must be an exact string")
        return "sha256:" + sha256(token.encode("ascii")).hexdigest()

    def _decode_and_authenticate(self, token: str) -> ExecutionTokenClaims:
        if type(token) is not str:
            raise InvalidExecutionToken("execution token must be an exact string")
        try:
            encoded_payload, encoded_signature = token.split(".")
        except ValueError as exc:
            raise InvalidExecutionToken("execution token shape is invalid") from exc
        payload = _b64decode(encoded_payload)
        signature = _b64decode(encoded_signature)
        expected_signature = hmac.digest(self._key, payload, "sha256")
        if not hmac.compare_digest(signature, expected_signature):
            raise InvalidExecutionToken("execution token signature is invalid")
        try:
            raw = json.loads(payload, parse_constant=lambda value: None)
            claims = ExecutionTokenClaims.model_validate(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise InvalidExecutionToken("execution token claims are invalid") from exc
        return claims


class _BrokerModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BrokerMismatch(BrokerError):
    """Raised before executor handoff when any upstream binding disagrees."""


class BrokerDisposition(StrEnum):
    DENIED = "denied"
    OBSERVED = "observed"


class ActionProposalContext(_BrokerModel):
    effect_name: str
    action_id: str
    attempt_id: str
    partition_id: str
    actor_id: str
    tenant_id: str
    identity_digest: str
    policy_digest: str
    session_id: str
    turn_id: str
    run_id: str
    task_contract_id: str
    task_version: int
    task_contract_digest: str
    completion_epoch_id: str
    capabilities: tuple[AuthorityCapability, ...]
    evidence_obligations: tuple[EvidenceKind, ...]


class NormalizedRequestMaterial(_BrokerModel):
    effect_declaration_digest: str
    normalizer_digest: str
    payload: bytes
    normalized_input_digest: str

    @classmethod
    def from_payload(
        cls, *, effect_declaration_digest: str, normalizer_digest: str, payload: bytes
    ) -> NormalizedRequestMaterial:
        return cls(
            effect_declaration_digest=effect_declaration_digest,
            normalizer_digest=normalizer_digest,
            payload=payload,
            normalized_input_digest="sha256:" + sha256(payload).hexdigest(),
        )


class DerivedResourceSet(_BrokerModel):
    normalized_input_digest: str
    effect_declaration_digest: str
    resource_deriver_digest: str
    read_set: tuple[str, ...]
    absence_set: tuple[str, ...]
    write_set: tuple[str, ...]
    egress_set: tuple[str, ...]
    read_set_digest: str
    absence_set_digest: str
    write_set_digest: str
    egress_set_digest: str
    workspace_view_binding_digest: str | None
    idempotency_key_digest: str

    @classmethod
    def create(
        cls,
        *,
        material: NormalizedRequestMaterial,
        declaration: EffectDeclarationBinding,
        resource_deriver_digest: str,
        read_set: tuple[str, ...],
        absence_set: tuple[str, ...],
        write_set: tuple[str, ...],
        egress_set: tuple[str, ...],
        workspace_view_binding_digest: str | None,
        idempotency_key_digest: str,
    ) -> DerivedResourceSet:
        return cls(
            normalized_input_digest=material.normalized_input_digest,
            effect_declaration_digest=declaration.effect_declaration_digest or "",
            resource_deriver_digest=resource_deriver_digest,
            read_set=read_set,
            absence_set=absence_set,
            write_set=write_set,
            egress_set=egress_set,
            read_set_digest=canonical_resource_refs_digest(read_set),
            absence_set_digest=canonical_resource_refs_digest(absence_set),
            write_set_digest=canonical_resource_refs_digest(write_set),
            egress_set_digest=canonical_resource_refs_digest(egress_set),
            workspace_view_binding_digest=workspace_view_binding_digest,
            idempotency_key_digest=idempotency_key_digest,
        )


class MaterialBinding(_BrokerModel):
    snapshot: NormalizedInputSnapshot
    binding_digest: str

    @classmethod
    def create(cls, snapshot: NormalizedInputSnapshot) -> MaterialBinding:
        return cls(
            snapshot=snapshot,
            binding_digest=canonical_digest(snapshot.model_dump(by_alias=True, mode="json")),
        )


class BrokerAdmission(_BrokerModel):
    intent: ActionIntent
    intent_digest: str
    proposal_digest: str
    material_binding_digest: str
    admission_sequence: int
    epoch_compare_version: int
    action_compare_version: int
    attempt_compare_version: int
    partition_compare_version: int

    @classmethod
    def create(
        cls,
        *,
        proposal: ActionProposal,
        material_binding_digest: str,
        admission_sequence: int,
        epoch_compare_version: int,
        action_compare_version: int,
        attempt_compare_version: int,
        partition_compare_version: int,
    ) -> BrokerAdmission:
        payload = proposal.model_dump(by_alias=True, mode="python")
        payload["schemaId"] = "magi.action_intent.v1"
        payload["admissionSequence"] = admission_sequence
        intent = ActionIntent.model_validate(payload)
        return cls(
            intent=intent,
            intent_digest=canonical_action_intent_digest(intent),
            proposal_digest=canonical_action_proposal_digest(proposal),
            material_binding_digest=material_binding_digest,
            admission_sequence=admission_sequence,
            epoch_compare_version=epoch_compare_version,
            action_compare_version=action_compare_version,
            attempt_compare_version=attempt_compare_version,
            partition_compare_version=partition_compare_version,
        )


class AuthorizationEvaluation(_BrokerModel):
    intent_digest: str
    policy_digest: str
    decision: AuthorityDecision
    evaluation_digest: str

    @classmethod
    def create(
        cls, *, intent: ActionIntent, policy_digest: str, decision: AuthorityDecision
    ) -> AuthorizationEvaluation:
        intent_digest = canonical_action_intent_digest(intent)
        evaluation_digest = canonical_digest(
            {
                "intentDigest": intent_digest,
                "policyDigest": policy_digest,
                "decision": decision.model_dump(by_alias=True, mode="json"),
            }
        )
        return cls(
            intent_digest=intent_digest,
            policy_digest=policy_digest,
            decision=decision,
            evaluation_digest=evaluation_digest,
        )


class FenceLease(_BrokerModel):
    partition_id: str
    lease_name: str
    owner_id: str
    effect_declaration_digest: str
    fencing_token: int
    compare_version: int


class ResourcePrecondition(_BrokerModel):
    resource_ref: str
    state: str
    identity_digest: str
    content_digest: str | None
    workspace_view_binding_digest: str | None


class PreconditionObservation(_BrokerModel):
    proposal_digest: str
    material_binding_digest: str
    fencing_token: int
    observer_digest: str
    resources: tuple[ResourcePrecondition, ...]
    observation_digest: str

    @classmethod
    def create(
        cls,
        *,
        proposal_digest: str,
        material_binding_digest: str,
        fencing_token: int,
        observer_digest: str,
        resources: tuple[ResourcePrecondition, ...],
    ) -> PreconditionObservation:
        payload = {
            "proposalDigest": proposal_digest,
            "materialBindingDigest": material_binding_digest,
            "fencingToken": fencing_token,
            "observerDigest": observer_digest,
            "resources": [resource.model_dump(mode="json") for resource in resources],
        }
        return cls(
            proposal_digest=proposal_digest,
            material_binding_digest=material_binding_digest,
            fencing_token=fencing_token,
            observer_digest=observer_digest,
            resources=resources,
            observation_digest=canonical_digest(payload),
        )


class ExecutorBinding(_BrokerModel):
    executor_id: str
    executor_version: str
    executor_digest: str
    sandbox_profile_digest: str


class EffectRuntime(_BrokerModel):
    normalizer: Any
    resource_deriver: Any
    executor: Any
    executor_binding: ExecutorBinding


class AuthorityGrantRequest(_BrokerModel):
    context: ActionProposalContext
    admission: BrokerAdmission
    authorization: AuthorizationEvaluation
    fence: FenceLease
    preconditions: PreconditionObservation
    executor: ExecutorBinding
    decision_request: Any | None = None
    resume_binding: Any | None = None
    resume_binding_digest: str | None = None


class PreparedExecution(_BrokerModel):
    grant: AuthorityGrantRequest
    admission: BrokerAdmission
    fence: FenceLease
    authority_contract: AuthorityContract
    authority_contract_digest: str
    precondition_digest: str
    action_compare_version: int
    attempt_compare_version: int
    partition_compare_version: int

    @classmethod
    def create(
        cls,
        *,
        grant: AuthorityGrantRequest,
        authority_contract: AuthorityContract,
        authority_contract_digest: str,
        action_compare_version: int,
        attempt_compare_version: int,
        partition_compare_version: int,
    ) -> PreparedExecution:
        return cls(
            grant=grant,
            admission=grant.admission,
            fence=grant.fence,
            authority_contract=authority_contract,
            authority_contract_digest=authority_contract_digest,
            precondition_digest=grant.preconditions.observation_digest,
            action_compare_version=action_compare_version,
            attempt_compare_version=attempt_compare_version,
            partition_compare_version=partition_compare_version,
        )


class ExecutionStartRecord(_BrokerModel):
    preparation: PreparedExecution
    execution_token_digest: str
    executor: ExecutorBinding
    action_compare_version: int
    attempt_compare_version: int
    partition_compare_version: int

    @classmethod
    def create(
        cls,
        *,
        preparation: PreparedExecution,
        execution_token_digest: str,
        executor: ExecutorBinding,
        action_compare_version: int,
        attempt_compare_version: int,
        partition_compare_version: int,
    ) -> ExecutionStartRecord:
        return cls(
            preparation=preparation,
            execution_token_digest=execution_token_digest,
            executor=executor,
            action_compare_version=action_compare_version,
            attempt_compare_version=attempt_compare_version,
            partition_compare_version=partition_compare_version,
        )


class ExecutorHandoff(_BrokerModel):
    start: ExecutionStartRecord
    execution_token: str


class BrokerResult(_BrokerModel):
    disposition: BrokerDisposition
    observation: BackendObservation | None = None
    observation_digest: str | None = None


class UniversalMutationBroker:
    """Fail-closed, sole ordered ingress for dormant governed effects."""

    def __init__(
        self,
        *,
        registry: EffectRegistry,
        runtimes: Mapping[str, EffectRuntime],
        material_store: Any,
        authority: Any,
        journal: Any,
        fence: Any,
        precondition_observer: Any,
        contract_issuer: Any,
        clock: Any,
        token_issuer_key: bytes,
        token_ttl_ms: int,
    ) -> None:
        self._registry = registry
        self._runtimes = dict(runtimes)
        self._material_store = material_store
        self._authority = authority
        self._journal = journal
        self._fence = fence
        self._observer = precondition_observer
        self._contract_issuer = contract_issuer
        self._clock = clock
        self._token_issuer = ExecutionTokenIssuer(key=token_issuer_key)
        self._token_ttl_ms = token_ttl_ms

    async def execute(
        self, *, context: ActionProposalContext, untrusted_request: dict[str, object]
    ) -> BrokerResult:
        declaration = self._registry.require(context.effect_name)
        runtime = self._runtimes.get(context.effect_name)
        if runtime is None:
            raise UndeclaredEffect(context.effect_name)
        material = runtime.normalizer.normalize(
            untrusted_request=untrusted_request, declaration=declaration
        )
        if (
            material.effect_declaration_digest != declaration.effect_declaration_digest
            or material.normalizer_digest != declaration.normalizer_digest
            or material.normalized_input_digest != "sha256:" + sha256(material.payload).hexdigest()
        ):
            raise BrokerMismatch("normalized material binding mismatch")
        resources = runtime.resource_deriver.derive(material=material, declaration=declaration)
        if (
            resources.normalized_input_digest != material.normalized_input_digest
            or resources.effect_declaration_digest != declaration.effect_declaration_digest
            or resources.resource_deriver_digest != declaration.resource_deriver_digest
        ):
            raise BrokerMismatch("derived resource binding mismatch")
        snapshot = self._material_store.persist(material=material, resources=resources)
        self._require_snapshot(snapshot, material, resources)
        material_binding = MaterialBinding.create(snapshot)
        proposal = ActionProposal(
            actionId=context.action_id,
            attemptId=context.attempt_id,
            partitionId=context.partition_id,
            actorId=context.actor_id,
            identityDigest=context.identity_digest,
            policyDigest=context.policy_digest,
            sessionId=context.session_id,
            turnId=context.turn_id,
            runId=context.run_id,
            taskContractId=context.task_contract_id,
            taskVersion=context.task_version,
            taskContractDigest=context.task_contract_digest,
            completionEpochId=context.completion_epoch_id,
            declaration=declaration,
            capabilities=context.capabilities,
            normalizedInputDigest=material.normalized_input_digest,
            normalizedRequestSnapshotRef=snapshot.snapshot_ref,
            readSet=resources.read_set,
            absenceSet=resources.absence_set,
            writeSet=resources.write_set,
            egressSet=resources.egress_set,
            readSetDigest=resources.read_set_digest,
            absenceSetDigest=resources.absence_set_digest,
            writeSetDigest=resources.write_set_digest,
            egressSetDigest=resources.egress_set_digest,
            workspaceViewBindingDigest=resources.workspace_view_binding_digest,
            idempotencyKeyDigest=resources.idempotency_key_digest,
            evidenceObligations=context.evidence_obligations,
        )
        admission = self._journal.admit(proposal=proposal, material_binding=material_binding)
        if admission.proposal_digest != canonical_action_proposal_digest(proposal):
            raise BrokerMismatch("admission proposal mismatch")
        authorization = self._authority.authorize(admission.intent)
        if (
            authorization.intent_digest != canonical_action_intent_digest(admission.intent)
            or authorization.policy_digest != context.policy_digest
        ):
            raise BrokerMismatch("authority evaluation mismatch")
        if authorization.decision.status == "deny":
            self._journal.deny(admission=admission, authorization=authorization)
            return BrokerResult(disposition=BrokerDisposition.DENIED)
        if authorization.decision.capabilities != admission.intent.capabilities:
            raise BrokerMismatch("authority capabilities mismatch")
        lease = self._fence.acquire(admission=admission, declaration=declaration)
        if (
            lease.partition_id != context.partition_id
            or lease.effect_declaration_digest != declaration.effect_declaration_digest
        ):
            raise BrokerMismatch("fence binding mismatch")
        preconditions = self._observer.observe(
            admission=admission, material_binding=material_binding, fence=lease
        )
        expected_refs = {*resources.read_set, *resources.absence_set}
        if (
            preconditions.proposal_digest != admission.proposal_digest
            or preconditions.fencing_token != lease.fencing_token
            or {item.resource_ref for item in preconditions.resources} != expected_refs
        ):
            raise BrokerMismatch("precondition coverage mismatch")
        grant = AuthorityGrantRequest(
            context=context,
            admission=admission,
            authorization=authorization,
            fence=lease,
            preconditions=preconditions,
            executor=runtime.executor_binding,
        )
        authority_contract = self._contract_issuer.issue(grant)
        if (
            authority_contract.normalized_request_digest != material.normalized_input_digest
            or authority_contract.fencing_token != lease.fencing_token
            or authority_contract.capabilities != admission.intent.capabilities
        ):
            raise BrokerMismatch("authority contract mismatch")
        authority_digest = canonical_authority_contract_digest(authority_contract)
        prepared = self._journal.consume_authority_and_prepare(
            grant=grant,
            authority_contract=authority_contract,
            authority_contract_digest=authority_digest,
        )
        if prepared.precondition_digest != preconditions.observation_digest:
            raise BrokerMismatch("preparation binding mismatch")
        now_ms = self._clock.now_unix_ms()
        token = self._token_issuer.issue(
            action_id=context.action_id,
            attempt_id=context.attempt_id,
            request_digest=material.normalized_input_digest,
            authority_digest=authority_digest,
            fencing_token=lease.fencing_token,
            expires_unix_ms=now_ms + self._token_ttl_ms,
            executor_digest=runtime.executor_binding.executor_digest,
            precondition_digest=preconditions.observation_digest,
        )
        token_digest = self._token_issuer.token_digest(token)
        start = self._journal.mark_executing(
            preparation=prepared,
            execution_token_digest=token_digest,
            executor=runtime.executor_binding,
        )
        if start.execution_token_digest != token_digest or start.executor != runtime.executor_binding:
            raise BrokerMismatch("execution start mismatch")
        self._token_issuer.consume(
            token,
            action_id=context.action_id,
            attempt_id=context.attempt_id,
            request_digest=material.normalized_input_digest,
            authority_digest=authority_digest,
            fencing_token=lease.fencing_token,
            now_unix_ms=now_ms,
            executor_digest=runtime.executor_binding.executor_digest,
            precondition_digest=preconditions.observation_digest,
        )
        observation = await runtime.executor.execute(
            ExecutorHandoff(start=start, execution_token=token)
        )
        recorded = self._journal.record_observation(start=start, observation=observation)
        return BrokerResult(
            disposition=BrokerDisposition.OBSERVED,
            observation=recorded,
            observation_digest=canonical_backend_observation_digest(recorded),
        )

    @staticmethod
    def _require_snapshot(
        snapshot: NormalizedInputSnapshot,
        material: NormalizedRequestMaterial,
        resources: DerivedResourceSet,
    ) -> None:
        if (
            snapshot.normalized_input_digest != material.normalized_input_digest
            or snapshot.normalizer_digest != material.normalizer_digest
            or snapshot.resource_deriver_digest != resources.resource_deriver_digest
            or snapshot.read_set_digest != resources.read_set_digest
            or snapshot.write_set_digest != resources.write_set_digest
        ):
            raise BrokerMismatch("persisted material binding mismatch")

__all__ = [
    "ActionProposalContext",
    "AuthorizationEvaluation",
    "AuthorityGrantRequest",
    "BrokerAdmission",
    "BrokerDisposition",
    "BrokerError",
    "BrokerMismatch",
    "BrokerResult",
    "DerivedResourceSet",
    "DuplicateEffectRegistration",
    "EffectRegistry",
    "EffectRuntime",
    "ExecutionStartRecord",
    "ExecutionTokenClaims",
    "ExecutionTokenIssuer",
    "ExecutorBinding",
    "ExecutorHandoff",
    "FenceLease",
    "InvalidExecutionToken",
    "MaterialBinding",
    "NormalizedRequestMaterial",
    "PreconditionObservation",
    "PreparedExecution",
    "ResourcePrecondition",
    "UniversalMutationBroker",
    "UndeclaredEffect",
]
