from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.billing.quota import QuotaDecision
from magi_agent.ops.safety import require_digest, require_safe_ref
from magi_agent.tenancy.context import TenantContext, TenantRuntimeAuthorityFlags


SpendReceiptStatus = Literal["reserved", "committed", "released", "fail_closed"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


def _digest_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class _SpendModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError(f"model_construct is disabled for {cls.__name__}")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError(f"model_copy update is disabled for {type(self).__name__}")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


class SpendAmount(_SpendModel):
    currency: Literal["USD"]
    micros: int = Field(ge=0)

    @property
    def amount_digest(self) -> str:
        return _digest_payload({"currency": self.currency, "micros": self.micros})

    def public_projection(self) -> dict[str, object]:
        return {
            "currency": self.currency,
            "micros": self.micros,
            "amountDigest": self.amount_digest,
        }


class SpendReservationRequest(_SpendModel):
    schema_version: Literal["openmagi.billing.spend_reservation_request.v1"] = Field(
        default="openmagi.billing.spend_reservation_request.v1",
        alias="schemaVersion",
    )
    tenant_context: TenantContext = Field(alias="tenantContext")
    reservation_id: str = Field(alias="reservationId")
    operation_ref: str = Field(alias="operationRef")
    spend_quota_key: str = Field(alias="spendQuotaKey")
    idempotency_key: str = Field(alias="idempotencyKey")
    amount: SpendAmount
    quota_decision: QuotaDecision = Field(alias="quotaDecision")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")

    @field_validator("reservation_id", "operation_ref", "spend_quota_key", "idempotency_key")
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        if self.policy_snapshot_digest != self.tenant_context.policy_snapshot_digest:
            raise ValueError("spend reservation policy snapshot must match tenant context")
        if self.quota_decision.tenant_context.context_digest != self.tenant_context.context_digest:
            raise ValueError("spend reservation quota decision tenant context mismatch")
        if self.quota_decision.operation_ref != self.operation_ref:
            raise ValueError("spend reservation quota decision operation mismatch")
        if self.operation_ref not in self.tenant_context.authority_scope.allowed_operations:
            raise ValueError("spend reservation operation must be inside tenant authority scope")
        if self.quota_decision.operation_ref not in self.tenant_context.authority_scope.allowed_operations:
            raise ValueError("spend reservation quota decision operation must be inside tenant authority scope")
        if self.quota_decision.quota_key != self.spend_quota_key:
            raise ValueError("spend reservation quota decision key mismatch")
        if self.quota_decision.unit != "usd_micros":
            raise ValueError("spend reservation requires usd_micros quota evidence")
        if self.quota_decision.requested_amount != self.amount.micros:
            raise ValueError("spend reservation amount must match quota decision amount")
        return self

    @property
    def request_digest(self) -> str:
        return _digest_payload(
            {
                "schemaVersion": self.schema_version,
                "tenantContextDigest": self.tenant_context.context_digest,
                "reservationId": self.reservation_id,
                "operationRef": self.operation_ref,
                "spendQuotaKey": self.spend_quota_key,
                "idempotencyKey": self.idempotency_key,
                "amountDigest": self.amount.amount_digest,
                "quotaDecisionDigest": self.quota_decision.decision_digest,
                "policySnapshotDigest": self.policy_snapshot_digest,
            }
        )


class SpendReservationReceipt(_SpendModel):
    schema_version: Literal["openmagi.billing.spend_receipt.v1"] = Field(
        default="openmagi.billing.spend_receipt.v1",
        alias="schemaVersion",
    )
    tenant_context: TenantContext = Field(alias="tenantContext")
    reservation_id: str = Field(alias="reservationId")
    operation_ref: str = Field(alias="operationRef")
    amount: SpendAmount
    status: SpendReceiptStatus
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    request_digest: str = Field(alias="requestDigest")
    parent_receipt_digest: str | None = Field(default=None, alias="parentReceiptDigest")
    live_billing_call: Literal[False] = Field(default=False, alias="liveBillingCall")
    production_billing_committed: Literal[False] = Field(
        default=False,
        alias="productionBillingCommitted",
    )
    quota_mutation_written: Literal[False] = Field(default=False, alias="quotaMutationWritten")
    authority_flags: TenantRuntimeAuthorityFlags = Field(
        default_factory=TenantRuntimeAuthorityFlags,
        alias="authorityFlags",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")

    @model_validator(mode="before")
    @classmethod
    def _force_no_live_side_effects(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for alias in ("liveBillingCall", "productionBillingCommitted", "quotaMutationWritten"):
            payload[alias] = False
        payload.pop("live_billing_call", None)
        payload.pop("production_billing_committed", None)
        payload.pop("quota_mutation_written", None)
        return payload

    @field_validator("reservation_id", "operation_ref")
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("policy_snapshot_digest", "request_digest", "parent_receipt_digest")
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("reason_codes")
    @classmethod
    def _validate_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("spend receipt requires reason codes")
        return tuple(require_safe_ref(item, field_name="reasonCodes") for item in value)

    @model_validator(mode="after")
    def _validate_policy(self) -> Self:
        if self.policy_snapshot_digest != self.tenant_context.policy_snapshot_digest:
            raise ValueError("spend receipt policy snapshot must match tenant context")
        if self.operation_ref not in self.tenant_context.authority_scope.allowed_operations:
            raise ValueError("spend receipt operation must be inside tenant authority scope")
        if self.status in {"committed", "released"} and self.parent_receipt_digest is None:
            raise ValueError("spend receipt transition requires parent receipt digest")
        if self.status in {"reserved", "fail_closed"} and self.parent_receipt_digest is not None:
            raise ValueError("initial spend receipt must not include parent receipt digest")
        return self

    @field_serializer("live_billing_call", "production_billing_committed", "quota_mutation_written")
    def _serialize_false(self, _value: object) -> bool:
        return False

    @property
    def receipt_digest(self) -> str:
        return _digest_payload(
            {
                "schemaVersion": self.schema_version,
                "tenantContextDigest": self.tenant_context.context_digest,
                "reservationId": self.reservation_id,
                "operationRef": self.operation_ref,
                "amountDigest": self.amount.amount_digest,
                "status": self.status,
                "policySnapshotDigest": self.policy_snapshot_digest,
                "reasonCodes": list(self.reason_codes),
                "requestDigest": self.request_digest,
                "parentReceiptDigest": self.parent_receipt_digest,
            }
        )

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.billing.spend_receipt.public.v1",
            "tenantContextDigest": self.tenant_context.context_digest,
            "reservationId": self.reservation_id,
            "operationRef": self.operation_ref,
            "amount": self.amount.public_projection(),
            "status": self.status,
            "policySnapshotDigest": self.policy_snapshot_digest,
            "reasonCodes": list(self.reason_codes),
            "requestDigest": self.request_digest,
            "parentReceiptDigest": self.parent_receipt_digest,
            "receiptDigest": self.receipt_digest,
            "liveBillingCall": False,
            "productionBillingCommitted": False,
            "quotaMutationWritten": False,
            "authorityFlags": self.authority_flags.public_projection(),
            "createdAt": self.created_at.isoformat(),
        }


class SpendCommitRequest(_SpendModel):
    reservation_receipt: SpendReservationReceipt = Field(alias="reservationReceipt")
    final_amount: SpendAmount = Field(alias="finalAmount")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")

    @field_validator("policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @model_validator(mode="after")
    def _validate_reservation(self) -> Self:
        if self.policy_snapshot_digest != self.reservation_receipt.policy_snapshot_digest:
            raise ValueError("commit policy snapshot must match reservation receipt")
        if self.reservation_receipt.status != "reserved":
            raise ValueError("only reserved spend receipts may be committed")
        if self.final_amount.currency != self.reservation_receipt.amount.currency:
            raise ValueError("commit amount currency must match reservation")
        if self.final_amount.micros > self.reservation_receipt.amount.micros:
            raise ValueError("commit amount must not exceed reservation amount")
        return self


class SpendReleaseRequest(_SpendModel):
    reservation_receipt: SpendReservationReceipt = Field(alias="reservationReceipt")
    reason_code: str = Field(alias="reasonCode")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")

    @field_validator("reason_code")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        return require_safe_ref(value, field_name="reasonCode")

    @field_validator("policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @model_validator(mode="after")
    def _validate_reservation(self) -> Self:
        if self.policy_snapshot_digest != self.reservation_receipt.policy_snapshot_digest:
            raise ValueError("release policy snapshot must match reservation receipt")
        if self.reservation_receipt.status != "reserved":
            raise ValueError("only reserved spend receipts may be released")
        return self


def reserve_spend(request: SpendReservationRequest) -> SpendReservationReceipt:
    validated = SpendReservationRequest.model_validate(request.model_dump(by_alias=True, mode="json"))
    if not validated.quota_decision.allowed or validated.quota_decision.status != "allowed":
        return _receipt(
            validated.tenant_context,
            reservation_id=validated.reservation_id,
            operation_ref=validated.operation_ref,
            amount=validated.amount,
            status="fail_closed",
            policy_snapshot_digest=validated.policy_snapshot_digest,
            reason_codes=("quota_not_allowed",),
            request_digest=validated.request_digest,
        )
    return _receipt(
        validated.tenant_context,
        reservation_id=validated.reservation_id,
        operation_ref=validated.operation_ref,
        amount=validated.amount,
        status="reserved",
        policy_snapshot_digest=validated.policy_snapshot_digest,
        reason_codes=("local_spend_reserved",),
        request_digest=validated.request_digest,
    )


def commit_spend_reservation(request: SpendCommitRequest) -> SpendReservationReceipt:
    validated = SpendCommitRequest.model_validate(request.model_dump(by_alias=True, mode="json"))
    receipt = validated.reservation_receipt
    return _receipt(
        receipt.tenant_context,
        reservation_id=receipt.reservation_id,
        operation_ref=receipt.operation_ref,
        amount=validated.final_amount,
        status="committed",
        policy_snapshot_digest=validated.policy_snapshot_digest,
        reason_codes=("local_spend_commit_recorded",),
        request_digest=receipt.request_digest,
        parent_receipt_digest=receipt.receipt_digest,
    )


def release_spend_reservation(request: SpendReleaseRequest) -> SpendReservationReceipt:
    validated = SpendReleaseRequest.model_validate(request.model_dump(by_alias=True, mode="json"))
    receipt = validated.reservation_receipt
    return _receipt(
        receipt.tenant_context,
        reservation_id=receipt.reservation_id,
        operation_ref=receipt.operation_ref,
        amount=receipt.amount,
        status="released",
        policy_snapshot_digest=validated.policy_snapshot_digest,
        reason_codes=(validated.reason_code,),
        request_digest=receipt.request_digest,
        parent_receipt_digest=receipt.receipt_digest,
    )


def _receipt(
    tenant_context: TenantContext,
    *,
    reservation_id: str,
    operation_ref: str,
    amount: SpendAmount,
    status: SpendReceiptStatus,
    policy_snapshot_digest: str,
    reason_codes: tuple[str, ...],
    request_digest: str,
    parent_receipt_digest: str | None = None,
) -> SpendReservationReceipt:
    return SpendReservationReceipt(
        tenantContext=tenant_context,
        reservationId=reservation_id,
        operationRef=operation_ref,
        amount=amount,
        status=status,
        policySnapshotDigest=policy_snapshot_digest,
        reasonCodes=reason_codes,
        requestDigest=request_digest,
        parentReceiptDigest=parent_receipt_digest,
    )
