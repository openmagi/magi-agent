from __future__ import annotations

from magi_agent.plugins.native._common import digest, ok_result
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult


def spawn_agent(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    prompt = str(arguments.get("prompt") or arguments.get("task") or "")
    persona = str(arguments.get("persona") or "general")
    output = {
        "status": "queued_locally",
        "persona": persona,
        "promptDigest": digest(prompt),
        "spawnDepth": context.spawn_depth,
        "liveChildRunnerAttached": False,
    }
    return ok_result("SpawnAgent", output)


def spawn_worktree_apply(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    patch_digest = digest(arguments.get("patch") or arguments.get("diff") or "")
    return ok_result(
        "SpawnWorktreeApply",
        {
            "status": "review_required",
            "patchDigest": patch_digest,
            "worktreeMutationAttached": False,
        },
    )
