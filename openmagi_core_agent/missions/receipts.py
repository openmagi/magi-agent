from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


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
_SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$", re.IGNORECASE)
_SAFE_REF_RE = re.compile(
    r"^(?:mission|mission-transition|run|turn|evidence|approval|policy|"
    r"policy-snapshot|receipt|ref|sha256):"
    r"[A-Za-z0-9_.:/=-]{1,191}$"
)
_SAFE_REDACTION_TOKEN_RE = re.compile(r"^\[[a-z0-9_.:-]*redacted[a-z0-9_.:-]*\]$")
_UNSAFE_REF_MARKER_RE = re.compile(
    r"raw[-_:]?(?:source|output|result|text|prompt|transcript|tool|log|args|"
    r"policy|snapshot|config|control|metadata|selector|recipe|authority|instruction)|"
    r"private[-_:]?(?:memory|mission|payload|path)|tool[-_:]?log|child[-_:]?prompt|"
    r"hidden[-_:]?reasoning|authorization|cookie|session|token|secret|credential|"
    r"private[-_:]?key|api[-_:]?key|bearer|connector[-_:]?token|password|"
    r"policy[-_:]?snapshot[-_:]?(?:text|prompt|payload|raw)|control[-_:]?metadata|"
    r"selector[-_:]?payload|recipe[-_:]?prompt|authority[-_:]?payload|"
    r"instruction[-_:]?payload",
    re.IGNORECASE,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[a-z]-[A-Za-z0-9._-]{8,}|"
    r"AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users(?:/[^,\s\"']*)?|/home(?:/[^,\s\"']*)?|"
    r"/workspace(?:/[^,\s\"']*)?|/data/bots(?:/[^,\s\"']*)?|"
    r"/var/lib/kubelet(?:/[^,\s\"']*)?|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|private[_ -]?reasoning|"
    r"authorization|cookie|set-cookie",
    re.IGNORECASE,
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


def sha256_ref(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_ref(encoded)


def sanitize_public_ref(value: str) -> str:
    raw = str(value)
    path_sanitized = sanitize_public_text(raw)
    if path_sanitized.startswith("[redacted") and _SAFE_REDACTION_TOKEN_RE.fullmatch(
        path_sanitized,
    ):
        return path_sanitized
    clean = path_sanitized.strip()
    if _SAFE_REDACTION_TOKEN_RE.fullmatch(clean):
        return clean
    if _SAFE_REF_RE.fullmatch(clean) and not has_unsafe_marker(clean):
        return clean[:220]
    if _SAFE_ID_RE.fullmatch(clean) and not has_unsafe_marker(clean):
        return clean[:160]
    return "ref:" + sha256_ref(raw).removeprefix("sha256:")


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


def sanitize_public_text(value: str) -> str:
    safe_lines = [
        line
        for line in str(value).splitlines()
        if line.strip() and not _RAW_PRIVATE_LINE_RE.search(line)
    ]
    clean = "\n".join(safe_lines)
    clean = _SECRET_TEXT_RE.sub("[redacted]", clean)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean.strip()


def sanitize_reason_code(value: str) -> str:
    raw = str(value).strip().lower().replace(" ", "_")
    if raw in _SAFE_REASON_CODES:
        return raw
    if raw and all(char.isalnum() or char in "_:-." for char in raw) and not has_unsafe_marker(raw):
        return raw[:160]
    clean = sanitize_public_text(value).strip().lower().replace(" ", "_")
    if not clean or has_unsafe_marker(clean):
        return "mission_lifecycle_reason"
    return clean[:160]


def has_unsafe_marker(value: str) -> bool:
    return _UNSAFE_REF_MARKER_RE.search(value) is not None


def string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(str(item) for item in value)
    return (str(value),)


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
