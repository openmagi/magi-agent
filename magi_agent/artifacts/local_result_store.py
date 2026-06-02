from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any, Literal, Self, TypeGuard

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.tools.output_budget import BudgetedToolResult


LocalResultStoreStatus = Literal["disabled", "stored_local_fake", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\bcookie\s*:\s*[^\n\r]+|"
    r"\bsid=[A-Za-z0-9._-]+|"
    r"\bsk-[A-Za-z0-9._-]+|"
    r"gh[opusr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|"
    r"/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|"
    r"/Users(?:/[^\s,;}\"']*)?|"
    r"/home(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|"
    r"s3://[^\s,;}\"']+|"
    r"gs://[^\s,;}\"']+|"
    r"supabase://[^\s,;}\"']+|"
    r"raw[_ -]?(?:tool|child|prompt|transcript|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)
_SENSITIVE_KEY_MARKERS = (
    "authorization",
    "cookie",
    "credential",
    "secret",
    "token",
    "password",
    "privatekey",
    "apikey",
    "servicekey",
    "key",
    "path",
    "raw",
    "prompt",
    "transcript",
)


class LocalResultStoreConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_store_enabled: bool = Field(default=False, alias="localFakeStoreEnabled")
    adk_artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="adkArtifactServiceAttached",
    )
    production_storage_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionStorageWritesEnabled",
    )
    live_attachment_enabled: Literal[False] = Field(
        default=False,
        alias="liveAttachmentEnabled",
    )


class ResultStoreAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="adkArtifactServiceAttached",
    )
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    live_attachment_enabled: Literal[False] = Field(
        default=False,
        alias="liveAttachmentEnabled",
    )
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> dict[str, bool]:
        _ = value
        return {}

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "adk_artifact_service_attached",
        "production_storage_written",
        "live_attachment_enabled",
        "user_visible_output_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class StoredResultBlob(BaseModel):
    model_config = _MODEL_CONFIG

    ref: str
    content_digest: str = Field(alias="contentDigest")
    blob: bytes
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_serializer("blob")
    def _serialize_blob(self, value: bytes) -> str:
        return "sha256:" + hashlib.sha256(value).hexdigest()


class LocalResultStoreReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    status: LocalResultStoreStatus
    ref: str
    content_digest: str = Field(alias="contentDigest")
    size_bytes: int = Field(alias="sizeBytes")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    metadata: Mapping[str, object] = Field(default_factory=dict)
    authority_flags: ResultStoreAuthorityFlags = Field(
        default_factory=ResultStoreAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = ResultStoreAuthorityFlags()
        return cls(**values)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ref": self.ref,
            "contentDigest": self.content_digest,
            "sizeBytes": self.size_bytes,
            "reasonCodes": list(self.reason_codes),
            "metadata": _safe_metadata(self.metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class LocalResultStore:
    """In-memory content-addressed store for local PR3 tests only."""

    openmagi_local_fake_provider = True

    def __init__(self, config: LocalResultStoreConfig | Mapping[str, object] | None = None) -> None:
        self.config = LocalResultStoreConfig.model_validate(config or {})
        self._blobs: dict[str, StoredResultBlob] = {}
        self.production_write_count = 0

    def put_tool_result(
        self,
        result: BudgetedToolResult,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> LocalResultStoreReceipt:
        safe_metadata = _safe_metadata(metadata or {})
        ref = result.result_ref
        receipt = LocalResultStoreReceipt(
            status="disabled",
            ref=ref,
            contentDigest=result.digest,
            sizeBytes=len(result.raw_blob),
            reasonCodes=("local_result_store_disabled",),
            metadata=safe_metadata,
            authorityFlags=ResultStoreAuthorityFlags(),
        )
        if not self.config.enabled or not self.config.local_fake_store_enabled:
            return receipt

        record = StoredResultBlob(
            ref=ref,
            contentDigest=result.digest,
            blob=result.raw_blob,
            metadata=safe_metadata,
        )
        self._blobs[ref] = record
        return LocalResultStoreReceipt(
            status="stored_local_fake",
            ref=ref,
            contentDigest=result.digest,
            sizeBytes=len(result.raw_blob),
            reasonCodes=("stored_local_fake_only",),
            metadata=safe_metadata,
            authorityFlags=ResultStoreAuthorityFlags(),
        )

    def get(self, ref: str) -> StoredResultBlob | None:
        return self._blobs.get(ref)


def is_trusted_local_result_store(value: object) -> TypeGuard[LocalResultStore]:
    """Only the built-in local fake store may receive raw budgeted result blobs."""

    return type(value) is LocalResultStore


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if _is_sensitive_key(key_text) or _PRIVATE_TEXT_RE.search(key_text):
            continue
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean:
                safe[key_text] = clean[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[key_text] = value
    return safe


def _safe_text(value: str) -> str:
    return _PRIVATE_TEXT_RE.sub("[redacted-private]", value).strip()


def _is_sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


__all__ = [
    "is_trusted_local_result_store",
    "LocalResultStore",
    "LocalResultStoreConfig",
    "LocalResultStoreReceipt",
    "ResultStoreAuthorityFlags",
    "StoredResultBlob",
]
