from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from magi_agent.meta_orchestration.inspection_loop import MetaInspectionLoopResult
from magi_agent.meta_orchestration.task_plan import (
    _copy_update_alias,
    _validate_public_ref,
    _validate_public_text,
    _validate_ref_tuple,
)
_FINAL_ASSEMBLY_INIT_TOKEN = object()


MetaFinalProjectionMode = Literal["blocked", "partial", "ready_for_projection"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class _MetaFinalAssemblyModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        raise TypeError("model_construct is disabled for meta final assembly contracts")

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


class MetaFinalAssemblyPlan(_MetaFinalAssemblyModel):
    assembly_id: str = Field(alias="assemblyId")
    accepted_child_evidence_refs: tuple[str, ...] = Field(alias="acceptedChildEvidenceRefs")
    excluded_child_refs: tuple[str, ...] = Field(alias="excludedChildRefs")
    required_verifier_refs: tuple[str, ...] = Field(alias="requiredVerifierRefs")
    final_output_digest: str = Field(alias="finalOutputDigest")
    projection_mode: MetaFinalProjectionMode = Field(alias="projectionMode")
    raw_child_transcript_used: Literal[False] = Field(
        default=False,
        alias="rawChildTranscriptUsed",
    )
    private_notes: tuple[str, ...] = Field(default=(), alias="privateNotes", exclude=True)
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    _canonical_payload_digest: str = PrivateAttr(default="")

    def __init__(self, **data: Any) -> None:
        token = data.pop("_inspection_assembly_token", None)
        if token is not _FINAL_ASSEMBLY_INIT_TOKEN:
            raise TypeError("final assembly plans must be produced from inspection results")
        super().__init__(**data)
        object.__setattr__(self, "_canonical_payload_digest", _plan_payload_digest(self))

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        if update:
            raise TypeError("final assembly plans cannot be updated after assembly")
        return self

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: str | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        _ = obj, strict, extra, from_attributes, context, by_alias, by_name
        raise TypeError("final assembly plans must be produced from inspection results")

    @field_validator("assembly_id")
    @classmethod
    def _validate_assembly_id(cls, value: str) -> str:
        return _validate_public_ref(value, "assemblyId")

    @field_validator("final_output_digest")
    @classmethod
    def _validate_final_output_digest(cls, value: str) -> str:
        clean = _validate_public_ref(value, "finalOutputDigest")
        if not clean.startswith("sha256:") or len(clean) != len("sha256:") + 64:
            raise ValueError("finalOutputDigest must be a sha256 digest ref")
        return clean

    @field_validator(
        "accepted_child_evidence_refs",
        "excluded_child_refs",
        "required_verifier_refs",
    )
    @classmethod
    def _validate_refs(cls, value: Sequence[str], info: Any) -> tuple[str, ...]:
        return _validate_ref_tuple(value, info.field_name)

    @field_validator("raw_child_transcript_used", mode="before")
    @classmethod
    def _validate_raw_transcript_unused(cls, value: object) -> object:
        if value is not False:
            raise ValueError("rawChildTranscriptUsed must remain false")
        return value

    @field_validator("private_notes")
    @classmethod
    def _validate_private_notes(cls, value: Sequence[str]) -> tuple[str, ...]:
        notes = tuple(value)
        for item in notes:
            _validate_public_text(item, "privateNotes")
        return notes

    @field_validator("default_off", mode="before")
    @classmethod
    def _validate_default_off(cls, value: object) -> object:
        if value is not True:
            raise ValueError("defaultOff must remain true")
        return value

    @classmethod
    def _from_inspection(
        cls,
        *,
        assembly_id: str,
        inspection: MetaInspectionLoopResult,
        required_verifier_refs: Sequence[str],
        satisfied_verifier_refs: Sequence[str] = (),
        private_notes: Sequence[str] = (),
    ) -> Self:
        parsed_inspection = inspect_canonical_result(inspection)
        accepted_refs = _accepted_refs_from_child_verdicts(parsed_inspection)
        excluded_child_refs = _excluded_child_refs(parsed_inspection)
        required_refs = _validate_ref_tuple(required_verifier_refs, "requiredVerifierRefs")
        satisfied_refs = frozenset(_validate_ref_tuple(satisfied_verifier_refs, "satisfiedVerifierRefs"))
        projection_mode = _projection_mode(
            parsed_inspection,
            required_verifier_refs=required_refs,
            satisfied_verifier_refs=satisfied_refs,
        )
        if projection_mode == "ready_for_projection" and not accepted_refs:
            raise ValueError("ready projection requires accepted child evidence refs")
        return cls(
            _inspection_assembly_token=_FINAL_ASSEMBLY_INIT_TOKEN,
            assemblyId=assembly_id,
            acceptedChildEvidenceRefs=accepted_refs,
            excludedChildRefs=excluded_child_refs,
            requiredVerifierRefs=required_refs,
            finalOutputDigest=_stable_final_output_digest(accepted_refs, required_refs),
            projectionMode=projection_mode,
            rawChildTranscriptUsed=False,
            privateNotes=tuple(private_notes),
            defaultOff=True,
        )

    def public_projection(self) -> dict[str, object]:
        if self._canonical_payload_digest != _plan_payload_digest(self):
            raise ValueError("final assembly plan was mutated after assembly")
        parsed = self
        return {
            "assemblyId": parsed.assembly_id,
            "acceptedChildEvidenceRefCount": len(parsed.accepted_child_evidence_refs),
            "excludedChildRefs": parsed.excluded_child_refs,
            "requiredVerifierRefs": parsed.required_verifier_refs,
            "finalOutputDigest": parsed.final_output_digest,
            "projectionMode": parsed.projection_mode,
            "rawChildTranscriptUsed": parsed.raw_child_transcript_used,
            "defaultOff": parsed.default_off,
        }


def assemble_final_output_from_inspection(
    assembly_id: str,
    inspection: MetaInspectionLoopResult,
    *,
    required_verifier_refs: Sequence[str],
    satisfied_verifier_refs: Sequence[str] = (),
    private_notes: Sequence[str] = (),
) -> MetaFinalAssemblyPlan:
    return MetaFinalAssemblyPlan._from_inspection(
        assembly_id=assembly_id,
        inspection=inspection,
        required_verifier_refs=required_verifier_refs,
        satisfied_verifier_refs=satisfied_verifier_refs,
        private_notes=private_notes,
    )


def inspect_canonical_result(inspection: MetaInspectionLoopResult) -> MetaInspectionLoopResult:
    if not isinstance(inspection, MetaInspectionLoopResult):
        raise ValueError("final assembly requires a MetaInspectionLoopResult")
    return MetaInspectionLoopResult.model_validate(
        {
            "loopId": inspection.loop_id,
            "childVerdicts": inspection.child_verdicts,
            "aggregateStatus": inspection.aggregate_status,
            "retryScheduleRefs": inspection.retry_schedule_refs,
            "exhaustedRetryReasons": inspection.exhausted_retry_reasons,
            "acceptedChildEvidenceRefsForAssembly": (
                inspection.accepted_child_evidence_refs_for_assembly
            ),
            "parentExecutedChildTools": inspection.parent_executed_child_tools,
            "defaultOff": inspection.default_off,
        }
    )


def _excluded_child_refs(inspection: MetaInspectionLoopResult) -> tuple[str, ...]:
    refs = tuple(
        child.task_id
        for child in inspection.child_verdicts
        if child.verdict.status in {"blocked", "rejected"}
    )
    return _validate_ref_tuple(refs, "excludedChildRefs")


def _accepted_refs_from_child_verdicts(inspection: MetaInspectionLoopResult) -> tuple[str, ...]:
    refs: list[str] = []
    for child in inspection.child_verdicts:
        if child.verdict.status == "accepted":
            refs.extend(child.verdict.accepted_evidence_refs)
    return _validate_ref_tuple(refs, "acceptedChildEvidenceRefs")


def _projection_mode(
    inspection: MetaInspectionLoopResult,
    *,
    required_verifier_refs: tuple[str, ...],
    satisfied_verifier_refs: frozenset[str],
) -> MetaFinalProjectionMode:
    if inspection.aggregate_status == "blocked":
        return "blocked"
    if any(ref not in satisfied_verifier_refs for ref in required_verifier_refs):
        return "blocked"
    if inspection.aggregate_status == "partial":
        return "partial"
    if inspection.aggregate_status == "complete":
        return "ready_for_projection"
    return "blocked"


def _stable_final_output_digest(
    accepted_refs: tuple[str, ...],
    required_verifier_refs: tuple[str, ...],
) -> str:
    payload = json.dumps(
        {
            "acceptedChildEvidenceRefs": accepted_refs,
            "requiredVerifierRefs": required_verifier_refs,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _plan_payload_digest(plan: MetaFinalAssemblyPlan) -> str:
    payload = json.dumps(
        {
            "assemblyId": plan.assembly_id,
            "acceptedChildEvidenceRefs": plan.accepted_child_evidence_refs,
            "excludedChildRefs": plan.excluded_child_refs,
            "requiredVerifierRefs": plan.required_verifier_refs,
            "finalOutputDigest": plan.final_output_digest,
            "projectionMode": plan.projection_mode,
            "rawChildTranscriptUsed": plan.raw_child_transcript_used,
            "defaultOff": plan.default_off,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "MetaFinalAssemblyPlan",
    "MetaFinalProjectionMode",
    "assemble_final_output_from_inspection",
]
