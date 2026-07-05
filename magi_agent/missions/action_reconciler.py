"""Hosted ``MissionActionReconciler`` — inbound UI actions -> work_queue.

Path 2 consumer (design section 7.4). The Python analog of the retired
TypeScript ``clawy-core-agent`` ``MissionActionReconciler.ts``: it polls the
already-deployed chat-proxy ``GET /v1/missions/actions?since=<cursor>`` receiver
(``missions.js:654-697``) and applies each human UI action (retry / unblock /
cancel / comment) back into the durable work_queue SQLite store. It pairs with
M7's outbound :class:`~magi_agent.missions.projector.MissionProjector`: the
projector pushes queue state to the hosted cache, the reconciler pulls control
requests back, and the queue transition then projects forward. This is the
"human actions route back as control-requests, never as direct authority edits"
half of the one-directional consistency stance (design section 4.1).

It REUSES M7's HTTP transport seam (:class:`MissionTransport` /
``UrllibMissionTransport`` + ``list_action_events``) — there is exactly one
mission HTTP client. It resolves ``mission_id -> task_id`` through M7's
``mission_projection`` table (``task_id_for_mission``); an action for a mission
this pod did not produce (no mapping) is skipped and logged.

Design authority:
``docs/plans/2026-07-05-magi-missions-workqueue-unification-design.md``
sections 5.2 (action -> queue transition table), 7.4 (reconciler), 4.1
(control-request stance).

Activation is CONFIGURATION-PRESENCE-driven, identical to the projector
(no-default-OFF policy): live iff BOTH ``CORE_AGENT_CHAT_PROXY_URL`` and
``GATEWAY_TOKEN`` are set (hosted pods) AND the hosted kill-switch
``CORE_AGENT_PYTHON_MISSION_RUNTIME`` is truthy. On OSS local the URL/token are
absent so the reconciler is naturally inert. See
:func:`magi_agent.missions.projector.projector_active`.

Fail-open (section 7.4): a poll/HTTP failure logs and retries on the next tick;
a reconciler outage never corrupts the queue. The 5.2 transitions are all
idempotent by construction — M3's CAS guards (``UPDATE ... WHERE status IN (...)``)
make a repeated cancel/retry/unblock on an already-transitioned task a safe
no-op — and a per-poll cursor (last processed ``created_at`` + a bounded set of
processed action-event ids) prevents reprocessing across the inclusive
``created_at >= since`` boundary and across restart. An illegal transition (e.g.
retry on a ``running`` task) is a store CAS no-op (returns ``None``): it is
logged and the cursor still advances, so a permanently-illegal action never
wedges the poll loop.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Protocol

from magi_agent.missions.projector import (
    MissionTransport,
    projector_active,
)
from magi_agent.missions.work_queue.models import WorkTask

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 15.0  # legacy default (MissionActionReconciler.ts:16,70-72)
DEFAULT_LIMIT = 50
# Bounded set of recently processed action-event ids kept alongside the cursor
# timestamp so the inclusive ``created_at >= since`` boundary is deduped without
# unbounded growth (mirrors the legacy ``MAX_PROCESSED_IDS`` = 500).
MAX_PROCESSED_IDS = 500

# 5.2 action-event -> store transition. ``comment`` is ledger-only in v1 (D5:
# NOT injected into agent context). NOTE: the deployed chat-proxy actions
# endpoint only surfaces ``cancel_requested`` / ``retry_requested`` /
# ``unblocked`` (``missions.js:63``); ``comment`` is mapped here for
# completeness (design 5.2) and covered by tests but is not delivered by the
# current receiver.
ACTION_EVENT_TYPES = frozenset(
    {"cancel_requested", "retry_requested", "unblocked", "comment"}
)


# ---------------------------------------------------------------------------
# Store seam (the reconciler's writer subset of SqliteWorkQueueStore)
# ---------------------------------------------------------------------------


class ReconcilerStore(Protocol):
    """The subset of ``SqliteWorkQueueStore`` the reconciler uses: the reverse
    mission-id resolver (M7 ``mission_projection`` table), the M3 CAS-guarded
    control transitions, and the M8 poll-cursor bookkeeping."""

    def task_id_for_mission(self, mission_id: str) -> str | None: ...

    def request_cancel(self, task_id: str, *, actor: str = "user") -> WorkTask | None: ...

    def request_retry(self, task_id: str, *, actor: str = "user") -> WorkTask | None: ...

    def request_unblock(self, task_id: str, *, actor: str = "user") -> WorkTask | None: ...

    def append_comment(self, task_id: str, *, author: str, message: str) -> bool: ...

    def get_mission_action_cursor(self) -> dict | None: ...

    def set_mission_action_cursor(
        self, *, last_created_at: str | None, processed_ids: list[str]
    ) -> None: ...


# ---------------------------------------------------------------------------
# MissionActionReconciler
# ---------------------------------------------------------------------------


class MissionActionReconciler:
    """Poll-loop consumer of hosted UI action events -> local queue transitions.

    Synchronous by design (like the projector's worker): ``poll_once`` runs on a
    gateway watcher daemon thread via ``asyncio.to_thread`` (see
    ``gateway/watchers.py``), so its blocking HTTP + SQLite never touch the event
    loop. ``poll_once`` returns the number of action events consumed this poll
    (advanced past the cursor), for tests and observability.
    """

    def __init__(
        self,
        transport: MissionTransport,
        store: ReconcilerStore,
        *,
        limit: int = DEFAULT_LIMIT,
        max_processed_ids: int = MAX_PROCESSED_IDS,
    ) -> None:
        self._transport = transport
        self._store = store
        self._limit = max(1, int(limit))
        self._max_processed_ids = max(1, int(max_processed_ids))

    def poll_once(self) -> int:
        """Pull one page of action events and apply each to the queue.

        A transport error propagates to the caller (the watcher loop logs and
        retries next tick — fail-open). Per-event, an unmapped mission or an
        illegal (CAS-rejected) transition is logged and the cursor still
        advances so the loop never wedges.
        """
        cursor = self._store.get_mission_action_cursor() or {}
        last_created_at = cursor.get("last_created_at")
        processed_ids: list[str] = list(cursor.get("processed_ids") or [])
        processed_set = set(processed_ids)

        params: dict[str, str] = {"limit": str(self._limit)}
        if last_created_at:
            params["since"] = str(last_created_at)

        events = self._transport.list_action_events(params)
        applied = 0
        for event in sorted(events, key=_sort_key):
            event_id = _event_id(event)
            if event_id is None or event_id in processed_set:
                continue
            # A store/DB exception here propagates (retry next tick, no advance).
            # A rejected transition returns falsy and is handled inside _apply.
            self._apply(event)

            processed_ids.append(event_id)
            processed_set.add(event_id)
            if len(processed_ids) > self._max_processed_ids:
                processed_ids = processed_ids[-self._max_processed_ids :]
                processed_set = set(processed_ids)
            created_at = event.get("created_at")
            last_created_at = _latest(last_created_at, created_at)
            self._store.set_mission_action_cursor(
                last_created_at=last_created_at, processed_ids=processed_ids
            )
            applied += 1
        return applied

    # -- per-event application (5.2) -------------------------------------

    def _apply(self, event: Mapping[str, object]) -> None:
        event_type = event.get("event_type")
        event_id = _event_id(event)
        mission_id = event.get("mission_id")
        if not isinstance(mission_id, str) or not mission_id:
            logger.warning("reconciler: action %s missing mission_id; skipping", event_id)
            return
        if event_type not in ACTION_EVENT_TYPES:
            logger.debug("reconciler: unmapped action event_type %r; skipping", event_type)
            return

        task_id = self._store.task_id_for_mission(mission_id)
        if task_id is None:
            # Action for a mission this pod did not produce (no mapping row).
            logger.info(
                "reconciler: no task mapping for mission %s; skipping action %s",
                mission_id,
                event_type,
            )
            return

        actor = _actor(event)
        if event_type == "cancel_requested":
            result: object = self._store.request_cancel(task_id, actor=actor)
        elif event_type == "retry_requested":
            result = self._store.request_retry(task_id, actor=actor)
        elif event_type == "unblocked":
            result = self._store.request_unblock(task_id, actor=actor)
        else:  # comment (D5 ledger-only)
            author, message = _comment_fields(event, actor)
            result = self._store.append_comment(task_id, author=author, message=message)

        if not result:
            # Illegal/no-op transition (CAS guard rejected it, e.g. retry on a
            # running task): log and advance the cursor — never wedge.
            logger.info(
                "reconciler: action %s on task %s was a no-op "
                "(illegal transition or missing task); cursor advances",
                event_type,
                task_id,
            )


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------


def _event_id(event: Mapping[str, object]) -> str | None:
    raw = event.get("id")
    return str(raw) if isinstance(raw, (str, int)) and str(raw) else None


def _sort_key(event: Mapping[str, object]) -> tuple[str, str]:
    """Match the receiver's ``order=created_at.asc,id.asc`` (missions.js:689)."""
    created = event.get("created_at")
    eid = event.get("id")
    return (str(created) if created is not None else "", str(eid) if eid is not None else "")


def _latest(current: str | None, candidate: object) -> str | None:
    if not isinstance(candidate, str) or not candidate:
        return current
    if not current:
        return candidate
    return candidate if candidate > current else current


def _actor(event: Mapping[str, object]) -> str:
    actor = event.get("actor_type")
    return actor if isinstance(actor, str) and actor else "user"


def _comment_fields(event: Mapping[str, object], actor: str) -> tuple[str, str]:
    payload = event.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    author = payload.get("author")
    if not isinstance(author, str) or not author:
        author = actor
    message = event.get("message")
    if not isinstance(message, str) or not message:
        pm = payload.get("message")
        message = pm if isinstance(pm, str) else ""
    return author, message


# ---------------------------------------------------------------------------
# Activation + construction (config-presence, shared with the projector)
# ---------------------------------------------------------------------------


def reconciler_active(env: Mapping[str, str]) -> bool:
    """Config-presence + kill-switch activation gate (section 7.4).

    Identical rule to the projector (:func:`projector_active`): both
    ``CORE_AGENT_CHAT_PROXY_URL`` and ``GATEWAY_TOKEN`` set AND the
    ``CORE_AGENT_PYTHON_MISSION_RUNTIME`` kill-switch truthy. Reused verbatim so
    the projector and reconciler activate together as one hosted seam.
    """
    return projector_active(env)


def build_reconciler_from_env(
    env: Mapping[str, str] | None = None,
) -> "MissionActionReconciler | None":
    """Build the reconciler from *env*, or None when inert (config absent/killed).

    Returns None on OSS local (URL/token absent) and while the kill-switch is
    OFF, mirroring :func:`magi_agent.missions.projector.build_projector_from_env`
    so the seam is naturally inert without a new capability flag.
    """
    import os  # noqa: PLC0415

    resolved = os.environ if env is None else env
    if not reconciler_active(resolved):
        return None

    from magi_agent.missions.projector import (  # noqa: PLC0415
        CHAT_PROXY_URL_ENV,
        GATEWAY_TOKEN_ENV,
        UrllibMissionTransport,
    )
    from magi_agent.missions.work_queue.store import (  # noqa: PLC0415
        SqliteWorkQueueStore,
        work_queue_db_path_from_env,
    )

    base_url = (resolved.get(CHAT_PROXY_URL_ENV) or "").strip()
    token = (resolved.get(GATEWAY_TOKEN_ENV) or "").strip()
    transport = UrllibMissionTransport(base_url, token)
    store = SqliteWorkQueueStore(work_queue_db_path_from_env())
    return MissionActionReconciler(transport, store)


__all__ = [
    "ACTION_EVENT_TYPES",
    "DEFAULT_LIMIT",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "MAX_PROCESSED_IDS",
    "MissionActionReconciler",
    "ReconcilerStore",
    "build_reconciler_from_env",
    "reconciler_active",
]
