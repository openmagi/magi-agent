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
import hmac
import json
import os
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from magi_agent.engine.contracts import EngineResult, Terminal
from magi_agent.cli.protocol import ControlResponse
from magi_agent.ops.health import _truthy_env
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.memory_mode_context import (
    current_memory_mode,
    memory_mode_request_scope,
)
from magi_agent.config.env import (
    is_hosted_full_access_enabled,
    is_hosted_streaming_serve_enabled,
)
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
from magi_agent.transport.local_turn_pump import drive_detached_local_stream
from magi_agent.transport.local_turn_store import LOCAL_TURN_STORE
from magi_agent.transport.streaming_chat import frame_for_event, frame_for_terminal
from magi_agent.transport.streaming_driver import drive_streaming_chat
from magi_agent.transport.streaming_sink import build_streaming_prompt_sink
from magi_agent.runtime.public_events import turn_phase_event
# PR-H: main-turn finalize-path TRACE helpers (gated on the existing
# MAGI_CHILD_RUNNER_EMPTY_DEBUG env; default-OFF; helpers swallow their own
# faults so logging can never break a turn). The two helpers below stamp
# handler entry and the END of the streaming response body so the operator
# can see WHICH layer's finalize ate the result on a silent-empty turn.
from magi_agent.runtime.child_runner_live import (
    _maybe_log_trace_chat_turn_handler_exit,
    _maybe_log_trace_chat_turn_start,
)

__all__ = [
    "register_streaming_chat_routes",
    "_streaming_chat_enabled",
    "_extract_prompt_text",
    "_local_full_access",
]

_SELECTED_FULL_TOOLHOST_TURN_ID = "turn-gate5b-full-toolhost"
_SELECTED_STREAM_HEARTBEAT_INTERVAL_SECONDS = 5.0

# Maximum allowed size (bytes) of the JSON-serialised ``response`` dict in
# a control-response request.  Protects against oversized payloads.
_MAX_CONTROL_RESPONSE_BYTES = 8192
_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


async def _wrap_handler_exit_trace(
    inner: AsyncIterator[bytes],
    *,
    session_id: str,
    turn_id: str,
) -> AsyncIterator[bytes]:
    """PR-H: emit a ``[chat_routes.trace] turn_handler_exit`` line when the
    streaming body finishes (normal exhaustion OR exception propagation).

    The route handler returns a :class:`StreamingResponse` immediately; the
    real "did the handler finalize cleanly?" moment is when the SSE body
    iterator stops. This wrapper sits over that iterator so the exit stamp
    fires at the actual finish, with ``final_text_len`` set to the total
    bytes streamed to the client. Default-OFF gating lives inside the trace
    helper; the wrapper itself is byte-identical when the flag is unset.
    """
    final_text_len = 0
    exception_cls: type | None = None
    try:
        async for chunk in inner:
            try:
                final_text_len += len(chunk)
            except Exception:  # noqa: BLE001 - never let counting break the stream.
                pass
            yield chunk
    except Exception as exc:  # noqa: BLE001 - re-raised below, captured for trace.
        exception_cls = exc.__class__
        raise
    finally:
        _maybe_log_trace_chat_turn_handler_exit(
            os.environ,
            session_id=session_id,
            turn_id=turn_id,
            final_text_len=final_text_len,
            exception=exception_cls,
        )


def _streaming_response(content: AsyncIterator[bytes]) -> StreamingResponse:
    return StreamingResponse(
        content,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------


def _streaming_chat_enabled() -> bool:
    """Return True when ``MAGI_STREAMING_CHAT`` is truthy. Evaluated per-call.

    I-1: routed through the typed flag registry. The ``FlagSpec`` is
    registered default-OFF so the previous ``_truthy_env`` semantics
    (missing/empty → False) survive byte-identically.
    """
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool("MAGI_STREAMING_CHAT")


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


def _normalize_model_provider(model: str | None) -> str | None:
    """Qualify a bare model id with its inferred provider for the local engine.

    A chat picker may send a bare model id (e.g. ``gemini-3.5-flash``). The local
    headless engine defaults an unqualified id to anthropic, so a non-anthropic
    bare id (gemini / openai / fireworks) routes to the wrong provider and 404s.
    Map the bare id to its provider family via ``_infer_provider_for_model`` and
    prefix it (``gemini/gemini-3.5-flash``). Anthropic ids stay bare (the
    prompt-cache model path expects the bare ``claude-*`` id); already-qualified
    ids (containing ``/``) and unknown families pass through unchanged.
    """
    if not model or "/" in model:
        return model
    try:
        from magi_agent.engine.providers import _infer_provider_for_model

        provider = _infer_provider_for_model(model)
    except Exception:  # noqa: BLE001 -- never break the build over inference.
        provider = None
    if provider and provider != "anthropic":
        return f"{provider}/{model}"
    return model


def _hosted_full_access(runtime: object) -> bool:
    """Return True when the operator opted this hosted deployment into full access.

    Default OFF (``MAGI_HOSTED_FULL_ACCESS``). When ON, a hosted bot that reaches
    the local headless engine path runs with ``bypassPermissions`` like the
    loopback local owner, so mutating/execution tools (Bash, SpawnAgent,
    FileWrite) run without an interactive approver instead of being safe-denied
    headless. Intended for single-tenant / trusted self-host bots whose gateway
    token is the sole access boundary.
    """
    return is_hosted_full_access_enabled()


def _qualified_litellm_model(model: str | None) -> str | None:
    """Resolve the provider-qualified litellm model id (e.g. ``fireworks_ai/...``).

    litellm prices by a provider-qualified id; the bare ``config.model`` (e.g.
    ``kimi-k2p6``) often is not in its price map. Re-resolve via the same
    ``resolve_provider_config`` the runner uses so pricing matches the call.
    Falls back to the bare model on any failure (litellm still infers some bare
    ids, e.g. ``claude-*``).
    """
    if not model:
        return None
    try:
        from magi_agent.engine.providers import resolve_provider_config

        cfg = resolve_provider_config(model_override=model)
    except Exception:  # noqa: BLE001 -- resolution is best-effort
        return model
    if cfg is None:
        return model
    return getattr(cfg, "litellm_model", None) or model


def _usage_price_overrides() -> tuple[float | None, float | None]:
    """Read the operator's USD-per-1M-token override rates (in, out), or (None, None)."""
    from magi_agent.config.flags import flag_str

    def _parse(raw: str | None) -> float | None:
        if not raw or not raw.strip():
            return None
        try:
            value = float(raw.strip())
        except ValueError:
            return None
        return value if value >= 0 else None

    return (
        _parse(flag_str("MAGI_USAGE_PRICE_IN_PER_MTOK")),
        _parse(flag_str("MAGI_USAGE_PRICE_OUT_PER_MTOK")),
    )


def _persist_local_turn_usage(
    runtime: object,
    session_id: str,
    terminal: object,
) -> None:
    """Persist one local turn's token/cost usage for the Usage dashboard.

    The local ``magi-agent serve`` engine path is the only writer of the
    per-session usage the ``/v1/app/runtime`` reader surfaces; nothing else
    persists it (the ADK session service is wired without a store). Best-effort:
    a bad model name, an unwritable workspace, or a missing optional dependency
    must never affect the live turn -- the caller already swallows exceptions, and
    this body adds its own guards.
    """
    usage = getattr(terminal, "usage", None) or {}
    tokens_in = int(usage.get("input_tokens") or 0)
    tokens_out = int(usage.get("output_tokens") or 0)
    tokens_cache_read = int(usage.get("cache_read_tokens") or 0)
    if tokens_in <= 0 and tokens_out <= 0:
        return  # nothing the model actually consumed this turn

    config = getattr(runtime, "config", None)
    model = getattr(config, "model", None)
    user_id = getattr(config, "user_id", None) or "local"

    from magi_agent.runtime.usage_cost import compute_cost_usd
    from magi_agent.storage.session_store import (
        SessionSqliteStore,
        SessionStoreConfig,
    )
    from magi_agent.transport.app_api import _workspace_root

    price_in, price_out = _usage_price_overrides()
    cost_usd = compute_cost_usd(
        _qualified_litellm_model(model),
        usage,
        price_in_per_mtok=price_in,
        price_out_per_mtok=price_out,
    )
    store = SessionSqliteStore(
        SessionStoreConfig(enabled=True),
        workspace_root=str(_workspace_root()),
    )
    try:
        # Ensure the parent session row exists (session_metadata FK) and refresh
        # its last-activity timestamp; the local engine path has no channel, so
        # the dashboard falls back to the session key for the label.
        store.save_sync(session_id, "magi", user_id, {})
        store.update_metadata_sync(
            session_id,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_cache_read=tokens_cache_read,
            cost_usd=cost_usd,
            increment_turn=True,
        )
    finally:
        store.close()


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
    """Extract the LATEST user-text content from ``body["messages"]``.

    The dashboard sends the full OpenAI-compat conversation history each turn
    (``messages = [user_1, assistant_1, user_2, assistant_2, ..., user_N]``).
    The new-turn prompt must be ONLY ``user_N``. Prior turns already live as
    ADK session events, so joining every prior user message into the prompt
    duplicates context AND lets a long prior request drown out a short new one.

    Queue masquerade 2nd-pass (PR-I, after #686). Pre-fix this function joined
    EVERY user-authored block across the whole history with newlines. When the
    prior turn aborted with text (e.g. ``missing_runtime_receipt`` after a
    long Tesla 10-K request) and the user typed a short fresh message ("hi"),
    the prompt became ``"<long Tesla 10-K request>\\nhi"`` and the runtime
    kept executing the prior task instead of greeting back. Walking from the
    end of ``messages`` and returning the first user message preserves the
    OpenAI-compat surface contract while killing the cross-turn join.

    Within the single latest user message, multimodal text blocks
    (``content`` is a list of ``{"type": "text", "text": ...}`` blocks) still
    concatenate with newlines. That path is the per-turn multimodal contract
    and was never the bug. A message without a role is still treated as user
    (bare ``{"content": "..."}`` payload compat).
    """
    if not isinstance(body, Mapping):
        return ""
    messages = body.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return ""
    # Walk newest-first; return the first user-authored message's content.
    # Assistant / system entries (and the bot's own "코드 작성/편집" self-intro)
    # stay excluded for the same coding-evidence-gate reason as the legacy
    # filter (the assistant-text exclusion is preserved).
    for message in reversed(list(messages)):
        if not isinstance(message, Mapping):
            continue
        role = message.get("role")
        if role is not None and role != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            stripped = content.strip()
            if stripped:
                return stripped
            continue
        if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
            block_parts: list[str] = []
            for block in content:
                if isinstance(block, Mapping):
                    text = block.get("text")
                    if isinstance(text, str):
                        block_parts.append(text)
            joined = "\n".join(part.strip() for part in block_parts if part.strip())
            if joined:
                return joined
            continue
    return ""


def _python_chat_route_on() -> bool:
    """True when the hosted python chat-route authority gate env flag is on.

    I-4: routed through the typed flag registry so this security-adjacent
    gate has a single discoverable definition instead of the
    "off"/"on"-string inline read.
    """

    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool("CORE_AGENT_PYTHON_CHAT_ROUTE")


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
    SSE bytes are sent -- chat-proxy uses that shape for typescript-authority
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


#: Per-token delta frames are never deduplicated by content key (PR-D4 /
#: N-40). Their replay is already guarded by the live_text_emitted /
#: live_thinking_emitted flags, so keying them would json.dumps(sort_keys=True)
#: once per token for a key that is provably never looked up.
_UNKEYED_DELTA_EVENT_TYPES = frozenset({"text_delta", "thinking_delta"})


def _selected_stream_event_key(payload: Mapping[str, object]) -> str | None:
    """Dedup key for a selected-stream public event, or None when the event
    must not be keyed (PR-D4 / N-40).

    Delta frames return None (dedup handled by the live_*_emitted replay
    guards). Heartbeat/pending llm_progress frames carry per-beat
    iter/elapsedMs, so they are keyed by shape (type + stage), not content,
    keeping ``emitted_event_keys`` O(distinct shapes) instead of O(beats)."""
    event_type = payload.get("type")
    if event_type in _UNKEYED_DELTA_EVENT_TYPES:
        return None
    if event_type == "llm_progress":
        return f"llm_progress:{payload.get('stage', '')}"
    return json.dumps(dict(payload), sort_keys=True, default=str)


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
    live_thinking_emitted = False

    def _enqueue_public_event(payload: Mapping[str, object]) -> None:
        nonlocal live_text_emitted, live_thinking_emitted
        event = _selected_stream_public_event_for_turn(payload, turn_id=turn_id)
        event_type = event.get("type")
        if event_type == "text_delta":
            live_text_emitted = True
        elif event_type == "thinking_delta":
            live_thinking_emitted = True
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
            key = _selected_stream_event_key(public_event)
            if key is not None:
                emitted_event_keys.add(key)
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
                key = _selected_stream_event_key(public_event)
                if key is not None:
                    emitted_event_keys.add(key)
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
                key = _selected_stream_event_key(public_event)
                if key is not None:
                    emitted_event_keys.add(key)
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
                event_type = public_event.get("type")
                if live_text_emitted and event_type == "text_delta":
                    continue
                # Symmetric with text_delta (PR-D4 / N-40): once any live
                # thinking_delta was emitted, skip every replayed one so
                # differing chunk boundaries never replay duplicate thinking
                # text.
                if live_thinking_emitted and event_type == "thinking_delta":
                    continue
                event_key = _selected_stream_event_key(public_event)
                if event_key is not None:
                    if event_key in emitted_event_keys:
                        continue
                    emitted_event_keys.add(event_key)
                if event_type == "text_delta":
                    live_text_emitted = True
                elif event_type == "thinking_delta":
                    live_thinking_emitted = True
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


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_streaming_chat_routes(
    app: FastAPI,
    runtime: object,
    *,
    engine_builder: Callable[[str, object, str | None], tuple[object, object]]
    | None = None,
) -> None:
    """Mount the three streaming-chat routes on *app*.

    Parameters
    ----------
    app:
        The FastAPI application instance.
    runtime:
        The ``OpenMagiRuntime`` (or compatible duck-typed stub). Used for
        ``runtime.config.gateway_token`` and as the fallback for
        ``runtime.config.model`` when a request carries no ``model`` override
        (see ``engine_builder``).
    engine_builder:
        Optional factory ``(session_id: str, sink, model_override) -> (engine, gate)``.
        ``model_override`` is the model selected by the dashboard (the
        ``/v1/chat/stream`` body ``model`` field); it wins over
        ``runtime.config.model`` and falls back to it when ``None``. When the
        factory is omitted the default uses
        :func:`~magi_agent.cli.wiring.build_headless_runtime` with
        ``permission_mode="default"`` and ``MAGI_AGENT_WORKSPACE`` or
        ``os.getcwd()`` as ``cwd``.
    """

    def _default_engine_builder(
        session_id: str, sink: object, model_override: str | None
    ) -> tuple[object, object]:
        # NOTE: build_headless_runtime(prompt_sink=...) wires a RulesPermissionGate
        # backed by an EMPTY RulesEngine, so the engine's `updated_input`
        # re-validation (which would re-check a rewritten tool's args against deny
        # rules) is a no-op for this streaming surface -- a control-response
        # `updated_input` is applied verbatim. This is acceptable because the
        # control-response comes from the gateway-token holder (full bot access).
        # If multi-user / shared-token control-responses are ever introduced here,
        # seed baseline deny rules so rewritten args are re-validated.
        from magi_agent.cli.wiring import (  # lazy to avoid cold-start cost
            build_headless_runtime,
            local_runner_policy_routing_enabled_from_env,
        )

        # The dashboard-selected model (when present) wins over the process
        # serve config; ``None``/absent falls back to ``runtime.config.model``
        # (byte-identical to the pre-J-1 behavior when no override is sent).
        model = model_override or getattr(
            getattr(runtime, "config", None), "model", None
        )
        model = _normalize_model_provider(model)
        # I-4: routed through the typed flag registry.
        from magi_agent.config.flags import flag_str  # noqa: PLC0415

        cwd = flag_str("MAGI_AGENT_WORKSPACE") or os.getcwd()
        full_access = _local_full_access(runtime) or _hosted_full_access(runtime)
        permission_mode = "bypassPermissions" if full_access else "default"
        # An active mode may TIGHTEN (never loosen) the permission posture.
        from magi_agent.customize.modes import (  # noqa: PLC0415
            active_permission_mode as _active_permission_mode,
            capped_permission_mode as _capped_permission_mode,
        )

        permission_mode = _capped_permission_mode(
            _active_permission_mode(), permission_mode
        )
        runner_policy_routing_enabled = (
            local_runner_policy_routing_enabled_from_env() if full_access else None
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

    def _auth_ok(request: Request) -> bool:
        token = getattr(getattr(runtime, "config", None), "gateway_token", None)
        if not token:  # None or empty string → refuse all requests
            return False
        # A-9: constant-time compare to avoid a timing side-channel on the token.
        presented = request.headers.get("authorization", "")
        return hmac.compare_digest(presented, f"Bearer {token}")

    # ------------------------------------------------------------------
    # Route 1 -- POST /v1/chat/stream
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
        # PR-H: stamp handler entry. Pairs with the exit stamp wrapped
        # around the streaming body below. Default-OFF (no-op unless
        # MAGI_CHILD_RUNNER_EMPTY_DEBUG is truthy).
        _maybe_log_trace_chat_turn_start(
            os.environ, session_id=session_id, turn_id=turn_id
        )
        prompt = _extract_prompt_text(body)
        # J-1: the dashboard sends the selected model in the body. Thread it into
        # the local headless builder as an override-with-fallback. The web client
        # default sentinel ``"auto"`` means "no override" (fall back to the serve
        # config), keeping behavior byte-identical for default callers.
        model_override = _body_string(body, "model", "") or None
        if model_override == "auto":
            model_override = None

        if hosted_serve:
            # Hosted serving mode (08-PR3, default-OFF): the selected gate5b
            # path is the ONLY serving path. Gate-inactive requests get the
            # completions-equivalent fallback JSON; they NEVER fall through to
            # the local headless engine (gate/counter/receipt bypass surface).
            refusal = _hosted_serve_gate_refusal(runtime, body, request)
            if refusal is not None:
                return refusal
            return _streaming_response(
                _wrap_handler_exit_trace(
                    _drive_selected_gate5b_stream(
                        runtime,
                        body,
                        request,
                        session_id=session_id,
                        turn_id=turn_id,
                    ),
                    session_id=session_id,
                    turn_id=turn_id,
                )
            )

        if _selected_gate5b_stream_active(runtime):
            return _streaming_response(
                _wrap_handler_exit_trace(
                    _drive_selected_gate5b_stream(
                        runtime,
                        body,
                        request,
                        session_id=session_id,
                        turn_id=turn_id,
                    ),
                    session_id=session_id,
                    turn_id=turn_id,
                )
            )

        queue: asyncio.Queue[object] = asyncio.Queue()
        sink = build_streaming_prompt_sink(queue, turn_id=turn_id)
        # The engine build runs synchronously BEFORE the StreamingResponse is
        # created, so a build-time failure must not escape as a bare 500 mid-
        # contract. No SSE bytes have been sent yet, so returning a JSON 500
        # here is safe (the client has not started consuming an event stream).
        try:
            with memory_mode_request_scope(request.headers):
                engine, gate = builder(session_id, sink, model_override)
        except Exception:
            return JSONResponse(status_code=500, content={"error": "engine_build_failed"})
        cancel = asyncio.Event()

        def _usage_recorder(terminal: object) -> None:
            _persist_local_turn_usage(runtime, session_id, terminal)

        # LOCAL streaming branch only (hosted gate5b returned above). Detach the
        # turn into a background pump so a browser refresh / disconnect no longer
        # tears down the turn via ``drive_streaming_chat``'s finally-cancel. The
        # pump fans SSE frames to the subscriber AND a snapshot reducer, keyed by
        # the reset-aware session key in LOCAL_TURN_STORE, so the two refresh
        # endpoints (``/v1/chat/active-snapshot`` + ``/v1/chat/channel-messages``)
        # can rehydrate the in-flight or just-finished turn. See
        # ``transport.local_turn_pump``. There is no separate flag: the local serve
        # profile force-enables ``MAGI_STREAMING_CHAT`` so this branch is the live
        # local chat path.
        undetached = drive_streaming_chat(
            engine,
            gate,
            {"prompt": prompt, "session_id": session_id, "turn_id": turn_id},
            cancel=cancel,
            queue=queue,
            sink=sink,
            registry=ACTIVE_TURNS,
            session_id=session_id,
            turn_id=turn_id,
            usage_recorder=_usage_recorder,
        )
        return _streaming_response(
            _wrap_handler_exit_trace(
                drive_detached_local_stream(
                    undetached,
                    session_id=session_id,
                    turn_id=turn_id,
                    cancel=cancel,
                ),
                session_id=session_id,
                turn_id=turn_id,
            )
        )

    # ------------------------------------------------------------------
    # Route 2 -- POST /v1/chat/control-response
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

        turn_id = _body_string(body, "turnId", "")
        if turn_id:
            turn = ACTIVE_TURNS.get(session_id, turn_id)
        else:
            resolved = ACTIVE_TURNS.get_single(session_id)
            if resolved == "ambiguous":
                return JSONResponse(
                    status_code=409,
                    content={"error": "ambiguous_active_turn"},
                )
            turn = resolved
        if turn is None:
            return JSONResponse(
                status_code=404,
                content={"error": "no_active_turn"},
            )

        turn.sink.deliver(ControlResponse(request_id=request_id, response=dict(response_dict)))
        return JSONResponse(
            status_code=200,
            content={"status": "delivered", "request_id": request_id},
        )

    # ------------------------------------------------------------------
    # Route 3 -- POST /v1/chat/cancel
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

        turn_id = _body_string(body, "turnId", "")
        if turn_id:
            turn = ACTIVE_TURNS.get(session_id, turn_id)
        else:
            resolved = ACTIVE_TURNS.get_single(session_id)
            if resolved == "ambiguous":
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "ambiguous_active_turn",
                        "activeTurnCompatible": False,
                        "handoffRequested": handoff,
                    },
                )
            turn = resolved
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

    # ------------------------------------------------------------------
    # Route 4 -- GET /v1/chat/active-snapshot?sessionId=
    #
    # Refresh/reconnect: return the LIVE snapshot of the in-flight (detached)
    # turn for a session, or a detached background-work snapshot after the
    # parent turn ended. Absorbs the chat-proxy ``active-snapshot`` role for the
    # LOCAL streaming branch. Returns ``{"snapshot": null}`` for every
    # "nothing to resume" path (no turn, TTL expired) so the browser reducer
    # falls back to committed history. Never a hard error on missing data.
    # ------------------------------------------------------------------
    @app.get("/v1/chat/active-snapshot", response_model=None)
    async def streaming_chat_active_snapshot(request: Request) -> JSONResponse:
        if not _auth_ok(request):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        if not _streaming_chat_enabled():
            return JSONResponse(
                status_code=503,
                content={"error": "streaming_chat_disabled"},
            )
        session_id = request.query_params.get("sessionId", "").strip()
        if not session_id:
            return JSONResponse(status_code=400, content={"error": "missing_session_id"})
        snapshot = LOCAL_TURN_STORE.active_snapshot(session_id)
        return JSONResponse(status_code=200, content={"snapshot": snapshot})

    # ------------------------------------------------------------------
    # Route 5 -- GET /v1/chat/channel-messages?sessionId=
    #
    # Refresh/reconnect fallback: return the just-committed assistant
    # message(s) for a session whose turn finished while the browser was away
    # (the completed-turn record held under a generous TTL). Absorbs the
    # chat-proxy ``channel-messages`` role for the LOCAL streaming branch.
    # Errored/aborted turns deliver no content. Always returns
    # ``{"messages": [...]}`` (possibly empty); never a hard error.
    # ------------------------------------------------------------------
    @app.get("/v1/chat/channel-messages", response_model=None)
    async def streaming_chat_channel_messages(request: Request) -> JSONResponse:
        if not _auth_ok(request):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        if not _streaming_chat_enabled():
            return JSONResponse(
                status_code=503,
                content={"error": "streaming_chat_disabled"},
            )
        session_id = request.query_params.get("sessionId", "").strip()
        if not session_id:
            return JSONResponse(status_code=400, content={"error": "missing_session_id"})
        messages = LOCAL_TURN_STORE.completed_messages(session_id)
        return JSONResponse(status_code=200, content={"messages": messages})
