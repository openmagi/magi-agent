from __future__ import annotations

from .manifest import Budget, ParallelSafety, PermissionClass, RuntimeMode, ToolManifest, ToolSource
from .registry import ToolRegistry


CORE_TOOL_SOURCE = ToolSource(kind="builtin", package="openmagi.core")
CORE_TOOL_INPUT_SCHEMA: dict[str, object] = {"type": "object", "additionalProperties": True}


def _manifest(
    name: str,
    description: str,
    *,
    permission: PermissionClass,
    modes: tuple[RuntimeMode, ...],
    tags: tuple[str, ...],
    dangerous: bool = False,
    mutates_workspace: bool = False,
    timeout_ms: int = 30_000,
    budget: Budget | None = None,
    parallel_safety: ParallelSafety = "unsafe",
) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=description,
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission=permission,
        input_schema=CORE_TOOL_INPUT_SCHEMA,
        timeout_ms=timeout_ms,
        budget=budget or Budget(max_calls_per_turn=10, max_parallel=1),
        dangerous=dangerous,
        is_concurrency_safe=not dangerous and permission in {"read", "meta"},
        mutates_workspace=mutates_workspace,
        parallel_safety=parallel_safety,
        available_in_modes=modes,
        tags=tags,
        enabled_by_default=True,
        opt_out=True,
    )


_CORE_TOOL_MANIFESTS: tuple[ToolManifest, ...] = (
    _manifest(
        "ToolSearch",
        "Search deferred tool metadata.",
        permission="meta",
        modes=("plan", "act"),
        tags=("tool", "search", "meta"),
        parallel_safety="readonly",
    ),
    _manifest(
        "FileRead",
        "Read workspace file contents.",
        permission="read",
        modes=("plan", "act"),
        tags=("workspace", "file", "read"),
        parallel_safety="readonly",
    ),
    _manifest(
        "FileWrite",
        "Write workspace file contents.",
        permission="write",
        modes=("act",),
        tags=("workspace", "file", "write"),
        mutates_workspace=True,
        parallel_safety="unsafe",
    ),
    _manifest(
        "FileEdit",
        "Edit existing workspace file contents.",
        permission="write",
        modes=("act",),
        tags=("workspace", "file", "edit"),
        mutates_workspace=True,
        parallel_safety="unsafe",
    ),
    _manifest(
        "PatchApply",
        "Apply a patch to workspace files.",
        permission="write",
        modes=("act",),
        tags=("workspace", "file", "patch", "write"),
        mutates_workspace=True,
        parallel_safety="unsafe",
    ),
    _manifest(
        "Glob",
        "List workspace paths matching a glob pattern.",
        permission="read",
        modes=("plan", "act"),
        tags=("workspace", "search", "read"),
        parallel_safety="readonly",
    ),
    _manifest(
        "Grep",
        "Search workspace text with a pattern.",
        permission="read",
        modes=("plan", "act"),
        tags=("workspace", "search", "read"),
        parallel_safety="readonly",
    ),
    _manifest(
        "Bash",
        "Run a shell command in the workspace.",
        permission="execute",
        modes=("act",),
        tags=("workspace", "command", "execute", "requires-approval"),
        dangerous=True,
        mutates_workspace=True,
        timeout_ms=120_000,
        parallel_safety="unsafe",
    ),
    _manifest(
        "TestRun",
        "Run a project verification command.",
        permission="execute",
        modes=("act",),
        tags=("verification", "command", "execute", "requires-approval"),
        dangerous=True,
        mutates_workspace=True,
        timeout_ms=300_000,
        parallel_safety="unsafe",
    ),
    _manifest(
        "GitDiff",
        "Inspect workspace git diff metadata.",
        permission="read",
        modes=("plan", "act"),
        tags=("workspace", "git", "read"),
        parallel_safety="readonly",
    ),
    _manifest(
        "AskUserQuestion",
        "Request user input through the OpenMagi control surface.",
        permission="meta",
        modes=("plan", "act"),
        tags=("runtime", "user", "meta"),
        parallel_safety="unsafe",
    ),
    _manifest(
        "EnterPlanMode",
        "Enter planning mode for non-mutating reasoning.",
        permission="meta",
        modes=("plan", "act"),
        tags=("runtime", "planning", "meta"),
        parallel_safety="unsafe",
    ),
    _manifest(
        "ExitPlanMode",
        "Exit planning mode and continue in act mode.",
        permission="meta",
        modes=("act",),
        tags=("runtime", "planning", "meta"),
        parallel_safety="unsafe",
    ),
    _manifest(
        "ArtifactCreate",
        "Create an artifact record for delivery.",
        permission="write",
        modes=("act",),
        tags=("artifact", "delivery", "write"),
        parallel_safety="unsafe",
    ),
    _manifest(
        "ArtifactRead",
        "Read artifact metadata or content.",
        permission="read",
        modes=("plan", "act"),
        tags=("artifact", "delivery", "read"),
        parallel_safety="readonly",
    ),
    _manifest(
        "ArtifactList",
        "List artifact records available to the turn.",
        permission="read",
        modes=("plan", "act"),
        tags=("artifact", "delivery", "read"),
        parallel_safety="readonly",
    ),
    _manifest(
        "Clock",
        "Read current time metadata.",
        permission="meta",
        modes=("plan", "act"),
        tags=("utility", "time", "meta"),
        parallel_safety="readonly",
    ),
    _manifest(
        "Calculation",
        "Evaluate deterministic calculation metadata.",
        permission="meta",
        modes=("plan", "act"),
        tags=("utility", "calculation", "meta"),
        parallel_safety="readonly",
    ),
    _manifest(
        "HealthStatus",
        "Read local runtime health metadata.",
        permission="meta",
        modes=("plan", "act"),
        tags=("runtime", "health", "meta"),
        parallel_safety="readonly",
    ),
    _manifest(
        "TaskList",
        "List local background task metadata.",
        permission="meta",
        modes=("plan", "act"),
        tags=("task", "background", "meta"),
        parallel_safety="readonly",
    ),
    _manifest(
        "TaskGet",
        "Read local background task metadata.",
        permission="meta",
        modes=("plan", "act"),
        tags=("task", "background", "meta"),
        parallel_safety="readonly",
    ),
    _manifest(
        "TaskOutput",
        "Read local background task output metadata.",
        permission="meta",
        modes=("plan", "act"),
        tags=("task", "background", "meta"),
        parallel_safety="readonly",
    ),
    _manifest(
        "CronList",
        "List local cron schedule metadata.",
        permission="meta",
        modes=("plan", "act"),
        tags=("cron", "background", "meta"),
        parallel_safety="readonly",
    ),
)


def core_tool_manifests() -> tuple[ToolManifest, ...]:
    return tuple(manifest.model_copy(deep=True) for manifest in _CORE_TOOL_MANIFESTS)


def register_core_tool_manifests(registry: ToolRegistry) -> tuple[ToolManifest, ...]:
    manifests = core_tool_manifests()
    for manifest in manifests:
        registry.register(manifest.model_copy(deep=True))
    return manifests
