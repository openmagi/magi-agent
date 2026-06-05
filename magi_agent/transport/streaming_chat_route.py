"""Hosted-grade SSE streaming-chat HTTP surface.

Three FastAPI routes wired additively (no edit to ``chat.py``):

``POST /v1/chat/stream``
    Start a new streaming turn for a session. Returns an SSE byte stream via
    ``drive_streaming_chat``. Default-OFF behind ``MAGI_STREAMING_CHAT``.

``POST /v1/chat/control-response``
    Resolve a parked tool-permission ask for an active turn's prompt sink.

``POST /v1/chat/cancel``
    Request cooperative cancellation of an active turn.

Registration
------------
Call :func:`register_streaming_chat_routes` in ``app.py`` right after the
existing ``register_chat_routes`` call. The routes are mounted additively; this
module does NOT import or modify ``magi_agent.transport.chat``.

Feature gate
------------
``MAGI_STREAMING_CHAT`` must be truthy (``1`` / ``true`` / ``yes`` / ``on``)
for the ``/v1/chat/stream`` route to execute.  The control-response and cancel
routes are always registered (they are no-ops when no active turn exists) and
are gated only by the gateway-token auth check.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from magi_agent.cli.protocol import ControlResponse
from magi_agent.ops.health import _truthy_env
from magi_agent.transport.active_turn import ACTIVE_TURNS, ActiveTurn
from magi_agent.transport.streaming_driver import drive_streaming_chat
from magi_agent.transport.streaming_sink import build_streaming_prompt_sink

__all__ = [
    "register_streaming_chat_routes",
    "_streaming_chat_enabled",
    "_extract_prompt_text",
]


# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------

def _streaming_chat_enabled() -> bool:
    """Return True when ``MAGI_STREAMING_CHAT`` is truthy. Evaluated per-call."""
    return _truthy_env("MAGI_STREAMING_CHAT")


# ---------------------------------------------------------------------------
# Local helpers (mirroring chat.py style; NOT imported from there)
# ---------------------------------------------------------------------------

def _body_string(body: object, key: str, default: str) -> str:
    """Extract a non-empty string field from a mapping body, or return *default*."""
    if isinstance(body, Mapping):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _extract_prompt_text(body: object) -> str:
    """Join all user-text content from ``body["messages"]``.

    Mirrors the logic of ``_local_chat_prompt_text`` in ``chat.py`` (string
    ``content``, or text blocks in a list ``content``), but lives entirely in
    this module so there is no import coupling.
    """
    if not isinstance(body, Mapping):
        return ""
    messages = body.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return ""
    text_parts: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
            for block in content:
                if isinstance(block, Mapping):
                    text = block.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
    return "\n".join(part.strip() for part in text_parts if part.strip())


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_streaming_chat_routes(
    app: FastAPI,
    runtime: object,
    *,
    engine_builder: Callable[[str, object], tuple[object, object]] | None = None,
) -> None:
    """Mount the three streaming-chat routes on *app*.

    Parameters
    ----------
    app:
        The FastAPI application instance.
    runtime:
        The ``OpenMagiRuntime`` (or compatible duck-typed stub). Used only for
        ``runtime.config.gateway_token`` and ``runtime.config.model``.
    engine_builder:
        Optional factory ``(session_id: str, sink) -> (engine, gate)``.
        When omitted the default uses :func:`~magi_agent.cli.wiring.build_headless_runtime`
        with ``permission_mode="default"`` and ``MAGI_AGENT_WORKSPACE`` or
        ``os.getcwd()`` as ``cwd``.
    """

    def _default_engine_builder(session_id: str, sink: object) -> tuple[object, object]:
        from magi_agent.cli.wiring import build_headless_runtime  # lazy to avoid cold-start cost

        model = getattr(getattr(runtime, "config", None), "model", None)
        cwd = os.environ.get("MAGI_AGENT_WORKSPACE") or os.getcwd()
        rt = build_headless_runtime(
            cwd=cwd,
            permission_mode="default",
            session_id=session_id,
            model=model,
            prompt_sink=sink,
        )
        return rt.engine, rt.gate

    builder = engine_builder if engine_builder is not None else _default_engine_builder

    def _auth_ok(request: Request) -> bool:
        token = getattr(getattr(runtime, "config", None), "gateway_token", None) or ""
        expected = f"Bearer {token}"
        return request.headers.get("authorization", "") == expected

    # ------------------------------------------------------------------
    # Route 1 — POST /v1/chat/stream
    # ------------------------------------------------------------------
    @app.post("/v1/chat/stream")
    async def streaming_chat_stream(request: Request):  # type: ignore[return]
        if not _auth_ok(request):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})

        if not _streaming_chat_enabled():
            return JSONResponse(
                status_code=503,
                content={"error": "streaming_chat_disabled"},
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "malformed_json"})

        session_id = _body_string(
            body,
            "sessionId",
            request.headers.get("x-openclaw-session-key", "stream-session"),
        )
        turn_id = _body_string(body, "turnId", f"{session_id}:turn")
        prompt = _extract_prompt_text(body)

        queue: asyncio.Queue[object] = asyncio.Queue()
        sink = build_streaming_prompt_sink(queue, turn_id=turn_id)
        engine, gate = builder(session_id, sink)
        cancel = asyncio.Event()

        return StreamingResponse(
            drive_streaming_chat(
                engine,
                gate,
                {"prompt": prompt, "session_id": session_id, "turn_id": turn_id},
                cancel=cancel,
                queue=queue,
                sink=sink,
                registry=ACTIVE_TURNS,
                session_id=session_id,
                turn_id=turn_id,
            ),
            media_type="text/event-stream",
        )

    # ------------------------------------------------------------------
    # Route 2 — POST /v1/chat/control-response
    # ------------------------------------------------------------------
    @app.post("/v1/chat/control-response")
    async def streaming_chat_control_response(request: Request):  # type: ignore[return]
        if not _auth_ok(request):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "malformed_json"})

        session_id = _body_string(body, "sessionId", "")
        request_id = _body_string(body, "request_id", "")
        response_dict = body.get("response") if isinstance(body, Mapping) else None
        if not isinstance(response_dict, Mapping):
            response_dict = {}

        turn = ACTIVE_TURNS.get(session_id)
        if turn is None:
            return JSONResponse(
                status_code=404,
                content={"error": "no_active_turn"},
            )

        turn.sink.deliver(
            ControlResponse(request_id=request_id, response=dict(response_dict))
        )
        return JSONResponse(
            status_code=200,
            content={"status": "delivered", "request_id": request_id},
        )

    # ------------------------------------------------------------------
    # Route 3 — POST /v1/chat/cancel
    # ------------------------------------------------------------------
    @app.post("/v1/chat/cancel")
    async def streaming_chat_cancel(request: Request):  # type: ignore[return]
        if not _auth_ok(request):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "malformed_json"})

        session_id = _body_string(
            body,
            "sessionId",
            request.headers.get("x-openclaw-session-key", ""),
        )
        handoff = bool(body.get("handoffRequested")) if isinstance(body, Mapping) else False

        turn = ACTIVE_TURNS.get(session_id)
        if turn is None:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "no_active_turn",
                    "activeTurnCompatible": False,
                    "handoffRequested": handoff,
                },
            )

        turn.cancel.set()
        return JSONResponse(
            status_code=200,
            content={
                "status": "cancelling",
                "activeTurnCompatible": True,
                "handoffRequested": handoff,
            },
        )
