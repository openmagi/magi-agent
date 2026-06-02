from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


GeneralAutomationEventType = Literal[
    "callback.before_tool",
    "callback.after_tool",
    "control.approval_required",
    "control.blocked",
    "control.resumed",
    "artifact.recorded",
    "source.recorded",
    "verifier.completed",
]
GeneralAutomationCallbackName = Literal[
    "before_tool_callback",
    "after_tool_callback",
    "before_model_callback",
    "after_model_callback",
    "before_agent_callback",
    "after_agent_callback",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,220}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class GeneralAutomationEventAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    callback_attached: Literal[False] = Field(default=False, alias="callbackAttached")
    tool_dispatch_enabled: Literal[False] = Field(
        default=False,
        alias="toolDispatchEnabled",
    )
    artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="artifactServiceAttached",
    )
    source_provider_called: Literal[False] = Field(
        default=False,
        alias="sourceProviderCalled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        return type(self)()


class GeneralAutomationEventProjectionRequest(BaseModel):
    model_config = _MODEL_CONFIG

    event_type: GeneralAutomationEventType = Field(alias="eventType")
    callback_name: GeneralAutomationCallbackName = Field(alias="callbackName")
    control_ref: str = Field(alias="controlRef")
    subject_ref: str = Field(alias="subjectRef")
    observed_at: str = Field(alias="observedAt")
    payload_digest: str = Field(alias="payloadDigest")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("control_ref", "subject_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("evidence_refs")
    @classmethod
    def _validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)

    @field_validator("payload_digest")
    @classmethod
    def _validate_payload_digest(cls, value: str) -> str:
        return _safe_digest(value)

    @field_validator("observed_at")
    @classmethod
    def _validate_observed_at(cls, value: str) -> str:
        if not _TIMESTAMP_RE.fullmatch(value):
            raise ValueError("observedAt must be UTC second timestamp")
        return value


class GeneralAutomationEventProjection(BaseModel):
    model_config = _MODEL_CONFIG

    event_type: GeneralAutomationEventType = Field(alias="eventType")
    event_ref: str = Field(alias="eventRef")
    callback_name: GeneralAutomationCallbackName = Field(alias="callbackName")
    control_ref: str = Field(alias="controlRef")
    subject_ref: str = Field(alias="subjectRef")
    observed_at: str = Field(alias="observedAt")
    payload_digest: str = Field(alias="payloadDigest")
    metadata_digest: str = Field(alias="metadataDigest")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    authority_flags: GeneralAutomationEventAuthorityFlags = Field(
        default_factory=GeneralAutomationEventAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("event_ref", "control_ref", "subject_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("evidence_refs")
    @classmethod
    def _validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)

    @field_validator("payload_digest", "metadata_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value)

    @field_validator("observed_at")
    @classmethod
    def _validate_observed_at(cls, value: str) -> str:
        if not _TIMESTAMP_RE.fullmatch(value):
            raise ValueError("observedAt must be UTC second timestamp")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "eventType": self.event_type,
            "eventRef": self.event_ref,
            "callbackName": self.callback_name,
            "controlRef": self.control_ref,
            "subjectRef": self.subject_ref,
            "observedAt": self.observed_at,
            "payloadDigest": self.payload_digest,
            "metadataDigest": self.metadata_digest,
            "evidenceRefs": self.evidence_refs,
            "adkBoundary": {
                "callbackEventVocabulary": "ADK callback",
                "eventProjectionOnly": True,
            },
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


def build_general_automation_event_projection(
    request: GeneralAutomationEventProjectionRequest,
) -> GeneralAutomationEventProjection:
    metadata_digest = _digest(request.metadata)
    return GeneralAutomationEventProjection(
        eventType=request.event_type,
        eventRef=_event_ref(request, metadata_digest),
        callbackName=request.callback_name,
        controlRef=request.control_ref,
        subjectRef=request.subject_ref,
        observedAt=request.observed_at,
        payloadDigest=request.payload_digest,
        metadataDigest=metadata_digest,
        evidenceRefs=request.evidence_refs,
    )


def _event_ref(
    request: GeneralAutomationEventProjectionRequest,
    metadata_digest: str,
) -> str:
    return "event:general-automation:" + _digest(
        {
            "eventType": request.event_type,
            "callbackName": request.callback_name,
            "controlRef": request.control_ref,
            "subjectRef": request.subject_ref,
            "observedAt": request.observed_at,
            "payloadDigest": request.payload_digest,
            "metadataDigest": metadata_digest,
            "evidenceRefs": request.evidence_refs,
        }
    )


def _safe_ref(value: str) -> str:
    if not value or not _REF_RE.fullmatch(value):
        raise ValueError("ref must be a safe public reference")
    return value


def _safe_digest(value: str) -> str:
    if not _DIGEST_RE.fullmatch(value):
        raise ValueError("digest must be sha256")
    return value


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


__all__ = [
    "GeneralAutomationEventProjection",
    "GeneralAutomationEventProjectionRequest",
    "build_general_automation_event_projection",
]
