from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Literal, Self
from weakref import finalize

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from openmagi_core_agent.evidence.child_runtime_envelope import ChildRuntimeEnvelope
from openmagi_core_agent.meta_orchestration.task_plan import (
    _copy_update_alias,
    _validate_public_ref,
    _validate_ref_tuple,
)


ChildAcceptanceStatus = Literal["accepted", "retry", "rejected", "blocked"]
ChildAcceptanceReasonCode = Literal[
    "accepted",
    "invalid_child_envelope",
    "runtime_receipt_mismatch",
    "parent_execution_mismatch",
    "child_execution_mismatch",
    "task_mismatch",
    "policy_snapshot_mismatch",
    "child_blocked",
    "missing_required_evidence",
    "retry_budget_exhausted",
]
RetryExhaustedStatus = Literal["rejected", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
    arbitrary_types_allowed=True,
)
_ACCEPTED_VERDICT_TOKEN = object()
_RUNTIME_ISSUED_RESULT_TOKEN = object()
_RUNTIME_CHILD_RESULT_OBJECT_IDS: set[int] = set()
_RUNTIME_CHILD_RESULT_FINGERPRINTS: dict[int, object] = {}
_RUNTIME_CHILD_RESULT_FINALIZERS: dict[int, object] = {}
_CHILD_ACCEPTANCE_VERDICT_OBJECT_IDS: set[int] = set()
_CHILD_ACCEPTANCE_VERDICT_FINGERPRINTS: dict[int, object] = {}
_CHILD_ACCEPTANCE_VERDICT_FINALIZERS: dict[int, object] = {}


class _ChildAcceptanceModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        raise TypeError("model_construct is disabled for child acceptance contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        return type(self).model_validate(data)


class ChildAcceptancePolicy(_ChildAcceptanceModel):
    parent_execution_id: str = Field(alias="parentExecutionId")
    child_execution_id: str = Field(alias="childExecutionId")
    task_id: str = Field(alias="taskId")
    parent_policy_snapshot_id: str = Field(alias="parentPolicySnapshotId")
    child_policy_snapshot_id: str = Field(alias="childPolicySnapshotId")
    runtime_receipt_ref: str = Field(alias="runtimeReceiptRef")
    required_evidence_refs: tuple[str, ...] = Field(alias="requiredEvidenceRefs")
    max_retry_budget: int = Field(alias="maxRetryBudget", ge=0, le=10, strict=True)
    current_attempt: int = Field(alias="currentAttempt", ge=0, le=10, strict=True)
    exhausted_status: RetryExhaustedStatus = Field(default="rejected", alias="exhaustedStatus")
    default_off: Literal[True] = Field(default=True, alias="defaultOff")

    @field_validator(
        "parent_execution_id",
        "child_execution_id",
        "task_id",
        "parent_policy_snapshot_id",
        "child_policy_snapshot_id",
        "runtime_receipt_ref",
    )
    @classmethod
    def _validate_ids(cls, value: str) -> str:
        return _validate_public_ref(value, "child acceptance policy identifiers")

    @field_validator("required_evidence_refs")
    @classmethod
    def _validate_required_refs(cls, value: Sequence[str]) -> tuple[str, ...]:
        refs = _validate_ref_tuple(value, "requiredEvidenceRefs")
        if not refs:
            raise ValueError("requiredEvidenceRefs must include at least one public evidence ref")
        return refs

    @field_validator("default_off", mode="before")
    @classmethod
    def _validate_default_off(cls, value: object) -> object:
        if value is not True:
            raise ValueError("defaultOff must remain true")
        return value

    @model_validator(mode="after")
    def _validate_retry_attempts(self) -> Self:
        if self.current_attempt > self.max_retry_budget:
            raise ValueError("currentAttempt must not exceed maxRetryBudget")
        return self


class RuntimeIssuedChildResult(_ChildAcceptanceModel):
    _issued_by_runtime_boundary: bool = PrivateAttr(default=False)

    envelope: ChildRuntimeEnvelope
    receipt_ref: str = Field(alias="receiptRef")
    issuance_token: object | None = Field(
        default=None,
        alias="issuanceToken",
        exclude=True,
        repr=False,
    )
    _issuance_token: ClassVar[object] = _RUNTIME_ISSUED_RESULT_TOKEN

    @classmethod
    def _from_runtime(
        cls,
        *,
        envelope: ChildRuntimeEnvelope,
        receipt_ref: str,
    ) -> Self:
        result = cls.model_validate(
            {
                "envelope": _revalidate_child_runtime_envelope(envelope),
                "receiptRef": _validate_public_ref(receipt_ref, "runtime child receipt ref"),
                "issuanceToken": cls._issuance_token,
            }
        )
        _mark_runtime_child_result_issued(result)
        return result

    @property
    def is_runtime_boundary_issued(self) -> bool:
        object_id = id(self)
        return (
            bool(self.__pydantic_private__.get("_issued_by_runtime_boundary"))
            and object_id in _RUNTIME_CHILD_RESULT_OBJECT_IDS
            and _RUNTIME_CHILD_RESULT_FINGERPRINTS.get(object_id)
            == _model_fingerprint(self)
        )

    @field_validator("envelope")
    @classmethod
    def _revalidate_envelope(cls, value: ChildRuntimeEnvelope) -> ChildRuntimeEnvelope:
        return _revalidate_child_runtime_envelope(value)

    @field_validator("receipt_ref")
    @classmethod
    def _validate_receipt_ref(cls, value: str) -> str:
        return _validate_public_ref(value, "runtime child receipt ref")

    @model_validator(mode="after")
    def _validate_runtime_issuance(self) -> Self:
        if self.issuance_token is not self._issuance_token:
            raise ValueError("child results must be issued by the runtime boundary")
        return self

    def revalidated_envelope(self) -> ChildRuntimeEnvelope:
        if self.issuance_token is not self._issuance_token or not self.is_runtime_boundary_issued:
            raise ValueError("child results must be issued by the runtime boundary")
        return _revalidate_child_runtime_envelope(self.envelope)


class ChildAcceptanceVerdict(_ChildAcceptanceModel):
    _issued_by_acceptance_evaluator: bool = PrivateAttr(default=False)

    status: ChildAcceptanceStatus
    reason_codes: tuple[ChildAcceptanceReasonCode, ...] = Field(alias="reasonCodes")
    accepted_evidence_refs: tuple[str, ...] = Field(alias="acceptedEvidenceRefs")
    missing_evidence_refs: tuple[str, ...] = Field(alias="missingEvidenceRefs")
    retryable: bool
    retry_budget_remaining: int = Field(alias="retryBudgetRemaining", ge=0, le=10, strict=True)
    _acceptance_token: ClassVar[object] = _ACCEPTED_VERDICT_TOKEN
    acceptance_token: object | None = Field(
        default=None,
        alias="acceptanceToken",
        exclude=True,
        repr=False,
    )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        if self.status == "accepted" and update:
            raise TypeError("accepted child verdicts cannot be updated after evaluation")
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        if self.status == "accepted" and data.get("status") == "accepted":
            data["acceptanceToken"] = self._acceptance_token
        return type(self).model_validate(data)

    @property
    def is_acceptance_evaluator_issued(self) -> bool:
        object_id = id(self)
        return (
            bool(self.__pydantic_private__.get("_issued_by_acceptance_evaluator"))
            and object_id in _CHILD_ACCEPTANCE_VERDICT_OBJECT_IDS
            and _CHILD_ACCEPTANCE_VERDICT_FINGERPRINTS.get(object_id)
            == _model_fingerprint(self)
        )

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(
        cls,
        value: Sequence[ChildAcceptanceReasonCode],
    ) -> tuple[ChildAcceptanceReasonCode, ...]:
        codes = tuple(value)
        if not codes:
            raise ValueError("reasonCodes must include at least one bounded reason")
        if len(set(codes)) != len(codes):
            raise ValueError("reasonCodes must not contain duplicates")
        return codes

    @field_validator("accepted_evidence_refs", "missing_evidence_refs")
    @classmethod
    def _validate_evidence_refs(cls, value: Sequence[str], info: Any) -> tuple[str, ...]:
        return _validate_ref_tuple(value, info.field_name)

    @model_validator(mode="after")
    def _validate_verdict_contract(self) -> Self:
        if self.status == "accepted" and self.acceptance_token is not self._acceptance_token:
            raise ValueError("accepted child verdicts must be produced by acceptance evaluation")
        if self.status == "accepted":
            if self.reason_codes != ("accepted",):
                raise ValueError("accepted verdicts must use the accepted reason code")
            if not self.accepted_evidence_refs:
                raise ValueError("accepted verdicts must include accepted evidence refs")
            if self.missing_evidence_refs:
                raise ValueError("accepted verdicts cannot have missing evidence refs")
            if self.retryable:
                raise ValueError("accepted verdicts cannot be retryable")
            return self
        if "accepted" in self.reason_codes:
            raise ValueError("accepted reason code is only valid for accepted verdicts")
        if self.status == "retry":
            if self.reason_codes != ("missing_required_evidence",):
                raise ValueError("retry verdicts must use missing_required_evidence")
            if not self.retryable:
                raise ValueError("retry verdicts must be retryable")
            if not self.missing_evidence_refs:
                raise ValueError("retry verdicts must include missing evidence refs")
            return self
        if self.status in {"rejected", "blocked"}:
            if self.retryable:
                raise ValueError("terminal verdicts cannot be retryable")
            if self.reason_codes == ("missing_required_evidence", "retry_budget_exhausted"):
                if not self.missing_evidence_refs:
                    raise ValueError("exhausted verdicts must include missing evidence refs")
                return self
            if self.missing_evidence_refs and self.status != "blocked":
                raise ValueError("terminal mismatch verdicts cannot include missing evidence refs")
            allowed_terminal_reasons = {
                "invalid_child_envelope",
                "runtime_receipt_mismatch",
                "parent_execution_mismatch",
                "child_execution_mismatch",
                "task_mismatch",
                "policy_snapshot_mismatch",
                "child_blocked",
            }
            if len(self.reason_codes) != 1 or self.reason_codes[0] not in allowed_terminal_reasons:
                raise ValueError("terminal verdicts must use a status-specific reason code")
            if self.reason_codes == ("child_blocked",) and self.status != "blocked":
                raise ValueError("child_blocked reason requires blocked status")
            if self.reason_codes != ("child_blocked",) and self.status == "blocked":
                raise ValueError("blocked status requires child_blocked or exhausted evidence reason")
            return self
        return self

    @classmethod
    def _from_evaluation(
        cls,
        *,
        status: ChildAcceptanceStatus,
        reason_codes: Sequence[ChildAcceptanceReasonCode],
        accepted_evidence_refs: Sequence[str],
        missing_evidence_refs: Sequence[str],
        retryable: bool,
        retry_budget_remaining: int,
    ) -> Self:
        payload: dict[str, object] = {
            "status": status,
            "reasonCodes": tuple(reason_codes),
            "acceptedEvidenceRefs": tuple(accepted_evidence_refs),
            "missingEvidenceRefs": tuple(missing_evidence_refs),
            "retryable": retryable,
            "retryBudgetRemaining": retry_budget_remaining,
        }
        if status == "accepted":
            payload["acceptanceToken"] = cls._acceptance_token
        verdict = cls.model_validate(payload)
        _mark_child_acceptance_verdict_issued(verdict)
        return verdict

    def public_projection(self) -> dict[str, object]:
        _validate_child_acceptance_verdict_issued(self)
        parsed = type(self).model_validate(
            {
                **self.model_dump(by_alias=True, mode="python", warnings=False),
                "acceptanceToken": (
                    self._acceptance_token if self.status == "accepted" else None
                ),
            }
        )
        return {
            "status": parsed.status,
            "reasonCodes": parsed.reason_codes,
            "acceptedEvidenceRefs": parsed.accepted_evidence_refs,
            "missingEvidenceRefs": parsed.missing_evidence_refs,
            "retryable": parsed.retryable,
            "retryBudgetRemaining": parsed.retry_budget_remaining,
        }


def issue_runtime_child_result(
    envelope: ChildRuntimeEnvelope,
    *,
    receipt_ref: str,
) -> RuntimeIssuedChildResult:
    return RuntimeIssuedChildResult._from_runtime(envelope=envelope, receipt_ref=receipt_ref)


def accept_real_child_envelope(
    envelope: ChildRuntimeEnvelope | object,
    *,
    receipt_ref: str,
    policy: ChildAcceptancePolicy | Mapping[str, object],
) -> ChildAcceptanceVerdict:
    """Token-validated acceptance for a real child runtime envelope.

    This is the meta-orchestration wiring used by the gated real child-execution
    path (Track 17 PR2): the envelope is promoted into a runtime-issued child
    result and evaluated against the runtime-issued token + policy.  A
    tampered/forged envelope or a structurally invalid/mismatched receipt token
    NEVER yields an accepted verdict — issuance failures degrade to a rejected
    ``invalid_child_envelope`` verdict rather than raising.

    NOTE: Implemented and tested in isolation, but not yet called from the
    real-execution path (``_run_real_child`` in ``child_runner_boundary.py``).
    Wiring into the live execution path is deferred to a later Track 17 PR.
    """
    try:
        issued: RuntimeIssuedChildResult | object = issue_runtime_child_result(
            envelope, receipt_ref=receipt_ref
        )
    except Exception:
        issued = object()
    return accept_child_result(issued, policy)


def accept_child_result(
    child_result: RuntimeIssuedChildResult | object,
    policy: ChildAcceptancePolicy | Mapping[str, object],
) -> ChildAcceptanceVerdict:
    parsed_policy = (
        policy
        if isinstance(policy, ChildAcceptancePolicy)
        else ChildAcceptancePolicy.model_validate(policy)
    )
    retry_remaining = _retry_budget_remaining(parsed_policy)
    try:
        if not isinstance(child_result, RuntimeIssuedChildResult):
            raise ValueError("child result must be runtime-issued")
        issued_result = child_result
        envelope = issued_result.revalidated_envelope()
    except Exception:
        return ChildAcceptanceVerdict._from_evaluation(
            status="rejected",
            reason_codes=("invalid_child_envelope",),
            accepted_evidence_refs=(),
            missing_evidence_refs=(),
            retryable=False,
            retry_budget_remaining=retry_remaining,
        )

    if issued_result.receipt_ref != parsed_policy.runtime_receipt_ref:
        return _terminal_verdict(
            reason_code="runtime_receipt_mismatch",
            policy=parsed_policy,
            retry_budget_remaining=retry_remaining,
        )
    if envelope.parent_boundary.execution_id != parsed_policy.parent_execution_id:
        return _terminal_verdict(
            reason_code="parent_execution_mismatch",
            policy=parsed_policy,
            retry_budget_remaining=retry_remaining,
        )
    if envelope.child_boundary.execution_id != parsed_policy.child_execution_id:
        return _terminal_verdict(
            reason_code="child_execution_mismatch",
            policy=parsed_policy,
            retry_budget_remaining=retry_remaining,
        )
    if envelope.task.task_id != parsed_policy.task_id:
        return _terminal_verdict(
            reason_code="task_mismatch",
            policy=parsed_policy,
            retry_budget_remaining=retry_remaining,
        )
    if (
        envelope.policy_snapshot.parent_policy_snapshot_id
        != parsed_policy.parent_policy_snapshot_id
        or envelope.policy_snapshot.child_policy_snapshot_id
        != parsed_policy.child_policy_snapshot_id
    ):
        return _terminal_verdict(
            reason_code="policy_snapshot_mismatch",
            policy=parsed_policy,
            retry_budget_remaining=retry_remaining,
        )
    if envelope.status == "blocked":
        return ChildAcceptanceVerdict._from_evaluation(
            status="blocked",
            reason_codes=("child_blocked",),
            accepted_evidence_refs=(),
            missing_evidence_refs=parsed_policy.required_evidence_refs,
            retryable=False,
            retry_budget_remaining=retry_remaining,
        )

    public_refs = _runtime_public_evidence_refs(envelope, receipt_ref=issued_result.receipt_ref)
    accepted_refs = tuple(ref for ref in parsed_policy.required_evidence_refs if ref in public_refs)
    missing_refs = tuple(ref for ref in parsed_policy.required_evidence_refs if ref not in public_refs)
    if missing_refs and retry_remaining > 0:
        return ChildAcceptanceVerdict._from_evaluation(
            status="retry",
            reason_codes=("missing_required_evidence",),
            accepted_evidence_refs=accepted_refs,
            missing_evidence_refs=missing_refs,
            retryable=True,
            retry_budget_remaining=retry_remaining,
        )
    if missing_refs:
        return ChildAcceptanceVerdict._from_evaluation(
            status=parsed_policy.exhausted_status,
            reason_codes=("missing_required_evidence", "retry_budget_exhausted"),
            accepted_evidence_refs=accepted_refs,
            missing_evidence_refs=missing_refs,
            retryable=False,
            retry_budget_remaining=0,
        )

    return ChildAcceptanceVerdict._from_evaluation(
        status="accepted",
        reason_codes=("accepted",),
        accepted_evidence_refs=accepted_refs,
        missing_evidence_refs=(),
        retryable=False,
        retry_budget_remaining=retry_remaining,
    )


def _retry_budget_remaining(policy: ChildAcceptancePolicy) -> int:
    return max(policy.max_retry_budget - policy.current_attempt, 0)


def _mark_runtime_child_result_issued(result: RuntimeIssuedChildResult) -> None:
    object_id = id(result)
    result.__pydantic_private__["_issued_by_runtime_boundary"] = True
    _RUNTIME_CHILD_RESULT_OBJECT_IDS.add(object_id)
    _RUNTIME_CHILD_RESULT_FINGERPRINTS[object_id] = _model_fingerprint(result)
    _RUNTIME_CHILD_RESULT_FINALIZERS[object_id] = finalize(
        result,
        _discard_runtime_child_result_object_id,
        object_id,
    )


def _discard_runtime_child_result_object_id(object_id: int) -> None:
    _RUNTIME_CHILD_RESULT_OBJECT_IDS.discard(object_id)
    _RUNTIME_CHILD_RESULT_FINGERPRINTS.pop(object_id, None)
    _RUNTIME_CHILD_RESULT_FINALIZERS.pop(object_id, None)


def _mark_child_acceptance_verdict_issued(verdict: ChildAcceptanceVerdict) -> None:
    object_id = id(verdict)
    verdict.__pydantic_private__["_issued_by_acceptance_evaluator"] = True
    _CHILD_ACCEPTANCE_VERDICT_OBJECT_IDS.add(object_id)
    _CHILD_ACCEPTANCE_VERDICT_FINGERPRINTS[object_id] = _model_fingerprint(verdict)
    _CHILD_ACCEPTANCE_VERDICT_FINALIZERS[object_id] = finalize(
        verdict,
        _discard_child_acceptance_verdict_object_id,
        object_id,
    )


def _discard_child_acceptance_verdict_object_id(object_id: int) -> None:
    _CHILD_ACCEPTANCE_VERDICT_OBJECT_IDS.discard(object_id)
    _CHILD_ACCEPTANCE_VERDICT_FINGERPRINTS.pop(object_id, None)
    _CHILD_ACCEPTANCE_VERDICT_FINALIZERS.pop(object_id, None)


def _validate_child_acceptance_verdict_issued(verdict: ChildAcceptanceVerdict) -> None:
    if not verdict.is_acceptance_evaluator_issued:
        raise ValueError("child acceptance verdict was modified after child acceptance evaluation")


def _model_fingerprint(model: BaseModel) -> object:
    return _freeze_for_fingerprint(
        model.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _freeze_for_fingerprint(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple(
            (str(key), _freeze_for_fingerprint(item))
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        )
    if isinstance(value, tuple | list):
        return tuple(_freeze_for_fingerprint(item) for item in value)
    return value


def _terminal_verdict(
    *,
    reason_code: ChildAcceptanceReasonCode,
    policy: ChildAcceptancePolicy,
    retry_budget_remaining: int,
) -> ChildAcceptanceVerdict:
    return ChildAcceptanceVerdict._from_evaluation(
        status="rejected",
        reason_codes=(reason_code,),
        accepted_evidence_refs=(),
        missing_evidence_refs=(),
        retryable=False,
        retry_budget_remaining=retry_budget_remaining,
    )


def _runtime_public_evidence_refs(
    envelope: ChildRuntimeEnvelope,
    *,
    receipt_ref: str,
) -> frozenset[str]:
    refs = (
        envelope.ledger_ref.ledger_id,
        receipt_ref,
        *envelope.audit_event_refs,
    )
    return frozenset(_validate_public_ref(ref, "child runtime public evidence ref") for ref in refs)


def _revalidate_child_runtime_envelope(envelope: ChildRuntimeEnvelope) -> ChildRuntimeEnvelope:
    if not isinstance(envelope, ChildRuntimeEnvelope) or not envelope.is_runtime_boundary_issued:
        raise ValueError("child runtime envelope must be runtime-issued")
    return envelope


__all__ = [
    "ChildAcceptancePolicy",
    "ChildAcceptanceReasonCode",
    "ChildAcceptanceStatus",
    "ChildAcceptanceVerdict",
    "RetryExhaustedStatus",
    "RuntimeIssuedChildResult",
    "accept_child_result",
    "accept_real_child_envelope",
    "issue_runtime_child_result",
]
