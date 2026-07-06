"""Hosted ``MissionProjector`` — outbound work_queue -> chat-proxy projection.

Path 2 producer (design section 7.1). The Python analog of the retired
TypeScript ``clawy-core-agent`` ``MissionClient.ts``: it pushes work_queue
lifecycle transitions (created / claimed / terminal / restart-recovery) to the
already-deployed chat-proxy ``/v1/missions*`` receiver so the hosted "Missions"
tab has an ``agent_missions`` projection cache to read. The chat-proxy receiver,
the Supabase tables, the Next.js routes and both UI panels already exist; only
this Python producer side is net-new.

Design authority:
``docs/plans/2026-07-05-magi-missions-workqueue-unification-design.md``
sections 5.4 (identity mapping), 7.1 (projector), 7.2 (wiring seams),
7.3 (event volume), 7.5 (authority seam).

Consistency stance (section 4):
* one-directional (queue -> hosted), never reads mission state back to mutate
  the queue;
* the ``agent_missions`` rows are a projection CACHE, never a second source of
  truth; a projector outage degrades only the cache, never the durable queue.

Activation is CONFIGURATION-PRESENCE-driven, not a new capability flag
(no-default-OFF policy): the projector is live iff BOTH
``CORE_AGENT_CHAT_PROXY_URL`` and ``GATEWAY_TOKEN`` are set (hosted pods) AND the
hosted kill-switch ``CORE_AGENT_PYTHON_MISSION_RUNTIME`` is truthy. On OSS local
the URL/token are absent so the projector is naturally inert. The kill-switch
default is NOT flipped here (PR-C1 flips ``'0'`` -> ``'1'`` in the deployment
template); until then the projector is inert everywhere.

    NOTE for PR-C1 (landmine): ``CORE_AGENT_PYTHON_MISSION_RUNTIME`` is currently
    a member of ``config/env.py`` ``false_only_flags`` (env.py ~:2020), so the
    hosted process RAISES ``RuntimeEnvError`` at boot if it is truthy. C1 must
    remove it from that list at the same time it flips the template value, or
    the pod will not boot. This PR does not touch either.

Fail-open, non-blocking (section 7.1): enqueue never blocks the caller/driver;
a bounded in-process ``queue.Queue`` is drained by a daemon worker thread; HTTP
failures retry ``max_retries`` times then log-and-drop; NO exception from
projection propagates into the driver/tool path.

Idempotent projection (section 5.4): create always uses
``idempotencyKey = "wq:<task_id>"``; the returned ``mission_id`` is persisted in
the ``mission_projection`` table (via the store helpers). If that mapping row is
lost, re-creating with the same idempotency key recovers the mission id without
duplicating a hosted row (chat-proxy dedupes on the key, ``missions.js:400-417``).
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from urllib import error as _urlerror
from urllib import request as _urlrequest

from magi_agent.missions.projection import (
    map_task_status,
    project_task_to_mission_summary,
)
from magi_agent.missions.work_queue.models import WorkTask

logger = logging.getLogger(__name__)

CHAT_PROXY_URL_ENV = "CORE_AGENT_CHAT_PROXY_URL"
GATEWAY_TOKEN_ENV = "GATEWAY_TOKEN"
KILL_SWITCH_FLAG = "CORE_AGENT_PYTHON_MISSION_RUNTIME"

DEFAULT_MAX_RETRIES = 3
DEFAULT_QUEUE_MAXSIZE = 2000
# 7.3: heartbeats are recorded per-tick in the store but projected at most once
# per this window so the hosted panel shows liveness without row noise.
DEFAULT_HEARTBEAT_WINDOW_SECONDS = 600  # 10 minutes


# ---------------------------------------------------------------------------
# Transport seam (mirrors MissionClient.ts 1:1)
# ---------------------------------------------------------------------------


class MissionTransport(Protocol):
    """The chat-proxy ``/v1/missions*`` endpoint set, mirrored 1:1 from
    ``MissionClient.ts`` (createMission / createRun / updateRun / appendEvent /
    createArtifact / listActionEvents / restart-recovery).

    Each method returns the parsed JSON response (a dict). Implementations raise
    on transport / non-2xx errors; the projector catches and retries.
    """

    def create_mission(self, body: dict) -> dict: ...

    def create_run(self, mission_id: str, body: dict) -> dict: ...

    def update_run(self, mission_id: str, run_id: str, body: dict) -> dict: ...

    def append_event(self, mission_id: str, body: dict) -> dict: ...

    def create_artifact(self, mission_id: str, body: dict) -> dict: ...

    def list_action_events(self, params: Mapping[str, str] | None = None) -> list[dict]: ...

    def restart_recovery(self, body: dict) -> dict: ...


class UrllibMissionTransport:
    """Stdlib ``urllib.request`` implementation of :class:`MissionTransport`.

    Synchronous by design: it runs on the projector's daemon worker THREAD
    (M5 established the driver runs off the event loop in a background thread),
    so a blocking request here never blocks an event loop. Bearer-authed with
    the bot gateway token exactly like ``MissionClient.ts:12,124`` and the
    chat-proxy bot-token auth (``missions.js:700``).
    """

    def __init__(self, base_url: str, gateway_token: str, *, timeout_s: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = gateway_token
        self._timeout_s = timeout_s

    # -- endpoint methods (1:1 with MissionClient.ts) --------------------

    def create_mission(self, body: dict) -> dict:
        return self._first(self._request("POST", "/v1/missions", body))

    def create_run(self, mission_id: str, body: dict) -> dict:
        return self._first(
            self._request("POST", f"/v1/missions/{_enc(mission_id)}/runs", body)
        )

    def update_run(self, mission_id: str, run_id: str, body: dict) -> dict:
        return self._first(
            self._request(
                "PATCH", f"/v1/missions/{_enc(mission_id)}/runs/{_enc(run_id)}", body
            )
        )

    def append_event(self, mission_id: str, body: dict) -> dict:
        return self._first(
            self._request("POST", f"/v1/missions/{_enc(mission_id)}/events", body)
        )

    def create_artifact(self, mission_id: str, body: dict) -> dict:
        return self._first(
            self._request("POST", f"/v1/missions/{_enc(mission_id)}/artifacts", body)
        )

    def list_action_events(self, params: Mapping[str, str] | None = None) -> list[dict]:
        suffix = ""
        if params:
            from urllib.parse import urlencode  # noqa: PLC0415

            query = urlencode({k: v for k, v in params.items() if v is not None})
            if query:
                suffix = f"?{query}"
        parsed = self._request("GET", f"/v1/missions/actions{suffix}", None)
        if isinstance(parsed, list):
            return parsed
        events = parsed.get("events") if isinstance(parsed, dict) else None
        return events if isinstance(events, list) else []

    def restart_recovery(self, body: dict) -> dict:
        return self._first(self._request("POST", "/v1/missions/restart-recovery", body))

    # -- low-level HTTP --------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self._token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = _urlrequest.Request(
            f"{self._base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with _urlrequest.urlopen(req, timeout=self._timeout_s) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except _urlerror.HTTPError as exc:  # non-2xx
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:200]
            except Exception:  # noqa: BLE001
                pass
            raise MissionTransportError(
                f"mission request failed: HTTP {exc.code} {detail}"
            ) from exc

    @staticmethod
    def _first(parsed: Any) -> dict:
        if isinstance(parsed, list):
            return parsed[0] if parsed else {}
        return parsed if isinstance(parsed, dict) else {}


class MissionTransportError(RuntimeError):
    """Raised by a transport on a non-2xx / malformed response."""


def _enc(value: str) -> str:
    from urllib.parse import quote  # noqa: PLC0415

    return quote(value, safe="")


# ---------------------------------------------------------------------------
# Mapping-store seam (durable identity mapping, section 5.4)
# ---------------------------------------------------------------------------


class MissionMappingStore(Protocol):
    """The subset of ``SqliteWorkQueueStore`` the projector reads/writes: the
    task read side plus the ``mission_projection`` identity mapping helpers."""

    def get(self, task_id: str) -> WorkTask | None: ...

    def list_task_runs(self, task_id: str, *, limit: int = 100) -> list[dict]: ...

    def get_mission_projection(self, task_id: str) -> dict | None: ...

    def upsert_mission_projection(
        self, task_id: str, *, mission_id: str | None, last_projected_status: str | None
    ) -> None: ...


# ---------------------------------------------------------------------------
# Queue items
# ---------------------------------------------------------------------------

_OP_CREATED = "created"
_OP_CHECKPOINT = "checkpoint"
_OP_HEARTBEAT = "heartbeat"
_OP_RESTART_RECOVERY = "restart_recovery"


@dataclass(frozen=True)
class _ProjectionItem:
    op: str
    task: WorkTask | None = None
    task_id: str | None = None
    checkpoint_kind: str | None = None
    summary_text: str | None = None
    recovery_started_at: str | None = None
    recovery_reason: str | None = None
    extra: dict = field(default_factory=dict)


# checkpoint_kind (driver.py) -> the projection this worker performs.
# "claimed"        -> create a running run + claimed event
# "completed"      -> close the run completed + completed event (receiver flips
#                     the mission-cache status to completed)
# "short_circuited"-> treated as completed (dedupe short-circuit)
# "failed"         -> close the run failed + failed event
_TERMINAL_CHECKPOINTS = frozenset({"completed", "short_circuited", "failed"})


# ---------------------------------------------------------------------------
# MissionProjector
# ---------------------------------------------------------------------------


class MissionProjector:
    """Bounded-queue, daemon-drained outbound mission projector.

    Thread-safety: enqueue is via ``queue.Queue.put_nowait`` (thread-safe, never
    blocks — a full queue drops with a log). The worker thread owns all HTTP and
    all mapping-store writes. The heartbeat throttle map is guarded by a lock.
    """

    def __init__(
        self,
        transport: MissionTransport,
        store: MissionMappingStore,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        heartbeat_window_seconds: float = DEFAULT_HEARTBEAT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        retry_sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._transport = transport
        self._store = store
        self._max_retries = max(1, int(max_retries))
        self._queue: queue.Queue[_ProjectionItem] = queue.Queue(maxsize=queue_maxsize)
        self._heartbeat_window = float(heartbeat_window_seconds)
        self._clock = clock
        self._retry_sleep = retry_sleep if retry_sleep is not None else _no_sleep
        self._last_heartbeat_at: dict[str, float] = {}
        # In-process task_id -> hosted mission run id (for update-on-terminal).
        # Lost on restart; the terminal path falls back to creating a terminal
        # run row when the id is unknown, so no correctness depends on it.
        self._task_run_ids: dict[str, str] = {}
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._started = False

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._worker = threading.Thread(
            target=self._run_worker, name="mission-projector", daemon=True
        )
        self._worker.start()

    def flush(self, timeout: float | None = None) -> bool:
        """Block until the queue is drained (test/shutdown aid).

        Returns True if fully drained. Uses ``queue.join`` which returns once
        every enqueued item has had ``task_done`` called (the worker always
        calls it, even on drop).
        """
        if timeout is None:
            self._queue.join()
            return True
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks:  # type: ignore[attr-defined]
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        return True

    def stop(self, timeout: float = 2.0) -> None:
        if not self._started:
            return
        self._queue.put(_ProjectionItem(op="__stop__"))
        if self._worker is not None:
            self._worker.join(timeout=timeout)
        self._started = False

    # -- enqueue API (non-blocking, thread-safe) -------------------------

    def on_task_created(self, task: WorkTask) -> None:
        self._enqueue(_ProjectionItem(op=_OP_CREATED, task=task))

    def on_task_checkpoint(
        self, *, task_id: str, checkpoint_kind: str, summary_text: str = ""
    ) -> None:
        self._enqueue(
            _ProjectionItem(
                op=_OP_CHECKPOINT,
                task_id=task_id,
                checkpoint_kind=checkpoint_kind,
                summary_text=summary_text,
            )
        )

    def on_heartbeat(self, task_id: str) -> None:
        """Project a coarse heartbeat, throttled to at most one per window (7.3).

        The throttle decision is made at enqueue time so a per-tick caller never
        even enqueues (let alone POSTs) more than once per window.
        """
        now = self._clock()
        with self._lock:
            last = self._last_heartbeat_at.get(task_id)
            if last is not None and (now - last) < self._heartbeat_window:
                return
            self._last_heartbeat_at[task_id] = now
        self._enqueue(_ProjectionItem(op=_OP_HEARTBEAT, task_id=task_id))

    def on_restart_recovery(self, *, started_at: str, reason: str = "abandoned_by_restart") -> None:
        self._enqueue(
            _ProjectionItem(
                op=_OP_RESTART_RECOVERY,
                recovery_started_at=started_at,
                recovery_reason=reason,
            )
        )

    def _enqueue(self, item: _ProjectionItem) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            logger.warning("mission projector queue full; dropping %s", item.op)

    # -- worker ----------------------------------------------------------

    def _run_worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item.op == "__stop__":
                    return
                self._handle_with_retry(item)
            except Exception:  # noqa: BLE001 — a worker must never die
                logger.debug("mission projector worker error", exc_info=True)
            finally:
                self._queue.task_done()

    def _handle_with_retry(self, item: _ProjectionItem) -> None:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                self._handle(item)
                return
            except Exception as exc:  # noqa: BLE001 — retry then drop
                last_exc = exc
                if attempt + 1 < self._max_retries:
                    self._retry_sleep(_backoff_seconds(attempt))
        logger.warning(
            "mission projector dropping %s after %d attempts: %s",
            item.op,
            self._max_retries,
            type(last_exc).__name__ if last_exc else "unknown",
        )

    def _handle(self, item: _ProjectionItem) -> None:
        if item.op == _OP_CREATED:
            assert item.task is not None
            self._ensure_mission(item.task)
        elif item.op == _OP_CHECKPOINT:
            assert item.task_id is not None and item.checkpoint_kind is not None
            self._handle_checkpoint(
                item.task_id, item.checkpoint_kind, item.summary_text or ""
            )
        elif item.op == _OP_HEARTBEAT:
            assert item.task_id is not None
            self._handle_heartbeat(item.task_id)
        elif item.op == _OP_RESTART_RECOVERY:
            assert item.recovery_started_at is not None
            self._transport.restart_recovery(
                {
                    "startedAt": item.recovery_started_at,
                    "reason": item.recovery_reason or "abandoned_by_restart",
                }
            )

    # -- projection primitives ------------------------------------------

    def _ensure_mission(self, task: WorkTask) -> str | None:
        """Return the hosted mission id for *task*, creating it idempotently.

        Reads the durable ``mission_projection`` mapping first; on a hit returns
        the stored mission id (no network). On a miss (first projection OR a lost
        mapping row) it POSTs ``createMission`` with ``idempotencyKey =
        "wq:<task_id>"`` — chat-proxy returns the existing row if the key was
        seen before (``missions.js:400-417``), so a lost mapping row is recovered
        without duplicating a hosted mission.
        """
        mapped = self._store.get_mission_projection(task.id)
        if mapped and mapped.get("mission_id"):
            return str(mapped["mission_id"])

        summary = project_task_to_mission_summary(task)
        created = self._transport.create_mission(_create_mission_body(summary))
        mission_id = created.get("id") if isinstance(created, dict) else None
        if not isinstance(mission_id, str) or not mission_id:
            raise MissionTransportError("mission create returned no id")
        self._store.upsert_mission_projection(
            task.id, mission_id=mission_id, last_projected_status=summary["status"]
        )
        return mission_id

    def _handle_checkpoint(
        self, task_id: str, checkpoint_kind: str, summary_text: str
    ) -> None:
        task = self._store.get(task_id)
        if task is None:
            return
        mission_id = self._ensure_mission(task)
        if mission_id is None:
            return

        if checkpoint_kind == "claimed":
            trigger = "goal_continue" if task.goal_mode else "user"
            run = self._transport.create_run(
                mission_id, {"triggerType": trigger, "status": "running"}
            )
            run_id = run.get("id") if isinstance(run, dict) else None
            if isinstance(run_id, str) and run_id:
                self._task_run_ids[task_id] = run_id
            self._transport.append_event(
                mission_id, {"actorType": "system", "eventType": "claimed"}
            )
        elif checkpoint_kind in _TERMINAL_CHECKPOINTS:
            run_status = "failed" if checkpoint_kind == "failed" else "completed"
            event_type = "failed" if checkpoint_kind == "failed" else "completed"
            finished_at = _now_iso()
            run_id = self._task_run_ids.pop(task_id, None)
            update_body: dict[str, Any] = {"status": run_status, "finishedAt": finished_at}
            if summary_text:
                key = "errorMessage" if run_status == "failed" else "resultPreview"
                update_body[key] = summary_text[:1000]
            if run_id:
                self._transport.update_run(mission_id, run_id, update_body)
            else:
                # No known open run (e.g. created while inert / after restart):
                # record a terminal run row so the ledger is not empty.
                self._transport.create_run(
                    mission_id,
                    {"triggerType": "user", "status": run_status, **_run_finish(update_body)},
                )
            self._transport.append_event(
                mission_id,
                {
                    "actorType": "system",
                    "eventType": event_type,
                    "message": summary_text[:2000] or None,
                },
            )
        else:
            # Any other checkpoint kind: a bare event, no run mutation.
            logger.debug("mission projector: unmapped checkpoint kind %r", checkpoint_kind)
            return

        # Keep the mapping's last-projected status current for idempotent
        # re-projection and for PR-M8's reconciler cursor bookkeeping.
        self._store.upsert_mission_projection(
            task_id,
            mission_id=mission_id,
            last_projected_status=map_task_status(task.status),
        )

    def _handle_heartbeat(self, task_id: str) -> None:
        task = self._store.get(task_id)
        if task is None:
            return
        mission_id = self._ensure_mission(task)
        if mission_id is None:
            return
        self._transport.append_event(
            mission_id, {"actorType": "system", "eventType": "heartbeat"}
        )


# ---------------------------------------------------------------------------
# create-mission body shaping (read-shape summary -> chat-proxy INPUT shape)
# ---------------------------------------------------------------------------


def _create_mission_body(summary: Mapping[str, Any]) -> dict:
    """Translate the M1 read-shape summary into the chat-proxy ``createMission``
    INPUT body (``validateCreateMission``, ``missions.js:119-136`` — camelCase
    ``channelType`` / ``channelId`` / ``createdBy`` / ``idempotencyKey``)."""
    return {
        "title": summary["title"],
        "kind": summary["kind"],
        "channelType": summary["channel_type"],
        "channelId": summary["channel_id"],
        "status": summary["status"],
        "createdBy": summary.get("created_by", "agent"),
        "summary": summary.get("summary"),
        "idempotencyKey": summary["idempotencyKey"],
        "metadata": summary.get("metadata", {}),
    }


def _run_finish(update_body: Mapping[str, Any]) -> dict:
    """The ``finishedAt`` / preview fields of a terminal run, for the create-run
    fallback path (createRun accepts no finishedAt, so only carry previews)."""
    out: dict[str, Any] = {}
    if "resultPreview" in update_body:
        out["resultPreview"] = update_body["resultPreview"]
    if "errorMessage" in update_body:
        out["errorMessage"] = update_body["errorMessage"]
    return out


def _backoff_seconds(attempt: int) -> float:
    return min(2.0, 0.1 * (2 ** attempt))


def _no_sleep(_seconds: float) -> None:
    return None


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Activation + process singleton
# ---------------------------------------------------------------------------


def projector_active(env: Mapping[str, str]) -> bool:
    """Config-presence + kill-switch activation gate (section 7.1).

    Active iff ``CORE_AGENT_CHAT_PROXY_URL`` and ``GATEWAY_TOKEN`` are both set
    AND ``CORE_AGENT_PYTHON_MISSION_RUNTIME`` is truthy. No new capability flag.
    """
    url = (env.get(CHAT_PROXY_URL_ENV) or "").strip()
    token = (env.get(GATEWAY_TOKEN_ENV) or "").strip()
    if not url or not token:
        return False
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool(KILL_SWITCH_FLAG, env=env)


def build_projector_from_env(
    env: Mapping[str, str] | None = None,
    *,
    start: bool = True,
) -> MissionProjector | None:
    """Build the projector from *env*, or None when inert (config absent / killed).

    Returns None on OSS local (URL/token absent) and while the kill-switch is
    OFF, so callers get a naturally inert seam without a new capability flag.
    """
    import os  # noqa: PLC0415

    resolved = os.environ if env is None else env
    if not projector_active(resolved):
        return None

    from magi_agent.missions.work_queue.store import (  # noqa: PLC0415
        SqliteWorkQueueStore,
        work_queue_db_path_from_env,
    )

    base_url = (resolved.get(CHAT_PROXY_URL_ENV) or "").strip()
    token = (resolved.get(GATEWAY_TOKEN_ENV) or "").strip()
    transport = UrllibMissionTransport(base_url, token)
    store = SqliteWorkQueueStore(work_queue_db_path_from_env())
    projector = MissionProjector(transport, store)
    if start:
        projector.start()
    return projector


_ACTIVE_PROJECTOR: MissionProjector | None = None
_ACTIVE_BUILT = False
_ACTIVE_LOCK = threading.Lock()


def _get_active_projector() -> MissionProjector | None:
    global _ACTIVE_PROJECTOR, _ACTIVE_BUILT
    if _ACTIVE_BUILT:
        return _ACTIVE_PROJECTOR
    with _ACTIVE_LOCK:
        if _ACTIVE_BUILT:
            return _ACTIVE_PROJECTOR
        try:
            _ACTIVE_PROJECTOR = build_projector_from_env()
        except Exception:  # noqa: BLE001 — never break a seam call site
            logger.debug("mission projector build failed", exc_info=True)
            _ACTIVE_PROJECTOR = None
        _ACTIVE_BUILT = True
    return _ACTIVE_PROJECTOR


def reset_active_projector() -> None:
    """Test seam: drop the cached process singleton so the next seam call
    rebuilds from the current env."""
    global _ACTIVE_PROJECTOR, _ACTIVE_BUILT
    with _ACTIVE_LOCK:
        if _ACTIVE_PROJECTOR is not None:
            try:
                _ACTIVE_PROJECTOR.stop()
            except Exception:  # noqa: BLE001
                pass
        _ACTIVE_PROJECTOR = None
        _ACTIVE_BUILT = False


# ---------------------------------------------------------------------------
# Seam wrappers (fail-open, non-blocking; called from the wiring chokepoints)
# ---------------------------------------------------------------------------


def notify_task_created(task: WorkTask) -> None:
    """Create seam (``scheduled_work.run_in_background`` after create_idempotent)."""
    projector = _get_active_projector()
    if projector is None:
        return
    try:
        projector.on_task_created(task)
    except Exception:  # noqa: BLE001 — projection must never break the tool path
        logger.debug("notify_task_created failed", exc_info=True)


def notify_task_checkpoint(*, task_id: str, checkpoint_kind: str, summary_text: str = "") -> None:
    """Transition seam (driver ``_emit_task_checkpoint_sync`` chokepoint)."""
    projector = _get_active_projector()
    if projector is None:
        return
    try:
        projector.on_task_checkpoint(
            task_id=task_id, checkpoint_kind=checkpoint_kind, summary_text=summary_text
        )
    except Exception:  # noqa: BLE001 — projection must never break dispatch
        logger.debug("notify_task_checkpoint failed", exc_info=True)


def notify_restart_recovery(*, started_at: str, reason: str = "abandoned_by_restart") -> None:
    """Recovery seam (``StartupRecoverySweep.run``)."""
    projector = _get_active_projector()
    if projector is None:
        return
    try:
        projector.on_restart_recovery(started_at=started_at, reason=reason)
    except Exception:  # noqa: BLE001 — projection must never break boot
        logger.debug("notify_restart_recovery failed", exc_info=True)


__all__ = [
    "DEFAULT_HEARTBEAT_WINDOW_SECONDS",
    "DEFAULT_MAX_RETRIES",
    "MissionMappingStore",
    "MissionProjector",
    "MissionTransport",
    "MissionTransportError",
    "UrllibMissionTransport",
    "build_projector_from_env",
    "notify_restart_recovery",
    "notify_task_checkpoint",
    "notify_task_created",
    "projector_active",
    "reset_active_projector",
]
