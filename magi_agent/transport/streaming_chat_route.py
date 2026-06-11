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
import hashlib
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
from magi_agent.channels.taskkind_classifier import FixedClassifier
from magi_agent.channels.workflow_classifier_live import (
    build_live_classifier_if_configured,
)
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
from magi_agent.config.env import is_hosted_streaming_serve_enabled
from magi_agent.gates.gate5b_full_toolhost import Gate5BFullToolHostConfig
from magi_agent.transport.active_turn import ACTIVE_TURNS, ActiveTurn
# NOTE: the underscore-named helpers below are owned by the decomposed chat
# modules (chat_shared / chat_routes); they are imported via the
# ``transport.chat`` re-export shim on purpose so this module does not couple
# to the in-flight chat_routes decomposition (08-PR2).
from magi_agent.transport.chat import (
    _canary_gate_error,
    _fallback_response,
    _gate2_sandbox_canary_config,
    _reason_for_gate_error,
    _route_config,
    _run_gate2_sandbox_workspace_canary_chat,
    gate5b_user_visible_chat_gate_active,
    run_gate5b_user_visible_chat_response,
)
from magi_agent.transport.streaming_chat import frame_for_event, frame_for_terminal
from magi_agent.transport.streaming_driver import drive_streaming_chat
from magi_agent.transport.streaming_sink import build_streaming_prompt_sink
from magi_agent.runtime.public_events import turn_phase_event

__all__ = [
    "register_streaming_chat_routes",
    "_streaming_chat_enabled",
    "_extract_prompt_text",
    "_local_full_access",
]

_DEFAULT_PER_CHILD_TOKENS = 8000
_DEFAULT_MODEL_MICROCENTS_PER_1K = 120
_SELECTED_FULL_TOOLHOST_TURN_ID = "turn-gate5b-full-toolhost"
_SELECTED_STREAM_HEARTBEAT_INTERVAL_SECONDS = 5.0
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
        # Only user-authored text. Joining assistant/system text used to let the
        # bot's own "코드 작성/편집" self-introduction trip the coding-evidence
        # gate's prompt classifier on every later turn of the session. A message
        # without a role is treated as user for bare {content} payload compat.
        role = message.get("role")
        if role is not None and role != "user":
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


def _python_chat_route_on() -> bool:
    """True when the hosted python chat-route authority gate env flag is on."""
    return os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() == "on"


def _selected_gate5b_stream_active(runtime: object) -> bool:
    if not _python_chat_route_on():
        return False
    try:
        return gate5b_user_visible_chat_gate_active(runtime)
    except Exception:
        return False


def _hosted_streaming_serve_active() -> bool:
    """Return True when the 08-PR3 hosted streaming-serve mode is ON.

    Evaluated per-call (mirrors ``_streaming_chat_enabled``). Default OFF.
    """
    return is_hosted_streaming_serve_enabled()


def _hosted_serve_chat_route_disabled_response(runtime: object) -> JSONResponse:
    config = getattr(runtime, "config", None)
    return JSONResponse(
        status_code=503,
        content={
            "error": "chat_route_disabled",
            "runtime": getattr(config, "runtime", None),
            "runtimeEngine": getattr(config, "runtime_engine", None),
        },
    )


def _hosted_serve_malformed_json_refusal(runtime: object) -> JSONResponse:
    """Completions-equivalent response for an unparsable hosted request body.

    Mirrors the ``/v1/chat/completions`` ordering: chat-route gate (checked
    before the body is ever parsed) → gate2 parse branch (400) → canary route
    gate (503 ``python_disabled``) → 400 ``python_error``/``malformed_json``.
    """
    if not _python_chat_route_on():
        return _hosted_serve_chat_route_disabled_response(runtime)
    gate2_config = _gate2_sandbox_canary_config(runtime)
    if not gate2_config.enabled and not _route_config(runtime).enabled:
        return _fallback_response(
            status_code=503,
            status="python_disabled",
            reason="canary_gate_disabled",
            runtime=runtime,
        )
    return _fallback_response(
        status_code=400,
        status="python_error",
        reason="malformed_json",
        runtime=runtime,
    )


def _hosted_serve_gate_refusal(
    runtime: object,
    body: object,
    request: Request,
) -> JSONResponse | None:
    """Completions-equivalent refusal/dispatch for hosted stream serving.

    Mirrors the ``/v1/chat/completions`` wrapper + ``run_gate5b_user_visible_
    chat_response`` entry gates so a hosted caller gets the exact same JSON
    failure surface (status / fallbackStatus / responseAuthority) BEFORE any
    SSE bytes are sent — chat-proxy uses that shape for typescript-authority
    fallback. Gate2 sandbox-workspace canary payloads are dispatched to the
    same gate2 chat boundary as completions (JSON response, not SSE). Returns
    ``None`` only when the selected gate5b canary gate is fully active. Never
    falls through to the local headless engine.
    """
    if not _python_chat_route_on():
        return _hosted_serve_chat_route_disabled_response(runtime)
    gate2_config = _gate2_sandbox_canary_config(runtime)
    if (
        gate2_config.enabled
        and isinstance(body, Mapping)
        and body.get("gate") == "gate2_sandbox_workspace_canary"
    ):
        return _run_gate2_sandbox_workspace_canary_chat(
            runtime,
            gate2_config,
            body,
            request=request,
        )
    route_config = _route_config(runtime)
    if not route_config.enabled:
        return _fallback_response(
            status_code=503,
            status="python_disabled",
            reason="canary_gate_disabled",
            runtime=runtime,
        )
    gate_error = _canary_gate_error(runtime, route_config)
    if gate_error is not None:
        status_code = 409 if gate_error == "invalid_authority" else 503
        return _fallback_response(
            status_code=status_code,
            status=gate_error,
            reason=_reason_for_gate_error(gate_error),
            runtime=runtime,
        )
    return None


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


def _runtime_scope_digest(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _selected_full_toolhost_stream_start_events(
    runtime: object,
    *,
    turn_id: str,
) -> tuple[Mapping[str, object], ...]:
    """Return immediate Work-panel events for the selected full-toolhost stream."""
    try:
        full_toolhost_config = getattr(runtime, "gate5b_full_toolhost_config", None)
        if not isinstance(full_toolhost_config, Gate5BFullToolHostConfig):
            return ()
        route_config = _route_config(runtime)
        runtime_config = getattr(runtime, "config", None)
        environment = route_config.environment or "local"
        if (
            not full_toolhost_config.enabled
            or full_toolhost_config.kill_switch_enabled
            or not full_toolhost_config.route_attachment_enabled
            or full_toolhost_config.max_tool_calls_per_turn <= 0
            or full_toolhost_config.selected_bot_digest
            != _runtime_scope_digest(getattr(runtime_config, "bot_id", None))
            or full_toolhost_config.selected_owner_digest
            != _runtime_scope_digest(getattr(runtime_config, "user_id", None))
            or full_toolhost_config.environment != environment
            or environment not in full_toolhost_config.environment_allowlist
        ):
            return ()
    except Exception:
        return ()
    return (
        turn_phase_event(turn_id=turn_id, phase="executing"),
        {
            "type": "llm_progress",
            "turnId": turn_id,
            "stage": "started",
            "label": "Running Python ADK",
            "detail": "Selected first-party toolhost active",
        },
    )


def _selected_stream_public_event_for_turn(
    payload: Mapping[str, object],
    *,
    turn_id: str,
) -> dict[str, object]:
    event = dict(payload)
    if (
        event.get("type") in {"turn_phase", "llm_progress", "heartbeat"}
        or event.get("turnId") == _SELECTED_FULL_TOOLHOST_TURN_ID
    ):
        event["turnId"] = turn_id
    return event


def _selected_stream_pending_events(
    *,
    turn_id: str,
    heartbeat_iter: int,
    elapsed_ms: int,
) -> tuple[Mapping[str, object], ...]:
    return (
        {
            "type": "heartbeat",
            "turnId": turn_id,
            "iter": heartbeat_iter,
            "elapsedMs": elapsed_ms,
        },
        {
            "type": "llm_progress",
            "turnId": turn_id,
            "stage": "waiting",
            "label": "Running Python ADK",
            "detail": "Waiting for selected model or tool progress",
            "iter": heartbeat_iter,
            "elapsedMs": elapsed_ms,
        },
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
        event = _selected_stream_public_event_for_turn(payload, turn_id=turn_id)
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
        for public_event in _selected_full_toolhost_stream_start_events(
            runtime,
            turn_id=turn_id,
        ):
            emitted_event_keys.add(_event_key(public_event))
            frame = _runtime_event_frame(public_event, turn_id=turn_id)
            if frame is not None:
                yield frame
        loop = asyncio.get_running_loop()
        stream_started_at = loop.time()
        heartbeat_iter = 0
        heartbeat_interval = max(
            0.001,
            float(_SELECTED_STREAM_HEARTBEAT_INTERVAL_SECONDS),
        )
        while True:
            if response_task.done() and live_events.empty():
                break
            next_event_task = asyncio.create_task(live_events.get())
            done, _pending = await asyncio.wait(
                {response_task, next_event_task},
                timeout=None if response_task.done() else heartbeat_interval,
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
            if response_task in done or response_task.done():
                continue
            heartbeat_iter += 1
            elapsed_ms = int((loop.time() - stream_started_at) * 1000)
            for public_event in _selected_stream_pending_events(
                turn_id=turn_id,
                heartbeat_iter=heartbeat_iter,
                elapsed_ms=elapsed_ms,
            ):
                emitted_event_keys.add(_event_key(public_event))
                frame = _runtime_event_frame(public_event, turn_id=turn_id)
                if frame is not None:
                    yield frame
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
                public_event = _selected_stream_public_event_for_turn(
                    public_event,
                    turn_id=turn_id,
                )
                if live_text_emitted and public_event.get("type") == "text_delta":
                    continue
                event_key = _event_key(public_event)
                if event_key in emitted_event_keys:
                    continue
                emitted_event_keys.add(event_key)
                if public_event.get("type") == "text_delta":
                    live_text_emitted = True
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
        Optional async classifier with ``aclassify(message_text) -> str``. When
        omitted the default is built by
        :func:`~magi_agent.channels.workflow_classifier_live.build_live_classifier_if_configured`:
        model-backed (auto-detect live) when a provider is configured, otherwise
        the inert ``TaskKindClassifier`` (returns ``"general"`` — auto-detect off,
        ``/research`` still works). An injected classifier takes precedence.
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
    # Classifier resolution (C5):
    #   1. An explicitly injected ``eligibility_classifier`` always wins (hosted
    #      deployments / tests inject their own model-backed classifier).
    #   2. Otherwise build one from the local provider config: when a model is
    #      configured the default classifier is model-backed (auto-detect goes
    #      live); when nothing is configured it degrades to the inert
    #      ``TaskKindClassifier`` (``aclassify`` → "general", the explicit
    #      "/research" path still works). No new feature flag — the classifier is
    #      live purely on the presence of a configured model.
    classifier = (
        eligibility_classifier
        if eligibility_classifier is not None
        else build_live_classifier_if_configured()
    )

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

        hosted_serve = _hosted_streaming_serve_active()
        try:
            body = await request.json()
        except Exception:
            if hosted_serve:
                return _hosted_serve_malformed_json_refusal(runtime)
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

        if hosted_serve:
            # Hosted serving mode (08-PR3, default-OFF): the selected gate5b
            # path is the ONLY serving path. Gate-inactive requests get the
            # completions-equivalent fallback JSON; they NEVER fall through to
            # the local headless engine (gate/counter/receipt bypass surface).
            refusal = _hosted_serve_gate_refusal(runtime, body, request)
            if refusal is not None:
                return refusal
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
