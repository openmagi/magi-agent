from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import ValidationError

from magi_agent.telemetry.trace_context import get_trace

from .types import (
    EvidenceContract,
    EvidenceContractFailure,
    EvidenceContractVerdict,
    EvidenceFieldMatcher,
    EvidenceRecord,
    EvidenceRequirement,
    EvidenceSource,
)


# Small scaffold guard for deterministic regex matching over caller-provided
# evidence. Over-limit candidates mismatch without attempting regex evaluation.
EVIDENCE_REGEX_CANDIDATE_LIMIT = 1000

_FAILURE_PRIORITY = {
    "EVIDENCE_CONTRACT_COMMAND_MISMATCH": 0,
    "EVIDENCE_CONTRACT_FIELD_MISMATCH": 1,
    "EVIDENCE_CONTRACT_STALE": 2,
    "EVIDENCE_CONTRACT_MISSING": 3,
    "EVIDENCE_CONTRACT_INVALID_CONFIG": 4,
}


class EvidenceContractEngine:
    """Deterministic evaluator for already-provided OpenMagi evidence records."""

    def evaluate(
        self,
        contract: EvidenceContract | Mapping[str, object],
        evidence_records: Iterable[EvidenceRecord],
    ) -> EvidenceContractVerdict:
        return evaluate_evidence_contract(contract, evidence_records)


def evaluate_evidence_contract(
    contract: EvidenceContract | Mapping[str, object],
    evidence_records: Iterable[EvidenceRecord],
) -> EvidenceContractVerdict:
    parsed_contract = _parse_contract_or_invalid_verdict(contract)
    if isinstance(parsed_contract, EvidenceContractVerdict):
        return parsed_contract
    invalid_config_verdict = _invalid_requirement_config_verdict(parsed_contract)
    if invalid_config_verdict is not None:
        return invalid_config_verdict

    parsed_records = _parse_records_or_invalid_verdict(parsed_contract, evidence_records)
    if isinstance(parsed_records, EvidenceContractVerdict):
        return parsed_records
    records = parsed_records
    matched_records: list[EvidenceRecord] = []
    missing_requirements: list[EvidenceRequirement] = []
    failures: list[EvidenceContractFailure] = []

    for requirement in parsed_contract.requirements:
        match = _match_requirement(parsed_contract, requirement, records)
        if match.record is not None:
            matched_records.append(match.record)
            continue
        if match.missing_requirement is not None:
            missing_requirements.append(match.missing_requirement)
        if match.failure is None:
            raise AssertionError("requirement mismatch must include a deterministic failure")
        failures.append(match.failure)

    verdict = _build_verdict(
        contract_id=parsed_contract.id,
        enforcement=parsed_contract.on_missing,
        missing_requirements=tuple(missing_requirements),
        matched_evidence=tuple(matched_records),
        failures=tuple(failures),
        retry_message=parsed_contract.retry_message,
        requirement_coverage=_contract_requirement_coverage(parsed_contract),
    )
    trace = get_trace()
    if trace is not None:
        trace.record("evidence", "EvidenceContractEngine", "evaluate", f"contract_id={parsed_contract.id}, ok={verdict.ok}, state={verdict.state}")
    return verdict


class _RequirementMatch:
    def __init__(
        self,
        *,
        record: EvidenceRecord | None = None,
        failure: EvidenceContractFailure | None = None,
        missing_requirement: EvidenceRequirement | None = None,
    ) -> None:
        self.record = record
        self.failure = failure
        self.missing_requirement = missing_requirement


def _parse_contract_or_invalid_verdict(
    contract: EvidenceContract | Mapping[str, object],
) -> EvidenceContract | EvidenceContractVerdict:
    try:
        if isinstance(contract, EvidenceContract):
            data = EvidenceContract.model_dump(
                contract,
                by_alias=False,
                mode="python",
                warnings=False,
            )
            return EvidenceContract.model_validate(data)
        return EvidenceContract.model_validate(contract)
    except Exception as exc:
        enforcement = _raw_enforcement(contract)
        contract_id = _raw_contract_id(contract)
        failure = EvidenceContractFailure(
            code="EVIDENCE_CONTRACT_INVALID_CONFIG",
            contract_id=contract_id,
            message="Evidence contract config is invalid.",
            metadata={"validationError": str(exc)},
        )
    return _build_verdict(
        contract_id=contract_id,
        enforcement=enforcement,
        missing_requirements=(),
        matched_evidence=(),
        failures=(failure,),
        retry_message=None,
        invalid_audit_state=enforcement == "audit",
    )


def _invalid_requirement_config_verdict(
    contract: EvidenceContract,
) -> EvidenceContractVerdict | None:
    failures: list[EvidenceContractFailure] = []
    for requirement in contract.requirements:
        invalid_fields = [
            field_name
            for field_name, configured in (
                ("commandPattern", requirement.command_pattern is not None),
                ("exitCode", requirement.exit_code is not None),
            )
            if configured and requirement.type != "TestRun"
        ]
        if not invalid_fields:
            continue
        failures.append(
            _failure(
                contract.id,
                requirement,
                "EVIDENCE_CONTRACT_INVALID_CONFIG",
                "Evidence requirement uses TestRun-only options on a non-TestRun type.",
                metadata={
                    "invalidFields": tuple(invalid_fields),
                    "requirementType": requirement.type,
                },
            )
        )

    if not failures:
        return None
    return _build_verdict(
        contract_id=contract.id,
        enforcement=contract.on_missing,
        missing_requirements=(),
        matched_evidence=(),
        failures=tuple(failures),
        retry_message=contract.retry_message,
        invalid_audit_state=contract.on_missing == "audit",
    )


def _parse_records_or_invalid_verdict(
    contract: EvidenceContract,
    evidence_records: Iterable[EvidenceRecord],
) -> tuple[EvidenceRecord, ...] | EvidenceContractVerdict:
    records: list[EvidenceRecord] = []
    index = 0
    try:
        record_iterator = iter(evidence_records)
    except Exception as exc:
        return _invalid_record_input_verdict(contract, index, exc)

    while True:
        try:
            record = next(record_iterator)
        except StopIteration:
            break
        except Exception as exc:
            return _invalid_record_input_verdict(contract, index, exc)
        try:
            if isinstance(record, EvidenceRecord):
                _reject_invalid_constructed_record_mappings(record)
                records.append(
                    EvidenceRecord.model_validate(
                        EvidenceRecord.model_dump(
                            record,
                            by_alias=False,
                            mode="python",
                            warnings=False,
                        )
                    )
                )
            else:
                records.append(EvidenceRecord.model_validate(record))
        except Exception as exc:
            return _invalid_record_input_verdict(contract, index, exc)
        index += 1
    return tuple(records)


def _invalid_record_input_verdict(
    contract: EvidenceContract,
    index: int,
    exc: Exception,
) -> EvidenceContractVerdict:
    failure = EvidenceContractFailure(
        code="EVIDENCE_CONTRACT_INVALID_CONFIG",
        contract_id=contract.id,
        message="Evidence record input is invalid.",
        metadata={
            "recordIndex": index,
            "validationError": "Evidence record validation failed.",
            "validationErrorCount": _validation_error_count(exc),
        },
    )
    return _build_verdict(
        contract_id=contract.id,
        enforcement=contract.on_missing,
        missing_requirements=(),
        matched_evidence=(),
        failures=(failure,),
        retry_message=contract.retry_message,
        invalid_audit_state=contract.on_missing == "audit",
    )


def _reject_invalid_constructed_record_mappings(record: EvidenceRecord) -> None:
    if not isinstance(record.fields, Mapping):
        raise ValueError("EvidenceRecord.fields must be a mapping")
    if not isinstance(record.metadata, Mapping):
        raise ValueError("EvidenceRecord.metadata must be a mapping")
    if not isinstance(record.source, EvidenceSource):
        raise ValueError("EvidenceRecord.source must be an EvidenceSource")
    if not isinstance(record.source.metadata, Mapping):
        raise ValueError("EvidenceSource.metadata must be a mapping")


def _validation_error_count(exc: BaseException) -> int:
    if isinstance(exc, ValidationError):
        return exc.error_count()
    return 1


def _raw_enforcement(contract: EvidenceContract | Mapping[str, object]) -> str:
    if isinstance(contract, Mapping):
        raw_value = _safe_mapping_get(contract, "onMissing", _safe_mapping_get(contract, "on_missing"))
    else:
        raw_value = _safe_getattr(contract, "on_missing")
    if type(raw_value) is str and raw_value == "block_final_answer":
        return "block_final_answer"
    return "audit"


def _raw_contract_id(contract: EvidenceContract | Mapping[str, object]) -> str:
    if isinstance(contract, Mapping):
        raw_id = _safe_mapping_get(contract, "id")
    else:
        raw_id = _safe_getattr(contract, "id")
    if type(raw_id) is str:
        try:
            EvidenceContractFailure(
                code="EVIDENCE_CONTRACT_INVALID_CONFIG",
                contract_id=raw_id,
            )
            return raw_id
        except ValidationError:
            pass
    return "invalid-config"


def _safe_getattr(
    value: object,
    name: str,
    default: object = None,
) -> object:
    try:
        return getattr(value, name, default)
    except Exception:
        return default


def _safe_mapping_get(
    mapping: Mapping[str, object],
    key: str,
    default: object = None,
) -> object:
    try:
        return mapping.get(key, default)
    except Exception:
        return default


def _match_requirement(
    contract: EvidenceContract,
    requirement: EvidenceRequirement,
    records: tuple[EvidenceRecord, ...],
) -> _RequirementMatch:
    candidates = tuple(record for record in records if record.type == requirement.type)
    if not candidates:
        return _RequirementMatch(
            failure=_failure(
                contract.id,
                requirement,
                "EVIDENCE_CONTRACT_MISSING",
                "Required evidence was not observed.",
            ),
            missing_requirement=requirement,
        )

    stale_failures: list[EvidenceContractFailure] = []
    fresh_failures: list[EvidenceContractFailure] = []
    for record in sorted(candidates, key=_record_sort_key):
        stale_failure = _stale_failure(contract, requirement, record)
        if stale_failure is not None:
            stale_failures.append(stale_failure)
            continue

        candidate_failure = _candidate_mismatch(contract, requirement, record)
        if candidate_failure is None:
            return _RequirementMatch(record=record)
        fresh_failures.append(candidate_failure)

    if fresh_failures:
        return _RequirementMatch(failure=_prioritized_failure(fresh_failures))
    return _RequirementMatch(failure=_prioritized_failure(stale_failures))


def _prioritized_failure(
    failures: Iterable[EvidenceContractFailure],
) -> EvidenceContractFailure:
    return min(failures, key=_failure_sort_key)


def _record_sort_key(
    record: EvidenceRecord,
) -> tuple[object, object, object, object, object]:
    return (
        _sort_value(record.observed_at),
        _sort_value(record.source.tool_call_id),
        _sort_value(record.source.event_id),
        _sort_value(record.source.artifact_id),
        _sort_value(record.model_dump(by_alias=True)),
    )


def _failure_sort_key(
    failure: EvidenceContractFailure,
) -> tuple[int, object, object, object, object, object, object]:
    metadata = failure.metadata
    return (
        _FAILURE_PRIORITY.get(failure.code, 99),
        _sort_value(metadata.get("field")),
        _sort_value(metadata.get("matcher")),
        _sort_value(metadata.get("expectedPattern")),
        _sort_value(metadata.get("expected")),
        _sort_value(metadata.get("actual")),
        _sort_value(metadata),
    )


def _sort_value(value: object) -> object:
    if isinstance(value, Mapping):
        return (
            "mapping",
            tuple((key, _sort_value(nested)) for key, nested in sorted(value.items())),
        )
    if isinstance(value, tuple):
        return ("sequence", tuple(_sort_value(nested) for nested in value))
    return (type(value).__name__, repr(value))


def _stale_failure(
    contract: EvidenceContract,
    requirement: EvidenceRequirement,
    record: EvidenceRecord,
) -> EvidenceContractFailure | None:
    if requirement.after is None:
        return None
    boundary = _boundary_value(contract, record, requirement.after)
    if boundary is None or record.observed_at >= boundary:
        return None
    return _failure(
        contract.id,
        requirement,
        "EVIDENCE_CONTRACT_STALE",
        "Evidence was observed before the required boundary.",
        metadata={
            "boundary": requirement.after,
            "boundaryObservedAt": boundary,
            "recordObservedAt": record.observed_at,
        },
    )


def _boundary_value(
    contract: EvidenceContract,
    record: EvidenceRecord,
    boundary_name: str,
) -> int | float | None:
    keys = {
        "last_code_mutation": ("lastCodeMutation", "last_code_mutation"),
        "contract_start": ("contractStart", "contract_start"),
    }[boundary_name]
    for source in (contract.when, record.metadata):
        if source is None:
            continue
        for key in keys:
            value = source.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int | float):
                return value
    return None


def _candidate_mismatch(
    contract: EvidenceContract,
    requirement: EvidenceRequirement,
    record: EvidenceRecord,
) -> EvidenceContractFailure | None:
    if record.status != "ok":
        return _failure(
            contract.id,
            requirement,
            "EVIDENCE_CONTRACT_FIELD_MISMATCH",
            "Evidence record status did not satisfy the requirement.",
            metadata={"recordStatus": record.status},
        )

    if requirement.type == "TestRun":
        command_failure = _match_test_run_command(contract, requirement, record)
        if command_failure is not None:
            return command_failure
        exit_code_failure = _match_test_run_exit_code(contract, requirement, record)
        if exit_code_failure is not None:
            return exit_code_failure

    for field_name, matcher in requirement.fields.items():
        field_match = _match_field(record.fields, field_name, matcher)
        if field_match is None:
            continue
        return _failure(
            contract.id,
            requirement,
            "EVIDENCE_CONTRACT_FIELD_MISMATCH",
            "Evidence field did not satisfy the requirement.",
            metadata={"field": field_name, **field_match},
        )

    return None


def _match_test_run_command(
    contract: EvidenceContract,
    requirement: EvidenceRequirement,
    record: EvidenceRecord,
) -> EvidenceContractFailure | None:
    if requirement.command_pattern is None:
        return None
    command = record.fields.get("command")
    mismatch = _match_regex_candidate(command, requirement.command_pattern)
    if mismatch is None:
        return None
    return _failure(
        contract.id,
        requirement,
        "EVIDENCE_CONTRACT_COMMAND_MISMATCH",
        "TestRun command did not satisfy commandPattern.",
        metadata={"field": "command", **mismatch},
    )


def _match_test_run_exit_code(
    contract: EvidenceContract,
    requirement: EvidenceRequirement,
    record: EvidenceRecord,
) -> EvidenceContractFailure | None:
    if requirement.exit_code is None:
        return None
    actual_exit_code = record.fields.get("exitCode")
    if _strict_equal(actual_exit_code, requirement.exit_code):
        return None
    return _failure(
        contract.id,
        requirement,
        "EVIDENCE_CONTRACT_FIELD_MISMATCH",
        "TestRun exitCode did not satisfy the requirement.",
        metadata={
            "field": "exitCode",
            "expected": requirement.exit_code,
            "actual": actual_exit_code,
        },
    )


def _match_field(
    fields: Mapping[str, object],
    field_name: str,
    matcher: EvidenceFieldMatcher,
) -> dict[str, object] | None:
    field_exists = field_name in fields
    value = fields.get(field_name)

    if matcher.exists is not None and field_exists is not matcher.exists:
        return {"matcher": "exists", "expected": matcher.exists, "actual": field_exists}
    if not field_exists:
        return _missing_field_value_matcher_mismatch(matcher)
    if matcher.equals is not None and not _strict_equal(value, matcher.equals):
        return {"matcher": "equals", "expected": matcher.equals, "actual": value}
    if matcher.one_of is not None and not any(
        _strict_equal(value, expected) for expected in matcher.one_of
    ):
        return {"matcher": "oneOf", "expected": matcher.one_of, "actual": value}
    if matcher.matches is not None:
        regex_mismatch = _match_regex_candidate(value, matcher.matches)
        if regex_mismatch is not None:
            return {"matcher": "matches", **regex_mismatch}

    return None


def _missing_field_value_matcher_mismatch(
    matcher: EvidenceFieldMatcher,
) -> dict[str, object] | None:
    if matcher.equals is not None:
        return {
            "matcher": "equals",
            "expected": matcher.equals,
            "actual": None,
            "actualExists": False,
        }
    if matcher.one_of is not None:
        return {
            "matcher": "oneOf",
            "expected": matcher.one_of,
            "actual": None,
            "actualExists": False,
        }
    if matcher.matches is not None:
        return {
            "matcher": "matches",
            "expectedPattern": matcher.matches,
            "actualExists": False,
        }
    return None


def _strict_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, Mapping) and isinstance(expected, Mapping):
        if actual.keys() != expected.keys():
            return False
        return all(_strict_equal(actual[key], expected[key]) for key in actual)
    if isinstance(actual, tuple) and isinstance(expected, tuple):
        if len(actual) != len(expected):
            return False
        return all(
            _strict_equal(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
    return actual == expected


def _match_regex_candidate(value: object, pattern: str) -> dict[str, object] | None:
    if type(value) is str:
        candidate = value
    elif isinstance(value, str):
        candidate = str.__str__(value)
    else:
        return {"expectedPattern": pattern, "actualType": type(value).__name__}
    if len(candidate) > EVIDENCE_REGEX_CANDIDATE_LIMIT:
        return {
            "expectedPattern": pattern,
            "candidateLength": len(candidate),
            "candidateLimit": EVIDENCE_REGEX_CANDIDATE_LIMIT,
        }
    if re.search(pattern, candidate):
        return None
    return {"expectedPattern": pattern, "actual": candidate}


def _failure(
    contract_id: str,
    requirement: EvidenceRequirement,
    code: str,
    message: str,
    *,
    metadata: Mapping[str, object] | None = None,
) -> EvidenceContractFailure:
    return EvidenceContractFailure(
        code=code,
        contract_id=contract_id,
        requirement_type=requirement.type,
        message=message,
        metadata=metadata or {},
    )


def _build_verdict(
    *,
    contract_id: str,
    enforcement: str,
    missing_requirements: tuple[EvidenceRequirement, ...],
    matched_evidence: tuple[EvidenceRecord, ...],
    failures: tuple[EvidenceContractFailure, ...],
    retry_message: str | None,
    requirement_coverage: tuple[str, ...] = (),
    invalid_audit_state: bool = False,
) -> EvidenceContractVerdict:
    return EvidenceContractVerdict(
        contract_id=contract_id,
        ok=not failures,
        state=_verdict_state(enforcement, failures, invalid_audit_state),
        enforcement=enforcement,
        missing_requirements=missing_requirements,
        matched_evidence=matched_evidence,
        failures=failures,
        retry_message=retry_message,
        requirement_coverage=requirement_coverage,
    )


def _contract_requirement_coverage(contract: EvidenceContract) -> tuple[str, ...]:
    return tuple(dict.fromkeys(requirement.type for requirement in contract.requirements))


def _verdict_state(
    enforcement: str,
    failures: tuple[EvidenceContractFailure, ...],
    invalid_audit_state: bool,
) -> str:
    if not failures:
        return "pass"
    if enforcement == "block_final_answer":
        return "block_ready"
    if invalid_audit_state:
        return "audit"
    if any(failure.code == "EVIDENCE_CONTRACT_MISSING" for failure in failures):
        return "missing"
    return "failed"


def evidence_command_digest(command: str) -> str:
    """Return a sha256 digest of a test command string.

    Used for digest-only public reports so raw command strings are not
    exposed in public projections.
    """
    import hashlib

    return "sha256:" + hashlib.sha256(command.encode("utf-8")).hexdigest()


__all__ = [
    "EVIDENCE_REGEX_CANDIDATE_LIMIT",
    "EvidenceContractEngine",
    "evaluate_evidence_contract",
    "evidence_command_digest",
]
