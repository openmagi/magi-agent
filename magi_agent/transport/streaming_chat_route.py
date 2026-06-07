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
existing ``register_chat_routes`` call. The routes are mounted additively. When
the selected Gate5B user-visible canary gate is active, the stream route reuses
the selected ``chat.py`` handler and adapts its safe public response into the
single-channel SSE stream.

Feature gate
------------
``MAGI_STREAMING_CHAT`` must be truthy (``1`` / ``true`` / ``yes`` / ``on``)
for all three routes to execute.  When the flag is off the routes return 503.
Auth is checked first (before the feature gate) on all three routes.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.protocol import ControlResponse
from magi_agent.ops.health import _truthy_env
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.transport.active_turn import ACTIVE_TURNS, ActiveTurn
from magi_agent.transport.chat import (
    gate5b_user_visible_chat_gate_active,
    run_gate5b_user_visible_chat_response,
)
from magi_agent.transport.streaming_chat import frame_for_event, frame_for_terminal
from magi_agent.transport.streaming_driver import drive_streaming_chat
from magi_agent.transport.streaming_sink import build_streaming_prompt_sink

__all__ = [
    "register_streaming_chat_routes",
    "_streaming_chat_enabled",
    "_extract_prompt_text",
]

# Maximum allowed size (bytes) of the JSON-serialised ``response`` dict in
# a control-response request.  Protects against oversized payloads.
_MAX_CONTROL_RESPONSE_BYTES = 8192


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


def _selected_gate5b_stream_active(runtime: object) -> bool:
    if os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() != "on":
        return False
    try:
        return gate5b_user_visible_chat_gate_active(runtime)
    except Exception:
        return False


def _json_response_mapping(response: JSONResponse) -> dict[str, object]:
    raw_body = getattr(response, "body", b"")
    if isinstance(raw_body, bytes):
        text = raw_body.decode("utf-8")
    else:
        text = str(raw_body)
    parsed = json.loads(text)
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _assistant_content_from_chat_response(payload: Mapping[str, object]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)):
        return ""
    if not choices:
        return ""
    first = choices[0]
    if not isinstance(first, Mapping):
        return ""
    message = first.get("message")
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _selected_failure_reason(payload: Mapping[str, object]) -> str:
    for key in ("reason", "error", "status"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "selected_stream_failed"


def _runtime_event_frame(payload: Mapping[str, object], *, turn_id: str) -> bytes | None:
    return frame_for_event(
        RuntimeEvent(
            type="status",
            payload=dict(payload),
            turn_id=turn_id,
        )
    )


async def _drive_selected_gate5b_stream(
    runtime: object,
    body: object,
    request: Request,
    *,
    session_id: str,
    turn_id: str,
) -> AsyncIterator[bytes]:
    """Adapt the selected Gate5B chat response to the streaming SSE contract."""
    try:
        response = await run_gate5b_user_visible_chat_response(
            runtime,
            body,
            request=request,
        )
        payload = _json_response_mapping(response)
    except Exception:
        reason = "selected_stream_bridge_error"
        frame = _runtime_event_frame(
            {"type": "error", "code": reason, "message": reason},
            turn_id=turn_id,
        )
        if frame is not None:
            yield frame
        for chunk in frame_for_terminal(
            EngineResult(
                terminal=Terminal.error,
                error=reason,
                session_id=session_id,
                turn_id=turn_id,
            )
        ):
            yield chunk
        return

    if response.status_code == 200 and payload.get("status") == "python_ready":
        public_events = payload.get("publicEvents")
        if isinstance(public_events, Sequence) and not isinstance(public_events, (str, bytes)):
            for public_event in public_events:
                if not isinstance(public_event, Mapping):
                    continue
                frame = _runtime_event_frame(public_event, turn_id=turn_id)
                if frame is not None:
                    yield frame
        content = _assistant_content_from_chat_response(payload)
        if content:
            frame = _runtime_event_frame(
                {"type": "text_delta", "delta": content},
                turn_id=turn_id,
            )
            if frame is not None:
                yield frame
        for chunk in frame_for_terminal(
            EngineResult(
                terminal=Terminal.completed,
                session_id=session_id,
                turn_id=turn_id,
            )
        ):
            yield chunk
        return

    reason = _selected_failure_reason(payload)
    frame = _runtime_event_frame(
        {"type": "error", "code": reason, "message": reason},
        turn_id=turn_id,
    )
    if frame is not None:
        yield frame
    for chunk in frame_for_terminal(
        EngineResult(
            terminal=Terminal.error,
            error=reason,
            session_id=session_id,
            turn_id=turn_id,
        )
    ):
        yield chunk


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
        # NOTE: build_headless_runtime(prompt_sink=...) wires a RulesPermissionGate
        # backed by an EMPTY RulesEngine, so the engine's `updated_input`
        # re-validation (which would re-check a rewritten tool's args against deny
        # rules) is a no-op for this streaming surface — a control-response
        # `updated_input` is applied verbatim. This is acceptable because the
        # control-response comes from the gateway-token holder (full bot access).
        # If multi-user / shared-token control-responses are ever introduced here,
        # seed baseline deny rules so rewritten args are re-validated.
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
        token = getattr(getattr(runtime, "config", None), "gateway_token", None)
        if not token:  # None or empty string → refuse all requests
            return False
        return request.headers.get("authorization", "") == f"Bearer {token}"

    # ------------------------------------------------------------------
    # Route 1 — POST /v1/chat/stream
    # ------------------------------------------------------------------
    @app.post("/v1/chat/stream", response_model=None)
    async def streaming_chat_stream(request: Request) -> Response:
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
            request.headers.get("x-openclaw-session-key", ""),
        )
        if not session_id:
            session_id = uuid.uuid4().hex
        turn_id = _body_string(body, "turnId", f"{session_id}:turn")
        prompt = _extract_prompt_text(body)

        if _selected_gate5b_stream_active(runtime):
            return StreamingResponse(
                _drive_selected_gate5b_stream(
                    runtime,
                    body,
                    request,
                    session_id=session_id,
                    turn_id=turn_id,
                ),
                media_type="text/event-stream",
            )

        queue: asyncio.Queue[object] = asyncio.Queue()
        sink = build_streaming_prompt_sink(queue, turn_id=turn_id)
        # The engine build runs synchronously BEFORE the StreamingResponse is
        # created, so a build-time failure must not escape as a bare 500 mid-
        # contract. No SSE bytes have been sent yet, so returning a JSON 500
        # here is safe (the client has not started consuming an event stream).
        try:
            engine, gate = builder(session_id, sink)
        except Exception:
            return JSONResponse(status_code=500, content={"error": "engine_build_failed"})
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
    @app.post("/v1/chat/control-response", response_model=None)
    async def streaming_chat_control_response(request: Request) -> JSONResponse:
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

        session_id = _body_string(body, "sessionId", "")
        if not session_id:
            session_id = request.headers.get("x-openclaw-session-key", "")
        if not session_id:
            return JSONResponse(status_code=400, content={"error": "missing_session_id"})

        request_id = _body_string(body, "request_id", "")
        response_dict = body.get("response") if isinstance(body, Mapping) else None
        if not isinstance(response_dict, Mapping):
            response_dict = {}

        if len(json.dumps(response_dict)) > _MAX_CONTROL_RESPONSE_BYTES:
            return JSONResponse(status_code=400, content={"error": "response_too_large"})

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
    @app.post("/v1/chat/cancel", response_model=None)
    async def streaming_chat_cancel(request: Request) -> JSONResponse:
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
            request.headers.get("x-openclaw-session-key", ""),
        )
        if not session_id:
            return JSONResponse(status_code=400, content={"error": "missing_session_id"})

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
