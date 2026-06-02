from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.meta_orchestration.child_acceptance import (
    ChildAcceptanceVerdict,
)
from magi_agent.meta_orchestration.task_plan import (
    _copy_update_alias,
    _validate_public_ref,
    _validate_ref_tuple,
)


MetaInspectionAggregateStatus = Literal["complete", "needs_retry", "blocked", "partial"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class _MetaInspectionModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        raise TypeError("model_construct is disabled for meta inspection loop contracts")

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


class MetaInspectedChildVerdict(_MetaInspectionModel):
    task_id: str = Field(alias="taskId")
    required: bool = True
    attempt: int = Field(ge=0, le=10, strict=True)
    verdict: ChildAcceptanceVerdict

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        data["verdict"] = self.verdict
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        return type(self).model_validate(data)

    @field_validator("task_id")
    @classmethod
    def _validate_task_id(cls, value: str) -> str:
        return _validate_public_ref(value, "taskId")

    @field_validator("required", mode="before")
    @classmethod
    def _validate_required(cls, value: object) -> object:
        if value is not True and value is not False:
            raise ValueError("required must be a strict boolean")
        return value

    @field_validator("verdict", mode="before")
    @classmethod
    def _validate_verdict(cls, value: object) -> object:
        if not isinstance(value, ChildAcceptanceVerdict):
            raise ValueError("child verdicts must be ChildAcceptanceVerdict instances")
        return value

    @model_validator(mode="after")
    def _validate_retry_budget(self) -> Self:
        if self.verdict.status == "retry" and self.verdict.retry_budget_remaining <= 0:
            raise ValueError("retry verdicts must include remaining retry budget")
        if self.verdict.status == "retry" and self.attempt >= 10:
            raise ValueError("retry verdicts cannot schedule beyond bounded attempt limit")
        return self

    def public_projection(self) -> dict[str, object]:
        parsed = type(self).model_validate(
            {
                "taskId": self.task_id,
                "required": self.required,
                "attempt": self.attempt,
                "verdict": self.verdict,
            }
        )
        return {
            "taskId": parsed.task_id,
            "status": parsed.verdict.status,
            "reasonCodes": parsed.verdict.reason_codes,
            "acceptedEvidenceRefCount": len(parsed.verdict.accepted_evidence_refs),
            "missingEvidenceRefCount": len(parsed.verdict.missing_evidence_refs),
            "retryable": parsed.verdict.retryable,
            "retryBudgetRemaining": parsed.verdict.retry_budget_remaining,
            "required": parsed.required,
        }


class MetaInspectionLoopResult(_MetaInspectionModel):
    loop_id: str = Field(alias="loopId")
    child_verdicts: tuple[MetaInspectedChildVerdict, ...] = Field(alias="childVerdicts")
    aggregate_status: MetaInspectionAggregateStatus = Field(alias="aggregateStatus")
    retry_schedule_refs: tuple[str, ...] = Field(alias="retryScheduleRefs")
    exhausted_retry_reasons: tuple[str, ...] = Field(alias="exhaustedRetryReasons")
    accepted_child_evidence_refs_for_assembly: tuple[str, ...] = Field(
        alias="acceptedChildEvidenceRefsForAssembly",
    )
    parent_executed_child_tools: Literal[False] = Field(
        default=False,
        alias="parentExecutedChildTools",
    )
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    private_notes: tuple[str, ...] = Field(default=(), alias="privateNotes", exclude=True)

    @field_validator("loop_id")
    @classmethod
    def _validate_loop_id(cls, value: str) -> str:
        return _validate_public_ref(value, "loopId")

    @field_validator("child_verdicts")
    @classmethod
    def _validate_child_verdicts(
        cls,
        value: Sequence[MetaInspectedChildVerdict],
    ) -> tuple[MetaInspectedChildVerdict, ...]:
        verdicts = tuple(value)
        task_ids = tuple(child.task_id for child in verdicts)
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("childVerdicts taskId values must be unique")
        return verdicts

    @field_validator(
        "retry_schedule_refs",
        "exhausted_retry_reasons",
        "accepted_child_evidence_refs_for_assembly",
    )
    @classmethod
    def _validate_ref_fields(cls, value: Sequence[str], info: Any) -> tuple[str, ...]:
        return _validate_ref_tuple(value, info.field_name)

    @field_validator("parent_executed_child_tools", mode="before")
    @classmethod
    def _validate_parent_executed_child_tools(cls, value: object) -> object:
        if value is not False:
            raise ValueError("parentExecutedChildTools must remain false")
        return value

    @field_validator("default_off", mode="before")
    @classmethod
    def _validate_default_off(cls, value: object) -> object:
        if value is not True:
            raise ValueError("defaultOff must remain true")
        return value

    @field_validator("private_notes")
    @classmethod
    def _validate_private_notes(cls, value: Sequence[str]) -> tuple[str, ...]:
        notes = tuple(value)
        for item in notes:
            if not item.strip():
                raise ValueError("privateNotes must be non-empty")
        return notes

    @field_serializer("parent_executed_child_tools")
    def _serialize_parent_executed_child_tools(self, _value: object) -> bool:
        return False

    @model_validator(mode="after")
    def _canonicalize_derived_fields(self) -> Self:
        object.__setattr__(self, "aggregate_status", _aggregate_status(self.child_verdicts))
        object.__setattr__(
            self,
            "retry_schedule_refs",
            _retry_schedule_refs(self.loop_id, self.child_verdicts),
        )
        object.__setattr__(
            self,
            "exhausted_retry_reasons",
            _exhausted_retry_reasons(self.child_verdicts),
        )
        object.__setattr__(
            self,
            "accepted_child_evidence_refs_for_assembly",
            _accepted_refs_for_assembly(self.child_verdicts),
        )
        object.__setattr__(self, "parent_executed_child_tools", False)
        object.__setattr__(self, "default_off", True)
        return self

    def public_projection(self) -> dict[str, object]:
        parsed = inspect_child_verdicts(self.loop_id, self.child_verdicts)
        return {
            "loopId": parsed.loop_id,
            "aggregateStatus": parsed.aggregate_status,
            "childVerdicts": tuple(child.public_projection() for child in parsed.child_verdicts),
            "retryScheduleRefs": parsed.retry_schedule_refs,
            "exhaustedRetryReasons": parsed.exhausted_retry_reasons,
            "acceptedChildEvidenceRefCountForAssembly": len(
                parsed.accepted_child_evidence_refs_for_assembly,
            ),
            "parentExecutedChildTools": parsed.parent_executed_child_tools,
            "defaultOff": parsed.default_off,
        }


def inspect_child_verdicts(
    loop_id: str,
    child_verdicts: Sequence[MetaInspectedChildVerdict | Mapping[str, object]],
    *,
    parent_executed_child_tools: bool = False,
    private_notes: Sequence[str] = (),
) -> MetaInspectionLoopResult:
    if parent_executed_child_tools:
        raise ValueError("parent inspection loop cannot execute child tools")
    parsed_loop_id = _validate_public_ref(loop_id, "loopId")
    inspected = tuple(_parse_child_verdict(item) for item in child_verdicts)

    return MetaInspectionLoopResult.model_validate(
        {
            "loopId": parsed_loop_id,
            "childVerdicts": inspected,
            "aggregateStatus": _aggregate_status(inspected),
            "retryScheduleRefs": _retry_schedule_refs(parsed_loop_id, inspected),
            "exhaustedRetryReasons": _exhausted_retry_reasons(inspected),
            "acceptedChildEvidenceRefsForAssembly": _accepted_refs_for_assembly(inspected),
            "parentExecutedChildTools": False,
            "defaultOff": True,
            "privateNotes": tuple(private_notes),
        }
    )


def _parse_child_verdict(
    item: MetaInspectedChildVerdict | Mapping[str, object],
) -> MetaInspectedChildVerdict:
    if isinstance(item, MetaInspectedChildVerdict):
        return item
    return MetaInspectedChildVerdict.model_validate(item)


def _aggregate_status(
    child_verdicts: tuple[MetaInspectedChildVerdict, ...],
) -> MetaInspectionAggregateStatus:
    if any(child.required and child.verdict.status in {"blocked", "rejected"} for child in child_verdicts):
        return "blocked"
    if any(child.verdict.status == "retry" for child in child_verdicts):
        return "needs_retry"
    required_children = tuple(child for child in child_verdicts if child.required)
    if required_children and all(child.verdict.status == "accepted" for child in required_children):
        if all(child.verdict.status == "accepted" for child in child_verdicts):
            return "complete"
        return "partial"
    return "partial"


def _retry_schedule_refs(
    loop_id: str,
    child_verdicts: tuple[MetaInspectedChildVerdict, ...],
) -> tuple[str, ...]:
    return tuple(
        _retry_ref(loop_id, child)
        for child in child_verdicts
        if child.verdict.status == "retry" and child.verdict.retry_budget_remaining > 0
    )


def _retry_ref(loop_id: str, child: MetaInspectedChildVerdict) -> str:
    reason = "-".join(child.verdict.reason_codes)
    return _validate_public_ref(
        f"retry:{loop_id}:{child.task_id}:attempt-{child.attempt + 1}:{reason}",
        "retryScheduleRefs",
    )


def _exhausted_retry_reasons(
    child_verdicts: tuple[MetaInspectedChildVerdict, ...],
) -> tuple[str, ...]:
    return tuple(
        _validate_public_ref(
            f"exhausted:{child.task_id}:{':'.join(child.verdict.reason_codes)}",
            "exhaustedRetryReasons",
        )
        for child in child_verdicts
        if "retry_budget_exhausted" in child.verdict.reason_codes
    )


def _accepted_refs_for_assembly(
    child_verdicts: tuple[MetaInspectedChildVerdict, ...],
) -> tuple[str, ...]:
    refs: list[str] = []
    for child in child_verdicts:
        if child.verdict.status == "accepted":
            refs.extend(child.verdict.accepted_evidence_refs)
    return _validate_ref_tuple(refs, "acceptedChildEvidenceRefsForAssembly")


__all__ = [
    "MetaInspectedChildVerdict",
    "MetaInspectionAggregateStatus",
    "MetaInspectionLoopResult",
    "inspect_child_verdicts",
]
