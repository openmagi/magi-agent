from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
from pathlib import PurePosixPath
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


WorkspaceAdoptionOperation = Literal[
    "preview",
    "apply",
    "cherry_pick",
    "reject",
    "no_op",
    "conflict",
]
WorkspaceAdoptionStatus = Literal[
    "disabled",
    "preview",
    "apply_intent",
    "applied_local_fake",
    "rejected",
    "no_op",
    "conflict",
    "blocked",
    "approval_required",
]
WorkspaceChangeAction = Literal["add", "modify", "delete"]

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
    r"(^|/)(?:\.env(?:[./-]|$)|\.npmrc$|\.pypirc$|\.netrc$|id_rsa$|"
    r"id_ed25519$|service-account\.json$|\.kube/config$|\.docker/config\.json$|"
    r".*(?:secret|token|credential|private[_-]?key|password|service[_-]?account).*"
    r"(?:$|/)|.*\.(?:pem|key|p12|pfx)$)",
    re.IGNORECASE,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|AKIA[A-Z0-9]{8,}|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD)[A-Z0-9_]*\s*[:=]\s*[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)


class WorkspaceMutationConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_apply_enabled: bool = Field(default=False, alias="localFakeApplyEnabled")
    production_workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationEnabled",
    )
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_production_flags_false(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        payload["productionWorkspaceMutationEnabled"] = False
        payload["productionWritesEnabled"] = False
        payload.pop("production_workspace_mutation_enabled", None)
        payload.pop("production_writes_enabled", None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["productionWorkspaceMutationEnabled"] = False
        values["productionWritesEnabled"] = False
        values.pop("production_workspace_mutation_enabled", None)
        values.pop("production_writes_enabled", None)
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        payload["productionWorkspaceMutationEnabled"] = False
        payload["productionWritesEnabled"] = False
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude
        return self.model_copy(update=update, deep=deep)

    @field_serializer(
        "production_workspace_mutation_enabled",
        "production_writes_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class WorkspaceMutationAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    live_workspace_mutation_attached: Literal[False] = Field(
        default=False,
        alias="liveWorkspaceMutationAttached",
    )
    filesystem_write_attempted: Literal[False] = Field(
        default=False,
        alias="filesystemWriteAttempted",
    )
    git_apply_attempted: Literal[False] = Field(default=False, alias="gitApplyAttempted")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

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
        "live_workspace_mutation_attached",
        "filesystem_write_attempted",
        "git_apply_attempted",
        "production_authority",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class WorkspaceChange(BaseModel):
    model_config = _MODEL_CONFIG

    path: str
    action: WorkspaceChangeAction = "modify"
    content_digest: str | None = Field(default=None, alias="contentDigest")

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _safe_relative_path(value)

    @field_validator("content_digest")
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith("sha256:"):
            raise ValueError("contentDigest must use sha256: prefix")
        return value


class WorkspaceMutationRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: WorkspaceAdoptionOperation
    adoption_id: str = Field(alias="adoptionId")
    parent_workspace_ref: str = Field(alias="parentWorkspaceRef")
    child_workspace_ref: str = Field(alias="childWorkspaceRef")
    base_revision: str = Field(alias="baseRevision")
    current_revision: str = Field(alias="currentRevision")
    changes: tuple[WorkspaceChange, ...] = ()
    dirty_parent_files: tuple[str, ...] = Field(default=(), alias="dirtyParentFiles")
    sealed_paths: tuple[str, ...] = Field(default=(), alias="sealedPaths")
    dry_run: bool = Field(default=True, alias="dryRun")
    explicit_apply_approved: bool = Field(default=False, alias="explicitApplyApproved")
    explicit_conflict_resolution: bool = Field(
        default=False,
        alias="explicitConflictResolution",
    )

    @field_validator("adoption_id", "parent_workspace_ref", "child_workspace_ref")
    @classmethod
    def _non_empty_refs(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("workspace mutation refs must be non-empty")
        return _sanitize_public_text(value)

    @field_validator("dirty_parent_files", "sealed_paths")
    @classmethod
    def _sanitize_path_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_relative_path(item) for item in value)


class WorkspaceMutationDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: WorkspaceAdoptionStatus
    operation: WorkspaceAdoptionOperation
    adoption_id: str = Field(alias="adoptionId")
    changed_files: tuple[str, ...] = Field(default=(), alias="changedFiles")
    blocked_paths: tuple[str, ...] = Field(default=(), alias="blockedPaths")
    conflict_paths: tuple[str, ...] = Field(default=(), alias="conflictPaths")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    receipt_ref: str = Field(alias="receiptRef")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    authority_flags: WorkspaceMutationAuthorityFlags = Field(
        default_factory=WorkspaceMutationAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("changed_files", "blocked_paths", "conflict_paths")
    @classmethod
    def _validate_path_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_relative_path(item) for item in value)

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = WorkspaceMutationAuthorityFlags()
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
        data["authorityFlags"] = WorkspaceMutationAuthorityFlags()
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "operation": self.operation,
            "adoptionId": self.adoption_id,
            "changedFiles": list(self.changed_files),
            "blockedPaths": list(self.blocked_paths),
            "conflictPaths": list(self.conflict_paths),
            "reasonCodes": list(self.reason_codes),
            "receiptRef": self.receipt_ref,
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class WorkspaceMutationBoundary:
    """Preflight/apply boundary. It never mutates a real workspace."""

    def __init__(self, config: WorkspaceMutationConfig) -> None:
        self.config = config

    def evaluate(self, request: WorkspaceMutationRequest) -> WorkspaceMutationDecision:
        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeApplyEnabled": self.config.local_fake_apply_enabled,
            "productionWorkspaceMutationEnabled": False,
            "productionWritesEnabled": False,
        }
        if not self.config.enabled:
            return _decision(
                request,
                "disabled",
                reason_codes=("workspace_mutation_disabled",),
                diagnostics=diagnostics,
            )

        blocked_paths = _blocked_paths(request)
        if blocked_paths:
            return _decision(
                request,
                "blocked",
                blocked_paths=blocked_paths,
                reason_codes=("unsafe_or_sealed_path_blocked",),
                diagnostics=diagnostics,
            )

        conflict_paths = _conflict_paths(request)
        stale = request.current_revision != request.base_revision
        if request.operation == "conflict" or conflict_paths or stale:
            reason_codes = ["workspace_conflict"]
            if stale:
                reason_codes.append("stale_workspace_revision")
            if conflict_paths:
                reason_codes.append("dirty_parent_overlap")
            if request.explicit_conflict_resolution and not stale:
                reason_codes.append("explicit_conflict_resolution_recorded")
            return _decision(
                request,
                "conflict",
                conflict_paths=conflict_paths,
                reason_codes=tuple(dict.fromkeys(reason_codes)),
                diagnostics=diagnostics,
            )

        if request.operation == "reject":
            return _decision(
                request,
                "rejected",
                reason_codes=("workspace_adoption_rejected",),
                diagnostics=diagnostics,
            )
        if request.operation == "no_op" or not request.changes:
            return _decision(
                request,
                "no_op",
                reason_codes=("workspace_no_op",),
                diagnostics=diagnostics,
            )
        if request.operation == "preview":
            return _decision(
                request,
                "preview",
                reason_codes=("workspace_preview_only",),
                diagnostics=diagnostics,
            )
        if request.operation in {"apply", "cherry_pick"}:
            if request.dry_run:
                return _decision(
                    request,
                    "apply_intent",
                    reason_codes=("workspace_dry_run_only",),
                    diagnostics=diagnostics,
                )
            if not request.explicit_apply_approved:
                return _decision(
                    request,
                    "approval_required",
                    reason_codes=("workspace_apply_requires_explicit_approval",),
                    diagnostics=diagnostics,
                )
            if self.config.local_fake_apply_enabled:
                return _decision(
                    request,
                    "applied_local_fake",
                    reason_codes=("local_fake_apply_receipt_only",),
                    diagnostics=diagnostics,
                )
            return _decision(
                request,
                "approval_required",
                reason_codes=("live_workspace_apply_disabled",),
                diagnostics=diagnostics,
            )
        return _decision(
            request,
            "blocked",
            reason_codes=("unsupported_workspace_operation",),
            diagnostics=diagnostics,
        )


def _decision(
    request: WorkspaceMutationRequest,
    status: WorkspaceAdoptionStatus,
    *,
    blocked_paths: tuple[str, ...] = (),
    conflict_paths: tuple[str, ...] = (),
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
) -> WorkspaceMutationDecision:
    changed_files = tuple(change.path for change in request.changes)
    return WorkspaceMutationDecision(
        status=status,
        operation=request.operation,
        adoptionId=request.adoption_id,
        changedFiles=changed_files,
        blockedPaths=blocked_paths,
        conflictPaths=conflict_paths,
        reasonCodes=reason_codes,
        receiptRef=_receipt_ref(request, status),
        diagnosticMetadata=diagnostics,
        authorityFlags=WorkspaceMutationAuthorityFlags(),
    )


def _blocked_paths(request: WorkspaceMutationRequest) -> tuple[str, ...]:
    sealed = set(request.sealed_paths)
    blocked: list[str] = []
    for change in request.changes:
        path = change.path
        if (
            path in sealed
            or PurePosixPath(path).name in _SEALED_BASENAMES
            or _SECRET_PATH_RE.search(path)
        ):
            blocked.append(path)
    return tuple(dict.fromkeys(blocked))


def _conflict_paths(request: WorkspaceMutationRequest) -> tuple[str, ...]:
    changed = {change.path for change in request.changes}
    return tuple(path for path in request.dirty_parent_files if path in changed)


def _safe_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    if not normalized:
        raise ValueError("workspace path must be non-empty")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("workspace paths must be relative and stay inside workspace")
    if any(part in {"", "."} for part in path.parts):
        raise ValueError("workspace paths must be normalized relative paths")
    return str(path)


def _receipt_ref(request: WorkspaceMutationRequest, status: WorkspaceAdoptionStatus) -> str:
    seed = "|".join(
        (
            request.adoption_id,
            request.operation,
            status,
            request.base_revision,
            request.current_revision,
            ",".join(change.path for change in request.changes),
        )
    )
    return f"workspace-receipt:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def _sanitize_public_text(value: str) -> str:
    clean = _SECRET_TEXT_RE.sub("[redacted]", value.strip())
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean[:240]


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(marker in normalized_key for marker in ("raw", "secret", "token", "path")):
            continue
        if isinstance(value, str):
            safe[str(key)] = _sanitize_public_text(value)
        elif isinstance(value, bool | int | float) or value is None:
            safe[str(key)] = value
    return safe


__all__ = [
    "WorkspaceAdoptionOperation",
    "WorkspaceAdoptionStatus",
    "WorkspaceChange",
    "WorkspaceMutationAuthorityFlags",
    "WorkspaceMutationBoundary",
    "WorkspaceMutationConfig",
    "WorkspaceMutationDecision",
    "WorkspaceMutationRequest",
]
