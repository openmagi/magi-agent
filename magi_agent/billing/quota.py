from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.ops.safety import (
    require_digest,
    require_safe_ref,
    safe_metadata,
    serialize_safe_value,
)
from magi_agent.tenancy.context import TenantContext, TenantRuntimeAuthorityFlags


QuotaUnit = Literal["requests", "tokens", "usd_micros", "tool_calls", "bytes", "jobs"]
QuotaDecisionStatus = Literal["allowed", "denied", "fail_closed"]
QuotaDecisionSource = Literal["disabled", "local_contract"]

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


class _BillingModel(BaseModel):
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


class QuotaEvaluationConfig(_BillingModel):
    local_evaluation_enabled: bool = Field(default=False, alias="localEvaluationEnabled")
    kill_switch_enabled: bool = Field(default=False, alias="killSwitchEnabled")
    live_billing_system_attached: Literal[False] = Field(
        default=False,
        alias="liveBillingSystemAttached",
    )
    production_quota_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="productionQuotaMutationEnabled",
    )
    authority_flags: TenantRuntimeAuthorityFlags = Field(
        default_factory=TenantRuntimeAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_non_production(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["liveBillingSystemAttached"] = False
        payload.pop("live_billing_system_attached", None)
        payload["productionQuotaMutationEnabled"] = False
        payload.pop("production_quota_mutation_enabled", None)
        return payload

    @field_serializer("live_billing_system_attached", "production_quota_mutation_enabled")
    def _serialize_false(self, _value: object) -> bool:
        return False


class QuotaLimit(_BillingModel):
    schema_version: Literal["openmagi.billing.quota_limit.v1"] = Field(
        default="openmagi.billing.quota_limit.v1",
        alias="schemaVersion",
    )
    quota_key: str = Field(alias="quotaKey")
    unit: QuotaUnit
    max_amount: int = Field(alias="maxAmount", ge=0)
    used_amount: int = Field(default=0, alias="usedAmount", ge=0)
    reserved_amount: int = Field(default=0, alias="reservedAmount", ge=0)
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("quota_key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        return require_safe_ref(value, field_name="quotaKey")

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return safe_metadata(value)

    @model_validator(mode="after")
    def _validate_usage(self) -> Self:
        if self.used_amount > self.max_amount:
            raise ValueError("usedAmount must not exceed maxAmount")
        if self.reserved_amount > self.max_amount:
            raise ValueError("reservedAmount must not exceed maxAmount")
        return self

    @property
    def remaining_amount(self) -> int:
        return max(0, self.max_amount - self.used_amount - self.reserved_amount)

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.billing.quota_limit.public.v1",
            "quotaKey": self.quota_key,
            "unit": self.unit,
            "maxAmount": self.max_amount,
            "usedAmount": self.used_amount,
            "reservedAmount": self.reserved_amount,
            "remainingAmount": self.remaining_amount,
            "metadata": {
                key: serialize_safe_value(item)
                for key, item in safe_metadata(self.metadata).items()
            },
        }


class QuotaRequest(_BillingModel):
    schema_version: Literal["openmagi.billing.quota_request.v1"] = Field(
        default="openmagi.billing.quota_request.v1",
        alias="schemaVersion",
    )
    tenant_context: TenantContext = Field(alias="tenantContext")
    operation_ref: str = Field(alias="operationRef")
    quota_key: str = Field(alias="quotaKey")
    requested_amount: int = Field(alias="requestedAmount", ge=1)
    idempotency_key: str = Field(alias="idempotencyKey")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("operation_ref", "quota_key", "idempotency_key")
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return safe_metadata(value)

    @model_validator(mode="after")
    def _validate_policy_and_authority(self) -> Self:
        if self.policy_snapshot_digest != self.tenant_context.policy_snapshot_digest:
            raise ValueError("quota request policy snapshot must match tenant context")
        if self.operation_ref not in self.tenant_context.authority_scope.allowed_operations:
            raise ValueError("quota operation must be inside tenant authority scope")
        return self

    @property
    def request_digest(self) -> str:
        return _digest_payload(
            {
                "schemaVersion": self.schema_version,
                "tenantContextDigest": self.tenant_context.context_digest,
                "operationRef": self.operation_ref,
                "quotaKey": self.quota_key,
                "requestedAmount": self.requested_amount,
                "idempotencyKey": self.idempotency_key,
                "policySnapshotDigest": self.policy_snapshot_digest,
                "metadata": dict(sorted(self.metadata.items())),
            }
        )


class QuotaDecision(_BillingModel):
    schema_version: Literal["openmagi.billing.quota_decision.v1"] = Field(
        default="openmagi.billing.quota_decision.v1",
        alias="schemaVersion",
    )
    tenant_context: TenantContext = Field(alias="tenantContext")
    operation_ref: str = Field(alias="operationRef")
    quota_key: str = Field(alias="quotaKey")
    unit: QuotaUnit
    requested_amount: int = Field(alias="requestedAmount", ge=0)
    allowed: bool
    status: QuotaDecisionStatus
    source: QuotaDecisionSource
    remaining_after_decision: int = Field(alias="remainingAfterDecision", ge=0)
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    decision_digest_ref: str | None = Field(default=None, alias="decisionDigestRef")
    live_billing_system_queried: Literal[False] = Field(
        default=False,
        alias="liveBillingSystemQueried",
    )
    production_quota_mutated: Literal[False] = Field(
        default=False,
        alias="productionQuotaMutated",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")

    @model_validator(mode="before")
    @classmethod
    def _force_no_live_side_effects(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["liveBillingSystemQueried"] = False
        payload.pop("live_billing_system_queried", None)
        payload["productionQuotaMutated"] = False
        payload.pop("production_quota_mutated", None)
        return payload

    @field_validator("operation_ref", "quota_key")
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("decision_digest_ref")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("reason_codes")
    @classmethod
    def _validate_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("quota decision requires reason codes")
        return tuple(require_safe_ref(item, field_name="reasonCodes") for item in value)

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        if self.policy_snapshot_digest != self.tenant_context.policy_snapshot_digest:
            raise ValueError("quota decision policy snapshot must match tenant context")
        if self.operation_ref not in self.tenant_context.authority_scope.allowed_operations:
            raise ValueError("quota decision operation must be inside tenant authority scope")
        if self.allowed and self.status != "allowed":
            raise ValueError("allowed quota decisions must have allowed status")
        if not self.allowed and self.status == "allowed":
            raise ValueError("denied quota decisions must not use allowed status")
        if self.source == "disabled" and self.status != "fail_closed":
            raise ValueError("disabled quota source must fail closed")
        return self

    @field_serializer("live_billing_system_queried", "production_quota_mutated")
    def _serialize_false(self, _value: object) -> bool:
        return False

    @property
    def decision_digest(self) -> str:
        return _digest_payload(
            {
                "schemaVersion": self.schema_version,
                "tenantContextDigest": self.tenant_context.context_digest,
                "operationRef": self.operation_ref,
                "quotaKey": self.quota_key,
                "unit": self.unit,
                "requestedAmount": self.requested_amount,
                "allowed": self.allowed,
                "status": self.status,
                "source": self.source,
                "remainingAfterDecision": self.remaining_after_decision,
                "policySnapshotDigest": self.policy_snapshot_digest,
                "reasonCodes": list(self.reason_codes),
            }
        )

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.billing.quota_decision.public.v1",
            "tenantContextDigest": self.tenant_context.context_digest,
            "operationRef": self.operation_ref,
            "quotaKey": self.quota_key,
            "unit": self.unit,
            "requestedAmount": self.requested_amount,
            "allowed": self.allowed,
            "status": self.status,
            "source": self.source,
            "remainingAfterDecision": self.remaining_after_decision,
            "policySnapshotDigest": self.policy_snapshot_digest,
            "reasonCodes": list(self.reason_codes),
            "decisionDigest": self.decision_digest,
            "liveBillingSystemQueried": False,
            "productionQuotaMutated": False,
        }


def evaluate_quota(
    request: QuotaRequest,
    *,
    limits: Sequence[QuotaLimit],
    config: QuotaEvaluationConfig | None = None,
) -> QuotaDecision:
    validated_request = QuotaRequest.model_validate(request.model_dump(by_alias=True, mode="json"))
    validated_config = config or QuotaEvaluationConfig()
    if not validated_config.local_evaluation_enabled:
        return _decision(
            validated_request,
            unit="requests",
            allowed=False,
            status="fail_closed",
            source="disabled",
            remaining_after_decision=0,
            reason_codes=("local_quota_evaluation_disabled",),
        )
    if validated_config.kill_switch_enabled:
        return _decision(
            validated_request,
            unit="requests",
            allowed=False,
            status="fail_closed",
            source="disabled",
            remaining_after_decision=0,
            reason_codes=("quota_kill_switch_enabled",),
        )

    matching = [limit for limit in limits if limit.quota_key == validated_request.quota_key]
    if not matching:
        return _decision(
            validated_request,
            unit="requests",
            allowed=False,
            status="fail_closed",
            source="local_contract",
            remaining_after_decision=0,
            reason_codes=("quota_limit_missing",),
        )
    limit = QuotaLimit.model_validate(matching[0].model_dump(by_alias=True, mode="json"))
    remaining = limit.remaining_amount
    if validated_request.requested_amount > remaining:
        return _decision(
            validated_request,
            unit=limit.unit,
            allowed=False,
            status="denied",
            source="local_contract",
            remaining_after_decision=remaining,
            reason_codes=("quota_exhausted",),
        )
    return _decision(
        validated_request,
        unit=limit.unit,
        allowed=True,
        status="allowed",
        source="local_contract",
        remaining_after_decision=remaining - validated_request.requested_amount,
        reason_codes=("quota_available",),
    )


def _decision(
    request: QuotaRequest,
    *,
    unit: QuotaUnit,
    allowed: bool,
    status: QuotaDecisionStatus,
    source: QuotaDecisionSource,
    remaining_after_decision: int,
    reason_codes: tuple[str, ...],
) -> QuotaDecision:
    return QuotaDecision(
        tenantContext=request.tenant_context,
        operationRef=request.operation_ref,
        quotaKey=request.quota_key,
        unit=unit,
        requestedAmount=request.requested_amount,
        allowed=allowed,
        status=status,
        source=source,
        remainingAfterDecision=remaining_after_decision,
        policySnapshotDigest=request.policy_snapshot_digest,
        reasonCodes=reason_codes,
    )
