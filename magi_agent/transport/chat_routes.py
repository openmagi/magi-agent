"""Chat route registration and the Gate5B user-visible serving engine.

Pure move out of ``magi_agent/transport/chat.py`` (08-PR1). Contains
``register_chat_routes`` (the ``/v1/chat/*`` + internal receipt/preflight
routes), the local ADK chat path (JSON + SSE), the selected Gate5B
user-visible chat response boundary (``run_gate5b_user_visible_chat_response``)
with its live/mocked runner implementations, tool-bundle attachment, gate1a
egress correlation, public-safe runner diagnostics, and the python-ready /
canary authority response builders. Behavior is unchanged; ``transport.chat``
re-exports these names for compatibility.
"""

from __future__ import annotations

import asyncio
import collections
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import datetime, timezone
import inspect
import json
from json import JSONDecodeError
import os
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from magi_agent.config.env import is_egress_gate_enabled
from magi_agent.config.flags import flag_bool, flag_str
from magi_agent.evidence.gate1a_egress_correlation import (
    GATE1A_EGRESS_CORRELATION_MODE,
    GATE1A_EGRESS_TELEMETRY_SOURCE,
    Gate1AEgressCorrelationContext,
)
from magi_agent.evidence.observed_egress import (
    ObservedEgressEvidence,
    get_observed_egress_evidence_provider,
    observed_egress_diagnostics,
)
from magi_agent.gates.gate1a_readonly_tools import (
    GATE1A_FORBIDDEN_TOOL_NAMES,
    Gate1AReadOnlyToolBundle,
    Gate1AReadOnlyToolConfig,
    build_gate1a_readonly_tool_bundle,
)
from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolBundle,
    Gate5BFullToolHostConfig,
    build_gate5b_full_toolhost_bundle,
)
from magi_agent.gates.gate8_readiness import gate8_readiness_health_metadata
from magi_agent.introspection.egress_gate import EgressVerifierStatus
from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
)
from magi_agent.recipes.materializer import RecipeMaterializer
from magi_agent.research.research_first_canary import (
    build_research_first_selected_response,
    research_first_selected_canary_active,
)
from magi_agent.runtime.governed_turn import run_governed_turn
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.runtime.turn_context import TurnContext
from magi_agent.runtime.public_events import (
    tool_end_event,
    tool_progress_event,
    tool_start_event,
    turn_phase_event,
)
from magi_agent.runtime.child_runner_status import child_runner_availability_metadata
from magi_agent.runtime.session_identity import _memory_mode_from_header

if TYPE_CHECKING:
    from magi_agent.runtime.session_identity import MemoryMode
from magi_agent.runtime.user_visible_model_routing import (
    _SAFE_LABEL_RE,
    _safe_label_or_none,
)
from magi_agent.runtime.hosted_runtime import build_hosted_runtime
# The hosted governed serving path (gate5b_serving) owns the runner engine; this
# module couples to the PUBLIC boundary surface only (no private underscore
# symbols). (P5-M1b retired run_gate5b4c3_live_runner_boundary_async.)
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    gate1a_correlated_model_or_label,
)
from magi_agent.shadow.gate5b4c3_runner_input_adapter import (
    build_gate5b4c3_runner_input,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterReservation,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    build_gate5b4c3_shadow_generation_diagnostic,
)
from magi_agent.transport.hosted_engine_result import collect_engine_to_boundary_result
from magi_agent.transport.hosted_turn_context import hosted_request_to_turn_context
from magi_agent.transport.chat_shared import (
    Gate5BUserVisibleChatRouteConfig,
    _RUNNER_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE,
    _bounded_public_text,
    bearer_auth_failed,
    _context_continuity_chat_diagnostic,
    _fallback_response,
    _is_sha256_digest,
    _reason_for_gate_error,
    _route_tool_bundle_full,
    _route_tool_bundle_readonly,
    _route_tool_bundle_ready,
    _safe_label_or_default,
    _sha256_digest,
    _shadow_generation_route_config,
)
from magi_agent.transport.active_turn import ACTIVE_TURNS, ActiveTurn, ActiveTurnClaim
from magi_agent.runtime.session_identity import session_key_from_headers
from magi_agent.transport.egress_critic import _maybe_run_egress_critic_gate
from magi_agent.transport.gate5b_governance import (
    build_gate5b_control_plane_plugins,
    gate5b_governance_enabled,
    gate5b_pre_final_grounding_status,
)
from magi_agent.transport.gate2_sandbox_canary import (
    _gate2_sandbox_canary_config,
    _run_gate2_sandbox_workspace_canary_chat,
)
from magi_agent.transport.generation_request import (
    UserVisibleGenerationRequest,
    _build_user_visible_generation_request,
    build_gate5b_user_visible_canary_runner_request,
    sanitize_gate5b_model_visible_identity_text,
)
from magi_agent.transport.shadow_generations import (
    Gate5B4C3ShadowGenerationRouteConfig,
)
from magi_agent.transport.usage_receipt_emit import (
    emit_runtime_direct_usage_receipt,
    usage_receipt_enabled,
)

# PR-G4 completion: the local ADK chat SSE path + its helpers were pure-moved to
# ``chat_routes_local`` (leaf, depends downward on ``chat_shared`` only). This
# module re-imports every moved name so the historical ``chat_routes`` import
# path (and the ``transport.chat`` re-export shim) keeps yielding the SAME
# objects the leaf owns — no duplicated definitions. (Before this, chat_routes
# still carried verbatim copies of these functions, so the shim exported
# duplicate objects and ``test_transport_chat_shim`` saw two distinct
# ``_local_adk_chat_response`` etc.)
from magi_agent.transport.chat_routes_local import (
    _BACKGROUND_INJECT_CONSUMER_ENV,
    _NoopChatSink,
    _apply_background_inject,
    _background_inject_consumer_enabled,
    _buffer_injection,
    _format_background_inject_block,
    _local_adk_chat_response,
    _local_adk_chat_sse,
    _local_chat_prompt_text,
    _local_runtime_event_delta,
    _resolve_local_learning_live_readiness,
    _sse_data,
    _sse_event,
)

# PR-G4/PR-G5/#1244 completion: the authority helpers + gate5b serving engine
# were pure-moved to the ``chat_authority`` and ``gate5b_serving`` leaves. Re-
# import every moved name so ``chat_routes`` (and the ``transport.chat`` shim)
# expose the SAME objects the leaves own instead of duplicate definitions.
from magi_agent.transport.chat_authority import (
    _FALLBACK_RECEIPT_SCOPE_GATES,
    _FALSE_RESPONSE_AUTHORITY_KEYS,
    _FALSE_RUNTIME_AUTHORITY_KEYS,
    _GATE1A_EGRESS_DISCIPLINE_MODE,
    _GATE1A_MAX_PROVIDER_TUNNELS_PER_MODEL_ATTEMPT,
    _INCOMPLETE_RUNNER_OUTPUT_RE,
    _augment_runner_error_diagnostic,
    _boundary_runner_error_diagnostic,
    _camel_to_snake,
    _canary_gate_error,
    _chat_runner_error_diagnostic,
    _disabled_surface_safety,
    _fallback_only_scope_error,
    _finish_counter_error,
    _gate1a_observed_egress_metadata,
    _gate1a_tooling_metadata,
    _gate5b_full_tooling_metadata,
    _gate8_selected_authority_metadata,
    _public_safe_error_preview_or_none,
    _public_safe_runner_error_diagnostic,
    _public_safe_tool_names,
    _public_safe_traceback_markers,
    _python_canary_authority,
    _python_ready_response,
    _route_tooling_metadata,
    _runner_incomplete_output_reason,
    _surface_safety,
)
from magi_agent.transport.gate5b_serving import (
    _FIRST_PARTY_HARNESS_RECIPE_PACK_IDS,
    _bounded_tuple,
    _build_gate1a_egress_correlation_context,
    _client_disconnected,
    _collect_gate1a_observed_egress_evidence,
    _first_party_harness_families,
    _first_party_harness_metadata,
    _first_party_recipe_pack_ids_from_payload,
    _gate1a_config,
    _gate1a_readonly_tool_bundle,
    _gate1a_workspace_root,
    _gate5b_full_toolhost_bundle,
    _gate5b_full_toolhost_config,
    _gate5b_full_toolhost_public_events,
    _gate5b_full_toolhost_tool_event_id,
    _gate5b_full_toolhost_workspace_root,
    _model_attempt_digest,
    _run_live_chat_runner,
    _run_mocked_chat_runner,
    _schedule_runtime_direct_usage_receipt,
    _swallow_task_result,
    _utc_now_iso,
    gate5b_user_visible_chat_gate_active,
    run_gate5b_user_visible_chat_response,
)


# A-8 (P0.2): the governed-turn funnel no longer hard-codes ``bypassPermissions``
# — its fallback and the child path default to deny/ask. The LOCAL serve path,
# however, is the "maximally capable + YOLO by default" surface (Kevin's local-
# serve stance), so it opts into bypass EXPLICITLY and visibly here, at the call
# site, rather than relying on a silent funnel default. This is the single,
# audited place the local serve YOLO authority is chosen; hard safety denies fire
# regardless of mode.
_LOCAL_SERVE_PERMISSION_MODE = "bypassPermissions"


def _route_config(runtime: OpenMagiRuntime) -> Gate5BUserVisibleChatRouteConfig:
    config = getattr(runtime, "gate5b_user_visible_chat_route_config", None)
    if isinstance(config, Gate5BUserVisibleChatRouteConfig):
        return config
    return Gate5BUserVisibleChatRouteConfig()


def _route_tool_bundle_names(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> list[str]:
    if not _route_tool_bundle_ready(bundle):
        return []
    return list(bundle.exposed_tool_names)


def _route_tool_bundle_mode(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> str:
    if _route_tool_bundle_full(bundle):
        return "gate5b_selected_full_toolhost"
    if _route_tool_bundle_readonly(bundle):
        return "gate1a_readonly_tools"
    return "no_route_tools"


def _local_chat_route_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single decision point for ``MAGI_AGENT_LOCAL_CHAT_ROUTE`` (I-4 follow-up).

    Self-host fallback gate for the local ADK chat route. Delegates to
    :func:`magi_agent.config.flags.flag_bool` so the strict-truthy convention
    (``1``/``true``/``yes``/``on`` after trim+lower — byte-identical to the
    legacy inline check) lives in exactly one place. The historic inline
    default was the literal string ``"off"``; the registry FlagSpec
    ``default=False`` plus ``flag_bool`` semantics yield the same falsey
    resolution for an unset env.
    """
    return flag_bool("MAGI_AGENT_LOCAL_CHAT_ROUTE", env=env)


def _python_chat_route_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Single decision point for ``CORE_AGENT_PYTHON_CHAT_ROUTE`` (I-4).

    Hosted-runtime authority gate for the python chat route. Delegates to
    :func:`magi_agent.config.flags.flag_bool` so the strict-truthy convention
    (``1``/``true``/``yes``/``on`` after trim+lower) lives in exactly one
    place. Replaces 6 inline ``os.environ`` reads of the flag previously
    scattered across this module — the legacy contract accepted only ``on``;
    the new contract is a strict superset that also accepts the other shared
    truthy literals. See ``tests/test_chat_route_flag_registry.py``.
    """
    return flag_bool("CORE_AGENT_PYTHON_CHAT_ROUTE", env=env)


def _local_chat_string(payload: object, key: str, default: str) -> str:
    if isinstance(payload, Mapping):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


# --------------------------------------------------------------------------
# Inject buffer (queue-to-next-turn)
#
# The gate5b live-runner boundary is a one-shot JSONResponse: it threads NO
# cooperative mid-turn seam, so we CANNOT deliver an injected message into a
# turn that is already running. The honest native behaviour is queue-to-next-
# turn: when a turn is in flight we append the injected text to a per-session
# buffer and acknowledge it. Consuming that buffer into the *next* turn's
# prompt is a separate prompt-assembly change (see deferral note in the inject
# route); this module only owns the buffer so the contract — accept + count —
# is real today and a future consumer has a single place to read from.
#
# Process-local, asyncio-loop-only (same constraints as ACTIVE_TURNS). The
# storage moved into ``missions.work_queue.inject_buffer`` so the background-
# task completion sink (in the gateway layer) can write into the same buffer
# without importing this module (which would pull FastAPI into the queue
# layer). This module still owns the chat-side append + consumer entry points.
from magi_agent.missions.work_queue import inject_buffer as _inject_buffer

# Back-compat alias: existing tests (e.g. test_chat_route_contract) reach into
# ``chat_routes._INJECT_BUFFERS`` directly to seed/inspect the buffer. Bind the
# name to the shared module-level dict so those tests keep working without
# importing the new location, and any mutation via either name stays coherent.
_INJECT_BUFFERS: dict[str, list[str]] = _inject_buffer._BUFFERS


def _resolve_session_key(payload: object, request: Request) -> str:
    """Resolve the canonical chat session key across completions/interrupt/inject.

    The chat-proxy sends the session in the interrupt/inject *body* as
    ``sessionKey`` and on ``/v1/chat/completions`` via the session-key *header*;
    both carry the same ``agent:main:app:<channel>`` value, so a turn registered
    from the completions header is found by an interrupt carrying the body key.
    ``sessionId`` body fallback preserves local-dashboard callers.
    """
    explicit = (
        _local_chat_string(payload, "sessionKey", "")
        or _local_chat_string(payload, "sessionId", "")
    )
    if explicit:
        return explicit
    return session_key_from_headers(request.headers) or ""


def register_chat_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        if bearer_auth_failed(request, runtime):
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
            )
        if _local_chat_route_enabled() and not _python_chat_route_enabled():
            try:
                payload = await request.json()
            except (JSONDecodeError, ValueError):
                return JSONResponse(
                    status_code=400,
                    content={"error": "malformed_json"},
                )
            return _local_adk_chat_response(runtime, payload)
        if not _python_chat_route_enabled():
            return JSONResponse(
                status_code=503,
                content={
                    "error": "chat_route_disabled",
                    "runtime": runtime.config.runtime,
                    "runtimeEngine": runtime.config.runtime_engine,
                },
            )
        gate2_config = _gate2_sandbox_canary_config(runtime)
        if gate2_config.enabled:
            try:
                payload = await request.json()
            except (JSONDecodeError, ValueError):
                return _fallback_response(
                    status_code=400,
                    status="python_error",
                    reason="malformed_json",
                    runtime=runtime,
                )
            if (
                isinstance(payload, Mapping)
                and payload.get("gate") == "gate2_sandbox_workspace_canary"
            ):
                return _run_gate2_sandbox_workspace_canary_chat(
                    runtime,
                    gate2_config,
                    payload,
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
        try:
            payload = await request.json()
        except (JSONDecodeError, ValueError):
            # H-36: the outer ``if not route_config.enabled`` guard at the
            # top of this handler already short-circuits when the route is
            # disabled. Re-checking it here was dead defensive scaffolding;
            # malformed JSON unconditionally returns 400 ``malformed_json``.
            return _fallback_response(
                status_code=400,
                status="python_error",
                reason="malformed_json",
                runtime=runtime,
            )
        return await run_gate5b_user_visible_chat_response(
            runtime,
            payload,
            request=request,
        )

    @app.post("/v1/chat/inject")
    async def chat_inject(request: Request) -> JSONResponse:
        if bearer_auth_failed(request, runtime):
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
            )
        if not _python_chat_route_enabled():
            return JSONResponse(
                status_code=503,
                content={
                    "error": "chat_route_disabled",
                    "reason": "python_inject_unsupported",
                    "fallback": "queue_to_completions",
                    "activeTurnCompatible": False,
                    "responseAuthority": "typescript",
                },
            )
        try:
            payload = await request.json()
        except (JSONDecodeError, ValueError):
            payload = {}
        session_id = _resolve_session_key(payload, request)
        turn_id = _local_chat_string(payload, "turnId", "")
        if not session_id:
            turn = None
        elif turn_id:
            turn = ACTIVE_TURNS.get(session_id, turn_id)
        else:
            resolved = ACTIVE_TURNS.get_single(session_id)
            if resolved == "ambiguous":
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "ambiguous_active_turn",
                        "reason": "python_inject_unsupported",
                        "fallback": "queue_to_completions",
                        "activeTurnCompatible": False,
                        "responseAuthority": "typescript",
                    },
                )
            turn = resolved
        if turn is None:
            # No in-flight turn: the caller falls back to queue_to_completions,
            # which is the correct behaviour — there is nothing to inject into.
            return JSONResponse(
                status_code=409,
                content={
                    "error": "no_active_turn",
                    "reason": "python_inject_unsupported",
                    "fallback": "queue_to_completions",
                    "activeTurnCompatible": False,
                    "responseAuthority": "typescript",
                },
            )
        # A turn IS running. The gate5b live-runner boundary is a one-shot
        # JSONResponse with no cooperative mid-turn seam, so we honestly
        # queue-to-next-turn rather than claim mid-turn delivery. Buffer the
        # text and acknowledge with a count the chat-proxy can surface.
        text = (
            _local_chat_string(payload, "text", "")
            or _local_chat_string(payload, "message", "")
            or _local_chat_string(payload, "content", "")
        )
        injection_id = f"{session_id}:inj:{int(time.time() * 1000)}"
        queued_count = _buffer_injection(session_id, text)
        return JSONResponse(
            status_code=200,
            content={
                "status": "queued",
                "injectionId": injection_id,
                "queuedCount": queued_count,
                "delivery": "next_turn",
                "activeTurnCompatible": True,
                "responseAuthority": "python",
            },
        )

    @app.post("/v1/chat/interrupt")
    async def chat_interrupt(request: Request) -> JSONResponse:
        if bearer_auth_failed(request, runtime):
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
            )
        if not _python_chat_route_enabled():
            return JSONResponse(
                status_code=503,
                content={
                    "error": "chat_route_disabled",
                    "reason": "python_interrupt_unsupported",
                    "fallback": "typescript_interrupt_required",
                    "activeTurnCompatible": False,
                    "handoffRequested": False,
                    "gateStateOpen": False,
                    "responseAuthority": "typescript",
                },
            )
        try:
            payload = await request.json()
        except (JSONDecodeError, ValueError):
            payload = {}
        handoff_requested = (
            isinstance(payload, Mapping) and payload.get("handoffRequested") is True
        )
        session_id = _resolve_session_key(payload, request)
        turn_id = _local_chat_string(payload, "turnId", "")
        if not session_id:
            turn = None
        elif turn_id:
            turn = ACTIVE_TURNS.get(session_id, turn_id)
        else:
            resolved = ACTIVE_TURNS.get_single(session_id)
            if resolved == "ambiguous":
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "ambiguous_active_turn",
                        "reason": "python_interrupt_unsupported",
                        "fallback": "typescript_interrupt_required",
                        "activeTurnCompatible": False,
                        "handoffRequested": handoff_requested,
                        "gateStateOpen": False,
                        "responseAuthority": "typescript",
                    },
                )
            turn = resolved
        if turn is None:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "no_active_turn",
                    "reason": "python_interrupt_unsupported",
                    "fallback": "typescript_interrupt_required",
                    "activeTurnCompatible": False,
                    "handoffRequested": handoff_requested,
                    "gateStateOpen": False,
                    "responseAuthority": "typescript",
                },
            )
        # Request a cooperative abort (for any path that polls it) AND hard-
        # cancel the driving task. The gate5b live-runner boundary has no
        # cancel poll, so the task-cancel is what actually aborts it — the
        # boundary catches asyncio.CancelledError and reports client_aborted.
        turn.cancel.set()
        if turn.task is not None and not turn.task.done():
            turn.task.cancel()
        return JSONResponse(
            status_code=200,
            content={
                "status": "cancelling",
                "reason": "python_interrupt_accepted",
                "fallback": "typescript_interrupt_required",
                "activeTurnCompatible": True,
                "handoffRequested": handoff_requested,
                "gateStateOpen": False,
                "responseAuthority": "python",
            },
        )


def _gate5b_governance_event_sink(
    original_sink: Callable[[Mapping[str, object]], None] | None,
    captured: list[Mapping[str, object]],
) -> Callable[[Mapping[str, object]], None]:
    """Wrap the public-event sink to capture the turn's events for grounding.

    Every event is appended to ``captured`` (so the pre-final fact-grounding gate
    can harvest the turn's tool-evidence corpus) and then forwarded to the
    caller's original sink, preserving the streaming contract. Capture itself
    never raises into the boundary: an append failure is swallowed and the
    original sink is still invoked. When governance is OFF the caller still
    passes this wrapper, but ``captured`` is simply never read, so behavior is
    unchanged (the forward keeps the original sink's semantics intact).
    """

    def _sink(event: Mapping[str, object]) -> None:
        try:
            captured.append(dict(event))
        except Exception:
            pass
        if original_sink is not None:
            original_sink(event)

    return _sink


def _pinned_recipe_pack_ids_from_payload(payload: object) -> tuple[str, ...]:
    """Read user-explicit recipe pin from the request payload.

    Reads ``pinnedRecipePackIds`` (camelCase) or ``pinned_recipe_pack_ids``
    (snake_case) from *payload* and returns a tuple of non-empty strings.
    Validation (registry lookup, hard-limit) is downstream in
    ``normalize_pinned_recipe_pack_ids``; this reader is a thin string filter.
    Returns ``()`` for any non-list value or absent key.
    """
    if not isinstance(payload, Mapping):
        return ()
    values = payload.get("pinnedRecipePackIds")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        values = payload.get("pinned_recipe_pack_ids")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return ()
    return tuple(v for v in values if isinstance(v, str) and v)

