"""Pure mapping kernel: work_queue substrate -> hosted "mission" shape.

This module is the single place where a :class:`WorkTask` (plus its task
events and task runs) is projected into the ``MissionSummary`` /
``agent_missions`` shape the chat "Missions" panel already renders
(``apps/web/src/lib/missions/types.ts``). It is shared by the local read
routes (PR-M2), the local control routes (PR-M3) and the hosted
``MissionProjector`` (PR-M7) so the mapping exists in exactly one place.

Design authority: ``docs/plans/2026-07-05-magi-missions-workqueue-unification-design.md``
section 5 (shape and status mapping).

Purity contract (same import-boundary discipline as ``board_api.py``):
no FastAPI, no network, no ADK, no DB. Pure functions over the pydantic
``WorkTask`` model and plain dict rows returned by
``SqliteWorkQueueStore.list_task_events`` / ``list_task_runs``.

Deliberately intentional: this module carries NO pydantic ``BaseModel``.
It emits typed ``dict`` payloads only, so it does not trip the
``tests/meta/golden_force_false`` authority-flag golden gate and it does not
add a default-off boundary. It IS the "public mission payload schema" the
``missions/events.py`` registry defers (that registry stays untouched here;
whether to flip it is design decision D3 / PR-M9).

Pinned store event-kind vocabulary (verified this session; the store is the
ONLY writer of ``work_queue_task_events`` rows, confirmed by grepping the
whole package for ``_append_event`` / ``INSERT INTO work_queue_task_events``):

    kind             magi_agent/missions/work_queue/store.py
    ---------------  ---------------------------------------
    promoted         line 669  (recompute_ready: todo -> ready)
    claim_rejected   line 692  (claim: parents not done)
    claimed          line 715  (claim: CAS win)
    claim_extended   line 762  (release_stale_claims: live worker)
    reclaimed        lines 775, 813 (release_stale_claims / reclaim_running_for_dead_pids)
    blocked          line 885  (record_failure: retries exhausted)
    failed           line 891  (record_failure: retry remaining)
    completed        line 918  (complete)

Control-action kinds added by PR-M3 (``store.request_cancel`` /
``request_retry`` / ``request_unblock`` / ``append_comment``): ``cancel_requested``,
``retry_requested``, ``unblocked``, ``comment``. These were already present as
forward-compat entries in ``STORE_EVENT_KIND_TO_MISSION_EVENT`` below; the store
is now a real writer, so they are also members of ``STORE_EVENT_KINDS``.

Pinned store run-status vocabulary (``work_queue_task_runs.status``, same
file): ``running`` (claim, :720/709), ``released`` (reclaim, :765/803),
``failed`` (record_failure, :872), ``done`` (complete, :907).
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Literal, get_args

from magi_agent.missions.work_queue.models import TaskStatus, WorkTask

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Target vocabularies (mirror apps/web/src/lib/missions/types.ts)
# ---------------------------------------------------------------------------

MissionStatus = Literal[
    "queued",
    "running",
    "blocked",
    "waiting",
    "completed",
    "failed",
    "cancelled",
    "paused",
]

MissionKind = Literal["manual", "goal"]

MissionEventType = Literal[
    "created",
    "claimed",
    "heartbeat",
    "evidence",
    "comment",
    "blocked",
    "unblocked",
    "retry_requested",
    "cancel_requested",
    "cancelled",
    "completed",
    "failed",
    "delivered",
    "paused",
    "resumed",
]

MissionRunStatus = Literal["running", "completed", "failed", "cancelled", "timed_out"]

# ---------------------------------------------------------------------------
# 5.1 TaskStatus -> MissionStatus
# ---------------------------------------------------------------------------

TASK_TO_MISSION_STATUS: dict[str, MissionStatus] = {
    "triage": "queued",
    "todo": "queued",
    "ready": "queued",
    "running": "running",
    "blocked": "blocked",
    "completed": "completed",
    "failed": "failed",
    "archived": "cancelled",
}


def map_task_status(task_status: str) -> MissionStatus:
    """Map a work_queue ``TaskStatus`` to a hosted ``MissionStatus`` (5.1).

    Raises ``KeyError`` on an unmapped status so that adding a future
    ``TaskStatus`` literal without extending this table is caught by the
    exhaustiveness test rather than silently producing a wrong shape.
    ``waiting`` and ``paused`` are never produced by this projection.
    """
    return TASK_TO_MISSION_STATUS[task_status]


# ---------------------------------------------------------------------------
# 5.5 store event-kind vocabulary  (pinned to store.py write sites)
# ---------------------------------------------------------------------------

# Every ``kind`` string the store actually writes today (see module docstring
# for file:line). The exhaustiveness test asserts this set matches the store.
STORE_EVENT_KINDS: frozenset[str] = frozenset(
    {
        # driver / lifecycle kinds
        "promoted",
        "claim_rejected",
        "claimed",
        "claim_extended",
        "reclaimed",
        "blocked",
        "failed",
        "completed",
        # control-action kinds written by the local control routes
        # (PR-M3 store.request_cancel / request_retry / request_unblock /
        # append_comment). Already present in the mapping below as
        # forward-compat entries; now the store is a real writer.
        "cancel_requested",
        "retry_requested",
        "unblocked",
        "comment",
    }
)

# Store kinds that carry no mission-event meaning (pure queue-internal
# bookkeeping). Whitelisted-ignorable: mapped to None WITHOUT a warning.
IGNORABLE_STORE_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "promoted",  # todo -> ready gate promotion
        "claim_rejected",  # parents-not-done CAS bounce
        "reclaimed",  # crashed-worker lease reclaim (task returns to ready)
    }
)

# store kind -> MissionEventType. Includes the control-action kinds the
# local control routes (PR-M3) and the reconciler (PR-M8) will append, so this
# kernel already understands them; the store does not write them yet.
STORE_EVENT_KIND_TO_MISSION_EVENT: dict[str, MissionEventType] = {
    # written by the store today
    "claimed": "claimed",
    "claim_extended": "heartbeat",
    "blocked": "blocked",
    "failed": "failed",
    "completed": "completed",
    # control-action kinds (forward-compat; written by M3 / M8)
    "created": "created",
    "unblocked": "unblocked",
    "retry_requested": "retry_requested",
    "cancel_requested": "cancel_requested",
    "cancelled": "cancelled",
    "comment": "comment",
}

# ---------------------------------------------------------------------------
# 5.5 run-status vocabulary
# ---------------------------------------------------------------------------

STORE_RUN_STATUS_TO_MISSION_RUN_STATUS: dict[str, MissionRunStatus] = {
    "running": "running",
    "done": "completed",
    "failed": "failed",
    "released": "cancelled",  # reclaimed after crash: closest terminal-not-completed
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _epoch_to_iso(epoch_seconds: int | None) -> str | None:
    """Convert an epoch-second timestamp to an ISO-8601 UTC string, or None."""
    if epoch_seconds is None:
        return None
    return _dt.datetime.fromtimestamp(int(epoch_seconds), tz=_dt.timezone.utc).isoformat()


def _latest_epoch(*values: int | None) -> int | None:
    """Return the largest non-None epoch value, or None when all are None."""
    present = [int(v) for v in values if v is not None]
    return max(present) if present else None


# ---------------------------------------------------------------------------
# 5.3 WorkTask -> MissionSummary / agent_missions
# ---------------------------------------------------------------------------


def project_task_to_mission_summary(
    task: WorkTask,
    *,
    run_count: int = 0,
    bot_id: str = "local",
    created_by: Literal["user", "agent", "cron", "system"] = "agent",
) -> dict:
    """Project a :class:`WorkTask` into the ``MissionSummary`` payload (5.3).

    The returned dict is a superset of ``MissionSummary`` (it also carries the
    ``idempotencyKey`` the hosted projector uses on create, 5.4). The raw
    work_queue status is always preserved in ``metadata.work_queue_status``.

    ``bot_id`` defaults to ``"local"`` (the OSS-local panel convention); the
    hosted projector never sends a bot id (chat-proxy resolves it from the
    gateway token) so it is a caller-supplied read-shape field only.
    ``run_count`` (from ``len(list_task_runs(...))``) becomes ``used_turns``.
    """
    has_session = bool(task.session_id)
    created_iso = _epoch_to_iso(task.created_at)
    completed_iso = _epoch_to_iso(task.completed_at)
    started_iso = _epoch_to_iso(task.started_at)
    updated_iso = _epoch_to_iso(
        _latest_epoch(task.created_at, task.started_at, task.completed_at)
    )

    return {
        "id": task.id,
        "bot_id": bot_id,
        "channel_type": "app" if has_session else "internal",
        "channel_id": task.session_id if has_session else "work-queue",
        "kind": "goal" if task.goal_mode else "manual",
        "title": task.title,
        "summary": task.body,
        "status": map_task_status(task.status),
        "priority": task.priority,
        "created_by": created_by,
        "assignee_profile": task.assignee,
        "parent_mission_id": None,
        "root_mission_id": None,
        "used_turns": run_count,
        "budget_turns": task.goal_max_turns,
        "last_event_at": None,
        "completed_at": completed_iso,
        "created_at": created_iso,
        "updated_at": updated_iso,
        # 5.4 identity: idempotent create key (chat-proxy dedupes on this)
        "idempotencyKey": f"wq:{task.id}",
        "metadata": {
            "work_task_id": task.id,
            "work_queue_status": task.status,
            "task_idempotency_key": task.idempotency_key,
            "started_at": started_iso,
            "consecutive_failures": task.consecutive_failures,
            "max_retries": task.max_retries,
            "last_failure_error": task.last_failure_error,
            "result": task.result,
        },
    }


# ---------------------------------------------------------------------------
# 5.5 task event -> mission event
# ---------------------------------------------------------------------------


def map_task_event(event: dict) -> dict | None:
    """Project one ``list_task_events`` row into a mission-event payload (5.5).

    Returns ``None`` for queue-internal kinds that have no mission meaning
    (``IGNORABLE_STORE_EVENT_KINDS``, silently) and for genuinely unknown
    kinds (logged at WARNING so a new unmapped store kind is countable, never
    a crash). The raw kind is preserved in ``work_queue_kind``.
    """
    kind = event.get("kind")
    mission_type = STORE_EVENT_KIND_TO_MISSION_EVENT.get(kind) if kind is not None else None
    if mission_type is None:
        if kind not in IGNORABLE_STORE_EVENT_KINDS:
            logger.warning("map_task_event: unmapped work_queue event kind %r", kind)
        return None
    return {
        "event_type": mission_type,
        "message": None,
        "payload": event.get("payload") or {},
        "created_at": _epoch_to_iso(event.get("created_at")),
        "work_queue_event_id": event.get("id"),
        "work_queue_kind": kind,
    }


# ---------------------------------------------------------------------------
# 5.5 task run -> mission run
# ---------------------------------------------------------------------------


def map_task_run(
    run: dict,
    *,
    trigger_type: Literal[
        "user", "goal_continue", "cron", "script_cron", "retry", "handoff", "resume"
    ] = "user",
) -> dict:
    """Project one ``list_task_runs`` row into a mission-run payload (5.5).

    The run row does not carry positional context (first run vs retry vs
    recovery resume), so ``trigger_type`` is a caller-supplied hint defaulting
    to ``"user"`` (see 5.5: first run ``user``/``goal_continue``, post-retry
    ``retry``, recovery ``resume``). An unknown store run status maps to
    ``failed`` (defensive: an unclosed/unknown run is never reported as a
    success) and is logged.
    """
    raw_status = run.get("status")
    mission_status = STORE_RUN_STATUS_TO_MISSION_RUN_STATUS.get(raw_status) if raw_status else None
    if mission_status is None:
        logger.warning("map_task_run: unmapped work_queue run status %r", raw_status)
        mission_status = "failed"
    return {
        "trigger_type": trigger_type,
        "status": mission_status,
        "session_key": None,
        "turn_id": None,
        "started_at": _epoch_to_iso(run.get("started_at")),
        "finished_at": _epoch_to_iso(run.get("ended_at")),
        "error_message": run.get("error"),
        "result_preview": run.get("summary"),
        "metadata": {
            "work_queue_run_id": run.get("id"),
            "work_queue_run_status": raw_status,
            "outcome": run.get("outcome"),
            "worker_pid": run.get("worker_pid"),
        },
    }


__all__ = [
    "IGNORABLE_STORE_EVENT_KINDS",
    "STORE_EVENT_KINDS",
    "STORE_EVENT_KIND_TO_MISSION_EVENT",
    "STORE_RUN_STATUS_TO_MISSION_RUN_STATUS",
    "TASK_TO_MISSION_STATUS",
    "MissionEventType",
    "MissionKind",
    "MissionRunStatus",
    "MissionStatus",
    "map_task_event",
    "map_task_run",
    "map_task_status",
    "project_task_to_mission_summary",
]


def _assert_task_status_exhaustive() -> None:
    """Import-time guard: every ``TaskStatus`` literal must be mapped.

    Cheap defensive mirror of the exhaustiveness test so a future enum value
    added without a mapping fails loudly at import rather than at first
    projection of that status.
    """
    missing = [s for s in get_args(TaskStatus) if s not in TASK_TO_MISSION_STATUS]
    if missing:
        raise RuntimeError(
            f"TASK_TO_MISSION_STATUS missing mapping(s) for TaskStatus: {missing!r}"
        )


_assert_task_status_exhaustive()
