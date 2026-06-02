from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openmagi_core_agent.shadow.gate2_shadow_tool_policy import (
    Gate2MutationOutcome,
    Gate2MutationReceipt,
    Gate2RollbackReceipt,
    Gate2SandboxMutationProvider,
)


Gate2SandboxCanaryStatus = Literal["completed", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ACTIONS = frozenset({"FileCreate", "FileEdit", "PatchApply"})
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
_SANDBOX_ROOT_NAMES = frozenset({"gate2-sandbox", "gate2-workspace-canary"})
_SANDBOX_PARENT_NAMES = frozenset(
    {"gate2-sandboxes", "openmagi-gate2-sandboxes", "gate2-workspace-canaries"}
)
_RELATIVE_PREFIX = "gate2-loop-a/"
_SEALED_FILE_NAMES = frozenset(
    {"agents.md", "claude.md", "tools.md", "soul.md", "heartbeat.md"}
)
_PROTECTED_SEGMENTS = frozenset(
    {
        ".git",
        ".kube",
        ".ssh",
        "auth",
        "credentials",
        "memory",
        "private",
        "secrets",
        "sessions",
        "tokens",
    }
)
_PROTECTED_ROOT_SEGMENTS = frozenset(
    {
        ".git",
        ".kube",
        ".ssh",
        "auth",
        "bots",
        "credentials",
        "data",
        "memory",
        "private",
        "secrets",
        "sessions",
        "tokens",
        "workspace",
        "workspaces",
    }
)
_SANDBOX_ROOT_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_PROTECTED_PATH_RE = re.compile(
    r"(?:^|/)(?:\.env(?:\.|$)|.*(?:auth|cookie|credential|key|password|secret|"
    r"session|token).*)(?:/|$)",
    re.IGNORECASE,
)
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_NOFOLLOW_FLAG = getattr(os, "O_NOFOLLOW", 0)


class Gate2SandboxCanaryRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_digest: str = Field(alias="requestDigest")
    action: str
    relative_path: str = Field(alias="relativePath")
    content: str = ""
    idempotency_key: str = Field(default="gate2-loop-a", alias="idempotencyKey")
    patch_digest: str | None = Field(default=None, alias="patchDigest")

    @model_validator(mode="after")
    def _validate_request(self) -> Self:
        if not _DIGEST_RE.fullmatch(self.request_digest):
            raise ValueError("Gate 2 request digest is required")
        return self


class Gate2ParentCreateDiagnostics(BaseModel):
    model_config = _MODEL_CONFIG

    sandbox_root_shape_kind: str = Field(alias="sandboxRootShapeKind")
    root_segment_count: int = Field(alias="rootSegmentCount")
    approved_parent_matched: bool = Field(alias="approvedParentMatched")
    safe_namespace_segment_count: int = Field(alias="safeNamespaceSegmentCount")
    final_root_name_matched: bool = Field(alias="finalRootNameMatched")
    parent_create_stage: str = Field(alias="parentCreateStage")
    parent_create_denied_reason: str = Field(alias="parentCreateDeniedReason")
    component_role: str = Field(alias="componentRole")
    component_index: int = Field(alias="componentIndex")
    mkdir_attempted: bool = Field(alias="mkdirAttempted")
    mkdir_failed: bool = Field(alias="mkdirFailed")
    open_no_follow_failed: bool = Field(alias="openNoFollowFailed")

    @model_validator(mode="after")
    def _validate_public_diagnostics(self) -> Self:
        for label in (
            self.sandbox_root_shape_kind,
            self.parent_create_stage,
            self.parent_create_denied_reason,
            self.component_role,
        ):
            if not _SAFE_REASON_RE.fullmatch(label):
                raise ValueError("Gate 2 parent diagnostics must be public-safe")
        if self.root_segment_count < 0 or self.root_segment_count > 256:
            raise ValueError("Gate 2 root segment count must be bounded")
        if (
            self.safe_namespace_segment_count < 0
            or self.safe_namespace_segment_count > 256
        ):
            raise ValueError("Gate 2 namespace segment count must be bounded")
        if self.component_index < 0 or self.component_index > 256:
            raise ValueError("Gate 2 component index must be bounded")
        return self


class Gate2SandboxRootReadiness(BaseModel):
    model_config = _MODEL_CONFIG

    ready: bool
    status: Literal["ready", "blocked"]
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    parent_create_diagnostics: Gate2ParentCreateDiagnostics | None = Field(
        default=None,
        alias="parentCreateDiagnostics",
    )

    @model_validator(mode="after")
    def _validate_public_readiness(self) -> Self:
        if self.status == "ready" and not self.ready:
            raise ValueError("Gate 2 sandbox root readiness status mismatch")
        if self.status == "blocked" and self.ready:
            raise ValueError("Gate 2 sandbox root readiness status mismatch")
        if not self.reason_codes:
            raise ValueError("Gate 2 sandbox root readiness requires reason codes")
        for reason in self.reason_codes:
            if not _SAFE_REASON_RE.fullmatch(reason):
                raise ValueError(
                    "Gate 2 sandbox root readiness reason must be public-safe"
                )
        return self


class Gate2SandboxCanaryResult(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["gate2.sandboxWorkspaceCanary.v1"] = Field(
        default="gate2.sandboxWorkspaceCanary.v1",
        alias="schemaVersion",
    )
    gate: Literal["gate2_sandbox_workspace_canary"] = "gate2_sandbox_workspace_canary"
    status: Gate2SandboxCanaryStatus
    reason: str
    request_digest: str = Field(alias="requestDigest")
    sandbox_path_digest: str = Field(alias="sandboxPathDigest")
    before_digest: str = Field(alias="beforeDigest")
    after_digest: str = Field(alias="afterDigest")
    readback_digest: str = Field(alias="readbackDigest")
    mutation_receipt: Gate2MutationReceipt = Field(alias="workspaceMutationReceipt")
    rollback_receipt: Gate2RollbackReceipt | None = Field(
        default=None,
        alias="rollbackReceipt",
    )
    parent_create_diagnostics: Gate2ParentCreateDiagnostics | None = Field(
        default=None,
        alias="parentCreateDiagnostics",
    )
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )
    write_mutation_authority_allowed: Literal[False] = Field(
        default=False,
        alias="writeMutationAuthorityAllowed",
    )
    tool_host_dispatch_allowed: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchAllowed",
    )

    @model_validator(mode="after")
    def _validate_public_result(self) -> Self:
        for digest in (
            self.request_digest,
            self.sandbox_path_digest,
            self.before_digest,
            self.after_digest,
            self.readback_digest,
        ):
            if not _DIGEST_RE.fullmatch(digest):
                raise ValueError("Gate 2 canary result must be digest-only")
        if not _SAFE_REASON_RE.fullmatch(self.reason):
            raise ValueError("Gate 2 canary reason must be public-safe")
        if self.status == "completed" and self.rollback_receipt is None:
            raise ValueError("Gate 2 canary completion requires rollback proof")
        return self


def check_gate2_sandbox_root_readiness(
    sandbox_root: str | Path | None,
) -> Gate2SandboxRootReadiness:
    """Preflight the selected Gate 2 sandbox root with descriptor-walk safety."""

    if sandbox_root is None:
        return Gate2SandboxRootReadiness(
            ready=False,
            status="blocked",
            reasonCodes=("sandbox_root_unavailable", "sandbox_root_missing"),
            parentCreateDiagnostics=None,
        )
    root = Path(sandbox_root)
    root_resolved = _safe_sandbox_root(root)
    if root_resolved is None:
        return Gate2SandboxRootReadiness(
            ready=False,
            status="blocked",
            reasonCodes=("sandbox_root_unavailable", "path_policy_denied"),
            parentCreateDiagnostics=_parent_create_diagnostics(
                root,
                stage="root_shape_validation",
                denied_reason="path_policy_denied",
                component_role="root_shape",
                component_index=0,
            ),
        )
    root_fd, root_failure, root_diagnostics = _open_sandbox_root_fd(
        root_resolved,
        create=True,
        parent_error="sandbox_write_parent_create_failed",
    )
    if root_fd is not None:
        try:
            os.close(root_fd)
        except OSError:
            pass
    if root_failure is None:
        return Gate2SandboxRootReadiness(
            ready=True,
            status="ready",
            reasonCodes=("sandbox_root_ready",),
            parentCreateDiagnostics=None,
        )
    reasons = [
        "sandbox_root_unavailable",
        root_failure,
    ]
    if root_diagnostics is not None:
        reasons.append(root_diagnostics.parent_create_denied_reason)
    return Gate2SandboxRootReadiness(
        ready=False,
        status="blocked",
        reasonCodes=tuple(dict.fromkeys(reasons)),
        parentCreateDiagnostics=root_diagnostics,
    )


def run_gate2_sandbox_workspace_canary(
    request: Gate2SandboxCanaryRequest,
    *,
    sandbox_root: Path,
    provider: Gate2SandboxMutationProvider | None = None,
    require_rollback_proof: bool = True,
    simulate_rollback_failure: bool = False,
) -> Gate2SandboxCanaryResult:
    provider = provider or Gate2SandboxMutationProvider()
    sandbox_path_digest = _digest({"path": request.relative_path})
    before_digest = _digest_bytes(None)
    root_resolved = _safe_sandbox_root(sandbox_root)
    if root_resolved is None or not _safe_relative_path(request.relative_path):
        denied = _denied_path_outcome(provider, request=request)
        return Gate2SandboxCanaryResult(
            status="blocked",
            reason="path_policy_denied",
            requestDigest=request.request_digest,
            sandboxPathDigest=sandbox_path_digest,
            beforeDigest=before_digest,
            afterDigest=before_digest,
            readbackDigest=before_digest,
            workspaceMutationReceipt=denied.receipt,
            rollbackReceipt=None,
        )
    policy_outcome = provider.simulate_mutation(
        action=request.action,
        requestDigest=request.request_digest,
        idempotencyKey=request.idempotency_key,
        relativePath=request.relative_path,
        content=request.content or None,
        patchDigest=request.patch_digest,
    )
    target_path = _safe_target_path(root_resolved, request.relative_path)
    before_bytes, before_error, before_diagnostics = _read_canary_target(
        root_resolved,
        request.relative_path,
    )
    if before_error is not None:
        before_bytes = None
        before_digest = _digest_bytes(before_bytes)
        return Gate2SandboxCanaryResult(
            status="blocked",
            reason="sandbox_read_failed",
            requestDigest=request.request_digest,
            sandboxPathDigest=sandbox_path_digest,
            beforeDigest=before_digest,
            afterDigest=before_digest,
            readbackDigest=before_digest,
            workspaceMutationReceipt=policy_outcome.receipt,
            rollbackReceipt=None,
            parentCreateDiagnostics=before_diagnostics,
        )
    before_digest = _digest_bytes(before_bytes)
    if policy_outcome.status != "simulated" or request.action not in _SAFE_ACTIONS:
        return Gate2SandboxCanaryResult(
            status="blocked",
            reason=policy_outcome.reason,
            requestDigest=request.request_digest,
            sandboxPathDigest=sandbox_path_digest,
            beforeDigest=before_digest,
            afterDigest=before_digest,
            readbackDigest=before_digest,
            workspaceMutationReceipt=policy_outcome.receipt,
            rollbackReceipt=None,
        )
    if target_path is None:
        denied = _denied_path_outcome(provider, request=request)
        return Gate2SandboxCanaryResult(
            status="blocked",
            reason="path_policy_denied",
            requestDigest=request.request_digest,
            sandboxPathDigest=sandbox_path_digest,
            beforeDigest=before_digest,
            afterDigest=before_digest,
            readbackDigest=before_digest,
            workspaceMutationReceipt=denied.receipt,
            rollbackReceipt=None,
        )
    if before_bytes is not None:
        return Gate2SandboxCanaryResult(
            status="blocked",
            reason="sandbox_write_existing_target_denied",
            requestDigest=request.request_digest,
            sandboxPathDigest=sandbox_path_digest,
            beforeDigest=before_digest,
            afterDigest=before_digest,
            readbackDigest=before_digest,
            workspaceMutationReceipt=policy_outcome.receipt,
            rollbackReceipt=None,
        )

    readback_bytes, write_error, write_diagnostics = _write_canary_target(
        root_resolved,
        request.relative_path,
        request.content,
    )
    if write_error is not None or readback_bytes is None:
        _restore_before_state(root_resolved, request.relative_path, before_bytes)
        _prune_empty_parents(target_path.parent, stop=root_resolved)
        return Gate2SandboxCanaryResult(
            status="blocked",
            reason=write_error or "sandbox_write_failed",
            requestDigest=request.request_digest,
            sandboxPathDigest=sandbox_path_digest,
            beforeDigest=before_digest,
            afterDigest=before_digest,
            readbackDigest=before_digest,
            workspaceMutationReceipt=policy_outcome.receipt,
            rollbackReceipt=None,
            parentCreateDiagnostics=write_diagnostics,
        )
    readback_digest = _digest_bytes(readback_bytes)
    after_digest = readback_digest
    rollback_receipt: Gate2RollbackReceipt | None = None
    rollback_reason = "rollback_not_proven"
    rollback_action: Literal["delete", "restore"] = (
        "delete" if before_bytes is None else "restore"
    )
    try:
        if before_bytes is None:
            rollback_succeeded = _unlink_canary_target(
                root_resolved,
                request.relative_path,
            )
        else:
            rollback_succeeded = _restore_before_state(
                root_resolved,
                request.relative_path,
                before_bytes,
            )
        if rollback_succeeded and not simulate_rollback_failure:
            post_rollback_bytes, post_rollback_error, _post_rollback_diagnostics = (
                _read_canary_target(
                    root_resolved,
                    request.relative_path,
                )
            )
            if post_rollback_error is not None:
                post_rollback_bytes = None
            post_rollback_digest = _digest_bytes(post_rollback_bytes)
            if post_rollback_digest == before_digest:
                rollback = provider.rollback(
                    mutationReceiptDigest=policy_outcome.receipt.receipt_digest,
                    requestDigest=request.request_digest,
                    rollbackAction=rollback_action,
                    postRollbackDigest=post_rollback_digest,
                )
                rollback_receipt = rollback.rollback_receipt
                rollback_reason = rollback.reason
    except OSError:
        rollback_receipt = None
    finally:
        _prune_empty_parents(target_path.parent, stop=root_resolved)

    if require_rollback_proof and rollback_receipt is None:
        return Gate2SandboxCanaryResult(
            status="blocked",
            reason="rollback_not_proven",
            requestDigest=request.request_digest,
            sandboxPathDigest=sandbox_path_digest,
            beforeDigest=before_digest,
            afterDigest=after_digest,
            readbackDigest=readback_digest,
            workspaceMutationReceipt=policy_outcome.receipt,
            rollbackReceipt=None,
        )

    return Gate2SandboxCanaryResult(
        status="completed",
        reason=rollback_reason if rollback_reason == "sandbox_rollback_simulated" else "sandbox_canary_completed",
        requestDigest=request.request_digest,
        sandboxPathDigest=sandbox_path_digest,
        beforeDigest=before_digest,
        afterDigest=after_digest,
        readbackDigest=readback_digest,
        workspaceMutationReceipt=policy_outcome.receipt,
        rollbackReceipt=rollback_receipt,
    )


def _safe_target_path(root: Path, relative_path: str) -> Path | None:
    try:
        root_resolved = root.expanduser().resolve()
        candidate = (root_resolved / relative_path).resolve()
    except OSError:
        return None
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate


def _safe_sandbox_root(root: Path) -> Path | None:
    try:
        if root.is_symlink():
            return None
        if root.exists() and not root.is_dir():
            return None
        resolved = root.expanduser().resolve()
    except OSError:
        return None
    if resolved.name not in _SANDBOX_ROOT_NAMES:
        return None
    parts = [part.lower() for part in resolved.parts]
    if not any(part in _SANDBOX_PARENT_NAMES for part in parts[:-1]):
        return None
    for index, part in enumerate(parts):
        if part in _PROTECTED_ROOT_SEGMENTS:
            if part == "private" and parts[index : index + 3] == ["private", "var", "folders"]:
                continue
            return None
        if any(
            marker in part
            for marker in ("credential", "private", "secret", "session", "token", "workspace")
        ):
            return None
    return resolved


def _write_canary_target(
    root: Path,
    relative_path: str,
    content: str,
) -> tuple[bytes | None, str | None, Gate2ParentCreateDiagnostics | None]:
    del content
    readback_bytes, write_error, diagnostics = _write_unlinked_target_bytes(
        root,
        relative_path,
        _public_canary_payload(),
        parent_error="sandbox_write_parent_create_failed",
        open_error="sandbox_write_open_failed",
        flush_error="sandbox_write_flush_failed",
    )
    if write_error is not None:
        return None, write_error, diagnostics
    return readback_bytes, None, None


def _write_unlinked_target_bytes(
    root: Path,
    relative_path: str,
    content: bytes,
    *,
    parent_error: str = "sandbox_write_parent_create_failed",
    open_error: str = "sandbox_write_open_failed",
    flush_error: str = "sandbox_write_flush_failed",
) -> tuple[bytes | None, str | None, Gate2ParentCreateDiagnostics | None]:
    parent_fd, parent_failure, parent_diagnostics = _open_sandbox_parent_fd(
        root,
        relative_path,
        create_parents=True,
        parent_error=parent_error,
    )
    if parent_fd is None:
        return None, parent_failure or parent_error, parent_diagnostics
    file_fd: int | None = None
    try:
        try:
            file_fd = os.open(
                _target_file_name(relative_path),
                os.O_RDWR | os.O_CREAT | os.O_EXCL | _FILE_NOFOLLOW_FLAG,
                0o600,
                dir_fd=parent_fd,
            )
        except OSError:
            return None, open_error, None
        try:
            file_stat = os.fstat(file_fd)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink > 1:
                return None, open_error, None
            os.unlink(_target_file_name(relative_path), dir_fd=parent_fd)
            if os.fstat(file_fd).st_nlink != 0:
                return None, open_error, None
        except OSError:
            return None, open_error, None
        try:
            _write_all(file_fd, content)
            os.fsync(file_fd)
        except OSError:
            return None, flush_error, None
        try:
            os.lseek(file_fd, 0, os.SEEK_SET)
            return _read_all(file_fd), None, None
        except OSError:
            return None, "sandbox_write_readback_failed", None
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        try:
            os.close(parent_fd)
        except OSError:
            pass


def _public_canary_payload() -> bytes:
    return b"gate2.sandboxWorkspaceCanary.v1\n"


def _read_canary_target(
    root: Path,
    relative_path: str,
) -> tuple[bytes | None, str | None, Gate2ParentCreateDiagnostics | None]:
    parent_fd, parent_failure, parent_diagnostics = _open_sandbox_parent_fd(
        root,
        relative_path,
        create_parents=False,
        parent_error="sandbox_read_failed",
    )
    if parent_fd is None:
        if parent_failure == "missing":
            return None, None, None
        return None, parent_failure or "sandbox_read_failed", parent_diagnostics
    file_fd: int | None = None
    try:
        try:
            file_fd = os.open(
                _target_file_name(relative_path),
                os.O_RDONLY | _FILE_NOFOLLOW_FLAG,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            return None, None, None
        except OSError:
            return None, "sandbox_read_failed", None
        try:
            file_stat = os.fstat(file_fd)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink > 1:
                return None, "sandbox_read_failed", None
        except OSError:
            return None, "sandbox_read_failed", None
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks), None, None
    except OSError:
        return None, "sandbox_read_failed", None
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        try:
            os.close(parent_fd)
        except OSError:
            pass


def _unlink_canary_target(root: Path, relative_path: str) -> bool:
    parent_fd, parent_failure, _parent_diagnostics = _open_sandbox_parent_fd(
        root,
        relative_path,
        create_parents=False,
        parent_error="sandbox_read_failed",
    )
    if parent_fd is None:
        return parent_failure == "missing"
    file_fd: int | None = None
    try:
        try:
            file_fd = os.open(
                _target_file_name(relative_path),
                os.O_RDONLY | _FILE_NOFOLLOW_FLAG,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            file_stat = os.fstat(file_fd)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink > 1:
                return False
        except OSError:
            return False
        try:
            os.unlink(_target_file_name(relative_path), dir_fd=parent_fd)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            return os.fstat(file_fd).st_nlink == 0
        except OSError:
            return False
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        try:
            os.close(parent_fd)
        except OSError:
            pass


def _open_sandbox_parent_fd(
    root: Path,
    relative_path: str,
    *,
    create_parents: bool,
    parent_error: str,
) -> tuple[int | None, str | None, Gate2ParentCreateDiagnostics | None]:
    parts = _relative_parts(relative_path)
    if len(parts) < 2:
        return (
            None,
            parent_error,
            _parent_create_diagnostics(
                root,
                stage="relative_parent_validation",
                denied_reason="relative_path_too_short",
                component_role="relative_path",
                component_index=0,
            ),
        )
    current_fd, root_failure, root_diagnostics = _open_sandbox_root_fd(
        root,
        create=create_parents,
        parent_error=parent_error,
    )
    if current_fd is None:
        return None, root_failure, root_diagnostics
    for index, part in enumerate(parts[:-1]):
        component_role = _relative_parent_component_role(parts, index)
        if create_parents:
            try:
                os.mkdir(part, 0o700, dir_fd=current_fd)
            except FileExistsError:
                pass
            except OSError as exc:
                try:
                    os.close(current_fd)
                except OSError:
                    pass
                return (
                    None,
                    parent_error,
                    _parent_create_diagnostics(
                        root,
                        stage="relative_parent_mkdir",
                        denied_reason=_mkdir_denied_reason(exc),
                        component_role=component_role,
                        component_index=index,
                        mkdir_attempted=True,
                        mkdir_failed=True,
                    ),
                )
        try:
            next_fd = os.open(part, _DIRECTORY_OPEN_FLAGS, dir_fd=current_fd)
        except (FileNotFoundError, NotADirectoryError):
            try:
                os.close(current_fd)
            except OSError:
                pass
            return (
                None,
                "missing" if not create_parents else parent_error,
                _parent_create_diagnostics(
                    root,
                    stage="relative_parent_open",
                    denied_reason=(
                        "missing_without_create"
                        if not create_parents
                        else "open_nofollow_failed"
                    ),
                    component_role=component_role,
                    component_index=index,
                    mkdir_attempted=create_parents,
                    open_no_follow_failed=create_parents,
                ),
            )
        except OSError:
            try:
                os.close(current_fd)
            except OSError:
                pass
            return (
                None,
                parent_error,
                _parent_create_diagnostics(
                    root,
                    stage="relative_parent_open",
                    denied_reason="open_nofollow_failed",
                    component_role=component_role,
                    component_index=index,
                    mkdir_attempted=create_parents,
                    open_no_follow_failed=True,
                ),
            )
        try:
            os.close(current_fd)
        except OSError:
            pass
        current_fd = next_fd
    return current_fd, None, None


def _open_sandbox_root_fd(
    root: Path,
    *,
    create: bool,
    parent_error: str,
) -> tuple[int | None, str | None, Gate2ParentCreateDiagnostics | None]:
    absolute_root = root.expanduser()
    if not absolute_root.is_absolute():
        return (
            None,
            parent_error,
            _parent_create_diagnostics(
                absolute_root,
                stage="root_validation",
                denied_reason="non_absolute_root",
                component_role="root_shape",
                component_index=0,
            ),
        )
    parts = list(absolute_root.parts)
    if not parts:
        return (
            None,
            parent_error,
            _parent_create_diagnostics(
                absolute_root,
                stage="root_validation",
                denied_reason="empty_root",
                component_role="root_shape",
                component_index=0,
            ),
        )
    namespace_count = _sandbox_root_namespace_segment_count(parts)
    if create and (namespace_count is None or namespace_count > 1):
        return (
            None,
            parent_error,
            _parent_create_diagnostics(
                absolute_root,
                stage="root_shape_validation",
                denied_reason=_sandbox_root_shape_denied_reason(parts),
                component_role="root_shape",
                component_index=0,
            ),
        )
    try:
        current_fd = os.open(parts[0], _DIRECTORY_OPEN_FLAGS)
    except OSError:
        return (
            None,
            parent_error,
            _parent_create_diagnostics(
                absolute_root,
                stage="root_component_open",
                denied_reason="open_nofollow_failed",
                component_role="filesystem_root",
                component_index=0,
                open_no_follow_failed=True,
            ),
        )
    entered_sandbox_parent = parts[0].lower() in _SANDBOX_PARENT_NAMES
    root_namespace_segments = 0
    final_index = len(parts) - 1
    for index, part in enumerate(parts[1:], start=1):
        part_label = part.lower()
        is_sandbox_parent = part_label in _SANDBOX_PARENT_NAMES
        is_final_sandbox_root = (
            index == final_index and part_label in _SANDBOX_ROOT_NAMES
        )
        is_root_namespace = (
            namespace_count is not None
            and entered_sandbox_parent
            and not is_sandbox_parent
            and not is_final_sandbox_root
        )
        can_create = (
            create
            and (
                is_sandbox_parent
                or (is_final_sandbox_root and entered_sandbox_parent)
                or (
                    is_root_namespace
                    and root_namespace_segments == 0
                    and _safe_root_namespace_part(part)
                )
            )
        )
        if can_create:
            try:
                os.mkdir(part, 0o700, dir_fd=current_fd)
            except FileExistsError:
                pass
            except OSError as exc:
                try:
                    os.close(current_fd)
                except OSError:
                    pass
                return (
                    None,
                    parent_error,
                    _parent_create_diagnostics(
                        absolute_root,
                        stage="root_component_mkdir",
                        denied_reason=_mkdir_denied_reason(exc),
                        component_role=_root_component_role(parts, index),
                        component_index=index,
                        mkdir_attempted=True,
                        mkdir_failed=True,
                    ),
                )
        try:
            next_fd = os.open(part, _DIRECTORY_OPEN_FLAGS, dir_fd=current_fd)
        except FileNotFoundError:
            try:
                os.close(current_fd)
            except OSError:
                pass
            return (
                None,
                "missing" if not create else parent_error,
                _parent_create_diagnostics(
                    absolute_root,
                    stage="root_component_open",
                    denied_reason="missing_without_create",
                    component_role=_root_component_role(parts, index),
                    component_index=index,
                    mkdir_attempted=can_create,
                    open_no_follow_failed=False,
                ),
            )
        except OSError:
            try:
                os.close(current_fd)
            except OSError:
                pass
            return (
                None,
                parent_error,
                _parent_create_diagnostics(
                    absolute_root,
                    stage="root_component_open",
                    denied_reason="open_nofollow_failed",
                    component_role=_root_component_role(parts, index),
                    component_index=index,
                    mkdir_attempted=can_create,
                    open_no_follow_failed=True,
                ),
            )
        try:
            os.close(current_fd)
        except OSError:
            pass
        current_fd = next_fd
        if is_sandbox_parent:
            entered_sandbox_parent = True
            root_namespace_segments = 0
        elif is_root_namespace:
            root_namespace_segments += 1
    return current_fd, None, None


def _parent_create_diagnostics(
    root: Path,
    *,
    stage: str,
    denied_reason: str,
    component_role: str,
    component_index: int,
    mkdir_attempted: bool = False,
    mkdir_failed: bool = False,
    open_no_follow_failed: bool = False,
) -> Gate2ParentCreateDiagnostics:
    parts = list(root.expanduser().parts)
    summary = _sandbox_root_shape_summary(parts)
    return Gate2ParentCreateDiagnostics(
        sandboxRootShapeKind=summary["shape_kind"],
        rootSegmentCount=summary["root_segment_count"],
        approvedParentMatched=summary["approved_parent_matched"],
        safeNamespaceSegmentCount=summary["safe_namespace_segment_count"],
        finalRootNameMatched=summary["final_root_name_matched"],
        parentCreateStage=stage,
        parentCreateDeniedReason=denied_reason,
        componentRole=component_role,
        componentIndex=min(max(component_index, 0), 256),
        mkdirAttempted=mkdir_attempted,
        mkdirFailed=mkdir_failed,
        openNoFollowFailed=open_no_follow_failed,
    )


def _sandbox_root_shape_summary(parts: list[str]) -> dict[str, object]:
    labels = [part.lower() for part in parts]
    root_segment_count = min(len(parts), 256)
    final_root_name_matched = bool(labels and labels[-1] in _SANDBOX_ROOT_NAMES)
    parent_indices = [
        index for index, label in enumerate(labels[:-1]) if label in _SANDBOX_PARENT_NAMES
    ]
    approved_parent_matched = len(parent_indices) == 1
    namespace_parts: list[str] = []
    if approved_parent_matched and final_root_name_matched:
        namespace_parts = parts[parent_indices[0] + 1 : -1]
    safe_namespace_segment_count = min(
        sum(1 for part in namespace_parts if _safe_root_namespace_part(part)),
        256,
    )

    if not labels:
        shape_kind = "empty_root"
    elif len(parent_indices) == 0:
        shape_kind = "missing_approved_parent"
    elif len(parent_indices) > 1:
        shape_kind = "nested_sandbox_parent"
    elif not final_root_name_matched:
        shape_kind = "final_root_name_mismatch"
    elif len(namespace_parts) == 0:
        shape_kind = "approved_direct_root"
    elif len(namespace_parts) == 1:
        shape_kind = (
            "approved_namespaced_root"
            if safe_namespace_segment_count == 1
            else "unsafe_namespace_segment"
        )
    else:
        shape_kind = "broad_namespace_chain"

    return {
        "shape_kind": shape_kind,
        "root_segment_count": root_segment_count,
        "approved_parent_matched": approved_parent_matched,
        "safe_namespace_segment_count": safe_namespace_segment_count,
        "final_root_name_matched": final_root_name_matched,
    }


def _sandbox_root_shape_denied_reason(parts: list[str]) -> str:
    shape_kind = str(_sandbox_root_shape_summary(parts)["shape_kind"])
    if shape_kind == "broad_namespace_chain":
        return "namespace_chain_too_broad"
    if shape_kind == "nested_sandbox_parent":
        return "nested_sandbox_parent"
    if shape_kind == "missing_approved_parent":
        return "approved_parent_missing"
    if shape_kind == "final_root_name_mismatch":
        return "final_root_name_mismatch"
    if shape_kind == "unsafe_namespace_segment":
        return "unsafe_namespace_segment"
    if shape_kind == "empty_root":
        return "empty_root"
    return "root_shape_denied"


def _mkdir_denied_reason(exc: OSError) -> str:
    if isinstance(exc, PermissionError) or exc.errno in {errno.EACCES, errno.EPERM}:
        return "mkdir_permission_denied"
    if exc.errno == errno.EROFS:
        return "mkdir_read_only_filesystem"
    return "mkdir_oserror"


def _relative_parent_component_role(parts: list[str], index: int) -> str:
    if index == 0:
        return "loop_root"
    if index == 1 and len(parts) > 1:
        return "loop_src"
    return "relative_parent"


def _root_component_role(parts: list[str], index: int) -> str:
    if index == 0:
        return "filesystem_root"
    labels = [part.lower() for part in parts]
    label = labels[index] if index < len(labels) else ""
    if label in _SANDBOX_PARENT_NAMES:
        return "sandbox_parent"
    if index == len(labels) - 1:
        return "final_root" if label in _SANDBOX_ROOT_NAMES else "final_component"
    parent_indices = [
        parent_index
        for parent_index, parent_label in enumerate(labels[:-1])
        if parent_label in _SANDBOX_PARENT_NAMES
    ]
    if len(parent_indices) == 1 and parent_indices[0] < index < len(labels) - 1:
        return "safe_namespace" if _safe_root_namespace_part(parts[index]) else "namespace"
    return "ancestor"


def _safe_root_namespace_part(part: str) -> bool:
    normalized = str(part or "").strip()
    if not _SANDBOX_ROOT_NAMESPACE_RE.fullmatch(normalized):
        return False
    lowered = normalized.lower()
    if lowered.startswith(".") or lowered in _PROTECTED_ROOT_SEGMENTS:
        return False
    return not any(
        marker in lowered
        for marker in ("credential", "private", "secret", "session", "token", "workspace")
    )


def _sandbox_root_namespace_segment_count(parts: list[str]) -> int | None:
    labels = [part.lower() for part in parts]
    if not labels or labels[-1] not in _SANDBOX_ROOT_NAMES:
        return None
    parent_indices = [
        index for index, label in enumerate(labels[:-1]) if label in _SANDBOX_PARENT_NAMES
    ]
    if len(parent_indices) != 1:
        return None
    return len(labels) - parent_indices[0] - 2


def _relative_parts(relative_path: str) -> list[str]:
    return [part for part in relative_path.replace("\\", "/").split("/") if part]


def _target_file_name(relative_path: str) -> str:
    parts = _relative_parts(relative_path)
    return parts[-1] if parts else ""


def _write_all(fd: int, content: bytes) -> None:
    view = memoryview(content)
    total = 0
    while total < len(view):
        written = os.write(fd, view[total:])
        if written <= 0:
            raise OSError
        total += written


def _read_all(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _safe_relative_path(relative_path: str) -> bool:
    normalized = str(relative_path or "").replace("\\", "/").strip()
    if not normalized.startswith(_RELATIVE_PREFIX):
        return False
    parts = [part for part in normalized.split("/") if part]
    if not parts or ".." in parts:
        return False
    for part in parts:
        if part.startswith("."):
            return False
        if part.lower() in _SEALED_FILE_NAMES:
            return False
        if part.lower() in _PROTECTED_SEGMENTS:
            return False
    return _PROTECTED_PATH_RE.search(normalized) is None


def _restore_before_state(
    root: Path,
    relative_path: str,
    before_bytes: bytes | None,
) -> bool:
    if before_bytes is None:
        return _unlink_canary_target(root, relative_path)
    return False


def _denied_path_outcome(
    provider: Gate2SandboxMutationProvider,
    *,
    request: Gate2SandboxCanaryRequest,
) -> Gate2MutationOutcome:
    return provider.policy.evaluate_action(
        action=request.action,
        requestDigest=request.request_digest,
        idempotencyKey=f"{request.idempotency_key}:path-denied",
        relativePath="../denied",
        content=request.content or None,
        patchDigest=request.patch_digest,
    )


def _prune_empty_parents(path: Path, *, stop: Path) -> None:
    try:
        stop_resolved = stop.expanduser().resolve()
    except OSError:
        return
    current = path
    while True:
        try:
            resolved = current.resolve()
            resolved.relative_to(stop_resolved)
        except (OSError, ValueError):
            return
        if resolved == stop_resolved:
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _digest_bytes(value: bytes | None) -> str:
    if value is None:
        return _digest({"state": "missing"})
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "Gate2SandboxCanaryRequest",
    "Gate2SandboxCanaryResult",
    "Gate2SandboxRootReadiness",
    "check_gate2_sandbox_root_readiness",
    "run_gate2_sandbox_workspace_canary",
]
