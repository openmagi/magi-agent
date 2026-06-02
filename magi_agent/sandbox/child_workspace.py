from __future__ import annotations

import posixpath

from .filesystem import evaluate_filesystem_access, resolve_workspace_path
from .policy import SandboxDecision, SandboxPolicy, build_decision, digest_payload


def evaluate_child_workspace_request(
    policy: SandboxPolicy,
    *,
    child_workspace_root: str,
    requested_path: str,
    mutates_parent: bool,
) -> SandboxDecision:
    reason_codes: list[str] = []
    child_root = _normalize_root(child_workspace_root)
    parent_root = policy.workspace_root
    if _paths_overlap(child_root, parent_root):
        reason_codes.append("child_workspace_must_be_isolated")
    if mutates_parent:
        reason_codes.append("parent_workspace_mutation_blocked")

    child_policy = SandboxPolicy.local_default(workspaceRoot=child_root)
    child_decision = evaluate_filesystem_access(
        child_policy,
        path=requested_path,
        operation="read",
    )
    reason_codes.extend(child_decision.reason_codes)
    child_resolved, _escaped = resolve_workspace_path(child_policy, requested_path)
    if _path_is_inside(child_resolved.absolute_path, parent_root):
        reason_codes.append("parent_workspace_path_blocked")

    return build_decision(
        allowed=not reason_codes,
        operation="child_workspace",
        reason_codes=tuple(reason_codes),
        target_digest=digest_payload(
            {
                "parentRootDigest": policy.workspace_root_digest,
                "childRootDigest": child_policy.workspace_root_digest,
                "requestedPathDigest": child_decision.target_digest,
            }
        ),
        target_kind="child_workspace",
        policy=policy,
    )


def _normalize_root(value: str) -> str:
    normalized = posixpath.normpath(value.replace("\\", "/").strip())
    return normalized.rstrip("/") or "/"


def _paths_overlap(first: str, second: str) -> bool:
    return _path_is_inside(first, second) or _path_is_inside(second, first)


def _path_is_inside(path: str, root: str) -> bool:
    if not root:
        return False
    normalized_path = _normalize_root(path)
    normalized_root = _normalize_root(root)
    try:
        common = posixpath.commonpath((normalized_path, normalized_root))
    except ValueError:
        return False
    return common == normalized_root


__all__ = ["evaluate_child_workspace_request"]
