from __future__ import annotations

import json
import logging
import secrets
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from magi_agent.observability.bus import ActivityBus
from magi_agent.observability.store import ActivityStore
from magi_agent.observability.taxonomy import get_meta_taxonomy

logger = logging.getLogger(__name__)

_PREFIX = "/api/observability/v1"


def make_auth_dependency(runtime: Any):
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


def event_frame(event: dict) -> str:
    return f"event: activity\ndata: {json.dumps(event, separators=(',', ':'), default=str)}\n\n"


def build_api_router(store: ActivityStore, bus: ActivityBus, runtime: Any) -> APIRouter:
    router = APIRouter(prefix=_PREFIX)
    auth = make_auth_dependency(runtime)
    bot_id = getattr(getattr(runtime, "config", None), "bot_id", None)

    @router.get("/meta")
    async def meta(_: str = Depends(auth)) -> dict:
        return {
            "version": "v1",
            "bot_id": bot_id,
            "events": store.count_events(),
            "kind_breakdown": store.kind_breakdown(),
            "categories": get_meta_taxonomy(),
        }

    @router.get("/activity")
    async def activity(
        _: str = Depends(auth),
        session_id: str | None = Query(default=None),
        kind: str | None = Query(default=None),
        exclude_kind: str | None = Query(default=None),
        status: str | None = Query(default=None),
        q: str | None = Query(default=None),
        since_id: int | None = Query(default=None),
        before_id: int | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=1000),
        has_evidence: bool = Query(default=False),
    ) -> dict:
        events = store.list_events(
            session_id=session_id,
            kind=kind,
            exclude_kind=exclude_kind,
            status=status,
            q=q,
            since_id=since_id,
            before_id=before_id,
            limit=limit,
            has_evidence=has_evidence,
        )
        return {"events": events}

    @router.get("/activity/stream")
    async def activity_stream(
        _: str = Depends(auth),
        session_id: str | None = Query(default=None),
        max_events: int | None = Query(default=None, ge=1, le=10000),
    ) -> StreamingResponse:
        channel = session_id or "*"

        async def gen() -> AsyncIterator[str]:
            sub = bus.subscribe(channel=channel)
            sent = 0
            try:
                # Sentinel confirms the stream is open. Delivered only to this
                # subscriber (never via bus.publish) so a client cannot inject
                # events into other subscribers' feeds.
                yield event_frame({"kind": "stream_open"})
                sent += 1
                if max_events is not None and sent >= max_events:
                    return
                async for ev in sub:
                    yield event_frame(ev)
                    sent += 1
                    if max_events is not None and sent >= max_events:
                        break
            finally:
                await sub.aclose()

        return StreamingResponse(gen(), media_type="text/event-stream")

    @router.get("/sessions")
    async def sessions(
        _: str = Depends(auth),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict:
        return {"sessions": store.list_sessions(limit=limit)}

    @router.get("/sessions/{session_id}/events")
    async def session_events(
        session_id: str,
        _: str = Depends(auth),
        since_id: int | None = Query(default=None),
        limit: int = Query(default=500, ge=1, le=1000),
    ) -> dict:
        return {
            "session_id": session_id,
            "events": store.list_events(session_id=session_id, since_id=since_id, limit=limit),
        }

    @router.get("/health/live")
    async def health_live(_: str = Depends(auth)) -> dict:
        try:
            from magi_agent.transport.health import healthz_payload

            return dict(healthz_payload(runtime))
        except Exception:
            logger.warning("observability /health/live unavailable", exc_info=True)
            return {"ok": False, "error": "health_unavailable"}

    @router.get("/board")
    async def board(_: str = Depends(auth)) -> dict:
        return {"board": store.latest_event_with_kind_like("board")}

    @router.get("/channels")
    async def channels(_: str = Depends(auth)) -> dict:
        # OSS runtime exposes no live channel registry; declared config only.
        return {"channels": [], "note": "no live channel registry in OSS runtime"}

    @router.get("/missions")
    async def missions(_: str = Depends(auth)) -> dict:
        # OSS runtime has no scheduler/mission store; placeholder for future wiring.
        return {"missions": [], "note": "no scheduler/mission registry in OSS runtime"}

    return router
