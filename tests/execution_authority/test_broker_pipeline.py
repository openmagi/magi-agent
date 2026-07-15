from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from magi_agent.execution_authority.broker import (
    ActionProposalContext,
    AuthorizationEvaluation,
    AuthorityGrantRequest,
    BrokerAdmission,
    BrokerDisposition,
    BrokerMismatch,
    DerivedResourceSet,
    EffectRegistry,
    EffectRuntime,
    ExecutionStartRecord,
    ExecutorBinding,
    ExecutorHandoff,
    FenceLease,
    NormalizedRequestMaterial,
    PreconditionObservation,
    PreparedExecution,
    ResourcePrecondition,
    UniversalMutationBroker,
)
from magi_agent.execution_authority.contracts import (
    AuthorityCapability,
    AuthorityContract,
    AuthorityDecision,
    canonical_authority_contract_digest,
)
from magi_agent.execution_authority.envelopes import (
    ActionIntent,
    BackendObservation,
    EffectDeclarationBinding,
    NormalizedInputSnapshot,
    canonical_backend_observation_digest,
    canonical_provider_guarantees_digest,
)
from magi_agent.execution_authority.state_machine import (
    AttemptKind,
    EffectClass,
    EvidenceKind,
    IdempotencyCapability,
    ObservationOutcome,
    ProviderGuarantee,
    RecoveryStrategy,
    ResourceSemantics,
    TransmissionState,
)


NOW_MS = 1_800_000_000_000
NOW = datetime.fromtimestamp(NOW_MS / 1_000, tz=UTC)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _resource(path: str = "a.txt") -> str:
    return f"workspace://sha256:{'a' * 64}/{path}"


def _declaration() -> EffectDeclarationBinding:
    guarantees = (ProviderGuarantee.LOCAL_ATOMIC,)
    return EffectDeclarationBinding(
        effectName="FileWrite",
        effectClass=EffectClass.WORKSPACE_WRITE,
        resourceSemantics=ResourceSemantics.WORKSPACE_TRANSACTION,
        handlerDigest=_digest("1"),
        normalizerDigest=_digest("2"),
        resourceDeriverDigest=_digest("3"),
        executorDigest=_digest("4"),
        recoveryAdapterDigest=_digest("5"),
        providerGuaranteesDigest=canonical_provider_guarantees_digest(guarantees),
        providerGuarantees=guarantees,
        idempotencyCapability=IdempotencyCapability.LOCAL_GENERATION_CAS,
        recoveryStrategy=RecoveryStrategy.WORKSPACE_TRANSACTION,
    )


def _capability() -> AuthorityCapability:
    return AuthorityCapability(
        effectClass=EffectClass.WORKSPACE_WRITE,
        resourceRef=_resource(),
        networkRefs=(),
        credentialRefs=(),
        workspaceViewBindingDigest=_digest("b"),
    )


def _context() -> ActionProposalContext:
    return ActionProposalContext(
        effect_name="FileWrite",
        action_id="action_1",
        attempt_id="attempt_1",
        partition_id="partition_1",
        actor_id="actor_1",
        tenant_id="tenant_1",
        identity_digest=_digest("c"),
        policy_digest=_digest("d"),
        session_id="session_1",
        turn_id="turn_1",
        run_id="run_1",
        task_contract_id="task_1",
        task_version=2,
        task_contract_digest=_digest("e"),
        completion_epoch_id="epoch_1",
        capabilities=(_capability(),),
        evidence_obligations=(
            EvidenceKind.ACTION_RECEIPT,
            EvidenceKind.WORKSPACE_POSTCONDITION,
        ),
    )


class _Clock:
    def now_unix_ms(self) -> int:
        return NOW_MS


class _Normalizer:
    def __init__(self, declaration: EffectDeclarationBinding, log: list[str]) -> None:
        self.normalizer_digest = declaration.normalizer_digest
        self.log = log
        self.tamper = False

    def normalize(
        self,
        *,
        untrusted_request: dict[str, object],
        declaration: EffectDeclarationBinding,
    ) -> NormalizedRequestMaterial:
        self.log.append("normalize")
        material = NormalizedRequestMaterial.from_payload(
            effect_declaration_digest=declaration.effect_declaration_digest or "",
            normalizer_digest=self.normalizer_digest,
            payload=b'{"path":"a.txt","text":"next"}',
        )
        if not self.tamper:
            return material
        return material.model_copy(update={"normalized_input_digest": _digest("f")})


class _Deriver:
    def __init__(self, declaration: EffectDeclarationBinding, log: list[str]) -> None:
        self.resource_deriver_digest = declaration.resource_deriver_digest
        self.log = log
        self.tamper = False

    def derive(
        self,
        *,
        material: NormalizedRequestMaterial,
        declaration: EffectDeclarationBinding,
    ) -> DerivedResourceSet:
        self.log.append("derive")
        derived = DerivedResourceSet.create(
            material=material,
            declaration=declaration,
            resource_deriver_digest=self.resource_deriver_digest,
            read_set=(_resource(),),
            absence_set=(),
            write_set=(_resource(),),
            egress_set=(),
            workspace_view_binding_digest=_digest("b"),
            idempotency_key_digest=_digest("9"),
        )
        if not self.tamper:
            return derived
        return derived.model_copy(update={"normalized_input_digest": _digest("f")})


class _MaterialStore:
    def __init__(self, declaration: EffectDeclarationBinding, log: list[str]) -> None:
        self.declaration = declaration
        self.log = log
        self.snapshot_tamper = False
        self.snapshot: NormalizedInputSnapshot | None = None

    def persist(
        self,
        *,
        material: NormalizedRequestMaterial,
        resources: DerivedResourceSet,
    ) -> NormalizedInputSnapshot:
        self.log.append("persist")
        snapshot = NormalizedInputSnapshot(
            effectDeclarationDigest=material.effect_declaration_digest,
            normalizedInputDigest=material.normalized_input_digest,
            normalizedPayloadRef=(
                f"authority-input-payload://{material.normalized_input_digest}"
            ),
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
            snapshotRef=f"authority-input://{material.normalized_input_digest}",
            normalizerDigest=material.normalizer_digest,
            resourceDeriverDigest=resources.resource_deriver_digest,
            storedAt=NOW,
            compareVersion=1,
        )
        if self.snapshot_tamper:
            payload = snapshot.model_dump(by_alias=True, mode="python")
            payload["normalizerDigest"] = _digest("f")
            snapshot = NormalizedInputSnapshot.model_validate(payload)
        self.snapshot = snapshot
        return snapshot

    def resolve(
        self,
        *,
        snapshot_ref: str,
        expected_normalized_input_digest: str,
    ) -> NormalizedInputSnapshot:
        self.log.append("resolve_material")
        assert self.snapshot is not None
        return self.snapshot


class _Authority:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.status = "allow"
        self.capabilities = (_capability(),)
        self.policy_digest = _digest("d")

    def authorize(self, intent: ActionIntent) -> AuthorizationEvaluation:
        self.log.append("authorize")
        capabilities = () if self.status == "deny" else self.capabilities
        decision = AuthorityDecision(
            status=self.status,
            reasonCodes=("test",),
            capabilities=capabilities,
        )
        return AuthorizationEvaluation.create(
            intent=intent,
            policy_digest=self.policy_digest,
            decision=decision,
        )


class _Fence:
    def __init__(self, declaration: EffectDeclarationBinding, log: list[str]) -> None:
        self.declaration = declaration
        self.log = log
        self.tamper = False

    def acquire(
        self,
        *,
        admission: BrokerAdmission,
        declaration: EffectDeclarationBinding,
    ) -> FenceLease:
        self.log.append("fence")
        return FenceLease(
            partition_id=("wrong" if self.tamper else admission.intent.partition_id),
            lease_name="effect:FileWrite",
            owner_id="broker_1",
            effect_declaration_digest=declaration.effect_declaration_digest or "",
            fencing_token=8,
            compare_version=1,
        )


class _Observer:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.omit = False

    def observe(
        self,
        *,
        admission: BrokerAdmission,
        material_binding: Any,
        fence: FenceLease,
    ) -> PreconditionObservation:
        self.log.append("observe")
        resources = () if self.omit else (
            ResourcePrecondition(
                resource_ref=_resource(),
                state="present",
                identity_digest=_digest("6"),
                content_digest=_digest("7"),
                workspace_view_binding_digest=_digest("b"),
            ),
        )
        return PreconditionObservation.create(
            proposal_digest=admission.proposal_digest,
            material_binding_digest=material_binding.binding_digest,
            fencing_token=fence.fencing_token,
            observer_digest=_digest("8"),
            resources=resources,
        )


class _ContractIssuer:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.tamper = False

    def issue(self, request: AuthorityGrantRequest) -> AuthorityContract:
        self.log.append("issue_contract")
        intent = request.admission.intent
        resume = request.resume_binding
        return AuthorityContract(
            authorityContractId="authority_1",
            issuerId="test_issuer",
            principalId=intent.actor_id,
            tenantId=request.context.tenant_id,
            sessionId=intent.session_id,
            turnId=intent.turn_id,
            taskContractId=intent.task_contract_id,
            taskVersion=intent.task_version,
            taskContractDigest=intent.task_contract_digest,
            completionEpochId=intent.completion_epoch_id,
            authorityPartitionId=intent.partition_id,
            actionId=intent.action_id,
            attemptId=intent.attempt_id,
            policyDigest=intent.policy_digest,
            normalizedRequestDigest=(
                _digest("f") if self.tamper else intent.normalized_input_digest
            ),
            argumentsDigest=_digest("0"),
            workingDirectoryDigest=_digest("1"),
            environmentDigest=_digest("2"),
            disclosureDigest=_digest("3"),
            capabilities=intent.capabilities,
            workspaceViewBindingDigest=intent.workspace_view_binding_digest,
            sandboxProfileDigest=request.executor.sandbox_profile_digest,
            guardianCeilingDigest=_digest("4"),
            expiresAt=NOW + timedelta(minutes=5),
            fencingToken=request.fence.fencing_token,
            decisionRequestId=(
                request.decision_request.decision_request_id
                if request.decision_request is not None
                else None
            ),
            resumeBindingDigest=(
                request.resume_binding_digest if resume is not None else None
            ),
            delegationChain=(),
        )


class _Journal:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.admission_tamper = False
        self.preparation_tamper = False
        self.start_tamper = False

    def admit(self, *, proposal: Any, material_binding: Any) -> BrokerAdmission:
        self.log.append("admit")
        admission = BrokerAdmission.create(
            proposal=proposal,
            material_binding_digest=material_binding.binding_digest,
            admission_sequence=3,
            epoch_compare_version=2,
            action_compare_version=1,
            attempt_compare_version=1,
            partition_compare_version=1,
        )
        if self.admission_tamper:
            return admission.model_copy(update={"proposal_digest": _digest("f")})
        return admission

    def deny(
        self,
        *,
        admission: BrokerAdmission,
        authorization: AuthorizationEvaluation,
    ) -> None:
        self.log.append("deny")

    def request_user_decision(self, **kwargs: object) -> Any:
        raise AssertionError("not used by ordinary path tests")

    def consume_authority_and_prepare(
        self,
        *,
        grant: AuthorityGrantRequest,
        authority_contract: AuthorityContract,
        authority_contract_digest: str,
    ) -> PreparedExecution:
        self.log.append("prepare")
        prepared = PreparedExecution.create(
            grant=grant,
            authority_contract=authority_contract,
            authority_contract_digest=authority_contract_digest,
            action_compare_version=2,
            attempt_compare_version=2,
            partition_compare_version=2,
        )
        if self.preparation_tamper:
            return prepared.model_copy(update={"precondition_digest": _digest("f")})
        return prepared

    def mark_executing(
        self,
        *,
        preparation: PreparedExecution,
        execution_token_digest: str,
        executor: ExecutorBinding,
    ) -> ExecutionStartRecord:
        self.log.append("mark_executing")
        start = ExecutionStartRecord.create(
            preparation=preparation,
            execution_token_digest=execution_token_digest,
            executor=executor,
            action_compare_version=3,
            attempt_compare_version=3,
            partition_compare_version=3,
        )
        if self.start_tamper:
            return start.model_copy(update={"execution_token_digest": _digest("f")})
        return start

    def record_observation(
        self,
        *,
        start: ExecutionStartRecord,
        observation: BackendObservation,
    ) -> BackendObservation:
        self.log.append("record_observation")
        return observation


class _Executor:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.calls = 0

    async def execute(self, handoff: ExecutorHandoff) -> BackendObservation:
        self.log.append("executor")
        self.calls += 1
        start = handoff.start
        prepared = start.preparation
        intent = prepared.admission.intent
        return BackendObservation(
            actionId=intent.action_id,
            attemptId=intent.attempt_id,
            partitionId=intent.partition_id,
            taskContractDigest=intent.task_contract_digest,
            actionIntentDigest=prepared.admission.intent_digest,
            requestDigest=intent.normalized_input_digest,
            authorityDigest=prepared.authority_contract_digest,
            fencingToken=prepared.fence.fencing_token,
            executorId=start.executor.executor_id,
            executorVersion=start.executor.executor_version,
            sandboxProfileDigest=start.executor.sandbox_profile_digest,
            attemptKind=AttemptKind.EXECUTION,
            effectMayHaveStarted=True,
            observedOutcome=ObservationOutcome.COMMITTED,
            transmissionState=TransmissionState.PROVEN_NOT_SENT,
            observedEffectRefs=(_resource(),),
            reasonCodes=("committed",),
        )


class _Rig:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.declaration = _declaration()
        self.normalizer = _Normalizer(self.declaration, self.log)
        self.deriver = _Deriver(self.declaration, self.log)
        self.material_store = _MaterialStore(self.declaration, self.log)
        self.authority = _Authority(self.log)
        self.fence = _Fence(self.declaration, self.log)
        self.observer = _Observer(self.log)
        self.contract_issuer = _ContractIssuer(self.log)
        self.journal = _Journal(self.log)
        self.executor = _Executor(self.log)
        executor_binding = ExecutorBinding(
            executor_id="workspace_writer",
            executor_version="1.0.0",
            executor_digest=self.declaration.executor_digest,
            sandbox_profile_digest=_digest("a"),
        )
        runtime = EffectRuntime(
            normalizer=self.normalizer,
            resource_deriver=self.deriver,
            executor=self.executor,
            executor_binding=executor_binding,
        )
        self.broker = UniversalMutationBroker(
            registry=EffectRegistry((self.declaration,)),
            runtimes={"FileWrite": runtime},
            material_store=self.material_store,
            authority=self.authority,
            journal=self.journal,
            fence=self.fence,
            precondition_observer=self.observer,
            contract_issuer=self.contract_issuer,
            clock=_Clock(),
            token_issuer_key=b"k" * 32,
            token_ttl_ms=30_000,
        )


@pytest.mark.asyncio
async def test_broker_orders_exact_admission_before_single_executor_handoff() -> None:
    rig = _Rig()

    result = await rig.broker.execute(
        context=_context(),
        untrusted_request={"path": "a.txt", "text": "next"},
    )

    assert result.disposition is BrokerDisposition.OBSERVED
    assert result.observation is not None
    assert result.observation_digest == canonical_backend_observation_digest(
        result.observation
    )
    assert rig.executor.calls == 1
    assert rig.log == [
        "normalize",
        "derive",
        "persist",
        "admit",
        "authorize",
        "fence",
        "observe",
        "issue_contract",
        "prepare",
        "mark_executing",
        "executor",
        "record_observation",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        "normalization",
        "derivation",
        "snapshot",
        "admission",
        "authority_capabilities",
        "fence",
        "preconditions",
        "contract",
        "preparation",
        "start",
    ],
)
async def test_broker_never_invokes_executor_after_upstream_mismatch(tamper: str) -> None:
    rig = _Rig()
    if tamper == "normalization":
        rig.normalizer.tamper = True
    elif tamper == "derivation":
        rig.deriver.tamper = True
    elif tamper == "snapshot":
        rig.material_store.snapshot_tamper = True
    elif tamper == "admission":
        rig.journal.admission_tamper = True
    elif tamper == "authority_capabilities":
        capability_payload = _capability().model_dump(by_alias=True, mode="python")
        capability_payload["resourceRef"] = _resource("other.txt")
        rig.authority.capabilities = (
            AuthorityCapability.model_validate(capability_payload),
        )
    elif tamper == "fence":
        rig.fence.tamper = True
    elif tamper == "preconditions":
        rig.observer.omit = True
    elif tamper == "contract":
        rig.contract_issuer.tamper = True
    elif tamper == "preparation":
        rig.journal.preparation_tamper = True
    elif tamper == "start":
        rig.journal.start_tamper = True

    with pytest.raises(BrokerMismatch):
        await rig.broker.execute(
            context=_context(),
            untrusted_request={"path": "a.txt", "text": "next"},
        )

    assert rig.executor.calls == 0


@pytest.mark.asyncio
async def test_authority_deny_is_durable_and_never_acquires_fence() -> None:
    rig = _Rig()
    rig.authority.status = "deny"

    result = await rig.broker.execute(
        context=_context(),
        untrusted_request={"path": "a.txt", "text": "next"},
    )

    assert result.disposition is BrokerDisposition.DENIED
    assert rig.executor.calls == 0
    assert rig.log[-2:] == ["authorize", "deny"]
    assert "fence" not in rig.log


def test_contract_issuer_fixture_binds_canonical_digest() -> None:
    """Keep the fake honest: production validates this exact contract."""
    rig = _Rig()
    material = rig.normalizer.normalize(
        untrusted_request={}, declaration=rig.declaration
    )
    resources = rig.deriver.derive(material=material, declaration=rig.declaration)
    snapshot = rig.material_store.persist(material=material, resources=resources)
    # The full flow exercises canonical digest validation; this assertion keeps
    # accidental future fixture weakening visible.
    assert snapshot.normalized_input_digest == material.normalized_input_digest
    assert callable(canonical_authority_contract_digest)
