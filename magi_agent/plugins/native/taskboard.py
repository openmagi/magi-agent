from __future__ import annotations

import json

from magi_agent.plugins.native._common import digest, ok_result, safe_child_path
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult


def task_board(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    action = str(arguments.get("action") or "list")
    path = safe_child_path(
        context,
        ".magi/taskboard.jsonl",
        default_name=".magi/taskboard.jsonl",
        mutating=action in {"add", "update", "set"},
        allow_internal=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if action in {"add", "update", "set"}:
        record = {
            "action": action,
            "title": str(arguments.get("title") or arguments.get("task") or "task"),
            "status": str(arguments.get("status") or "pending"),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        return ok_result("TaskBoard", {"recordDigest": digest(record), "pathRef": ".magi/taskboard.jsonl"})
    count = 0
    if path.exists():
        count = sum(1 for _ in path.open(encoding="utf-8"))
    return ok_result("TaskBoard", {"taskCount": count, "pathRef": ".magi/taskboard.jsonl"})


def memory_redact(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    target = str(arguments.get("target") or arguments.get("memoryId") or "memory")
    return ok_result("MemoryRedact", {"targetDigest": digest(target), "redactionRecorded": True})


def notify_user(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    message = str(arguments.get("message") or arguments.get("text") or "")
    return ok_result("NotifyUser", {"messageDigest": digest(message), "channel": context.channel or "local"})


def switch_to_act_mode(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    return ok_result("SwitchToActMode", {"requestedMode": "act", "turnId": context.turn_id or "unknown-turn"})
