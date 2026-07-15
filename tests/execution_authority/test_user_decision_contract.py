from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
import json
from types import SimpleNamespace
from typing import Annotated, Callable

import pytest
from pydantic import ConfigDict, Field, TypeAdapter, ValidationError

from magi_agent.execution_authority.contracts import (
    AuthorityCapability,
    AuthorityResumeBinding,
    UserDecisionReceipt,
    UserDecisionRequest,
    canonical_authority_resume_binding_digest,
    canonical_capabilities_digest,
    canonical_user_decision_receipt_digest,
    canonical_user_decision_request_digest,
    validate_user_decision_receipt_binding,
)
from magi_agent.execution_authority.ports import (
    ResumeBindingVerifierPort,
    UserDecisionKeyPort,
    UserDecisionVerifierPort,
)
from magi_agent.ops.safety import canonical_digest


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
WORKSPACE_ROOT_REF = f"workspace://sha256:{'0' * 64}/"
WORKSPACE_A_REF = WORKSPACE_ROOT_REF + "a.txt"
WORKSPACE_B_REF = WORKSPACE_ROOT_REF + "b.txt"


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _capability(
    resource: str = WORKSPACE_A_REF,
    *,
    effect_class: str = "workspace.write",
    network_refs: tuple[str, ...] = (),
    credential_refs: tuple[str, ...] = (),
    workspace_view_binding_digest: str | None = None,
) -> AuthorityCapability:
    if workspace_view_binding_digest is None and (
        effect_class.startswith("workspace.") or resource.startswith("workspace:")
    ):
        workspace_view_binding_digest = _digest("8")
    return AuthorityCapability(
        effectClass=effect_class,
        resourceRef=resource,
        networkRefs=network_refs,
        credentialRefs=credential_refs,
        workspaceViewBindingDigest=workspace_view_binding_digest,
    )


def _request_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaId": "magi.user_decision_request.v1",
        "decisionRequestId": "decision_01",
        "principalId": "actor_01",
        "tenantId": "tenant_01",
        "sessionId": "session_01",
        "turnId": "turn_01",
        "taskContractId": "task_01",
        "taskVersion": 1,
        "taskContractDigest": _digest("1"),
        "completionEpochId": "epoch_01",
        "actionId": "action_01",
        "authorityPartitionId": "workspace_01",
        "normalizedRequestDigest": _digest("2"),
        "capabilities": (_capability(),),
        "workspaceViewBindingDigest": _digest("8"),
        "authorityCeilingDigest": _digest("3"),
        "policyDigest": _digest("4"),
        "pendingEventId": "event_01",
        "reasonCodes": ("sensitive_write",),
        "createdAt": NOW,
        "expiresAt": NOW + timedelta(minutes=5),
        "compareVersion": 0,
    }
    payload.update(overrides)
    return payload


def _request(**overrides: object) -> UserDecisionRequest:
    return UserDecisionRequest.model_validate(_request_payload(**overrides))


def _receipt_payload(
    request: UserDecisionRequest | None = None,
    **overrides: object,
) -> dict[str, object]:
    bound_request = request or _request()
    payload: dict[str, object] = {
        "schemaId": "magi.user_decision_receipt.v1",
        "receiptId": "receipt_01",
        "decisionRequestId": bound_request.decision_request_id,
        "decision": "approve",
        "authenticatedActorId": bound_request.principal_id,
        "authenticationKeyId": "key_01",
        "authenticationContextDigest": _digest("5"),
        "authenticationNonceDigest": _digest("6"),
        "transportReceiptDigest": _digest("7"),
        "principalId": bound_request.principal_id,
        "tenantId": bound_request.tenant_id,
        "sessionId": bound_request.session_id,
        "turnId": bound_request.turn_id,
        "taskContractId": bound_request.task_contract_id,
        "taskVersion": bound_request.task_version,
        "taskContractDigest": bound_request.task_contract_digest,
        "completionEpochId": bound_request.completion_epoch_id,
        "actionId": bound_request.action_id,
        "authorityPartitionId": bound_request.authority_partition_id,
        "normalizedRequestDigest": bound_request.normalized_request_digest,
        "authorityCeilingDigest": bound_request.authority_ceiling_digest,
        "policyDigest": bound_request.policy_digest,
        "capabilitiesDigest": bound_request.capabilities_digest,
        "workspaceViewBindingDigest": bound_request.workspace_view_binding_digest,
        "issuedAt": bound_request.created_at + timedelta(seconds=1),
        "expiresAt": bound_request.expires_at,
        "revokesReceiptDigest": None,
    }
    payload.update(overrides)
    return payload


def _receipt(
    request: UserDecisionRequest | None = None,
    **overrides: object,
) -> UserDecisionReceipt:
    return UserDecisionReceipt.model_validate(_receipt_payload(request, **overrides))


def _resume_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "decisionRequestId": "decision_01",
        "authenticatedActorId": "actor_01",
        "sessionId": "session_01",
        "turnId": "turn_01",
        "runId": "run_01",
        "actionId": "action_01",
        "taskContractId": "task_01",
        "taskVersion": 1,
        "taskContractDigest": _digest("1"),
        "completionEpochId": "epoch_01",
        "transcriptDigest": _digest("2"),
        "checkpointDigest": _digest("3"),
        "authorityPartitionId": "workspace_01",
        "expectedHeadSequence": 10,
        "expectedHeadHash": _digest("4"),
        "expectedHeadCompareVersion": 11,
        "stateProjectionId": "projection_01",
        "expectedStateSequence": 8,
        "expectedStateEventHash": _digest("5"),
        "expectedStateRoot": _digest("6"),
        "expectedStateCompareVersion": 9,
    }
    payload.update(overrides)
    return payload


def _resume(**overrides: object) -> AuthorityResumeBinding:
    return AuthorityResumeBinding.model_validate(_resume_payload(**overrides))


def test_request_derives_complete_ordered_capabilities_digest() -> None:
    first = _capability(
        network_refs=("https://api.example.test/v1",),
        credential_refs=("credential://tenant/key-a",),
    )
    second = _capability(
        WORKSPACE_B_REF,
        effect_class="workspace.delete",
        network_refs=("https://api.example.test/v2",),
        credential_refs=("credential://tenant/key-b",),
    )
    request = _request(capabilities=(first, second))
    expected_payload = {
        "capabilities": [
            AuthorityCapability.model_dump(first, by_alias=True, mode="json"),
            AuthorityCapability.model_dump(second, by_alias=True, mode="json"),
        ]
    }

    assert request.capabilities_digest == canonical_digest(expected_payload)
    assert request.capabilities_digest == canonical_capabilities_digest((first, second))
    assert canonical_capabilities_digest((first, second)) != canonical_capabilities_digest(
        (second, first)
    )
    assert canonical_capabilities_digest((first,)) != canonical_capabilities_digest(
        (
            _capability(
                network_refs=("https://api.example.test/changed",),
                credential_refs=("credential://tenant/key-a",),
            ),
        )
    )


def test_supplied_capabilities_digest_must_match_and_round_trips_through_json() -> None:
    derived = _request()
    matching = _request(capabilitiesDigest=derived.capabilities_digest)

    assert matching == derived
    assert (
        UserDecisionRequest.model_validate_json(matching.model_dump_json(by_alias=True)) == matching
    )

    with pytest.raises(ValidationError, match="capabilitiesDigest"):
        _request(capabilitiesDigest=_digest("f"))


@pytest.mark.parametrize(
    "as_unordered",
    (
        pytest.param(lambda values: set(values), id="set"),
        pytest.param(lambda values: frozenset(values), id="frozenset"),
    ),
)
def test_request_rejects_unordered_or_duplicate_capabilities(
    as_unordered: Callable[[tuple[AuthorityCapability, ...]], object],
) -> None:
    first = _capability()
    second = _capability(WORKSPACE_B_REF)

    with pytest.raises(ValidationError, match="ordered"):
        _request(capabilities=as_unordered((first, second)))
    with pytest.raises(ValidationError, match="duplicate"):
        _request(capabilities=(first, first))


def test_request_uses_exact_aliases_schema_and_complete_json_shape() -> None:
    request = _request()
    alias_json = request.model_dump(by_alias=True, mode="json")

    assert request.schema_id == "magi.user_decision_request.v1"
    assert set(alias_json) == {
        "schemaId",
        "decisionRequestId",
        "principalId",
        "tenantId",
        "sessionId",
        "turnId",
        "taskContractId",
        "taskVersion",
        "taskContractDigest",
        "completionEpochId",
        "actionId",
        "authorityPartitionId",
        "normalizedRequestDigest",
        "capabilities",
        "capabilitiesDigest",
        "workspaceViewBindingDigest",
        "authorityCeilingDigest",
        "policyDigest",
        "pendingEventId",
        "reasonCodes",
        "createdAt",
        "expiresAt",
        "compareVersion",
    }
    assert alias_json["capabilitiesDigest"] == request.capabilities_digest

    with pytest.raises(ValidationError):
        _request(schemaId="magi.user_decision_request.v2")


def test_user_decision_schema_id_is_a_strict_discriminated_union_tag() -> None:
    adapter = TypeAdapter(
        Annotated[
            UserDecisionRequest | UserDecisionReceipt,
            Field(discriminator="schema_id"),
        ]
    )

    assert type(adapter.validate_python(_request_payload())) is UserDecisionRequest
    assert type(adapter.validate_python(_receipt_payload())) is UserDecisionReceipt

    with pytest.raises(ValidationError, match="union_tag_invalid"):
        adapter.validate_python(_request_payload(schemaId="magi.user_decision_request.v2"))

    class StringSubclass(str):
        pass

    with pytest.raises(ValidationError, match="exact string"):
        adapter.validate_python(
            _request_payload(schemaId=StringSubclass("magi.user_decision_request.v1"))
        )


@pytest.mark.parametrize(
    "field",
    (
        "decisionRequestId",
        "principalId",
        "tenantId",
        "sessionId",
        "turnId",
        "taskContractId",
        "completionEpochId",
        "actionId",
        "authorityPartitionId",
        "pendingEventId",
    ),
)
def test_request_requires_every_identity_to_be_a_nonempty_exact_string(field: str) -> None:
    with pytest.raises(ValidationError):
        _request(**{field: ""})

    class StringSubclass(str):
        pass

    with pytest.raises(ValidationError, match="exact string"):
        _request(**{field: StringSubclass("hidden")})


@pytest.mark.parametrize(
    "field",
    (
        "taskContractDigest",
        "normalizedRequestDigest",
        "authorityCeilingDigest",
        "policyDigest",
        "workspaceViewBindingDigest",
    ),
)
def test_request_requires_every_digest_to_be_a_canonical_exact_string(field: str) -> None:
    with pytest.raises(ValidationError, match="sha256"):
        _request(**{field: "not-a-digest"})

    class StringSubclass(str):
        pass

    with pytest.raises(ValidationError, match="exact string"):
        _request(**{field: StringSubclass(_digest("a"))})


@pytest.mark.parametrize("field,invalid", (("taskVersion", 1.0), ("compareVersion", False)))
def test_request_counters_are_strict_integers(field: str, invalid: object) -> None:
    with pytest.raises(ValidationError):
        _request(**{field: invalid})


def test_request_rejects_invalid_counter_ranges_and_reason_code_shapes() -> None:
    with pytest.raises(ValidationError):
        _request(taskVersion=0)
    with pytest.raises(ValidationError):
        _request(compareVersion=-1)
    with pytest.raises(ValidationError, match="ordered"):
        _request(reasonCodes={"sensitive_write"})
    with pytest.raises(ValidationError):
        _request(reasonCodes=())


@pytest.mark.parametrize(
    "created_at,expires_at",
    (
        pytest.param(NOW, NOW, id="equal"),
        pytest.param(NOW, NOW - timedelta(seconds=1), id="earlier"),
    ),
)
def test_request_expiry_must_follow_creation(
    created_at: datetime,
    expires_at: datetime,
) -> None:
    with pytest.raises(ValidationError, match="expiresAt"):
        _request(createdAt=created_at, expiresAt=expires_at)


@pytest.mark.parametrize(
    "field,value",
    (
        pytest.param("createdAt", NOW.replace(tzinfo=None), id="created-naive"),
        pytest.param("expiresAt", NOW.astimezone(timezone(timedelta(hours=9))), id="expiry-offset"),
        pytest.param("createdAt", 1_700_000_000, id="numeric"),
        pytest.param("createdAt", "1700000000", id="numeric-string"),
    ),
)
def test_request_datetimes_must_be_exact_utc_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match="datetime|UTC"):
        _request(**{field: value})


def test_request_rejects_datetime_subclasses_but_accepts_historical_windows() -> None:
    class DateTimeSubclass(datetime):
        pass

    subclass = DateTimeSubclass(2026, 7, 14, 12, 0, tzinfo=UTC)
    with pytest.raises(ValidationError, match="datetime"):
        _request(createdAt=subclass)

    historical = _request(
        createdAt="2000-01-01T00:00:00Z",
        expiresAt="2000-01-01T00:05:00Z",
    )
    assert historical.expires_at > historical.created_at


def test_receipt_uses_exact_aliases_schema_and_json_round_trip() -> None:
    receipt = _receipt()
    alias_json = receipt.model_dump(by_alias=True, mode="json")

    assert receipt.schema_id == "magi.user_decision_receipt.v1"
    assert set(alias_json) == {
        "schemaId",
        "receiptId",
        "decisionRequestId",
        "decision",
        "authenticatedActorId",
        "authenticationKeyId",
        "authenticationContextDigest",
        "authenticationNonceDigest",
        "transportReceiptDigest",
        "principalId",
        "tenantId",
        "sessionId",
        "turnId",
        "taskContractId",
        "taskVersion",
        "taskContractDigest",
        "completionEpochId",
        "actionId",
        "authorityPartitionId",
        "normalizedRequestDigest",
        "authorityCeilingDigest",
        "policyDigest",
        "capabilitiesDigest",
        "workspaceViewBindingDigest",
        "issuedAt",
        "expiresAt",
        "revokesReceiptDigest",
    }
    assert (
        UserDecisionReceipt.model_validate_json(receipt.model_dump_json(by_alias=True)) == receipt
    )

    with pytest.raises(ValidationError):
        _receipt(schemaId="magi.user_decision_receipt.v2")


@pytest.mark.parametrize(
    "field",
    (
        "receiptId",
        "decisionRequestId",
        "authenticatedActorId",
        "authenticationKeyId",
        "principalId",
        "tenantId",
        "sessionId",
        "turnId",
        "taskContractId",
        "completionEpochId",
        "actionId",
        "authorityPartitionId",
    ),
)
def test_receipt_requires_every_identity_to_be_nonempty(field: str) -> None:
    with pytest.raises(ValidationError):
        _receipt(**{field: ""})


@pytest.mark.parametrize(
    "field",
    (
        "authenticationContextDigest",
        "authenticationNonceDigest",
        "transportReceiptDigest",
        "taskContractDigest",
        "normalizedRequestDigest",
        "authorityCeilingDigest",
        "policyDigest",
        "capabilitiesDigest",
        "workspaceViewBindingDigest",
        "revokesReceiptDigest",
    ),
)
def test_receipt_requires_every_present_digest_to_be_canonical(field: str) -> None:
    companions = {"decision": "revoke"} if field == "revokesReceiptDigest" else {}
    with pytest.raises(ValidationError, match="sha256"):
        _receipt(**companions, **{field: "not-a-digest"})


def test_receipt_actor_must_equal_principal() -> None:
    with pytest.raises(ValidationError, match="authenticatedActorId"):
        _receipt(authenticatedActorId="different_actor")


@pytest.mark.parametrize("decision", ("approve", "deny"))
def test_approve_and_deny_forbid_revocation_target(decision: str) -> None:
    with pytest.raises(ValidationError, match="revokesReceiptDigest"):
        _receipt(decision=decision, revokesReceiptDigest=_digest("e"))


def test_revoke_requires_a_prior_receipt_digest() -> None:
    with pytest.raises(ValidationError, match="revokesReceiptDigest"):
        _receipt(decision="revoke", revokesReceiptDigest=None)

    receipt = _receipt(decision="revoke", revokesReceiptDigest=_digest("e"))
    assert receipt.revokes_receipt_digest == _digest("e")


def test_receipt_expiry_must_follow_issue_but_not_consult_wall_clock() -> None:
    with pytest.raises(ValidationError, match="expiresAt"):
        _receipt(issuedAt=NOW, expiresAt=NOW)

    historical_request = _request(
        createdAt="2000-01-01T00:00:00Z",
        expiresAt="2000-01-01T00:05:00Z",
    )
    historical_receipt = _receipt(
        historical_request,
        issuedAt="2000-01-01T00:00:01Z",
        expiresAt="2000-01-01T00:05:00Z",
    )
    assert historical_receipt.expires_at > historical_receipt.issued_at


@pytest.mark.parametrize("field", ("issuedAt", "expiresAt"))
def test_receipt_rejects_non_utc_numeric_and_subclass_datetimes(field: str) -> None:
    class DateTimeSubclass(datetime):
        pass

    invalid_values: tuple[object, ...] = (
        NOW.replace(tzinfo=None),
        NOW.astimezone(timezone(timedelta(hours=-5))),
        1_700_000_000,
        "1700000000",
        DateTimeSubclass(2026, 7, 14, 12, 0, tzinfo=UTC),
    )
    for value in invalid_values:
        with pytest.raises(ValidationError, match="datetime|UTC"):
            _receipt(**{field: value})


@pytest.mark.parametrize(
    "extra_field",
    ("nonce", "signature", "key", "rawNonce", "rawSignature", "keyMaterial"),
)
def test_raw_authentication_material_is_not_a_contract_field(extra_field: str) -> None:
    assert extra_field not in UserDecisionReceipt.model_fields
    with pytest.raises(ValidationError, match="canonical JSON requires an exact string"):
        _receipt(**{extra_field: b"opaque-authentication-material"})


@pytest.mark.parametrize(
    "receipt_changes",
    (
        pytest.param({"decisionRequestId": "decision_02"}, id="decision-request"),
        pytest.param(
            {"authenticatedActorId": "actor_02", "principalId": "actor_02"},
            id="actor-principal",
        ),
        pytest.param({"tenantId": "tenant_02"}, id="tenant"),
        pytest.param({"sessionId": "session_02"}, id="session"),
        pytest.param({"turnId": "turn_02"}, id="turn"),
        pytest.param({"taskContractId": "task_02"}, id="task-id"),
        pytest.param({"taskVersion": 2}, id="task-version"),
        pytest.param({"taskContractDigest": _digest("a")}, id="task-digest"),
        pytest.param({"completionEpochId": "epoch_02"}, id="epoch"),
        pytest.param({"actionId": "action_02"}, id="action"),
        pytest.param({"authorityPartitionId": "workspace_02"}, id="partition"),
        pytest.param({"normalizedRequestDigest": _digest("b")}, id="request-digest"),
        pytest.param({"authorityCeilingDigest": _digest("c")}, id="ceiling"),
        pytest.param({"policyDigest": _digest("d")}, id="policy"),
        pytest.param({"capabilitiesDigest": _digest("e")}, id="capabilities"),
        pytest.param({"workspaceViewBindingDigest": _digest("f")}, id="workspace-view"),
    ),
)
def test_binding_helper_rejects_every_request_binding_drift(
    receipt_changes: dict[str, object],
) -> None:
    request = _request()
    receipt = _receipt(request, **receipt_changes)

    with pytest.raises(ValueError, match="does not match"):
        validate_user_decision_receipt_binding(request, receipt)


def test_binding_helper_enforces_the_request_time_window() -> None:
    request = _request()
    issued_too_early = _receipt(
        request,
        issuedAt=request.created_at - timedelta(seconds=1),
        expiresAt=request.created_at + timedelta(seconds=1),
    )
    expires_too_late = _receipt(
        request,
        expiresAt=request.expires_at + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="issuedAt"):
        validate_user_decision_receipt_binding(request, issued_too_early)
    with pytest.raises(ValueError, match="expiresAt"):
        validate_user_decision_receipt_binding(request, expires_too_late)


def test_binding_helper_returns_the_exact_validated_receipt() -> None:
    request = _request()
    receipt = _receipt(request)

    assert validate_user_decision_receipt_binding(request, receipt) == receipt


def test_request_digest_matches_snapshot_utf8_canonical_json() -> None:
    request = _request(decisionRequestId="결정_01", principalId="사용자_01")
    request_payload = UserDecisionRequest.model_dump(
        request,
        by_alias=True,
        mode="json",
    )
    request_json = json.dumps(
        request_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    snapshot_digest = "sha256:" + sha256(request_json.encode("utf-8")).hexdigest()

    assert canonical_user_decision_request_digest(request) == snapshot_digest


def test_request_and_receipt_digests_cover_complete_alias_json() -> None:
    request = _request(decisionRequestId="결정_01", principalId="사용자_01")
    receipt = _receipt(request, receiptId="영수증_01")

    assert canonical_user_decision_receipt_digest(receipt) == canonical_digest(
        UserDecisionReceipt.model_dump(receipt, by_alias=True, mode="json")
    )
    assert canonical_user_decision_request_digest(
        _request(decisionRequestId="결정_02", principalId="사용자_01")
    ) != canonical_user_decision_request_digest(request)
    assert canonical_user_decision_receipt_digest(
        _receipt(request, receiptId="영수증_02")
    ) != canonical_user_decision_receipt_digest(receipt)


def test_request_normalizes_mutable_capability_subclasses_and_rejects_hidden_semantics() -> None:
    class MutableCapability(AuthorityCapability):
        model_config = ConfigDict(frozen=False)

    class SemanticCapability(AuthorityCapability):
        semantic_variant: str = Field(alias="semanticVariant")

    external = MutableCapability(
        effectClass="workspace.write",
        resourceRef=WORKSPACE_A_REF,
        networkRefs=(),
        credentialRefs=(),
        workspaceViewBindingDigest=_digest("8"),
    )
    request = _request(capabilities=(external,))
    assert type(request.capabilities[0]) is AuthorityCapability
    assert request.capabilities[0] is not external

    external.effect_class = "workspace.delete"
    assert request.capabilities[0].effect_class == "workspace.write"

    semantic = SemanticCapability(
        effectClass="workspace.write",
        resourceRef=WORKSPACE_A_REF,
        networkRefs=(),
        credentialRefs=(),
        workspaceViewBindingDigest=_digest("8"),
        semanticVariant="hidden",
    )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _request(capabilities=(semantic,))


def test_canonical_capabilities_helper_rejects_subclasses_ducks_and_tampering() -> None:
    class SemanticCapability(AuthorityCapability):
        semantic_variant: str = Field(alias="semanticVariant")

    capability = _capability()
    semantic = SemanticCapability(
        **capability.model_dump(by_alias=True),
        semanticVariant="hidden",
    )
    duck = SimpleNamespace(**capability.model_dump())

    for candidate in ((semantic,), (duck,), [capability]):
        with pytest.raises(TypeError):
            canonical_capabilities_digest(candidate)  # type: ignore[arg-type]

    capability.__dict__["effect_class"] = ""
    with pytest.raises(ValidationError):
        canonical_capabilities_digest((capability,))


def test_digest_and_binding_helpers_reject_subclasses_ducks_and_tampered_instances() -> None:
    class SemanticRequest(UserDecisionRequest):
        semantic_variant: str = Field(alias="semanticVariant")

    class SemanticReceipt(UserDecisionReceipt):
        semantic_variant: str = Field(alias="semanticVariant")

    class SemanticResume(AuthorityResumeBinding):
        semantic_variant: str = Field(alias="semanticVariant")

    request = _request()
    receipt = _receipt(request)
    resume = _resume()
    semantic_request = SemanticRequest.model_validate(
        {**request.model_dump(by_alias=True), "semanticVariant": "hidden"}
    )
    semantic_receipt = SemanticReceipt.model_validate(
        {**receipt.model_dump(by_alias=True), "semanticVariant": "hidden"}
    )
    semantic_resume = SemanticResume.model_validate(
        {**resume.model_dump(by_alias=True), "semanticVariant": "hidden"}
    )

    with pytest.raises(TypeError, match="exact UserDecisionRequest"):
        canonical_user_decision_request_digest(semantic_request)
    with pytest.raises(TypeError, match="exact UserDecisionReceipt"):
        canonical_user_decision_receipt_digest(semantic_receipt)
    with pytest.raises(TypeError, match="exact AuthorityResumeBinding"):
        canonical_authority_resume_binding_digest(semantic_resume)
    with pytest.raises(TypeError, match="exact UserDecisionRequest"):
        validate_user_decision_receipt_binding(SimpleNamespace(), receipt)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact UserDecisionReceipt"):
        validate_user_decision_receipt_binding(request, SimpleNamespace())  # type: ignore[arg-type]

    request.__dict__["task_version"] = 1.0
    with pytest.raises(ValidationError):
        canonical_user_decision_request_digest(request)
    with pytest.raises(ValidationError):
        validate_user_decision_receipt_binding(request, receipt)

    receipt = _receipt()
    receipt.__dict__["task_version"] = False
    with pytest.raises(ValidationError):
        canonical_user_decision_receipt_digest(receipt)

    resume.__dict__["expected_head_sequence"] = -1
    with pytest.raises(ValidationError):
        canonical_authority_resume_binding_digest(resume)


def test_user_decision_contracts_are_frozen_and_close_copy_bypasses() -> None:
    for contract in (_request(), _receipt(), _resume()):
        with pytest.raises(ValidationError):
            contract.__setattr__(next(iter(type(contract).model_fields)), "changed")
        with pytest.raises(ValueError, match="model_copy update"):
            contract.model_copy(update={next(iter(type(contract).model_fields)): "changed"})
        with pytest.raises(ValueError, match="copy update"):
            contract.copy(update={next(iter(type(contract).model_fields)): "changed"})


def test_resume_binding_uses_the_exact_planned_aliases() -> None:
    binding = _resume()
    assert set(binding.model_dump(by_alias=True)) == set(_resume_payload())
    assert (
        AuthorityResumeBinding.model_validate_json(binding.model_dump_json(by_alias=True))
        == binding
    )


@pytest.mark.parametrize(
    "field",
    (
        "decisionRequestId",
        "authenticatedActorId",
        "sessionId",
        "turnId",
        "runId",
        "actionId",
        "taskContractId",
        "completionEpochId",
        "authorityPartitionId",
        "stateProjectionId",
    ),
)
def test_resume_binding_requires_all_identities_to_be_nonempty_exact_strings(field: str) -> None:
    with pytest.raises(ValidationError):
        _resume(**{field: ""})

    class StringSubclass(str):
        pass

    with pytest.raises(ValidationError, match="exact string"):
        _resume(**{field: StringSubclass("hidden")})


@pytest.mark.parametrize(
    "field",
    (
        "taskContractDigest",
        "transcriptDigest",
        "checkpointDigest",
        "expectedHeadHash",
        "expectedStateEventHash",
        "expectedStateRoot",
    ),
)
def test_resume_binding_requires_all_hashes_and_roots_to_be_canonical(field: str) -> None:
    with pytest.raises(ValidationError, match="sha256"):
        _resume(**{field: "not-a-digest"})


@pytest.mark.parametrize(
    "field",
    (
        "taskVersion",
        "expectedHeadSequence",
        "expectedHeadCompareVersion",
        "expectedStateSequence",
        "expectedStateCompareVersion",
    ),
)
@pytest.mark.parametrize("invalid", (-1, False, 1.0, "1"))
def test_resume_binding_counters_are_exact_nonnegative_integers(
    field: str,
    invalid: object,
) -> None:
    if field == "taskVersion" and invalid == -1:
        invalid = 0
    with pytest.raises(ValidationError):
        _resume(**{field: invalid})


@pytest.mark.parametrize(
    "field,changed",
    (
        ("decisionRequestId", "decision_02"),
        ("authenticatedActorId", "actor_02"),
        ("sessionId", "session_02"),
        ("turnId", "turn_02"),
        ("runId", "run_02"),
        ("actionId", "action_02"),
        ("taskContractId", "task_02"),
        ("taskVersion", 2),
        ("taskContractDigest", _digest("a")),
        ("completionEpochId", "epoch_02"),
        ("transcriptDigest", _digest("b")),
        ("checkpointDigest", _digest("c")),
        ("authorityPartitionId", "workspace_02"),
        ("expectedHeadSequence", 11),
        ("expectedHeadHash", _digest("d")),
        ("expectedHeadCompareVersion", 12),
        ("stateProjectionId", "projection_02"),
        ("expectedStateSequence", 9),
        ("expectedStateEventHash", _digest("e")),
        ("expectedStateRoot", _digest("f")),
        ("expectedStateCompareVersion", 10),
    ),
)
def test_resume_binding_digest_is_sensitive_to_every_field(
    field: str,
    changed: object,
) -> None:
    baseline = _resume()
    modified = _resume(**{field: changed})

    assert canonical_authority_resume_binding_digest(modified) != (
        canonical_authority_resume_binding_digest(baseline)
    )


def test_resume_binding_digest_uses_complete_alias_json_and_repo_kernel() -> None:
    binding = _resume()

    assert canonical_authority_resume_binding_digest(binding) == canonical_digest(
        AuthorityResumeBinding.model_dump(binding, by_alias=True, mode="json")
    )


def test_ports_are_runtime_checkable_structural_boundaries() -> None:
    request = _request()
    receipt = _receipt(request)
    binding = _resume()

    class Verifier:
        def verify(
            self,
            *,
            opaque_envelope: object,
            request: UserDecisionRequest,
        ) -> UserDecisionReceipt:
            _ = opaque_envelope, request
            return receipt

    class KeyLookup:
        def key_for(
            self,
            *,
            key_id: str,
            tenant_id: str,
            principal_id: str,
            authentication_context_digest: str,
        ) -> bytes | None:
            _ = key_id, tenant_id, principal_id, authentication_context_digest
            return b"opaque-key"

    class ResumeVerifier:
        def verify_current(self, binding: AuthorityResumeBinding) -> AuthorityResumeBinding:
            return binding

    assert isinstance(Verifier(), UserDecisionVerifierPort)
    assert isinstance(KeyLookup(), UserDecisionKeyPort)
    assert isinstance(ResumeVerifier(), ResumeBindingVerifierPort)
    assert Verifier().verify(opaque_envelope=object(), request=request) == receipt
    assert ResumeVerifier().verify_current(binding) == binding

    assert not isinstance(object(), UserDecisionVerifierPort)
    assert not isinstance(object(), UserDecisionKeyPort)
    assert not isinstance(object(), ResumeBindingVerifierPort)
