"""WS1 PR1c - static side-effecting-tool classifier (design section 0.5).

A durable checkpoint's ``resumable`` flag governs whether the startup sweep may
auto-replay a crashed turn. Replaying a turn whose last tool had an EXTERNAL
side effect (an outbound channel send, a background-work enqueue, an MCP call)
would double-fire that effect, so such checkpoints must be marked
``resumable=False`` and never auto-resumed.

This module is the static, deterministic, LLM-free classifier the headless tap
calls per persisted ``tool_end`` (section 0.4a). It is intentionally COARSE:
hard per-send idempotency is WS7's job (outbox). The rule here is FAIL-CLOSED -
an UNKNOWN tool name is treated as side-effecting, so the failure mode is
"resume too rarely" (safe), never "auto-resume a half-sent message" (unsafe).

Pure: no flag-system or engine dependency. No imports beyond stdlib typing.
"""
from __future__ import annotations

from collections.abc import Sequence

__all__ = [
    "SIDE_EFFECTING_TOOL_NAMES",
    "SIDE_EFFECTING_NAME_FRAGMENTS",
    "is_side_effecting_tool",
    "is_turn_resumable",
]


# Tools whose replay could double-fire an external effect. Conservative by
# design; add to this list rather than relaxing the fail-closed default.
SIDE_EFFECTING_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # Outbound channel / delivery sends.
        "send_telegram_message",
        "send_message",
        "send_channel_message",
        "deliver_message",
        "post_message",
        "reply_to_message",
        # Background-work enqueue (re-running re-enqueues the task).
        "RunInBackground",
        "run_in_background",
        "enqueue_work",
        "enqueue_task",
        "schedule_task",
        # MCP / external tool invocation.
        "call_tool",
        "mcp_call_tool",
        # Spawning child agents (a child may itself emit side effects).
        "SpawnAgent",
        "spawn_agent",
    }
)

# Substring fragments that mark a tool name as an outbound/delivery effect even
# when the exact name is not enumerated above (the registered-pattern clause of
# section 0.5). Matched case-insensitively against the tool name.
SIDE_EFFECTING_NAME_FRAGMENTS: tuple[str, ...] = (
    "send_",
    "deliver",
    "publish",
    "notify",
    "broadcast",
    "enqueue",
    "dispatch",
)

# Tools KNOWN to be pure / read-only / locally-scoped, so a replay is safe. A
# name that is neither here nor side-effecting is UNKNOWN and fails closed.
_KNOWN_PURE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "read_file",
        "read",
        "grep",
        "glob",
        "ls",
        "list_files",
        "search",
        "view",
        "cat",
        "stat",
        "select_recipe",
    }
)


def is_side_effecting_tool(tool_name: str | None) -> bool:
    """Whether replaying ``tool_name`` could double-fire an external effect.

    Fail-closed: a name that is neither a KNOWN pure tool nor an explicit
    side-effecting tool/fragment is treated as side-effecting (returns True).
    ``None`` (no tool ran) is NOT side-effecting.
    """
    if tool_name is None:
        return False
    name = tool_name.strip()
    if not name:
        # An empty/blank tool name is unclassifiable: fail closed.
        return True
    if name in SIDE_EFFECTING_TOOL_NAMES:
        return True
    lowered = name.lower()
    if any(fragment in lowered for fragment in SIDE_EFFECTING_NAME_FRAGMENTS):
        return True
    if name in _KNOWN_PURE_TOOL_NAMES:
        return False
    # Unknown tool: fail closed.
    return True


def is_turn_resumable(
    *,
    pending_tool_ids: Sequence[str],
    last_completed_tool_name: str | None,
) -> bool:
    """Whether a checkpoint at this point may be auto-resumed.

    ``True`` iff there is NO pending (started-but-unfinished) tool AND the most
    recent completed tool is not side-effecting (section 0.5). A side-effecting
    tool mid-flight (a ``tool_start`` with no matching ``tool_end``) is therefore
    ``resumable=False``.
    """
    if len(pending_tool_ids) > 0:
        return False
    return not is_side_effecting_tool(last_completed_tool_name)
