from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


BrowserArtifactKind = Literal[
    "screenshot",
    "dom_summary",
    "action_receipt",
    "download",
]
BrowserSideEffect = Literal["external_form_submission", "channel_delivery"]
BrowserSideEffectStatus = Literal["approval_required", "approved", "recorded"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")


class BrowserEvidenceAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    browser_worker_session_started: Literal[False] = Field(
        default=False,
        alias="browserWorkerSessionStarted",
    )
    browser_action_performed: Literal[False] = Field(
        default=False,
        alias="browserActionPerformed",
    )
    external_form_submitted: Literal[False] = Field(
        default=False,
        alias="externalFormSubmitted",
    )
    channel_delivery_performed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryPerformed",
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


class BrowserArtifactEvidence(BaseModel):
    model_config = _MODEL_CONFIG

    kind: BrowserArtifactKind
    artifact_ref: str = Field(alias="artifactRef")
    content_digest: str = Field(alias="contentDigest")
    source_ref: str = Field(alias="sourceRef")
    label_digest: str = Field(alias="labelDigest")
    authority_flags: BrowserEvidenceAuthorityFlags = Field(
        default_factory=BrowserEvidenceAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("artifact_ref", "source_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("content_digest", "label_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "artifactRef": self.artifact_ref,
            "contentDigest": self.content_digest,
            "sourceRef": self.source_ref,
            "labelDigest": self.label_digest,
            "adkBoundary": {
                "artifactService": "ArtifactService",
                "artifactRefsOnly": True,
            },
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


class BrowserSideEffectDecision(BaseModel):
    model_config = _MODEL_CONFIG

    side_effect: BrowserSideEffect = Field(alias="sideEffect")
    status: BrowserSideEffectStatus
    artifact_ref: str = Field(alias="artifactRef")
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    channel_delivery_receipt_ref: str | None = Field(
        default=None,
        alias="channelDeliveryReceiptRef",
    )
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    authority_flags: BrowserEvidenceAuthorityFlags = Field(
        default_factory=BrowserEvidenceAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("artifact_ref", "approval_ref", "channel_delivery_receipt_ref")
    @classmethod
    def _validate_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "sideEffect": self.side_effect,
            "status": self.status,
            "artifactRef": self.artifact_ref,
            "approvalRef": self.approval_ref,
            "channelDeliveryReceiptRef": self.channel_delivery_receipt_ref,
            "reasonCodes": self.reason_codes,
            "adkBoundary": {
                "artifactService": "ArtifactService",
                "artifactRef": self.artifact_ref,
            },
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


def build_browser_artifact_evidence(
    *,
    kind: BrowserArtifactKind,
    contentDigest: str,
    sourceRef: str,
    label: str = "",
) -> BrowserArtifactEvidence:
    content_digest = _safe_digest(contentDigest)
    return BrowserArtifactEvidence(
        kind=kind,
        artifactRef=_artifact_ref(kind, content_digest),
        contentDigest=content_digest,
        sourceRef=sourceRef,
        labelDigest=_digest(label),
    )


def evaluate_browser_side_effect(
    *,
    sideEffect: BrowserSideEffect,
    artifactRef: str,
    approvalRef: str | None = None,
    channelDeliveryReceiptRef: str | None = None,
) -> BrowserSideEffectDecision:
    if sideEffect == "external_form_submission":
        if approvalRef is None:
            return BrowserSideEffectDecision(
                sideEffect=sideEffect,
                status="approval_required",
                artifactRef=artifactRef,
                reasonCodes=("external_form_submission_approval_required",),
            )
        return BrowserSideEffectDecision(
            sideEffect=sideEffect,
            status="approved",
            artifactRef=artifactRef,
            approvalRef=approvalRef,
            reasonCodes=("external_form_submission_approval_recorded",),
        )

    if channelDeliveryReceiptRef is None:
        return BrowserSideEffectDecision(
            sideEffect=sideEffect,
            status="approval_required",
            artifactRef=artifactRef,
            reasonCodes=("channel_delivery_receipt_required",),
        )
    return BrowserSideEffectDecision(
        sideEffect=sideEffect,
        status="recorded",
        artifactRef=artifactRef,
        channelDeliveryReceiptRef=channelDeliveryReceiptRef,
        reasonCodes=("channel_delivery_receipt_recorded",),
    )


def _artifact_ref(kind: BrowserArtifactKind, content_digest: str) -> str:
    return f"artifact:browser-{kind.replace('_', '-')}:{content_digest}"


def _safe_digest(value: str) -> str:
    if not _DIGEST_RE.fullmatch(value):
        raise ValueError("digest must be sha256")
    return value


def _safe_ref(value: str) -> str:
    if not value or not _REF_RE.fullmatch(value):
        raise ValueError("ref must be a safe public reference")
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
    "BrowserArtifactEvidence",
    "BrowserEvidenceAuthorityFlags",
    "BrowserSideEffectDecision",
    "build_browser_artifact_evidence",
    "evaluate_browser_side_effect",
]
