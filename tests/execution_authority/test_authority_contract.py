from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum
from types import SimpleNamespace

import pytest
from pydantic import ConfigDict, Field, ValidationError

from magi_agent.execution_authority.contracts import (
    AuthorityCapability,
    AuthorityContract,
    AuthorityDecision,
    AuthorityInput,
    canonical_authority_contract_digest,
    resolve_authority,
    validate_delegated_authority,
)
from magi_agent.ops.safety import canonical_digest


def _digest(character: str) -> str:
    return "sha256:" + character * 64


class _BehaviorString(str):
    def __eq__(self, other: object) -> bool:
        return str.__eq__(self, other)

    __hash__ = str.__hash__


def _as_bytes(value: str) -> object:
    return value.encode()


def _as_enum(value: str) -> object:
    return Enum("_WireStringEnum", {"VALUE": value}, type=str).VALUE


def _as_behavior_string(value: str) -> object:
    return _BehaviorString(value)


_NON_EXACT_STRING_FACTORIES: tuple[Callable[[str], object], ...] = (
    _as_bytes,
    _as_enum,
    _as_behavior_string,
)


def _capability(
    effect_class: str,
    resource_ref: str,
    *,
    network_refs: tuple[str, ...] = (),
    credential_refs: tuple[str, ...] = (),
    workspace_view_binding_digest: str | None = None,
) -> AuthorityCapability:
    if workspace_view_binding_digest is None and (
        effect_class.startswith("workspace.") or resource_ref.startswith("workspace:")
    ):
        workspace_view_binding_digest = _digest("e")
    return AuthorityCapability(
        effectClass=effect_class,
        resourceRef=resource_ref,
        networkRefs=network_refs,
        credentialRefs=credential_refs,
        workspaceViewBindingDigest=workspace_view_binding_digest,
    )


def _contract(**updates: object) -> AuthorityContract:
    payload: dict[str, object] = {
        "authorityContractId": "authority_01",
        "issuerId": "issuer_01",
        "principalId": "principal_01",
        "tenantId": "tenant_01",
        "sessionId": "session_01",
        "turnId": "turn_01",
        "childActorId": None,
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
        "capabilities": [
            {
                "effectClass": "workspace.write",
                "resourceRef": "workspace:project",
                "networkRefs": [],
                "credentialRefs": [],
                "workspaceViewBindingDigest": _digest("e"),
            }
        ],
        "workspaceViewBindingDigest": _digest("e"),
        "sandboxProfileDigest": _digest("c"),
        "guardianCeilingDigest": _digest("d"),
        "expiresAt": datetime(2025, 1, 1, tzinfo=UTC),
        "revokedAt": None,
        "revocationDigest": None,
        "fencingToken": 7,
        "decisionRequestId": None,
        "resumeBindingDigest": None,
        "parentAuthorityDigest": None,
        "delegationChain": [],
    }
    payload.update(updates)
    return AuthorityContract.model_validate(payload)


def _delegated_child(
    parent: AuthorityContract,
    **updates: object,
) -> AuthorityContract:
    parent_digest = canonical_authority_contract_digest(parent)
    payload = parent.model_dump(by_alias=True, mode="python")
    payload.update(
        {
            "authorityContractId": "authority_child",
            "issuerId": "issuer_child",
            "childActorId": "child_actor_01",
            "capabilities": [parent.capabilities[0]],
            "expiresAt": parent.expires_at - timedelta(minutes=5),
            "parentAuthorityDigest": parent_digest,
            "delegationChain": (*parent.delegation_chain, parent_digest),
        }
    )
    payload.update(updates)
    return AuthorityContract.model_validate(payload)


def test_resolver_intersects_scopes_and_denies_only_the_exact_capability() -> None:
    workspace_write = _capability("workspace.write", "workspace:project")
    process_exec = _capability(
        "process.exec",
        "binary:git",
        network_refs=("network:github",),
        credential_refs=("credential:github",),
    )
    action_digest = _digest("e")

    decision = resolve_authority(
        inputs=(
            AuthorityInput(
                source="user",
                decision="allow",
                capabilities=(workspace_write, process_exec),
            ),
            AuthorityInput(
                source="session",
                decision="allow",
                capabilities=(workspace_write,),
            ),
            AuthorityInput(
                source="platform",
                decision="deny",
                capabilities=(process_exec,),
            ),
        ),
        action_digest=action_digest,
    )

    assert decision.status == "allow"
    assert decision.reason_codes == ("authority_intersection", action_digest)
    assert decision.capabilities == (workspace_write,)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    (
        (("allow", "review_required"), "review_required"),
        (
            ("review_required", "allow", "user_decision_required"),
            "user_decision_required",
        ),
    ),
)
def test_resolver_uses_review_and_user_decision_precedence(
    statuses: tuple[str, ...],
    expected: str,
) -> None:
    capability = _capability("workspace.write", "workspace:project")

    decision = resolve_authority(
        inputs=tuple(
            AuthorityInput(
                source="user",
                decision=status,  # type: ignore[arg-type]
                capabilities=(capability,),
            )
            for status in statuses
        ),
        action_digest=_digest("f"),
    )

    assert decision.status == expected


@pytest.mark.parametrize(
    "guardian_decision",
    ("allow", "review_required", "user_decision_required"),
)
def test_guardian_cannot_originate_authority(guardian_decision: str) -> None:
    capability = _capability("workspace.write", "workspace:project")

    decision = resolve_authority(
        inputs=(
            AuthorityInput(
                source="guardian",
                decision=guardian_decision,  # type: ignore[arg-type]
                capabilities=(capability,),
            ),
        ),
        action_digest=_digest("e"),
    )

    assert decision.status == "deny"
    assert decision.reason_codes == ("no_allowing_authority",)
    assert decision.capabilities == ()


def test_guardian_attenuates_non_guardian_capabilities() -> None:
    workspace_write = _capability("workspace.write", "workspace:project")
    process_exec = _capability("process.exec", "binary:git")

    decision = resolve_authority(
        inputs=(
            AuthorityInput(
                source="user",
                decision="allow",
                capabilities=(workspace_write, process_exec),
            ),
            AuthorityInput(
                source="guardian",
                decision="allow",
                capabilities=(workspace_write,),
            ),
        ),
        action_digest=_digest("e"),
    )

    assert decision.status == "allow"
    assert decision.capabilities == (workspace_write,)


def test_authority_intersection_selects_the_narrower_hierarchical_scope() -> None:
    broad = _capability(
        "workspace.write",
        "workspace:project",
        network_refs=("https://api.example.test/",),
    )
    narrow = _capability(
        "workspace.write",
        "workspace:project/reports",
        network_refs=("https://api.example.test/reports",),
    )

    decision = resolve_authority(
        inputs=(
            AuthorityInput(source="platform", decision="allow", capabilities=(broad,)),
            AuthorityInput(source="tool", decision="allow", capabilities=(narrow,)),
        ),
        action_digest=_digest("e"),
    )

    assert decision.status == "allow"
    assert decision.capabilities == (narrow,)


def test_broad_guardian_deny_blocks_a_descendant_capability() -> None:
    broad = _capability("workspace.write", "workspace:project")
    child = _capability("workspace.write", "workspace:project/secret")

    decision = resolve_authority(
        inputs=(
            AuthorityInput(source="user", decision="allow", capabilities=(child,)),
            AuthorityInput(source="guardian", decision="deny", capabilities=(broad,)),
        ),
        action_digest=_digest("e"),
    )

    assert decision.status == "deny"
    assert decision.reason_codes == ("deny_wins",)
    assert decision.capabilities == ()


@pytest.mark.parametrize(
    "guardian_decision",
    ("review_required", "user_decision_required"),
)
def test_guardian_status_can_only_restrict_non_guardian_origin(
    guardian_decision: str,
) -> None:
    capability = _capability("workspace.write", "workspace:project")

    decision = resolve_authority(
        inputs=(
            AuthorityInput(
                source="user",
                decision="allow",
                capabilities=(capability,),
            ),
            AuthorityInput(
                source="guardian",
                decision=guardian_decision,  # type: ignore[arg-type]
                capabilities=(capability,),
            ),
        ),
        action_digest=_digest("e"),
    )

    assert decision.status == guardian_decision
    assert decision.capabilities == (capability,)


def test_delegated_authority_requires_a_distinct_contract_identity() -> None:
    parent = _contract()
    child = _delegated_child(parent, authorityContractId=parent.authority_contract_id)

    with pytest.raises(ValueError, match="distinct authorityContractId"):
        validate_delegated_authority(parent, child)


def test_delegation_can_remove_every_workspace_capability_and_binding() -> None:
    workspace_write = _capability("workspace.write", "workspace:project")
    process_exec = _capability("process.exec", "binary:git")
    parent = _contract(capabilities=[workspace_write, process_exec])
    child = _delegated_child(
        parent,
        capabilities=[process_exec],
        workspaceViewBindingDigest=None,
    )

    assert validate_delegated_authority(parent, child) == child


def test_authority_contract_binds_the_complete_action_envelope() -> None:
    contract = _contract()

    assert contract.authority_contract_id == "authority_01"
    assert contract.task_contract_id == "task_01"
    assert contract.task_version == 3
    assert contract.task_contract_digest == _digest("1")
    assert contract.policy_digest == _digest("2")
    assert contract.normalized_request_digest == _digest("3")
    assert contract.command_digest == _digest("4")
    assert contract.arguments_digest == _digest("5")
    assert contract.working_directory_digest == _digest("6")
    assert contract.environment_digest == _digest("7")
    assert contract.request_body_digest == _digest("8")
    assert contract.credential_scope_digest == _digest("9")
    assert contract.network_digest == _digest("a")
    assert contract.disclosure_digest == _digest("b")
    assert contract.workspace_view_binding_digest == _digest("e")
    assert contract.sandbox_profile_digest == _digest("c")
    assert contract.guardian_ceiling_digest == _digest("d")
    assert contract.expires_at == datetime(2025, 1, 1, tzinfo=UTC)
    assert contract.fencing_token == 7
    assert contract.maximum_uses == 1
    assert contract.schema_version == 1
    assert type(contract.capabilities[0]) is AuthorityCapability
    assert contract.model_dump(by_alias=True, mode="json")["authorityContractId"] == (
        "authority_01"
    )


def test_valid_delegation_attenuates_to_a_subset_and_shorter_expiry() -> None:
    workspace_write = _capability("workspace.write", "workspace:project")
    process_exec = _capability("process.exec", "binary:git")
    parent = _contract(capabilities=(workspace_write, process_exec))
    child = _delegated_child(parent)

    validated = validate_delegated_authority(parent, child)

    assert validated == child
    assert validated is not child
    assert type(validated) is AuthorityContract
    assert validated.capabilities == (workspace_write,)
    assert validated.expires_at < parent.expires_at
    parent_digest = canonical_authority_contract_digest(parent)
    assert validated.parent_authority_digest == parent_digest
    assert validated.delegation_chain == (*parent.delegation_chain, parent_digest)


def test_delegation_can_attenuate_resource_network_and_credential_scopes() -> None:
    parent_capability = _capability(
        "workspace.write",
        "workspace:project",
        network_refs=("https://api.example.test/",),
        credential_refs=("credential:storage",),
    )
    child_capability = _capability(
        "workspace.write",
        "workspace:project/reports",
        network_refs=("https://api.example.test/reports",),
        credential_refs=("credential:storage/reports",),
    )
    parent = _contract(capabilities=(parent_capability,))
    child = _delegated_child(parent, capabilities=(child_capability,))

    assert validate_delegated_authority(parent, child) == child


@pytest.mark.parametrize(
    "capabilities",
    (
        pytest.param(
            (
                _capability("workspace.write", "workspace:project"),
                _capability("network.connect", "network:unbound"),
            ),
            id="added",
        ),
        pytest.param(
            (_capability("workspace.write", "workspace:other"),),
            id="changed",
        ),
    ),
)
def test_delegation_rejects_added_or_changed_capabilities(
    capabilities: tuple[AuthorityCapability, ...],
) -> None:
    parent = _contract(
        capabilities=(
            _capability("workspace.write", "workspace:project"),
            _capability("process.exec", "binary:git"),
        )
    )
    child = _delegated_child(parent, capabilities=capabilities)

    with pytest.raises(ValueError, match="capabilities"):
        validate_delegated_authority(parent, child)


def test_delegation_rejects_a_longer_expiry() -> None:
    parent = _contract()
    child = _delegated_child(parent, expiresAt=parent.expires_at + timedelta(seconds=1))

    with pytest.raises(ValueError, match="expiresAt"):
        validate_delegated_authority(parent, child)


@pytest.mark.parametrize(
    "updates",
    (
        pytest.param({"taskContractDigest": _digest("0")}, id="task"),
        pytest.param({"policyDigest": _digest("0")}, id="policy"),
        pytest.param({"normalizedRequestDigest": _digest("0")}, id="request"),
        pytest.param({"actionId": "action_02"}, id="action"),
        pytest.param({"argumentsDigest": _digest("0")}, id="resource"),
        pytest.param({"sandboxProfileDigest": _digest("0")}, id="sandbox"),
        pytest.param({"guardianCeilingDigest": _digest("0")}, id="guardian"),
        pytest.param({"fencingToken": 8}, id="fence"),
        pytest.param({"tenantId": "tenant_02"}, id="tenant"),
        pytest.param({"sessionId": "session_02"}, id="session"),
        pytest.param({"turnId": "turn_02"}, id="turn"),
        pytest.param({"principalId": "principal_02"}, id="principal"),
    ),
)
def test_delegation_rejects_changes_to_exact_parent_bindings(
    updates: dict[str, object],
) -> None:
    parent = _contract()
    child = _delegated_child(parent, **updates)

    with pytest.raises(ValueError, match="may not differ"):
        validate_delegated_authority(parent, child)


def test_delegation_rejects_the_wrong_parent_digest_or_chain() -> None:
    parent = _contract()
    parent_digest = canonical_authority_contract_digest(parent)
    wrong_digest = _digest("0")
    wrong_parent = _delegated_child(
        parent,
        parentAuthorityDigest=wrong_digest,
        delegationChain=(wrong_digest,),
    )
    wrong_chain = _delegated_child(
        parent,
        delegationChain=(wrong_digest, parent_digest),
    )

    for child in (wrong_parent, wrong_chain):
        with pytest.raises(ValueError, match="parentAuthorityDigest|delegationChain"):
            validate_delegated_authority(parent, child)


@pytest.mark.parametrize(
    "invalid_time",
    (
        pytest.param(datetime(2026, 1, 1), id="naive"),
        pytest.param(
            datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=9))),
            id="non-utc",
        ),
    ),
)
def test_contract_rejects_naive_and_non_utc_datetimes(invalid_time: datetime) -> None:
    with pytest.raises(ValidationError, match="UTC"):
        _contract(expiresAt=invalid_time)

    with pytest.raises(ValidationError, match="UTC"):
        _contract(revokedAt=invalid_time, revocationDigest=_digest("0"))


def test_contract_rejects_behavior_overriding_datetime_subclasses() -> None:
    class LiarDateTime(datetime):
        def __gt__(self, other: object) -> bool:
            _ = other
            return False

    with pytest.raises(ValidationError, match="exact datetime"):
        _contract(expiresAt=LiarDateTime(2030, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize("field", ("expiresAt", "revokedAt"))
@pytest.mark.parametrize("numeric_time", (1_735_689_600, 1_735_689_600.0))
def test_contract_rejects_numeric_datetime_inputs(
    field: str,
    numeric_time: int | float,
) -> None:
    companions = {"revocationDigest": _digest("0")} if field == "revokedAt" else {}

    with pytest.raises(
        ValidationError,
        match="datetime instances or ISO strings|floating-point numbers",
    ):
        _contract(**companions, **{field: numeric_time})


def test_contract_accepts_exact_datetime_and_iso_datetime_strings() -> None:
    expected = datetime(2025, 1, 1, tzinfo=UTC)

    assert _contract(expiresAt=expected).expires_at == expected
    assert _contract(expiresAt="2025-01-01T00:00:00Z").expires_at == expected

    payload = _contract().model_dump(by_alias=True, mode="json")
    payload["expiresAt"] = "2025-01-01T00:00:00Z"
    assert AuthorityContract.model_validate_json(json.dumps(payload)).expires_at == expected


def test_contract_json_rejects_numeric_datetime_inputs() -> None:
    payload = _contract().model_dump(by_alias=True, mode="json")
    payload["expiresAt"] = 1_735_689_600

    with pytest.raises(ValidationError, match="datetime instances or ISO strings"):
        AuthorityContract.model_validate_json(json.dumps(payload))


def test_contract_accepts_historically_expired_utc_envelopes() -> None:
    expires_at = datetime(2000, 1, 1, tzinfo=UTC)

    assert _contract(expiresAt=expires_at).expires_at == expires_at


@pytest.mark.parametrize(
    ("updates", "message"),
    (
        ({"revokedAt": datetime(2024, 1, 1, tzinfo=UTC)}, "revokedAt"),
        ({"revocationDigest": _digest("0")}, "revocationDigest"),
        ({"decisionRequestId": "decision_01"}, "decisionRequestId"),
        ({"resumeBindingDigest": _digest("0")}, "resumeBindingDigest"),
    ),
)
def test_contract_requires_revocation_and_resume_pairs(
    updates: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _contract(**updates)


@pytest.mark.parametrize(
    "updates",
    (
        pytest.param({"taskContractDigest": "not-a-digest"}, id="task"),
        pytest.param({"policyDigest": "not-a-digest"}, id="policy"),
        pytest.param({"normalizedRequestDigest": "not-a-digest"}, id="request"),
        pytest.param({"commandDigest": "not-a-digest"}, id="command"),
        pytest.param({"argumentsDigest": "not-a-digest"}, id="arguments"),
        pytest.param({"workingDirectoryDigest": "not-a-digest"}, id="working-directory"),
        pytest.param({"environmentDigest": "not-a-digest"}, id="environment"),
        pytest.param({"requestBodyDigest": "not-a-digest"}, id="request-body"),
        pytest.param({"credentialScopeDigest": "not-a-digest"}, id="credential-scope"),
        pytest.param({"networkDigest": "not-a-digest"}, id="network"),
        pytest.param({"disclosureDigest": "not-a-digest"}, id="disclosure"),
        pytest.param({"sandboxProfileDigest": "not-a-digest"}, id="sandbox"),
        pytest.param({"guardianCeilingDigest": "not-a-digest"}, id="guardian"),
        pytest.param(
            {
                "revokedAt": datetime(2024, 1, 1, tzinfo=UTC),
                "revocationDigest": "not-a-digest",
            },
            id="revocation",
        ),
        pytest.param(
            {"decisionRequestId": "decision_01", "resumeBindingDigest": "not-a-digest"},
            id="resume",
        ),
        pytest.param({"parentAuthorityDigest": "not-a-digest"}, id="parent"),
        pytest.param({"delegationChain": ("not-a-digest",)}, id="chain"),
    ),
)
def test_every_contract_digest_field_requires_canonical_sha256(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="sha256"):
        _contract(**updates)


@pytest.mark.parametrize("invalid", (True, "3", b"3", 3.0))
@pytest.mark.parametrize("field", ("taskVersion", "fencingToken"))
def test_authority_critical_integers_reject_coercible_values(
    field: str,
    invalid: object,
) -> None:
    with pytest.raises(ValidationError):
        _contract(**{field: invalid})


def test_authority_critical_integer_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        _contract(taskVersion=0)
    with pytest.raises(ValidationError):
        _contract(fencingToken=-1)


@pytest.mark.parametrize("invalid", (True, "1", b"1", 1.0))
@pytest.mark.parametrize("field", ("maximumUses", "schemaVersion"))
def test_literal_one_fields_require_the_exact_builtin_integer(
    field: str,
    invalid: object,
) -> None:
    with pytest.raises(ValidationError, match="exact integer 1|floating-point numbers"):
        _contract(**{field: invalid})


@pytest.mark.parametrize(
    "updates",
    (
        pytest.param(
            {"parentAuthorityDigest": None, "delegationChain": (_digest("0"),)},
            id="root-with-chain",
        ),
        pytest.param(
            {"parentAuthorityDigest": _digest("0"), "delegationChain": ()},
            id="delegated-without-chain",
        ),
        pytest.param(
            {
                "parentAuthorityDigest": _digest("0"),
                "delegationChain": (_digest("1"),),
            },
            id="delegated-with-wrong-chain-tail",
        ),
    ),
)
def test_contract_locally_couples_parent_digest_and_delegation_chain(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="parentAuthorityDigest|delegationChain"):
        _contract(**updates)


@pytest.mark.parametrize(
    "as_unordered",
    (
        pytest.param(lambda values: set(values), id="set"),
        pytest.param(lambda values: frozenset(values), id="frozenset"),
    ),
)
@pytest.mark.parametrize("field", ("networkRefs", "credentialRefs"))
def test_capability_refs_reject_unordered_inputs(
    field: str,
    as_unordered: Callable[[tuple[object, ...]], object],
) -> None:
    with pytest.raises(ValidationError, match="ordered"):
        AuthorityCapability.model_validate(
            {
                "effectClass": "network.connect",
                "resourceRef": "network:github",
                "networkRefs": (),
                "credentialRefs": (),
                field: as_unordered(("ref:a", "ref:b")),
            }
        )


@pytest.mark.parametrize(
    "as_unordered",
    (
        pytest.param(lambda values: set(values), id="set"),
        pytest.param(lambda values: frozenset(values), id="frozenset"),
    ),
)
def test_authority_models_reject_unordered_capabilities_and_reason_codes(
    as_unordered: Callable[[tuple[object, ...]], object],
) -> None:
    capability = _capability("workspace.write", "workspace:project")

    with pytest.raises(ValidationError, match="ordered"):
        AuthorityInput.model_validate(
            {
                "source": "user",
                "decision": "allow",
                "capabilities": as_unordered((capability,)),
            }
        )
    with pytest.raises(ValidationError, match="ordered"):
        AuthorityDecision.model_validate(
            {
                "status": "allow",
                "reasonCodes": ("authority_intersection",),
                "capabilities": as_unordered((capability,)),
            }
        )
    with pytest.raises(ValidationError, match="ordered"):
        AuthorityDecision.model_validate(
            {
                "status": "allow",
                "reasonCodes": as_unordered(("authority_intersection",)),
                "capabilities": (capability,),
            }
        )
    with pytest.raises(ValidationError, match="ordered"):
        _contract(capabilities=as_unordered((capability,)))
    with pytest.raises(ValidationError, match="ordered"):
        _contract(delegationChain=as_unordered((_digest("0"),)))


def test_contract_rejects_duplicate_capabilities() -> None:
    capability = _capability("workspace.write", "workspace:project")

    with pytest.raises(ValidationError, match="duplicate"):
        _contract(capabilities=(capability, capability))


def test_inputs_and_decisions_reject_duplicate_capabilities() -> None:
    capability = _capability("workspace.write", "workspace:project")

    with pytest.raises(ValidationError, match="duplicate"):
        AuthorityInput(
            source="user",
            decision="allow",
            capabilities=(capability, capability),
        )
    with pytest.raises(ValidationError, match="duplicate"):
        AuthorityDecision(
            status="allow",
            reasonCodes=("authority_intersection",),
            capabilities=(capability, capability),
        )


def test_capability_and_contract_identity_strings_are_nonempty() -> None:
    with pytest.raises(ValidationError):
        _capability("", "workspace:project")
    with pytest.raises(ValidationError):
        _capability("workspace.write", "")
    with pytest.raises(ValidationError):
        _contract(authorityContractId="")
    with pytest.raises(ValidationError):
        _contract(childActorId="")
    with pytest.raises(ValidationError):
        _contract(decisionRequestId="", resumeBindingDigest=_digest("0"))


@pytest.mark.parametrize("make_invalid", _NON_EXACT_STRING_FACTORIES)
@pytest.mark.parametrize(
    ("field", "valid"),
    (
        ("effectClass", "workspace.write"),
        ("resourceRef", "workspace:project"),
    ),
)
def test_capability_identity_fields_require_exact_strings(
    field: str,
    valid: str,
    make_invalid: Callable[[str], object],
) -> None:
    payload: dict[str, object] = {
        "effectClass": "workspace.write",
        "resourceRef": "workspace:project",
        "networkRefs": (),
        "credentialRefs": (),
    }
    payload[field] = make_invalid(valid)

    with pytest.raises(ValidationError, match="exact string"):
        AuthorityCapability.model_validate(payload)


@pytest.mark.parametrize("make_invalid", _NON_EXACT_STRING_FACTORIES)
@pytest.mark.parametrize("field", ("networkRefs", "credentialRefs"))
def test_capability_ref_elements_require_exact_strings(
    field: str,
    make_invalid: Callable[[str], object],
) -> None:
    payload: dict[str, object] = {
        "effectClass": "network.connect",
        "resourceRef": "network:github",
        "networkRefs": (),
        "credentialRefs": (),
    }
    payload[field] = (make_invalid("ref:github"),)

    with pytest.raises(ValidationError, match="exact string"):
        AuthorityCapability.model_validate(payload)


@pytest.mark.parametrize("make_invalid", _NON_EXACT_STRING_FACTORIES)
@pytest.mark.parametrize(
    ("model", "field", "valid"),
    (
        (AuthorityInput, "source", "user"),
        (AuthorityInput, "decision", "allow"),
        (AuthorityDecision, "status", "allow"),
    ),
)
def test_authority_status_fields_require_exact_strings(
    model: type[AuthorityInput] | type[AuthorityDecision],
    field: str,
    valid: str,
    make_invalid: Callable[[str], object],
) -> None:
    capability = _capability("workspace.write", "workspace:project")
    if model is AuthorityInput:
        payload: dict[str, object] = {
            "source": "user",
            "decision": "allow",
            "capabilities": (capability,),
        }
    else:
        payload = {
            "status": "allow",
            "reasonCodes": ("authority_intersection",),
            "capabilities": (capability,),
        }
    payload[field] = make_invalid(valid)

    with pytest.raises(ValidationError, match="exact string"):
        model.model_validate(payload)


@pytest.mark.parametrize("make_invalid", _NON_EXACT_STRING_FACTORIES)
def test_authority_reason_code_elements_require_exact_strings(
    make_invalid: Callable[[str], object],
) -> None:
    with pytest.raises(ValidationError, match="exact string"):
        AuthorityDecision.model_validate(
            {
                "status": "allow",
                "reasonCodes": (make_invalid("authority_intersection"),),
                "capabilities": (_capability("workspace.write", "workspace:project"),),
            }
        )


@pytest.mark.parametrize("make_invalid", _NON_EXACT_STRING_FACTORIES)
@pytest.mark.parametrize(
    ("field", "valid", "companions"),
    (
        ("authorityContractId", "authority_01", {}),
        ("issuerId", "issuer_01", {}),
        ("principalId", "principal_01", {}),
        ("tenantId", "tenant_01", {}),
        ("sessionId", "session_01", {}),
        ("turnId", "turn_01", {}),
        ("childActorId", "child_01", {}),
        ("taskContractId", "task_01", {}),
        ("completionEpochId", "epoch_01", {}),
        ("authorityPartitionId", "partition_01", {}),
        ("actionId", "action_01", {}),
        ("attemptId", "attempt_01", {}),
        (
            "decisionRequestId",
            "decision_01",
            {"resumeBindingDigest": _digest("0")},
        ),
    ),
)
def test_contract_identity_fields_require_exact_strings(
    field: str,
    valid: str,
    companions: dict[str, object],
    make_invalid: Callable[[str], object],
) -> None:
    with pytest.raises(ValidationError, match="exact string"):
        _contract(**companions, **{field: make_invalid(valid)})


@pytest.mark.parametrize("make_invalid", _NON_EXACT_STRING_FACTORIES)
@pytest.mark.parametrize(
    ("field", "companions"),
    (
        ("taskContractDigest", {}),
        ("policyDigest", {}),
        ("normalizedRequestDigest", {}),
        ("commandDigest", {}),
        ("argumentsDigest", {}),
        ("workingDirectoryDigest", {}),
        ("environmentDigest", {}),
        ("requestBodyDigest", {}),
        ("credentialScopeDigest", {}),
        ("networkDigest", {}),
        ("disclosureDigest", {}),
        ("sandboxProfileDigest", {}),
        ("guardianCeilingDigest", {}),
        (
            "revocationDigest",
            {"revokedAt": datetime(2024, 1, 1, tzinfo=UTC)},
        ),
        (
            "resumeBindingDigest",
            {"decisionRequestId": "decision_01"},
        ),
        (
            "parentAuthorityDigest",
            {"delegationChain": (_digest("0"),)},
        ),
    ),
)
def test_contract_digest_fields_require_exact_strings(
    field: str,
    companions: dict[str, object],
    make_invalid: Callable[[str], object],
) -> None:
    digest = _digest("0")
    with pytest.raises(ValidationError, match="exact string"):
        _contract(**companions, **{field: make_invalid(digest)})


@pytest.mark.parametrize("make_invalid", _NON_EXACT_STRING_FACTORIES)
def test_delegation_chain_elements_require_exact_strings(
    make_invalid: Callable[[str], object],
) -> None:
    digest = _digest("0")
    with pytest.raises(ValidationError, match="exact string"):
        _contract(
            parentAuthorityDigest=digest,
            delegationChain=(make_invalid(digest),),
        )


def test_input_requires_capabilities_and_non_deny_decision_cannot_be_empty() -> None:
    with pytest.raises(ValidationError):
        AuthorityInput(source="user", decision="allow", capabilities=())
    with pytest.raises(ValidationError, match="capabilities"):
        AuthorityDecision(status="allow", reasonCodes=(), capabilities=())

    denied = AuthorityDecision(status="deny", reasonCodes=("denied",))
    assert denied.capabilities == ()


def test_mutable_capability_subclass_is_normalized_to_the_exact_base_type() -> None:
    class MutableCapability(AuthorityCapability):
        model_config = ConfigDict(frozen=False)

    external = MutableCapability(
        effectClass="workspace.write",
        resourceRef="workspace:project",
        networkRefs=(),
        credentialRefs=(),
        workspaceViewBindingDigest=_digest("e"),
    )

    authority_input = AuthorityInput(
        source="user",
        decision="allow",
        capabilities=(external,),
    )

    assert type(authority_input.capabilities[0]) is AuthorityCapability
    assert authority_input.capabilities[0] is not external
    external.effect_class = "workspace.delete"
    assert authority_input.capabilities[0].effect_class == "workspace.write"


def test_hidden_capability_subclass_semantics_are_rejected() -> None:
    class SemanticCapability(AuthorityCapability):
        semantic_variant: str = Field(alias="semanticVariant")

    semantic = SemanticCapability(
        effectClass="workspace.write",
        resourceRef="workspace:project",
        networkRefs=(),
        credentialRefs=(),
        workspaceViewBindingDigest=_digest("e"),
        semanticVariant="hidden",
    )

    with pytest.raises(ValidationError, match="extra_forbidden"):
        AuthorityInput(
            source="user",
            decision="allow",
            capabilities=(semantic,),
        )


def test_resolver_reports_no_allowing_authority_for_empty_or_all_deny_inputs() -> None:
    action_digest = _digest("e")
    capability = _capability("workspace.write", "workspace:project")

    empty = resolve_authority(inputs=(), action_digest=action_digest)
    all_deny = resolve_authority(
        inputs=(
            AuthorityInput(
                source="platform",
                decision="deny",
                capabilities=(capability,),
            ),
        ),
        action_digest=action_digest,
    )

    assert empty.status == "deny"
    assert empty.reason_codes == ("no_allowing_authority",)
    assert empty.capabilities == ()
    assert all_deny == empty


@pytest.mark.parametrize("include_deny", (False, True))
def test_resolver_reports_deny_wins_for_an_empty_effective_set(
    include_deny: bool,
) -> None:
    workspace_write = _capability("workspace.write", "workspace:project")
    process_exec = _capability("process.exec", "binary:git")
    inputs = [
        AuthorityInput(
            source="user",
            decision="allow",
            capabilities=(workspace_write,),
        )
    ]
    if include_deny:
        inputs.append(
            AuthorityInput(
                source="platform",
                decision="deny",
                capabilities=(workspace_write,),
            )
        )
    else:
        inputs.append(
            AuthorityInput(
                source="session",
                decision="allow",
                capabilities=(process_exec,),
            )
        )

    decision = resolve_authority(inputs=tuple(inputs), action_digest=_digest("e"))

    assert decision.status == "deny"
    assert decision.reason_codes == ("deny_wins",)
    assert decision.capabilities == ()


def test_resolver_capability_order_is_deterministic() -> None:
    capability_z = _capability(
        "workspace.write",
        "workspace:project",
        credential_refs=("credential:a",),
    )
    capability_a = _capability(
        "network.connect",
        "network:provider",
        credential_refs=("credential:z",),
    )
    action_digest = _digest("e")

    first = resolve_authority(
        inputs=(
            AuthorityInput(
                source="user",
                decision="allow",
                capabilities=(capability_z, capability_a),
            ),
        ),
        action_digest=action_digest,
    )
    second = resolve_authority(
        inputs=(
            AuthorityInput(
                source="user",
                decision="allow",
                capabilities=(capability_a, capability_z),
            ),
        ),
        action_digest=action_digest,
    )

    assert first == second
    assert first.capabilities == (capability_a, capability_z)


@pytest.mark.parametrize(
    "raw_inputs",
    (
        pytest.param(lambda authority_input: [authority_input], id="list"),
        pytest.param(lambda authority_input: {authority_input}, id="set"),
    ),
)
def test_resolver_rejects_non_tuple_input_collections(
    raw_inputs: Callable[[AuthorityInput], object],
) -> None:
    authority_input = AuthorityInput(
        source="user",
        decision="allow",
        capabilities=(_capability("workspace.write", "workspace:project"),),
    )

    with pytest.raises(TypeError, match="exact tuple"):
        resolve_authority(
            inputs=raw_inputs(authority_input),  # type: ignore[arg-type]
            action_digest=_digest("e"),
        )


def test_resolver_rejects_tuple_and_input_subclasses() -> None:
    class InputsTuple(tuple[AuthorityInput, ...]):
        pass

    class SemanticAuthorityInput(AuthorityInput):
        semantic_variant: str = Field(alias="semanticVariant")

    capability = _capability("workspace.write", "workspace:project")
    authority_input = AuthorityInput(
        source="user",
        decision="allow",
        capabilities=(capability,),
    )
    semantic_input = SemanticAuthorityInput(
        source="user",
        decision="allow",
        capabilities=(capability,),
        semanticVariant="hidden",
    )

    with pytest.raises(TypeError, match="exact tuple"):
        resolve_authority(
            inputs=InputsTuple((authority_input,)),  # type: ignore[arg-type]
            action_digest=_digest("e"),
        )
    with pytest.raises(TypeError, match="exact AuthorityInput"):
        resolve_authority(
            inputs=(semantic_input,),
            action_digest=_digest("e"),
        )


def test_resolver_revalidates_tampered_exact_inputs_and_capabilities() -> None:
    capability = _capability("workspace.write", "workspace:project")
    authority_input = AuthorityInput(
        source="user",
        decision="allow",
        capabilities=(capability,),
    )
    authority_input.__dict__["decision"] = "not-a-decision"

    with pytest.raises(ValidationError):
        resolve_authority(inputs=(authority_input,), action_digest=_digest("e"))

    capability = _capability("workspace.write", "workspace:project")
    authority_input = AuthorityInput(
        source="user",
        decision="allow",
        capabilities=(capability,),
    )
    authority_input.capabilities[0].__dict__["effect_class"] = ""

    with pytest.raises(ValidationError):
        resolve_authority(inputs=(authority_input,), action_digest=_digest("e"))


def test_resolver_requires_a_canonical_action_digest() -> None:
    with pytest.raises(ValueError, match="sha256"):
        resolve_authority(inputs=(), action_digest="not-a-digest")


@pytest.mark.parametrize("make_invalid", _NON_EXACT_STRING_FACTORIES)
def test_resolver_requires_an_exact_string_action_digest(
    make_invalid: Callable[[str], object],
) -> None:
    with pytest.raises(TypeError, match="exact string"):
        resolve_authority(
            inputs=(),
            action_digest=make_invalid(_digest("e")),  # type: ignore[arg-type]
        )


def test_authority_contract_digest_uses_the_repo_kernel_and_complete_alias_json() -> None:
    contract = _contract(authorityContractId="권한_01", principalId="사용자_01")
    alias_json = AuthorityContract.model_dump(contract, by_alias=True, mode="json")

    assert canonical_authority_contract_digest(contract) == canonical_digest(alias_json)
    assert canonical_authority_contract_digest(
        _contract(authorityContractId="권한_02", principalId="사용자_01")
    ) != canonical_authority_contract_digest(contract)


def test_digest_helper_rejects_subclasses_ducks_and_tampered_exact_instances() -> None:
    class SemanticAuthorityContract(AuthorityContract):
        semantic_variant: str = Field(alias="semanticVariant")

    class OverriddenDumpContract(AuthorityContract):
        def model_dump(  # type: ignore[override]
            self,
            *args: object,
            **kwargs: object,
        ) -> dict[str, object]:
            _ = args, kwargs
            raise AssertionError("caller-controlled serializer was invoked")

    contract = _contract()
    payload = contract.model_dump(by_alias=True, mode="python")
    semantic = SemanticAuthorityContract.model_validate({**payload, "semanticVariant": "hidden"})
    overridden = OverriddenDumpContract.model_validate(payload)
    duck = SimpleNamespace(model_dump=lambda **_kwargs: payload)

    for candidate in (semantic, overridden, duck):
        with pytest.raises(TypeError, match="exact AuthorityContract"):
            canonical_authority_contract_digest(candidate)  # type: ignore[arg-type]

    contract.__dict__["fencing_token"] = 7.0
    with pytest.raises(ValidationError):
        canonical_authority_contract_digest(contract)

    contract = _contract()
    contract.__dict__["model_dump"] = lambda **_kwargs: {"forged": True}
    with pytest.raises(ValidationError):
        canonical_authority_contract_digest(contract)


def test_delegation_helper_rejects_subclasses_ducks_and_tampered_instances() -> None:
    class SemanticAuthorityContract(AuthorityContract):
        semantic_variant: str = Field(alias="semanticVariant")

    parent = _contract()
    child = _delegated_child(parent)
    payload = child.model_dump(by_alias=True, mode="python")
    semantic_child = SemanticAuthorityContract.model_validate(
        {**payload, "semanticVariant": "hidden"}
    )
    duck = SimpleNamespace(**payload)

    with pytest.raises(TypeError, match="exact AuthorityContract"):
        validate_delegated_authority(parent, semantic_child)
    with pytest.raises(TypeError, match="exact AuthorityContract"):
        validate_delegated_authority(parent, duck)  # type: ignore[arg-type]

    parent.__dict__["fencing_token"] = 7.0
    with pytest.raises(ValidationError):
        validate_delegated_authority(parent, child)

    parent = _contract()
    child = _delegated_child(parent)
    child.__dict__["expires_at"] = parent.expires_at + timedelta(seconds=1)
    with pytest.raises(ValueError, match="expiresAt"):
        validate_delegated_authority(parent, child)
