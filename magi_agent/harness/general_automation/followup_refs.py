from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


AutomationOutputKind = Literal[
    "browser_dom",
    "shell_log",
    "csv_preview",
    "pdf_text",
    "mcp_output",
]
FollowupOperation = Literal["read", "search"]
FollowupDecisionStatus = Literal["accepted", "blocked"]
AdkArtifactServiceBoundary = Literal["ArtifactService"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_OUTPUT_REF_RE = re.compile(r"^artifact:general-automation-output:sha256:[a-f0-9]{64}$")
_SAFE_QUERY_RE = re.compile(r"^[^\x00]{0,240}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,96}$")


class FollowupOutputRef(BaseModel):
    model_config = _MODEL_CONFIG

    source_kind: AutomationOutputKind = Field(alias="sourceKind")
    full_output_ref: str = Field(alias="fullOutputRef")
    digest: str
    byte_count: int = Field(alias="byteCount", ge=0)
    adk_artifact_service_boundary: AdkArtifactServiceBoundary = Field(
        default="ArtifactService",
        alias="adkArtifactServiceBoundary",
    )

    @field_validator("full_output_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        if not _OUTPUT_REF_RE.fullmatch(value):
            raise ValueError("fullOutputRef must be a general automation artifact ref")
        return value

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be sha256")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "sourceKind": self.source_kind,
            "fullOutputRef": self.full_output_ref,
            "digest": self.digest,
            "byteCount": self.byte_count,
            "adkArtifactServiceBoundary": self.adk_artifact_service_boundary,
        }


class FollowupToolContract(BaseModel):
    model_config = _MODEL_CONFIG

    tool_name: Literal["ReadOutputRef", "SearchOutputRef"] = Field(alias="toolName")
    operation: FollowupOperation
    full_output_ref: str = Field(alias="fullOutputRef")
    input_schema: dict[str, object] = Field(alias="inputSchema")
    adk_artifact_service_boundary: AdkArtifactServiceBoundary = Field(
        default="ArtifactService",
        alias="adkArtifactServiceBoundary",
    )

    @field_validator("full_output_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        if not _OUTPUT_REF_RE.fullmatch(value):
            raise ValueError("fullOutputRef must be a general automation artifact ref")
        return value

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")


class ModeledFollowupRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: FollowupOperation
    full_output_ref: str = Field(alias="fullOutputRef")
    digest: str
    query: str | None = None

    @field_validator("full_output_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        if not _OUTPUT_REF_RE.fullmatch(value):
            raise ValueError("fullOutputRef must be a general automation artifact ref")
        return value

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be sha256")
        return value

    @field_validator("query")
    @classmethod
    def _validate_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not _SAFE_QUERY_RE.fullmatch(cleaned):
            raise ValueError("query must be safe public text")
        return cleaned[:240]

    @model_validator(mode="after")
    def _validate_operation_shape(self) -> "ModeledFollowupRequest":
        if self.operation == "search" and self.query is None:
            raise ValueError("search follow-up requires query")
        return self


class FollowupRefDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: FollowupDecisionStatus
    operation: FollowupOperation
    full_output_ref: str = Field(alias="fullOutputRef")
    digest: str
    content_loaded: Literal[False] = Field(default=False, alias="contentLoaded")
    adk_artifact_service_boundary: AdkArtifactServiceBoundary = Field(
        default="ArtifactService",
        alias="adkArtifactServiceBoundary",
    )
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    @field_validator("full_output_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        if not _OUTPUT_REF_RE.fullmatch(value):
            raise ValueError("fullOutputRef must be a general automation artifact ref")
        return value

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be sha256")
        return value

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _REASON_CODE_RE.fullmatch(item):
                raise ValueError("reason codes must be safe public identifiers")
        return value

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")


def followup_tool_contracts(ref: FollowupOutputRef) -> tuple[FollowupToolContract, ...]:
    return (
        FollowupToolContract(
            toolName="ReadOutputRef",
            operation="read",
            fullOutputRef=ref.full_output_ref,
            inputSchema=_schema_for("read"),
        ),
        FollowupToolContract(
            toolName="SearchOutputRef",
            operation="search",
            fullOutputRef=ref.full_output_ref,
            inputSchema=_schema_for("search"),
        ),
    )


def validate_followup_ref_request(
    request: ModeledFollowupRequest,
    *,
    available_refs: Sequence[FollowupOutputRef],
) -> FollowupRefDecision:
    match = next(
        (
            ref
            for ref in available_refs
            if ref.full_output_ref == request.full_output_ref and ref.digest == request.digest
        ),
        None,
    )
    if match is None:
        return FollowupRefDecision(
            status="blocked",
            operation=request.operation,
            fullOutputRef=request.full_output_ref,
            digest=request.digest,
            reasonCodes=("followup_ref_not_available",),
        )
    return FollowupRefDecision(
        status="accepted",
        operation=request.operation,
        fullOutputRef=match.full_output_ref,
        digest=match.digest,
        reasonCodes=("followup_ref_available",),
    )


def _schema_for(operation: FollowupOperation) -> dict[str, object]:
    properties: dict[str, object] = {
        "fullOutputRef": {
            "type": "string",
            "pattern": _OUTPUT_REF_RE.pattern,
        },
        "digest": {
            "type": "string",
            "pattern": _DIGEST_RE.pattern,
        },
    }
    required = ["fullOutputRef", "digest"]
    if operation == "search":
        properties["query"] = {"type": "string", "maxLength": 240}
        required.append("query")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


__all__ = [
    "AdkArtifactServiceBoundary",
    "AutomationOutputKind",
    "FollowupOutputRef",
    "FollowupRefDecision",
    "FollowupToolContract",
    "ModeledFollowupRequest",
    "followup_tool_contracts",
    "validate_followup_ref_request",
]
