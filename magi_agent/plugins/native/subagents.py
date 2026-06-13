from __future__ import annotations

import inspect
import os
import re
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path

from magi_agent.plugins.native._common import digest, ok_result
from magi_agent.runtime.public_events import child_progress_event
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult, ToolStatus

_LEGACY_RUNTIME_ENV_PREFIX = "CORE" + "_AGENT_"

_HOSTED_WORKSPACE_ENV_KEYS = (
    "MAGI_AGENT_WORKSPACE",
    "MAGI_WORKSPACE_ROOT",
    "MAGI_WORKSPACE",
    f"{_LEGACY_RUNTIME_ENV_PREFIX}PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT",
    f"{_LEGACY_RUNTIME_ENV_PREFIX}PYTHON_MEMORY_WORKSPACE_ROOT",
)
_PUBLIC_CHILD_TASK_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,120}$")


def _public_child_task_id(value: object, *, fallback: str) -> str:
    for candidate in (value, fallback):
        if not isinstance(candidate, str):
            continue
        text = candidate.strip()
        if not text:
            continue
        if _PUBLIC_CHILD_TASK_ID_RE.fullmatch(text):
            return text
        return f"child-{digest(text)[7:23]}"
    return f"child-{uuid.uuid4().hex[:12]}"


def _child_event_receipt_ref(*, parent_execution_id: str, task_id: str) -> str:
    return "receipt:" + digest(
        {
            "parentExecutionId": parent_execution_id,
            "surface": "spawn_agent_child_lifecycle",
            "taskId": task_id,
        }
    )


async def _emit_agent_event(context: ToolContext, event: Mapping[str, object]) -> None:
    emitter = context.emit_agent_event
    if not callable(emitter):
        return
    try:
        result = emitter(dict(event))
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001 — progress events must not affect tool execution.
        return


async def _emit_child_started(
    context: ToolContext,
    *,
    task_id: str,
    parent_turn_id: str,
    child_receipt_ref: str,
) -> None:
    await _emit_agent_event(
        context,
        {
            "type": "child_started",
            "taskId": task_id,
            "parentTurnId": parent_turn_id,
            "detail": "Delegated child started",
            "childReceiptRef": child_receipt_ref,
        },
    )
    await _emit_agent_event(
        context,
        {
            "type": "child_progress",
            "taskId": task_id,
            "detail": "Running delegated child",
            "childReceiptRef": child_receipt_ref,
        },
    )


async def _emit_child_finished(
    context: ToolContext,
    *,
    task_id: str,
    child_receipt_ref: str,
    status: ToolStatus,
    error_code: str | None,
) -> None:
    if status == "ok":
        await _emit_agent_event(
            context,
            {
                "type": "child_completed",
                "taskId": task_id,
                "childReceiptRef": child_receipt_ref,
            },
        )
        return
    if status == "error":
        await _emit_agent_event(
            context,
            {
                "type": "child_failed",
                "taskId": task_id,
                "errorMessage": error_code or "child_runner_error",
                "childReceiptRef": child_receipt_ref,
            },
        )
        return
    await _emit_agent_event(
        context,
        {
            "type": "child_cancelled",
            "taskId": task_id,
            "reason": error_code or "child_runner_blocked",
            "childReceiptRef": child_receipt_ref,
        },
    )


def _spawn_agent_result(
    status: ToolStatus,
    output: Mapping[str, object],
    *,
    error_code: str | None = None,
) -> ToolResult:
    safe_output = dict(output)
    output_digest = digest(safe_output)
    metadata: dict[str, object] = {
        "toolName": "SpawnAgent",
        "handler": "first_party_native_local",
        "outputDigest": output_digest,
    }
    if error_code:
        metadata["reason"] = error_code
    return ToolResult(
        status=status,
        output=safe_output,
        llmOutput=safe_output,
        transcriptOutput={
            "toolName": "SpawnAgent",
            "outputDigest": output_digest,
        },
        errorCode=error_code,
        errorMessage=error_code,
        metadata=metadata,
    )


def _child_result_status_and_reason(result: object) -> tuple[ToolStatus, str | None, str]:
    envelope = getattr(result, "envelope", None)
    envelope_status = str(getattr(envelope, "status", "") or "")
    boundary_status = str(getattr(result, "status", "") or "blocked")
    boundary_error = getattr(result, "error_code", None)
    summary = str(getattr(envelope, "summary", "") or "")

    if boundary_status == "ok" and envelope_status in {"", "completed"}:
        return "ok", None, "ok"
    if boundary_status == "error" or envelope_status == "failed":
        return "error", str(boundary_error or "live_child_runner_error"), "error"
    reason = str(boundary_error or summary or "child_runner_blocked")
    return "blocked", reason, "blocked"


def _safe_workspace_candidate(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        path = Path(stripped).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".magi-child-probe-{uuid.uuid4().hex[:12]}"
        probe.mkdir()
        probe.rmdir()
    except Exception:  # noqa: BLE001 — candidate is simply unusable.
        return None
    return str(path)


def _first_writable_workspace_root(context: ToolContext) -> str | None:
    for candidate in (context.spawn_workspace, context.workspace_root):
        workspace = _safe_workspace_candidate(candidate)
        if workspace is not None:
            return workspace
    for key in _HOSTED_WORKSPACE_ENV_KEYS:
        workspace = _safe_workspace_candidate(os.environ.get(key))
        if workspace is not None:
            return workspace
    return None


def _default_temp_child_workspace() -> str | None:
    try:
        return tempfile.mkdtemp(prefix="magi-child-")
    except Exception:  # noqa: BLE001 — hosted read-only roots land here.
        return None


def _isolated_child_workspace_under(workspace_root: str) -> str | None:
    try:
        child_root = Path(workspace_root) / ".magi" / "child-workspaces"
        child_root.mkdir(parents=True, exist_ok=True)
        return tempfile.mkdtemp(prefix="magi-child-", dir=str(child_root))
    except Exception:  # noqa: BLE001 — fall back to the default tempdir path.
        return None


def _child_workspace_for_toolset(
    toolset_profile: str,
    context: ToolContext,
) -> tuple[str | None, str | None]:
    if toolset_profile == "none":
        return None, None

    workspace_root = _first_writable_workspace_root(context)
    if toolset_profile == "readonly":
        if workspace_root is not None:
            return workspace_root, None
        fallback = _default_temp_child_workspace()
        if fallback is not None:
            return fallback, None
        return None, "child_workspace_unavailable"

    # ``full`` can mutate, so give it an isolated writable child directory when
    # a parent workspace is available. This keeps hosted read-only-root pods
    # working while preserving separation for mutating children.
    if workspace_root is not None:
        isolated = _isolated_child_workspace_under(workspace_root)
        if isolated is not None:
            return isolated, None
    fallback = _default_temp_child_workspace()
    if fallback is not None:
        return fallback, None
    return None, "child_workspace_unavailable"


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
        return _spawn_agent_result(
            "blocked",
            output,
            error_code="live_child_runner_disabled",
        )

    # --- LIVE path (gate ON): route through real child runner -----------------
    # All imports of runner/boundary/config types are LAZY (inside this function
    # only) so the module stays import-clean when the gate is off.
    #
    # Design:
    # 1. Build a ChildTaskRequest from the tool arguments.
    # 2. Construct RealLocalChildRunner with a toolset PROFILE resolved from the
    #    MAGI_CHILD_RUNNER_TOOLSET gate (PR1, doc 07). Default (unset / "none")
    #    keeps the historical text-only empty toolset; "readonly" forwards the
    #    non-mutating inspection tools against the caller workspace; "full" is
    #    gated upstream by the permission-unification follow-up and receives an
    #    isolated child workspace under the writable parent workspace.
    # 3. Build ChildRunnerConfig with live gate ON.
    # 4. await boundary.run(request) on the dispatch event loop.
    # 5. Project the sanitised result envelope into the ToolResult output.
    # Any exception on the live path falls back to a blocked ToolResult (never
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
            context.session_id or context.turn_id or f"spawn-parent-{uuid.uuid4().hex[:12]}"
        )
        turn_id = context.turn_id or f"spawn-turn-{uuid.uuid4().hex[:12]}"
        task_id = _public_child_task_id(
            context.tool_use_id,
            fallback=f"spawn-task-{uuid.uuid4().hex[:12]}",
        )
        child_receipt_ref = _child_event_receipt_ref(
            parent_execution_id=parent_exec_id,
            task_id=task_id,
        )

        # Provider/model from arguments (optional per-task override), else the
        # ChildRunnerConfig defaults will be used by the runner's route resolver.
        req_provider: str | None = str(arguments["provider"]) if arguments.get("provider") else None
        req_model: str | None = str(arguments["model"]) if arguments.get("model") else None

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

        toolset_profile = resolve_child_toolset_profile()
        child_workspace, workspace_error = _child_workspace_for_toolset(
            toolset_profile,
            context,
        )
        if workspace_error is not None:
            fallback_output = {
                "status": "blocked",
                "persona": persona,
                "promptDigest": digest(prompt),
                "spawnDepth": context.spawn_depth,
                "liveChildRunnerAttached": False,
            }
            return _spawn_agent_result(
                "blocked",
                fallback_output,
                error_code=workspace_error,
            )

        async def _emit_live_child_progress(event: Mapping[str, object]) -> None:
            detail = event.get("detail")
            if not isinstance(detail, str) or not detail.strip():
                return
            payload = child_progress_event(task_id=task_id, detail=detail)
            payload["childReceiptRef"] = child_receipt_ref
            await _emit_agent_event(context, payload)

        runner = RealLocalChildRunner(
            toolset_profile=toolset_profile,
            workspace_root=child_workspace,
            progress_sink=_emit_live_child_progress,
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
        await _emit_child_started(
            context,
            task_id=task_id,
            parent_turn_id=turn_id,
            child_receipt_ref=child_receipt_ref,
        )
        result = await boundary.run(request)

        # Project the sanitised envelope into the output payload.
        envelope = result.envelope
        summary = envelope.summary if envelope is not None else ""
        tool_status, error_code, output_status = _child_result_status_and_reason(result)
        await _emit_child_finished(
            context,
            task_id=task_id,
            child_receipt_ref=child_receipt_ref,
            status=tool_status,
            error_code=error_code,
        )

        output = {
            "status": output_status,
            "persona": persona,
            "promptDigest": digest(prompt),
            "spawnDepth": context.spawn_depth,
            "liveChildRunnerAttached": True,
            # Sanitised summary from the envelope (already redacted by boundary).
            "summary": summary,
        }
        return _spawn_agent_result(tool_status, output, error_code=error_code)

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
        return _spawn_agent_result(
            "blocked",
            fallback_output,
            error_code="live_child_runner_attach_failed",
        )


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
