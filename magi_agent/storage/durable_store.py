from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Literal, Self
from urllib.parse import unquote_plus

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:/@+-]{1,160}$")
_SAFE_REF_VALUE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,40}:[A-Za-z0-9_.:@+-]{1,120}$")
_SAFE_ENUM_VALUE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,40}$")
_UNSAFE_KEY_RE = re.compile(
    r"raw|prompt|output|authorization|auth|cookie|session[_-]?key|connector[_-]?token|"
    r"secret|password|credential|private[_-]?key|api[_-]?key|token",
    re.IGNORECASE,
)
_UNSAFE_TEXT_RE = re.compile(
    r"authorization\s*:|bearer\s+|cookie\s*:|set-cookie\s*:|sid=|"
    r"(?:password|api[_-]?key|auth[_-]?key|session[_-]?key|private[_-]?key|"
    r"connector[_-]?token|secret|credential|token|signature)\s*[:=]|"
    r"x-amz-signature|x-goog-signature|sig=|signed[_-]?url|"
    r"\bsk-[A-Za-z0-9._-]+|gh[opusr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|AKIA[0-9A-Z]{8,}|"
    r"raw[_ -]?(?:prompt|output|tool|child|transcript|log)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|"
    r"/Users/|/home/|/workspace/|/data/bots/|/var/lib/kubelet/",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:^|/)(?:Users|home|workspace|data/bots|var/lib/kubelet)(?:/|$)|"
    r"pvc-[A-Za-z0-9-]+|"
    r"(?:^|/)(?:\.ssh|\.kube|\.aws|\.config)(?:/|$)",
    re.IGNORECASE,
)


class DurableStoreSafetyError(ValueError):
    """Raised when a durable-store contract would persist unsafe data."""


class DurableStoreSchemaVersion:
    CURRENT = "openmagi.durable_store.v1"


RUNTIME_METADATA_COLLECTIONS: tuple[str, ...] = (
    "sessions",
    "checkpoints",
    "replay_fork_lineage",
    "receipts",
    "evidence_ledger_index",
    "job_queue",
    "policy_snapshot_refs",
    "artifact_index",
    "eval_observations",
    "delivery_action_receipts",
    "credential_lease_metadata",
    # WS3 PR3a: content-free index for the durable plan/todo ledger. The todo
    # text itself lives in the workspace JSONL; this collection only holds
    # digests + safe int/ref metadata for WS1 StartupRecoverySweep discovery.
    "plan_ledger",
)


class DurableStoreKind(str):
    @classmethod
    def model_validate(cls, value: object) -> Literal["memory", "sqlite"]:
        if value in {"memory", "sqlite"}:
            return value  # type: ignore[return-value]
        if value == "postgres":
            raise DurableStoreSafetyError("hosted adapter is unavailable in the Python scaffold")
        raise DurableStoreSafetyError("unsupported durable store kind")


class DurableStoreConfig(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["memory", "sqlite"]
    sqlite_path: Path | None = Field(default=None, alias="sqlitePath")
    artifact_store: Literal["filesystem", "object"] = Field(alias="artifactStore")
    artifact_path: Path | None = Field(default=None, alias="artifactPath")
    export_path: Path | None = Field(default=None, alias="exportPath")
    sqlite_wal: bool = Field(default=True, alias="sqliteWal")
    sqlite_busy_timeout_ms: int = Field(default=5000, alias="sqliteBusyTimeoutMs", ge=0, le=60000)
    sqlite_multi_writer_allowed: Literal[False] = Field(
        default=False,
        alias="sqliteMultiWriterAllowed",
    )
    postgres_dsn_ref: str | None = Field(default=None, alias="postgresDsnRef")
    hosted_sync_required: Literal[False] = Field(default=False, alias="hostedSyncRequired")
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )

    @model_validator(mode="after")
    def _validate_sqlite_path(self) -> Self:
        if self.kind == "sqlite" and self.sqlite_path is None:
            raise DurableStoreSafetyError("sqlite durable store requires sqlitePath")
        return self

    @model_validator(mode="before")
    @classmethod
    def _force_default_off(cls, value: object) -> dict[str, object]:
        if isinstance(value, Mapping):
            result = dict(value)
        else:
            result = {}
        result["productionWritesEnabled"] = False
        result.pop("production_writes_enabled", None)
        result["hostedSyncRequired"] = False
        result.pop("hosted_sync_required", None)
        result["postgresDsnRef"] = None
        result.pop("postgres_dsn_ref", None)
        result["sqliteMultiWriterAllowed"] = False
        result.pop("sqlite_multi_writer_allowed", None)
        return result

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["productionWritesEnabled"] = False
        values.pop("production_writes_enabled", None)
        values["hostedSyncRequired"] = False
        values.pop("hosted_sync_required", None)
        values["postgresDsnRef"] = None
        values.pop("postgres_dsn_ref", None)
        values["sqliteMultiWriterAllowed"] = False
        values.pop("sqlite_multi_writer_allowed", None)
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(by_alias=True, mode="json")
        if update:
            payload.update(update)
        payload["productionWritesEnabled"] = False
        payload.pop("production_writes_enabled", None)
        payload["hostedSyncRequired"] = False
        payload.pop("hosted_sync_required", None)
        payload["postgresDsnRef"] = None
        payload.pop("postgres_dsn_ref", None)
        payload["sqliteMultiWriterAllowed"] = False
        payload.pop("sqlite_multi_writer_allowed", None)
        _ = deep
        return type(self).model_validate(payload)

    @field_serializer(
        "production_writes_enabled",
        "hosted_sync_required",
        "sqlite_multi_writer_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    @field_serializer("postgres_dsn_ref")
    def _serialize_postgres_dsn_ref(self, _value: object) -> None:
        return None


class HostedDurableStoreAdapterBoundary(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["postgres"] = "postgres"
    enabled: Literal[False] = False
    stores_secret_material: Literal[False] = Field(default=False, alias="storesSecretMaterial")
    requires_separate_approval: Literal[True] = Field(
        default=True,
        alias="requiresSeparateApproval",
    )
    activation_blockers: tuple[str, ...] = Field(
        default=(
            "hosted_adapter_not_available",
            "requires_saas_db_contract",
            "requires_secrets_binding",
        ),
        alias="activationBlockers",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_disabled(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["enabled"] = False
        payload["storesSecretMaterial"] = False
        payload.pop("stores_secret_material", None)
        payload["requiresSeparateApproval"] = True
        payload.pop("requires_separate_approval", None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["enabled"] = False
        values["storesSecretMaterial"] = False
        values.pop("stores_secret_material", None)
        values["requiresSeparateApproval"] = True
        values.pop("requires_separate_approval", None)
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(by_alias=True, mode="json")
        if update:
            payload.update(update)
        payload["enabled"] = False
        payload["storesSecretMaterial"] = False
        payload["requiresSeparateApproval"] = True
        _ = deep
        return type(self).model_validate(payload)

    @field_validator("activation_blockers")
    @classmethod
    def _validate_blockers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_require_safe_enum(item, field_name="activationBlockers") for item in value)

    @field_serializer("enabled", "stores_secret_material")
    def _serialize_false(self, _value: object) -> bool:
        return False

    @field_serializer("requires_separate_approval")
    def _serialize_true(self, _value: object) -> bool:
        return True

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")


class RuntimeMetadataIndexRecord(BaseModel):
    model_config = _MODEL_CONFIG

    collection: str
    item_id: str = Field(alias="itemId")
    item_digest: str = Field(alias="itemDigest")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("collection")
    @classmethod
    def _validate_collection(cls, value: str) -> str:
        if value not in RUNTIME_METADATA_COLLECTIONS:
            raise DurableStoreSafetyError("runtime metadata collection is not registered")
        return value

    @field_validator("item_id")
    @classmethod
    def _validate_item_id(cls, value: str) -> str:
        if not _SAFE_ID_RE.fullmatch(value):
            raise DurableStoreSafetyError("runtime metadata identifiers must be safe refs")
        _assert_safe_persisted_string(
            value,
            private_path_message="runtime metadata identifiers must not expose private path data",
        )
        return value

    @field_validator("item_digest", "policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise DurableStoreSafetyError("runtime metadata digest fields must be sha256 digests")
        return value

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        _assert_safe_metadata(value)
        return dict(value)

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for runtime metadata index records")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))

    @property
    def index_digest(self) -> str:
        return _digest_json(self.storage_payload())

    def storage_payload(self) -> dict[str, object]:
        validated = type(self).model_validate(self.model_dump(by_alias=True, mode="json"))
        return validated.model_dump(by_alias=True, mode="json")

    def to_durable_record(self) -> "DurableRecord":
        return DurableRecord(
            collection=self.collection,
            recordId=self.item_id,
            contentDigest=self.item_digest,
            policySnapshotDigest=self.policy_snapshot_digest,
            metadata={"runtimeIndexDigest": self.index_digest},
        )


class DurableStoreBackupContract(BaseModel):
    model_config = _MODEL_CONFIG

    export_digest: str = Field(alias="exportDigest")
    destination_ref: str = Field(alias="destinationRef")
    includes_artifact_blobs: Literal[False] = Field(default=False, alias="includesArtifactBlobs")
    includes_secret_material: Literal[False] = Field(default=False, alias="includesSecretMaterial")
    side_effects_allowed: Literal[False] = Field(default=False, alias="sideEffectsAllowed")
    sqlite_multi_writer_allowed: Literal[False] = Field(
        default=False,
        alias="sqliteMultiWriterAllowed",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_safe_flags(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["includesArtifactBlobs"] = False
        payload.pop("includes_artifact_blobs", None)
        payload["includesSecretMaterial"] = False
        payload.pop("includes_secret_material", None)
        payload["sideEffectsAllowed"] = False
        payload.pop("side_effects_allowed", None)
        payload["sqliteMultiWriterAllowed"] = False
        payload.pop("sqlite_multi_writer_allowed", None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["includesArtifactBlobs"] = False
        values["includesSecretMaterial"] = False
        values["sideEffectsAllowed"] = False
        values["sqliteMultiWriterAllowed"] = False
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(by_alias=True, mode="json")
        if update:
            payload.update(update)
        payload["includesArtifactBlobs"] = False
        payload["includesSecretMaterial"] = False
        payload["sideEffectsAllowed"] = False
        payload["sqliteMultiWriterAllowed"] = False
        _ = deep
        return type(self).model_validate(payload)

    @field_validator("export_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise DurableStoreSafetyError("backup export digest must be a sha256 digest")
        return value

    @field_validator("destination_ref")
    @classmethod
    def _validate_destination_ref(cls, value: str) -> str:
        if not _SAFE_REF_VALUE_RE.fullmatch(value):
            raise DurableStoreSafetyError("backup destination must be a safe ref")
        _assert_safe_persisted_string(
            value,
            private_path_message="backup destination must not expose private path data",
        )
        return value

    @field_serializer(
        "includes_artifact_blobs",
        "includes_secret_material",
        "side_effects_allowed",
        "sqlite_multi_writer_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")


class DurableRecord(BaseModel):
    model_config = _MODEL_CONFIG

    collection: str
    record_id: str = Field(alias="recordId")
    content_digest: str = Field(alias="contentDigest")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        alias="createdAt",
    )

    @field_validator("collection", "record_id")
    @classmethod
    def _validate_id(cls, value: str, info: object) -> str:
        if not _SAFE_ID_RE.fullmatch(value):
            raise DurableStoreSafetyError("durable identifiers must be safe refs")
        if getattr(info, "field_name", "") != "collection" or value not in RUNTIME_METADATA_COLLECTIONS:
            _assert_safe_persisted_string(
                value,
                private_path_message="durable identifiers must not expose private path data",
            )
        return value

    @field_validator("content_digest", "policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise DurableStoreSafetyError("digest fields must be sha256 digests")
        return value

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        _assert_safe_metadata(value)
        return dict(value)

    @property
    def record_digest(self) -> str:
        payload = {
            "collection": self.collection,
            "recordId": self.record_id,
            "contentDigest": self.content_digest,
            "policySnapshotDigest": self.policy_snapshot_digest,
            "metadata": self.metadata,
            "createdAt": self.created_at.isoformat(),
        }
        return _digest_json(payload)

    def storage_payload(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")


class ArtifactIndexRecord(BaseModel):
    model_config = _MODEL_CONFIG

    artifact_id: str = Field(alias="artifactId")
    content_digest: str = Field(alias="contentDigest")
    blob_ref: str = Field(alias="blobRef")
    size_bytes: int = Field(alias="sizeBytes", ge=0)
    render_receipt_digest: str = Field(alias="renderReceiptDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("artifact_id")
    @classmethod
    def _validate_artifact_id(cls, value: str) -> str:
        if not _SAFE_ID_RE.fullmatch(value):
            raise DurableStoreSafetyError("artifact identifier must be a safe ref")
        _assert_safe_persisted_string(
            value,
            private_path_message="artifact identifiers must not expose private path data",
        )
        return value

    @field_validator("content_digest", "render_receipt_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise DurableStoreSafetyError("artifact digest fields must be sha256 digests")
        return value

    @field_validator("blob_ref")
    @classmethod
    def _validate_blob_ref(cls, value: str) -> str:
        if not (
            value.startswith("artifact://filesystem/")
            or value.startswith("artifact://object/")
        ):
            raise DurableStoreSafetyError("artifact blobs must live outside SQLite")
        _assert_safe_persisted_string(
            value,
            private_path_message="artifact blob refs must not expose private path data",
        )
        _artifact_blob_digest(value)
        return value

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        _assert_no_inline_artifact_metadata(value)
        _assert_safe_metadata(value)
        return dict(value)

    @model_validator(mode="after")
    def _validate_blob_digest_matches_content(self) -> Self:
        if _artifact_blob_digest(self.blob_ref) != self.content_digest:
            raise DurableStoreSafetyError("artifact blob ref content digest must match content digest")
        return self

    @property
    def artifact_digest(self) -> str:
        return _digest_json(self.storage_payload())

    def storage_payload(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")


class DurableStoreReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    status: Literal["stored", "duplicate_blocked", "blocked"]
    collection: str
    record_id: str = Field(alias="recordId")
    record_digest: str = Field(alias="recordDigest")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    production_write: Literal[False] = Field(default=False, alias="productionWrite")

    @model_validator(mode="before")
    @classmethod
    def _force_no_production_write(cls, value: object) -> dict[str, object]:
        if isinstance(value, Mapping):
            result = dict(value)
        else:
            result = {}
        result["productionWrite"] = False
        result.pop("production_write", None)
        return result

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["productionWrite"] = False
        values.pop("production_write", None)
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(by_alias=True, mode="json")
        if update:
            payload.update(update)
        payload["productionWrite"] = False
        payload.pop("production_write", None)
        _ = deep
        return type(self).model_validate(payload)

    @field_serializer("reason_codes")
    def _serialize_reason_codes(self, value: tuple[str, ...]) -> list[str]:
        return list(value)

    @field_serializer("production_write")
    def _serialize_production_write(self, _value: object) -> bool:
        return False


class CorruptionReport(BaseModel):
    model_config = _MODEL_CONFIG

    ok: bool
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    integrity_mode: Literal["local_logical_integrity"] = Field(
        default="local_logical_integrity",
        alias="integrityMode",
    )
    external_tamper_evidence: Literal[False] = Field(
        default=False,
        alias="externalTamperEvidence",
    )
    external_anchor_required: Literal[True] = Field(
        default=True,
        alias="externalAnchorRequired",
    )

    @field_serializer("reason_codes")
    def _serialize_reason_codes(self, value: tuple[str, ...]) -> list[str]:
        return list(value)

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["externalTamperEvidence"] = False
        values.pop("external_tamper_evidence", None)
        values["externalAnchorRequired"] = True
        values.pop("external_anchor_required", None)
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(by_alias=True, mode="json")
        if update:
            payload.update(update)
        payload["externalTamperEvidence"] = False
        payload.pop("external_tamper_evidence", None)
        payload["externalAnchorRequired"] = True
        payload.pop("external_anchor_required", None)
        _ = deep
        return type(self).model_validate(payload)

    @field_serializer("external_tamper_evidence")
    def _serialize_external_tamper_evidence(self, _value: object) -> bool:
        return False

    @field_serializer("external_anchor_required")
    def _serialize_external_anchor_required(self, _value: object) -> bool:
        return True


class ReplayDecision(BaseModel):
    model_config = _MODEL_CONFIG

    mode: Literal["replay"]
    allow_side_effects: Literal[False] = Field(default=False, alias="allowSideEffects")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    @model_validator(mode="before")
    @classmethod
    def _force_no_side_effects(cls, value: object) -> dict[str, object]:
        if isinstance(value, Mapping):
            result = dict(value)
        else:
            result = {}
        result["allowSideEffects"] = False
        return result

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["allowSideEffects"] = False
        values.pop("allow_side_effects", None)
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))

    @field_serializer("reason_codes")
    def _serialize_reason_codes(self, value: tuple[str, ...]) -> list[str]:
        return list(value)

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")


def durable_store_config_from_env(env: Mapping[str, str]) -> DurableStoreConfig:
    kind = DurableStoreKind.model_validate(env.get("OPENMAGI_DURABLE_STORE", "sqlite"))
    sqlite_path = env.get("OPENMAGI_DURABLE_SQLITE_PATH")
    artifact_path = env.get("OPENMAGI_ARTIFACT_PATH")
    export_path = env.get("OPENMAGI_RUNTIME_EXPORT_PATH")
    sqlite_wal = env.get("OPENMAGI_DURABLE_SQLITE_WAL", "1") not in {"0", "false", "False"}
    timeout = int(env.get("OPENMAGI_DURABLE_SQLITE_BUSY_TIMEOUT_MS", "5000"))

    return DurableStoreConfig(
        kind=kind,
        sqlitePath=Path(sqlite_path or "/var/lib/openmagi/runtime/durable.sqlite")
        if kind == "sqlite"
        else None,
        artifactStore=env.get("OPENMAGI_ARTIFACT_STORE", "filesystem"),
        artifactPath=Path(artifact_path or "/var/lib/openmagi/runtime/artifacts"),
        exportPath=Path(export_path or "/var/lib/openmagi/runtime/exports"),
        sqliteWal=sqlite_wal,
        sqliteBusyTimeoutMs=timeout,
    )


def runtime_metadata_collections() -> tuple[str, ...]:
    return RUNTIME_METADATA_COLLECTIONS


def _assert_safe_metadata(metadata: Mapping[str, object], *, prefix: str = "") -> None:
    for key, value in metadata.items():
        key_text = f"{prefix}.{key}" if prefix else str(key)
        compact_key = re.sub(r"[^a-z0-9]", "", key_text.lower())
        if _UNSAFE_KEY_RE.search(key_text) or _UNSAFE_KEY_RE.search(compact_key):
            raise DurableStoreSafetyError("durable metadata cannot contain raw or sensitive fields")
        _assert_safe_persisted_string(
            key_text,
            private_path_message="durable metadata cannot contain raw or sensitive fields",
            sensitive_message="durable metadata cannot contain raw or sensitive fields",
        )
        if isinstance(value, Mapping):
            _assert_safe_metadata(value, prefix=key_text)
        elif isinstance(value, (str, bytes)):
            _assert_safe_metadata_string(value, key_text=key_text)
        elif isinstance(value, list | tuple):
            _assert_safe_sequence(value, prefix=key_text)
        elif not _metadata_scalar_is_safe(value):
            raise DurableStoreSafetyError("durable metadata contains unsupported metadata value")


def _assert_safe_sequence(values: list[object] | tuple[object, ...], *, prefix: str) -> None:
    for item in values:
        if isinstance(item, Mapping):
            _assert_safe_metadata(item, prefix=prefix)
        elif isinstance(item, list | tuple):
            _assert_safe_sequence(item, prefix=prefix)
        elif isinstance(item, (str, bytes)):
            _assert_safe_metadata_string(item, key_text=prefix)
        elif not _metadata_scalar_is_safe(item):
            raise DurableStoreSafetyError("durable metadata contains unsupported metadata value")


def _assert_no_inline_artifact_metadata_values(value: object) -> None:
    if isinstance(value, Mapping):
        for nested in value.values():
            _assert_no_inline_artifact_metadata_values(nested)
    elif isinstance(value, list | tuple):
        for item in value:
            _assert_no_inline_artifact_metadata_values(item)
    elif isinstance(value, (str, bytes)) and _artifact_metadata_value_can_hold_inline_blob(value):
        raise DurableStoreSafetyError("artifact blobs must live outside SQLite")


def _assert_no_inline_artifact_metadata(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _artifact_metadata_key_can_hold_inline_blob(str(key)):
                raise DurableStoreSafetyError("artifact blobs must live outside SQLite")
            _assert_no_inline_artifact_metadata(nested)
    elif isinstance(value, list | tuple):
        for item in value:
            _assert_no_inline_artifact_metadata(item)
    elif isinstance(value, (str, bytes)) and _artifact_metadata_value_can_hold_inline_blob(value):
        raise DurableStoreSafetyError("artifact blobs must live outside SQLite")


def _metadata_scalar_is_safe(value: object) -> bool:
    if value is None or isinstance(value, bool | int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _artifact_metadata_key_can_hold_inline_blob(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    safe_ref_suffixes = (
        "ref",
        "refs",
        "digest",
        "digests",
        "id",
        "ids",
        "count",
        "status",
        "kind",
        "mode",
        "format",
        "mimetype",
    )
    if compact.endswith(safe_ref_suffixes):
        return False
    return any(
        marker in compact
        for marker in ("blob", "bytes", "content", "raw", "payload", "body", "filedata")
    ) or compact in {"data", "file"} or compact.endswith("data")


def _artifact_metadata_value_can_hold_inline_blob(value: str | bytes) -> bool:
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    return any(
        re.search(r"^\s*data:", variant, re.IGNORECASE)
        or re.search(r"^\s*(?:inline|blob):", variant, re.IGNORECASE)
        for variant in _text_variants(text)
    )


def _assert_safe_metadata_string(value: str | bytes, *, key_text: str) -> None:
    _assert_safe_persisted_string(
        value,
        private_path_message="durable metadata cannot contain raw or sensitive fields",
        sensitive_message="durable metadata cannot contain raw or sensitive fields",
    )
    if _artifact_metadata_value_can_hold_inline_blob(value):
        raise DurableStoreSafetyError("artifact blobs must live outside SQLite")
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    if not _metadata_string_value_is_allowlisted(text, key_text=key_text):
        raise DurableStoreSafetyError("durable metadata string values must be safe refs or digests")


def _metadata_string_value_is_allowlisted(value: str, *, key_text: str) -> bool:
    if _DIGEST_RE.fullmatch(value):
        return True
    if value.startswith(("artifact://filesystem/", "artifact://object/")):
        _artifact_blob_digest(value)
        return True
    if _SAFE_REF_VALUE_RE.fullmatch(value):
        return True
    compact_key = re.sub(r"[^a-z0-9]", "", key_text.lower())
    enum_keys = ("status", "mode", "kind", "format", "mimetype", "type")
    return compact_key.endswith(enum_keys) and _SAFE_ENUM_VALUE_RE.fullmatch(value) is not None


def _require_safe_enum(value: str, *, field_name: str) -> str:
    if _SAFE_ENUM_VALUE_RE.fullmatch(value) is None:
        raise DurableStoreSafetyError(f"{field_name} must be a safe enum value")
    return value


def _assert_safe_persisted_string(
    value: str | bytes,
    *,
    private_path_message: str,
    sensitive_message: str = "durable metadata cannot contain raw or sensitive fields",
) -> None:
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    for variant in _text_variants(text):
        compact_variant = re.sub(r"[^a-z0-9]", "", variant.lower())
        if _PRIVATE_PATH_RE.search(variant):
            raise DurableStoreSafetyError(private_path_message)
        if _UNSAFE_TEXT_RE.search(variant) or _compact_text_contains_secret_marker(compact_variant):
            raise DurableStoreSafetyError(sensitive_message)


def _text_variants(value: str) -> tuple[str, ...]:
    variants: list[str] = []
    current = value
    for _ in range(3):
        if current not in variants:
            variants.append(current)
        decoded = unquote_plus(current)
        if decoded == current:
            break
        current = decoded
    compact = re.sub(r"[^a-z0-9:=/_-]", "", value.lower())
    if compact not in variants:
        variants.append(compact)
    return tuple(variants)


def _compact_text_contains_secret_marker(value: str) -> bool:
    return any(
        marker in value
        for marker in (
            "authorization",
            "bearer",
            "cookie",
            "sessionkey",
            "connectortoken",
            "privatekey",
            "apikey",
            "authkey",
            "password",
            "credential",
            "secret",
            "token",
        )
    )


def _artifact_blob_digest(value: str) -> str:
    for prefix in ("artifact://filesystem/", "artifact://object/"):
        if value.startswith(prefix):
            suffix = value.removeprefix(prefix)
            break
    else:
        raise DurableStoreSafetyError("artifact blobs must live outside SQLite")

    if _DIGEST_RE.fullmatch(suffix):
        return suffix
    slash_digest = re.fullmatch(r"sha256/([0-9a-f]{64})", suffix)
    if slash_digest is not None:
        return f"sha256:{slash_digest.group(1)}"
    raise DurableStoreSafetyError("artifact blob refs must be content-addressed")


def _digest_json(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
