from __future__ import annotations

import logging
from typing import Any

from magi_agent.observability.models import ActivityEvent

logger = logging.getLogger(__name__)

# hook point (str value of HookPoint enum) -> (kind, default status)
_POINT_MAP: dict[str, tuple[str, str | None]] = {
    "beforeTurnStart": ("turn_start", "running"),
    "afterTurnEnd": ("turn_end", "ok"),
    "beforeToolUse": ("tool_start", "running"),
    "afterToolUse": ("tool_end", "ok"),
    "onError": ("error", "error"),
    "onAbort": ("aborted", "error"),
    "onTaskCheckpoint": ("checkpoint", None),
    "onRuleViolation": ("rule_violation", "blocked"),
    "onArtifactCreated": ("artifact_created", "ok"),
    "beforeCompaction": ("compaction_start", "running"),
    "afterCompaction": ("compaction_end", "ok"),
}


def _get(ctx: Any, *names: str) -> Any:
    for name in names:
        value = getattr(ctx, name, None)
        if value is not None:
            return value
    return None


def project(point: str, ctx: Any) -> ActivityEvent | None:
    """Map a hook lifecycle point + context into a sanitized ActivityEvent.

    Returns None for points we do not surface. Never raises: any extraction
    failure yields None so the tap stays fail-open.

    NOTE (plan-2): the live HookContext exposes session_id and turn_id but NOT
    tool_name/error/result. Until the hook tap threads a per-call tool/error
    payload (or HookContext is extended), tool_name and error will be None for
    real hook invocations; events still record kind/status/session_id/run_id.
    """
    mapping = _POINT_MAP.get(point)
    if mapping is None:
        return None
    kind, status = mapping
    try:
        error = _get(ctx, "error", "error_message")
        summary = _get(ctx, "summary")
        if error is not None and summary is None:
            summary = str(error)
        return ActivityEvent(
            kind=kind,
            status=status,
            session_id=_get(ctx, "session_id"),
            run_id=_get(ctx, "run_id", "turn_id"),
            parent_run_id=_get(ctx, "parent_run_id"),
            tool_name=_get(ctx, "tool_name"),
            summary=summary,
        )
    except Exception:
        logger.debug("projector failed for point=%s", point, exc_info=True)
        return None


def project_public_event(
    payload: dict, *, session_id: str | None, turn_id: str | None
) -> ActivityEvent | None:
    """Map a sanitized public engine event dict into an ActivityEvent. Fail-open."""
    try:
        if not isinstance(payload, dict):
            return None
        kind = payload.get("type")
        if not kind:
            return None
        status = payload.get("status")
        tool_name = payload.get("toolName") or payload.get("name")
        error = payload.get("error") or payload.get("message")
        summary = error if isinstance(error, str) else None
        safe_payload: dict = {}
        for k, v in payload.items():
            if k == "type":
                continue
            if isinstance(v, str):
                safe_payload[k] = v[:512]
            elif isinstance(v, (int, float, bool)) or v is None:
                safe_payload[k] = v
            # nested/large structures are intentionally dropped from the stored payload
        return ActivityEvent(
            kind=str(kind),
            status=str(status) if status is not None else None,
            tool_name=str(tool_name) if tool_name is not None else None,
            session_id=session_id,
            run_id=turn_id,
            summary=summary,
            payload=safe_payload,
        )
    except Exception:
        logger.debug("project_public_event failed", exc_info=True)
        return None
