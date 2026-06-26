from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.runtime import receipt_redaction as _kernel
from magi_agent.runtime.receipt_redaction import (
    _SAFE_ID_RE,
    _SAFE_REDACTION_TOKEN_RE,
    _SECRET_TEXT_RE,
    _PRIVATE_PATH_RE,
    _RAW_PRIVATE_LINE_RE,
    _UNSAFE_REF_MARKER_RE,
    canonical_digest,
    has_unsafe_marker,
    sanitize_public_text,
    sha256_ref,
    string_tuple,
)


MissionLifecycleState: TypeAlias = Literal[
    "draft",
    "pending_approval",
    "scheduled",
    "running",
    "paused",
    "blocked",
    "completed",
    "failed",
    "cancelled",
]
MissionTransitionStatus: TypeAlias = Literal[
    "disabled",
    "blocked",
    "approval_required",
    "applied_local_fake",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
# Mission ref namespace. Secret/path scrubbing lives in the shared kernel
# (runtime.receipt_redaction); only this allowlist of pass-through ref prefixes
# is mission-specific.
_SAFE_REF_RE = re.compile(
    r"^(?:mission|mission-transition|run|turn|evidence|approval|policy|"
    r"policy-snapshot|receipt|ref|sha256):"
    r"[A-Za-z0-9_.:/=-]{1,191}$"
)
# Re-exported shared scrubbing primitives kept referenced so the kernel-identity
# invariant (test_receipt_redaction_kernel) holds for this module's namespace.
_REEXPORTED = (
    _SAFE_ID_RE,
    _SAFE_REDACTION_TOKEN_RE,
    _SECRET_TEXT_RE,
    _PRIVATE_PATH_RE,
    _RAW_PRIVATE_LINE_RE,
    _UNSAFE_REF_MARKER_RE,
)
_SAFE_REASON_CODES = frozenset(
    {
        "mission_lifecycle_disabled",
        "missing_mission_lifecycle_policy",
        "private_mission_payload_denied",
        "mission_transition_denied",
        "mission_transition_not_allowed_by_policy",
        "missing_mission_transition_evidence",
        "missing_mission_transition_approval",
        "local_fake_mission_transition_disabled",
        "mission_policy_disallows_local_fake_transition",
        "local_fake_mission_transition_receipt",
        "mission_lifecycle_reason",
    }
)


class MissionLifecycleAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    production_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="productionMutationEnabled",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    cron_mutation_enabled: Literal[False] = Field(default=False, alias="cronMutationEnabled")
    background_execution_enabled: Literal[False] = Field(
        default=False,
        alias="backgroundExecutionEnabled",
    )
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

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field in cls.model_fields.values():
            payload[field.alias or ""] = False
        for field_name in cls.model_fields:
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
        "production_mutation_enabled",
        "traffic_attached",
        "scheduler_attached",
        "cron_mutation_enabled",
        "background_execution_enabled",
        "tool_host_dispatch_enabled",
        "channel_delivery_enabled",
        "workspace_mutation_enabled",
        "memory_mutation_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class MissionTransitionReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["missionTransitionReceipt.v1"] = Field(
        default="missionTransitionReceipt.v1",
        alias="schemaVersion",
    )
    receipt_id: str = Field(alias="receiptId")
    receipt_digest: str = Field(alias="receiptDigest")
    mission_id: str = Field(alias="missionId")
    run_id: str = Field(alias="runId")
    turn_id: str = Field(alias="turnId")
    from_state: MissionLifecycleState = Field(alias="fromState")
    to_state: MissionLifecycleState = Field(alias="toState")
    status: MissionTransitionStatus
    transition_allowed: bool = Field(default=False, alias="transitionAllowed")
    local_fake_transition_recorded: bool = Field(
        default=False,
        alias="localFakeTransitionRecorded",
    )
    local_test_only: bool = Field(default=False, alias="localTestOnly")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    policy_snapshot_ref: str = Field(alias="policySnapshotRef")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    reason_digest: str = Field(alias="reasonDigest")
    production_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="productionMutationEnabled",
    )
    provider_call_attempted: Literal[False] = Field(default=False, alias="providerCallAttempted")
    filesystem_mutation_attempted: Literal[False] = Field(
        default=False,
        alias="filesystemMutationAttempted",
    )
    database_mutation_attempted: Literal[False] = Field(
        default=False,
        alias="databaseMutationAttempted",
    )
    network_call_attempted: Literal[False] = Field(default=False, alias="networkCallAttempted")
    authority_flags: MissionLifecycleAuthorityFlags = Field(
        default_factory=MissionLifecycleAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["productionMutationEnabled"] = False
        payload["providerCallAttempted"] = False
        payload["filesystemMutationAttempted"] = False
        payload["databaseMutationAttempted"] = False
        payload["networkCallAttempted"] = False
        payload["authorityFlags"] = MissionLifecycleAuthorityFlags()
        for field_name in (
            "production_mutation_enabled",
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
        "receipt_digest",
        "mission_id",
        "run_id",
        "turn_id",
        "policy_snapshot_ref",
        mode="before",
    )
    @classmethod
    def _sanitize_ref_fields(cls, value: object) -> str:
        return sanitize_public_ref(str(value or "mission:unspecified"))

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _sanitize_evidence_refs(cls, value: object) -> tuple[str, ...]:
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
        return tuple(sanitize_reason_code(item) for item in string_tuple(value))

    @field_serializer(
        "production_mutation_enabled",
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
            "missionId": sanitize_public_ref(self.mission_id),
            "runId": sanitize_public_ref(self.run_id),
            "turnId": sanitize_public_ref(self.turn_id),
            "fromState": self.from_state,
            "toState": self.to_state,
            "status": self.status,
            "transitionAllowed": self.transition_allowed,
            "localFakeTransitionRecorded": self.local_fake_transition_recorded,
            "localTestOnly": self.local_test_only,
            "policySnapshotDigest": sanitize_public_ref(self.policy_snapshot_digest),
            "policySnapshotRef": sanitize_public_ref(self.policy_snapshot_ref),
            "evidenceRefs": [sanitize_public_ref(ref) for ref in self.evidence_refs],
            "approvalRef": (
                None if self.approval_ref is None else sanitize_public_ref(self.approval_ref)
            ),
            "reasonCodes": [sanitize_reason_code(code) for code in self.reason_codes],
            "reasonDigest": sanitize_public_ref(self.reason_digest),
            "productionMutationEnabled": False,
            "providerCallAttempted": False,
            "filesystemMutationAttempted": False,
            "databaseMutationAttempted": False,
            "networkCallAttempted": False,
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


def sanitize_public_ref(value: str) -> str:
    return _kernel.sanitize_public_ref(value, safe_ref_re=_SAFE_REF_RE)


def sanitize_public_id(value: str, *, prefix: str) -> str:
    clean = sanitize_public_text(str(value)).strip()
    if (
        clean != str(value).strip()
        or clean.startswith("[")
        or not _SAFE_ID_RE.fullmatch(clean)
        or has_unsafe_marker(clean)
    ):
        return f"{prefix}:" + sha256_ref(str(value)).removeprefix("sha256:")
    return clean[:160]


def sanitize_reason_code(value: str) -> str:
    return _kernel.sanitize_reason_code(
        value,
        default="mission_lifecycle_reason",
        safe_codes=_SAFE_REASON_CODES,
    )


__all__ = [
    "MissionLifecycleAuthorityFlags",
    "MissionLifecycleState",
    "MissionTransitionReceipt",
    "MissionTransitionStatus",
    "canonical_digest",
    "has_unsafe_marker",
    "sanitize_public_id",
    "sanitize_public_ref",
    "sanitize_public_text",
    "sanitize_reason_code",
    "sha256_ref",
    "string_tuple",
]
