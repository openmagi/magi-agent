from __future__ import annotations

import posixpath
import re
from typing import Literal

from .policy import SandboxDecision, SandboxPolicy, build_decision, digest_payload


FilesystemOperation = Literal["read", "write", "execute"]

_SECRET_PATH_RE = re.compile(
    r"(^|/)(?:\.env(?:[./-]|$)|\.npmrc$|\.pypirc$|\.netrc$|id_rsa$|"
    r"id_ed25519$|service-account\.json$|\.kube/config$|\.docker/config\.json$|"
    r".*(?:secret|token|credential|private[_-]?key|password|service[_-]?account).*"
    r"(?:$|/)|.*\.(?:pem|key|p12|pfx)$)",
    re.IGNORECASE,
)


def evaluate_filesystem_access(
    policy: SandboxPolicy,
    *,
    path: str,
    operation: FilesystemOperation,
) -> SandboxDecision:
    resolved, escaped = resolve_workspace_path(policy, path)
    reason_codes: list[str] = []
    if escaped:
        reason_codes.append("workspace_escape_blocked")
    if _is_secret_path(resolved.relative_path):
        reason_codes.append("secret_path_blocked")
    if operation == "write" and resolved.basename in policy.sealed_basenames:
        reason_codes.append("sealed_file_write_blocked")
    if operation == "execute":
        reason_codes.append("filesystem_execute_blocked")

    return build_decision(
        allowed=not reason_codes,
        operation=operation,
        reason_codes=tuple(reason_codes),
        target_digest=resolved.path_digest,
        target_kind="workspace_path",
        policy=policy,
    )


class ResolvedWorkspacePath:
    def __init__(self, *, absolute_path: str, relative_path: str) -> None:
        self.absolute_path = absolute_path
        self.relative_path = relative_path

    @property
    def basename(self) -> str:
        return posixpath.basename(self.relative_path)

    @property
    def path_digest(self) -> str:
        return digest_payload(
            {
                "absolutePath": self.absolute_path,
                "relativePath": self.relative_path,
            }
        )


def resolve_workspace_path(policy: SandboxPolicy, path: str) -> tuple[ResolvedWorkspacePath, bool]:
    raw = path.replace("\\", "/").strip()
    root = policy.workspace_root
    if raw.startswith("/"):
        normalized = posixpath.normpath(raw)
    else:
        normalized = posixpath.normpath(posixpath.join(root, raw))
    escaped = normalized != root and not normalized.startswith(root + "/")
    relative = normalized.removeprefix(root).lstrip("/") if not escaped else posixpath.basename(normalized)
    if relative in {"", "."}:
        relative = "."
    return ResolvedWorkspacePath(absolute_path=normalized, relative_path=relative), escaped


def _is_secret_path(relative_path: str) -> bool:
    return bool(_SECRET_PATH_RE.search(relative_path))


__all__ = ["ResolvedWorkspacePath", "evaluate_filesystem_access", "resolve_workspace_path"]
