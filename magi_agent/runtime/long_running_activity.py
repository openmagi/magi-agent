from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.runtime.receipt_utils import (
    canonical_digest,
    has_unsafe_marker,
    sanitize_public_ref,
    sanitize_public_text,
    sanitize_reason_code,
    sha256_ref,
    strict_sha256_ref,
    string_tuple,
)


LongRunningActivityEvent: TypeAlias = Literal[
    "start",
    "heartbeat",
    "progress",
    "completion",
    "cancellation",
    "timeout",
    "failure",
]
LongRunningActivityStatus: TypeAlias = Literal[
    "disabled",
    "blocked",
    "approval_required",
    "recorded_local_fake",
    "duplicate",
]
LongRunningSideEffectSurface: TypeAlias = Literal[
    "workspace",
    "memory",
    "channel",
    "cron",
    "artifact",
]

ADK_LONG_RUNNING_FUNCTION_TOOL_REF = "google.adk.tools.LongRunningFunctionTool"

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SAFE_ACTIVITY_REASON_CODES = frozenset(
    {
        "long_running_activity_disabled",
        "missing_long_running_activity_policy",
        "private_activity_payload_denied",
        "activity_event_not_allowed",
        "missing_activity_evidence",
        "missing_activity_cancellation_approval",
        "activity_side_effect_surface_not_allowed",
        "local_fake_activity_disabled",
        "long_running_activity_local_fake_denied",
        "local_fake_activity_receipt_only",
        "missing_activity_idempotency_key",
        "activity_idempotency_duplicate",
        "activity_idempotency_conflict",
        "long_running_activity_reason",
    }
)
_UNSAFE_ACTIVITY_MARKER_RE = re.compile(
    r"raw[-_:]?(?:source|output|result|text|prompt|transcript|tool|log|args|"
    r"policy|snapshot|config|control|metadata|selector|recipe|authority|"
    r"instruction|activity|task|mission|payload)|"
    r"private[-_:]?(?:memory|mission|payload|path|activity|task)|"
    r"tool[-_:]?log|child[-_:]?prompt|hidden[-_:]?reasoning|authorization|"
    r"cookie|session|token|secret|credential|private[-_:]?key|api[-_:]?key|"
    r"bearer|connector[-_:]?token|password|control[-_:]?payload|"
    r"activity[-_:]?payload|task[-_:]?payload|mission[-_:]?payload",
    re.IGNORECASE,
)


class LongRunningActivityAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    long_running_function_tool_attached: Literal[False] = Field(
        default=False,
        alias="longRunningFunctionToolAttached",
    )
    production_background_execution_enabled: Literal[False] = Field(
        default=False,
        alias="productionBackgroundExecutionEnabled",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    user_visible_output_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEnabled",
    )
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )
    provider_call_allowed: Literal[False] = Field(default=False, alias="providerCallAllowed")
    tool_host_dispatch_enabled: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchEnabled",
    )
    workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="workspaceMutationEnabled",
    )
    memory_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="memoryMutationEnabled",
    )
    channel_delivery_enabled: Literal[False] = Field(
        default=False,
        alias="channelDeliveryEnabled",
    )
    cron_mutation_enabled: Literal[False] = Field(default=False, alias="cronMutationEnabled")
    artifact_delivery_enabled: Literal[False] = Field(
        default=False,
        alias="artifactDeliveryEnabled",
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
        "long_running_function_tool_attached",
        "production_background_execution_enabled",
        "traffic_attached",
        "user_visible_output_enabled",
        "production_writes_enabled",
        "provider_call_allowed",
        "tool_host_dispatch_enabled",
        "workspace_mutation_enabled",
        "memory_mutation_enabled",
        "channel_delivery_enabled",
        "cron_mutation_enabled",
        "artifact_delivery_enabled",
        "filesystem_mutation_allowed",
        "database_mutation_allowed",
        "network_call_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class LongRunningActivityConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_activity_enabled: bool = Field(default=False, alias="localFakeActivityEnabled")
    adk_long_running_function_tool_ref: Literal[
        "google.adk.tools.LongRunningFunctionTool"
    ] = Field(
        default=ADK_LONG_RUNNING_FUNCTION_TOOL_REF,
        alias="adkLongRunningFunctionToolRef",
    )
    wraps_adk_long_running_function_tool: Literal[True] = Field(
        default=True,
        alias="wrapsAdkLongRunningFunctionTool",
    )
    long_running_function_tool_attached: Literal[False] = Field(
        default=False,
        alias="longRunningFunctionToolAttached",
    )
    production_background_execution_enabled: Literal[False] = Field(
        default=False,
        alias="productionBackgroundExecutionEnabled",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    user_visible_output_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEnabled",
    )
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
            "long_running_function_tool_attached",
            "production_background_execution_enabled",
            "traffic_attached",
            "user_visible_output_enabled",
            "production_writes_enabled",
            "provider_call_allowed",
        ):
            payload.pop(field_name, None)
        payload["longRunningFunctionToolAttached"] = False
        payload["productionBackgroundExecutionEnabled"] = False
        payload["trafficAttached"] = False
        payload["userVisibleOutputEnabled"] = False
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
        "long_running_function_tool_attached",
        "production_background_execution_enabled",
        "traffic_attached",
        "user_visible_output_enabled",
        "production_writes_enabled",
        "provider_call_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    def authority_flags(self) -> LongRunningActivityAuthorityFlags:
        return LongRunningActivityAuthorityFlags()


class LongRunningActivityPolicy(BaseModel):
    model_config = _MODEL_CONFIG

    policy_ref: str = Field(alias="policyRef")
    policy_snapshot_ref: str = Field(alias="policySnapshotRef")
    local_fake_activity_allowed: bool = Field(default=False, alias="localFakeActivityAllowed")
    evidence_required: bool = Field(default=True, alias="evidenceRequired")
    approval_required_for_cancellation: bool = Field(
        default=True,
        alias="approvalRequiredForCancellation",
    )
    idempotency_required: bool = Field(default=True, alias="idempotencyRequired")
    allowed_events: tuple[LongRunningActivityEvent, ...] = Field(
        default=("start", "heartbeat", "progress", "completion", "cancellation", "timeout", "failure"),
        alias="allowedEvents",
    )
    allowed_side_effect_surfaces: tuple[LongRunningSideEffectSurface, ...] = Field(
        default=(),
        alias="allowedSideEffectSurfaces",
    )
    live_background_execution_enabled: Literal[False] = Field(
        default=False,
        alias="liveBackgroundExecutionEnabled",
    )
    long_running_function_tool_attachment_allowed: Literal[False] = Field(
        default=False,
        alias="longRunningFunctionToolAttachmentAllowed",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_default_off_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["liveBackgroundExecutionEnabled"] = False
        payload["longRunningFunctionToolAttachmentAllowed"] = False
        payload.pop("live_background_execution_enabled", None)
        payload.pop("long_running_function_tool_attachment_allowed", None)
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
        return sanitize_public_ref(str(value or "policy:long-running-activity"))

    @field_validator("allowed_events", mode="before")
    @classmethod
    def _coerce_events(cls, value: object) -> tuple[str, ...]:
        return string_tuple(value)

    @field_validator("allowed_side_effect_surfaces", mode="before")
    @classmethod
    def _coerce_surfaces(cls, value: object) -> tuple[str, ...]:
        return string_tuple(value)

    @field_serializer(
        "live_background_execution_enabled",
        "long_running_function_tool_attachment_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class LongRunningActivityRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    activity_id: str = Field(alias="activityId")
    scope_ref: str = Field(alias="scopeRef")
    run_id: str = Field(alias="runId")
    turn_id: str = Field(alias="turnId")
    event: LongRunningActivityEvent
    now: int = Field(default=0, ge=0)
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    progress_message: str | None = Field(default=None, alias="progressMessage")
    output_refs: tuple[str, ...] = Field(default=(), alias="outputRefs")
    failure_reason: str | None = Field(default=None, alias="failureReason")
    timeout_ms: int | None = Field(default=None, alias="timeoutMs", ge=0)
    requested_side_effect_surfaces: tuple[LongRunningSideEffectSurface, ...] = Field(
        default=(),
        alias="requestedSideEffectSurfaces",
    )
    private_activity_payload: bool = Field(
        default=False,
        alias="privateActivityPayload",
        exclude=True,
    )
    raw_prompt: str | None = Field(default=None, alias="rawPrompt", exclude=True, repr=False)
    raw_output: str | None = Field(default=None, alias="rawOutput", exclude=True, repr=False)
    raw_tool_args: str | None = Field(
        default=None,
        alias="rawToolArgs",
        exclude=True,
        repr=False,
    )
    tool_logs: str | None = Field(default=None, alias="toolLogs", exclude=True, repr=False)
    child_prompt: str | None = Field(default=None, alias="childPrompt", exclude=True, repr=False)
    auth_header: str | None = Field(default=None, alias="authHeader", exclude=True, repr=False)
    cookie_header: str | None = Field(default=None, alias="cookieHeader", exclude=True, repr=False)

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

    @field_validator("request_id", "activity_id", "scope_ref", "run_id", "turn_id", mode="before")
    @classmethod
    def _sanitize_ref_fields(cls, value: object) -> str:
        return sanitize_public_ref(str(value or "activity:unspecified"))

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _sanitize_idempotency_key(cls, value: object) -> str | None:
        if value is None:
            return None
        return "idempotency:" + sha256_ref(str(value)).removeprefix("sha256:")

    @field_validator("evidence_refs", "output_refs", mode="before")
    @classmethod
    def _sanitize_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(sanitize_public_ref(item) for item in string_tuple(value))

    @field_validator("approval_ref", mode="before")
    @classmethod
    def _sanitize_approval_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        return sanitize_public_ref(str(value))

    @field_validator("progress_message", "failure_reason", mode="before")
    @classmethod
    def _sanitize_public_text(cls, value: object) -> str | None:
        if value is None:
            return None
        return _safe_activity_text(str(value))

    @field_validator("requested_side_effect_surfaces", mode="before")
    @classmethod
    def _coerce_surfaces(cls, value: object) -> tuple[str, ...]:
        return string_tuple(value)

    @property
    def request_digest(self) -> str:
        return canonical_digest(
            {
                "requestId": self.request_id,
                "activityId": self.activity_id,
                "scopeRef": self.scope_ref,
                "runId": self.run_id,
                "turnId": self.turn_id,
                "event": self.event,
                "idempotencyKey": self.idempotency_key,
                "evidenceRefs": self.evidence_refs,
                "approvalRef": self.approval_ref,
                "progressDigest": None
                if self.progress_message is None
                else sha256_ref(self.progress_message),
                "outputRefs": self.output_refs,
                "failureDigest": None
                if self.failure_reason is None
                else sha256_ref(self.failure_reason),
                "timeoutMs": self.timeout_ms,
                "requestedSideEffectSurfaces": self.requested_side_effect_surfaces,
            }
        )


class LongRunningActivityReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["longRunningActivityReceipt.v1"] = Field(
        default="longRunningActivityReceipt.v1",
        alias="schemaVersion",
    )
    receipt_id: str = Field(alias="receiptId")
    receipt_digest: str = Field(alias="receiptDigest")
    request_digest: str = Field(alias="requestDigest")
    activity_id: str = Field(alias="activityId")
    scope_ref: str = Field(alias="scopeRef")
    run_id: str = Field(alias="runId")
    turn_id: str = Field(alias="turnId")
    event: LongRunningActivityEvent
    status: LongRunningActivityStatus
    idempotency_key_digest: str | None = Field(default=None, alias="idempotencyKeyDigest")
    occurred_at: int = Field(default=0, alias="occurredAt", ge=0)
    timeout_ms: int | None = Field(default=None, alias="timeoutMs", ge=0)
    progress_digest: str | None = Field(default=None, alias="progressDigest")
    output_ref_digests: tuple[str, ...] = Field(default=(), alias="outputRefDigests")
    failure_reason_digest: str | None = Field(default=None, alias="failureReasonDigest")
    requested_side_effect_surfaces: tuple[LongRunningSideEffectSurface, ...] = Field(
        default=(),
        alias="requestedSideEffectSurfaces",
    )
    allowed_side_effect_surfaces: tuple[LongRunningSideEffectSurface, ...] = Field(
        default=(),
        alias="allowedSideEffectSurfaces",
    )
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    policy_snapshot_ref: str = Field(alias="policySnapshotRef")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    adk_long_running_function_tool_ref: Literal[
        "google.adk.tools.LongRunningFunctionTool"
    ] = Field(
        default=ADK_LONG_RUNNING_FUNCTION_TOOL_REF,
        alias="adkLongRunningFunctionToolRef",
    )
    local_fake_receipt_recorded: bool = Field(
        default=False,
        alias="localFakeReceiptRecorded",
    )
    local_test_only: bool = Field(default=False, alias="localTestOnly")
    long_running_function_tool_attached: Literal[False] = Field(
        default=False,
        alias="longRunningFunctionToolAttached",
    )
    production_background_execution_enabled: Literal[False] = Field(
        default=False,
        alias="productionBackgroundExecutionEnabled",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    user_visible_output_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEnabled",
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
    authority_flags: LongRunningActivityAuthorityFlags = Field(
        default_factory=LongRunningActivityAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["longRunningFunctionToolAttached"] = False
        payload["productionBackgroundExecutionEnabled"] = False
        payload["trafficAttached"] = False
        payload["userVisibleOutputEnabled"] = False
        payload["productionWritesEnabled"] = False
        payload["providerCallAttempted"] = False
        payload["filesystemMutationAttempted"] = False
        payload["databaseMutationAttempted"] = False
        payload["networkCallAttempted"] = False
        payload["authorityFlags"] = LongRunningActivityAuthorityFlags()
        for field_name in (
            "long_running_function_tool_attached",
            "production_background_execution_enabled",
            "traffic_attached",
            "user_visible_output_enabled",
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
        "activity_id",
        "scope_ref",
        "run_id",
        "turn_id",
        "policy_snapshot_ref",
        mode="before",
    )
    @classmethod
    def _sanitize_ref_fields(cls, value: object) -> str:
        return sanitize_public_ref(str(value or "activity:unspecified"))

    @field_validator(
        "request_digest",
        "receipt_digest",
        "policy_snapshot_digest",
        "idempotency_key_digest",
        "progress_digest",
        "failure_reason_digest",
        mode="before",
    )
    @classmethod
    def _sanitize_optional_digest(cls, value: object) -> str | None:
        if value is None:
            return None
        return strict_sha256_ref(value)

    @field_validator("output_ref_digests", mode="before")
    @classmethod
    def _sanitize_digest_tuple(cls, value: object) -> tuple[str, ...]:
        return tuple(strict_sha256_ref(item) for item in string_tuple(value))

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _sanitize_ref_tuple(cls, value: object) -> tuple[str, ...]:
        return tuple(sanitize_public_ref(item) for item in string_tuple(value))

    @field_validator("approval_ref", mode="before")
    @classmethod
    def _sanitize_approval_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        return sanitize_public_ref(str(value))

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_activity_reason_code(item) for item in string_tuple(value))

    @field_serializer(
        "long_running_function_tool_attached",
        "production_background_execution_enabled",
        "traffic_attached",
        "user_visible_output_enabled",
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
            "receiptId": sanitize_public_ref(self.receipt_id),
            "receiptDigest": sanitize_public_ref(self.receipt_digest),
            "requestDigest": sanitize_public_ref(self.request_digest),
            "activityId": sanitize_public_ref(self.activity_id),
            "scopeRef": sanitize_public_ref(self.scope_ref),
            "runId": sanitize_public_ref(self.run_id),
            "turnId": sanitize_public_ref(self.turn_id),
            "event": self.event,
            "status": self.status,
            "idempotencyKeyDigest": (
                None
                if self.idempotency_key_digest is None
                else sanitize_public_ref(self.idempotency_key_digest)
            ),
            "occurredAt": self.occurred_at,
            "timeoutMs": self.timeout_ms,
            "progressDigest": (
                None
                if self.progress_digest is None
                else sanitize_public_ref(self.progress_digest)
            ),
            "outputRefDigests": [
                sanitize_public_ref(ref) for ref in self.output_ref_digests
            ],
            "failureReasonDigest": (
                None
                if self.failure_reason_digest is None
                else sanitize_public_ref(self.failure_reason_digest)
            ),
            "requestedSideEffectSurfaces": list(self.requested_side_effect_surfaces),
            "allowedSideEffectSurfaces": list(self.allowed_side_effect_surfaces),
            "policySnapshotDigest": sanitize_public_ref(self.policy_snapshot_digest),
            "policySnapshotRef": sanitize_public_ref(self.policy_snapshot_ref),
            "evidenceRefs": [sanitize_public_ref(ref) for ref in self.evidence_refs],
            "approvalRef": (
                None if self.approval_ref is None else sanitize_public_ref(self.approval_ref)
            ),
            "reasonCodes": [_activity_reason_code(code) for code in self.reason_codes],
            "adkLongRunningFunctionToolRef": self.adk_long_running_function_tool_ref,
            "localFakeReceiptRecorded": self.local_fake_receipt_recorded,
            "localTestOnly": self.local_test_only,
            "longRunningFunctionToolAttached": False,
            "productionBackgroundExecutionEnabled": False,
            "trafficAttached": False,
            "userVisibleOutputEnabled": False,
            "productionWritesEnabled": False,
            "providerCallAttempted": False,
            "filesystemMutationAttempted": False,
            "databaseMutationAttempted": False,
            "networkCallAttempted": False,
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class LongRunningActivityResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: LongRunningActivityStatus
    receipt: LongRunningActivityReceipt
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    authority_flags: LongRunningActivityAuthorityFlags = Field(
        default_factory=LongRunningActivityAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["authorityFlags"] = LongRunningActivityAuthorityFlags()
        payload.pop("authority_flags", None)
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
        payload["authorityFlags"] = LongRunningActivityAuthorityFlags()
        _ = deep
        return type(self).model_validate(payload)

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_activity_reason_code(item) for item in string_tuple(value))

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "receipt": self.receipt.public_projection(),
            "reasonCodes": [_activity_reason_code(code) for code in self.reason_codes],
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


def evaluate_long_running_activity(
    *,
    config: LongRunningActivityConfig,
    request: LongRunningActivityRequest,
    policy: LongRunningActivityPolicy | None,
) -> LongRunningActivityResult:
    if not config.enabled:
        return _result(
            request=request,
            policy=policy,
            status="disabled",
            reason_codes=("long_running_activity_disabled",),
            local_fake_receipt_recorded=False,
        )
    if policy is None:
        return _result(
            request=request,
            policy=None,
            status="blocked",
            reason_codes=("missing_long_running_activity_policy",),
            local_fake_receipt_recorded=False,
        )
    if request.private_activity_payload:
        return _result(
            request=request,
            policy=policy,
            status="blocked",
            reason_codes=("private_activity_payload_denied",),
            local_fake_receipt_recorded=False,
        )
    if request.event not in policy.allowed_events:
        return _result(
            request=request,
            policy=policy,
            status="blocked",
            reason_codes=("activity_event_not_allowed",),
            local_fake_receipt_recorded=False,
        )
    if policy.evidence_required and not request.evidence_refs:
        return _result(
            request=request,
            policy=policy,
            status="blocked",
            reason_codes=("missing_activity_evidence",),
            local_fake_receipt_recorded=False,
        )
    if policy.idempotency_required and request.idempotency_key is None:
        return _result(
            request=request,
            policy=policy,
            status="blocked",
            reason_codes=("missing_activity_idempotency_key",),
            local_fake_receipt_recorded=False,
        )
    if (
        request.event == "cancellation"
        and policy.approval_required_for_cancellation
        and request.approval_ref is None
    ):
        return _result(
            request=request,
            policy=policy,
            status="approval_required",
            reason_codes=("missing_activity_cancellation_approval",),
            local_fake_receipt_recorded=False,
        )
    if not set(request.requested_side_effect_surfaces).issubset(
        set(policy.allowed_side_effect_surfaces),
    ):
        return _result(
            request=request,
            policy=policy,
            status="blocked",
            reason_codes=("activity_side_effect_surface_not_allowed",),
            local_fake_receipt_recorded=False,
        )
    if not config.local_fake_activity_enabled:
        return _result(
            request=request,
            policy=policy,
            status="blocked",
            reason_codes=("local_fake_activity_disabled",),
            local_fake_receipt_recorded=False,
        )
    if not policy.local_fake_activity_allowed:
        return _result(
            request=request,
            policy=policy,
            status="blocked",
            reason_codes=("long_running_activity_local_fake_denied",),
            local_fake_receipt_recorded=False,
        )
    return _result(
        request=request,
        policy=policy,
        status="recorded_local_fake",
        reason_codes=("local_fake_activity_receipt_only",),
        local_fake_receipt_recorded=True,
    )


def duplicate_activity_result(*, receipt: LongRunningActivityReceipt) -> LongRunningActivityResult:
    return LongRunningActivityResult(
        status="duplicate",
        receipt=receipt,
        reasonCodes=("activity_idempotency_duplicate",),
    )


def idempotency_conflict_result(
    *,
    request: LongRunningActivityRequest,
    policy: LongRunningActivityPolicy | None,
    existing_request_digest: str,
) -> LongRunningActivityResult:
    _ = existing_request_digest
    return _result(
        request=request,
        policy=policy,
        status="blocked",
        reason_codes=("activity_idempotency_conflict",),
        local_fake_receipt_recorded=False,
    )


def _result(
    *,
    request: LongRunningActivityRequest,
    policy: LongRunningActivityPolicy | None,
    status: LongRunningActivityStatus,
    reason_codes: tuple[str, ...],
    local_fake_receipt_recorded: bool,
) -> LongRunningActivityResult:
    receipt = _receipt(
        request=request,
        policy=policy,
        status=status,
        reason_codes=reason_codes,
        local_fake_receipt_recorded=local_fake_receipt_recorded,
    )
    return LongRunningActivityResult(
        status=status,
        receipt=receipt,
        reasonCodes=reason_codes,
    )


def _receipt(
    *,
    request: LongRunningActivityRequest,
    policy: LongRunningActivityPolicy | None,
    status: LongRunningActivityStatus,
    reason_codes: tuple[str, ...],
    local_fake_receipt_recorded: bool,
) -> LongRunningActivityReceipt:
    policy_snapshot_ref = (
        policy.policy_snapshot_ref if policy is not None else "policy-snapshot:missing"
    )
    policy_snapshot_digest = _policy_snapshot_digest(policy)
    allowed_surfaces = policy.allowed_side_effect_surfaces if policy is not None else ()
    payload: dict[str, object] = {
        "schemaVersion": "longRunningActivityReceipt.v1",
        "requestDigest": request.request_digest,
        "activityId": request.activity_id,
        "scopeRef": request.scope_ref,
        "runId": request.run_id,
        "turnId": request.turn_id,
        "event": request.event,
        "status": status,
        "idempotencyKeyDigest": request.idempotency_key,
        "occurredAt": request.now,
        "timeoutMs": request.timeout_ms,
        "progressDigest": None if request.progress_message is None else sha256_ref(request.progress_message),
        "outputRefDigests": tuple(sha256_ref(ref) for ref in request.output_refs),
        "failureReasonDigest": None if request.failure_reason is None else sha256_ref(request.failure_reason),
        "requestedSideEffectSurfaces": request.requested_side_effect_surfaces,
        "allowedSideEffectSurfaces": allowed_surfaces,
        "policySnapshotDigest": policy_snapshot_digest,
        "policySnapshotRef": policy_snapshot_ref,
        "evidenceRefs": request.evidence_refs,
        "approvalRef": request.approval_ref,
        "reasonCodes": tuple(_activity_reason_code(code) for code in reason_codes),
        "adkLongRunningFunctionToolRef": ADK_LONG_RUNNING_FUNCTION_TOOL_REF,
        "localFakeReceiptRecorded": local_fake_receipt_recorded,
        "localTestOnly": local_fake_receipt_recorded,
        "longRunningFunctionToolAttached": False,
        "productionBackgroundExecutionEnabled": False,
        "trafficAttached": False,
        "userVisibleOutputEnabled": False,
        "productionWritesEnabled": False,
        "providerCallAttempted": False,
        "filesystemMutationAttempted": False,
        "databaseMutationAttempted": False,
        "networkCallAttempted": False,
    }
    receipt_digest = canonical_digest(payload)
    payload["receiptDigest"] = receipt_digest
    payload["receiptId"] = "activity-receipt:" + receipt_digest.removeprefix("sha256:")[:24]
    return LongRunningActivityReceipt(**payload)


def _safe_activity_text(value: str) -> str:
    clean = sanitize_public_text(value)
    if not clean or _UNSAFE_ACTIVITY_MARKER_RE.search(clean) or has_unsafe_marker(clean):
        return "[redacted]"
    return clean[:600]


def _policy_snapshot_digest(policy: LongRunningActivityPolicy | None) -> str:
    if policy is None:
        return sha256_ref("policy-snapshot:missing")
    return canonical_digest(
        {
            "policyRef": policy.policy_ref,
            "policySnapshotRef": policy.policy_snapshot_ref,
            "localFakeActivityAllowed": policy.local_fake_activity_allowed,
            "evidenceRequired": policy.evidence_required,
            "approvalRequiredForCancellation": policy.approval_required_for_cancellation,
            "idempotencyRequired": policy.idempotency_required,
            "allowedEvents": policy.allowed_events,
            "allowedSideEffectSurfaces": policy.allowed_side_effect_surfaces,
            "liveBackgroundExecutionEnabled": False,
            "longRunningFunctionToolAttachmentAllowed": False,
        }
    )


def _activity_reason_code(value: str) -> str:
    raw = str(value).strip().lower().replace(" ", "_")
    if raw in _SAFE_ACTIVITY_REASON_CODES:
        return raw
    clean = sanitize_reason_code(value)
    if not clean or has_unsafe_marker(clean) or _UNSAFE_ACTIVITY_MARKER_RE.search(clean):
        return "long_running_activity_reason"
    return clean[:160]


__all__ = [
    "ADK_LONG_RUNNING_FUNCTION_TOOL_REF",
    "LongRunningActivityAuthorityFlags",
    "LongRunningActivityConfig",
    "LongRunningActivityEvent",
    "LongRunningActivityPolicy",
    "LongRunningActivityReceipt",
    "LongRunningActivityRequest",
    "LongRunningActivityResult",
    "LongRunningActivityStatus",
    "LongRunningSideEffectSurface",
    "duplicate_activity_result",
    "evaluate_long_running_activity",
    "idempotency_conflict_result",
]
