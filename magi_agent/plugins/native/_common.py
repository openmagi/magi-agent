from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path

from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.policy import redact_public_text

_SEALED_BASENAMES = {"AGENTS.md", "CLAUDE.md", "SOUL.md", "TOOLS.md", "HEARTBEAT.md"}
_SECRET_NAME_RE = re.compile(
    r"(^|/)(?:\.env(?:[./_-]|$)|.*(?:^|[._/-])"
    r"(?:secrets?|tokens?|credentials?|sessions?|api[_-]?keys?|"
    r"private(?:[_-]?keys?)?|passwords?|kubeconfig)"
    r"(?:[._/-]|$).*)",
    re.IGNORECASE,
)
_SECRET_BASENAMES = {".netrc", ".npmrc", ".pypirc", "id_rsa", "kubeconfig"}
_SECRET_SUFFIXES = ("/.aws/credentials", "/.kube/config")


def workspace_root(context: ToolContext) -> Path:
    root = context.workspace_root or "."
    return Path(root).expanduser().resolve()


def safe_child_path(
    context: ToolContext,
    path_value: object,
    *,
    default_name: str,
    mutating: bool = True,
    allow_internal: bool = False,
) -> Path:
    root = workspace_root(context)
    relative = str(path_value or default_name).strip() or default_name
    if relative.startswith("/"):
        raise ValueError("absolute_path_blocked")
    normalized = relative.replace("\\", "/")
    normalized = str(Path(normalized).as_posix())
    normalized = "" if normalized == "." else normalized
    if _is_protected_workspace_path(normalized, mutating=mutating, allow_internal=allow_internal):
        raise ValueError(_protected_path_reason(normalized, mutating=mutating))
    candidate = (root / relative).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("path_traversal_blocked")
    resolved_relative = "" if candidate == root else candidate.relative_to(root).as_posix()
    if _is_protected_workspace_path(
        resolved_relative,
        mutating=mutating,
        allow_internal=allow_internal,
    ):
        raise ValueError(_protected_path_reason(resolved_relative, mutating=mutating))
    return candidate


def protected_workspace_path_reason(
    path_value: object,
    *,
    mutating: bool = False,
    allow_internal: bool = False,
) -> str:
    relative = str(path_value or "").strip().replace("\\", "/")
    normalized = str(Path(relative).as_posix())
    normalized = "" if normalized == "." else normalized
    return (
        ""
        if not _is_protected_workspace_path(
            normalized,
            mutating=mutating,
            allow_internal=allow_internal,
        )
        else _protected_path_reason(normalized, mutating=mutating)
    )


def _is_protected_workspace_path(path: str, *, mutating: bool, allow_internal: bool) -> bool:
    if allow_internal and (path == ".magi" or path.startswith(".magi/")):
        return False
    return _protected_path_reason(path, mutating=mutating) != ""


def _protected_path_reason(path: str, *, mutating: bool) -> str:
    parts = tuple(part for part in path.split("/") if part)
    if ".git" in parts:
        return "protected_git_path"
    if path.rsplit("/", 1)[-1] in _SEALED_BASENAMES:
        return "sealed_file_write_blocked" if mutating else "sealed_file_read_blocked"
    if path == "memory" or path.startswith("memory/"):
        return "protected_memory_path"
    lowered = path.lower()
    basename = lowered.rsplit("/", 1)[-1]
    if (
        basename in _SECRET_BASENAMES
        or any(f"/{lowered}".endswith(suffix) for suffix in _SECRET_SUFFIXES)
        or _SECRET_NAME_RE.search(path) is not None
    ):
        return "secret_path_denied"
    if mutating and any(part.startswith(".") for part in parts):
        return "hidden_path_write_blocked"
    return ""


def digest(value: object) -> str:
    material = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def ok_result(
    tool_name: str,
    output: Mapping[str, object],
    *,
    evidence_declaration: Mapping[str, object] | None = None,
) -> ToolResult:
    safe_output = dict(output)
    metadata: dict[str, object] = {
        "toolName": tool_name,
        "handler": "first_party_native_local",
        "outputDigest": digest(safe_output),
    }
    # Optional typed-evidence declaration lifted by the declaration-lift path
    # (record_tool_result -> extraction) into an EvidenceRecord. Omitted ->
    # byte-identical metadata for every existing caller.
    if evidence_declaration is not None:
        metadata["evidence"] = dict(evidence_declaration)
    return ToolResult(
        status="ok",
        output=safe_output,
        llmOutput=safe_output,
        transcriptOutput={
            "toolName": tool_name,
            "outputDigest": digest(safe_output),
        },
        metadata=metadata,
    )


def blocked_result(tool_name: str, reason: str) -> ToolResult:
    return ToolResult(
        status="blocked",
        errorCode=reason,
        errorMessage=redact_public_text(reason, max_chars=160),
        metadata={
            "toolName": tool_name,
            "handler": "first_party_native_local",
            "reason": reason,
        },
    )
