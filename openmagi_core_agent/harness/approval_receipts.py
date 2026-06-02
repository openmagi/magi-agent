from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ApprovalScope = Literal[
    "single_tool_call",
    "single_turn",
    "time_window",
    "gate_activation",
    "workflow_run",
]
ApprovalSource = Literal["human_operator", "platform_policy", "org_policy", "test_harness"]
ApprovedActionKind = Literal[
    "tool_call",
    "gate_activation",
    "workflow_run",
    "model_call",
    "channel_delivery",
]

_DIGEST_PREFIX = "sha256:"
_SENSITIVE_KEYS = ("authorization", "cookie", "token", "secret", "api_key", "password", "prompt")


class ApprovalReceiptVerification(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    ok: bool
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


class ApprovalReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    approval_id: str = Field(alias="approvalId")
    approver_ref: str = Field(alias="approverRef")
    approval_source: ApprovalSource = Field(alias="approvalSource")
    approved_action_kind: ApprovedActionKind = Field(alias="approvedActionKind")
    approved_action_digest: str = Field(alias="approvedActionDigest")
    approved_scope: ApprovalScope = Field(alias="approvedScope")
    policy_decision_id: str = Field(alias="policyDecisionId")
    effective_policy_snapshot_digest: str = Field(alias="effectivePolicySnapshotDigest")
    issued_at: datetime = Field(alias="issuedAt")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    constraints: Mapping[str, object] = Field(default_factory=dict)
    approval_digest: str = Field(alias="approvalDigest")
    revokes_approval_digest: str | None = Field(default=None, alias="revokesApprovalDigest")
    revoked_by_ref: str | None = Field(default=None, alias="revokedByRef")
    revoked_at: datetime | None = Field(default=None, alias="revokedAt")
    reason_code: str | None = Field(default=None, alias="reasonCode")

    @field_validator("approval_id", "approver_ref", "policy_decision_id")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("approval identifiers must be non-empty")
        return value

    @field_validator(
        "approved_action_digest",
        "effective_policy_snapshot_digest",
        "approval_digest",
        "revokes_approval_digest",
    )
    @classmethod
    def _validate_optional_digest(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _require_digest(value, getattr(info, "field_name", "digest"))

    @field_validator("constraints")
    @classmethod
    def _validate_constraints(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=True).lower()
        if any(key in encoded for key in _SENSITIVE_KEYS):
            raise ValueError("constraints must not contain raw prompts, credentials, or secret-like fields")
        return dict(value)

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        expected = digest_approval_receipt_payload(self)
        if self.approval_digest != expected:
            raise ValueError("approvalDigest does not match receipt content")
        if self.revokes_approval_digest is not None:
            if not self.revoked_by_ref or self.revoked_at is None or not self.reason_code:
                raise ValueError("revocation receipt requires revokedByRef, revokedAt, and reasonCode")
        return self

    def revoke(
        self,
        *,
        revocationId: str,
        revokedByRef: str,
        revokedAt: datetime,
        reasonCode: str,
    ) -> ApprovalReceipt:
        payload = self.model_dump(by_alias=True, mode="json")
        payload.update(
            {
                "approvalId": revocationId,
                "revokesApprovalDigest": self.approval_digest,
                "revokedByRef": revokedByRef,
                "revokedAt": _datetime_json(revokedAt),
                "reasonCode": reasonCode,
            }
        )
        payload.pop("approvalDigest", None)
        return ApprovalReceipt(**payload, approvalDigest=_digest_json(payload))


def build_approval_receipt(
    *,
    approvalId: str,
    approverRef: str,
    approvalSource: ApprovalSource,
    approvedActionKind: ApprovedActionKind,
    approvedActionDigest: str,
    approvedScope: ApprovalScope,
    policyDecisionId: str,
    effectivePolicySnapshotDigest: str,
    issuedAt: datetime,
    expiresAt: datetime | None,
    constraints: Mapping[str, object],
) -> ApprovalReceipt:
    payload = {
        "approvalId": approvalId,
        "approverRef": approverRef,
        "approvalSource": approvalSource,
        "approvedActionKind": approvedActionKind,
        "approvedActionDigest": approvedActionDigest,
        "approvedScope": approvedScope,
        "policyDecisionId": policyDecisionId,
        "effectivePolicySnapshotDigest": effectivePolicySnapshotDigest,
        "issuedAt": _datetime_json(issuedAt),
        "expiresAt": _datetime_json(expiresAt) if expiresAt else None,
        "constraints": dict(constraints),
        "revokesApprovalDigest": None,
        "revokedByRef": None,
        "revokedAt": None,
        "reasonCode": None,
    }
    return ApprovalReceipt(**payload, approvalDigest=_digest_json(payload))


def verify_approval_receipt_for_action(
    receipt: ApprovalReceipt,
    *,
    actionDigest: str,
    requiredScope: ApprovalScope,
    now: datetime,
    requiredActionKind: ApprovedActionKind | None = None,
    effectivePolicySnapshotDigest: str | None = None,
    revocations: tuple[ApprovalReceipt, ...] = (),
) -> ApprovalReceiptVerification:
    reason_codes: list[str] = []
    if receipt.approved_action_digest != actionDigest:
        reason_codes.append("approval_action_digest_mismatch")
    if receipt.approved_scope != requiredScope:
        reason_codes.append("approval_scope_mismatch")
    if (
        requiredActionKind is not None
        and receipt.approved_action_kind != requiredActionKind
    ):
        reason_codes.append("approval_action_kind_mismatch")
    if (
        effectivePolicySnapshotDigest is not None
        and receipt.effective_policy_snapshot_digest != effectivePolicySnapshotDigest
    ):
        reason_codes.append("approval_policy_snapshot_mismatch")
    if receipt.expires_at is not None and now > receipt.expires_at:
        reason_codes.append("approval_expired")
    if any(item.revokes_approval_digest == receipt.approval_digest for item in revocations):
        reason_codes.append("approval_revoked")
    return ApprovalReceiptVerification(
        ok=not reason_codes,
        reasonCodes=tuple(dict.fromkeys(reason_codes)),
    )


def digest_approval_receipt_payload(receipt: ApprovalReceipt) -> str:
    payload = receipt.model_dump(by_alias=True, mode="json")
    payload.pop("approvalDigest", None)
    return _digest_json(payload)


def _require_digest(value: str, field_name: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value


def _digest_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return _DIGEST_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _datetime_json(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
