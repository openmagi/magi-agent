from __future__ import annotations

from hashlib import sha256
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.harness.general_automation.path_policy import (
    AdkControlKind,
    PathAccessDecision,
    PathOperationClass,
)


ExternalDirectoryReceiptStatus = Literal["approval_required"]
BlockedResultStatus = Literal["blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_APPROVAL_REF_RE = re.compile(r"^approval:external-directory:sha256:[a-f0-9]{64}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,96}$")


class ExternalDirectoryApprovalReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    approval_ref: str = Field(alias="approvalRef")
    status: ExternalDirectoryReceiptStatus = "approval_required"
    operation_class: PathOperationClass = Field(alias="operationClass")
    canonical_path_prefix: str = Field(alias="canonicalPathPrefix")
    canonical_path_prefix_digest: str = Field(alias="canonicalPathPrefixDigest")
    path_digest: str = Field(alias="pathDigest")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    adk_control_kind: AdkControlKind = Field(
        default="tool_callback_control_request",
        alias="adkControlKind",
    )

    @field_validator("approval_ref")
    @classmethod
    def _validate_approval_ref(cls, value: str) -> str:
        if not _APPROVAL_REF_RE.fullmatch(value):
            raise ValueError("approvalRef must be an external-directory sha256 approval ref")
        return value

    @field_validator("canonical_path_prefix")
    @classmethod
    def _validate_prefix(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("canonicalPathPrefix must be absolute")
        return value

    @field_validator("canonical_path_prefix_digest", "path_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest values must be sha256 digests")
        return value

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _REASON_CODE_RE.fullmatch(item):
                raise ValueError("reason codes must be safe public identifiers")
        return value

    @model_validator(mode="after")
    def _validate_approval_state(self) -> "ExternalDirectoryApprovalReceipt":
        if "external_directory_approval_required" not in self.reason_codes:
            raise ValueError("external directory receipt requires approval reason code")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "approvalRef": self.approval_ref,
            "status": self.status,
            "operationClass": self.operation_class,
            "canonicalPathPrefixDigest": self.canonical_path_prefix_digest,
            "pathDigest": self.path_digest,
            "reasonCodes": self.reason_codes,
            "adkControlKind": self.adk_control_kind,
        }


class ExternalDirectoryBlockedResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: BlockedResultStatus = "blocked"
    error_code: Literal["external_directory_access_denied"] = Field(
        default="external_directory_access_denied",
        alias="errorCode",
    )
    model_visible: Literal[True] = Field(default=True, alias="modelVisible")
    approval_required: Literal[False] = Field(default=False, alias="approvalRequired")
    operation_class: PathOperationClass = Field(alias="operationClass")
    path_digest: str = Field(alias="pathDigest")
    canonical_path_prefix_digest: str = Field(alias="canonicalPathPrefixDigest")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    adk_control_kind: AdkControlKind = Field(
        default="tool_callback_control_request",
        alias="adkControlKind",
    )

    @field_validator("path_digest", "canonical_path_prefix_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest values must be sha256 digests")
        return value

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _REASON_CODE_RE.fullmatch(item):
                raise ValueError("reason codes must be safe public identifiers")
        return value


def build_external_directory_approval_receipt(
    decision: PathAccessDecision,
    *,
    approvalRef: str,
) -> ExternalDirectoryApprovalReceipt:
    if decision.status != "external_directory" or not decision.approval_required:
        raise ValueError("approval receipt requires an external directory decision")

    return ExternalDirectoryApprovalReceipt(
        approvalRef=approvalRef,
        operationClass=decision.operation_class,
        canonicalPathPrefix=decision.canonical_path_prefix,
        canonicalPathPrefixDigest=_digest(decision.canonical_path_prefix),
        pathDigest=decision.path_digest,
        reasonCodes=decision.reason_codes,
        adkControlKind=decision.adk_control_kind,
    )


def project_external_directory_denial(
    receipt: ExternalDirectoryApprovalReceipt,
    *,
    denialReason: str,
) -> ExternalDirectoryBlockedResult:
    _validate_reason_code(denialReason)
    return ExternalDirectoryBlockedResult(
        operationClass=receipt.operation_class,
        pathDigest=receipt.path_digest,
        canonicalPathPrefixDigest=receipt.canonical_path_prefix_digest,
        reasonCodes=(
            "external_directory_access_denied",
            denialReason,
        ),
        adkControlKind=receipt.adk_control_kind,
    )


def _validate_reason_code(value: str) -> str:
    if not _REASON_CODE_RE.fullmatch(value):
        raise ValueError("denialReason must be a safe public reason code")
    return value


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "ExternalDirectoryApprovalReceipt",
    "ExternalDirectoryBlockedResult",
    "build_external_directory_approval_receipt",
    "project_external_directory_denial",
]
