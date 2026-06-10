from __future__ import annotations

import json
import os
from collections.abc import Mapping

from magi_agent.config.env import _is_true, native_receipts_honest
from magi_agent.plugins.native._common import (
    blocked_result,
    digest,
    ok_result,
    safe_child_path,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

# Backing-system attachment flags. These are owned by other clusters and stay
# unset until those clusters wire a real backing, so the honest branch fires.
# When set, the handler routes past the honest block to the (cluster-owned)
# live delegation seam.
#   - NotifyUser channel adapter -> B17 (channels provider port)
#   - SwitchToActMode permission/plan-mode path -> cluster 14 (control-plane)
#   - MemoryRedact authority hook -> cluster 01 (memory)
NOTIFY_CHANNEL_ATTACHED_ENV = "MAGI_NOTIFY_CHANNEL_ATTACHED"
MODE_SWITCH_ATTACHED_ENV = "MAGI_MODE_SWITCH_ATTACHED"
MEMORY_REDACT_ATTACHED_ENV = "MAGI_MEMORY_REDACT_ATTACHED"


def _env(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def _notify_channel_attached(env: Mapping[str, str] | None = None) -> bool:
    return _is_true(_env(env).get(NOTIFY_CHANNEL_ATTACHED_ENV))


def _mode_switch_attached(env: Mapping[str, str] | None = None) -> bool:
    return _is_true(_env(env).get(MODE_SWITCH_ATTACHED_ENV))


def _memory_redact_attached(env: Mapping[str, str] | None = None) -> bool:
    return _is_true(_env(env).get(MEMORY_REDACT_ATTACHED_ENV))


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
    if native_receipts_honest() and not _memory_redact_attached():
        # The redaction authority hook (cluster 01 memory) is not attached, so
        # no redaction is executed. Do not pretend it was recorded.
        return blocked_result("MemoryRedact", "memory_redaction_not_attached")
    return ok_result("MemoryRedact", {"targetDigest": digest(target), "redactionRecorded": True})


def notify_user(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    message = str(arguments.get("message") or arguments.get("text") or "")
    if native_receipts_honest() and not _notify_channel_attached():
        # No channel adapter is wired (B17), so the message is delivered nowhere.
        # Do not return a digest the model can mis-report as "notified".
        return blocked_result("NotifyUser", "notify_user_not_configured")
    return ok_result("NotifyUser", {"messageDigest": digest(message), "channel": context.channel or "local"})


def switch_to_act_mode(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    if native_receipts_honest() and not _mode_switch_attached():
        # The permission/plan-mode switch path (cluster 14) is not connected, so
        # the turn/permission state is unchanged. Report that honestly instead of
        # claiming a mode switch happened.
        return blocked_result("SwitchToActMode", "mode_switch_unsupported_local")
    return ok_result("SwitchToActMode", {"requestedMode": "act", "turnId": context.turn_id or "unknown-turn"})
