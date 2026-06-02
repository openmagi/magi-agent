"""Gate 2 sandbox workspace mutation provider with rollback receipts.

This module provides a sandbox file mutation layer that:
- Only allows FileCreate, FileEdit, PatchApply under Gate 2 canary metadata
- Uses tempfile-based isolation so no production workspace is touched
- Produces digest-only receipts (no raw paths, contents, or auth tokens)
- Enforces path traversal, sealed-path, and absolute-path rejection
- Always sets productionWorkspaceMutationAllowed = False
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


SandboxMutationAction = Literal["FileCreate", "FileEdit", "PatchApply"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ACTIONS: frozenset[str] = frozenset({"FileCreate", "FileEdit", "PatchApply"})
_SEALED_FILE_NAMES = frozenset(
    {"agents.md", "claude.md", "tools.md", "soul.md", "heartbeat.md"}
)
_PROTECTED_PATH_RE = re.compile(
    r"(?:^|/)(?:"
    r"\.env(?:[./]|$)"
    r"|\.git(?:/|$)"
    r"|\.ssh(?:/|$)"
    r"|\.kube(?:/|$)"
    r"|[^/]*(?:auth|cookie|credential|password|secret|session|token)[^/]*(?:/|$)"
    r")",
    re.IGNORECASE,
)
_MEMORY_PATH_RE = re.compile(
    r"(?:^|/)(?:memory|hipocampus|\.memory)(?:/|$)",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)


class SandboxMutationReceipt(BaseModel):
    """Digest-only receipt for a single sandbox file mutation."""

    model_config = _MODEL_CONFIG

    kind: Literal["gate2_sandbox_workspace_mutation"] = "gate2_sandbox_workspace_mutation"
    action: SandboxMutationAction
    workspace_digest: str = Field(alias="workspaceDigest")
    relative_path_digest: str = Field(alias="relativePathDigest")
    before_digest: str | None = Field(alias="beforeDigest")
    after_digest: str | None = Field(alias="afterDigest")
    rollback_receipt_digest: str | None = Field(alias="rollbackReceiptDigest")
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )

    @model_validator(mode="after")
    def _validate_digests(self) -> Self:
        for digest in (
            self.workspace_digest,
            self.relative_path_digest,
            self.before_digest,
            self.after_digest,
            self.rollback_receipt_digest,
        ):
            if digest is not None and not _DIGEST_RE.fullmatch(digest):
                raise ValueError("sandbox mutation receipts must use digest-only values")
        return self

    @field_serializer("production_workspace_mutation_allowed")
    def _serialize_false(self, _value: object) -> bool:
        return False


class SandboxRollbackReceipt(BaseModel):
    """Digest-only receipt for sandbox file rollback (delete or restore)."""

    model_config = _MODEL_CONFIG

    kind: Literal["gate2_sandbox_workspace_rollback"] = "gate2_sandbox_workspace_rollback"
    mutation_receipt_digest: str = Field(alias="mutationReceiptDigest")
    rollback_digest: str = Field(alias="rollbackDigest")
    restored_digest: str = Field(alias="restoredDigest")
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )

    @model_validator(mode="after")
    def _validate_digests(self) -> Self:
        for digest in (
            self.mutation_receipt_digest,
            self.rollback_digest,
            self.restored_digest,
        ):
            if not _DIGEST_RE.fullmatch(digest):
                raise ValueError("sandbox rollback receipts must use digest-only values")
        return self

    @field_serializer("production_workspace_mutation_allowed")
    def _serialize_false(self, _value: object) -> bool:
        return False


class SandboxMutationOutcome(BaseModel):
    """Result of a sandbox mutation attempt."""

    model_config = _MODEL_CONFIG

    status: Literal["completed", "denied", "rolled_back"]
    reason: str
    mutation_receipt: SandboxMutationReceipt = Field(alias="mutationReceipt")
    rollback_receipt: SandboxRollbackReceipt | None = Field(
        default=None,
        alias="rollbackReceipt",
    )
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )

    @field_serializer("production_workspace_mutation_allowed")
    def _serialize_false(self, _value: object) -> bool:
        return False


class Gate2SandboxWorkspaceMutationProvider:
    """Sandbox workspace mutation provider gated on Gate 2 canary metadata.

    - Uses a tempfile.TemporaryDirectory for all mutations
    - Only processes FileCreate, FileEdit, PatchApply
    - Never touches production workspace
    - All receipts are digest-only (no raw paths or contents)
    """

    def __init__(
        self,
        *,
        sandbox_root: Path | None = None,
        gate2_selected: bool = False,
    ) -> None:
        self._gate2_selected = gate2_selected
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        if sandbox_root is not None:
            self._sandbox_root = sandbox_root
        else:
            self._temp_dir = tempfile.TemporaryDirectory(prefix="gate2-sandbox-")
            self._sandbox_root = Path(self._temp_dir.name)
        self._mutation_receipts: dict[str, SandboxMutationReceipt] = {}
        self._rollback_receipts: dict[str, SandboxRollbackReceipt] = {}
        self._before_states: dict[str, bytes | None] = {}

    @property
    def sandbox_root(self) -> Path:
        return self._sandbox_root

    def cleanup(self) -> None:
        """Clean up the temporary directory if one was created."""
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None

    def mutate(
        self,
        *,
        action: str,
        relative_path: str,
        content: str = "",
        patch_content: str | None = None,
    ) -> SandboxMutationOutcome:
        """Execute a sandbox file mutation and return digest-only receipts."""
        workspace_digest = _digest(str(self._sandbox_root))
        path_digest = _digest(relative_path)

        # Gate 2 selected metadata check
        if not self._gate2_selected:
            return _denied_outcome(
                action=action if action in _SAFE_ACTIONS else "FileCreate",
                workspace_digest=workspace_digest,
                path_digest=path_digest,
                reason="gate2_canary_not_selected",
            )

        # Action validation
        if action not in _SAFE_ACTIONS:
            return _denied_outcome(
                action="FileCreate",  # safe fallback for receipt
                workspace_digest=workspace_digest,
                path_digest=path_digest,
                reason="forbidden_sandbox_action",
            )

        # Path validation
        path_denial = _check_path(relative_path)
        if path_denial is not None:
            return _denied_outcome(
                action=action,  # type: ignore[arg-type]
                workspace_digest=workspace_digest,
                path_digest=path_digest,
                reason=path_denial,
            )

        # Resolve target path safely within sandbox
        target = _safe_target(self._sandbox_root, relative_path)
        if target is None:
            return _denied_outcome(
                action=action,  # type: ignore[arg-type]
                workspace_digest=workspace_digest,
                path_digest=path_digest,
                reason="path_traversal_denied",
            )

        # Read before state
        before_bytes = target.read_bytes() if target.exists() else None
        before_digest = _digest_bytes(before_bytes)

        # Perform mutation
        target.parent.mkdir(parents=True, exist_ok=True)
        if action == "PatchApply" and patch_content is not None:
            # For PatchApply, apply patch content to existing file
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            target.write_text(existing + patch_content, encoding="utf-8")
        else:
            target.write_text(content, encoding="utf-8")

        # Read after state
        after_bytes = target.read_bytes()
        after_digest = _digest_bytes(after_bytes)

        # Create mutation receipt
        mutation_receipt_digest = _digest(
            {
                "kind": "gate2_sandbox_workspace_mutation",
                "action": action,
                "workspaceDigest": workspace_digest,
                "relativePathDigest": path_digest,
                "beforeDigest": before_digest,
                "afterDigest": after_digest,
            }
        )

        # Perform rollback
        if before_bytes is None:
            target.unlink(missing_ok=True)
            _prune_empty_parents(target.parent, stop=self._sandbox_root)
        else:
            target.write_bytes(before_bytes)

        restored_bytes = target.read_bytes() if target.exists() else None
        restored_digest = _digest_bytes(restored_bytes)

        rollback_receipt_digest = _digest(
            {
                "mutationReceiptDigest": mutation_receipt_digest,
                "restoredDigest": restored_digest,
            }
        )

        rollback_receipt = SandboxRollbackReceipt(
            mutationReceiptDigest=mutation_receipt_digest,
            rollbackDigest=rollback_receipt_digest,
            restoredDigest=restored_digest,
        )

        mutation_receipt = SandboxMutationReceipt(
            action=action,  # type: ignore[arg-type]
            workspaceDigest=workspace_digest,
            relativePathDigest=path_digest,
            beforeDigest=before_digest,
            afterDigest=after_digest,
            rollbackReceiptDigest=rollback_receipt_digest,
        )

        self._mutation_receipts[mutation_receipt_digest] = mutation_receipt
        self._rollback_receipts[mutation_receipt_digest] = rollback_receipt
        self._before_states[mutation_receipt_digest] = before_bytes

        return SandboxMutationOutcome(
            status="completed",
            reason="sandbox_mutation_completed",
            mutationReceipt=mutation_receipt,
            rollbackReceipt=rollback_receipt,
        )

    def public_projection(self, outcome: SandboxMutationOutcome) -> dict[str, object]:
        """Return a digest-only public projection with no raw paths or content."""
        data = outcome.model_dump(by_alias=True, mode="json")
        rendered = json.dumps(data, sort_keys=True)
        # Final safety check: no private paths leak
        if _PRIVATE_PATH_RE.search(rendered):
            raise ValueError("production workspace path detected in public projection")
        return data


def _denied_outcome(
    *,
    action: SandboxMutationAction,
    workspace_digest: str,
    path_digest: str,
    reason: str,
) -> SandboxMutationOutcome:
    before_digest = _digest_bytes(None)
    return SandboxMutationOutcome(
        status="denied",
        reason=reason,
        mutationReceipt=SandboxMutationReceipt(
            action=action,
            workspaceDigest=workspace_digest,
            relativePathDigest=path_digest,
            beforeDigest=before_digest,
            afterDigest=None,
            rollbackReceiptDigest=None,
        ),
        rollbackReceipt=None,
    )


def _check_path(relative_path: str) -> str | None:
    """Validate relative path. Returns denial reason or None if OK."""
    normalized = relative_path.replace("\\", "/").strip()
    if not normalized:
        return "empty_path_denied"

    # Absolute path check
    if normalized.startswith("/") or normalized.startswith("~"):
        return "absolute_path_denied"

    parts = PurePosixPath(normalized).parts

    # Path traversal check
    if ".." in parts:
        return "path_traversal_denied"

    # Dot-prefixed component check (hidden files/dirs) excluding reasonable ones
    # but blocking .git, .ssh, .kube, .env etc
    for part in parts:
        if part.lower() in _SEALED_FILE_NAMES:
            return "sealed_path_denied"

    # Protected path patterns
    if _PROTECTED_PATH_RE.search(normalized):
        return "private_path_denied"

    # Memory path check
    if _MEMORY_PATH_RE.search(normalized):
        return "memory_path_denied"

    return None


def _safe_target(root: Path, relative_path: str) -> Path | None:
    """Resolve target path safely within sandbox root. Returns None on escape."""
    try:
        root_resolved = root.resolve()
        candidate = (root_resolved / relative_path).resolve()
        candidate.relative_to(root_resolved)
    except (OSError, ValueError):
        return None
    return candidate


def _prune_empty_parents(path: Path, *, stop: Path) -> None:
    """Remove empty parent directories up to (but not including) stop."""
    try:
        stop_resolved = stop.resolve()
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
    "Gate2SandboxWorkspaceMutationProvider",
    "SandboxMutationAction",
    "SandboxMutationOutcome",
    "SandboxMutationReceipt",
    "SandboxRollbackReceipt",
]
