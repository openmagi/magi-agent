from __future__ import annotations

import inspect
import os
import re
import tempfile
import uuid
import zlib
from collections.abc import Mapping
from pathlib import Path

from magi_agent.plugins.native._common import digest, ok_result
from magi_agent.runtime.public_events import (
    child_cancelled_event,
    child_completed_event,
    child_failed_event,
    child_progress_event,
    child_started_event,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult, ToolStatus

# Public-safe roster used as deterministic chip names for spawned subagents.
# Mirrors ``apps/web/src/lib/chat/work-console.ts`` ``SUBAGENT_NAMES`` so the
# UI's index-based fallback agrees with backend-supplied names when both fire.
SUBAGENT_NAMES: tuple[str, ...] = (
    "Halley",
    "Meitner",
    "Kant",
    "Noether",
    "Turing",
    "Curie",
    "Hopper",
    "Lovelace",
    "Feynman",
    "Franklin",
    "Shannon",
    "Lamarr",
)

# Cap the chip label so a verbose LLM cannot push a 5KB string into every
# public event.  The UI chip is single-line; 64 characters is comfortable.
_TASK_TITLE_LIMIT = 64


def _agent_name_for_task(task_id: str) -> str:
    """Deterministic agent name from a task id.

    Same ``task_id`` → same name across runs and processes.  Uses CRC32 (a
    non-cryptographic 32-bit hash) so we get good distribution without pulling
    a hashing dependency.  Empty/None falls back to the first roster name.
    """
    if not isinstance(task_id, str) or not task_id:
        return SUBAGENT_NAMES[0]
    index = zlib.crc32(task_id.encode("utf-8")) % len(SUBAGENT_NAMES)
    return SUBAGENT_NAMES[index]


def _model_label(provider: str | None, model: str | None) -> str | None:
    """Format ``"<provider>:<model>"`` when both are present, else ``None``."""
    if not isinstance(provider, str) or not provider.strip():
        return None
    if not isinstance(model, str) or not model.strip():
        return None
    return f"{provider.strip()}:{model.strip()}"


def _sanitized_task_title(arguments: Mapping[str, object]) -> str | None:
    """Extract an opt-in public-safe short label from SpawnAgent arguments.

    The SpawnAgent tool description tells the LLM to pass ``taskTitle`` as a
    SHORT human-readable brief (≤ 64 chars) that's safe to display in the
    UI's per-agent chip.  This is NOT a fallback to ``prompt``/``task`` —
    those carry the actual private prompt body and stay redacted to honour
    the privacy contract enforced by gate5b sanitization tests.
    """
    raw = arguments.get("taskTitle")
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if len(text) > _TASK_TITLE_LIMIT:
        text = text[:_TASK_TITLE_LIMIT].rstrip()
    return text

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
    agent_name: str | None = None,
    model: str | None = None,
    task_title: str | None = None,
) -> None:
    event = child_started_event(
        task_id=task_id,
        parent_turn_id=parent_turn_id,
        child_receipt_ref=child_receipt_ref,
        agent_name=agent_name,
        model=model,
        task_title=task_title,
    )
    await _emit_agent_event(context, event)
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
    summary: str | None = None,
) -> None:
    if status == "ok":
        await _emit_agent_event(
            context,
            child_completed_event(
                task_id=task_id,
                child_receipt_ref=child_receipt_ref,
                summary=summary,
            ),
        )
        return
    if status == "error":
        await _emit_agent_event(
            context,
            child_failed_event(
                task_id=task_id,
                child_receipt_ref=child_receipt_ref,
                error_message=error_code or "child_runner_error",
                summary=summary,
            ),
        )
        return
    await _emit_agent_event(
        context,
        child_cancelled_event(
            task_id=task_id,
            child_receipt_ref=child_receipt_ref,
            reason=error_code or "child_runner_blocked",
            summary=summary,
        ),
    )


def _spawn_agent_result(
    status: ToolStatus,
    output: Mapping[str, object],
    *,
    error_code: str | None = None,
) -> ToolResult:
    safe_output = dict(output)
    output_digest = digest(safe_output)
    live_attached = safe_output.get("liveChildRunnerAttached") is True
    child_runner_availability = safe_output.get("childRunnerAvailability")
    child_execution_failed = safe_output.get("childExecutionFailed")
    metadata: dict[str, object] = {
        "toolName": "SpawnAgent",
        "handler": "first_party_native_local",
        "outputDigest": output_digest,
        "liveChildRunnerAttached": live_attached,
    }
    if isinstance(child_runner_availability, str) and child_runner_availability.strip():
        metadata["childRunnerAvailability"] = child_runner_availability
    if isinstance(child_execution_failed, bool):
        metadata["childExecutionFailed"] = child_execution_failed
    failure_reason = safe_output.get("childFailureReason")
    if isinstance(failure_reason, str) and failure_reason.strip():
        metadata["childFailureReason"] = failure_reason
    if error_code:
        metadata["reason"] = error_code
    # LLM-facing projection: answer-forward and free of bookkeeping noise
    # (promptDigest/spawnDepth/liveChildRunnerAttached/childRunnerAvailability),
    # so the caller reads the child's ACTUAL result instead of mistaking the
    # envelope for "metadata only". The full safe_output stays on `output` for
    # the evidence/observability layer (first_party_activity reads it).
    llm_output: dict[str, object] = {"status": safe_output.get("status")}
    model = safe_output.get("model")
    if isinstance(model, str) and model.strip():
        llm_output["model"] = model
    result_text = safe_output.get("summary")
    if isinstance(result_text, str) and result_text.strip():
        llm_output["result"] = result_text
    reason = error_code or (failure_reason if isinstance(failure_reason, str) else None)
    if isinstance(reason, str) and reason.strip():
        llm_output["reason"] = reason
    return ToolResult(
        status=status,
        output=safe_output,
        llmOutput=llm_output,
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
        # Surface the child's ACTUAL failure reason (carried on the envelope
        # summary, e.g. ``child_llm_collector_status_failed`` /
        # ``child_model_route_unknown``) before falling back to the generic
        # code. Pre-fix, a runner that RETURNED a failed mapping (never raised)
        # left ``error_code`` unset, so the real reason on the summary was
        # dropped and the parent model saw only ``live_child_runner_error`` and
        # confabulated a "connection error". Mirrors the ``blocked`` branch,
        # which already prefers ``summary`` over the generic fallback.
        return "error", str(boundary_error or summary or "live_child_runner_error"), "error"
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

        # Build the metadata dict: carry spawn_depth (boundary cap),
        # parent_tool_names (tighten-only producer, Task 2B.2), and
        # parentMemoryMode (memory-inherit producer, Task F1).
        # MemoryMode is a str-subclass Enum — use .value to emit the canonical
        # bare string ("normal"/"read_only"/"incognito"), never "MemoryMode.X".
        parent_memory_mode_value: str = getattr(
            context.memory_mode, "value", str(context.memory_mode)
        )
        request_metadata: dict[str, object] = {
            "spawnDepth": context.spawn_depth + 1,
            "parentToolNames": context.parent_tool_names,
            "parentMemoryMode": parent_memory_mode_value,
        }
        raw_allowed = arguments.get("allowedTools") or arguments.get("allowed_tools")
        allowed_tools = tuple(
            a for a in (raw_allowed or ()) if isinstance(a, str) and a.strip()
        )
        if allowed_tools:
            request_metadata["allowedTools"] = allowed_tools
        raw_recipe = (
            arguments.get("recipeRefs")
            or arguments.get("recipe_refs")
            or arguments.get("recipe_pack_ids")
        )
        recipe_refs = tuple(
            r for r in (raw_recipe or ()) if isinstance(r, str) and r.strip()
        )
        if recipe_refs:
            request_metadata["recipeRefs"] = recipe_refs
        request = ChildTaskRequest(
            parentExecutionId=parent_exec_id,
            turnId=turn_id,
            taskId=task_id,
            objective=prompt or "Complete the delegated subtask.",
            metadata=request_metadata,
            provider=req_provider,
            model=req_model,
            budgetMs=budget_ms,
            spawnCap=context.spawn_cap,
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
            spawn_cap=request.spawn_cap,
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
            agent_name=_agent_name_for_task(task_id),
            model=_model_label(req_provider, req_model),
            task_title=_sanitized_task_title(arguments),
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
            summary=summary if isinstance(summary, str) and summary.strip() else None,
        )

        output = {
            "status": output_status,
            "persona": persona,
            "promptDigest": digest(prompt),
            "spawnDepth": context.spawn_depth,
            "liveChildRunnerAttached": True,
            "childRunnerAvailability": "live_attached",
            "childExecutionFailed": tool_status != "ok",
            # Sanitised summary from the envelope (already redacted by boundary).
            "summary": summary,
        }
        # Model attribution (provider:model the parent requested) so the caller
        # can tell which model produced which answer — essential for the
        # cross-validation / panel-of-models use case.
        if req_model:
            output["model"] = f"{req_provider or 'anthropic'}:{req_model}"
        if tool_status != "ok" and error_code is not None:
            output["childFailureReason"] = error_code
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
