from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import re
from pathlib import PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


ReadMode = Literal["full", "partial", "metadata"]
MutationKind = Literal["edit", "create", "delete", "patch", "replace"]
ReadLedgerStatus = Literal["ok", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SEALED_BASENAMES = frozenset(
    {
        "AGENTS.md",
        "CLAUDE.md",
        "HEARTBEAT.md",
        "SOUL.md",
        "TOOLS.md",
    }
)
_SECRET_PATH_RE = re.compile(
    r"(^|/)(?:\.env(?:[./_-]|$)|\.npmrc$|\.pypirc$|\.netrc$|"
    r"id_rsa$|id_ed25519$|.*(?:secret|token|credential|private[_-]?key|"
    r"password|service[_-]?account).*(?:$|/)|.*\.(?:pem|key|p12|pfx)$)",
    re.IGNORECASE,
)
_PRIVATE_REF_RE = re.compile(
    r"(?:/Users/|/home/|/workspace/|/data/bots/|/var/lib/|authorization|"
    r"cookie|token|secret|session[_-]?key)",
    re.IGNORECASE,
)


class ReadLedgerConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_in_memory_enabled: bool = Field(default=False, alias="localInMemoryEnabled")
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )


class ReadLedgerAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    read_ledger_enabled: bool = Field(default=False, alias="readLedgerEnabled")
    local_in_memory_only: bool = Field(default=False, alias="localInMemoryOnly")
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )
    workspace_mutation_authority: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAuthority",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["productionWritesEnabled"] = False
        values["workspaceMutationAuthority"] = False
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        data["productionWritesEnabled"] = False
        data["workspaceMutationAuthority"] = False
        return type(self).model_validate(data)

    @field_serializer("production_writes_enabled", "workspace_mutation_authority")
    def _serialize_false(self, _value: object) -> bool:
        return False


class ReadLedgerEntry(BaseModel):
    model_config = _MODEL_CONFIG

    session_id: str = Field(alias="sessionId")
    workspace_ref: str = Field(alias="workspaceRef")
    path: str
    digest: str
    size_bytes: int = Field(ge=0, alias="sizeBytes")
    mtime_ns: int = Field(ge=0, alias="mtimeNs")
    read_mode: ReadMode = Field(alias="readMode")
    turn_id: str = Field(alias="turnId")
    tool_use_id: str = Field(alias="toolUseId")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
    entry_ref: str = Field(alias="entryRef")
    path_ref: str = Field(alias="pathRef")

    @field_validator("session_id", "turn_id", "tool_use_id")
    @classmethod
    def _validate_public_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("read ledger ids must be non-empty")
        if _PRIVATE_REF_RE.search(value):
            raise ValueError("read ledger ids must not contain private data")
        return value.strip()[:180]

    @field_validator("workspace_ref")
    @classmethod
    def _validate_workspace_ref(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("workspaceRef must be non-empty")
        if _PRIVATE_REF_RE.search(value):
            raise ValueError("workspaceRef must not contain private data")
        return value.strip()[:180]

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return safe_workspace_relative_path(value)

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("digest must be sha256:<64 hex chars>")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "entryRef": self.entry_ref,
            "pathRef": self.path_ref,
            "digestRef": digest_ref(self.digest),
            "sizeBytes": self.size_bytes,
            "readMode": self.read_mode,
            "turnId": self.turn_id,
            "toolUseId": self.tool_use_id,
            "createdAt": self.created_at.isoformat(),
        }


class WorkspaceMutationReadCheck(BaseModel):
    model_config = _MODEL_CONFIG

    session_id: str = Field(alias="sessionId")
    workspace_ref: str = Field(alias="workspaceRef")
    path: str
    current_digest: str | None = Field(default=None, alias="currentDigest")
    mutation_kind: MutationKind = Field(default="edit", alias="mutationKind")

    @field_validator("session_id", "workspace_ref")
    @classmethod
    def _validate_non_private_ref(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("mutation read check refs must be non-empty")
        if _PRIVATE_REF_RE.search(value):
            raise ValueError("mutation read check refs must not contain private data")
        return value.strip()[:180]

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return safe_workspace_relative_path(value)

    @field_validator("current_digest")
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("currentDigest must be sha256:<64 hex chars>")
        return value


class WorkspaceMutationReadDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: ReadLedgerStatus
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    entry_ref: str | None = Field(default=None, alias="entryRef")
    path_ref: str = Field(alias="pathRef")
    digest_ref: str | None = Field(default=None, alias="digestRef")
    authority_flags: ReadLedgerAuthorityFlags = Field(alias="authorityFlags")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        flags = values.get("authorityFlags")
        if flags is None:
            values["authorityFlags"] = ReadLedgerAuthorityFlags()
        else:
            values["authorityFlags"] = (
                flags
                if isinstance(flags, ReadLedgerAuthorityFlags)
                else ReadLedgerAuthorityFlags.model_construct(**dict(flags))
                if isinstance(flags, Mapping)
                else ReadLedgerAuthorityFlags()
            )
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        flags = data.get("authorityFlags") or {}
        data["authorityFlags"] = (
            flags
            if isinstance(flags, ReadLedgerAuthorityFlags)
            else ReadLedgerAuthorityFlags.model_construct(**dict(flags))
            if isinstance(flags, Mapping)
            else ReadLedgerAuthorityFlags()
        )
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        projection: dict[str, object] = {
            "status": self.status,
            "reasonCodes": list(self.reason_codes),
            "pathRef": self.path_ref,
            "authorityFlags": {
                "readLedgerEnabled": bool(self.authority_flags.read_ledger_enabled),
                "localInMemoryOnly": bool(self.authority_flags.local_in_memory_only),
                "productionWritesEnabled": False,
                "workspaceMutationAuthority": False,
            },
        }
        if self.entry_ref is not None:
            projection["entryRef"] = self.entry_ref
        if self.digest_ref is not None:
            projection["digestRef"] = self.digest_ref
        return projection


class ReadLedger:
    """Session/workspace-scoped read-state primitive. It never writes production state."""

    def __init__(self, config: ReadLedgerConfig | None = None) -> None:
        self.config = config or ReadLedgerConfig()
        self._entries: list[ReadLedgerEntry] = []

    def record_read(
        self,
        *,
        session_id: str,
        workspace_ref: str,
        path: str,
        digest: str,
        size_bytes: int,
        mtime_ns: int,
        read_mode: ReadMode,
        turn_id: str,
        tool_use_id: str,
        created_at: datetime | None = None,
    ) -> ReadLedgerEntry | None:
        if not self.config.enabled or not self.config.local_in_memory_enabled:
            return None
        safe_path = safe_workspace_relative_path(path)
        entry = ReadLedgerEntry(
            sessionId=session_id,
            workspaceRef=workspace_ref,
            path=safe_path,
            digest=digest,
            sizeBytes=size_bytes,
            mtimeNs=mtime_ns,
            readMode=read_mode,
            turnId=turn_id,
            toolUseId=tool_use_id,
            createdAt=created_at or datetime.now(UTC),
            entryRef=read_entry_ref(session_id, workspace_ref, safe_path, digest),
            pathRef=workspace_path_ref(workspace_ref, safe_path),
        )
        self._entries.append(entry)
        return entry

    def get_latest_read(
        self,
        *,
        session_id: str,
        workspace_ref: str,
        path: str,
    ) -> ReadLedgerEntry | None:
        safe_path = safe_workspace_relative_path(path)
        for entry in reversed(self._entries):
            if (
                entry.session_id == session_id
                and entry.workspace_ref == workspace_ref
                and entry.path == safe_path
            ):
                return entry
        return None

    def require_fresh_full_read(
        self,
        check: WorkspaceMutationReadCheck,
    ) -> WorkspaceMutationReadDecision:
        flags = ReadLedgerAuthorityFlags(
            readLedgerEnabled=self.config.enabled,
            localInMemoryOnly=self.config.local_in_memory_enabled,
            productionWritesEnabled=False,
            workspaceMutationAuthority=False,
        )
        path_ref = workspace_path_ref(check.workspace_ref, check.path)
        if not self.config.enabled:
            return _decision("blocked", ("read_ledger_disabled",), path_ref, flags)
        if not self.config.local_in_memory_enabled:
            return _decision("blocked", ("read_ledger_local_store_disabled",), path_ref, flags)
        if is_unsafe_workspace_path(check.path):
            return _decision(
                "blocked",
                ("unsafe_or_sealed_path_blocked",),
                path_ref,
                flags,
            )
        if check.current_digest is None:
            if check.mutation_kind == "create":
                return _decision(
                    "ok",
                    ("create_operation_no_prior_read_required",),
                    path_ref,
                    flags,
                )
            return _decision("blocked", ("edit_requires_existing_file",), path_ref, flags)

        entry = self.get_latest_read(
            session_id=check.session_id,
            workspace_ref=check.workspace_ref,
            path=check.path,
        )
        if entry is None:
            return _decision("blocked", ("no_prior_read",), path_ref, flags)
        if entry.read_mode != "full":
            return _decision(
                "blocked",
                ("full_read_required",),
                path_ref,
                flags,
                entry_ref=entry.entry_ref,
            )
        if entry.digest != check.current_digest:
            return _decision(
                "blocked",
                ("stale_read_digest",),
                path_ref,
                flags,
                entry_ref=entry.entry_ref,
                digest_ref=digest_ref(check.current_digest),
            )
        return _decision(
            "ok",
            ("fresh_full_read",),
            path_ref,
            flags,
            entry_ref=entry.entry_ref,
            digest_ref=digest_ref(check.current_digest),
        )


def workspace_content_digest(content: str | bytes) -> str:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def workspace_path_ref(workspace_ref: str, path: str) -> str:
    return "path-ref:" + _short_hash(f"{workspace_ref}|{safe_workspace_relative_path(path)}")


def read_entry_ref(session_id: str, workspace_ref: str, path: str, digest: str) -> str:
    return "read-ledger:" + _short_hash(
        f"{session_id}|{workspace_ref}|{safe_workspace_relative_path(path)}|{digest}",
    )


def digest_ref(digest: str) -> str:
    return "digest-ref:" + _short_hash(digest)


def safe_workspace_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    if not normalized:
        raise ValueError("workspace path must be non-empty")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("workspace paths must stay inside workspace")
    if any(part in {"", "."} for part in path.parts):
        raise ValueError("workspace paths must be normalized relative paths")
    return str(path)


def is_unsafe_workspace_path(path: str) -> bool:
    safe_path = safe_workspace_relative_path(path)
    return (
        PurePosixPath(safe_path).name in _SEALED_BASENAMES
        or safe_path == "memory"
        or safe_path.startswith("memory/")
        or _SECRET_PATH_RE.search(safe_path) is not None
    )


def _decision(
    status: ReadLedgerStatus,
    reason_codes: tuple[str, ...],
    path_ref: str,
    flags: ReadLedgerAuthorityFlags,
    *,
    entry_ref: str | None = None,
    digest_ref: str | None = None,
) -> WorkspaceMutationReadDecision:
    return WorkspaceMutationReadDecision(
        status=status,
        reasonCodes=reason_codes,
        entryRef=entry_ref,
        pathRef=path_ref,
        digestRef=digest_ref,
        authorityFlags=flags,
    )


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


__all__ = [
    "MutationKind",
    "ReadLedger",
    "ReadLedgerAuthorityFlags",
    "ReadLedgerConfig",
    "ReadLedgerEntry",
    "ReadLedgerStatus",
    "ReadMode",
    "WorkspaceMutationReadCheck",
    "WorkspaceMutationReadDecision",
    "digest_ref",
    "is_unsafe_workspace_path",
    "read_entry_ref",
    "safe_workspace_relative_path",
    "workspace_content_digest",
    "workspace_path_ref",
]
