from __future__ import annotations

import os
from collections.abc import Mapping

from magi_agent.config.env import _is_true, native_receipts_honest
from magi_agent.plugins.native._common import blocked_result, digest, ok_result
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.policy import redact_public_text

# Backing-system attachment flags. These are owned by the always-on/scheduler
# cluster (03); until that cluster wires a real job store / background-task
# runtime they stay unset, so the honest branch fires. When set, the handler
# routes past the honest block to the (cluster-03-owned) live delegation seam.
SCHEDULER_ATTACHED_ENV = "MAGI_SCHEDULER_ATTACHED"
BACKGROUND_TASKS_ATTACHED_ENV = "MAGI_BACKGROUND_TASKS_ATTACHED"
# Exposes the live ``RunInBackground`` enqueue entrypoint. OFF (default) keeps
# the honest block; flipping it ON ALSO requires ``BACKGROUND_TASKS_ATTACHED``
# (a real work-queue store) before a task is actually created.
BACKGROUND_TASK_TOOL_ENABLED_ENV = "MAGI_BACKGROUND_TASK_TOOL_ENABLED"

_MAX_TITLE_CHARS = 200
_MAX_BODY_CHARS = 4000


def _env(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def _scheduler_attached(env: Mapping[str, str] | None = None) -> bool:
    return _is_true(_env(env).get(SCHEDULER_ATTACHED_ENV))


def _background_tasks_attached(env: Mapping[str, str] | None = None) -> bool:
    return _is_true(_env(env).get(BACKGROUND_TASKS_ATTACHED_ENV))


def background_task_tool_enabled(env: Mapping[str, str] | None = None) -> bool:
    return _is_true(_env(env).get(BACKGROUND_TASK_TOOL_ENABLED_ENV))


def _record(tool_name: str, arguments: dict[str, object], context: ToolContext) -> ToolResult:
    payload = {
        "toolName": tool_name,
        "botId": context.bot_id,
        "sessionId": context.session_id,
        "localOnly": True,
        "argumentsDigest": digest(arguments),
    }
    return ok_result(tool_name, payload)


def cron_create(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    safe_args = {
        "schedule": redact_public_text(str(arguments.get("schedule") or ""), max_chars=120),
        "task": redact_public_text(str(arguments.get("task") or arguments.get("prompt") or ""), max_chars=500),
    }
    if native_receipts_honest() and not _scheduler_attached():
        return blocked_result("CronCreate", "cron_not_configured")
    return _record("CronCreate", safe_args, context)


def cron_list(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return ok_result(
        "CronList",
        {
            "items": (),
            "localOnly": True,
            "schedulerAttached": _scheduler_attached(),
            "argumentsDigest": digest(arguments),
        },
    )


def cron_update(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    if native_receipts_honest() and not _scheduler_attached():
        return blocked_result("CronUpdate", "cron_not_configured")
    return _record("CronUpdate", arguments, context)


def cron_delete(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    if native_receipts_honest() and not _scheduler_attached():
        return blocked_result("CronDelete", "cron_not_configured")
    return _record("CronDelete", arguments, context)


def task_wait(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    if native_receipts_honest() and not _background_tasks_attached():
        return blocked_result("TaskWait", "background_tasks_not_configured")
    return _record("TaskWait", arguments, context)


def task_get(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    if native_receipts_honest() and not _background_tasks_attached():
        return blocked_result("TaskGet", "background_tasks_not_configured")
    return _record("TaskGet", arguments, context)


def task_list(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return ok_result(
        "TaskList",
        {
            "items": (),
            "localOnly": True,
            "backgroundTasksAttached": _background_tasks_attached(),
            "argumentsDigest": digest(arguments),
        },
    )


def task_output(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    if native_receipts_honest() and not _background_tasks_attached():
        return blocked_result("TaskOutput", "background_tasks_not_configured")
    return _record("TaskOutput", arguments, context)


def task_stop(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    if native_receipts_honest() and not _background_tasks_attached():
        return blocked_result("TaskStop", "background_tasks_not_configured")
    return _record("TaskStop", arguments, context)


# ---------------------------------------------------------------------------
# RunInBackground — enqueue a task on the durable work-queue.
# ---------------------------------------------------------------------------
# Entrance seam for the /workflows-style UX: the model (or a user request the
# model relays) puts a long-horizon task on the work-queue and the chat turn
# ends without blocking. Live behaviour requires BOTH flags:
#   * MAGI_BACKGROUND_TASK_TOOL_ENABLED — exposes this entrypoint live, and
#   * MAGI_BACKGROUND_TASKS_ATTACHED   — a real SqliteWorkQueueStore is wired.
# Either off -> honest blocked_result; no task is created. Once both are on the
# dispatcher (PR3) consumes the task; today the entry just lands in the store.


def _clamp(value: object, max_chars: int) -> str:
    raw = "" if value is None else str(value)
    return redact_public_text(raw, max_chars=max_chars)


def _emit_mission_created(context: ToolContext, task: object) -> None:
    """PR-M5 seam 1 (live bridge, create-time): surface the durable WorkTask
    as a ``mission_created`` agent event on the live turn stream.

    Rides the SAME pending-agent-events path SpawnAgent uses
    (``chat_routes_local._push_agent_event`` -> SSE ``agent`` frame ->
    ``local_turn_store._upsert_mission``), so the chat "Work" tab (live) and
    the "Missions" tab (durable ledger) are two views of the same row from the
    moment the task is created. No new channel: it reuses
    ``context.emit_agent_event``.

    Best-effort and non-blocking: a missing emitter (no live turn wired the
    emitter, e.g. a hosted non-serve path) or a raising emitter must never
    affect the tool result. The mission status is projected through the M1
    kernel (``projection.map_task_status``), never the raw ``TaskStatus``, and
    the id is the BARE durable task id (the ephemeral ``goal:{turn_id}`` scheme
    belongs only to the in-memory goal mission in ``chat_routes_local``).
    """
    emitter = getattr(context, "emit_agent_event", None)
    if not callable(emitter):
        return
    try:
        import inspect
        import time as _time

        from magi_agent.missions.projection import project_task_to_mission_summary

        mission = project_task_to_mission_summary(task)
        event = {
            "type": "mission_created",
            "mission": {
                "id": mission["id"],
                "title": mission["title"],
                "kind": mission["kind"],
                "status": mission["status"],
                "createdAt": int(_time.time() * 1000),
                "metadata": {
                    "workTaskId": mission["id"],
                    "workQueueStatus": mission["metadata"]["work_queue_status"],
                },
            },
        }
        result = emitter(dict(event))
        # The wired serve emitter (``_push_agent_event``) is sync and returns
        # None. run_in_background is a sync tool so we cannot await; if a future
        # emitter returns a coroutine, close it to avoid an unawaited warning
        # rather than block (best-effort progress event).
        if inspect.iscoroutine(result):
            result.close()
    except Exception:  # noqa: BLE001 - a progress emit must never fail the tool.
        return


def run_in_background(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    title = _clamp(arguments.get("title"), _MAX_TITLE_CHARS).strip()
    body = _clamp(arguments.get("body"), _MAX_BODY_CHARS).strip() or None

    if not background_task_tool_enabled():
        return blocked_result("RunInBackground", "background_task_tool_disabled")
    if native_receipts_honest() and not _background_tasks_attached():
        return blocked_result("RunInBackground", "background_tasks_not_configured")
    if not title:
        return blocked_result("RunInBackground", "title_required")

    # Imported lazily so the module stays cheap when the tool is disabled.
    from magi_agent.missions.work_queue.models import WorkTask
    from magi_agent.missions.work_queue.store import (
        SqliteWorkQueueStore,
        work_queue_db_path_from_env,
    )
    import time as _time
    import uuid as _uuid

    session_id = context.session_id
    idem_payload = {"session": session_id, "title": title, "body": body}
    idempotency_key = digest(idem_payload)
    goal_mode = bool(arguments.get("goal_mode"))
    raw_max_turns = arguments.get("goal_max_turns")
    goal_max_turns = int(raw_max_turns) if isinstance(raw_max_turns, (int, float)) else None

    task = WorkTask(
        id=str(_uuid.uuid4()),
        title=title,
        body=body,
        status="todo",
        created_at=int(_time.time()),
        session_id=session_id,
        idempotency_key=idempotency_key,
        goal_mode=goal_mode,
        goal_max_turns=goal_max_turns,
    )

    store = SqliteWorkQueueStore(work_queue_db_path_from_env())
    stored = store.create_idempotent(task)

    # PR-M5 seam 1: emit the create-time live mission event (best-effort).
    _emit_mission_created(context, stored)

    # PR-M7 hosted projection: create seam (section 7.2). Fail-open and
    # non-blocking; inert (no-op) unless the projector config is present.
    from magi_agent.missions.projector import notify_task_created  # noqa: PLC0415

    notify_task_created(stored)

    short_id = stored.id[:6]
    ack = (
        f"Started in background (task {short_id}) — track on the work-queue board "
        f"or via /tasks. The result will return in our next reply when it completes."
    )
    return ok_result(
        "RunInBackground",
        {
            "taskId": stored.id,
            "status": stored.status,
            "title": stored.title,
            "goalMode": stored.goal_mode,
            "ack": ack,
            "argumentsDigest": idempotency_key,
        },
    )
