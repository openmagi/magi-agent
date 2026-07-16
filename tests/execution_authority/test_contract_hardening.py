from __future__ import annotations

from collections import UserDict
from collections.abc import Callable
from datetime import UTC, datetime
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from magi_agent.execution_authority.contracts import (
    AuthorityCapability,
    AuthorityContract,
    AuthorityDecision,
    AuthorityEvaluationReceipt,
    AuthorityInput,
    DependencyContract,
    ProofObligation,
    ResearchClaimRequirement,
    ResearchProofObligation,
    Requirement,
    TaskContractSnapshot,
    canonical_authority_decision_digest,
    canonical_authority_evaluation_receipt_digest,
    canonical_authority_inputs_digest,
    canonical_capabilities_digest,
    canonical_research_proposition_digest,
    resolve_authority,
    resolve_authority_evaluation,
)
from magi_agent.execution_authority.state_machine import RequirementState
from magi_agent.ops.safety import canonical_digest


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _dependency(identifier: str) -> DependencyContract:
    return DependencyContract(
        dependencyId=identifier,
        requiredSchema="openmagi.dependency.v1",
        unavailableBehavior="block",
    )


def _requirement(identifier: str) -> Requirement:
    return Requirement(
        requirementId=identifier,
        text=f"Satisfy {identifier}",
        state=RequirementState.PENDING,
        proof=ProofObligation(
            evidenceKinds=("artifact_receipt",),
            freshness="current completion epoch",
        ),
    )


def _task_payload() -> dict[str, object]:
    return {
        "taskContractId": "task_01",
        "version": 3,
        "completionEpochId": "epoch_01",
        "sourceMessageDigests": [_digest("a")],
        "intent": "Produce the requested artifact",
        "inclusions": ["artifact"],
        "exclusions": ["deployment"],
        "constraints": ["remain dormant"],
        "assumptions": ["the input is complete"],
        "dependencies": [_dependency("dependency_01")],
        "acceptableBlockedBehavior": "Report the blocking dependency",
        "acceptableUnavailableBehavior": "Report unavailable evidence",
        "requirements": [_requirement("requirement_01")],
    }


def _capability(
    effect_class: str = "workspace.write",
    *,
    resource_ref: str = "workspace:project",
) -> AuthorityCapability:
    workspace_digest = _digest("e") if effect_class.startswith("workspace.") else None
    return AuthorityCapability(
        effectClass=effect_class,
        resourceRef=resource_ref,
        networkRefs=(),
        credentialRefs=(),
        workspaceViewBindingDigest=workspace_digest,
    )


def _contract_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "authorityContractId": "authority_01",
        "issuerId": "issuer_01",
        "principalId": "principal_01",
        "tenantId": "tenant_01",
        "sessionId": "session_01",
        "turnId": "turn_01",
        "taskContractId": "task_01",
        "taskVersion": 3,
        "taskContractDigest": _digest("1"),
        "completionEpochId": "epoch_01",
        "authorityPartitionId": "partition_01",
        "actionId": "action_01",
        "attemptId": "attempt_01",
        "policyDigest": _digest("2"),
        "normalizedRequestDigest": _digest("3"),
        "commandDigest": _digest("4"),
        "argumentsDigest": _digest("5"),
        "workingDirectoryDigest": _digest("6"),
        "environmentDigest": _digest("7"),
        "requestBodyDigest": _digest("8"),
        "credentialScopeDigest": _digest("9"),
        "networkDigest": _digest("a"),
        "disclosureDigest": _digest("b"),
        "capabilities": [_capability()],
        "workspaceViewBindingDigest": _digest("e"),
        "sandboxProfileDigest": _digest("c"),
        "guardianCeilingDigest": _digest("d"),
        "expiresAt": datetime(2030, 1, 1, tzinfo=UTC),
        "fencingToken": 7,
        "delegationChain": [],
    }
    payload.update(updates)
    return payload


def test_task_dependencies_require_unique_ids_and_canonical_id_order() -> None:
    payload = _task_payload()
    payload["dependencies"] = [_dependency("dependency_01"), _dependency("dependency_01")]
    with pytest.raises(ValidationError, match="dependency IDs must be unique"):
        TaskContractSnapshot.model_validate(payload)

    payload["dependencies"] = [_dependency("dependency_02"), _dependency("dependency_01")]
    snapshot = TaskContractSnapshot.model_validate(payload)
    assert tuple(item.dependency_id for item in snapshot.dependencies) == (
        "dependency_01",
        "dependency_02",
    )


@pytest.mark.parametrize(
    ("field", "values"),
    (
        ("sourceMessageDigests", (_digest("b"), _digest("a"))),
        ("inclusions", ("zeta", "alpha")),
        ("exclusions", ("zeta", "alpha")),
        ("constraints", ("zeta", "alpha")),
        ("assumptions", ("zeta", "alpha")),
    ),
)
def test_task_set_like_string_sequences_require_unique_sorted_values(
    field: str,
    values: tuple[str, str],
) -> None:
    payload = _task_payload()
    payload[field] = values
    snapshot = TaskContractSnapshot.model_validate(payload)
    attribute = {
        "sourceMessageDigests": "source_message_digests",
        "inclusions": "inclusions",
        "exclusions": "exclusions",
        "constraints": "constraints",
        "assumptions": "assumptions",
    }[field]
    assert getattr(snapshot, attribute) == tuple(sorted(values))

    payload[field] = (values[0], values[0])
    with pytest.raises(ValidationError, match="unique"):
        TaskContractSnapshot.model_validate(payload)


def test_task_requirements_and_research_sets_require_canonical_order() -> None:
    payload = _task_payload()
    payload["requirements"] = [_requirement("requirement_02"), _requirement("requirement_01")]
    snapshot = TaskContractSnapshot.model_validate(payload)
    assert tuple(item.requirement_id for item in snapshot.requirements) == (
        "requirement_01",
        "requirement_02",
    )

    claim_a = ResearchClaimRequirement(
        claimId="claim_a",
        claimClass="factual",
        proposition="alpha",
        freshness="same_state_root",
    )
    claim_b = ResearchClaimRequirement(
        claimId="claim_b",
        claimClass="factual",
        proposition="beta",
        freshness="same_state_root",
    )
    research = ResearchProofObligation(
        claims=(claim_b, claim_a),
        queryClasses=("official_primary",),
        primarySourceRule="required",
        conflictHandling="resolve",
        stoppingRules=("claim_coverage_met",),
        limitedSnippetAllowance="forbidden",
    )
    assert tuple(claim.claim_id for claim in research.claims) == ("claim_a", "claim_b")


@pytest.mark.parametrize(
    "factory",
    (
        lambda: DependencyContract(
            dependencyId="   ",
            requiredSchema="openmagi.dependency.v1",
            unavailableBehavior="block",
        ),
        lambda: DependencyContract(
            dependencyId="dependency_01",
            requiredSchema="\t",
            unavailableBehavior="block",
        ),
        lambda: AuthorityCapability(
            effectClass="network.read",
            resourceRef=" \n ",
            networkRefs=(),
            credentialRefs=(),
        ),
        lambda: AuthorityDecision(
            status="deny",
            reasonCodes=("   ",),
            capabilities=(),
        ),
    ),
)
def test_identity_reference_schema_and_reason_strings_reject_whitespace_only(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(ValidationError, match="whitespace-only"):
        factory()


def test_capability_and_reason_sets_require_unique_sorted_values() -> None:
    capability = AuthorityCapability(
        effectClass="network.read",
        resourceRef="https://example.test/resource",
        networkRefs=("https://z.example.test", "https://a.example.test"),
        credentialRefs=(),
    )
    assert capability.network_refs == (
        "https://a.example.test",
        "https://z.example.test",
    )

    decision = AuthorityDecision(
        status="deny",
        reasonCodes=("zeta", "alpha"),
        capabilities=(),
    )
    assert decision.reason_codes == ("alpha", "zeta")

    with pytest.raises(ValidationError, match="unique"):
        AuthorityCapability(
            effectClass="network.read",
            resourceRef="https://example.test/resource",
            networkRefs=("https://api.example.test", "https://api.example.test"),
            credentialRefs=(),
        )
    with pytest.raises(ValidationError, match="unique"):
        AuthorityDecision(
            status="deny",
            reasonCodes=("duplicate", "duplicate"),
            capabilities=(),
        )


def test_evidence_and_research_string_sets_are_unique_and_canonically_sorted() -> None:
    proof = ProofObligation(
        evidenceKinds=("workspace_postcondition", "action_receipt"),
        freshness="current completion epoch",
    )
    assert proof.evidence_kinds == ("action_receipt", "workspace_postcondition")
    with pytest.raises(ValidationError, match="unique"):
        ProofObligation(
            evidenceKinds=("action_receipt", "action_receipt"),
            freshness="current completion epoch",
        )

    claim = ResearchClaimRequirement(
        claimId="claim_a",
        claimClass="factual",
        proposition="alpha",
        freshness="same_state_root",
    )
    research = ResearchProofObligation(
        claims=(claim,),
        queryClasses=("reputable_secondary", "official_primary"),
        primarySourceRule="required",
        conflictHandling="resolve",
        stoppingRules=("source_classes_exhausted", "claim_coverage_met"),
        limitedSnippetAllowance="forbidden",
    )
    assert research.query_classes == ("official_primary", "reputable_secondary")
    assert research.stopping_rules == (
        "claim_coverage_met",
        "source_classes_exhausted",
    )


def test_mutating_authority_requires_a_positive_fencing_token() -> None:
    with pytest.raises(ValidationError, match="positive fencingToken"):
        AuthorityContract.model_validate(_contract_payload(fencingToken=0))

    read_only = AuthorityCapability(
        effectClass="workspace.read",
        resourceRef="workspace:project",
        networkRefs=(),
        credentialRefs=(),
        workspaceViewBindingDigest=_digest("e"),
    )
    contract = AuthorityContract.model_validate(
        _contract_payload(capabilities=(read_only,), fencingToken=0)
    )
    assert contract.fencing_token == 0


@pytest.mark.parametrize("effect_class", ("process.exec", "process.execute"))
def test_process_authority_requires_a_command_digest(effect_class: str) -> None:
    capability = _capability(effect_class, resource_ref="binary:git")
    with pytest.raises(ValidationError, match="commandDigest"):
        AuthorityContract.model_validate(
            _contract_payload(
                capabilities=(capability,),
                commandDigest=None,
                workspaceViewBindingDigest=None,
            )
        )


def test_network_and_credential_components_are_required_when_capabilities_use_them() -> None:
    capability = AuthorityCapability(
        effectClass="network.write",
        resourceRef="https://api.example.test/messages",
        networkRefs=("https://api.example.test",),
        credentialRefs=("credential:provider",),
    )
    with pytest.raises(ValidationError, match="networkDigest"):
        AuthorityContract.model_validate(
            _contract_payload(
                capabilities=(capability,),
                networkDigest=None,
                workspaceViewBindingDigest=None,
            )
        )
    with pytest.raises(ValidationError, match="credentialScopeDigest"):
        AuthorityContract.model_validate(
            _contract_payload(
                capabilities=(capability,),
                credentialScopeDigest=None,
                workspaceViewBindingDigest=None,
            )
        )
    with pytest.raises(ValidationError, match="requestBodyDigest"):
        AuthorityContract.model_validate(
            _contract_payload(
                capabilities=(capability,),
                requestBodyDigest=None,
                workspaceViewBindingDigest=None,
            )
        )


@pytest.mark.parametrize(
    ("effect_class", "resource_ref"),
    (
        ("database.write", "database:primary/table"),
        ("message.send", "message:telegram/chat"),
        ("artifact.deliver", "artifact:release/archive"),
    ),
)
def test_payload_effect_authority_requires_a_request_body_digest(
    effect_class: str,
    resource_ref: str,
) -> None:
    capability = _capability(effect_class, resource_ref=resource_ref)
    with pytest.raises(ValidationError, match="requestBodyDigest"):
        AuthorityContract.model_validate(
            _contract_payload(
                capabilities=(capability,),
                requestBodyDigest=None,
                workspaceViewBindingDigest=None,
            )
        )


def test_authority_evaluation_receipt_binds_inputs_action_and_decision() -> None:
    capability = _capability()
    inputs = (AuthorityInput(source="user", decision="allow", capabilities=(capability,)),)
    action_digest = _digest("f")
    decision = resolve_authority(inputs=inputs, action_digest=action_digest)

    receipt = resolve_authority_evaluation(
        inputs=inputs,
        action_digest=action_digest,
        evaluator_id="authority-kernel",
        evaluator_version="1.0.0",
        policy_digest=_digest("d"),
        evaluated_at=datetime(2029, 1, 1, tzinfo=UTC),
    )

    assert type(receipt) is AuthorityEvaluationReceipt
    assert receipt.action_digest == action_digest
    assert receipt.authority_inputs_digest == canonical_authority_inputs_digest(inputs)
    assert receipt.decision == decision
    assert receipt.decision_digest == canonical_authority_decision_digest(decision)
    receipt_digest = canonical_authority_evaluation_receipt_digest(receipt)
    assert receipt_digest.startswith("sha256:")

    payload = receipt.model_dump(by_alias=True, mode="python")
    payload["decisionDigest"] = _digest("0")
    with pytest.raises(ValidationError, match="decisionDigest"):
        AuthorityEvaluationReceipt.model_validate(payload)

    for field in ("authorityInputsDigest", "decisionDigest"):
        explicit_empty = receipt.model_dump(
            by_alias=True,
            mode="python",
            exclude={"authority_inputs_digest", "decision_digest"},
        )
        explicit_empty[field] = ""
        with pytest.raises(ValidationError, match="canonical sha256"):
            AuthorityEvaluationReceipt.model_validate(explicit_empty)


def test_domain_specific_digests_cannot_alias_structurally_similar_payloads() -> None:
    proposition = "same semantic bytes"
    proposition_digest = canonical_research_proposition_digest(proposition)
    raw_digest = canonical_digest({"proposition": proposition})
    assert proposition_digest != raw_digest

    capability = AuthorityCapability(
        effectClass="network.read",
        resourceRef="https://example.test",
        networkRefs=(),
        credentialRefs=(),
    )
    capability_payload = AuthorityCapability.model_dump(capability, by_alias=True, mode="json")
    assert canonical_capabilities_digest((capability,)) != canonical_digest(
        {"capabilities": [capability_payload]}
    )


def test_duplicate_safe_json_decoder_and_type_adapter_bypass_are_closed() -> None:
    payload = _contract_payload()
    encoded = json.dumps(payload, default=str)
    duplicate = encoded[:-1] + ',"authorityContractId":"forged"}'

    with pytest.raises(ValueError, match="duplicate key"):
        AuthorityContract.model_validate_json(duplicate)
    with pytest.raises(ValidationError, match="duplicate-safe"):
        TypeAdapter(AuthorityContract).validate_json(encoded)


def test_contract_json_preflight_rejects_unbalanced_containers() -> None:
    with pytest.raises(ValueError, match="unbalanced containers"):
        AuthorityContract.model_validate_json("{}]")
    with pytest.raises(ValueError, match="unbalanced containers"):
        AuthorityContract.model_validate_json('{"authorityContractId":"unterminated"')


def test_contract_input_rejects_hostile_mapping_and_iterable_containers() -> None:
    class HostileIterable:
        def __iter__(self):  # type: ignore[no-untyped-def]
            yield _capability()

    with pytest.raises(ValidationError, match="exact dict"):
        AuthorityContract.model_validate(UserDict(_contract_payload()))
    with pytest.raises(ValidationError, match="iterable containers"):
        AuthorityInput.model_validate(
            {
                "source": "user",
                "decision": "allow",
                "capabilities": HostileIterable(),
            }
        )


@pytest.mark.parametrize("unsafe_integer", (2**53, -(2**53)))
def test_i_json_integer_limits_apply_to_python_and_json_inputs(unsafe_integer: int) -> None:
    payload = _contract_payload(fencingToken=unsafe_integer)
    with pytest.raises(ValidationError, match="I-JSON"):
        AuthorityContract.model_validate(payload)
    with pytest.raises(ValidationError, match="I-JSON"):
        AuthorityContract.model_validate_json(json.dumps(payload, default=str))
