from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from types import SimpleNamespace

import pytest
from pydantic import ConfigDict, Field, ValidationError

from magi_agent.execution_authority.contracts import (
    DependencyContract,
    ProofObligation,
    ResearchClaimRequirement,
    Requirement,
    TaskContractBinding,
    TaskContractSnapshot,
    bind_task_contract,
    canonical_task_contract_bytes,
    canonical_task_contract_digest,
    canonical_task_contract_json,
    validate_task_contract_binding,
)
from magi_agent.execution_authority.state_machine import RequirementState


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _snapshot(**updates: object) -> TaskContractSnapshot:
    payload: dict[str, object] = {
        "taskContractId": "task_01",
        "version": 3,
        "completionEpochId": "epoch_01",
        "sourceMessageDigests": [_digest("a")],
        "intent": "Produce the requested artifact",
        "inclusions": ["artifact"],
        "exclusions": ["deployment"],
        "constraints": ["remain dormant"],
        "assumptions": ["the input is complete"],
        "dependencies": [
            {
                "dependencyId": "dependency_01",
                "requiredSchema": "openmagi.dependency.v1",
                "unavailableBehavior": "block",
            }
        ],
        "acceptableBlockedBehavior": "Report the blocking dependency",
        "acceptableUnavailableBehavior": "Report unavailable evidence",
        "requirements": [
            {
                "requirementId": "requirement_01",
                "text": "Create the artifact",
                "state": "pending",
                "proof": {
                    "evidenceKinds": ["artifact_receipt"],
                    "freshness": "current completion epoch",
                    "requiredProducer": "artifact verifier",
                },
            }
        ],
    }
    payload.update(updates)
    return TaskContractSnapshot.model_validate(payload)


def test_plan_construction_example_is_frozen_and_alias_aware() -> None:
    proof = ProofObligation.model_validate(
        {
            "evidence_kinds": ("artifact_receipt",),
            "freshness": "current completion epoch",
        }
    )
    requirement = Requirement.model_validate(
        {
            "requirement_id": "requirement_01",
            "text": "Create the artifact",
            "state": RequirementState.PENDING,
            "proof": proof,
        }
    )
    contract = TaskContractSnapshot.model_validate(
        {
            "task_contract_id": "task_01",
            "version": 3,
            "completion_epoch_id": "epoch_01",
            "source_message_digests": (_digest("a"),),
            "intent": "Produce the requested artifact",
            "inclusions": ("artifact",),
            "exclusions": ("deployment",),
            "constraints": ("remain dormant",),
            "assumptions": ("the input is complete",),
            "dependencies": (),
            "acceptable_blocked_behavior": "Report the blocker",
            "acceptable_unavailable_behavior": "Report unavailable evidence",
            "requirements": (requirement,),
        }
    )

    assert contract.schema_id == "magi.task_contract.v1"
    assert contract.task_contract_id == "task_01"
    assert contract.version == 3
    assert contract.requirements == (requirement,)
    assert contract.model_dump(by_alias=True, mode="json")["taskContractId"] == "task_01"

    with pytest.raises(ValidationError, match="frozen"):
        contract.version = 4  # type: ignore[misc]


def test_duplicate_requirement_ids_are_rejected() -> None:
    duplicate = {
        "requirementId": "requirement_01",
        "text": "Create a second artifact",
        "state": "pending",
        "proof": {
            "evidenceKinds": ["artifact_receipt"],
            "freshness": "current completion epoch",
        },
    }

    with pytest.raises(ValidationError, match="requirement IDs"):
        _snapshot(
            requirements=[_snapshot().model_dump(by_alias=True)["requirements"][0], duplicate]
        )


def test_task_contract_supersession_is_paired_and_version_monotonic() -> None:
    with pytest.raises(ValidationError, match="both-or-neither"):
        _snapshot(supersedesTaskContractId="task_01")
    with pytest.raises(ValidationError, match="advance supersedesVersion"):
        _snapshot(
            version=3,
            supersedesTaskContractId="task_01",
            supersedesVersion=1,
        )
    clarified = _snapshot(
        version=3,
        supersedesTaskContractId="task_01",
        supersedesVersion=2,
    )
    assert clarified.version == clarified.supersedes_version + 1


@pytest.mark.parametrize(
    ("field", "changed_value"),
    (
        (
            "requirements",
            [
                {
                    "requirementId": "requirement_01",
                    "text": "Create the revised artifact",
                    "state": "pending",
                    "proof": {
                        "evidenceKinds": ["artifact_receipt"],
                        "freshness": "current completion epoch",
                        "requiredProducer": "artifact verifier",
                    },
                }
            ],
        ),
        ("assumptions", ["the input has been revised"]),
        ("sourceMessageDigests", [_digest("b")]),
        ("acceptableBlockedBehavior", "Return a structured blocked result"),
    ),
)
def test_complete_snapshot_digest_changes_when_one_semantic_field_changes(
    field: str,
    changed_value: object,
) -> None:
    original = _snapshot()
    changed = _snapshot(**{field: changed_value})

    assert original.task_contract_id == changed.task_contract_id
    assert original.version == changed.version
    assert canonical_task_contract_digest(original) != canonical_task_contract_digest(changed)


def test_binding_is_derived_from_and_validated_against_the_complete_snapshot() -> None:
    snapshot = _snapshot()

    binding = bind_task_contract(snapshot)

    expected_digest = canonical_task_contract_digest(snapshot)
    assert binding.task_contract_id == "task_01"
    assert binding.task_version == 3
    assert binding.task_contract_digest == expected_digest
    assert binding.task_contract_snapshot_ref == f"authority-task://{expected_digest}"
    validated = validate_task_contract_binding(snapshot, binding)
    assert validated == binding
    assert validated is not binding
    assert type(validated) is TaskContractBinding


@pytest.mark.parametrize(
    ("updates", "message"),
    (
        ({"task_contract_id": "task_02"}, "taskContractId"),
        ({"task_version": 4}, "taskVersion"),
        (
            {
                "task_contract_digest": _digest("0"),
                "task_contract_snapshot_ref": f"authority-task://{_digest('0')}",
            },
            "taskContractDigest",
        ),
        (
            {"task_contract_snapshot_ref": f"authority-task://{_digest('1')}"},
            "taskContractSnapshotRef",
        ),
    ),
)
def test_binding_validation_rejects_each_mismatched_field(
    updates: dict[str, object],
    message: str,
) -> None:
    snapshot = _snapshot()
    binding = bind_task_contract(snapshot)
    binding.__dict__.update(updates)

    with pytest.raises(ValueError, match=message):
        validate_task_contract_binding(snapshot, binding)


def test_callers_cannot_supply_an_independent_digest_or_unknown_fields() -> None:
    snapshot = _snapshot()

    with pytest.raises(TypeError, match="unexpected keyword"):
        bind_task_contract(  # type: ignore[call-arg]
            snapshot,
            task_contract_digest="sha256:" + "0" * 64,
        )

    snapshot_payload = snapshot.model_dump(by_alias=True, mode="json")
    snapshot_payload["taskContractDigest"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TaskContractSnapshot.model_validate(snapshot_payload)

    binding_payload = bind_task_contract(snapshot).model_dump(by_alias=True, mode="json")
    binding_payload["unknownDigest"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TaskContractBinding.model_validate(binding_payload)


def test_contract_collections_are_tuples_and_copy_escape_hatches_are_closed() -> None:
    snapshot = _snapshot()

    assert isinstance(snapshot.source_message_digests, tuple)
    assert isinstance(snapshot.inclusions, tuple)
    assert isinstance(snapshot.exclusions, tuple)
    assert isinstance(snapshot.constraints, tuple)
    assert isinstance(snapshot.assumptions, tuple)
    assert isinstance(snapshot.dependencies, tuple)
    assert isinstance(snapshot.requirements, tuple)
    assert isinstance(snapshot.requirements[0].proof.evidence_kinds, tuple)

    with pytest.raises(ValueError, match="copy update"):
        snapshot.copy(update={"version": 0})
    with pytest.raises(ValueError, match="model_copy update"):
        snapshot.model_copy(update={"version": 0})
    with pytest.raises(ValueError, match="model_construct is disabled"):
        TaskContractSnapshot.model_construct(version=0)

    copied = snapshot.copy()
    assert copied == snapshot
    assert copied is not snapshot


def test_non_ascii_snapshot_uses_the_task_contract_unicode_byte_stream() -> None:
    snapshot = _snapshot(
        taskContractId="작업_01",
        completionEpochId="완료_01",
        sourceMessageDigests=[_digest("c")],
        intent="사용자가 요청한 결과물을 만든다",
        inclusions=["결과물"],
        exclusions=["배포"],
        constraints=["실행 권한과 연결하지 않는다"],
        assumptions=["입력은 완전하다"],
        dependencies=[],
        acceptableBlockedBehavior="차단 사유를 보고한다",
        acceptableUnavailableBehavior="사용 불가 상태를 보고한다",
        requirements=[
            {
                "requirementId": "요구사항_01",
                "text": "결과물을 생성한다",
                "state": "pending",
                "proof": {
                    "evidenceKinds": ["결과물 영수증"],
                    "freshness": "현재 완료 에포크",
                    "requiredProducer": "검증기",
                },
            }
        ],
    )
    canonical_json = canonical_task_contract_json(snapshot)
    canonical_bytes = canonical_task_contract_bytes(snapshot)

    assert "사용자가" in canonical_json
    assert canonical_task_contract_digest(snapshot) == (
        "sha256:" + sha256(canonical_bytes).hexdigest()
    )


def test_declared_minimum_lengths_and_versions_are_enforced() -> None:
    with pytest.raises(ValidationError):
        ProofObligation(evidenceKinds=(), freshness="current")
    with pytest.raises(ValidationError):
        ProofObligation(evidenceKinds=("receipt",), freshness="")
    with pytest.raises(ValidationError):
        Requirement(
            requirementId="",
            text="Create the artifact",
            state=RequirementState.PENDING,
            proof=ProofObligation(evidenceKinds=("receipt",), freshness="current"),
        )
    with pytest.raises(ValidationError):
        DependencyContract(
            dependencyId="dependency_01",
            requiredSchema="",
            unavailableBehavior="block",
        )
    with pytest.raises(ValidationError):
        _snapshot(version=0)
    with pytest.raises(ValidationError):
        _snapshot(sourceMessageDigests=[])
    with pytest.raises(ValidationError):
        _snapshot(requirements=[])
    with pytest.raises(ValidationError):
        _snapshot(supersedesVersion=0)
    with pytest.raises(ValidationError):
        TaskContractBinding(
            taskContractId="task_01",
            taskVersion=0,
            taskContractDigest="sha256:" + "0" * 64,
            taskContractSnapshotRef="authority-task://sha256:" + "0" * 64,
        )


def test_mutable_nested_proof_subclass_is_copied_into_the_exact_base_type() -> None:
    class MutableProofObligation(ProofObligation):
        model_config = ConfigDict(frozen=False)

    external_proof = MutableProofObligation(
        evidenceKinds=("artifact_receipt",),
        freshness="current completion epoch",
    )
    snapshot = _snapshot(
        requirements=[
            {
                "requirementId": "requirement_01",
                "text": "Create the artifact",
                "state": "pending",
                "proof": external_proof,
            }
        ]
    )
    original_digest = canonical_task_contract_digest(snapshot)
    binding = bind_task_contract(snapshot)

    assert type(snapshot.requirements[0].proof) is ProofObligation
    assert snapshot.requirements[0].proof is not external_proof

    external_proof.freshness = "stale"

    assert canonical_task_contract_digest(snapshot) == original_digest
    assert validate_task_contract_binding(snapshot, binding) == binding


def test_requirement_subclass_semantics_are_rejected() -> None:
    class SemanticRequirement(Requirement):
        semantic_variant: str = Field(alias="semanticVariant")

    def semantic_requirement(variant: str) -> SemanticRequirement:
        return SemanticRequirement(
            requirementId="requirement_01",
            text="Create the artifact",
            state=RequirementState.PENDING,
            proof=ProofObligation(evidenceKinds=("artifact_receipt",), freshness="current"),
            semanticVariant=variant,
        )

    for variant in ("first", "second"):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            _snapshot(requirements=[semantic_requirement(variant)])


def test_unordered_assumptions_are_rejected_instead_of_hash_ordered() -> None:
    with pytest.raises(ValidationError, match="ordered"):
        _snapshot(assumptions={"first", "second", "third"})


def test_nested_proof_subclass_semantics_are_rejected() -> None:
    class SemanticProofObligation(ProofObligation):
        semantic_variant: str = Field(alias="semanticVariant")

    def semantic_proof(variant: str) -> SemanticProofObligation:
        return SemanticProofObligation(
            evidenceKinds=("artifact_receipt",),
            freshness="current",
            semanticVariant=variant,
        )

    def snapshot_with(proof: ProofObligation) -> TaskContractSnapshot:
        return _snapshot(
            requirements=[
                {
                    "requirementId": "requirement_01",
                    "text": "Create the artifact",
                    "state": "pending",
                    "proof": proof,
                }
            ]
        )

    for variant in ("first", "second"):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            snapshot_with(semantic_proof(variant))


def test_digest_and_bind_reject_task_contract_snapshot_subclasses() -> None:
    class SemanticTaskContractSnapshot(TaskContractSnapshot):
        semantic_variant: str = Field(alias="semanticVariant")

    class OverriddenDumpSnapshot(TaskContractSnapshot):
        def model_dump(  # type: ignore[override]
            self,
            *args: object,
            **kwargs: object,
        ) -> dict[str, object]:
            _ = args, kwargs
            raise AssertionError("caller-controlled model_dump was invoked")

    payload = _snapshot().model_dump(by_alias=True, mode="json")
    semantic = SemanticTaskContractSnapshot.model_validate({**payload, "semanticVariant": "hidden"})
    overridden = OverriddenDumpSnapshot.model_validate(payload)

    for candidate in (semantic, overridden):
        with pytest.raises(TypeError, match="exact TaskContractSnapshot"):
            canonical_task_contract_digest(candidate)
        with pytest.raises(TypeError, match="exact TaskContractSnapshot"):
            bind_task_contract(candidate)


def test_public_helpers_reject_duck_typed_snapshots_and_bindings() -> None:
    snapshot = _snapshot()
    payload = snapshot.model_dump(by_alias=True, mode="json")
    duck_snapshot = SimpleNamespace(
        task_contract_id=snapshot.task_contract_id,
        version=snapshot.version,
        model_dump=lambda **_kwargs: payload,
    )
    binding = bind_task_contract(snapshot)
    duck_binding = SimpleNamespace(
        task_contract_id=binding.task_contract_id,
        task_version=binding.task_version,
        task_contract_digest=binding.task_contract_digest,
        task_contract_snapshot_ref=binding.task_contract_snapshot_ref,
    )

    with pytest.raises(TypeError, match="exact TaskContractSnapshot"):
        canonical_task_contract_digest(duck_snapshot)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact TaskContractSnapshot"):
        bind_task_contract(duck_snapshot)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact TaskContractBinding"):
        validate_task_contract_binding(snapshot, duck_binding)  # type: ignore[arg-type]


def test_public_helpers_revalidate_tampered_existing_exact_instances() -> None:
    for helper in (canonical_task_contract_digest, bind_task_contract):
        snapshot = _snapshot()
        snapshot.__dict__["version"] = 0
        with pytest.raises(ValidationError):
            helper(snapshot)

    snapshot = _snapshot()
    binding = bind_task_contract(snapshot)
    binding.__dict__["task_version"] = 3.0
    with pytest.raises(ValidationError):
        validate_task_contract_binding(snapshot, binding)


def test_digest_rejects_an_exact_snapshot_with_a_tampered_serializer() -> None:
    snapshot = _snapshot()
    snapshot.__dict__["model_dump"] = lambda **_kwargs: {"forged": True}

    with pytest.raises(ValidationError):
        canonical_task_contract_digest(snapshot)


@pytest.mark.parametrize(
    "as_unordered",
    (
        pytest.param(lambda values: set(values), id="set"),
        pytest.param(lambda values: frozenset(values), id="frozenset"),
    ),
)
def test_unordered_evidence_kinds_are_rejected(
    as_unordered: Callable[[tuple[object, ...]], object],
) -> None:
    with pytest.raises(ValidationError, match="ordered"):
        ProofObligation(
            evidenceKinds=as_unordered(  # type: ignore[arg-type]
                ("artifact_receipt", "verification_receipt")
            ),
            freshness="current",
        )


@pytest.mark.parametrize(
    ("field", "values"),
    (
        ("sourceMessageDigests", (_digest("a"), _digest("b"))),
        ("inclusions", ("artifact", "receipt")),
        ("exclusions", ("deployment", "billing")),
        ("constraints", ("dormant", "local")),
        ("assumptions", ("input complete", "schema available")),
        (
            "dependencies",
            (
                DependencyContract(
                    dependencyId="dependency_01",
                    requiredSchema="openmagi.dependency.v1",
                    unavailableBehavior="block",
                ),
                DependencyContract(
                    dependencyId="dependency_02",
                    requiredSchema="openmagi.dependency.v2",
                    unavailableBehavior="report",
                ),
            ),
        ),
        (
            "requirements",
            (
                Requirement(
                    requirementId="requirement_01",
                    text="Create the artifact",
                    state=RequirementState.PENDING,
                    proof=ProofObligation(evidenceKinds=("receipt",), freshness="current"),
                ),
                Requirement(
                    requirementId="requirement_02",
                    text="Verify the artifact",
                    state=RequirementState.PENDING,
                    proof=ProofObligation(evidenceKinds=("receipt",), freshness="current"),
                ),
            ),
        ),
    ),
)
@pytest.mark.parametrize(
    "as_unordered",
    (
        pytest.param(lambda values: set(values), id="set"),
        pytest.param(lambda values: frozenset(values), id="frozenset"),
    ),
)
def test_unordered_snapshot_collections_are_rejected(
    field: str,
    values: tuple[object, ...],
    as_unordered: Callable[[tuple[object, ...]], object],
) -> None:
    with pytest.raises(ValidationError, match="ordered"):
        _snapshot(**{field: as_unordered(values)})


@pytest.mark.parametrize("invalid_version", (True, "3", b"3", 3.0))
def test_version_fields_reject_coercible_non_integers(invalid_version: object) -> None:
    with pytest.raises(ValidationError):
        _snapshot(version=invalid_version)
    with pytest.raises(ValidationError):
        _snapshot(supersedesVersion=invalid_version)

    binding_payload = bind_task_contract(_snapshot()).model_dump(by_alias=True, mode="json")
    binding_payload["taskVersion"] = invalid_version
    with pytest.raises(ValidationError):
        TaskContractBinding.model_validate(binding_payload)


@pytest.mark.parametrize(
    "invalid_digest",
    (
        "not-a-digest",
        "sha256:" + "a" * 63,
        "sha256:" + "A" * 64,
        "SHA256:" + "a" * 64,
    ),
)
def test_source_message_digests_require_canonical_sha256_shape(invalid_digest: str) -> None:
    with pytest.raises(ValidationError, match="sha256"):
        _snapshot(sourceMessageDigests=[invalid_digest])


@pytest.mark.parametrize(
    "invalid_digest",
    (
        "",
        "not-a-digest",
        "sha256:" + "a" * 63,
        "sha256:" + "A" * 64,
    ),
)
def test_binding_digest_requires_canonical_sha256_shape(invalid_digest: str) -> None:
    with pytest.raises(ValidationError, match="sha256"):
        TaskContractBinding(
            taskContractId="task_01",
            taskVersion=3,
            taskContractDigest=invalid_digest,
            taskContractSnapshotRef=f"authority-task://{invalid_digest}",
        )


def test_binding_requires_nonempty_id_and_exact_digest_derived_snapshot_ref() -> None:
    digest = _digest("a")
    with pytest.raises(ValidationError):
        TaskContractBinding(
            taskContractId="",
            taskVersion=3,
            taskContractDigest=digest,
            taskContractSnapshotRef=f"authority-task://{digest}",
        )

    for invalid_ref in (
        digest,
        f"task-contract://{digest}",
        f"authority-task://{_digest('b')}",
        f"authority-task://{digest}/extra",
    ):
        with pytest.raises(ValidationError, match="taskContractSnapshotRef"):
            TaskContractBinding(
                taskContractId="task_01",
                taskVersion=3,
                taskContractDigest=digest,
                taskContractSnapshotRef=invalid_ref,
            )


def test_stale_binding_fails_against_same_id_and_version_mutated_snapshot() -> None:
    original = _snapshot()
    binding = bind_task_contract(original)
    mutated = _snapshot(assumptions=["the input changed"])

    assert mutated.task_contract_id == original.task_contract_id
    assert mutated.version == original.version
    with pytest.raises(ValueError, match="taskContractDigest"):
        validate_task_contract_binding(mutated, binding)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("intent", b"binary intent"),
        ("inclusions", [b"binary inclusion"]),
        ("constraints", {"unordered constraint"}),
    ),
)
def test_task_contract_rejects_non_json_binary_and_unordered_inputs(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match="canonical JSON"):
        _snapshot(**{field: value})


def test_research_claim_requirement_binds_the_exact_proposition_text() -> None:
    proposition = "서울의 현재 기온은 섭씨 23도다."
    digest = "sha256:" + sha256(proposition.encode()).hexdigest()
    claim = ResearchClaimRequirement(
        claimId="claim_weather",
        claimClass="temporal_fact",
        proposition=proposition,
        propositionDigest=digest,
        freshness="same_retrieval_window",
    )
    assert claim.proposition_digest == digest

    payload = claim.model_dump(by_alias=True, mode="json")
    payload["proposition"] = "서울의 현재 기온은 섭씨 24도다."
    with pytest.raises(ValidationError, match="propositionDigest"):
        ResearchClaimRequirement.model_validate(payload)
