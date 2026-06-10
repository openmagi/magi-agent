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
        # HONEST receipt (D4 fix): the previous "queued_locally" literal implied
        # the task was accepted into a queue, but no child runner is attached or
        # scheduled — nothing happens.  We surface an explicit not-attached status
        # plus a machine-readable reason and a human activation hint.  All legacy
        # keys are preserved so existing consumers keep working.
        output = {
            "status": "not_attached",
            "reason": "live_child_runner_disabled",
            "hint": (
                "Live child runner is disabled. Set "
                "MAGI_CHILD_RUNNER_LIVE_ENABLED=1 to spawn a real subagent."
            ),
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
    # 2. Construct RealLocalChildRunner with a toolset PROFILE resolved from the
    #    MAGI_CHILD_RUNNER_TOOLSET gate (PR1, doc 07). Default (unset / "none")
    #    keeps the historical text-only empty toolset; "readonly" forwards the
    #    non-mutating inspection tools (FileRead/Glob/Grep/GitDiff); "full" is
    #    gated upstream by the permission-unification follow-up. The child runs
    #    in an ISOLATED workspace tempdir so it can never mutate the parent cwd.
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
        from magi_agent.runtime.child_toolset import (  # noqa: PLC0415
            resolve_child_toolset_profile,
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

        # Resolve the toolset profile from the dedicated env gate. The gate is
        # the final authority (a caller-supplied "toolsetProfile" argument never
        # ESCALATES past it); default unset/"none" => empty toolset (text-only).
        # The child gets an ISOLATED workspace tempdir so a tool-enabled child
        # can never mutate the parent's working directory.
        import tempfile  # noqa: PLC0415

        toolset_profile = resolve_child_toolset_profile()
        child_workspace = (
            tempfile.mkdtemp(prefix="magi-child-") if toolset_profile != "none" else None
        )
        runner = RealLocalChildRunner(
            toolset_profile=toolset_profile,
            workspace_root=child_workspace,
        )

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
    # HONEST receipt (D4 fix): the previous "review_required" literal implied a
    # patch was staged awaiting review, but no worktree mutation is ever
    # attempted.  Surface an explicit unimplemented status + reason.  Legacy keys
    # (patchDigest / worktreeMutationAttached) are preserved.
    return ok_result(
        "SpawnWorktreeApply",
        {
            "status": "unimplemented",
            "reason": "worktree_apply_not_implemented",
            "patchDigest": patch_digest,
            "worktreeMutationAttached": False,
        },
    )
