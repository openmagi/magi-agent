from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Self, TypeAlias
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.missions.receipts import (
    canonical_digest,
    has_unsafe_marker,
    sanitize_public_ref,
    sanitize_public_text,
    sanitize_reason_code,
    sha256_ref,
    string_tuple,
)


CronMutationOperation: TypeAlias = Literal["create", "update", "delete"]
CronSchedulerMutationStatus: TypeAlias = Literal[
    "disabled",
    "blocked",
    "approval_required",
    "recorded_local_fake",
    "duplicate",
]
CronCompensationPolicy: TypeAlias = Literal[
    "manual_review_required",
    "restore_previous_definition",
    "noop_local_fake",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_UNSAFE_CRON_MARKER_RE = re.compile(
    r"raw[-_:]?(?:source|output|result|text|prompt|transcript|tool|log|args|"
    r"policy|snapshot|config|control|metadata|selector|recipe|authority|"
    r"instruction|scheduler|cron|mission|payload)|"
    r"private[-_:]?(?:memory|mission|payload|path|cron|scheduler)|"
    r"tool[-_:]?log|child[-_:]?prompt|hidden[-_:]?reasoning|authorization|"
    r"cookie|session|token|secret|credential|private[-_:]?key|api[-_:]?key|"
    r"bearer|connector[-_:]?token|password|control[-_:]?payload|"
    r"scheduler[-_:]?payload|cron[-_:]?payload|mission[-_:]?payload",
    re.IGNORECASE,
)
_CRON_ALLOWED_FIELDS = 5
_SAFE_CRON_REASON_CODES = frozenset(
    {
        "cron_scheduler_mutation_disabled",
        "missing_cron_mutation_policy",
        "private_scheduler_payload_denied",
        "missing_cron_idempotency_key",
        "missing_cron_mutation_evidence",
        "missing_cron_mutation_approval",
        "cron_operation_not_allowed",
        "invalid_cron_timezone",
        "cron_timezone_not_allowed",
        "missing_cron_compensation_policy",
        "missing_cron_schedule",
        "invalid_cron_schedule",
        "local_fake_scheduler_receipts_disabled",
        "cron_local_fake_mutation_denied",
        "local_fake_cron_scheduler_receipt_only",
        "cron_idempotency_duplicate",
        "cron_idempotency_conflict",
        "cron_scheduler_reason",
    }
)


class CronSchedulerMutationAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    live_cron_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="liveCronMutationEnabled",
    )
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    background_execution_enabled: Literal[False] = Field(
        default=False,
        alias="backgroundExecutionEnabled",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )
    provider_call_allowed: Literal[False] = Field(default=False, alias="providerCallAllowed")
    tool_host_dispatch_enabled: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchEnabled",
    )
    channel_delivery_enabled: Literal[False] = Field(
        default=False,
        alias="channelDeliveryEnabled",
    )
    workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="workspaceMutationEnabled",
    )
    memory_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="memoryMutationEnabled",
    )
    filesystem_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="filesystemMutationAllowed",
    )
    database_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="databaseMutationAllowed",
    )
    network_call_allowed: Literal[False] = Field(default=False, alias="networkCallAllowed")

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field_name, field in cls.model_fields.items():
            payload[field.alias or field_name] = False
            payload.pop(field_name, None)
        return payload

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

    @field_serializer(
        "live_cron_mutation_enabled",
        "scheduler_attached",
        "background_execution_enabled",
        "traffic_attached",
        "production_writes_enabled",
        "provider_call_allowed",
        "tool_host_dispatch_enabled",
        "channel_delivery_enabled",
        "workspace_mutation_enabled",
        "memory_mutation_enabled",
        "filesystem_mutation_allowed",
        "database_mutation_allowed",
        "network_call_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class CronSchedulerMutationConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_scheduler_receipts_enabled: bool = Field(
        default=False,
        alias="localFakeSchedulerReceiptsEnabled",
    )
    live_cron_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="liveCronMutationEnabled",
    )
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    background_execution_enabled: Literal[False] = Field(
        default=False,
        alias="backgroundExecutionEnabled",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )
    provider_call_allowed: Literal[False] = Field(default=False, alias="providerCallAllowed")

    @model_validator(mode="before")
    @classmethod
    def _force_default_off_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field_name in (
            "live_cron_mutation_enabled",
            "scheduler_attached",
            "background_execution_enabled",
            "traffic_attached",
            "production_writes_enabled",
            "provider_call_allowed",
        ):
            payload.pop(field_name, None)
        payload["liveCronMutationEnabled"] = False
        payload["schedulerAttached"] = False
        payload["backgroundExecutionEnabled"] = False
        payload["trafficAttached"] = False
        payload["productionWritesEnabled"] = False
        payload["providerCallAllowed"] = False
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        _ = deep
        return type(self).model_validate(payload)

    @field_serializer(
        "live_cron_mutation_enabled",
        "scheduler_attached",
        "background_execution_enabled",
        "traffic_attached",
        "production_writes_enabled",
        "provider_call_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    def authority_flags(self) -> CronSchedulerMutationAuthorityFlags:
        return CronSchedulerMutationAuthorityFlags()


class CronMutationPolicy(BaseModel):
    model_config = _MODEL_CONFIG

    policy_ref: str = Field(alias="policyRef")
    policy_snapshot_ref: str = Field(alias="policySnapshotRef")
    local_fake_mutation_allowed: bool = Field(
        default=False,
        alias="localFakeMutationAllowed",
    )
    approval_required: bool = Field(default=True, alias="approvalRequired")
    idempotency_required: bool = Field(default=True, alias="idempotencyRequired")
    evidence_required: bool = Field(default=True, alias="evidenceRequired")
    allowed_operations: tuple[CronMutationOperation, ...] = Field(
        default=("create", "update", "delete"),
        alias="allowedOperations",
    )
    allowed_timezones: tuple[str, ...] = Field(default=("UTC",), alias="allowedTimezones")
    compensation_required: bool = Field(default=True, alias="compensationRequired")
    live_cron_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="liveCronMutationEnabled",
    )
    scheduler_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="schedulerMutationAllowed",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_default_off_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["liveCronMutationEnabled"] = False
        payload["schedulerMutationAllowed"] = False
        payload.pop("live_cron_mutation_enabled", None)
        payload.pop("scheduler_mutation_allowed", None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        _ = deep
        return type(self).model_validate(payload)

    @field_validator("policy_ref", "policy_snapshot_ref", mode="before")
    @classmethod
    def _sanitize_refs(cls, value: object) -> str:
        return _cron_public_ref(str(value or "policy:cron-mutation"), prefix="policy")

    @field_validator("allowed_timezones", mode="before")
    @classmethod
    def _sanitize_timezones(cls, value: object) -> tuple[str, ...]:
        return tuple(_sanitize_timezone_name(item) for item in string_tuple(value))

    @field_serializer("live_cron_mutation_enabled", "scheduler_mutation_allowed")
    def _serialize_false(self, _value: object) -> bool:
        return False


class CronNextRunPreview(BaseModel):
    model_config = _MODEL_CONFIG

    timezone: str
    next_run_at: int = Field(alias="nextRunAt", ge=0)
    schedule_digest: str = Field(alias="scheduleDigest")

    @field_validator("timezone", mode="before")
    @classmethod
    def _sanitize_timezone(cls, value: object) -> str:
        return _sanitize_timezone_name(value)

    @field_validator("schedule_digest", mode="before")
    @classmethod
    def _sanitize_digest(cls, value: object) -> str:
        return _strict_sha256_ref(str(value or ""))

    def public_projection(self) -> dict[str, object]:
        return {
            "timezone": self.timezone,
            "nextRunAt": self.next_run_at,
            "scheduleDigest": self.schedule_digest,
        }


class CronMutationRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    mission_id: str = Field(alias="missionId")
    run_id: str = Field(alias="runId")
    turn_id: str = Field(alias="turnId")
    operation: CronMutationOperation
    cron_id: str = Field(alias="cronId")
    schedule_expression: str | None = Field(default=None, alias="scheduleExpression")
    timezone: str = "UTC"
    now: int = Field(default=0, ge=0)
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    compensation_policy: CronCompensationPolicy | None = Field(
        default="manual_review_required",
        alias="compensationPolicy",
    )
    private_scheduler_payload: bool = Field(
        default=False,
        alias="privateSchedulerPayload",
        exclude=True,
    )
    raw_prompt: str | None = Field(default=None, alias="rawPrompt", exclude=True)
    raw_output: str | None = Field(default=None, alias="rawOutput", exclude=True)
    tool_logs: str | None = Field(default=None, alias="toolLogs", exclude=True)
    child_prompt: str | None = Field(default=None, alias="childPrompt", exclude=True)

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        _ = deep
        return type(self).model_validate(payload)

    @field_validator("request_id", "mission_id", "run_id", "turn_id", "cron_id", mode="before")
    @classmethod
    def _sanitize_ref_fields(cls, value: object) -> str:
        return _cron_public_ref(str(value or "cron:unspecified"), prefix="cron")

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _sanitize_idempotency_key(cls, value: object) -> str | None:
        if value is None:
            return None
        return _digest_only_ref(str(value), prefix="idempotency")

    @field_validator("approval_ref", mode="before")
    @classmethod
    def _sanitize_approval_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        return _cron_public_ref(str(value), prefix="approval")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _sanitize_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(_cron_public_ref(item, prefix="evidence") for item in string_tuple(value))

    @field_validator("timezone", mode="before")
    @classmethod
    def _sanitize_timezone(cls, value: object) -> str:
        return _sanitize_timezone_name(value)

    @field_validator("schedule_expression", mode="before")
    @classmethod
    def _sanitize_schedule_expression(cls, value: object) -> str | None:
        if value is None:
            return None
        clean = sanitize_public_text(str(value)).strip()
        if not clean or _has_unsafe_cron_marker(clean):
            return None
        return clean[:200]


class CronSchedulerMutationReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["cronSchedulerMutationReceipt.v1"] = Field(
        default="cronSchedulerMutationReceipt.v1",
        alias="schemaVersion",
    )
    receipt_id: str = Field(alias="receiptId")
    receipt_digest: str = Field(alias="receiptDigest")
    request_digest: str = Field(alias="requestDigest")
    operation: CronMutationOperation
    cron_id: str = Field(alias="cronId")
    mission_id: str = Field(alias="missionId")
    run_id: str = Field(alias="runId")
    turn_id: str = Field(alias="turnId")
    status: CronSchedulerMutationStatus
    idempotency_key_digest: str | None = Field(default=None, alias="idempotencyKeyDigest")
    timezone: str
    schedule_digest: str | None = Field(default=None, alias="scheduleDigest")
    next_run_preview: CronNextRunPreview | None = Field(default=None, alias="nextRunPreview")
    compensation_policy: CronCompensationPolicy | None = Field(
        default=None,
        alias="compensationPolicy",
    )
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    policy_snapshot_ref: str = Field(alias="policySnapshotRef")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    local_fake_receipt_recorded: bool = Field(
        default=False,
        alias="localFakeReceiptRecorded",
    )
    local_test_only: bool = Field(default=False, alias="localTestOnly")
    live_cron_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="liveCronMutationEnabled",
    )
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    background_execution_enabled: Literal[False] = Field(
        default=False,
        alias="backgroundExecutionEnabled",
    )
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )
    provider_call_attempted: Literal[False] = Field(
        default=False,
        alias="providerCallAttempted",
    )
    filesystem_mutation_attempted: Literal[False] = Field(
        default=False,
        alias="filesystemMutationAttempted",
    )
    database_mutation_attempted: Literal[False] = Field(
        default=False,
        alias="databaseMutationAttempted",
    )
    network_call_attempted: Literal[False] = Field(default=False, alias="networkCallAttempted")
    authority_flags: CronSchedulerMutationAuthorityFlags = Field(
        default_factory=CronSchedulerMutationAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["liveCronMutationEnabled"] = False
        payload["schedulerAttached"] = False
        payload["backgroundExecutionEnabled"] = False
        payload["productionWritesEnabled"] = False
        payload["providerCallAttempted"] = False
        payload["filesystemMutationAttempted"] = False
        payload["databaseMutationAttempted"] = False
        payload["networkCallAttempted"] = False
        payload["authorityFlags"] = CronSchedulerMutationAuthorityFlags()
        for field_name in (
            "live_cron_mutation_enabled",
            "scheduler_attached",
            "background_execution_enabled",
            "production_writes_enabled",
            "provider_call_attempted",
            "filesystem_mutation_attempted",
            "database_mutation_attempted",
            "network_call_attempted",
            "authority_flags",
        ):
            payload.pop(field_name, None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        _ = deep
        return type(self).model_validate(payload)

    @field_validator(
        "receipt_id",
        "cron_id",
        "mission_id",
        "run_id",
        "turn_id",
        "policy_snapshot_ref",
        mode="before",
    )
    @classmethod
    def _sanitize_ref_fields(cls, value: object) -> str:
        return _cron_public_ref(str(value or "cron:unspecified"), prefix="cron")

    @field_validator("receipt_digest", "request_digest", "policy_snapshot_digest", mode="before")
    @classmethod
    def _sanitize_digest_fields(cls, value: object) -> str:
        return _strict_sha256_ref(str(value or ""))

    @field_validator("idempotency_key_digest", "schedule_digest", mode="before")
    @classmethod
    def _sanitize_optional_digest_fields(cls, value: object) -> str | None:
        if value is None:
            return None
        return _strict_sha256_ref(str(value))

    @field_validator("timezone", mode="before")
    @classmethod
    def _sanitize_timezone(cls, value: object) -> str:
        return _sanitize_timezone_name(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _sanitize_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(_cron_public_ref(item, prefix="evidence") for item in string_tuple(value))

    @field_validator("approval_ref", mode="before")
    @classmethod
    def _sanitize_approval_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        return _cron_public_ref(str(value), prefix="approval")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_sanitize_cron_reason_code(item) for item in string_tuple(value))

    @field_serializer(
        "live_cron_mutation_enabled",
        "scheduler_attached",
        "background_execution_enabled",
        "production_writes_enabled",
        "provider_call_attempted",
        "filesystem_mutation_attempted",
        "database_mutation_attempted",
        "network_call_attempted",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "receiptId": _cron_public_ref(self.receipt_id, prefix="cron-mutation"),
            "receiptDigest": self.receipt_digest,
            "requestDigest": self.request_digest,
            "operation": self.operation,
            "cronId": _cron_public_ref(self.cron_id, prefix="cron"),
            "missionId": _cron_public_ref(self.mission_id, prefix="mission"),
            "runId": _cron_public_ref(self.run_id, prefix="run"),
            "turnId": _cron_public_ref(self.turn_id, prefix="turn"),
            "status": self.status,
            "idempotencyKeyDigest": self.idempotency_key_digest,
            "timezone": self.timezone,
            "scheduleDigest": self.schedule_digest,
            "nextRunPreview": (
                None if self.next_run_preview is None else self.next_run_preview.public_projection()
            ),
            "compensationPolicy": self.compensation_policy,
            "policySnapshotDigest": self.policy_snapshot_digest,
            "policySnapshotRef": _cron_public_ref(self.policy_snapshot_ref, prefix="policy-snapshot"),
            "evidenceRefs": [_cron_public_ref(ref, prefix="evidence") for ref in self.evidence_refs],
            "approvalRef": None if self.approval_ref is None else _cron_public_ref(self.approval_ref, prefix="approval"),
            "reasonCodes": [_sanitize_cron_reason_code(code) for code in self.reason_codes],
            "localFakeReceiptRecorded": self.local_fake_receipt_recorded,
            "localTestOnly": self.local_test_only,
            "liveCronMutationEnabled": False,
            "schedulerAttached": False,
            "backgroundExecutionEnabled": False,
            "productionWritesEnabled": False,
            "providerCallAttempted": False,
            "filesystemMutationAttempted": False,
            "databaseMutationAttempted": False,
            "networkCallAttempted": False,
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class CronSchedulerMutationResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: CronSchedulerMutationStatus
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    receipt: CronSchedulerMutationReceipt | None = None
    request_digest: str = Field(alias="requestDigest")
    authority_flags: CronSchedulerMutationAuthorityFlags = Field(
        default_factory=CronSchedulerMutationAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_authority_flags(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["authorityFlags"] = CronSchedulerMutationAuthorityFlags()
        payload.pop("authority_flags", None)
        if "requestDigest" not in payload:
            receipt = payload.get("receipt")
            if isinstance(receipt, CronSchedulerMutationReceipt):
                payload["requestDigest"] = receipt.request_digest
            elif isinstance(receipt, Mapping) and isinstance(receipt.get("requestDigest"), str):
                payload["requestDigest"] = receipt["requestDigest"]
            else:
                payload["requestDigest"] = sha256_ref("cron-mutation-result")
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        _ = deep
        return type(self).model_validate(payload)

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_sanitize_cron_reason_code(item) for item in string_tuple(value))

    @field_validator("request_digest", mode="before")
    @classmethod
    def _sanitize_request_digest(cls, value: object) -> str:
        return _strict_sha256_ref(str(value or ""))

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasonCodes": [_sanitize_cron_reason_code(code) for code in self.reason_codes],
            "requestDigest": self.request_digest,
            "receipt": None if self.receipt is None else self.receipt.public_projection(),
            "authorityFlags": CronSchedulerMutationAuthorityFlags().model_dump(by_alias=True),
        }


def evaluate_cron_mutation(
    *,
    config: CronSchedulerMutationConfig | Mapping[str, Any] | None,
    request: CronMutationRequest | Mapping[str, Any],
    policy: CronMutationPolicy | Mapping[str, Any] | None,
) -> CronSchedulerMutationResult:
    safe_config = (
        config
        if isinstance(config, CronSchedulerMutationConfig)
        else CronSchedulerMutationConfig.model_validate(config or {})
    )
    safe_request = CronMutationRequest.model_validate(request)
    safe_policy = (
        policy
        if isinstance(policy, CronMutationPolicy)
        else CronMutationPolicy.model_validate(policy)
        if policy is not None
        else None
    )
    request_digest = _request_digest(safe_request)
    policy_digest = _policy_snapshot_digest(safe_policy)

    if not safe_config.enabled:
        return _result(
            status="disabled",
            reason_codes=("cron_scheduler_mutation_disabled",),
            request=safe_request,
            policy=safe_policy,
            request_digest=request_digest,
            policy_snapshot_digest=policy_digest,
            local_fake_recorded=False,
        )

    denial_status, denial_reasons = _denial_reasons(safe_request, safe_policy)
    if denial_status is not None:
        return _result(
            status=denial_status,
            reason_codes=denial_reasons,
            request=safe_request,
            policy=safe_policy,
            request_digest=request_digest,
            policy_snapshot_digest=policy_digest,
            local_fake_recorded=False,
        )

    if not safe_config.local_fake_scheduler_receipts_enabled:
        return _result(
            status="blocked",
            reason_codes=("local_fake_scheduler_receipts_disabled",),
            request=safe_request,
            policy=safe_policy,
            request_digest=request_digest,
            policy_snapshot_digest=policy_digest,
            local_fake_recorded=False,
        )

    assert safe_policy is not None
    if not safe_policy.local_fake_mutation_allowed:
        return _result(
            status="blocked",
            reason_codes=("cron_local_fake_mutation_denied",),
            request=safe_request,
            policy=safe_policy,
            request_digest=request_digest,
            policy_snapshot_digest=policy_digest,
            local_fake_recorded=False,
        )

    return _result(
        status="recorded_local_fake",
        reason_codes=("local_fake_cron_scheduler_receipt_only",),
        request=safe_request,
        policy=safe_policy,
        request_digest=request_digest,
        policy_snapshot_digest=policy_digest,
        local_fake_recorded=True,
    )


def idempotency_conflict_result(
    *,
    request: CronMutationRequest,
    policy: CronMutationPolicy | None,
    existing_request_digest: str,
) -> CronSchedulerMutationResult:
    policy_digest = _policy_snapshot_digest(policy)
    reason_codes = (
        "cron_idempotency_conflict",
        _digest_reason("existing_request", existing_request_digest),
    )
    return _result(
        status="blocked",
        reason_codes=reason_codes[:1],
        request=request,
        policy=policy,
        request_digest=_request_digest(request),
        policy_snapshot_digest=policy_digest,
        local_fake_recorded=False,
    )


def duplicate_result(
    *,
    receipt: CronSchedulerMutationReceipt,
) -> CronSchedulerMutationResult:
    return CronSchedulerMutationResult(
        status="duplicate",
        reasonCodes=("cron_idempotency_duplicate",),
        requestDigest=receipt.request_digest,
        receipt=receipt,
        authorityFlags=CronSchedulerMutationAuthorityFlags(),
    )


def _denial_reasons(
    request: CronMutationRequest,
    policy: CronMutationPolicy | None,
) -> tuple[CronSchedulerMutationStatus | None, tuple[str, ...]]:
    if policy is None:
        return "blocked", ("missing_cron_mutation_policy",)
    if request.private_scheduler_payload:
        return "blocked", ("private_scheduler_payload_denied",)
    if policy.idempotency_required and request.idempotency_key is None:
        return "blocked", ("missing_cron_idempotency_key",)
    if policy.evidence_required and not request.evidence_refs:
        return "blocked", ("missing_cron_mutation_evidence",)
    if policy.approval_required and request.approval_ref is None:
        return "approval_required", ("missing_cron_mutation_approval",)
    if request.operation not in policy.allowed_operations:
        return "blocked", ("cron_operation_not_allowed",)
    if not _timezone_exists(request.timezone):
        return "blocked", ("invalid_cron_timezone",)
    if request.timezone not in policy.allowed_timezones:
        return "blocked", ("cron_timezone_not_allowed",)
    if policy.compensation_required and request.compensation_policy is None:
        return "blocked", ("missing_cron_compensation_policy",)
    if request.operation in {"create", "update"}:
        if request.schedule_expression is None:
            return "blocked", ("missing_cron_schedule",)
        if _next_run_preview(request) is None:
            return "blocked", ("invalid_cron_schedule",)
    return None, ()


def _result(
    *,
    status: CronSchedulerMutationStatus,
    reason_codes: Sequence[str],
    request: CronMutationRequest,
    policy: CronMutationPolicy | None,
    request_digest: str,
    policy_snapshot_digest: str,
    local_fake_recorded: bool,
) -> CronSchedulerMutationResult:
    safe_reason_codes = tuple(_sanitize_cron_reason_code(item) for item in reason_codes)
    receipt = _receipt_for_mutation(
        status=status,
        reason_codes=safe_reason_codes,
        request=request,
        policy=policy,
        request_digest=request_digest,
        policy_snapshot_digest=policy_snapshot_digest,
        local_fake_recorded=local_fake_recorded,
    )
    return CronSchedulerMutationResult(
        status=status,
        reasonCodes=safe_reason_codes,
        receipt=receipt,
        requestDigest=request_digest,
        authorityFlags=CronSchedulerMutationAuthorityFlags(),
    )


def _receipt_for_mutation(
    *,
    status: CronSchedulerMutationStatus,
    reason_codes: tuple[str, ...],
    request: CronMutationRequest,
    policy: CronMutationPolicy | None,
    request_digest: str,
    policy_snapshot_digest: str,
    local_fake_recorded: bool,
) -> CronSchedulerMutationReceipt:
    policy_snapshot_ref = (
        "policy-snapshot:absent"
        if policy is None
        else _cron_public_ref(policy.policy_snapshot_ref, prefix="policy-snapshot")
    )
    schedule_digest = None
    if request.schedule_expression is not None:
        schedule_digest = sha256_ref(request.schedule_expression)
    next_run_preview = _next_run_preview(request) if local_fake_recorded else None
    digest_payload = {
        "schemaVersion": "cronSchedulerMutationReceipt.v1",
        "requestDigest": request_digest,
        "operation": request.operation,
        "cronId": request.cron_id,
        "missionId": request.mission_id,
        "runId": request.run_id,
        "turnId": request.turn_id,
        "status": status,
        "idempotencyKeyDigest": request.idempotency_key,
        "timezone": request.timezone,
        "scheduleDigest": schedule_digest,
        "nextRunPreview": (
            None
            if next_run_preview is None
            else next_run_preview.model_dump(by_alias=True, mode="json", warnings=False)
        ),
        "compensationPolicy": request.compensation_policy,
        "policySnapshotDigest": policy_snapshot_digest,
        "policySnapshotRef": policy_snapshot_ref,
        "evidenceRefs": request.evidence_refs,
        "approvalRef": request.approval_ref,
        "reasonCodes": reason_codes,
        "localFakeReceiptRecorded": local_fake_recorded,
    }
    receipt_digest = canonical_digest(digest_payload)
    return CronSchedulerMutationReceipt(
        receiptId=f"cron-mutation:{receipt_digest[7:23]}",
        receiptDigest=receipt_digest,
        requestDigest=request_digest,
        operation=request.operation,
        cronId=request.cron_id,
        missionId=request.mission_id,
        runId=request.run_id,
        turnId=request.turn_id,
        status=status,
        idempotencyKeyDigest=request.idempotency_key,
        timezone=request.timezone,
        scheduleDigest=schedule_digest,
        nextRunPreview=next_run_preview,
        compensationPolicy=request.compensation_policy,
        policySnapshotDigest=policy_snapshot_digest,
        policySnapshotRef=policy_snapshot_ref,
        evidenceRefs=request.evidence_refs,
        approvalRef=request.approval_ref,
        reasonCodes=reason_codes,
        localFakeReceiptRecorded=local_fake_recorded,
        localTestOnly=local_fake_recorded,
        authorityFlags=CronSchedulerMutationAuthorityFlags(),
    )


def _request_digest(request: CronMutationRequest) -> str:
    return canonical_digest(
        {
            "request": request.model_dump(
                by_alias=True,
                mode="json",
                exclude={
                    "raw_prompt",
                    "raw_output",
                    "tool_logs",
                    "child_prompt",
                    "private_scheduler_payload",
                },
                warnings=False,
            ),
        },
    )


def _policy_snapshot_digest(policy: CronMutationPolicy | None) -> str:
    payload = (
        {"policy": None}
        if policy is None
        else policy.model_dump(by_alias=True, mode="json", warnings=False)
    )
    return canonical_digest({"policy": payload})


def _next_run_preview(request: CronMutationRequest) -> CronNextRunPreview | None:
    if request.operation == "delete" or request.schedule_expression is None:
        return None
    try:
        next_run_at = _next_fire_after(
            expression=request.schedule_expression,
            timezone=request.timezone,
            now=request.now,
        )
    except (ValueError, ZoneInfoNotFoundError):
        return None
    return CronNextRunPreview(
        timezone=request.timezone,
        nextRunAt=next_run_at,
        scheduleDigest=sha256_ref(request.schedule_expression),
    )


def _next_fire_after(*, expression: str, timezone: str, now: int) -> int:
    fields = expression.split()
    if len(fields) != _CRON_ALLOWED_FIELDS:
        raise ValueError("cron expression must contain five fields")
    minute_values = _parse_cron_field(fields[0], 0, 59)
    hour_values = _parse_cron_field(fields[1], 0, 23)
    day_values = _parse_cron_field(fields[2], 1, 31)
    month_values = _parse_cron_field(fields[3], 1, 12)
    weekday_values = _parse_cron_field(fields[4], 0, 7)
    if 7 in weekday_values:
        weekday_values = frozenset(0 if value == 7 else value for value in weekday_values)

    tz = ZoneInfo(timezone)
    current_utc = datetime.fromtimestamp((now + 1) / 1000, tz=UTC)
    candidate = current_utc.astimezone(tz).replace(second=0, microsecond=0)
    if candidate <= current_utc.astimezone(tz):
        candidate = candidate + timedelta(minutes=1)
    for _ in range(60 * 24 * 366):
        cron_weekday = (candidate.weekday() + 1) % 7
        if (
            candidate.minute in minute_values
            and candidate.hour in hour_values
            and candidate.day in day_values
            and candidate.month in month_values
            and cron_weekday in weekday_values
        ):
            return int(candidate.astimezone(UTC).timestamp() * 1000)
        candidate = candidate + timedelta(minutes=1)
    raise ValueError("cron expression has no next run within one year")


def _parse_cron_field(field: str, minimum: int, maximum: int) -> frozenset[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            range_part, step_part = part.split("/", 1)
            step = int(step_part)
            if step <= 0:
                raise ValueError("cron step must be positive")
        else:
            range_part = part
        if range_part == "*":
            values.update(range(minimum, maximum + 1, step))
        elif "-" in range_part:
            start_text, end_text = range_part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start < minimum or end > maximum or start > end:
                raise ValueError("cron range out of bounds")
            values.update(range(start, end + 1, step))
        else:
            value = int(range_part)
            if value < minimum or value > maximum:
                raise ValueError("cron value out of range")
            values.add(value)
    if not values:
        raise ValueError("cron field cannot be empty")
    return frozenset(values)


def _timezone_exists(timezone: str) -> bool:
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return False
    return True


def _sanitize_timezone_name(value: object) -> str:
    if value is None:
        return "UTC"
    raw = str(value).strip()
    if not raw:
        return "UTC"
    clean = sanitize_public_text(raw)
    if not clean or _has_unsafe_cron_marker(clean):
        return "[redacted-timezone]"
    return clean[:80]


def _strict_sha256_ref(value: str) -> str:
    raw = str(value)
    if re.fullmatch(r"sha256:[a-f0-9]{64}", raw):
        return raw
    return sha256_ref(raw)


def _cron_public_ref(value: str, *, prefix: str) -> str:
    raw = str(value)
    if _has_unsafe_cron_marker(raw):
        return _digest_only_ref(raw, prefix=prefix)
    clean = sanitize_public_ref(raw)
    if clean.startswith("["):
        return _digest_only_ref(raw, prefix=prefix)
    if _has_unsafe_cron_marker(clean):
        return _digest_only_ref(raw, prefix=prefix)
    return clean


def _digest_only_ref(value: str, *, prefix: str) -> str:
    return f"{prefix}:" + sha256_ref(str(value)).removeprefix("sha256:")


def _sanitize_cron_reason_code(value: str) -> str:
    raw = str(value)
    normalized = raw.strip().lower().replace(" ", "_")
    if normalized in _SAFE_CRON_REASON_CODES:
        return normalized
    if _has_unsafe_cron_marker(raw):
        return "cron_scheduler_reason"
    return sanitize_reason_code(raw)


def _digest_reason(label: str, value: str) -> str:
    return f"{label}:{sha256_ref(value).removeprefix('sha256:')}"


def _has_unsafe_cron_marker(value: str) -> bool:
    return has_unsafe_marker(value) or _UNSAFE_CRON_MARKER_RE.search(value) is not None


__all__ = [
    "CronCompensationPolicy",
    "CronMutationOperation",
    "CronMutationPolicy",
    "CronMutationRequest",
    "CronNextRunPreview",
    "CronSchedulerMutationAuthorityFlags",
    "CronSchedulerMutationConfig",
    "CronSchedulerMutationReceipt",
    "CronSchedulerMutationResult",
    "CronSchedulerMutationStatus",
    "duplicate_result",
    "evaluate_cron_mutation",
    "idempotency_conflict_result",
    "sha256_ref",
]
