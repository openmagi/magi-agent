from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


ToolReceiptStatus = Literal["success", "error", "blocked", "timeout"]
RedactionStatus = Literal["redacted", "no_redaction_needed", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:!-]{0,180}$")
_JWT_LIKE_RE = re.compile(
    r"(?:^|[^A-Za-z0-9_-])[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
    r"[A-Za-z0-9_-]{10,}(?:$|[^A-Za-z0-9_-])"
)
_REDACTED_DIGEST = "sha256:" + "0" * 64
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"authorization\s*:|set-cookie\s*:|\bcookie\b|\bbearer\s+[A-Za-z0-9._~+/=-]{6,}|"
    r"\bsid=[A-Za-z0-9._-]+|\bsk-[A-Za-z0-9._-]{6,}|gh[opusr]_[A-Za-z0-9_]{6,}|"
    r"github_pat_[A-Za-z0-9_]+|xox[a-z]-[A-Za-z0-9._-]+|AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|api[_-]?key\s*[:=]|password\s*[:=]|secret\s*[:=]|"
    r"token\s*[:=]|\b(?:auth|cookie|credential|credentials?|password|private|secret|"
    r"session|token)s?\b|private[_-]?key|"
    r"/Users(?:/|\b)|/home(?:/|\b)|/workspace(?:/|\b)|/data/bots(?:/|\b)|"
    r"/var/lib/kubelet(?:/|\b)|pvc-[A-Za-z0-9-]+|"
    r"raw[_ -]?(?:tool|child|prompt|transcript|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)


class ReceiptAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    read_only: Literal[True] = Field(default=True, alias="readOnly")
    mutation_allowed: Literal[False] = Field(default=False, alias="mutationAllowed")
    channel_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()


class ToolExecutionReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    receipt_id: str = Field(alias="receiptId")
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    tool_version: str = Field(alias="toolVersion")
    input_digest: str = Field(alias="inputDigest")
    output_digest: str = Field(alias="outputDigest")
    status: ToolReceiptStatus
    started_at: str = Field(alias="startedAt")
    ended_at: str = Field(alias="endedAt")
    authority_flags: ReceiptAuthorityFlags = Field(alias="authorityFlags")
    policy_decision_id: str = Field(alias="policyDecisionId")
    redaction_status: RedactionStatus = Field(alias="redactionStatus")
    source_ref: str | None = Field(default=None, alias="sourceRef")
    artifact_ref: str | None = Field(default=None, alias="artifactRef")

    @field_validator("input_digest", "output_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value)

    @field_validator(
        "receipt_id",
        "tool_call_id",
        "tool_name",
        "tool_version",
        "policy_decision_id",
        "source_ref",
        "artifact_ref",
    )
    @classmethod
    def _validate_ref(cls, value: str | None) -> str | None:
        return None if value is None else _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        payload: dict[str, object | None] = {
            "receiptId": _public_text(self.receipt_id),
            "toolCallId": _public_text(self.tool_call_id),
            "toolName": _public_text(self.tool_name),
            "toolVersion": _public_text(self.tool_version),
            "inputDigest": _public_digest(self.input_digest),
            "outputDigest": _public_digest(self.output_digest),
            "status": _public_text(self.status),
            "startedAt": _public_text(self.started_at),
            "endedAt": _public_text(self.ended_at),
            "authorityFlags": ReceiptAuthorityFlags().model_dump(by_alias=True),
            "policyDecisionId": _public_text(self.policy_decision_id),
            "redactionStatus": _public_text(self.redaction_status),
            "sourceRef": _public_ref(self.source_ref),
            "artifactRef": _public_ref(self.artifact_ref),
        }
        return {key: value for key, value in payload.items() if value is not None}


class SourceEvidenceReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    source_ref: str = Field(alias="sourceRef")
    opened_at: str = Field(alias="openedAt")
    content_digest: str = Field(alias="contentDigest")
    snapshot_ref: str = Field(alias="snapshotRef")
    span_ref: str = Field(alias="spanRef")
    quote_digest: str = Field(alias="quoteDigest")

    @field_validator("content_digest", "quote_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value)

    @field_validator("source_ref", "snapshot_ref", "span_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "sourceRef": _public_ref(self.source_ref) or "redacted_ref",
            "openedAt": _public_text(self.opened_at),
            "contentDigest": _public_digest(self.content_digest),
            "snapshotRef": _public_ref(self.snapshot_ref) or "redacted_ref",
            "spanRef": _public_ref(self.span_ref) or "redacted_ref",
            "quoteDigest": _public_digest(self.quote_digest),
        }


class CalculationEvidenceReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    calculation_ref: str = Field(alias="calculationRef")
    input_file_digest: str = Field(alias="inputFileDigest")
    range_ref: str = Field(alias="rangeRef")
    formula_digest: str = Field(alias="formulaDigest")
    result_digest: str = Field(alias="resultDigest")

    @field_validator("input_file_digest", "formula_digest", "result_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value)

    @field_validator("calculation_ref", "range_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "calculationRef": _public_ref(self.calculation_ref) or "redacted_ref",
            "inputFileDigest": _public_digest(self.input_file_digest),
            "rangeRef": _public_ref(self.range_ref) or "redacted_ref",
            "formulaDigest": _public_digest(self.formula_digest),
            "resultDigest": _public_digest(self.result_digest),
        }


def _safe_digest(value: str) -> str:
    text = value.strip()
    if _contains_private_text(text) or _DIGEST_RE.fullmatch(text) is None:
        raise ValueError("digest must be sha256:<64 lowercase hex> and contain no raw private data")
    return text


def _safe_ref(value: str) -> str:
    text = value.strip()
    if _contains_private_text(text) or _SAFE_REF_RE.fullmatch(text) is None:
        raise ValueError("ref must be sanitized and public-safe")
    return text


def _contains_private_text(value: str) -> bool:
    return bool(_PRIVATE_TEXT_RE.search(value) or _JWT_LIKE_RE.search(value))


def _public_digest(value: object) -> str:
    try:
        return _safe_digest(str(value))
    except ValueError:
        return _REDACTED_DIGEST


def _public_ref(value: object | None) -> str | None:
    if value is None:
        return None
    try:
        return _safe_ref(str(value))
    except ValueError:
        return "redacted_ref"


def _public_text(value: object) -> str:
    text = str(value).strip()
    if not text or _contains_private_text(text):
        return "redacted_value"
    return text[:240]


__all__ = [
    "CalculationEvidenceReceipt",
    "ReceiptAuthorityFlags",
    "RedactionStatus",
    "SourceEvidenceReceipt",
    "ToolExecutionReceipt",
    "ToolReceiptStatus",
]
