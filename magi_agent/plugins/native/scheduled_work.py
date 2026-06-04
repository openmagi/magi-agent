from __future__ import annotations

from magi_agent.plugins.native._common import digest, ok_result
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.policy import redact_public_text


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
    return _record("CronCreate", safe_args, context)


def cron_list(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return ok_result("CronList", {"items": (), "localOnly": True, "argumentsDigest": digest(arguments)})


def cron_update(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return _record("CronUpdate", arguments, context)


def cron_delete(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return _record("CronDelete", arguments, context)


def task_wait(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return _record("TaskWait", arguments, context)


def task_get(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return _record("TaskGet", arguments, context)


def task_list(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return ok_result("TaskList", {"items": (), "localOnly": True, "argumentsDigest": digest(arguments)})


def task_output(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return _record("TaskOutput", arguments, context)


def task_stop(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return _record("TaskStop", arguments, context)
