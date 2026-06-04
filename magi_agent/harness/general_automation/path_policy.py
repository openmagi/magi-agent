from __future__ import annotations

from hashlib import sha256
import posixpath
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PathAccessStatus = Literal["workspace_local", "external_directory", "blocked"]
PathOperationClass = Literal["read", "write", "list", "delete", "execute"]
AdkControlKind = Literal["tool_callback_control_request"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SAFE_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,96}$")
_MOUNT_PREFIXES = ("/Volumes", "/mnt", "/media")
_TMP_PREFIXES = ("/tmp", "/private/tmp", "/var/tmp")


class PathAccessRequest(BaseModel):
    model_config = _MODEL_CONFIG

    workspace_root: str = Field(alias="workspaceRoot")
    path: str
    operation_class: PathOperationClass = Field(alias="operationClass")
    home_dir: str | None = Field(default=None, alias="homeDir")

    @field_validator("workspace_root", "path", "home_dir")
    @classmethod
    def _validate_path_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("path values must be non-empty")
        if "\x00" in cleaned:
            raise ValueError("path values must not contain NUL bytes")
        return cleaned

    @model_validator(mode="after")
    def _validate_roots(self) -> Self:
        if not self.workspace_root.startswith("/"):
            raise ValueError("workspaceRoot must be an absolute POSIX path")
        if self.home_dir is not None and not self.home_dir.startswith("/"):
            raise ValueError("homeDir must be an absolute POSIX path")
        return self


class PathAccessDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: PathAccessStatus
    operation_class: PathOperationClass = Field(alias="operationClass")
    canonical_path_prefix: str = Field(alias="canonicalPathPrefix")
    path_digest: str = Field(alias="pathDigest")
    approval_required: bool = Field(alias="approvalRequired")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    adk_control_kind: AdkControlKind = Field(
        default="tool_callback_control_request",
        alias="adkControlKind",
    )

    @field_validator("canonical_path_prefix")
    @classmethod
    def _validate_prefix(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("canonicalPathPrefix must be absolute")
        return value

    @field_validator("path_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("pathDigest must be a sha256 digest")
        return value

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _SAFE_REASON_CODE_RE.fullmatch(item):
                raise ValueError("reason codes must be safe public identifiers")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "operationClass": self.operation_class,
            "approvalRequired": self.approval_required,
            "pathDigest": self.path_digest,
            "reasonCodes": self.reason_codes,
            "adkControlKind": self.adk_control_kind,
        }


def classify_path_access(request: PathAccessRequest) -> PathAccessDecision:
    workspace_root = _normalize_absolute_path(request.workspace_root)
    home_dir = _normalize_absolute_path(request.home_dir) if request.home_dir else None
    canonical_path = _canonicalize_request_path(
        request.path,
        workspace_root=workspace_root,
        home_dir=home_dir,
    )
    path_digest = _digest(canonical_path)

    if _is_under(canonical_path, workspace_root):
        # Read and list operations are silent (no approval needed) — mirrors
        # OpenCode's ``read:"*":"allow"`` posture.
        # Write, delete, and execute operations require approval:
        # - write/delete mutate workspace state directly.
        # - execute is treated as mutation-class: running a workspace file carries
        #   side-effects of the same magnitude as writing it, and applying the same
        #   approval posture closes the "write then execute silently" gap.
        _mutation_class = {"write", "delete", "execute"}
        if request.operation_class in _mutation_class:
            return PathAccessDecision(
                status="workspace_local",
                operationClass=request.operation_class,
                canonicalPathPrefix=workspace_root,
                pathDigest=path_digest,
                approvalRequired=True,
                reasonCodes=("workspace_write_requires_approval",),
            )
        return PathAccessDecision(
            status="workspace_local",
            operationClass=request.operation_class,
            canonicalPathPrefix=workspace_root,
            pathDigest=path_digest,
            approvalRequired=False,
            reasonCodes=("workspace_local_access",),
        )

    external_prefix = _external_directory_prefix(canonical_path, home_dir=home_dir)
    if external_prefix is not None:
        return PathAccessDecision(
            status="external_directory",
            operationClass=request.operation_class,
            canonicalPathPrefix=external_prefix,
            pathDigest=path_digest,
            approvalRequired=True,
            reasonCodes=("external_directory_approval_required",),
        )

    return PathAccessDecision(
        status="blocked",
        operationClass=request.operation_class,
        canonicalPathPrefix=_public_root_prefix(canonical_path),
        pathDigest=path_digest,
        approvalRequired=False,
        reasonCodes=("unsupported_external_path",),
    )


def _canonicalize_request_path(
    raw_path: str,
    *,
    workspace_root: str,
    home_dir: str | None,
) -> str:
    if raw_path == "~" or raw_path.startswith("~/"):
        if home_dir is None:
            return "/~"
        raw_path = home_dir + raw_path[1:]
    elif raw_path.startswith("~"):
        return "/~"
    elif not raw_path.startswith("/"):
        raw_path = posixpath.join(workspace_root, raw_path)
    return _normalize_absolute_path(raw_path)


def _normalize_absolute_path(path: str) -> str:
    normalized = posixpath.normpath(path.replace("\\", "/"))
    if normalized == ".":
        return "/"
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def _external_directory_prefix(canonical_path: str, *, home_dir: str | None) -> str | None:
    for prefix in _TMP_PREFIXES:
        if _is_under(canonical_path, prefix):
            return prefix

    for prefix in _MOUNT_PREFIXES:
        if _is_under(canonical_path, prefix):
            return _prefix_with_next_part(canonical_path, prefix)

    if home_dir is not None and _is_under(canonical_path, home_dir):
        return _prefix_with_next_part(canonical_path, home_dir)

    return None


def _prefix_with_next_part(canonical_path: str, prefix: str) -> str:
    if canonical_path == prefix:
        return prefix
    remainder = canonical_path.removeprefix(prefix).lstrip("/")
    first_part = remainder.split("/", 1)[0]
    return f"{prefix}/{first_part}" if first_part else prefix


def _public_root_prefix(canonical_path: str) -> str:
    if canonical_path == "/":
        return "/"
    parts = [part for part in canonical_path.split("/") if part]
    if not parts:
        return "/"
    return "/" + parts[0]


def _is_under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "AdkControlKind",
    "PathAccessDecision",
    "PathAccessRequest",
    "PathAccessStatus",
    "PathOperationClass",
    "classify_path_access",
]
