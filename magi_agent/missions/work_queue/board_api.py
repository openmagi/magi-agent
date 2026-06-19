"""Read-only FastAPI board router for the durable work-queue.

Mirrors ``magi_agent.observability.api`` for auth + router shape and
``magi_agent.observability.integration`` for the gated-mount pattern.
Import boundary: MAY import FastAPI; must NOT import google.adk, network
clients, or subprocess.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from magi_agent.config.flags import flag_bool
from magi_agent.missions.work_queue.store import SqliteWorkQueueStore

logger = logging.getLogger(__name__)

_PREFIX = "/api/work-queue/v1"


# ---------------------------------------------------------------------------
# Auth dependency — mirrors observability/api.py make_auth_dependency
# ---------------------------------------------------------------------------


def make_auth_dependency(runtime: Any):
    """Return a FastAPI dependency that validates Bearer tokens.

    Fail-closed: a falsy ``runtime.config.gateway_token`` (unset / empty)
    always returns 401 so the board is never accidentally open.
    """
    expected = getattr(getattr(runtime, "config", None), "gateway_token", None)

    async def _auth(authorization: str | None = Header(default=None)) -> str:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        # No token configured (falsy `expected`) → fail closed: always deny.
        if not expected or not secrets.compare_digest(token, str(expected)):
            raise HTTPException(status_code=401, detail="unauthorized")
        return token

    return _auth


# ---------------------------------------------------------------------------
# Router builder
# ---------------------------------------------------------------------------


def build_work_queue_board_router(store: Any, runtime: Any) -> APIRouter:
    """Return a read-only APIRouter (prefix ``/api/work-queue/v1``) backed by
    *store*.  All endpoints require a valid Bearer token via the runtime's
    ``config.gateway_token``; the router never writes or mutates state."""
    router = APIRouter(prefix=_PREFIX)
    auth = make_auth_dependency(runtime)

    @router.get("/tasks")
    async def list_tasks(
        _: str = Depends(auth),
        status: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        tasks = store.list_tasks(status=status, limit=limit, offset=offset)
        return {"tasks": [t.model_dump() for t in tasks]}

    @router.get("/tasks/{task_id}/events")
    async def task_events(
        task_id: str,
        _: str = Depends(auth),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict:
        return {
            "task_id": task_id,
            "events": store.list_task_events(task_id, limit=limit),
        }

    @router.get("/tasks/{task_id}/runs")
    async def task_runs(
        task_id: str,
        _: str = Depends(auth),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict:
        return {
            "task_id": task_id,
            "runs": store.list_task_runs(task_id, limit=limit),
        }

    @router.get("/tasks/{task_id}")
    async def task_detail(
        task_id: str,
        _: str = Depends(auth),
    ) -> dict:
        task = store.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return {"task": task.model_dump()}

    return router


# ---------------------------------------------------------------------------
# Gate helper — mirrors observability env-read convention
# ---------------------------------------------------------------------------


def is_work_queue_board_api_enabled() -> bool:
    """Return True iff ``MAGI_WORK_QUEUE_BOARD_API_ENABLED`` is set to a
    truthy value (``1``/``true``/``yes``/``on``).  Default-OFF.

    Reads via the central flag registry (``flag_bool``) so call-site counts
    in the ratchet gate are not incremented.
    """
    return flag_bool("MAGI_WORK_QUEUE_BOARD_API_ENABLED")


# ---------------------------------------------------------------------------
# Gated mount — mirrors observability/integration.py register_observability
# ---------------------------------------------------------------------------


def register_work_queue_board(app: Any, runtime: Any) -> None:
    """Mount the read-only work-queue board router into *app*.

    Fully inert (returns None) when ``MAGI_WORK_QUEUE_BOARD_API_ENABLED`` is
    unset or falsy, leaving the default fleet surface byte-identical.

    Idempotent: a second call when the router is already mounted is a no-op
    (guarded via ``app.state``).
    """
    if not is_work_queue_board_api_enabled():
        return None

    if getattr(getattr(app, "state", None), "work_queue_board_mounted", False):
        return None  # already registered

    from magi_agent.missions.work_queue.store import work_queue_db_path_from_env  # noqa: PLC0415

    db_path = work_queue_db_path_from_env()
    store = SqliteWorkQueueStore(db_path)
    router = build_work_queue_board_router(store, runtime)
    app.include_router(router)

    try:
        app.state.work_queue_board_mounted = True
    except Exception:
        pass  # state attribute not available; idempotency best-effort

    logger.info("work-queue board API mounted at %s", _PREFIX)
    return None
