from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


GeneralAutomationControlType = Literal[
    "approval_required",
    "blocked",
    "resume_ready",
    "artifact_recorded",
    "source_recorded",
    "verifier_state",
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
_REASON_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,120}$")


class GeneralAutomationControlAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    callback_attached: Literal[False] = Field(default=False, alias="callbackAttached")
    tool_dispatch_enabled: Literal[False] = Field(
        default=False,
        alias="toolDispatchEnabled",
    )
    approval_bypassed: Literal[False] = Field(default=False, alias="approvalBypassed")
    artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="artifactServiceAttached",
    )
    source_provider_called: Literal[False] = Field(
        default=False,
        alias="sourceProviderCalled",
    )
    verifier_executed: Literal[False] = Field(default=False, alias="verifierExecuted")
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


class GeneralAutomationControlProjectionRequest(BaseModel):
    model_config = _MODEL_CONFIG

    control_type: GeneralAutomationControlType = Field(alias="controlType")
    subject_ref: str = Field(alias="subjectRef")
    policy_ref: str = Field(alias="policyRef")
    payload_digest: str = Field(alias="payloadDigest")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    resume_ref: str | None = Field(default=None, alias="resumeRef")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("subject_ref", "policy_ref", "approval_ref", "resume_ref")
    @classmethod
    def _validate_optional_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _safe_ref(value)

    @field_validator("evidence_refs")
    @classmethod
    def _validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)

    @field_validator("payload_digest")
    @classmethod
    def _validate_payload_digest(cls, value: str) -> str:
        return _safe_digest(value)

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _REASON_RE.fullmatch(item):
                raise ValueError("reason codes must be public identifiers")
        return value


class GeneralAutomationControlProjection(BaseModel):
    model_config = _MODEL_CONFIG

    control_type: GeneralAutomationControlType = Field(alias="controlType")
    control_ref: str = Field(alias="controlRef")
    subject_ref: str = Field(alias="subjectRef")
    policy_ref: str = Field(alias="policyRef")
    payload_digest: str = Field(alias="payloadDigest")
    metadata_digest: str = Field(alias="metadataDigest")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    resume_ref: str | None = Field(default=None, alias="resumeRef")
    execution_allowed: Literal[False] = Field(default=False, alias="executionAllowed")
    authority_flags: GeneralAutomationControlAuthorityFlags = Field(
        default_factory=GeneralAutomationControlAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("control_ref", "subject_ref", "policy_ref", "approval_ref", "resume_ref")
    @classmethod
    def _validate_optional_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _safe_ref(value)

    @field_validator("evidence_refs")
    @classmethod
    def _validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)

    @field_validator("payload_digest", "metadata_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "controlType": self.control_type,
            "controlRef": self.control_ref,
            "subjectRef": self.subject_ref,
            "policyRef": self.policy_ref,
            "payloadDigest": self.payload_digest,
            "metadataDigest": self.metadata_digest,
            "evidenceRefs": self.evidence_refs,
            "reasonCodes": self.reason_codes,
            "approvalRef": self.approval_ref,
            "resumeRef": self.resume_ref,
            "executionAllowed": self.execution_allowed,
            "adkBoundary": {
                "callbackEventVocabulary": "ADK callback",
                "controlProjectionOnly": True,
            },
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


def build_general_automation_control_projection(
    request: GeneralAutomationControlProjectionRequest,
) -> GeneralAutomationControlProjection:
    metadata_digest = _digest(request.metadata)
    return GeneralAutomationControlProjection(
        controlType=request.control_type,
        controlRef=_control_ref(request, metadata_digest),
        subjectRef=request.subject_ref,
        policyRef=request.policy_ref,
        payloadDigest=request.payload_digest,
        metadataDigest=metadata_digest,
        evidenceRefs=request.evidence_refs,
        reasonCodes=request.reason_codes,
        approvalRef=request.approval_ref,
        resumeRef=request.resume_ref,
    )


def _control_ref(
    request: GeneralAutomationControlProjectionRequest,
    metadata_digest: str,
) -> str:
    return "control:general-automation:" + _digest(
        {
            "controlType": request.control_type,
            "subjectRef": request.subject_ref,
            "policyRef": request.policy_ref,
            "payloadDigest": request.payload_digest,
            "metadataDigest": metadata_digest,
            "evidenceRefs": request.evidence_refs,
            "reasonCodes": request.reason_codes,
            "approvalRef": request.approval_ref,
            "resumeRef": request.resume_ref,
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
    "GeneralAutomationControlProjection",
    "GeneralAutomationControlProjectionRequest",
    "build_general_automation_control_projection",
]
