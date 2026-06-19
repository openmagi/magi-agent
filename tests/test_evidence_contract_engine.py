from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator, Mapping

import pytest
from pydantic import ValidationError

from magi_agent.evidence import (
    EVIDENCE_REGEX_CANDIDATE_LIMIT,
    EvidenceContract,
    EvidenceContractEngine,
    EvidenceRecord,
    EvidenceRequirement,
    evaluate_evidence_contract,
)


def _test_record(
    *,
    status: str = "ok",
    observed_at: int | float = 200,
    command: str = "python -m pytest tests/test_unit.py",
    exit_code: int = 0,
    fields: dict[str, object] | None = None,
) -> EvidenceRecord:
    merged_fields: dict[str, object] = {
        "command": command,
        "exitCode": exit_code,
        "suite": "unit",
        "result": "passed",
        "durationMs": 1234,
    }
    if fields:
        merged_fields.update(fields)
    return EvidenceRecord.model_validate(
        {
            "type": "TestRun",
            "status": status,
            "observedAt": observed_at,
            "source": {"kind": "tool_trace", "toolName": "Bash", "toolCallId": "call_tests"},
            "fields": merged_fields,
            "preview": "pytest passed",
            "metadata": {"contractStart": 100, "lastCodeMutation": 150},
        }
    )


def _contract(
    *,
    on_missing: str = "audit",
    requirements: list[dict[str, object]] | None = None,
    when: dict[str, object] | None = None,
) -> EvidenceContract:
    return EvidenceContract.model_validate(
        {
            "id": "coding-evidence",
            "triggers": ["beforeCommit"],
            "when": when or {"contractStart": 100, "lastCodeMutation": 150},
            "requirements": requirements
            or [
                {
                    "type": "TestRun",
                    "after": "last_code_mutation",
                    "commandPattern": "^python -m pytest",
                    "exitCode": 0,
                    "fields": {"result": {"equals": "passed"}},
                }
            ],
            "onMissing": on_missing,
            "retryMessage": "Run verification before finalizing.",
        }
    )


class _ComparisonTrapInt(int):
    def __ge__(self, other: object) -> bool:
        raise AssertionError("constructed observedAt comparison was invoked")

    def __lt__(self, other: object) -> bool:
        raise AssertionError("constructed observedAt comparison was invoked")


class _IntSubclass(int):
    pass


class _EqualityTrap:
    def __eq__(self, other: object) -> bool:
        raise AssertionError("constructed object equality was invoked")


class _RuntimeErrorMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        raise RuntimeError(f"hostile mapping access for {key}")

    def __iter__(self) -> Iterator[str]:
        return iter(("id",))

    def __len__(self) -> int:
        return 1


class _EqualityRuntimeError:
    def __eq__(self, other: object) -> bool:
        raise RuntimeError(f"hostile equality against {other!r}")


class _IterRaises:
    def __iter__(self) -> Iterator[EvidenceRecord]:
        raise RuntimeError("hostile evidence_records __iter__")


class _NextRaisesAfterOne:
    def __init__(self) -> None:
        self._index = 0

    def __iter__(self) -> "_NextRaisesAfterOne":
        return self

    def __next__(self) -> EvidenceRecord:
        if self._index == 0:
            self._index += 1
            return _test_record()
        raise RuntimeError("hostile evidence_records __next__")


def _constructed_record(
    **overrides: object,
) -> EvidenceRecord:
    base_record = _test_record()
    values: dict[str, object] = {
        "type": base_record.type,
        "status": base_record.status,
        "observed_at": base_record.observed_at,
        "source": base_record.source,
        "fields": base_record.fields,
        "preview": base_record.preview,
        "metadata": base_record.metadata,
    }
    values.update(overrides)
    return EvidenceRecord.model_construct(**values)


def test_pass_verdict_for_matched_test_run_with_command_exit_field_and_boundary() -> None:
    verdict = evaluate_evidence_contract(_contract(), [_test_record()])

    assert verdict.ok is True
    assert verdict.state == "pass"
    assert verdict.enforcement == "audit"
    assert verdict.failures == ()
    assert verdict.missing_requirements == ()
    assert verdict.matched_evidence[0].fields["command"] == "python -m pytest tests/test_unit.py"
    assert verdict.traffic_attached is False
    assert verdict.execution_attached is False


def test_stale_evidence_after_last_code_mutation_and_contract_start() -> None:
    last_code_verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "after": "last_code_mutation",
                    "commandPattern": "^python -m pytest",
                    "exitCode": 0,
                }
            ]
        ),
        [_test_record(observed_at=149)],
    )
    contract_start_verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "after": "contract_start",
                    "commandPattern": "^python -m pytest",
                    "exitCode": 0,
                }
            ]
        ),
        [_test_record(observed_at=99)],
    )

    assert last_code_verdict.state == "failed"
    assert last_code_verdict.failures[0].code == "EVIDENCE_CONTRACT_STALE"
    assert last_code_verdict.failures[0].metadata["boundary"] == "last_code_mutation"
    assert contract_start_verdict.state == "failed"
    assert contract_start_verdict.failures[0].code == "EVIDENCE_CONTRACT_STALE"
    assert contract_start_verdict.failures[0].metadata["boundary"] == "contract_start"


def test_field_matchers_equals_one_of_matches_and_exists() -> None:
    verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "fields": {
                        "result": {"equals": "passed"},
                        "suite": {"oneOf": ["unit", "integration"]},
                        "command": {"matches": "^python -m pytest"},
                        "durationMs": {"exists": True},
                    },
                }
            ]
        ),
        [_test_record()],
    )
    missing_exists_verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "fields": {"missingField": {"exists": True}},
                }
            ]
        ),
        [_test_record()],
    )

    assert verdict.ok is True
    assert verdict.state == "pass"
    assert missing_exists_verdict.ok is False
    assert missing_exists_verdict.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"


def test_missing_requirement_in_audit_mode_records_failure_without_blocking() -> None:
    verdict = evaluate_evidence_contract(_contract(on_missing="audit"), [])

    assert verdict.ok is False
    assert verdict.state == "missing"
    assert verdict.enforcement == "audit"
    assert verdict.missing_requirements[0].type == "TestRun"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_MISSING"
    assert verdict.traffic_attached is False
    assert verdict.execution_attached is False


def test_failed_evidence_record_yields_failed_verdict_state() -> None:
    verdict = evaluate_evidence_contract(_contract(), [_test_record(status="failed")])

    assert verdict.ok is False
    assert verdict.state == "failed"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"
    assert verdict.failures[0].metadata["recordStatus"] == "failed"


def test_block_final_answer_missing_or_failing_evidence_is_block_ready_without_traffic() -> None:
    missing_verdict = evaluate_evidence_contract(
        _contract(on_missing="block_final_answer"),
        [],
    )
    failing_verdict = evaluate_evidence_contract(
        _contract(on_missing="block_final_answer"),
        [_test_record(exit_code=1)],
    )

    assert missing_verdict.ok is False
    assert missing_verdict.state == "block_ready"
    assert missing_verdict.enforcement == "block_final_answer"
    assert missing_verdict.traffic_attached is False
    assert missing_verdict.execution_attached is False
    assert failing_verdict.ok is False
    assert failing_verdict.state == "block_ready"
    assert failing_verdict.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"


def test_test_run_exit_code_uses_strict_equality_for_bool_values() -> None:
    verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "exitCode": 0,
                }
            ]
        ),
        [_test_record(exit_code=False)],
    )

    assert verdict.ok is False
    assert verdict.state == "failed"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"
    assert verdict.failures[0].metadata["field"] == "exitCode"
    assert verdict.failures[0].metadata["actual"] is False


def test_field_equals_and_one_of_use_strict_equality_for_bool_values() -> None:
    equals_verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "fields": {"numeric": {"equals": 1}},
                }
            ]
        ),
        [_test_record(fields={"numeric": True})],
    )
    one_of_verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "fields": {"numeric": {"oneOf": [1]}},
                }
            ]
        ),
        [_test_record(fields={"numeric": True})],
    )

    assert equals_verdict.ok is False
    assert equals_verdict.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"
    assert equals_verdict.failures[0].metadata["matcher"] == "equals"
    assert equals_verdict.failures[0].metadata["actual"] is True
    assert one_of_verdict.ok is False
    assert one_of_verdict.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"
    assert one_of_verdict.failures[0].metadata["matcher"] == "oneOf"
    assert one_of_verdict.failures[0].metadata["actual"] is True


def test_nested_field_equals_uses_strict_equality_for_bool_values() -> None:
    verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "fields": {"nested": {"equals": {"value": 1}}},
                }
            ]
        ),
        [_test_record(fields={"nested": {"value": True}})],
    )

    assert verdict.ok is False
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"
    assert verdict.failures[0].metadata["matcher"] == "equals"
    assert verdict.failures[0].metadata["actual"] == {"value": True}


def test_nested_field_one_of_uses_strict_equality_for_bool_values() -> None:
    verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "fields": {"nested": {"oneOf": [{"value": 1}]}},
                }
            ]
        ),
        [_test_record(fields={"nested": {"value": True}})],
    )

    assert verdict.ok is False
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"
    assert verdict.failures[0].metadata["matcher"] == "oneOf"
    assert verdict.failures[0].metadata["actual"] == {"value": True}


def test_one_of_null_requires_field_presence_while_explicit_null_matches() -> None:
    missing_verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "fields": {"missing": {"oneOf": [None]}},
                }
            ]
        ),
        [_test_record()],
    )
    explicit_null_verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "fields": {"nullable": {"oneOf": [None]}},
                }
            ]
        ),
        [_test_record(fields={"nullable": None})],
    )

    assert missing_verdict.ok is False
    assert missing_verdict.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"
    assert missing_verdict.failures[0].metadata["field"] == "missing"
    assert missing_verdict.failures[0].metadata["matcher"] == "oneOf"
    assert explicit_null_verdict.ok is True


def test_test_run_only_requirement_options_on_non_test_run_return_invalid_config() -> None:
    audit_verdict = evaluate_evidence_contract(
        _contract(
            on_missing="audit",
            requirements=[
                {
                    "type": "GitDiff",
                    "commandPattern": "^python -m pytest",
                }
            ],
        ),
        [
            EvidenceRecord.model_validate(
                {
                    "type": "GitDiff",
                    "status": "ok",
                    "observedAt": 200,
                    "source": {"kind": "tool_trace", "toolName": "Bash"},
                    "fields": {"changedFiles": ["src/app.py"]},
                }
            )
        ],
    )
    block_verdict = evaluate_evidence_contract(
        _contract(
            on_missing="block_final_answer",
            requirements=[
                {
                    "type": "GitDiff",
                    "exitCode": 0,
                }
            ],
        ),
        [
            EvidenceRecord.model_validate(
                {
                    "type": "GitDiff",
                    "status": "ok",
                    "observedAt": 200,
                    "source": {"kind": "tool_trace", "toolName": "Bash"},
                    "fields": {"changedFiles": ["src/app.py"]},
                }
            )
        ],
    )

    assert audit_verdict.ok is False
    assert audit_verdict.state == "audit"
    assert audit_verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert block_verdict.ok is False
    assert block_verdict.state == "block_ready"
    assert block_verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"


def test_constructed_invalid_record_returns_invalid_config_before_stale_comparison() -> None:
    verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "after": "last_code_mutation",
                    "exitCode": 0,
                }
            ]
        ),
        [
            _constructed_record(
                observed_at=_ComparisonTrapInt(149),
                metadata={"lastCodeMutation": _IntSubclass(150)},
            )
        ],
    )

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.enforcement == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert verdict.failures[0].metadata["recordIndex"] == 0
    assert "validationError" in verdict.failures[0].metadata


def test_constructed_invalid_record_blocks_in_block_final_answer_mode() -> None:
    verdict = evaluate_evidence_contract(
        _contract(on_missing="block_final_answer"),
        [
            _constructed_record(
                fields={"result": "passed", "opaque": _EqualityTrap()},
            )
        ],
    )

    assert verdict.ok is False
    assert verdict.state == "block_ready"
    assert verdict.enforcement == "block_final_answer"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert verdict.failures[0].metadata["recordIndex"] == 0


def test_constructed_record_nested_custom_object_returns_invalid_config_without_equality() -> None:
    verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "fields": {"nested": {"equals": {"value": "expected"}}},
                }
            ]
        ),
        [
            _constructed_record(
                fields={"nested": {"value": _EqualityTrap()}},
            )
        ],
    )

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert "actual" not in verdict.failures[0].metadata


def test_constructed_record_custom_object_actual_mismatch_is_rejected_before_equality() -> None:
    verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "fields": {"opaque": {"equals": "expected"}},
                }
            ]
        ),
        [
            _constructed_record(
                fields={"opaque": _EqualityTrap()},
            )
        ],
    )

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert "actual" not in verdict.failures[0].metadata


def test_constructed_record_with_non_mapping_fields_returns_invalid_config() -> None:
    verdict = evaluate_evidence_contract(
        _contract(),
        [_constructed_record(fields=object())],
    )

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert verdict.failures[0].metadata["validationErrorCount"] == 1


def test_constructed_record_with_none_fields_returns_invalid_config() -> None:
    verdict = evaluate_evidence_contract(
        _contract(),
        [_constructed_record(fields=None)],
    )

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"


def test_constructed_record_with_none_source_returns_invalid_config() -> None:
    verdict = evaluate_evidence_contract(
        _contract(),
        [_constructed_record(source=None)],
    )

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert verdict.failures[0].metadata["recordIndex"] == 0


def test_constructed_record_with_object_source_returns_invalid_config() -> None:
    verdict = evaluate_evidence_contract(
        _contract(),
        [_constructed_record(source=object())],
    )

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert verdict.failures[0].metadata["recordIndex"] == 0


def test_hostile_raw_evidence_record_mapping_runtime_error_returns_invalid_config() -> None:
    verdict = evaluate_evidence_contract(_contract(), [_RuntimeErrorMapping()])

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert verdict.failures[0].metadata["recordIndex"] == 0


def test_hostile_evidence_record_subclass_model_copy_is_not_used() -> None:
    class HostileRecord(EvidenceRecord):
        def model_copy(self, *args: object, **kwargs: object) -> EvidenceRecord:
            return _constructed_record(fields={"opaque": _EqualityTrap()})

    record = HostileRecord.model_validate(_test_record().model_dump(by_alias=True))
    verdict = evaluate_evidence_contract(_contract(), [record])

    assert verdict.ok is True
    assert verdict.state == "pass"


def test_failure_reporting_prefers_fresh_mismatch_over_stale_candidate_regardless_of_order() -> None:
    stale_match = _test_record(observed_at=149, exit_code=0)
    fresh_mismatch = _test_record(observed_at=200, exit_code=1)
    contract = _contract(
        requirements=[
            {
                "type": "TestRun",
                "after": "last_code_mutation",
                "exitCode": 0,
            }
        ]
    )

    stale_first = evaluate_evidence_contract(contract, [stale_match, fresh_mismatch])
    fresh_first = evaluate_evidence_contract(contract, [fresh_mismatch, stale_match])

    assert stale_first.ok is False
    assert stale_first.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"
    assert stale_first.failures[0].metadata["field"] == "exitCode"
    assert stale_first.failures[0].metadata["actual"] == 1
    assert fresh_first.ok is False
    assert fresh_first.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"
    assert fresh_first.failures[0].metadata["field"] == "exitCode"
    assert fresh_first.failures[0].metadata["actual"] == 1


def test_fresh_mismatch_reporting_prefers_command_failure_regardless_of_order() -> None:
    bad_command = _test_record(command="npm test", exit_code=0)
    bad_exit_code = _test_record(command="python -m pytest tests/test_unit.py", exit_code=1)
    contract = _contract(
        requirements=[
            {
                "type": "TestRun",
                "commandPattern": "^python -m pytest",
                "exitCode": 0,
            }
        ]
    )

    command_first = evaluate_evidence_contract(contract, [bad_command, bad_exit_code])
    exit_code_first = evaluate_evidence_contract(contract, [bad_exit_code, bad_command])

    assert command_first.ok is False
    assert command_first.failures[0].code == "EVIDENCE_CONTRACT_COMMAND_MISMATCH"
    assert command_first.failures[0].metadata["field"] == "command"
    assert command_first.failures[0].metadata["actual"] == "npm test"
    assert exit_code_first.ok is False
    assert exit_code_first.failures[0].code == command_first.failures[0].code
    assert exit_code_first.failures[0].metadata == command_first.failures[0].metadata


def test_fresh_candidate_still_passes_when_stale_candidate_is_present_first() -> None:
    stale_mismatch = _test_record(observed_at=149, exit_code=1)
    fresh_match = _test_record(observed_at=200, exit_code=0)

    verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "after": "last_code_mutation",
                    "exitCode": 0,
                }
            ]
        ),
        [stale_mismatch, fresh_match],
    )

    assert verdict.ok is True
    assert verdict.state == "pass"
    assert verdict.matched_evidence[0].observed_at == 200


def test_passing_candidate_selection_is_independent_of_input_order() -> None:
    older_match = _test_record(observed_at=200, command="python -m pytest tests/old.py")
    newer_match = _test_record(observed_at=201, command="python -m pytest tests/new.py")
    contract = _contract(
        requirements=[
            {
                "type": "TestRun",
                "after": "last_code_mutation",
                "commandPattern": "^python -m pytest",
                "exitCode": 0,
            }
        ]
    )

    older_first = evaluate_evidence_contract(contract, [older_match, newer_match])
    newer_first = evaluate_evidence_contract(contract, [newer_match, older_match])

    assert older_first.ok is True
    assert newer_first.ok is True
    assert older_first.matched_evidence[0].fields["command"] == "python -m pytest tests/old.py"
    assert newer_first.matched_evidence[0].fields["command"] == "python -m pytest tests/old.py"


def test_invalid_raw_audit_config_returns_invalid_config_audit_verdict() -> None:
    verdict = evaluate_evidence_contract(
        {"id": "bad-audit", "triggers": ["beforeCommit"], "requirements": [], "onMissing": "audit"},
        [_test_record()],
    )

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.enforcement == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"


def test_invalid_raw_block_config_returns_invalid_config_block_ready_verdict() -> None:
    verdict = evaluate_evidence_contract(
        {
            "id": "bad-block",
            "triggers": ["beforeCommit"],
            "requirements": [],
            "onMissing": "block_final_answer",
        },
        [_test_record()],
    )

    assert verdict.ok is False
    assert verdict.state == "block_ready"
    assert verdict.enforcement == "block_final_answer"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"


def test_constructed_invalid_contract_returns_block_ready_invalid_config_verdict() -> None:
    # C-4: ``EvidenceContract.model_construct`` no longer provides a bypass
    # escape hatch -- it routes through ``model_validate`` via the
    # ``FalseOnlyAuthorityModel`` kernel, so a structurally-invalid catalog
    # type (StripeWebhookAck) fails CLOSED at construction time before the
    # engine is ever asked to evaluate it. The "constructed-invalid block_ready
    # verdict" scenario is therefore unreachable via the pydantic API; the
    # invariant is now stricter (fail-CLOSED-on-construct rather than
    # fail-CLOSED-on-evaluate). The engine's hostile-mapping path
    # (``_RuntimeErrorMapping`` etc) still exercises the
    # ``EVIDENCE_CONTRACT_INVALID_CONFIG`` verdict code path.
    with pytest.raises(ValidationError):
        EvidenceContract.model_construct(
            id="constructed-bad-block",
            triggers=("beforeCommit",),
            requirements=(EvidenceRequirement.model_construct(type="StripeWebhookAck"),),
            on_missing="block_final_answer",
            retry_message="Provide external acknowledgement evidence.",
        )


def test_hostile_constructed_contract_attribute_error_after_validation_failure_returns_invalid_config() -> None:
    # C-4: as with the sibling test above, ``model_construct`` on a force-false
    # contract now routes through ``model_validate`` (kernel-owned). An
    # UnknownEvidenceType requirement is fail-CLOSED at construction; the
    # hostile __getattribute__ subclass path is no longer reachable through
    # ``model_construct``. The engine's hostile-mapping path remains tested by
    # ``test_hostile_raw_contract_mapping_runtime_error_returns_invalid_config``.
    class HostileContract(EvidenceContract):
        def __getattribute__(self, name: str) -> object:
            if name in {"id", "on_missing"}:
                raise RuntimeError(f"hostile contract attribute access for {name}")
            return super().__getattribute__(name)

    with pytest.raises(ValidationError):
        HostileContract.model_construct(
            id="constructed-hostile",
            triggers=("beforeCommit",),
            requirements=(EvidenceRequirement.model_construct(type="UnknownEvidenceType"),),
            on_missing="block_final_answer",
        )


def test_hostile_raw_contract_mapping_runtime_error_returns_invalid_config() -> None:
    verdict = evaluate_evidence_contract(_RuntimeErrorMapping(), [_test_record()])

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.enforcement == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert verdict.failures[0].contract_id == "invalid-config"


def test_hostile_raw_contract_on_missing_equality_returns_invalid_config() -> None:
    verdict = evaluate_evidence_contract(
        {
            "id": "bad-hostile-on-missing",
            "triggers": ["beforeCommit"],
            "requirements": [],
            "onMissing": _EqualityRuntimeError(),
        },
        [_test_record()],
    )

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.enforcement == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert verdict.failures[0].contract_id == "bad-hostile-on-missing"


def test_hostile_evidence_contract_subclass_model_copy_is_not_used() -> None:
    class HostileContract(EvidenceContract):
        def model_copy(self, *args: object, **kwargs: object) -> EvidenceContract:
            raise AssertionError("contract model_copy must not be used")

    contract = HostileContract.model_validate(_contract().model_dump(by_alias=True))

    verdict = evaluate_evidence_contract(contract, [_test_record()])

    assert verdict.ok is True
    assert verdict.state == "pass"


def test_hostile_evidence_records_iter_raises_returns_invalid_config_with_index_zero() -> None:
    verdict = evaluate_evidence_contract(_contract(), _IterRaises())

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert verdict.failures[0].metadata["recordIndex"] == 0


def test_hostile_evidence_records_next_raises_returns_invalid_config_with_failing_index() -> None:
    verdict = evaluate_evidence_contract(_contract(), _NextRaisesAfterOne())

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert verdict.failures[0].metadata["recordIndex"] == 1


def test_candidate_regex_input_length_is_bounded_and_mismatches_safely() -> None:
    verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "fields": {"command": {"matches": "^python -m pytest"}},
                }
            ]
        ),
        [_test_record(command="p" * (EVIDENCE_REGEX_CANDIDATE_LIMIT + 1))],
    )

    assert verdict.ok is False
    assert verdict.state == "failed"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"
    assert verdict.failures[0].metadata["candidateLength"] == EVIDENCE_REGEX_CANDIDATE_LIMIT + 1
    assert verdict.failures[0].metadata["candidateLimit"] == EVIDENCE_REGEX_CANDIDATE_LIMIT


def test_constructed_record_rejects_str_subclass_before_regex_matching() -> None:
    class LyingLongString(str):
        def __len__(self) -> int:
            return 1

    payload = LyingLongString("python -m pytest" + ("x" * EVIDENCE_REGEX_CANDIDATE_LIMIT))
    base_record = _test_record()
    constructed_record = EvidenceRecord.model_construct(
        type=base_record.type,
        status=base_record.status,
        observed_at=base_record.observed_at,
        source=base_record.source,
        fields={**base_record.fields, "command": payload},
        preview=base_record.preview,
        metadata=base_record.metadata,
    )
    verdict = evaluate_evidence_contract(
        _contract(
            requirements=[
                {
                    "type": "TestRun",
                    "fields": {"command": {"matches": "^python -m pytest"}},
                }
            ]
        ),
        [constructed_record],
    )

    assert verdict.ok is False
    assert verdict.state == "audit"
    assert verdict.failures[0].code == "EVIDENCE_CONTRACT_INVALID_CONFIG"
    assert verdict.failures[0].metadata["recordIndex"] == 0


def test_engine_class_uses_same_deterministic_evaluator() -> None:
    engine = EvidenceContractEngine()

    assert engine.evaluate(_contract(), [_test_record()]).state == "pass"


def test_contracts_module_import_boundary_stays_adk_and_runtime_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

contracts = importlib.import_module("magi_agent.evidence.contracts")
assert contracts.EVIDENCE_REGEX_CANDIDATE_LIMIT == 1000

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
    raise AssertionError(f"evidence contracts import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_verdict_dump_has_no_runner_kwargs_and_no_attachments() -> None:
    verdict = evaluate_evidence_contract(_contract(), [_test_record()])
    dumped = verdict.model_dump(by_alias=True)

    assert "trafficAttached" not in dumped
    assert "executionAttached" not in dumped
    assert "runnerKwargs" not in dumped
    assert "runner_kwargs" not in dumped
