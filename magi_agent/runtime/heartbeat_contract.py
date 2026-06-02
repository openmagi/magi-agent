from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
import hashlib
import json
from math import isfinite
import re
from types import MappingProxyType
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from magi_agent.ops.safety import (
    reject_private_text,
    require_digest,
    require_safe_ref,
    safe_metadata,
)


StaleRunVerdictValue = Literal[
    "healthy",
    "silent_but_within_threshold",
    "inactive_timeout",
    "lease_expired",
    "worker_lost",
    "rollback_required",
    "resume_pending",
    "cancelled",
    "blocked_for_operator",
]
ResumeDecisionValue = Literal[
    "resume_same_session",
    "resume_with_system_note",
    "retry_from_checkpoint",
    "cancel_and_project_failure",
    "block_for_operator",
    "ignore_completed",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=False,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_PUBLIC_ID_PREFIXES = {
    "activity_id": "activity:",
    "active_child_id": "child:",
    "heartbeat_id": "heartbeat:",
    "last_event_id": "event:",
    "last_receipt_id": "activity:",
    "lease_id": "lease:",
    "run_id": "run:",
    "session_key": "sess:",
    "turn_id": "turn:",
    "worker_id": "worker:",
}
_PHASE_LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,80}$")
_PUBLIC_ID_SUFFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_AUTHORITY_SHAPED_REF_RE = re.compile(
    r"^(?:"
    r"provider|tool|capability|permission|browser|db|workspace|memory|mission"
    r"|authority|activation|channel|env|gate2|gate8|k8s|kubernetes|liveauthority"
    r"|model|production|scheduler|traffic|wakeagent"
    r")[:.][A-Za-z0-9_.+-]+$",
    re.IGNORECASE,
)
_HEARTBEAT_RECEIPT_DIGEST_PREFIX = "sha256:heartbeat:"
_ACTIVITY_RECEIPT_DIGEST_PREFIX = "sha256:activity:"
_AUTHORITY_SHAPED_METADATA_KEYS = {
    "activationenabled",
    "activationrequired",
    "authority",
    "browsertool",
    "capability",
    "capabilityscope",
    "channelattached",
    "channeldelivery",
    "channeldeliveryenabled",
    "dbwriteenabled",
    "executionattached",
    "gate2activation",
    "gate8activation",
    "liveauthority",
    "liveschedulerenabled",
    "memoryattached",
    "memorywriteenabled",
    "missionruntimeenabled",
    "modelattached",
    "modelcallenabled",
    "permission",
    "permissiongrant",
    "permissions",
    "productionattached",
    "productionauthority",
    "productionwritesenabled",
    "provider",
    "providerattached",
    "providerauthority",
    "providerauthorityenabled",
    "providerid",
    "providername",
    "providernameref",
    "providernames",
    "providerref",
    "providerrefs",
    "routeattached",
    "runtimeactivation",
    "schedulerattached",
    "schedulerenabled",
    "scriptname",
    "tool",
    "toolattached",
    "toolname",
    "trafficattached",
    "wakeagent",
    "watchdogscript",
    "workspaceattached",
    "workspacemutation",
    "workspacewriteenabled",
}
_PUBLIC_ID_AUTHORITY_PREFIXES = (
    "activation",
    "browser",
    "capability",
    "channel",
    "channeldelivery",
    "db",
    "env",
    "gate2",
    "gate8",
    "heartbeat",
    "k8s",
    "kubernetes",
    "liveauthority",
    "livescheduler",
    "memory",
    "mission",
    "modelcall",
    "modelprovider",
    "permission",
    "production",
    "provider",
    "scheduler",
    "tool",
    "traffic",
    "wakeagent",
    "workspace",
)
_AUTHORITY_VALUE_PREFIXES = (
    "activation",
    "authority",
    "browser",
    "browserautomation",
    "channeldelivery",
    "db",
    "dbwrite",
    "envpatch",
    "gate2",
    "gate8",
    "k8s",
    "kubernetes",
    "liveauthority",
    "livescheduler",
    "memory",
    "memorywrite",
    "mission",
    "missionruntime",
    "modelcall",
    "modelprovider",
    "provider",
    "providerauthority",
    "scheduler",
    "traffic",
    "trafficattachment",
    "trafficattached",
    "wakeagent",
    "workspace",
    "workspacewrite",
)
_AUTHORITY_SHAPED_METADATA_KEY_PREFIXES = (
    "activation",
    "authority",
    "browser",
    "capability",
    "channel",
    "channeldelivery",
    "dbwrite",
    "env",
    "execution",
    "gate2",
    "gate8",
    "k8s",
    "kubernetes",
    "liveauthority",
    "livescheduler",
    "memorywrite",
    "missionruntime",
    "model",
    "permission",
    "production",
    "provider",
    "route",
    "scheduler",
    "script",
    "secret",
    "tool",
    "traffic",
    "wakeagent",
    "watchdog",
    "workspace",
)
_HEARTBEAT_ACTIVITY_TYPES = {"heartbeat", "heartbeat_event", "heartbeat_receipt"}


def heartbeat_receipt_digest(payload: Mapping[str, object]) -> str:
    """Return a canonical mutation digest, not a proof of worker or lease authority."""
    return _typed_digest_payload(
        _canonical_digest_payload(_heartbeat_digest_payload(payload)),
        prefix=_HEARTBEAT_RECEIPT_DIGEST_PREFIX,
    )


def activity_receipt_digest(payload: Mapping[str, object]) -> str:
    """Return a canonical mutation digest, not a proof of worker or lease authority."""
    return _typed_digest_payload(
        _canonical_digest_payload(_activity_digest_payload(payload)),
        prefix=_ACTIVITY_RECEIPT_DIGEST_PREFIX,
    )


class _RuntimeHeartbeatModel(BaseModel):
    model_config = _MODEL_CONFIG

    def __init__(self, **data: object) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise _sanitize_validation_error(exc, title=type(self).__name__) from None

    @classmethod
    def model_validate(cls, obj: object, *args: object, **kwargs: object) -> Self:
        try:
            return super().model_validate(obj, *args, **kwargs)
        except ValidationError as exc:
            raise _sanitize_validation_error(exc, title=cls.__name__) from None

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *args: object,
        **kwargs: object,
    ) -> Self:
        try:
            return super().model_validate_json(json_data, *args, **kwargs)
        except ValidationError as exc:
            raise _sanitize_validation_error(exc, title=cls.__name__) from None

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError(f"model_construct is disabled for {cls.__name__}")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError(f"model_copy update is disabled for {type(self).__name__}")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))

    def copy(
        self,
        *,
        include: object = None,
        exclude: object = None,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        if update or include is not None or exclude is not None:
            raise ValueError(f"copy update/include/exclude is disabled for {type(self).__name__}")
        return self.model_copy(deep=deep)


class RunLease(_RuntimeHeartbeatModel):
    schema_version: Literal["openmagi.runtime.lease.v1"] = Field(
        default="openmagi.runtime.lease.v1",
        alias="schemaVersion",
    )
    run_id: str = Field(alias="runId")
    turn_id: str = Field(alias="turnId")
    session_key: str = Field(alias="sessionKey")
    worker_id: str = Field(alias="workerId")
    lease_id: str = Field(alias="leaseId")
    lease_acquired_at: datetime = Field(alias="leaseAcquiredAt")
    lease_expires_at: datetime = Field(alias="leaseExpiresAt")
    phase: str
    active_boundary: str = Field(alias="activeBoundary")
    authority_scope: str = Field(alias="authorityScope")
    generation: int = Field(ge=0)
    fencing_token: str = Field(alias="fencingToken")

    @field_validator(
        "run_id",
        "turn_id",
        "session_key",
        "worker_id",
        "lease_id",
    )
    @classmethod
    def _validate_public_ids(cls, value: str, info: object) -> str:
        return _safe_public_id_for_field(
            value,
            field_name=getattr(info, "field_name", "runtimeRef"),
        )

    @field_validator("phase")
    @classmethod
    def _validate_phase(cls, value: str) -> str:
        return _safe_phase_label(value, field_name="phase")

    @field_validator(
        "active_boundary",
        "authority_scope",
    )
    @classmethod
    def _validate_safe_refs(cls, value: str, info: object) -> str:
        return _safe_ref(value, field_name=getattr(info, "field_name", "runtimeRef"))

    @field_validator("fencing_token")
    @classmethod
    def _validate_fencing_token(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("generation", mode="before")
    @classmethod
    def _validate_generation(cls, value: object) -> object:
        return _strict_non_negative_int(value, field_name="generation")

    @field_validator("lease_acquired_at", "lease_expires_at")
    @classmethod
    def _validate_aware_datetime(cls, value: datetime, info: object) -> datetime:
        return _require_aware_utc(value, field_name=getattr(info, "field_name", "timestamp"))

    @model_validator(mode="after")
    def _validate_lease_ordering(self) -> Self:
        if self.lease_expires_at <= self.lease_acquired_at:
            raise ValueError("leaseExpiresAt must be after leaseAcquiredAt")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.runtime.lease.public.v1",
            "runId": self.run_id,
            "turnId": self.turn_id,
            "leaseDigest": _digest_text(self.lease_id),
            "workerDigest": _digest_text(self.worker_id),
            "leaseExpiresAt": _json_datetime(self.lease_expires_at),
            "phaseDigest": _digest_text(self.phase),
            "activeBoundaryDigest": _digest_text(self.active_boundary),
            "authorityScopeDigest": _digest_text(self.authority_scope),
            "generation": self.generation,
            "fencingTokenDigest": self.fencing_token,
            "publicSafe": True,
            "liveAuthority": False,
            "trafficAttached": False,
            **_public_non_authority_markers(),
        }


class HeartbeatReceipt(_RuntimeHeartbeatModel):
    schema_version: Literal["openmagi.runtime.heartbeat.receipt.v1"] = Field(
        default="openmagi.runtime.heartbeat.receipt.v1",
        alias="schemaVersion",
    )
    heartbeat_id: str = Field(alias="heartbeatId")
    run_id: str = Field(alias="runId")
    lease_id: str = Field(alias="leaseId")
    sequence: int = Field(ge=0)
    emitted_at: datetime = Field(alias="emittedAt")
    last_activity_at: datetime = Field(alias="lastActivityAt")
    last_activity_receipt_digest: str = Field(alias="lastActivityReceiptDigest")
    last_event_id: str | None = Field(default=None, alias="lastEventId")
    last_receipt_id: str | None = Field(default=None, alias="lastReceiptId")
    phase: str
    active_tool_name: str | None = Field(default=None, alias="activeToolName")
    active_child_id: str | None = Field(default=None, alias="activeChildId")
    pending_approval_ids: tuple[str, ...] = Field(default=(), alias="pendingApprovalIds")
    digest: str
    public_safe: Literal[True] = Field(alias="publicSafe")

    @model_validator(mode="before")
    @classmethod
    def _reject_snake_case_wire_fields(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_alias_bypass(value, field_name="public_safe")
            _reject_alias_bypass(value, field_name="last_activity_receipt_digest")
        return value

    @field_validator(
        "heartbeat_id",
        "run_id",
        "lease_id",
        "last_event_id",
        "last_receipt_id",
        "active_child_id",
    )
    @classmethod
    def _validate_public_ids(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _safe_public_id_for_field(
            value,
            field_name=getattr(info, "field_name", "runtimeRef"),
        )

    @field_validator("phase")
    @classmethod
    def _validate_phase(cls, value: str) -> str:
        return _safe_phase_label(value, field_name="phase")

    @field_validator(
        "active_tool_name",
    )
    @classmethod
    def _validate_safe_refs(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _safe_ref(value, field_name=getattr(info, "field_name", "runtimeRef"))

    @field_validator("pending_approval_ids", mode="before")
    @classmethod
    def _normalize_pending_approvals(cls, value: object) -> tuple[str, ...]:
        return _safe_ref_tuple(
            value,
            field_name="pendingApprovalIds",
            required_prefix="approval:",
        )

    @field_validator("sequence", mode="before")
    @classmethod
    def _validate_sequence(cls, value: object) -> object:
        return _strict_non_negative_int(value, field_name="sequence")

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_heartbeat_receipt_digest(value)

    @field_validator("last_activity_receipt_digest")
    @classmethod
    def _validate_last_activity_receipt_digest(cls, value: str) -> str:
        return _require_activity_receipt_digest(value)

    @field_validator("emitted_at", "last_activity_at")
    @classmethod
    def _validate_aware_datetime(cls, value: datetime, info: object) -> datetime:
        return _require_aware_utc(value, field_name=getattr(info, "field_name", "timestamp"))

    @model_validator(mode="after")
    def _validate_canonical_digest(self) -> Self:
        if self.last_activity_at >= self.emitted_at:
            raise ValueError("lastActivityAt must be before emittedAt")
        expected = heartbeat_receipt_digest(self.model_dump(by_alias=True, mode="json"))
        if self.digest != expected:
            raise ValueError("digest must match canonical heartbeat receipt payload")
        return self

    def public_projection(self) -> dict[str, object]:
        projected: dict[str, object] = {
            "schemaVersion": "openmagi.runtime.heartbeat.receipt.public.v1",
            "heartbeatId": self.heartbeat_id,
            "runId": self.run_id,
            "leaseDigest": _digest_text(self.lease_id),
            "sequence": self.sequence,
            "emittedAt": _json_datetime(self.emitted_at),
            "lastActivityAt": _json_datetime(self.last_activity_at),
            "lastActivityReceiptDigest": self.last_activity_receipt_digest,
            "lastEventDigest": _digest_text(self.last_event_id) if self.last_event_id else None,
            "phaseDigest": _digest_text(self.phase),
            "pendingApprovalDigests": [
                _digest_text(approval_id) for approval_id in self.pending_approval_ids
            ],
            "digest": self.digest,
            "publicSafe": True,
            "liveAuthority": False,
            "trafficAttached": False,
            **_public_non_authority_markers(),
        }
        if self.active_tool_name is not None:
            projected["activeToolDigest"] = _digest_text(self.active_tool_name)
        if self.active_child_id is not None:
            projected["activeChildDigest"] = _digest_text(self.active_child_id)
        return projected


class ActivityReceipt(_RuntimeHeartbeatModel):
    schema_version: Literal["openmagi.runtime.activity.receipt.v1"] = Field(
        default="openmagi.runtime.activity.receipt.v1",
        alias="schemaVersion",
    )
    activity_id: str = Field(alias="activityId")
    run_id: str = Field(alias="runId")
    lease_id: str = Field(alias="leaseId")
    sequence: int = Field(ge=0)
    emitted_at: datetime = Field(alias="emittedAt")
    activity_type: str = Field(alias="activityType")
    activity_ref: str = Field(alias="activityRef")
    metadata: Mapping[str, object] = Field(default_factory=dict)
    digest: str
    public_safe: Literal[True] = Field(alias="publicSafe")

    @model_validator(mode="before")
    @classmethod
    def _reject_snake_case_wire_fields(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_alias_bypass(value, field_name="public_safe")
        return value

    @field_validator(
        "activity_id",
        "run_id",
        "lease_id",
    )
    @classmethod
    def _validate_public_ids(cls, value: str, info: object) -> str:
        return _safe_public_id_for_field(
            value,
            field_name=getattr(info, "field_name", "runtimeRef"),
        )

    @field_validator(
        "activity_type",
    )
    @classmethod
    def _validate_activity_type(cls, value: str) -> str:
        clean = _safe_ref(value, field_name="activityType")
        if clean in _HEARTBEAT_ACTIVITY_TYPES or _is_heartbeat_shaped_ref(clean):
            raise ValueError("activityType must not represent heartbeat activity")
        return clean

    @field_validator("activity_ref")
    @classmethod
    def _validate_activity_ref(cls, value: str) -> str:
        clean = _safe_ref(value, field_name="activityRef")
        if _is_heartbeat_shaped_ref(clean):
            raise ValueError("activityRef must not reference heartbeat receipts")
        return clean

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _immutable_safe_metadata(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return _mutable_json_mapping(value)

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_activity_receipt_digest(value)

    @field_validator("sequence", mode="before")
    @classmethod
    def _validate_sequence(cls, value: object) -> object:
        return _strict_non_negative_int(value, field_name="sequence")

    @field_validator("emitted_at")
    @classmethod
    def _validate_aware_datetime(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, field_name="emittedAt")

    @model_validator(mode="after")
    def _validate_canonical_digest(self) -> Self:
        expected = activity_receipt_digest(self.model_dump(by_alias=True, mode="json"))
        if self.digest != expected:
            raise ValueError("digest must match canonical activity receipt payload")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.runtime.activity.receipt.public.v1",
            "activityId": self.activity_id,
            "runId": self.run_id,
            "leaseDigest": _digest_text(self.lease_id),
            "sequence": self.sequence,
            "emittedAt": _json_datetime(self.emitted_at),
            "activityTypeDigest": _digest_text(self.activity_type),
            "activityRefDigest": _digest_text(self.activity_ref),
            "digest": self.digest,
            "publicSafe": True,
            "liveAuthority": False,
            "trafficAttached": False,
            **_public_non_authority_markers(),
        }


class StaleRunVerdict(_RuntimeHeartbeatModel):
    schema_version: Literal["openmagi.runtime.stale_run.verdict.v1"] = Field(
        default="openmagi.runtime.stale_run.verdict.v1",
        alias="schemaVersion",
    )
    verdict: StaleRunVerdictValue
    run_id: str = Field(alias="runId")
    checked_at: datetime = Field(alias="checkedAt")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    heartbeat_digest: str | None = Field(default=None, alias="heartbeatDigest")
    activity_digest: str | None = Field(default=None, alias="activityDigest")
    lease_digest: str | None = Field(default=None, alias="leaseDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        return _safe_public_id_for_field(value, field_name="run_id")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _normalize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return _safe_ref_tuple(value, field_name="reasonCodes")

    @field_validator("heartbeat_digest")
    @classmethod
    def _validate_optional_heartbeat_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_heartbeat_receipt_digest(value)

    @field_validator("activity_digest")
    @classmethod
    def _validate_optional_activity_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_activity_receipt_digest(value)

    @field_validator("lease_digest")
    @classmethod
    def _validate_optional_lease_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _immutable_safe_metadata(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return _mutable_json_mapping(value)

    @field_validator("checked_at")
    @classmethod
    def _validate_aware_datetime(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, field_name="checkedAt")

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.runtime.stale_run.verdict.public.v1",
            "verdict": self.verdict,
            "runId": self.run_id,
            "checkedAt": _json_datetime(self.checked_at),
            "reasonCodeDigests": [_digest_text(reason) for reason in self.reason_codes],
            "heartbeatDigest": self.heartbeat_digest,
            "activityDigest": self.activity_digest,
            "leaseDigest": self.lease_digest,
            "publicSafe": True,
            "liveAuthority": False,
            "trafficAttached": False,
            **_public_non_authority_markers(),
        }


class ResumeDecision(_RuntimeHeartbeatModel):
    schema_version: Literal["openmagi.runtime.resume.decision.v1"] = Field(
        default="openmagi.runtime.resume.decision.v1",
        alias="schemaVersion",
    )
    decision: ResumeDecisionValue
    run_id: str = Field(alias="runId")
    decided_at: datetime = Field(alias="decidedAt")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    checkpoint_digest: str | None = Field(default=None, alias="checkpointDigest")
    verdict_digest: str | None = Field(default=None, alias="verdictDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        return _safe_public_id_for_field(value, field_name="run_id")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _normalize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return _safe_ref_tuple(value, field_name="reasonCodes")

    @field_validator("checkpoint_digest", "verdict_digest")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _immutable_safe_metadata(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return _mutable_json_mapping(value)

    @field_validator("decided_at")
    @classmethod
    def _validate_aware_datetime(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, field_name="decidedAt")

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.runtime.resume.decision.public.v1",
            "decision": self.decision,
            "runId": self.run_id,
            "decidedAt": _json_datetime(self.decided_at),
            "reasonCodeDigests": [_digest_text(reason) for reason in self.reason_codes],
            "checkpointDigest": self.checkpoint_digest,
            "verdictDigest": self.verdict_digest,
            "publicSafe": True,
            "liveAuthority": False,
            "trafficAttached": False,
            **_public_non_authority_markers(),
        }


def _safe_ref_tuple(
    value: object,
    *,
    field_name: str,
    required_prefix: str | None = None,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or isinstance(value, Mapping) or not isinstance(value, Iterable):
        raise ValueError(f"{field_name} must be an array of safe refs")
    refs: list[str] = []
    for ref in tuple(value):
        if not isinstance(ref, str):
            raise ValueError(f"{field_name} must contain only safe refs")
        clean = _safe_ref(ref, field_name=field_name)
        if required_prefix is not None:
            _require_ref_prefix(clean, field_name=field_name, required_prefix=required_prefix)
        refs.append(clean)
    return tuple(refs)


def _safe_public_id_for_field(value: str, *, field_name: str) -> str:
    clean = _safe_ref(value, field_name=field_name)
    return _require_ref_prefix(
        clean,
        field_name=field_name,
        required_prefix=_PUBLIC_ID_PREFIXES[field_name],
    )


def _require_ref_prefix(value: str, *, field_name: str, required_prefix: str) -> str:
    if not value.startswith(required_prefix) or len(value) == len(required_prefix):
        raise ValueError(f"{field_name} must use {required_prefix} public id prefix")
    suffix = value.removeprefix(required_prefix)
    if _PUBLIC_ID_SUFFIX_RE.fullmatch(suffix) is None or _is_authority_shaped_public_id_suffix(
        suffix
    ):
        raise ValueError(f"{field_name} must use a non-authority public id suffix")
    return value


def _safe_phase_label(value: str, *, field_name: str) -> str:
    clean = value.strip()
    reject_private_text(clean, field_name=field_name)
    if not _PHASE_LABEL_RE.fullmatch(clean) or _is_authority_shaped_ref(clean):
        raise ValueError(f"{field_name} must be a non-authority public label")
    return clean


def _safe_ref(value: str, *, field_name: str) -> str:
    clean = value.strip()
    reject_private_text(clean, field_name=field_name)
    if _is_authority_shaped_ref(clean):
        raise ValueError(f"{field_name} must not be authority-shaped")
    return require_safe_ref(clean, field_name=field_name)


def _strict_non_negative_int(value: object, *, field_name: str) -> object:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _is_authority_shaped_ref(value: str) -> bool:
    clean = value.strip()
    return _AUTHORITY_SHAPED_REF_RE.fullmatch(clean) is not None or _normalized_starts_with(
        clean,
        _AUTHORITY_VALUE_PREFIXES,
    )


def _is_authority_shaped_public_id_suffix(value: str) -> bool:
    return _normalized_starts_with(value, _PUBLIC_ID_AUTHORITY_PREFIXES)


def _is_heartbeat_shaped_ref(value: str) -> bool:
    return _normalize_marker(value).startswith("heartbeat")


def _normalized_starts_with(value: str, prefixes: tuple[str, ...]) -> bool:
    normalized = _normalize_marker(value)
    return any(normalized.startswith(prefix) for prefix in prefixes)


def _normalize_marker(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _reject_alias_bypass(payload: Mapping[str, object], *, field_name: str) -> None:
    if field_name in payload:
        raise ValueError(f"{field_name} must use its explicit wire alias")


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _public_non_authority_markers() -> dict[str, object]:
    return {
        "trustedLeaseAuthority": False,
        "authorityProof": "requires_trusted_lease_store",
    }


def _digest_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _typed_digest_payload(payload: Mapping[str, object], *, prefix: str) -> str:
    # Deterministic correlation digests are not secrecy boundaries for low-entropy values.
    return prefix + _digest_payload(payload).removeprefix("sha256:")


def _require_heartbeat_receipt_digest(value: str) -> str:
    return _require_typed_receipt_digest(value, prefix=_HEARTBEAT_RECEIPT_DIGEST_PREFIX)


def _require_activity_receipt_digest(value: str) -> str:
    return _require_typed_receipt_digest(value, prefix=_ACTIVITY_RECEIPT_DIGEST_PREFIX)


def _require_typed_receipt_digest(value: str, *, prefix: str) -> str:
    if not value.startswith(prefix):
        raise ValueError(f"receipt fields must use {prefix}<sha256> digests")
    suffix = value.removeprefix(prefix)
    if not re.fullmatch(r"[0-9a-f]{64}", suffix):
        raise ValueError(f"receipt fields must use {prefix}<sha256> digests")
    return value


def _heartbeat_digest_payload(payload: Mapping[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    normalized.setdefault("schemaVersion", "openmagi.runtime.heartbeat.receipt.v1")
    normalized.setdefault("lastEventId", None)
    normalized.setdefault("lastReceiptId", None)
    normalized.setdefault("activeToolName", None)
    normalized.setdefault("activeChildId", None)
    normalized.setdefault("pendingApprovalIds", [])
    return normalized


def _activity_digest_payload(payload: Mapping[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    normalized.setdefault("schemaVersion", "openmagi.runtime.activity.receipt.v1")
    normalized.setdefault("metadata", {})
    return normalized


def _canonical_digest_payload(payload: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _canonical_digest_value(value, key=str(key))
        for key, value in sorted(payload.items(), key=lambda pair: str(pair[0]))
        if key != "digest"
    }


def _canonical_digest_value(value: object, *, key: str) -> object:
    if isinstance(value, datetime):
        raise ValueError("digest helpers require JSON-safe values, not datetime objects")
    if isinstance(value, Mapping):
        return {
            str(nested_key): _canonical_digest_value(item, key=str(nested_key))
            for nested_key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, tuple | list):
        return [_canonical_digest_value(item, key=key) for item in value]
    if isinstance(value, str):
        if key in {"emittedAt", "lastActivityAt"}:
            return _normalize_datetime_string_for_digest(value)
        return value
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("digest helpers require JSON-safe finite numeric values")
        return value
    raise ValueError("digest helpers require JSON-safe values")


def _immutable_safe_metadata(value: Mapping[str, object]) -> Mapping[str, object]:
    for key in value:
        if not isinstance(key, str):
            raise ValueError("metadata keys must be strings")
        _reject_authority_shaped_metadata_key(key)
    _ = safe_metadata({key: True for key in value})
    return MappingProxyType(
        {
            str(key): _immutable_safe_metadata_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    )


def _immutable_safe_metadata_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _immutable_safe_metadata(value)
    if isinstance(value, tuple | list):
        return tuple(_immutable_safe_metadata_value(item) for item in value)
    if isinstance(value, str):
        _reject_authority_shaped_metadata_value(value)
    return safe_metadata({"value": value})["value"]


def _reject_authority_shaped_metadata_key(value: str) -> None:
    normalized = _normalize_marker(value)
    if normalized in _AUTHORITY_SHAPED_METADATA_KEYS or _normalized_starts_with(
        value,
        _AUTHORITY_SHAPED_METADATA_KEY_PREFIXES,
    ):
        raise ValueError("metadata keys must not be authority-shaped")


def _reject_authority_shaped_metadata_value(value: str) -> None:
    if _is_authority_shaped_ref(value):
        raise ValueError("metadata values must not be authority-shaped")


def _mutable_json_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _mutable_json_value(item)
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }


def _mutable_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _mutable_json_mapping(value)
    if isinstance(value, tuple | list):
        return [_mutable_json_value(item) for item in value]
    return value


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _normalize_datetime_string_for_digest(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return value
    return _json_datetime(parsed)


def _json_datetime(value: datetime) -> str:
    dumped = _require_aware_utc(value, field_name="timestamp").isoformat()
    if dumped.endswith("+00:00"):
        return dumped[:-6] + "Z"
    return dumped


def _sanitize_validation_error(exc: ValidationError, *, title: str) -> ValidationError:
    sanitized_errors: list[dict[str, object]] = []
    for error in exc.errors(include_input=False):
        _ = error
        sanitized_errors.append(
            {
                "type": "value_error",
                "loc": ("runtimeHeartbeatContract",),
                "input": None,
                "ctx": {
                    "error": ValueError(
                        "runtime contract validation failed: sha256 digest or safety check failed"
                    )
                },
            }
        )
    return ValidationError.from_exception_data(title, sanitized_errors)
