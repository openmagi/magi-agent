from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.harness.general_automation.followup_refs import (
    AutomationOutputKind,
    FollowupOutputRef,
    FollowupToolContract,
    followup_tool_contracts,
)
from magi_agent.harness.general_automation.text_scrub import scrub_text as _scrub_text


OutputReferenceStatus = Literal["referenced"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


class AutomationOutputBudgetRequest(BaseModel):
    model_config = _MODEL_CONFIG

    source_kind: AutomationOutputKind = Field(alias="sourceKind")
    output_text: str = Field(alias="outputText", repr=False)
    preview_chars: int = Field(default=4000, alias="previewChars", ge=1, le=64_000)

    @field_validator("output_text")
    @classmethod
    def _validate_output_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("outputText must not contain NUL bytes")
        return value

    @field_serializer("output_text")
    def _serialize_output_text(self, value: str) -> str:
        return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


class OutputReferenceAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="adkArtifactServiceAttached",
    )
    artifact_written: Literal[False] = Field(default=False, alias="artifactWritten")
    channel_delivery_performed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryPerformed",
    )
    completed_work_claim_allowed: Literal[False] = Field(
        default=False,
        alias="completedWorkClaimAllowed",
    )
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "adk_artifact_service_attached",
        "artifact_written",
        "channel_delivery_performed",
        "completed_work_claim_allowed",
        "user_visible_output_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class AutomationOutputReference(BaseModel):
    model_config = _MODEL_CONFIG

    status: OutputReferenceStatus = "referenced"
    source_kind: AutomationOutputKind = Field(alias="sourceKind")
    preview: str
    full_output_ref: str = Field(alias="fullOutputRef")
    digest: str
    byte_count: int = Field(alias="byteCount", ge=0)
    truncated: bool
    followup_ref: FollowupOutputRef = Field(alias="followupRef")
    followup_tools: tuple[FollowupToolContract, ...] = Field(alias="followupTools")
    authority_flags: OutputReferenceAuthorityFlags = Field(
        default_factory=OutputReferenceAuthorityFlags,
        alias="authorityFlags",
    )
    delivery_claim_allowed: Literal[False] = Field(
        default=False,
        alias="deliveryClaimAllowed",
    )
    completed_work_claim_allowed: Literal[False] = Field(
        default=False,
        alias="completedWorkClaimAllowed",
    )

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be sha256")
        return value

    @model_validator(mode="after")
    def _validate_ref_matches_digest(self) -> Self:
        if not self.full_output_ref.endswith(self.digest):
            raise ValueError("fullOutputRef must include digest")
        if self.followup_ref.digest != self.digest:
            raise ValueError("followup ref digest mismatch")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "sourceKind": self.source_kind,
            "preview": self.preview,
            "fullOutputRef": self.full_output_ref,
            "digest": self.digest,
            "byteCount": self.byte_count,
            "truncated": self.truncated,
            "followupTools": [tool.public_projection() for tool in self.followup_tools],
            "adkBoundary": {
                "artifactService": "ArtifactService",
                "artifactRef": self.full_output_ref,
            },
            "authorityFlags": self.authority_flags.model_dump(by_alias=True, mode="json"),
            "deliveryClaimAllowed": self.delivery_claim_allowed,
            "completedWorkClaimAllowed": self.completed_work_claim_allowed,
        }


def apply_output_budget_policy(
    request: AutomationOutputBudgetRequest,
) -> AutomationOutputReference:
    raw_bytes = request.output_text.encode("utf-8")
    digest = "sha256:" + sha256(raw_bytes).hexdigest()
    safe_output = _safe_text(request.output_text)
    truncated = len(safe_output) > request.preview_chars
    preview = safe_output[: request.preview_chars] if truncated else safe_output
    full_output_ref = f"artifact:general-automation-output:{digest}"
    followup_ref = FollowupOutputRef(
        sourceKind=request.source_kind,
        fullOutputRef=full_output_ref,
        digest=digest,
        byteCount=len(raw_bytes),
    )

    return AutomationOutputReference(
        sourceKind=request.source_kind,
        preview=preview,
        fullOutputRef=full_output_ref,
        digest=digest,
        byteCount=len(raw_bytes),
        truncated=truncated,
        followupRef=followup_ref,
        followupTools=followup_tool_contracts(followup_ref),
    )


def _safe_text(value: str) -> str:
    return _scrub_text(value).strip()


__all__ = [
    "AutomationOutputBudgetRequest",
    "AutomationOutputReference",
    "OutputReferenceAuthorityFlags",
    "apply_output_budget_policy",
]
