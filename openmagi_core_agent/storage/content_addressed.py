from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
import math
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from .durable_store import DurableRecord, DurableStoreSafetyError


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@+-]{1,160}$")
_SAFE_METADATA_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:@+-]{0,120}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,40}:[A-Za-z0-9_.:@+-]{1,120}$")
_SAFE_ENUM_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,40}$")
_UNSAFE_TEXT_RE = re.compile(
    r"authorization|bearer|cookie|session[_-]?key|connector[_-]?token|"
    r"secret|password|credential|private[_-]?key|api[_-]?key|token|"
    r"raw[_ -]?(?:prompt|output|tool|child|transcript|log)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|"
    r"/Users/|/home/|/workspace/|/data/bots/|/var/lib/kubelet/|"
    r"data:|inline:|blob:",
    re.IGNORECASE,
)
_UNSAFE_KEY_RE = re.compile(
    r"raw|prompt|output|authorization|auth|cookie|session[_-]?key|connector[_-]?token|"
    r"secret|password|credential|private[_-]?key|api[_-]?key|token",
    re.IGNORECASE,
)


class _ContentModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for content-addressed records")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


class ContentAddressedJSONRecord(_ContentModel):
    schema_version: Literal["openmagi.content_addressed_json.v1"] = Field(
        default="openmagi.content_addressed_json.v1",
        alias="schemaVersion",
    )
    record_kind: str = Field(alias="recordKind")
    payload: Mapping[str, object]
    content_digest: str = Field(alias="contentDigest")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        alias="createdAt",
    )

    @model_validator(mode="before")
    @classmethod
    def _fill_digest(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        if "contentDigest" not in payload and "content_digest" not in payload:
            payload["contentDigest"] = content_digest_for_payload(
                payload.get("recordKind") or payload.get("record_kind"),
                payload.get("payload"),
            )
        return payload

    @model_validator(mode="after")
    def _validate_content_digest_matches_payload(self) -> Self:
        expected = content_digest_for_payload(self.record_kind, self.payload)
        if self.content_digest != expected:
            raise DurableStoreSafetyError("content digest must match canonical payload")
        return self

    @field_validator("record_kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        return _require_safe_enum(value, field_name="recordKind")

    @field_validator("payload")
    @classmethod
    def _validate_payload(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        _assert_safe_payload(value)
        return dict(value)

    @field_validator("content_digest", "policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value)

    def storage_payload(self) -> dict[str, object]:
        validated = type(self).model_validate(self.model_dump(by_alias=True, mode="json"))
        return validated.model_dump(by_alias=True, mode="json")

    def to_durable_record(self, *, collection: str, record_id: str) -> DurableRecord:
        return DurableRecord(
            collection=collection,
            recordId=record_id,
            contentDigest=self.content_digest,
            policySnapshotDigest=self.policy_snapshot_digest,
            metadata={
                "contentRecordKind": f"kind:{self.record_kind}",
                "contentRecordDigest": self.content_digest,
            },
        )


class StoredTurnCheckpoint(_ContentModel):
    schema_version: Literal["openmagi.turn_checkpoint.storage.v1"] = Field(
        default="openmagi.turn_checkpoint.storage.v1",
        alias="schemaVersion",
    )
    run_id: str = Field(alias="runId")
    turn_id: str = Field(alias="turnId")
    checkpoint_id: str = Field(alias="checkpointId")
    state_record_digest: str = Field(alias="stateRecordDigest")
    parent_ledger_digest: str = Field(alias="parentLedgerDigest")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    context_projection_digest: str = Field(alias="contextProjectionDigest")
    replay_observation_digest: str | None = Field(default=None, alias="replayObservationDigest")
    fork_parent_checkpoint_digest: str | None = Field(
        default=None,
        alias="forkParentCheckpointDigest",
    )
    replay_allowed: StrictBool = Field(default=True, alias="replayAllowed")
    fork_allowed: StrictBool = Field(default=True, alias="forkAllowed")
    side_effects_allowed: Literal[False] = Field(default=False, alias="sideEffectsAllowed")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        alias="createdAt",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_no_side_effects(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["sideEffectsAllowed"] = False
        payload.pop("side_effects_allowed", None)
        return payload

    @field_validator("run_id", "turn_id", "checkpoint_id")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        if not _SAFE_ID_RE.fullmatch(value):
            raise DurableStoreSafetyError("checkpoint identifiers must be safe refs")
        _assert_safe_text(value)
        return value

    @field_validator(
        "state_record_digest",
        "parent_ledger_digest",
        "policy_snapshot_digest",
        "context_projection_digest",
        "replay_observation_digest",
        "fork_parent_checkpoint_digest",
    )
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_digest(value)

    @property
    def checkpoint_digest(self) -> str:
        return _digest_json(self.storage_payload())

    def storage_payload(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")

    def content_record(self) -> ContentAddressedJSONRecord:
        return ContentAddressedJSONRecord(
            recordKind="turn_checkpoint",
            payload={
                "schemaVersion": self.schema_version,
                "runId": self.run_id,
                "turnId": self.turn_id,
                "checkpointId": self.checkpoint_id,
                "stateRecordDigest": self.state_record_digest,
                "parentLedgerDigest": self.parent_ledger_digest,
                "policySnapshotDigest": self.policy_snapshot_digest,
                "contextProjectionDigest": self.context_projection_digest,
                "replayObservationDigest": self.replay_observation_digest,
                "forkParentCheckpointDigest": self.fork_parent_checkpoint_digest,
                "replayAllowed": self.replay_allowed,
                "forkAllowed": self.fork_allowed,
                "sideEffectsAllowed": False,
            },
            policySnapshotDigest=self.policy_snapshot_digest,
        )

    def replay_decision(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.turn_checkpoint.replay_decision.v1",
            "checkpointDigest": self.checkpoint_digest,
            "replayAllowed": self.replay_allowed,
            "forkAllowed": self.fork_allowed,
            "sideEffectsAllowed": False,
        }

    def fork_decision(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.turn_checkpoint.fork_decision.v1",
            "checkpointDigest": self.checkpoint_digest,
            "forkAllowed": self.fork_allowed,
            "sideEffectsAllowed": False,
            "parentLedgerDigest": self.parent_ledger_digest,
            "policySnapshotDigest": self.policy_snapshot_digest,
        }


def content_digest_for_payload(record_kind: object, payload: object) -> str:
    if not isinstance(record_kind, str):
        raise DurableStoreSafetyError("recordKind is required for content digest")
    if not isinstance(payload, Mapping):
        raise DurableStoreSafetyError("content-addressed payload must be a JSON object")
    encoded = json.dumps(
        {
            "schemaVersion": "openmagi.content_addressed_json.v1",
            "recordKind": record_kind,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _digest_json(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _require_digest(value: str) -> str:
    if not _DIGEST_RE.fullmatch(value):
        raise DurableStoreSafetyError("digest fields must be sha256 digests")
    return value


def _require_safe_enum(value: str, *, field_name: str) -> str:
    if _SAFE_ENUM_RE.fullmatch(value) is None:
        raise DurableStoreSafetyError(f"{field_name} must be a safe enum")
    return value


def _assert_safe_payload(value: Mapping[str, object], *, prefix: str = "") -> None:
    for key, nested in value.items():
        key_text = f"{prefix}.{key}" if prefix else str(key)
        if not _SAFE_METADATA_KEY_RE.fullmatch(str(key)):
            raise DurableStoreSafetyError("content-addressed payload keys must be safe refs")
        _assert_safe_text(key_text)
        if _UNSAFE_KEY_RE.search(key_text) or _UNSAFE_KEY_RE.search(_compact_key(key_text)):
            raise DurableStoreSafetyError("content-addressed payload cannot expose raw or sensitive data")
        if isinstance(nested, Mapping):
            _assert_safe_payload(nested, prefix=key_text)
        elif isinstance(nested, list | tuple):
            _assert_safe_sequence(nested, prefix=key_text)
        elif isinstance(nested, str):
            _assert_safe_scalar_string(nested)
        elif nested is None or isinstance(nested, bool | int):
            continue
        elif isinstance(nested, float):
            if not math.isfinite(nested):
                raise DurableStoreSafetyError("content-addressed payload values must be finite JSON numbers")
        else:
            raise DurableStoreSafetyError("content-addressed payload values must be JSON-safe")


def _assert_safe_sequence(value: list[object] | tuple[object, ...], *, prefix: str) -> None:
    for nested in value:
        if isinstance(nested, Mapping):
            _assert_safe_payload(nested, prefix=prefix)
        elif isinstance(nested, list | tuple):
            _assert_safe_sequence(nested, prefix=prefix)
        elif isinstance(nested, str):
            _assert_safe_scalar_string(nested)
        elif nested is None or isinstance(nested, bool | int):
            continue
        elif isinstance(nested, float):
            if not math.isfinite(nested):
                raise DurableStoreSafetyError("content-addressed payload values must be finite JSON numbers")
        else:
            raise DurableStoreSafetyError("content-addressed payload values must be JSON-safe")


def _assert_safe_scalar_string(value: str) -> None:
    _assert_safe_text(value)
    if (
        _DIGEST_RE.fullmatch(value)
        or _SAFE_REF_RE.fullmatch(value)
        or _SAFE_ENUM_RE.fullmatch(value)
    ):
        return
    raise DurableStoreSafetyError("content-addressed payload strings must be safe refs or digests")


def _assert_safe_text(value: str) -> None:
    if "\\" in value or value.startswith(("~", ".")) or _UNSAFE_TEXT_RE.search(value):
        raise DurableStoreSafetyError("content-addressed payload cannot expose raw or sensitive data")


def _compact_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


__all__ = [
    "ContentAddressedJSONRecord",
    "StoredTurnCheckpoint",
    "content_digest_for_payload",
]
