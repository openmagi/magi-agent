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


def _env(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def _scheduler_attached(env: Mapping[str, str] | None = None) -> bool:
    return _is_true(_env(env).get(SCHEDULER_ATTACHED_ENV))


def _background_tasks_attached(env: Mapping[str, str] | None = None) -> bool:
    return _is_true(_env(env).get(BACKGROUND_TASKS_ATTACHED_ENV))


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
