from __future__ import annotations

import uuid

from magi_agent.plugins.native._common import digest, ok_result
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult


async def spawn_agent(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    prompt = str(arguments.get("prompt") or arguments.get("task") or "")
    persona = str(arguments.get("persona") or "general")

    # --- DEFAULT (gate OFF): byte-identical to original local-fake payload ----
    # The gate is evaluated lazily (inside the function, not at import time) so
    # that importing this module NEVER pulls child_runner_live / litellm /
    # google.adk into sys.modules.  When the gate is off we return exactly the
    # same keys/values as before — no new fields, no reordering.
    #
    # The lazy import is ONLY executed when the function is called; it is NEVER
    # executed at module load time.
    from magi_agent.runtime.child_runner_live import (  # noqa: PLC0415
        is_live_child_runner_enabled,
    )

    if not is_live_child_runner_enabled():
        output = {
            "status": "queued_locally",
            "persona": persona,
            "promptDigest": digest(prompt),
            "spawnDepth": context.spawn_depth,
            "liveChildRunnerAttached": False,
        }
        return ok_result("SpawnAgent", output)

    # --- LIVE path (gate ON): route through real child runner -----------------
    # All imports of runner/boundary/config types are LAZY (inside this function
    # only) so the module stays import-clean when the gate is off.
    #
    # Design:
    # 1. Build a ChildTaskRequest from the tool arguments.
    # 2. Construct RealLocalChildRunner(tools=[]) — HARD-WIRED empty toolset to
    #    enforce v1 text-only; caller-supplied tools are intentionally NOT
    #    forwarded (prevents accidental tool escalation).
    # 3. Build ChildRunnerConfig with live gate ON.
    # 4. await boundary.run(request) on the dispatch event loop.
    # 5. Project the sanitised result envelope into the ToolResult output.
    # Any exception on the live path falls back to a blocked/ok result (never
    # raises out of spawn_agent).
    #
    # NOTE (agents-counter): a single spawn_agent call spawns exactly one child,
    # so we pass agents_spawned_so_far=0 (a per-call snapshot).  Run-level
    # accumulation across many successive spawn_agent calls (fan-out) is a future
    # concern — the boundary documents this as the caller's responsibility; the
    # boundary does NOT self-increment.
    try:
        from magi_agent.runtime.child_runner_boundary import (  # noqa: PLC0415
            ChildRunnerConfig,
            ChildTaskRequest,
            LocalChildRunnerBoundary,
        )
        from magi_agent.runtime.child_runner_live import (  # noqa: PLC0415
            RealLocalChildRunner,
        )

        # Build the request — use the context's IDs as parent identifiers, or
        # fall back to stable generated values so the request is always valid.
        parent_exec_id = (
            context.session_id
            or context.turn_id
            or f"spawn-parent-{uuid.uuid4().hex[:12]}"
        )
        turn_id = context.turn_id or f"spawn-turn-{uuid.uuid4().hex[:12]}"
        task_id = f"spawn-task-{uuid.uuid4().hex[:12]}"

        # Provider/model from arguments (optional per-task override), else the
        # ChildRunnerConfig defaults will be used by the runner's route resolver.
        req_provider: str | None = (
            str(arguments["provider"]) if arguments.get("provider") else None
        )
        req_model: str | None = (
            str(arguments["model"]) if arguments.get("model") else None
        )

        # budget_ms from arguments (optional).
        # LLM tool calls often encode numbers as strings; accept int/float OR a
        # string that int()-parses to a non-negative value.  Bools and
        # unparseable strings produce 0 (never-crash convention).
        budget_ms_raw = arguments.get("budget_ms") or arguments.get("budgetMs")
        if isinstance(budget_ms_raw, bool):
            budget_ms = 0
        elif isinstance(budget_ms_raw, int | float):
            budget_ms = int(budget_ms_raw)
        elif isinstance(budget_ms_raw, str):
            try:
                budget_ms = max(0, int(budget_ms_raw))
            except (ValueError, OverflowError):
                budget_ms = 0
        else:
            budget_ms = 0

        request = ChildTaskRequest(
            parentExecutionId=parent_exec_id,
            turnId=turn_id,
            taskId=task_id,
            objective=prompt or "Complete the delegated subtask.",
            # Carry the caller's spawn_depth into the request metadata so the
            # boundary's spawn-depth cap can enforce it correctly.
            metadata={"spawnDepth": context.spawn_depth + 1},
            provider=req_provider,
            model=req_model,
            budgetMs=budget_ms,
        )

        # HARD-WIRE tools=[] (v1 text-only enforcement — no caller-supplied tools
        # are forwarded; this prevents accidental tool escalation in child turns).
        runner = RealLocalChildRunner(tools=[])

        config = ChildRunnerConfig(
            enabled=True,
            liveChildRunnerEnabled=True,
        )

        # Drive the async boundary directly on the dispatch event loop. The tool
        # dispatcher awaits coroutine handlers (and never thread-offloads them),
        # so awaiting here runs the child without spinning a nested event loop —
        # the previous asyncio.run() raised RuntimeError on the live loop and
        # silently degraded every production call to "blocked".
        boundary = LocalChildRunnerBoundary(
            config,
            child_runner=runner,
            agents_spawned_so_far=0,
        )
        result = await boundary.run(request)

        # Project the sanitised envelope into the output payload.
        envelope = result.envelope
        summary = envelope.summary if envelope is not None else ""
        status = result.status if result.status else "blocked"

        output = {
            "status": status,
            "persona": persona,
            "promptDigest": digest(prompt),
            "spawnDepth": context.spawn_depth,
            "liveChildRunnerAttached": True,
            # Sanitised summary from the envelope (already redacted by boundary).
            "summary": summary,
        }
        return ok_result("SpawnAgent", output)

    except Exception:  # noqa: BLE001 — NEVER raise out of spawn_agent
        # Any failure on the live path (missing key, unknown route, event-loop
        # conflict, construction error) degrades to a safe blocked result.
        # liveChildRunnerAttached=False because the live runner was not
        # successfully attached/completed.
        fallback_output = {
            "status": "blocked",
            "persona": persona,
            "promptDigest": digest(prompt),
            "spawnDepth": context.spawn_depth,
            "liveChildRunnerAttached": False,
        }
        return ok_result("SpawnAgent", fallback_output)


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
