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
from magi_agent.channels.workflow_confirm_store import (
    InMemoryPendingConfirmationStore,
    PendingConfirmationStore,
)
from magi_agent.channels.workflow_gate import channel_workflows_enabled
from magi_agent.channels.taskkind_classifier import FixedClassifier, TaskKindClassifier
from magi_agent.channels.workflow_orchestrator import (
    WorkflowOrchestratorResult,
    resolve_confirmation,
    route_inbound,
    start_research,
)
from magi_agent.cli.protocol import ControlResponse
from magi_agent.ops.health import _truthy_env
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.memory_mode_context import (
    current_memory_mode,
    memory_mode_request_scope,
)
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
    "_local_full_access",
]

_DEFAULT_PER_CHILD_TOKENS = 8000
_DEFAULT_MODEL_MICROCENTS_PER_1K = 120
# Process-wide default store so a confirmation prompt issued on one /v1/chat/stream
# request survives until the user's yes/no arrives on the NEXT request.
_DEFAULT_CONFIRM_STORE: PendingConfirmationStore = InMemoryPendingConfirmationStore()

# Maximum allowed size (bytes) of the JSON-serialised ``response`` dict in
# a control-response request.  Protects against oversized payloads.
_MAX_CONTROL_RESPONSE_BYTES = 8192


# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------

def _streaming_chat_enabled() -> bool:
    """Return True when ``MAGI_STREAMING_CHAT`` is truthy. Evaluated per-call."""
    return _truthy_env("MAGI_STREAMING_CHAT")


def _local_full_access(runtime: object) -> bool:
    """Return True for the loopback local ``magi-agent serve`` owner.

    This mirrors ``magi_agent.main`` local defaults without importing main from
    the transport layer. Hosted/multi-user deployments have real user/bot/token
    values and therefore keep the normal permission gate.
    """
    config = getattr(runtime, "config", None)
    return (
        getattr(config, "bot_id", None) == "local-bot"
        and getattr(config, "user_id", None) == "local-user"
        and getattr(config, "gateway_token", None) == "local-dev-token"
    )


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
    live_events: asyncio.Queue[Mapping[str, object]] = asyncio.Queue()
    emitted_event_keys: set[str] = set()
    live_text_emitted = False

    def _event_key(payload: Mapping[str, object]) -> str:
        return json.dumps(dict(payload), sort_keys=True, default=str)

    def _enqueue_public_event(payload: Mapping[str, object]) -> None:
        nonlocal live_text_emitted
        event = dict(payload)
        if event.get("type") == "text_delta":
            live_text_emitted = True
        live_events.put_nowait(event)

    async def _run_selected_response() -> object:
        return await run_gate5b_user_visible_chat_response(
            runtime,
            body,
            request=request,
            public_event_sink=_enqueue_public_event,
        )

    response_task = asyncio.create_task(_run_selected_response())
    try:
        while True:
            if response_task.done() and live_events.empty():
                break
            next_event_task = asyncio.create_task(live_events.get())
            done, _pending = await asyncio.wait(
                {response_task, next_event_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if next_event_task in done:
                public_event = next_event_task.result()
                emitted_event_keys.add(_event_key(public_event))
                frame = _runtime_event_frame(public_event, turn_id=turn_id)
                if frame is not None:
                    yield frame
                continue
            next_event_task.cancel()
            try:
                await next_event_task
            except asyncio.CancelledError:
                pass
        response = response_task.result()
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
    finally:
        if not response_task.done():
            response_task.cancel()
            try:
                await response_task
            except (asyncio.CancelledError, Exception):
                pass

    if response.status_code == 200 and payload.get("status") == "python_ready":
        public_events = payload.get("publicEvents")
        if isinstance(public_events, Sequence) and not isinstance(public_events, (str, bytes)):
            for public_event in public_events:
                if not isinstance(public_event, Mapping):
                    continue
                event_key = _event_key(public_event)
                if event_key in emitted_event_keys:
                    continue
                emitted_event_keys.add(event_key)
                frame = _runtime_event_frame(public_event, turn_id=turn_id)
                if frame is not None:
                    yield frame
        content = _assistant_content_from_chat_response(payload)
        if content and not live_text_emitted:
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


async def _drive_workflow_result_stream(
    result: WorkflowOrchestratorResult,
    *,
    session_id: str,
    turn_id: str,
) -> AsyncIterator[bytes]:
    """Project workflow confirmation/execution results onto the SSE contract."""
    if result.outcome == "awaiting_confirmation":
        phase = "planning"
        message = result.message or "Confirm workflow execution."
    elif result.outcome == "executed":
        phase = "committed"
        suffix = f": {result.executor_status}" if result.executor_status else ""
        message = f"Workflow executed{suffix}."
    elif result.outcome == "declined":
        phase = "committed"
        message = result.message or "Workflow declined."
    else:
        phase = "committed"
        message = result.message or "Workflow handled."

    phase_frame = _runtime_event_frame(
        {
            "type": "turn_phase",
            "eventId": f"{turn_id}:workflow:{result.outcome}",
            "turnId": turn_id,
            "phase": phase,
            "status": result.outcome,
            "message": message,
        },
        turn_id=turn_id,
    )
    if phase_frame is not None:
        yield phase_frame
    if message:
        text_frame = _runtime_event_frame(
            {"type": "text_delta", "delta": message},
            turn_id=turn_id,
        )
        if text_frame is not None:
            yield text_frame
    for chunk in frame_for_terminal(
        EngineResult(
            terminal=Terminal.completed,
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
    confirm_store: PendingConfirmationStore | None = None,
    eligibility_classifier: object | None = None,
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
    confirm_store:
        Optional :class:`~magi_agent.channels.workflow_confirm_store.PendingConfirmationStore`
        for workflow confirmation state. Defaults to the module-level
        ``_DEFAULT_CONFIRM_STORE`` (in-memory, process-scoped).
    eligibility_classifier:
        Optional async classifier with ``aclassify(message_text) -> str``. Defaults
        to :class:`~magi_agent.channels.taskkind_classifier.TaskKindClassifier` with
        no model factory (auto-detect inert; returns ``"general"`` until a live model
        is injected). The explicit ``/research`` path works regardless.
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
        from magi_agent.cli.wiring import (  # lazy to avoid cold-start cost
            build_headless_runtime,
            local_runner_policy_routing_enabled_from_env,
        )

        model = getattr(getattr(runtime, "config", None), "model", None)
        cwd = os.environ.get("MAGI_AGENT_WORKSPACE") or os.getcwd()
        local_full_access = _local_full_access(runtime)
        permission_mode = "bypassPermissions" if local_full_access else "default"
        runner_policy_routing_enabled = (
            local_runner_policy_routing_enabled_from_env()
            if local_full_access
            else None
        )
        rt = build_headless_runtime(
            cwd=cwd,
            permission_mode=permission_mode,
            session_id=session_id,
            model=model,
            prompt_sink=sink,
            runner_policy_routing_enabled=runner_policy_routing_enabled,
            memory_mode=current_memory_mode(),
        )
        return rt.engine, rt.gate

    builder = engine_builder if engine_builder is not None else _default_engine_builder

    store = confirm_store if confirm_store is not None else _DEFAULT_CONFIRM_STORE
    # Default classifier has NO model_factory → aclassify() returns "general"
    # (auto-detect inert until a live model is wired). The explicit "/research"
    # path works regardless. A hosted deployment injects a model-backed classifier.
    classifier = eligibility_classifier if eligibility_classifier is not None else TaskKindClassifier()

    async def _maybe_handle_workflow(
        prompt: str, session_id: str
    ) -> WorkflowOrchestratorResult | None:
        """Workflow pre-check for /v1/chat/stream.

        Returns a workflow result to short-circuit the normal turn, or None to
        proceed normally.
        Guarded by the channel flag; fail-open (any error → None → normal turn)."""
        if not channel_workflows_enabled():
            return None
        try:
            # If a confirmation is pending for this session, treat THIS message
            # as the yes/no answer.
            resolved = await resolve_confirmation(prompt, session_id=session_id, store=store)
            if resolved.outcome == "executed":
                return resolved
            if resolved.outcome == "declined":
                return resolved
            # resolved.outcome == "not_pending" → no pending; treat as new inbound.
            rates = dict(
                per_child_token_estimate=_DEFAULT_PER_CHILD_TOKENS,
                model_microcents_per_1k=_DEFAULT_MODEL_MICROCENTS_PER_1K,
            )
            stripped = prompt.strip()
            if stripped.startswith("/research"):
                query = stripped[len("/research"):].strip() or stripped
                out = start_research(query, session_id=session_id, store=store, **rates)
            else:
                label = await classifier.aclassify(prompt)
                out = route_inbound(
                    prompt,
                    session_id=session_id,
                    classifier=FixedClassifier(label),
                    store=store,
                    **rates,
                )
            if out.outcome == "awaiting_confirmation":
                return out
            return None  # normal_llm → fall through to the streaming turn
        except Exception:
            return None  # FAIL-OPEN — never break normal chat

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
        wf = await _maybe_handle_workflow(prompt, session_id)
        if wf is not None:
            return StreamingResponse(
                _drive_workflow_result_stream(
                    wf,
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
            with memory_mode_request_scope(request.headers):
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
