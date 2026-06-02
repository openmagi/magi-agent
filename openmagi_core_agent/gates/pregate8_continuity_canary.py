from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from openmagi_core_agent.runtime.context_packet import ContextContinuityAuthorityFlags


PreGate8ContinuityCanaryStatus = Literal["pass", "fail"]
PreGate8ContinuityFallbackStatus = Literal[
    "none",
    "typescript_fallback",
    "closed",
    "unavailable",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


class _PreGate8ContinuityCanaryModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class PreGate8ContinuityCanaryEvidence(_PreGate8ContinuityCanaryModel):
    schema_version: Literal["pregate8.continuityCanaryEvidence.v1"] = Field(
        default="pregate8.continuityCanaryEvidence.v1",
        alias="schemaVersion",
    )
    status: PreGate8ContinuityCanaryStatus
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    diagnostic_only: Literal[True] = Field(default=True, alias="diagnosticOnly")
    response_authority: Literal["none"] = Field(default="none", alias="responseAuthority")
    fallback_status: PreGate8ContinuityFallbackStatus = Field(alias="fallbackStatus")
    imported_event_count: int = Field(ge=0, alias="importedEventCount")
    rejected_entry_count: int = Field(ge=0, alias="rejectedEntryCount")
    compaction_applied: bool = Field(alias="compactionApplied")
    compaction_boundary_respected: bool = Field(
        default=True,
        alias="compactionBoundaryRespected",
    )
    projection_digest: str | None = Field(default=None, alias="projectionDigest")
    model_visible_digest: str | None = Field(default=None, alias="modelVisibleDigest")
    source_transcript_head_digest: str | None = Field(
        default=None,
        alias="sourceTranscriptHeadDigest",
    )
    observed_adk_session_digest: str = Field(alias="observedAdkSessionDigest")
    observed_model_visible_digest: str = Field(alias="observedModelVisibleDigest")
    antecedent_digest: str | None = Field(default=None, alias="antecedentDigest")
    current_followup_digest: str | None = Field(default=None, alias="currentFollowupDigest")
    antecedent_present_in_adk_session: bool = Field(
        default=False,
        alias="antecedentPresentInAdkSession",
    )
    antecedent_present_in_model_visible_projection: bool = Field(
        default=False,
        alias="antecedentPresentInModelVisibleProjection",
    )
    current_followup_present_in_model_visible_message: bool = Field(
        default=False,
        alias="currentFollowupPresentInModelVisibleMessage",
    )
    forbidden_payload_observed: bool = Field(
        default=False,
        alias="forbiddenPayloadObserved",
    )
    private_payload_rejected: bool = Field(default=True, alias="privatePayloadRejected")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    authority_flags: ContextContinuityAuthorityFlags = Field(
        default_factory=ContextContinuityAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_local_no_authority(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["schemaVersion"] = "pregate8.continuityCanaryEvidence.v1"
        data["localOnly"] = True
        data["diagnosticOnly"] = True
        data["responseAuthority"] = "none"
        data["authorityFlags"] = ContextContinuityAuthorityFlags().model_dump(
            by_alias=True,
            mode="python",
        )
        return data

    @field_serializer("authority_flags")
    def _serialize_authority_flags(self, _value: object) -> dict[str, bool]:
        return ContextContinuityAuthorityFlags().model_dump(by_alias=True, mode="json")

    @field_validator(
        "projection_digest",
        "model_visible_digest",
        "source_transcript_head_digest",
        "antecedent_digest",
        "current_followup_digest",
    )
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_digest(value)

    @field_validator(
        "observed_adk_session_digest",
        "observed_model_visible_digest",
    )
    @classmethod
    def _validate_required_digest(cls, value: str) -> str:
        return _validate_digest(value)


def build_pre_gate8_continuity_canary_evidence(
    runner_result: object,
    *,
    adk_session_texts: Sequence[str],
    model_visible_message: str,
    expected_antecedent: str,
    current_followup: str = "",
    forbidden_payloads: Sequence[str] = (),
    require_compaction_applied: bool = False,
    require_rejected_entries: bool = False,
    fallback_status: PreGate8ContinuityFallbackStatus = "none",
) -> PreGate8ContinuityCanaryEvidence:
    context = getattr(runner_result, "context_continuity", None)
    imported_event_count = _int_attr(context, "imported_event_count")
    rejected_entry_count = _int_attr(context, "rejected_entry_count")
    compaction_applied = _bool_attr(context, "compaction_applied")
    projection_digest = _digest_attr(context, "projection_digest")
    model_visible_digest = _digest_attr(context, "model_visible_digest")
    source_transcript_head_digest = _digest_attr(context, "source_transcript_head_digest")

    session_text = "\n".join(_string_items(adk_session_texts))
    combined_observed_text = f"{session_text}\n{model_visible_message}"
    antecedent_present_in_session = bool(expected_antecedent) and (
        expected_antecedent in session_text
    )
    antecedent_present_in_projection = bool(expected_antecedent) and (
        expected_antecedent in model_visible_message
    )
    followup_present = not current_followup or current_followup in model_visible_message
    forbidden_observed = any(
        forbidden and forbidden in combined_observed_text
        for forbidden in forbidden_payloads
    )
    compaction_boundary_respected = (
        (not require_compaction_applied or compaction_applied) and not forbidden_observed
    )
    private_payload_rejected = (
        require_rejected_entries and rejected_entry_count > 0 and not forbidden_observed
    )
    private_payload_requirement_satisfied = (
        private_payload_rejected if require_rejected_entries else not forbidden_observed
    )

    runner_completed = getattr(runner_result, "status", None) == "completed"
    digests_present = bool(
        projection_digest and model_visible_digest and source_transcript_head_digest
    )
    antecedent_present = (
        antecedent_present_in_session or antecedent_present_in_projection
    )
    passed = all(
        (
            runner_completed,
            imported_event_count > 0,
            digests_present,
            antecedent_present,
            followup_present,
            compaction_boundary_respected,
            private_payload_requirement_satisfied,
            fallback_status == "none",
        )
    )

    return PreGate8ContinuityCanaryEvidence(
        status="pass" if passed else "fail",
        fallbackStatus=fallback_status,
        importedEventCount=imported_event_count,
        rejectedEntryCount=rejected_entry_count,
        compactionApplied=compaction_applied,
        compactionBoundaryRespected=compaction_boundary_respected,
        projectionDigest=projection_digest,
        modelVisibleDigest=model_visible_digest,
        sourceTranscriptHeadDigest=source_transcript_head_digest,
        observedAdkSessionDigest=_digest_json(tuple(_string_items(adk_session_texts))),
        observedModelVisibleDigest=_digest_text(model_visible_message),
        antecedentDigest=_digest_text(expected_antecedent) if expected_antecedent else None,
        currentFollowupDigest=_digest_text(current_followup) if current_followup else None,
        antecedentPresentInAdkSession=antecedent_present_in_session,
        antecedentPresentInModelVisibleProjection=antecedent_present_in_projection,
        currentFollowupPresentInModelVisibleMessage=followup_present,
        forbiddenPayloadObserved=forbidden_observed,
        privatePayloadRejected=private_payload_rejected,
        reasonCodes=_reason_codes(
            runner_completed=runner_completed,
            antecedent_present=antecedent_present,
            followup_present=followup_present,
            require_compaction_applied=require_compaction_applied,
            compaction_boundary_respected=compaction_boundary_respected,
            forbidden_payloads=forbidden_payloads,
            forbidden_observed=forbidden_observed,
            require_rejected_entries=require_rejected_entries,
            private_payload_rejected=private_payload_requirement_satisfied,
            fallback_status=fallback_status,
            digests_present=digests_present,
        ),
    )


def _reason_codes(
    *,
    runner_completed: bool,
    antecedent_present: bool,
    followup_present: bool,
    require_compaction_applied: bool,
    compaction_boundary_respected: bool,
    forbidden_payloads: Sequence[str],
    forbidden_observed: bool,
    require_rejected_entries: bool,
    private_payload_rejected: bool,
    fallback_status: PreGate8ContinuityFallbackStatus,
    digests_present: bool,
) -> tuple[str, ...]:
    codes: list[str] = []
    codes.append("runner_completed" if runner_completed else "runner_not_completed")
    codes.append("antecedent_present" if antecedent_present else "antecedent_missing")
    codes.append("followup_present" if followup_present else "followup_missing")
    if require_compaction_applied:
        codes.append(
            "compaction_boundary_respected"
            if compaction_boundary_respected
            else "compaction_boundary_failed"
        )
    if forbidden_payloads:
        codes.append(
            "forbidden_payload_observed"
            if forbidden_observed
            else "forbidden_payload_absent"
        )
    if require_rejected_entries:
        codes.append(
            "private_payload_rejected"
            if private_payload_rejected
            else "private_payload_rejection_missing"
        )
    codes.append("fallback_none" if fallback_status == "none" else "fallback_active")
    if not digests_present:
        codes.append("continuity_digest_missing")
    return tuple(codes)


def _int_attr(value: object, name: str) -> int:
    raw = getattr(value, name, 0)
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return max(0, raw)
    return 0


def _bool_attr(value: object, name: str) -> bool:
    raw = getattr(value, name, False)
    return raw is True


def _digest_attr(value: object, name: str) -> str | None:
    raw = getattr(value, name, None)
    if isinstance(raw, str) and _DIGEST_RE.fullmatch(raw):
        return raw
    return None


def _validate_digest(value: str) -> str:
    if not _DIGEST_RE.fullmatch(value):
        raise ValueError("continuity canary evidence digests must be sha256 digests")
    return value


def _string_items(value: Sequence[str]) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str))


def _digest_json(value: object) -> str:
    return _digest_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def _digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


__all__ = [
    "PreGate8ContinuityCanaryEvidence",
    "PreGate8ContinuityCanaryStatus",
    "PreGate8ContinuityFallbackStatus",
    "build_pre_gate8_continuity_canary_evidence",
]
