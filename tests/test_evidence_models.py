from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.evidence.types import (
    EvidenceContract,
    EvidenceContractFailure,
    EvidenceContractScopeMetadata,
    EvidenceContractVerdict,
    EvidenceFieldMatcher,
    EvidenceRecord,
    EvidenceRequirement,
    EvidenceSource,
)


def _basic_contract_payload() -> dict[str, object]:
    return {
        "id": "coding-basic",
        "description": "Require local code evidence before final answers.",
        "triggers": ["beforeCommit"],
        "requirements": [{"type": "GitDiff", "after": "last_code_mutation"}],
        "onMissing": "audit",
    }


def test_evidence_record_and_contract_accept_aliases_and_dump_camel_case() -> None:
    record = EvidenceRecord.model_validate(
        {
            "type": "TestRun",
            "status": "ok",
            "observedAt": 1_779_999_999.5,
            "source": {
                "kind": "tool_trace",
                "toolName": "Bash",
                "toolCallId": "call_123",
                "metadata": {"producerSurface": "tool_host"},
            },
            "fields": {"command": "pytest", "exitCode": 0},
            "preview": "pytest passed",
        }
    )
    snake_contract = EvidenceContract(
        id="snake-contract",
        description="Snake input remains accepted for OpenMagi YAML.",
        triggers=("beforeCommit",),
        when={"request_modes": ["coding"], "touched_paths": ["src/**"]},
        requirements=(
            EvidenceRequirement(
                type="TestRun",
                after="last_code_mutation",
                command_pattern="pytest|vitest",
                exit_code=0,
                fields={
                    "status": EvidenceFieldMatcher(equals="passed"),
                    "framework": EvidenceFieldMatcher(one_of=("pytest", "vitest")),
                    "durationMs": EvidenceFieldMatcher(exists=True),
                    "command": EvidenceFieldMatcher(matches="^pytest"),
                },
            ),
        ),
        on_missing="block_final_answer",
        retry_message="Run relevant verification before finalizing.",
    )
    camel_contract = EvidenceContract.model_validate(
        {
            "id": "camel-contract",
            "description": "Camel aliases remain accepted for JSON/API callers.",
            "triggers": ["afterToolUse", "beforeCommit"],
            "when": {"requestModes": ["coding"]},
            "requirements": [
                {
                    "type": "TestRun",
                    "after": "last_code_mutation",
                    "commandPattern": "pytest|vitest",
                    "exitCode": 0,
                    "fields": {
                        "status": {"equals": "passed"},
                        "framework": {"oneOf": ["pytest", "vitest"]},
                    },
                }
            ],
            "onMissing": "block_final_answer",
            "retryMessage": "Run relevant verification before finalizing.",
        }
    )

    assert record.observed_at == 1_779_999_999.5
    assert record.source.tool_name == "Bash"
    assert snake_contract.requirements[0].command_pattern == "pytest|vitest"
    assert camel_contract.requirements[0].exit_code == 0

    dumped_record = record.model_dump(by_alias=True)
    dumped_contract = snake_contract.model_dump(by_alias=True)
    assert dumped_record["observedAt"] == 1_779_999_999.5
    assert dumped_record["source"]["toolName"] == "Bash"
    assert dumped_contract["requirements"][0]["commandPattern"] == "pytest|vitest"
    assert dumped_contract["requirements"][0]["exitCode"] == 0
    assert dumped_contract["requirements"][0]["fields"]["framework"]["oneOf"] == (
        "pytest",
        "vitest",
    )
    assert dumped_contract["onMissing"] == "block_final_answer"
    assert dumped_contract["retryMessage"] == "Run relevant verification before finalizing."
    assert dumped_contract["trafficAttached"] is False
    assert dumped_contract["executionAttached"] is False


@pytest.mark.parametrize(
    "payload",
    (
        {**_basic_contract_payload(), "id": " "},
        {**_basic_contract_payload(), "requirements": []},
        {**_basic_contract_payload(), "trafficAttached": True},
        {**_basic_contract_payload(), "executionAttached": True},
        {**_basic_contract_payload(), "routeAttached": False},
        {
            **_basic_contract_payload(),
            "requirements": [{"type": " ", "after": "last_code_mutation"}],
        },
        {
            **_basic_contract_payload(),
            "requirements": [{"type": "custom:", "after": "contract_start"}],
        },
        {
            **_basic_contract_payload(),
            "requirements": [{"type": "custom:bad name", "after": "contract_start"}],
        },
    ),
)
def test_invalid_contracts_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EvidenceContract.model_validate(payload)


def test_non_catalog_pascal_case_evidence_types_require_custom_prefix() -> None:
    with pytest.raises(ValidationError):
        EvidenceRequirement(type="StripeWebhookAck")

    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(
            {
                "type": "StripeWebhookAck",
                "status": "ok",
                "observedAt": 1,
                "source": {"kind": "external_ack"},
            }
        )

    with pytest.raises(ValidationError):
        EvidenceContractFailure(
            code="EVIDENCE_CONTRACT_MISSING",
            contract_id="billing-safe",
            requirement_type="StripeWebhookAck",
        )

    custom_requirement = EvidenceRequirement(type="custom:StripeWebhookAck")
    custom_record = EvidenceRecord.model_validate(
        {
            "type": "custom:StripeWebhookAck",
            "status": "ok",
            "observedAt": 1,
            "source": {"kind": "external_ack"},
        }
    )
    custom_failure = EvidenceContractFailure(
        code="EVIDENCE_CONTRACT_MISSING",
        contract_id="billing-safe",
        requirement_type="custom:StripeWebhookAck",
    )

    assert custom_requirement.type == "custom:StripeWebhookAck"
    assert custom_record.type == "custom:StripeWebhookAck"
    assert custom_failure.requirement_type == "custom:StripeWebhookAck"


@pytest.mark.parametrize(
    "payload",
    (
        {"matches": None},
        {"oneOf": None},
        {"exists": None},
        {"equals": None},
    ),
)
def test_field_matcher_rejects_explicit_null_matcher_values(
    payload: dict[str, object | None],
) -> None:
    with pytest.raises(ValidationError):
        EvidenceFieldMatcher.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    (
        {"matches": "["},
        {"matches": "(unterminated"},
    ),
)
def test_field_matcher_rejects_malformed_regex_patterns(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        EvidenceFieldMatcher.model_validate(payload)


def test_requirement_rejects_malformed_command_pattern_regex() -> None:
    with pytest.raises(ValidationError):
        EvidenceRequirement(type="TestRun", command_pattern="[")


@pytest.mark.parametrize(
    "pattern",
    (
        r"[\s\S]*",
        r"[\s\S]+",
        r"[\d\D]*",
        r"[\d\D]+",
        r"[\w\W]*",
        r"[\w\W]+",
        r"(.)*",
        r"(.)+",
        r"(?:.)*",
        r"(?:.)+",
        r"([\s\S])*",
        r"([\s\S])+",
        r"(?:[\s\S])*",
        r"(?:[\s\S])+",
        r"([\d\D])*",
        r"([\d\D])+",
        r"(?:[\d\D])*",
        r"(?:[\d\D])+",
        r"([\w\W])*",
        r"([\w\W])+",
        r"(?:[\w\W])*",
        r"(?:[\w\W])+",
        r"(?s:.)*",
        r"(?s:.)+",
    ),
)
def test_field_matcher_rejects_all_input_regex_equivalents(pattern: str) -> None:
    with pytest.raises(ValidationError):
        EvidenceFieldMatcher(matches=pattern)


@pytest.mark.parametrize(
    "pattern",
    (
        r"[\s\S]*",
        r"[\s\S]+",
        r"[\d\D]*",
        r"[\d\D]+",
        r"[\w\W]*",
        r"[\w\W]+",
        r"(.)*",
        r"(.)+",
        r"(?:.)*",
        r"(?:.)+",
        r"([\s\S])*",
        r"([\s\S])+",
        r"(?:[\s\S])*",
        r"(?:[\s\S])+",
        r"([\d\D])*",
        r"([\d\D])+",
        r"(?:[\d\D])*",
        r"(?:[\d\D])+",
        r"([\w\W])*",
        r"([\w\W])+",
        r"(?:[\w\W])*",
        r"(?:[\w\W])+",
        r"(?s:.)*",
        r"(?s:.)+",
    ),
)
def test_requirement_rejects_all_input_command_pattern_equivalents(pattern: str) -> None:
    with pytest.raises(ValidationError):
        EvidenceRequirement(type="TestRun", command_pattern=pattern)


@pytest.mark.parametrize("pattern", ("pytest|vitest", "^pytest"))
def test_evidence_regex_validation_keeps_safe_command_patterns(pattern: str) -> None:
    EvidenceFieldMatcher(matches=pattern)
    EvidenceRequirement(type="TestRun", command_pattern=pattern)


def test_field_matcher_rejects_repeated_quantified_literal_atoms() -> None:
    with pytest.raises(ValidationError):
        EvidenceFieldMatcher(matches="^" + "a*" * 8 + "b$")


def test_requirement_rejects_repeated_quantified_command_literal_atoms() -> None:
    with pytest.raises(ValidationError):
        EvidenceRequirement(type="TestRun", command_pattern="^" + "a*" * 8 + "b$")


@pytest.mark.parametrize(
    "pattern",
    (
        "^(a+)+$",
        "(?=secret)",
        "(?!secret)",
        "(?<=secret)",
        "(?<!secret)",
        r"^(foo)\1$",
        r"(?P<word>foo)(?P=word)",
        r"(a)?(?(1)b|c)",
        "(a|aa)+$",
        ".*",
        ".+",
        "^prefix.*",
        ".*suffix$",
    ),
)
def test_field_matcher_rejects_unsafe_but_valid_regex_patterns(pattern: str) -> None:
    with pytest.raises(ValidationError):
        EvidenceFieldMatcher(matches=pattern)


@pytest.mark.parametrize(
    "pattern",
    (
        "^(a+)+$",
        "(?=secret)",
        "(?!secret)",
        "(?<=secret)",
        "(?<!secret)",
        r"^(foo)\1$",
        r"(?P<word>foo)(?P=word)",
        r"(a)?(?(1)b|c)",
        "(a|aa)+$",
        ".*",
        ".+",
        "^prefix.*",
        ".*suffix$",
    ),
)
def test_requirement_rejects_unsafe_but_valid_command_patterns(pattern: str) -> None:
    with pytest.raises(ValidationError):
        EvidenceRequirement(type="TestRun", command_pattern=pattern)


@pytest.mark.parametrize(
    "pattern",
    (
        r".{0,}",
        r".{1,}",
        r"(.){0,}",
        r"(?:.){1,}",
        r"[\s\S]{0,}",
        r"(?:[\s\S]){1,}",
        r"(?s:.){0,}",
        r"(?s:[\s\S])*",
        r"(.)*",
        r"(?:.)+",
        r"([\s\S])*",
        r"(?:[\s\S])+",
    ),
)
def test_field_matcher_rejects_wildcard_grouping_and_brace_regex_bypasses(
    pattern: str,
) -> None:
    with pytest.raises(ValidationError):
        EvidenceFieldMatcher(matches=pattern)


@pytest.mark.parametrize(
    "pattern",
    (
        r".{0,}",
        r".{1,}",
        r"(.){0,}",
        r"(?:.){1,}",
        r"[\s\S]{0,}",
        r"(?:[\s\S]){1,}",
        r"(?s:.){0,}",
        r"(?s:[\s\S])*",
        r"(.)*",
        r"(?:.)+",
        r"([\s\S])*",
        r"(?:[\s\S])+",
    ),
)
def test_requirement_rejects_wildcard_grouping_and_brace_command_bypasses(
    pattern: str,
) -> None:
    with pytest.raises(ValidationError):
        EvidenceRequirement(type="TestRun", command_pattern=pattern)


@pytest.mark.parametrize("exists", ("false", 1))
def test_field_matcher_exists_rejects_coerced_bool_values(exists: object) -> None:
    with pytest.raises(ValidationError):
        EvidenceFieldMatcher(exists=exists)


@pytest.mark.parametrize("exit_code", (True, "0"))
def test_requirement_exit_code_rejects_coerced_int_values(exit_code: object) -> None:
    with pytest.raises(ValidationError):
        EvidenceRequirement(type="TestRun", exit_code=exit_code)


def test_exists_only_matcher_and_requirement_model_copy_round_trip() -> None:
    matcher = EvidenceFieldMatcher(exists=True)
    requirement = EvidenceRequirement(type="TestRun", fields={"x": matcher})

    matcher_copy = matcher.model_copy()
    requirement_copy = requirement.model_copy()

    assert matcher_copy.exists is True
    assert requirement_copy.fields["x"].exists is True


def test_exists_only_matcher_model_dump_round_trips_without_exclude_none() -> None:
    matcher = EvidenceFieldMatcher(exists=True)
    dumped = matcher.model_dump(by_alias=True)

    assert dumped == {"exists": True}
    assert EvidenceFieldMatcher.model_validate(dumped).exists is True


def test_exists_only_contract_model_dump_round_trips_without_exclude_none() -> None:
    contract = EvidenceContract(
        id="exists-only",
        triggers=("beforeCommit",),
        requirements=(
            EvidenceRequirement(
                type="TestRun",
                fields={"x": EvidenceFieldMatcher(exists=True)},
            ),
        ),
        on_missing="audit",
    )
    dumped = contract.model_dump(by_alias=True)

    assert dumped["requirements"][0]["fields"]["x"] == {"exists": True}
    round_tripped = EvidenceContract.model_validate(dumped)
    assert round_tripped.requirements[0].fields["x"].exists is True


def test_source_and_matcher_models_reject_unknown_extra_fields() -> None:
    with pytest.raises(ValidationError):
        EvidenceSource.model_validate({"kind": "tool_trace", "toolName": "Bash", "route": "no"})

    with pytest.raises(ValidationError):
        EvidenceFieldMatcher.model_validate({"equals": "ok", "pythonCallable": "no"})

    with pytest.raises(ValidationError):
        EvidenceFieldMatcher.model_validate({})


class _CustomObject:
    pass


def _callable_metadata() -> str:
    return "runtime object"


@pytest.mark.parametrize(
    "payload",
    (
        {"equals": _callable_metadata},
        {"equals": _CustomObject()},
        {"equals": b"opaque"},
        {"oneOf": ["ok", _callable_metadata]},
        {"oneOf": ["ok", _CustomObject()]},
        {"oneOf": ["ok", b"opaque"]},
    ),
)
def test_field_matcher_rejects_non_declarative_equals_and_one_of_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        EvidenceFieldMatcher.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "payload"),
    (
        (
            EvidenceSource,
            {"kind": "tool_trace", "metadata": {"handler": _callable_metadata}},
        ),
        (
            EvidenceSource,
            {"kind": "tool_trace", "metadata": {"opaque": _CustomObject()}},
        ),
        (
            EvidenceSource,
            {"kind": "tool_trace", "metadata": {1: "numeric key"}},
        ),
        (
            EvidenceRecord,
            {
                "type": "TestRun",
                "status": "ok",
                "observedAt": 1,
                "source": {"kind": "tool_trace"},
                "fields": {"raw": b"opaque"},
            },
        ),
        (
            EvidenceRecord,
            {
                "type": "TestRun",
                "status": "ok",
                "observedAt": 1,
                "source": {"kind": "tool_trace"},
                "metadata": {"opaque": _CustomObject()},
            },
        ),
        (
            EvidenceContract,
            {
                **_basic_contract_payload(),
                "when": {"predicate": _callable_metadata},
            },
        ),
        (
            EvidenceContract,
            {
                **_basic_contract_payload(),
                "when": {("tuple", "key"): "not declarative"},
            },
        ),
        (
            EvidenceContractFailure,
            {
                "code": "EVIDENCE_CONTRACT_MISSING",
                "contractId": "coding-basic",
                "metadata": {"opaque": _CustomObject()},
            },
        ),
        (
            EvidenceContractFailure,
            {
                "code": "EVIDENCE_CONTRACT_MISSING",
                "contractId": "coding-basic",
                "metadata": {"raw": b"opaque"},
            },
        ),
    ),
)
def test_metadata_fields_reject_non_declarative_values(
    model: type[EvidenceSource | EvidenceRecord | EvidenceContract | EvidenceContractFailure],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


class _StrSubclass(str):
    pass


class _IntSubclass(int):
    pass


@pytest.mark.parametrize(
    ("model", "payload"),
    (
        (
            EvidenceRecord,
            {
                "type": "TestRun",
                "status": "ok",
                "observedAt": 1,
                "source": {"kind": "tool_trace"},
                "fields": {"value": _StrSubclass("ok")},
            },
        ),
        (
            EvidenceRecord,
            {
                "type": "TestRun",
                "status": "ok",
                "observedAt": 1,
                "source": {"kind": "tool_trace"},
                "fields": {"value": _IntSubclass(1)},
            },
        ),
        (
            EvidenceSource,
            {"kind": "tool_trace", "metadata": {"value": _StrSubclass("ok")}},
        ),
        (
            EvidenceSource,
            {"kind": "tool_trace", "metadata": {"value": _IntSubclass(1)}},
        ),
    ),
)
def test_metadata_fields_reject_primitive_subclasses(
    model: type[EvidenceRecord | EvidenceSource],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    (
        {"equals": _StrSubclass("ok")},
        {"equals": _IntSubclass(1)},
        {"oneOf": [_StrSubclass("ok")]},
        {"oneOf": [_IntSubclass(1)]},
    ),
)
def test_field_matcher_rejects_primitive_subclasses(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EvidenceFieldMatcher.model_validate(payload)


@pytest.mark.parametrize(
    "observed_at",
    (
        float("nan"),
        float("inf"),
        float("-inf"),
        True,
        "123",
        "1779999999.5",
    ),
)
def test_evidence_record_rejects_non_finite_or_coerced_observed_at(
    observed_at: object,
) -> None:
    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(
            {
                "type": "TestRun",
                "status": "ok",
                "observedAt": observed_at,
                "source": {"kind": "tool_trace"},
            }
        )


def test_nested_evidence_mapping_fields_are_immutable_and_dump_as_dicts() -> None:
    record = EvidenceRecord.model_validate(
        {
            "type": "TestRun",
            "status": "ok",
            "observedAt": 1,
            "source": {
                "kind": "tool_trace",
                "metadata": {"surface": "tool_host", "nested": {"stable": True}},
            },
            "fields": {"command": "pytest", "details": {"exitCode": 0}},
            "metadata": {"labels": ["verification"]},
        }
    )
    requirement = EvidenceRequirement(
        type="TestRun",
        fields={"status": EvidenceFieldMatcher(equals="passed")},
    )
    contract = EvidenceContract.model_validate(
        {
            **_basic_contract_payload(),
            "when": {"requestModes": ["coding"], "nested": {"safe": True}},
        }
    )
    failure = EvidenceContractFailure(
        code="EVIDENCE_CONTRACT_MISSING",
        contract_id="coding-basic",
        requirement_type="TestRun",
        metadata={"details": {"missing": "TestRun"}},
    )

    with pytest.raises(TypeError):
        record.source.metadata["extra"] = "no"
    with pytest.raises(TypeError):
        record.source.metadata["nested"]["stable"] = False
    with pytest.raises(TypeError):
        record.fields["extra"] = "no"
    with pytest.raises(TypeError):
        record.fields["details"]["exitCode"] = 1
    with pytest.raises(TypeError):
        record.metadata["extra"] = "no"
    with pytest.raises(AttributeError):
        record.metadata["labels"].append("mutable")
    with pytest.raises(TypeError):
        requirement.fields["extra"] = EvidenceFieldMatcher(exists=True)
    with pytest.raises(TypeError):
        contract.when["extra"] = "no"
    with pytest.raises(TypeError):
        contract.when["nested"]["safe"] = False
    with pytest.raises(TypeError):
        failure.metadata["details"]["missing"] = "other"

    dumped = record.model_dump(by_alias=True)
    assert dumped["source"]["metadata"] == {
        "surface": "tool_host",
        "nested": {"stable": True},
    }
    assert type(dumped["source"]["metadata"]) is dict
    assert dumped["fields"] == {"command": "pytest", "details": {"exitCode": 0}}
    assert type(dumped["fields"]) is dict
    assert dumped["metadata"] == {"labels": ["verification"]}
    assert type(dumped["metadata"]) is dict
    assert contract.model_dump(by_alias=True)["when"] == {
        "requestModes": ["coding"],
        "nested": {"safe": True},
    }
    assert failure.model_dump(by_alias=True)["metadata"] == {
        "details": {"missing": "TestRun"}
    }


def test_model_copy_revalidates_protected_evidence_contract_invariants() -> None:
    contract = EvidenceContract.model_validate(_basic_contract_payload())
    scope = contract.model_copy(
        update={
            "scope": {
                "agentRoles": ["coding"],
                "runOn": ["main"],
                "trafficAttached": False,
                "executionAttached": False,
            }
        }
    ).scope
    verdict = EvidenceContractVerdict(
        contract_id="coding-basic",
        ok=True,
        state="pass",
        enforcement="audit",
        missing_requirements=(),
        matched_evidence=(),
        failures=(),
    )

    with pytest.raises(ValidationError):
        contract.model_copy(update={"traffic_attached": True})
    with pytest.raises(ValidationError):
        contract.model_copy(update={"executionAttached": True})
    with pytest.raises(ValidationError):
        contract.model_copy(update={"requirements": ()})
    assert scope is not None
    with pytest.raises(ValidationError):
        scope.model_copy(update={"trafficAttached": True})
    with pytest.raises(ValidationError):
        scope.model_copy(update={"execution_attached": True})
    with pytest.raises(ValidationError):
        verdict.model_copy(update={"trafficAttached": True})
    with pytest.raises(ValidationError):
        verdict.model_copy(update={"execution_attached": True})


@pytest.mark.parametrize(
    "payload",
    (
        {**_basic_contract_payload(), "trafficAttached": 0},
        {**_basic_contract_payload(), "executionAttached": 0},
    ),
)
def test_contract_attachment_flags_reject_coerced_bool_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        EvidenceContract.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    (
        {
            "agentRoles": ["coding"],
            "runOn": ["main"],
            "auditBeforeBlock": "false",
        },
        {
            "agentRoles": ["coding"],
            "runOn": ["main"],
            "optOutAllowed": 0,
        },
        {
            "agentRoles": ["coding"],
            "runOn": ["main"],
            "optOutAllowed": False,
            "hardSafety": "true",
        },
        {
            "agentRoles": ["coding"],
            "runOn": ["main"],
            "trafficAttached": 0,
        },
        {
            "agentRoles": ["coding"],
            "runOn": ["main"],
            "executionAttached": 0,
        },
    ),
)
def test_contract_scope_booleans_reject_coerced_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        EvidenceContractScopeMetadata.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    (
        {
            "contractId": "coding-basic",
            "ok": "false",
            "state": "missing",
            "enforcement": "audit",
            "missingRequirements": (),
            "matchedEvidence": (),
            "failures": (),
        },
        {
            "contractId": "coding-basic",
            "ok": False,
            "state": "missing",
            "enforcement": "audit",
            "missingRequirements": (),
            "matchedEvidence": (),
            "failures": (),
            "trafficAttached": 0,
        },
        {
            "contractId": "coding-basic",
            "ok": False,
            "state": "missing",
            "enforcement": "audit",
            "missingRequirements": (),
            "matchedEvidence": (),
            "failures": (),
            "executionAttached": 0,
        },
    ),
)
def test_verdict_booleans_reject_coerced_values(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EvidenceContractVerdict.model_validate(payload)


def test_contract_revalidates_constructed_invalid_nested_requirement() -> None:
    invalid_requirement = EvidenceRequirement.model_construct(
        type="StripeWebhookAck",
        fields={},
    )

    with pytest.raises(ValidationError):
        EvidenceContract.model_validate(
            {
                **_basic_contract_payload(),
                "requirements": (invalid_requirement,),
            }
        )


def test_contract_model_copy_revalidates_constructed_invalid_nested_requirement() -> None:
    contract = EvidenceContract.model_validate(_basic_contract_payload())
    invalid_requirement = EvidenceRequirement.model_construct(
        type="StripeWebhookAck",
        fields={},
    )

    with pytest.raises(ValidationError):
        contract.model_copy(update={"requirements": (invalid_requirement,)})


def test_requirement_revalidates_constructed_invalid_nested_field_matcher() -> None:
    invalid_matcher = EvidenceFieldMatcher.model_construct(exists="false")

    with pytest.raises(ValidationError):
        EvidenceRequirement(type="TestRun", fields={"status": invalid_matcher})


def test_model_copy_rejects_constructed_source_invalid_default_fields() -> None:
    invalid_source = EvidenceSource.model_construct(
        kind="tool_trace",
        metadata={"bad": object()},
        _fields_set={"kind"},
    )

    with pytest.raises(ValidationError):
        invalid_source.model_copy()


def test_model_copy_rejects_constructed_requirement_invalid_default_fields() -> None:
    invalid_requirement = EvidenceRequirement.model_construct(
        type="TestRun",
        fields={"x": EvidenceFieldMatcher.model_construct(exists="false")},
        _fields_set={"type"},
    )

    with pytest.raises(ValidationError):
        invalid_requirement.model_copy()


def test_record_revalidates_constructed_nested_source_default_fields() -> None:
    invalid_source = EvidenceSource.model_construct(
        kind="tool_trace",
        metadata={"bad": object()},
        _fields_set={"kind"},
    )

    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(
            {
                "type": "TestRun",
                "status": "ok",
                "observedAt": 1,
                "source": invalid_source,
            }
        )


def test_contract_scope_metadata_preserves_roles_run_scope_and_spawn_depth() -> None:
    contract = EvidenceContract.model_validate(
        {
            **_basic_contract_payload(),
            "scope": {
                "agentRoles": ["coding", "research"],
                "runOn": ["main", "child"],
                "spawnDepth": {"minDepth": 0, "maxDepth": 2},
                "enforcement": "audit",
                "optOutAllowed": True,
                "hardSafety": False,
            },
        }
    )

    assert contract.scope is not None
    assert contract.scope.agent_roles == ("coding", "research")
    assert contract.scope.run_on == ("main", "child")
    assert contract.scope.spawn_depth.min_depth == 0
    assert contract.scope.spawn_depth.max_depth == 2

    dumped_scope = contract.model_dump(by_alias=True)["scope"]
    assert dumped_scope["agentRoles"] == ("coding", "research")
    assert dumped_scope["runOn"] == ("main", "child")
    assert dumped_scope["spawnDepth"] == {"minDepth": 0, "maxDepth": 2}
    assert dumped_scope["optOutAllowed"] is True
    assert dumped_scope["hardSafety"] is False


def test_hard_safety_contract_scope_cannot_be_opted_out_when_non_optional() -> None:
    with pytest.raises(ValidationError):
        EvidenceContract.model_validate(
            {
                **_basic_contract_payload(),
                "scope": {
                    "agentRoles": ["coding", "research", "general"],
                    "runOn": ["main", "child"],
                    "spawnDepth": {"minDepth": 0, "maxDepth": 3},
                    "enforcement": "block_final_answer",
                    "optOutAllowed": True,
                    "hardSafety": True,
                },
            }
        )

    hard_safety = EvidenceContract.model_validate(
        {
            **_basic_contract_payload(),
            "scope": {
                "agentRoles": ["coding", "research", "general"],
                "runOn": ["main", "child"],
                "spawnDepth": {"minDepth": 0, "maxDepth": 3},
                "enforcement": "block_final_answer",
                "optOutAllowed": False,
                "hardSafety": True,
            },
        }
    )

    assert hard_safety.scope is not None
    assert hard_safety.scope.hard_safety is True
    assert hard_safety.scope.opt_out_allowed is False


def test_verdict_skeleton_uses_stable_failure_codes_and_aliases() -> None:
    requirement = EvidenceRequirement(type="TestRun", command_pattern="pytest", exit_code=0)
    failure = EvidenceContractFailure(
        code="EVIDENCE_CONTRACT_MISSING",
        contract_id="coding-basic",
        requirement_type="TestRun",
        message="TestRun evidence was not observed.",
    )
    verdict = EvidenceContractVerdict(
        contract_id="coding-basic",
        ok=False,
        state="missing",
        enforcement="audit",
        missing_requirements=(requirement,),
        matched_evidence=(),
        failures=(failure,),
        retry_message="Run tests before finalizing.",
    )

    dumped = verdict.model_dump(by_alias=True)
    assert dumped["contractId"] == "coding-basic"
    assert dumped["missingRequirements"][0]["commandPattern"] == "pytest"
    assert dumped["failures"][0]["code"] == "EVIDENCE_CONTRACT_MISSING"
    assert dumped["failures"][0]["requirementType"] == "TestRun"
    assert dumped["retryMessage"] == "Run tests before finalizing."
    assert "trafficAttached" not in dumped
    assert "executionAttached" not in dumped

    with pytest.raises(ValidationError):
        EvidenceContractFailure(
            code="CLAIM_CITATION_REQUIRED",
            contract_id="coding-basic",
            requirement_type="TestRun",
        )


def test_evidence_package_import_boundary_stays_adk_and_runtime_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.evidence")
importlib.import_module("magi_agent.evidence.types")
importlib.import_module("magi_agent.evidence.builtin")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.runtime",
    "magi_agent.transport",
    "magi_agent.tools.dispatcher",
    "magi_agent.hooks.bus",
)
loaded = [
    name
    for name in sys.modules
    if name == "google.adk" or name.startswith(forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"evidence import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
