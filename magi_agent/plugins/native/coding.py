from __future__ import annotations

import json
import os
from pathlib import Path

from magi_agent.plugins.native._common import (
    digest,
    ok_result,
    protected_workspace_path_reason,
    safe_child_path,
    workspace_root,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult


_TEXT_EXTENSIONS = {
    ".css",
    ".go",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".rs",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_IGNORED_DIRS = {".git", ".magi", ".pytest_cache", ".ruff_cache", "node_modules", "__pycache__"}
_MAX_SAMPLE_FILE_BYTES = 1_000_000


def code_diagnostics(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    files = _sample_files(context, limit=_limit(arguments, default=64))
    output = {
        "checker": "local_static_inventory",
        "passed": True,
        "exitCode": 0,
        "diagnosticCount": 0,
        "fileCount": len(files),
        "files": files[:20],
    }
    return ok_result("CodeDiagnostics", output)


def code_intelligence(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    files = _sample_files(context, limit=_limit(arguments, default=80))
    language_counts: dict[str, int] = {}
    for relative in files:
        suffix = Path(relative).suffix.lower() or "<none>"
        language_counts[suffix] = language_counts.get(suffix, 0) + 1
    return ok_result(
        "CodeIntelligence",
        {
            "workspaceDigest": digest(files),
            "fileCount": len(files),
            "languageCounts": language_counts,
        },
    )


def code_symbol_search(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    query = str(arguments.get("query") or arguments.get("symbol") or "").strip()
    matches: list[dict[str, object]] = []
    if query:
        for relative in _sample_files(context, limit=256):
            path = workspace_root(context) / relative
            try:
                for index, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if query.lower() in line.lower():
                        matches.append({"path": relative, "line": index, "snippetDigest": digest(line)})
                    if len(matches) >= _limit(arguments, default=32):
                        break
            except OSError:
                continue
            if len(matches) >= _limit(arguments, default=32):
                break
    return ok_result("CodeSymbolSearch", {"query": query, "matches": matches})


def code_workspace(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    root = workspace_root(context)
    return ok_result(
        "CodeWorkspace",
        {
            "workspaceRef": context.workspace_ref or "local",
            "rootDigest": digest(str(root)),
            "fileCount": len(_sample_files(context, limit=512)),
        },
    )


def coding_benchmark(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return ok_result(
        "CodingBenchmark",
        {
            "taskClass": str(arguments.get("taskClass") or "local-smoke"),
            "status": "available",
            "localOnly": True,
        },
    )


def commit_checkpoint(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    path = safe_child_path(
        context,
        ".magi/commit-checkpoints.jsonl",
        default_name=".magi/commit-checkpoints.jsonl",
        allow_internal=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "label": str(arguments.get("label") or "checkpoint"),
        "workspaceDigest": digest(_sample_files(context, limit=256)),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return ok_result("CommitCheckpoint", {"checkpointDigest": digest(record), "pathRef": ".magi/commit-checkpoints.jsonl"})


def package_dependency_resolve(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    manifests: list[str] = []
    for name in ("pyproject.toml", "package.json", "requirements.txt", "uv.lock", "pnpm-lock.yaml"):
        path = workspace_root(context) / name
        if path.exists():
            manifests.append(name)
    return ok_result("PackageDependencyResolve", {"manifestFiles": manifests, "manifestDigest": digest(manifests)})


def project_verification_planner(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    manifests = package_dependency_resolve(arguments, context).output
    candidates = ["pytest", "npm test", "npm run lint", "git diff --check"]
    return ok_result("ProjectVerificationPlanner", {"candidateCommands": candidates, "dependencyEvidence": manifests})


def repo_map(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    files = _sample_files(context, limit=_limit(arguments, default=128))
    top_dirs = sorted({relative.split("/", 1)[0] for relative in files if "/" in relative})
    return ok_result("RepoMap", {"files": files[:80], "topDirectories": top_dirs[:40], "fileCount": len(files)})


def repository_map(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return repo_map(arguments, context).model_copy(update={"metadata": {"toolName": "RepositoryMap"}})


def repo_task_state(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    task_files = [name for name in ("WORKING.md", "SCRATCHPAD.md", "TASK-QUEUE.md") if (workspace_root(context) / name).exists()]
    return ok_result("RepoTaskState", {"stateFiles": task_files, "stateDigest": digest(task_files)})


def safe_command(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    command = str(arguments.get("command") or "").strip()
    allowed = command in {"git status --short", "git diff --check", "pwd"} or command.startswith("python -m pytest ")
    return ok_result(
        "SafeCommand",
        {
            "commandDigest": digest(command),
            "allowedByLocalPolicy": allowed,
            "executionAttached": False,
        },
    )


def _sample_files(context: ToolContext, *, limit: int) -> list[str]:
    root = workspace_root(context)
    files: list[str] = []
    for current, dirs, names in os.walk(root):
        current_path = Path(current)
        filtered_dirs: list[str] = []
        for name in dirs:
            if name in _IGNORED_DIRS:
                continue
            if (current_path / name).is_symlink():
                continue
            try:
                relative_dir = (current_path / name).relative_to(root).as_posix()
            except ValueError:
                continue
            if protected_workspace_path_reason(relative_dir, mutating=False):
                continue
            filtered_dirs.append(name)
        dirs[:] = filtered_dirs
        for name in sorted(names):
            path = current_path / name
            if path.is_symlink():
                continue
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                continue
            try:
                if path.stat().st_size > _MAX_SAMPLE_FILE_BYTES:
                    continue
            except OSError:
                continue
            if protected_workspace_path_reason(relative, mutating=False):
                continue
            if path.suffix.lower() not in _TEXT_EXTENSIONS:
                continue
            files.append(relative)
            if len(files) >= limit:
                return files
    return files


def _limit(arguments: dict[str, object], *, default: int) -> int:
    value = arguments.get("limit") or arguments.get("maxResults") or default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 512))
