from __future__ import annotations

from .manifest import Budget, ParallelSafety, PermissionClass, RuntimeMode, ToolManifest, ToolSource
from .registry import ToolRegistry


CORE_TOOL_SOURCE = ToolSource(kind="builtin", package="openmagi.core")
CORE_TOOL_INPUT_SCHEMA: dict[str, object] = {"type": "object", "additionalProperties": True}

# Structured schema for TodoWrite so the model knows the exact payload shape:
# a full task list, each item carrying free-text ``content`` and a lifecycle
# ``status`` of pending | in_progress | completed.
TODO_WRITE_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["todos"],
    "properties": {
        "todos": {
            "type": "array",
            "description": "The full task list, replacing any previous list.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["content", "status"],
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Short description of the task.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed"],
                    },
                },
            },
        }
    },
}


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
    input_schema: dict[str, object] | None = None,
) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=description,
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission=permission,
        input_schema=input_schema if input_schema is not None else CORE_TOOL_INPUT_SCHEMA,
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
        "TodoWrite",
        "Record and update the agent's task list for multi-step work.",
        permission="meta",
        modes=("plan", "act"),
        tags=("task", "planning", "meta"),
        parallel_safety="unsafe",
        input_schema=TODO_WRITE_INPUT_SCHEMA,
    ),
    _manifest(
        "FileRead",
        "Read workspace file contents.",
        permission="read",
        modes=("plan", "act"),
        tags=("workspace", "file", "read"),
        parallel_safety="readonly",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative path to the file to read.",
                },
            },
            "required": ["path"],
        },
    ),
    _manifest(
        "FileWrite",
        "Write workspace file contents.",
        permission="write",
        modes=("act",),
        tags=("workspace", "file", "write"),
        mutates_workspace=True,
        parallel_safety="unsafe",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative path to the file to write (created if missing, overwritten if present).",
                },
                "content": {
                    "type": "string",
                    "description": "Full new contents of the file.",
                },
            },
            "required": ["path", "content"],
        },
    ),
    _manifest(
        "FileEdit",
        "Edit an existing workspace file by replacing an exact snippet of text.",
        permission="write",
        modes=("act",),
        tags=("workspace", "file", "edit"),
        mutates_workspace=True,
        parallel_safety="unsafe",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative path to the file to edit.",
                },
                "old_text": {
                    "type": "string",
                    "description": "Exact existing text to replace. Must appear in the file; include enough surrounding context to match uniquely.",
                },
                "new_text": {
                    "type": "string",
                    "description": "Replacement text for old_text.",
                },
            },
            "required": ["path", "old_text", "new_text"],
        },
    ),
    _manifest(
        "PatchApply",
        "Apply a Codex-style multi-file envelope patch (add/update/delete/move).",
        permission="write",
        modes=("act",),
        tags=("workspace", "file", "patch", "edit"),
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
    # D2: agent-callable declarative memory write — default OFF, act-mode only.
    # Only DECLARATIVE facts (stable preferences, user traits) may be persisted.
    # Task-state (PR numbers, SHAs, "done/merged") is rejected at the boundary.
    # Real writes require MAGI_MEMORY_WRITE_ENABLED=1 AND an injected provider.
    ToolManifest(
        name="MemoryWrite",
        description=(
            "Persist a declarative fact about the user or session to long-term memory. "
            "Only stable preferences and user-level facts are accepted — task-state "
            "(PR numbers, commit SHAs, 'done/merged/in progress') is rejected. "
            "Writes are gated: real persistence requires MAGI_MEMORY_WRITE_ENABLED=1."
        ),
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="write",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["fact"],
            "properties": {
                "fact": {
                    "type": "string",
                    "description": (
                        "The declarative fact to remember. "
                        "Must be a stable user preference or trait, not a task event."
                    ),
                    "maxLength": 2000,
                },
                "target_file": {
                    "type": "string",
                    "description": "Target file: 'MEMORY.md' (default) or 'USER.md'.",
                    "enum": ["MEMORY.md", "USER.md"],
                },
            },
        },
        timeout_ms=10_000,
        budget=Budget(max_calls_per_turn=5, max_parallel=1),
        dangerous=False,
        is_concurrency_safe=False,
        mutates_workspace=True,
        parallel_safety="unsafe",
        available_in_modes=("act",),
        tags=("memory", "write", "declarative"),
        enabled_by_default=False,  # gate-off by default
        opt_out=True,
    ),
    # Self-introspection (pull) — default OFF, gated by
    # MAGI_SELF_INTROSPECTION_ENABLED. Read-only/introspective: it only projects
    # the session evidence ledger (never raw transcript) so the model can
    # truthfully answer questions about its own prior actions. Real availability
    # is flipped on at bind time (see runtime wiring) only when the env gate is
    # truthy — mirrors the MemoryWrite bound-but-not-advertised pattern.
    ToolManifest(
        name="InspectSelfEvidence",
        description=(
            "Inspect your own recorded runtime evidence for this session — which "
            "files you actually read, which tools you called (and their status), "
            "which workflow phases you reached, and verifier verdicts. Use this to "
            "truthfully answer questions about your own prior actions (\"did you "
            "really read X?\", \"did you follow the workflow?\") instead of "
            "guessing. Returns a compact projection of the evidence ledger, never "
            "raw transcript."
        ),
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="meta",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["query_type"],
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": [
                        "files_read",
                        "tools_called",
                        "phases",
                        "verifier_verdicts",
                        "summary",
                    ],
                    "description": (
                        "Which evidence slice to return. 'summary' returns all "
                        "slices; the others return just that slice (plus "
                        "scope+note)."
                    ),
                },
                "turn": {
                    "type": ["string", "null"],
                    "description": (
                        "Optional turn_id to scope the projection to a single "
                        "turn. Omit (or null) for the whole session."
                    ),
                },
                "ref": {
                    "type": ["string", "null"],
                    "description": (
                        "Optional case-insensitive substring filter applied to "
                        "the relevant slice's identifier (file path / tool name / "
                        "phase name / verdict stage)."
                    ),
                },
            },
        },
        timeout_ms=10_000,
        budget=Budget(max_calls_per_turn=10, max_parallel=1),
        dangerous=False,
        is_concurrency_safe=True,
        mutates_workspace=False,
        parallel_safety="readonly",
        available_in_modes=("plan", "act"),
        tags=("introspection", "evidence", "meta", "read"),
        enabled_by_default=False,  # gate-off by default
        opt_out=True,
    ),
)


def core_tool_manifests() -> tuple[ToolManifest, ...]:
    return tuple(manifest.model_copy(deep=True) for manifest in _CORE_TOOL_MANIFESTS)


def register_core_tool_manifests(registry: ToolRegistry) -> tuple[ToolManifest, ...]:
    manifests = core_tool_manifests()
    for manifest in manifests:
        registry.register(manifest.model_copy(deep=True))
    return manifests
