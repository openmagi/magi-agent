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
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    run_gate5b4c3_live_runner_boundary_async,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterReservation,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    build_gate5b4c3_shadow_generation_diagnostic,
)
from magi_agent.transport.chat_shared import (
    Gate5BUserVisibleChatRouteConfig,
    _RUNNER_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE,
    _bounded_public_text,
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
from magi_agent.transport.active_turn import ACTIVE_TURNS, ActiveTurn
from magi_agent.runtime.session_identity import session_key_from_headers
from magi_agent.transport.egress_critic import _maybe_run_egress_critic_gate
from magi_agent.transport.gate5b_governance import (
    build_gate5b_control_plane_plugins,
    gate5b_governance_enabled,
    gate5b_pre_final_grounding_status,
)
from magi_agent.transport.gate2_sandbox_canary import (
    Gate1ASelectedAttemptPreflightPayload,
    Gate5BUserVisibleDeliveryReceiptPayload,
    _gate2_sandbox_canary_config,
    _record_gate2_sandbox_workspace_delivery_receipt,
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _camel_to_snake(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper():
            chars.append("_")
            chars.append(char.lower())
        else:
            chars.append(char)
    return "".join(chars).lstrip("_")


_INCOMPLETE_RUNNER_OUTPUT_RE = re.compile(
    r"(?:"
    r"잠시만\s*기다|"
    r"기다려\s*주|"
    r"조금만\s*더\s*기다|"
    r"완료되면|"
    r"전달(?:드리|해)\s*겠|"
    r"진행\s*중|"
    r"처리\s*중|"
    r"실행\s*중|"
    r"작업\s*중|"
    r"please\s+wait|"
    r"still\s+working|"
    r"in\s+progress|"
    r"once\s+(?:it\s+is\s+)?complete|"
    r"when\s+(?:it\s+is\s+)?complete|"
    r"i(?:'|’)ll\s+(?:continue|update)|"
    r"i\s+will\s+(?:continue|update)|"
    r"will\s+(?:continue|update|send|share)\b"
    r")",
    re.IGNORECASE,
)


_FALLBACK_RECEIPT_SCOPE_GATES = frozenset(
    {
        "gate1a_readonly_tools",
        "gate7_5_context_continuity",
    }
)


_FIRST_PARTY_HARNESS_RECIPE_PACK_IDS = (
    "openmagi.context-safety",
    "openmagi.evidence",
    "openmagi.agent-methodology",
    "openmagi.superpowers-compat",
    "openmagi.web-acquisition",
    "openmagi.research",
    "openmagi.dev-coding",
    "openmagi.missions",
    "openmagi.scheduled-work",
    "openmagi.memory-agentmemory",
    "openmagi.channel-delivery",
    "openmagi.office-automation",
    "openmagi.artifact-delivery",
    "openmagi.spreadsheet-automation",
    "openmagi.browser-automation",
    "openmagi.document-review",
    "openmagi.lightweight-scripting",
)


_FALSE_RUNTIME_AUTHORITY_KEYS = (
    "transcriptWritesAllowed",
    "sseWritesAllowed",
    "channelWritesAllowed",
    "dbWritesAllowed",
    "workspaceMutationAllowed",
    "childExecutionAllowed",
    "missionRuntimeAllowed",
    "evidenceBlockModeAllowed",
)


_FALSE_RESPONSE_AUTHORITY_KEYS = (
    "memoryWriteAllowed",
    "toolDispatchAllowed",
    *_FALSE_RUNTIME_AUTHORITY_KEYS,
)


_GATE1A_EGRESS_DISCIPLINE_MODE = "bounded_provider_tunnels"


_GATE1A_MAX_PROVIDER_TUNNELS_PER_MODEL_ATTEMPT = 2


def _local_chat_route_enabled() -> bool:
    return os.environ.get("MAGI_AGENT_LOCAL_CHAT_ROUTE", "off").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _local_adk_chat_response(
    runtime: OpenMagiRuntime,
    payload: object,
) -> StreamingResponse:
    prompt = _local_chat_prompt_text(payload)
    return StreamingResponse(
        _local_adk_chat_sse(runtime, payload, prompt),
        media_type="text/event-stream",
    )


async def _local_adk_chat_sse(
    runtime: OpenMagiRuntime,
    payload: object,
    prompt: str,
) -> AsyncIterator[str]:
    from magi_agent.cli.contracts import EngineResult
    from magi_agent.cli.wiring import (
        build_headless_runtime,
        local_runner_policy_routing_enabled_from_env,
    )
    from magi_agent.config.env import LOCAL_DEV_MODEL_SENTINEL

    session_id = _local_chat_string(payload, "sessionId", "local-dashboard")
    turn_id = _local_chat_string(payload, "turnId", f"{session_id}:turn")
    yield _sse_data({"choices": [{"index": 0, "delta": {"role": "assistant"}}]})
    yield _sse_event(
        "agent",
        {
            "type": "turn_phase",
            "turnId": turn_id,
            "phase": "executing",
        },
    )
    yield _sse_event(
        "agent",
        {
            "type": "llm_progress",
            "turnId": turn_id,
            "stage": "started",
            "label": "Running local ADK",
            "detail": "Local headless engine active",
        },
    )

    # The no-env local fallback injects ``LOCAL_DEV_MODEL_SENTINEL`` as the
    # required ``CORE_AGENT_MODEL``; treat it as "unset" so the headless runner
    # uses the per-provider default model instead of trying to call a
    # nonexistent ``<provider>/local-dev`` model.
    configured_model = runtime.config.model
    model_override = (
        None if configured_model == LOCAL_DEV_MODEL_SENTINEL else configured_model
    )
    workspace_root = os.environ.get("MAGI_AGENT_WORKSPACE") or os.getcwd()
    # Per-turn query-based memory recall (PR-E item 3): pass the incoming user
    # message as the recall query so build_cli_instruction can search the
    # workspace memory tree and inject a <memory-recall> block. Gated + fail-soft
    # downstream (recall_enabled AND prefer_local_search, incognito-aware): when
    # off this is byte-identical (recall_query is just an unused string).
    #
    # TODO(PR-C offload): the recall search runs SYNCHRONOUSLY inside
    # build_headless_runtime → build_cli_instruction (prompt assembly), so it is
    # on this event loop. It already has an empty-tree guard
    # (memory_recall_block._has_indexable_memory) + a tiny corpus + a qmd
    # subprocess timeout, so it is cheap today. It is NOT offloaded here because
    # build_headless_runtime is a single sync call that assembles the whole
    # runtime (engine/gate/commands), not just recall — wrapping the lot in
    # to_thread would move unrelated wiring off-loop. If the memory tree ever
    # grows enough to matter, split the recall-block build out of prompt assembly
    # and to_thread JUST that, mirroring the record_turn offload below.
    # 01-PR4 (C2, issue 3): thread the REAL bot/owner identity into prompt
    # assembly so the gated-live learning recall/write ladder matches the
    # selected-canary digest against the genuine identity (the previous literal
    # "local" default could only ever target the literal "local" scope). The
    # readiness config itself is operator/control-plane-owned: locally there is
    # none, so it resolves to ``disabled`` and the serve prompt stays
    # byte-identical (default-OFF). The hosted prompt-assembly seam (08-hosted-
    # path) is where a real readiness config gets threaded in.
    learning_live_readiness = _resolve_local_learning_live_readiness(runtime)
    runtime_config = getattr(runtime, "config", None)
    serve_bot_id = str(getattr(runtime_config, "bot_id", None) or "local")
    serve_owner_user_id = str(getattr(runtime_config, "user_id", None) or "local")
    headless = build_headless_runtime(
        cwd=workspace_root,
        permission_mode="bypassPermissions",
        session_id=session_id,
        model=model_override,
        runner_policy_routing_enabled=local_runner_policy_routing_enabled_from_env(),
        recall_query=prompt,
        bot_id=serve_bot_id,
        owner_user_id=serve_owner_user_id,
        learning_live_readiness=learning_live_readiness,
    )
    # Route the top-level serve turn through the single ``run_governed_turn``
    # primitive (Phase 1). ``runtime=headless`` reuses the SAME runner/gate/
    # driver assembly built above — the primitive does not rebuild it — so this
    # is behavior-preserving. ``to_turn_input()`` adds ``harness_state=ctx``,
    # which the engine only reads for (inert) task-type/runner-policy metadata
    # (a non-Mapping yields ``()`` and is never forwarded to ADK), leaving the
    # observable event stream byte-identical to the prior inline dict call.
    ctx = TurnContext(
        prompt=prompt,
        session_id=session_id,
        turn_id=turn_id,
        model=model_override,
    )
    stream = run_governed_turn(ctx, runtime=headless)
    # Accumulate the assistant text + a tool-use signal so the turn-end memory
    # hook (below) can flush a concise daily entry and skip trivial turns. This
    # mirrors data we already stream, so it adds no extra engine work.
    assistant_parts: list[str] = []
    used_tool = False
    turn_errored = False
    async for item in stream:
        if isinstance(item, EngineResult):
            if item.error:
                turn_errored = True
                yield _sse_event(
                    "agent",
                    {
                        "type": "error",
                        "turnId": turn_id,
                        "reason": item.error,
                    },
                )
            break
        event_payload = dict(item.payload)
        if event_payload.get("type") == "tool_start":
            used_tool = True
        yield _sse_event("agent", event_payload)
        delta = _local_runtime_event_delta(event_payload)
        if delta:
            assistant_parts.append(delta)
            yield _sse_data({"choices": [{"index": 0, "delta": {"content": delta}}]})
    # ── TURN-END MEMORY HOOK (PR-B) ─────────────────────────────────────────
    # This is the turn-finalization point of the live local chat path: the
    # engine stream has drained, so the assistant turn is complete. Flush a
    # concise turn entry to memory/daily/YYYY-MM-DD.md (the compaction tree's
    # raw input) and trigger a compaction build once per session. Both are GATED
    # (default-OFF master) and FAIL-SOFT — record_turn never raises, so a memory
    # error can never break the user's turn or the SSE stream. Errored turns are
    # skipped (nothing useful to persist). Real date injected at this call site.
    if not turn_errored:
        from magi_agent.runtime.memory_mode_context import (  # noqa: PLC0415
            current_memory_mode,
        )
        from magi_agent.runtime.memory_turn_hook import record_turn  # noqa: PLC0415

        # Thread the per-request memory mode so incognito / read_only actually
        # suppress the live daily flush. ``current_memory_mode()`` is NORMAL
        # unless the (default-OFF) memory-mode routing gate bound it from the
        # ``x-core-agent-memory-mode`` header; ``.value`` yields the string form
        # ``record_turn`` compares against ``_NON_WRITING_MODES``.
        #
        # HOT-PATH OFFLOAD (PR-C): ``record_turn`` is synchronous and its
        # first-turn ``_maybe_run_compaction`` can do ~300ms of file IO (a final
        # review measured it). Run it on a worker thread via ``asyncio.to_thread``
        # so the daily flush + compaction build never block this SSE event loop.
        # Still fail-soft (record_turn swallows its own errors) and gated (no-op
        # when memory is off); the await just keeps the loop responsive.
        #
        # CONCURRENCY: offloading makes genuine concurrent execution possible, and
        # compaction_tree.append_daily_entry is read-modify-write (atomic write,
        # but last-writer-wins if two same-workspace turns finalize concurrently →
        # a daily entry could be lost). Acceptable for the single-user local CLI;
        # a lock here would only be needed if concurrent same-workspace turns
        # become common.
        await asyncio.to_thread(
            record_turn,
            workspace_root=workspace_root,
            session_id=session_id,
            turn_id=turn_id,
            user_text=prompt,
            assistant_text="".join(assistant_parts),
            used_tool=used_tool,
            memory_mode=current_memory_mode().value,
        )
    #
    # NOTE: the Hermes-style background memory *review* (re-reading the transcript
    # to "save what the model forgot") is a SEPARATE mechanism that still needs a
    # live model-backed reviewer and MUST run OFF this hot path. It is intentionally
    # NOT wired here — see magi_agent/harness/memory_review.py. ──────────────────
    yield _sse_data({"choices": [{"index": 0, "finish_reason": "stop"}]})
    yield "data: [DONE]\n\n"


def _local_runtime_event_delta(payload: Mapping[str, object]) -> str:
    for key in ("delta", "text", "content"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


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
# Process-local, asyncio-loop-only (same constraints as ACTIVE_TURNS).
_INJECT_BUFFERS: dict[str, list[str]] = {}


def _buffer_injection(session_id: str, text: str) -> int:
    """Append *text* to *session_id*'s pending-injection buffer; return its size."""
    buffer = _INJECT_BUFFERS.setdefault(session_id, [])
    buffer.append(text)
    return len(buffer)


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


class _NoopChatSink:
    """A do-nothing :class:`ActiveTurn.sink` stand-in for the gate5b path.

    The gate5b live-runner boundary has no headless permission sink (that is a
    cli/engine concept consumed by ``/v1/chat/control-response``). The interrupt
    route never touches ``turn.sink``, so a no-op placeholder satisfies the
    dataclass without dragging engine imports into this module.
    """

    def deliver(self, *_args: object, **_kwargs: object) -> None:  # pragma: no cover
        return None


def _sse_data(payload: Mapping[str, object]) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _sse_event(name: str, payload: Mapping[str, object]) -> str:
    return f"event: {name}\n{_sse_data(payload)}"


def _local_chat_prompt_text(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return ""
    messages = payload.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return ""
    text_parts: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        # Only user-authored text — assistant/system text in the joined prompt
        # poisoned the coding-evidence-gate prompt classifier (see
        # streaming_chat_route._extract_prompt_text). Missing role = user.
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


def _resolve_local_learning_live_readiness(runtime: OpenMagiRuntime) -> object | None:
    """Return the operator/control-plane learning-live readiness config, or None.

    01-PR4 (C2): the gated-live learning recall/write serve seam consumes a
    readiness config the runtime/control-plane resolves (it owns the selected-
    canary digests + environment) — NOT any net-new env var (spec "no new
    flags"). Mirrors the optional ``getattr(runtime, "<canary>_config", None)``
    pattern used for the other gate route configs. When no config is bound (the
    default local case), this returns ``None`` so the serve prompt stays
    byte-identical (the live ladder resolves ``disabled``). Hosted prompt
    assembly (08-hosted-path) is where a real readiness config gets bound.
    """
    from magi_agent.gates.learning_live_readiness import (  # noqa: PLC0415
        LearningLiveReadinessConfig,
    )

    config = getattr(runtime, "learning_live_readiness_config", None)
    if isinstance(config, LearningLiveReadinessConfig):
        return config
    return None


def register_chat_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {runtime.config.gateway_token}"
        if auth != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
            )
        if (
            _local_chat_route_enabled()
            and os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() != "on"
        ):
            try:
                payload = await request.json()
            except (JSONDecodeError, ValueError):
                return JSONResponse(
                    status_code=400,
                    content={"error": "malformed_json"},
                )
            return _local_adk_chat_response(runtime, payload)
        if os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() != "on":
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
            if not route_config.enabled:
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
        return await run_gate5b_user_visible_chat_response(
            runtime,
            payload,
            request=request,
        )

    @app.post("/v1/chat/inject")
    async def chat_inject(request: Request) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {runtime.config.gateway_token}"
        if auth != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
            )
        if os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() != "on":
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
        turn = ACTIVE_TURNS.get(session_id) if session_id else None
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
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {runtime.config.gateway_token}"
        if auth != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
            )
        if os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() != "on":
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
        turn = ACTIVE_TURNS.get(session_id) if session_id else None
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

    @app.post("/v1/internal/gate5b/user-visible-delivery-receipts")
    async def gate5b_user_visible_delivery_receipts(
        request: Request,
        payload: Gate5BUserVisibleDeliveryReceiptPayload,
    ) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {runtime.config.gateway_token}"
        if auth != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
            )
        if os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() != "on":
            return _fallback_response(
                status_code=503,
                status="python_disabled",
                reason="chat_route_disabled",
                runtime=runtime,
            )
        if payload.gate == "gate2_sandbox_workspace_canary":
            return _record_gate2_sandbox_workspace_delivery_receipt(
                runtime=runtime,
                payload=payload,
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
        shadow_config = _shadow_generation_route_config(runtime)
        if shadow_config.counter_store is None:
            return _fallback_response(
                status_code=503,
                status="python_error",
                reason="counter_store_unavailable",
                runtime=runtime,
            )
        fallback_scope_error = _fallback_only_scope_error(
            payload=payload,
            runtime=runtime,
            route_config=route_config,
        )
        if fallback_scope_error is not None:
            return JSONResponse(
                status_code=409,
                content={
                    "schemaVersion": "gate5b.userVisibleDeliveryReceiptResponse.v1",
                    "status": "receipt_rejected",
                    "receiptStatus": "scope_mismatch",
                    "requestDigest": payload.request_digest,
                    "deliveryStatus": payload.delivery_status,
                    "responseAuthority": "typescript",
                    "reason": fallback_scope_error,
                    "diagnosticOnly": True,
                    "localOnly": True,
                },
            )
        research_first_receipt_error = (
            shadow_config.counter_store.gate8_research_first_delivery_receipt_error(
                request_digest=payload.request_digest,
                selected_bot_digest=_sha256_digest(runtime.config.bot_id),
                trusted_owner_user_id_digest=_sha256_digest(runtime.config.user_id),
                environment=route_config.environment,
                delivery_status=payload.delivery_status,
                gate=payload.gate,
                route_decision=payload.route_decision,
                response_authority=payload.response_authority,
                output_digest=payload.output_digest,
                source_ledger_digest=payload.source_ledger_digest,
                final_projection_digest=payload.final_projection_digest,
                research_evidence_status=payload.research_evidence_status,
                citation_evidence_status=payload.citation_evidence_status,
                verifier_evidence_status=payload.verifier_evidence_status,
                final_projection_evidence_status=(
                    payload.final_projection_evidence_status
                ),
                source_inspected_event_count=payload.source_inspected_event_count,
                rule_check_event_count=payload.rule_check_event_count,
            )
        )
        if research_first_receipt_error is not None:
            return JSONResponse(
                status_code=409,
                content={
                    "schemaVersion": "gate5b.userVisibleDeliveryReceiptResponse.v1",
                    "status": "receipt_rejected",
                    "receiptStatus": "receipt_rejected",
                    "requestDigest": payload.request_digest,
                    "deliveryStatus": payload.delivery_status,
                    "responseAuthority": "typescript",
                    "reason": research_first_receipt_error,
                    "diagnosticOnly": True,
                    "localOnly": True,
                },
            )

        receipt = shadow_config.counter_store.record_delivery_receipt(
            request_digest=payload.request_digest,
            selected_bot_digest=_sha256_digest(runtime.config.bot_id),
            trusted_owner_user_id_digest=_sha256_digest(runtime.config.user_id),
            environment=route_config.environment,
            delivery_status=payload.delivery_status,
            reason=payload.reason,
            body_digest=payload.body_digest,
            route_decision=payload.route_decision,
            response_authority=payload.response_authority,
            gate=payload.gate,
            served_at=payload.served_at,
            completed_at=payload.completed_at,
            fallback_reason=payload.fallback_reason,
            sse_frame_count=payload.sse_frame_count,
            tool_receipt_count=payload.tool_receipt_count,
            model_attempt_count=payload.model_attempt_count,
            provider_request_count=payload.provider_request_count,
            expected_model_attempt_count=payload.expected_model_attempt_count,
            egress_connect_count=payload.egress_connect_count,
            egress_tunnel_count=payload.egress_tunnel_count,
            egress_discipline_mode=payload.egress_discipline_mode,
            egress_evidence_status=payload.egress_evidence_status,
            egress_evidence_source=payload.egress_evidence_source,
            egress_evidence_redaction_status=payload.egress_evidence_redaction_status,
            egress_evidence_decision_reason=payload.egress_evidence_decision_reason,
            model_attempt_digest=payload.model_attempt_digest,
            max_provider_tunnels_per_model_attempt=(
                payload.max_provider_tunnels_per_model_attempt
            ),
            egress_host_classes=payload.egress_host_classes,
            egress_correlation_digest=payload.egress_correlation_digest,
            egress_window_started_at=payload.egress_window_started_at,
            egress_window_ended_at=payload.egress_window_ended_at,
            egress_outside_gate_window=payload.egress_outside_gate_window,
            output_digest=payload.output_digest,
            workspace_mutation_receipt_digest=payload.workspace_mutation_receipt_digest,
            rollback_receipt_digest=payload.rollback_receipt_digest,
            sandbox_path_digest=payload.sandbox_path_digest,
            source_ledger_digest=payload.source_ledger_digest,
            final_projection_digest=payload.final_projection_digest,
            research_evidence_status=payload.research_evidence_status,
            citation_evidence_status=payload.citation_evidence_status,
            verifier_evidence_status=payload.verifier_evidence_status,
            final_projection_evidence_status=payload.final_projection_evidence_status,
            source_inspected_event_count=payload.source_inspected_event_count,
            rule_check_event_count=payload.rule_check_event_count,
            unsupported_claim_omitted_count=payload.unsupported_claim_omitted_count,
            python_attempted=payload.python_attempted,
            python_counter_record_present=payload.python_counter_record_present,
            context_continuity=_context_continuity_chat_diagnostic(runtime),
        )
        status_code = 404 if receipt.status == "not_found" else 202
        return JSONResponse(
            status_code=status_code,
            content={
                "schemaVersion": "gate5b.userVisibleDeliveryReceiptResponse.v1",
                "status": (
                    "receipt_not_found"
                    if receipt.status == "not_found"
                    else "receipt_recorded"
                ),
                "receiptStatus": receipt.status,
                "requestDigest": payload.request_digest,
                "deliveryStatus": payload.delivery_status,
                "responseAuthority": "typescript",
                "diagnosticOnly": True,
                "localOnly": True,
                "counter": {
                    "deliveryReceiptCount": receipt.delivery_receipt_count,
                    "deliveryDuplicateCount": receipt.delivery_duplicate_count,
                    "deliveryConflictCount": receipt.delivery_conflict_count,
                },
            },
        )

    @app.post("/v1/internal/gate1a/selected-attempt-preflight")
    async def gate1a_selected_attempt_preflight(
        request: Request,
        payload: Gate1ASelectedAttemptPreflightPayload,
    ) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {runtime.config.gateway_token}"
        if auth != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized"},
            )
        if os.environ.get("CORE_AGENT_PYTHON_CHAT_ROUTE", "off").lower() != "on":
            return _fallback_response(
                status_code=503,
                status="python_disabled",
                reason="chat_route_disabled",
                runtime=runtime,
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
        shadow_config = _shadow_generation_route_config(runtime)
        if shadow_config.counter_store is None:
            return _fallback_response(
                status_code=503,
                status="python_error",
                reason="counter_store_unavailable",
                runtime=runtime,
            )
        budgets = shadow_config.generation_config.approved_budgets
        preflight = shadow_config.counter_store.preflight_gate1a_selected_attempt(
            request_digest=payload.request_digest,
            selected_bot_digest=_sha256_digest(runtime.config.bot_id),
            trusted_owner_user_id_digest=_sha256_digest(runtime.config.user_id),
            environment=route_config.environment,
            max_daily_generation_runs=budgets.max_daily_generation_runs,
            max_daily_generation_cost_usd=budgets.max_daily_generation_cost_usd,
            max_concurrent_generation_runs=budgets.max_concurrent_generation_runs,
            max_pending_generation_runs=budgets.max_pending_generation_runs,
            cost_cap_usd=budgets.max_cost_usd,
            fallback_receipt_path_available=payload.fallback_receipt_path_available,
        )
        return JSONResponse(
            status_code=200 if preflight.status == "ready" else 409,
            content={
                **preflight.model_dump(by_alias=True, mode="json"),
                "diagnosticOnly": True,
                "localOnly": True,
                "responseAuthority": "typescript",
            },
        )


async def run_gate5b_user_visible_chat_response(
    runtime: OpenMagiRuntime,
    payload: object,
    *,
    request: Request,
    public_event_sink: Callable[[Mapping[str, object]], None] | None = None,
) -> JSONResponse:
    """Run the selected Gate5B user-visible chat path for HTTP adapters.

    This preserves the existing completions-route boundary in one place so
    additive surfaces such as ``/v1/chat/stream`` can reuse the same selected
    canary gates, ToolHost attachment, evidence, counters, and fallback
    diagnostics without minting a second runtime path.
    """
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
    gate1a_bundle = _gate1a_readonly_tool_bundle(runtime, route_config)
    memory_mode = _memory_mode_from_header(
        request.headers.get("x-core-agent-memory-mode")
    )
    gate5b_full_bundle = _gate5b_full_toolhost_bundle(
        runtime,
        route_config,
        memory_mode=memory_mode,
        public_event_sink=public_event_sink,
        session_id=_local_chat_string(payload, "sessionId", "") or None,
    )
    tool_bundle = (
        gate5b_full_bundle
        if gate5b_full_bundle.status == "ready"
        else gate1a_bundle
    )
    # Bundles are created per-request; the gate5b host may lazily spawn
    # language-server subprocesses on the first code-file write. Tear them down
    # at the end of the request so we never leak FDs/processes/memory across
    # requests in a long-lived worker pod. Fail-open: shutdown errors never
    # affect the response.
    try:
        if research_first_selected_canary_active(payload):
            try:
                research_first = build_research_first_selected_response(
                    payload,
                    bot_id=runtime.config.bot_id,
                    user_id=runtime.config.user_id,
                    environment=route_config.environment,
                    now_ms=int(time.time() * 1000),
                    request_digest=request.headers.get("x-gate5b-canary-request-digest"),
                )
                shadow_config = _shadow_generation_route_config(runtime)
                if shadow_config.counter_store is None:
                    return _fallback_response(
                        status_code=503,
                        status="python_error",
                        reason="counter_store_unavailable",
                        runtime=runtime,
                    )
                source_ledger = research_first.metadata.get("sourceLedger")
                source_ledger_digest = (
                    source_ledger.get("ledgerDigest")
                    if isinstance(source_ledger, Mapping)
                    else None
                )
                if not isinstance(source_ledger_digest, str):
                    raise ValueError("research-first source ledger digest missing")
                counter_state = (
                    shadow_config.counter_store.record_gate8_research_first_canary_evidence(
                        request_digest=str(research_first.metadata["requestDigest"]),
                        selected_bot_digest=_sha256_digest(runtime.config.bot_id),
                        trusted_owner_user_id_digest=_sha256_digest(runtime.config.user_id),
                        environment=route_config.environment,
                        source_ledger_digest=source_ledger_digest,
                        output_digest=research_first.final_gate_result.final_answer_digest,
                    )
                )
            except (KeyError, OSError, ValidationError, ValueError, TypeError):
                return _fallback_response(
                    status_code=422,
                    status="python_error",
                    reason="research_first_projection_failed",
                    runtime=runtime,
                )
            return _python_ready_response(
                runtime=runtime,
                content=research_first.content,
                event_count=research_first.event_count,
                adk_invoked=False,
                runner_attempted=False,
                model_call_attempted=False,
                mocked_runner_invoked=False,
                counter_state=counter_state,
                counter_status="research_first_completed",
                public_events=research_first.public_events,
                research_first_metadata=research_first.metadata,
            )
        if route_config.mocked_runner is not None:
            return _run_mocked_chat_runner(runtime, route_config, payload, tool_bundle)
        return await _run_live_chat_runner(
            runtime,
            route_config,
            payload,
            request=request,
            gate1a_bundle=tool_bundle,
            public_event_sink=public_event_sink,
        )
    finally:
        try:
            gate5b_full_bundle.host.shutdown()
        except Exception:  # noqa: BLE001 — teardown must never break a response
            pass


def _gate1a_config(runtime: OpenMagiRuntime) -> Gate1AReadOnlyToolConfig:
    config = getattr(runtime, "gate1a_readonly_tools_config", None)
    if isinstance(config, Gate1AReadOnlyToolConfig):
        return config
    return Gate1AReadOnlyToolConfig()


def _gate1a_readonly_tool_bundle(
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
) -> Gate1AReadOnlyToolBundle:
    return build_gate1a_readonly_tool_bundle(
        config=_gate1a_config(runtime),
        scope={
            "selectedBotDigest": _sha256_digest(runtime.config.bot_id),
            "selectedOwnerDigest": _sha256_digest(runtime.config.user_id),
            "environment": route_config.environment or "local",
        },
        workspace_root=_gate1a_workspace_root(),
    )


def _gate5b_full_toolhost_config(runtime: OpenMagiRuntime) -> Gate5BFullToolHostConfig:
    config = getattr(runtime, "gate5b_full_toolhost_config", None)
    if isinstance(config, Gate5BFullToolHostConfig):
        return config
    return Gate5BFullToolHostConfig()


def _gate5b_full_toolhost_bundle(
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
    *,
    memory_mode: "MemoryMode | str" = "normal",
    public_event_sink: Callable[[Mapping[str, object]], None] | None = None,
    session_id: str | None = None,
) -> Gate5BFullToolBundle:
    return build_gate5b_full_toolhost_bundle(
        config=_gate5b_full_toolhost_config(runtime),
        scope={
            "selectedBotDigest": _sha256_digest(runtime.config.bot_id),
            "selectedOwnerDigest": _sha256_digest(runtime.config.user_id),
            "environment": route_config.environment or "local",
        },
        workspace_root=_gate5b_full_toolhost_workspace_root(),
        tool_registry=runtime.tool_registry,
        memory_mode=memory_mode,
        public_event_sink=public_event_sink,
        # Top-level serve turn: parent depth 0 (a SpawnAgent call requests depth
        # 1). The session id gives spawned children a stable parent reference.
        session_id=session_id,
        spawn_depth=0,
    )


def _gate5b_full_toolhost_workspace_root() -> Path:
    configured = os.environ.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT")
    if configured:
        return Path(configured)
    return Path.cwd()


def _gate1a_workspace_root() -> Path:
    configured = os.environ.get("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT")
    if configured:
        return Path(configured)
    return Path.cwd()


def _run_mocked_chat_runner(
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
    payload: object,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
) -> JSONResponse:
    del gate1a_bundle
    try:
        runner_request = build_gate5b_user_visible_canary_runner_request(
            payload if isinstance(payload, Mapping) else {},
            context_continuity=_context_continuity_chat_diagnostic(runtime),
        )
        result = route_config.mocked_runner(runner_request)
        if not isinstance(result, Mapping):
            raise ValueError("mocked runner result must be a mapping")
        content = sanitize_gate5b_model_visible_identity_text(str(result.get("content") or ""))
        event_count = int(result.get("eventCount") or 0)
    except TimeoutError:
        return _fallback_response(
            status_code=504,
            status="timeout",
            reason="mocked_runner_timeout",
            runtime=runtime,
        )
    except (Exception, ValidationError, ValueError, TypeError):
        return _fallback_response(
            status_code=502,
            status="python_error",
            reason="mocked_runner_error",
            runtime=runtime,
        )
    return _python_ready_response(
        runtime=runtime,
        content=content,
        event_count=event_count,
        adk_invoked=False,
        runner_attempted=False,
        model_call_attempted=False,
        mocked_runner_invoked=True,
    )


def _swallow_task_result(task: "asyncio.Task[object]") -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


def _schedule_runtime_direct_usage_receipt(
    *,
    runtime: OpenMagiRuntime,
    model: str,
    usage: Mapping[str, int] | None,
    turn_id: str,
) -> None:
    if not usage or not model:
        return
    if not usage_receipt_enabled(os.environ):
        return
    try:
        coro = emit_runtime_direct_usage_receipt(
            api_proxy_url=str(runtime.config.api_proxy_url),
            gateway_token=runtime.config.gateway_token,
            bot_id=runtime.config.bot_id,
            user_id=runtime.config.user_id,
            model=model,
            usage=usage,
            turn_id=turn_id,
        )
    except Exception:  # noqa: BLE001 - metering setup must not break the turn
        return
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:
        coro.close()
        return
    task.add_done_callback(_swallow_task_result)


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


async def _run_live_chat_runner(
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
    payload: object,
    *,
    request: Request,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
    public_event_sink: Callable[[Mapping[str, object]], None] | None = None,
) -> JSONResponse:
    shadow_config = _shadow_generation_route_config(runtime)
    generation_config = shadow_config.generation_config
    if not shadow_config.live_runner_boundary_enabled:
        return _fallback_response(
            status_code=503,
            status="python_error",
            reason="live_runner_gate_disabled",
            runtime=runtime,
        )
    if shadow_config.counter_store is None:
        return _fallback_response(
            status_code=503,
            status="python_error",
            reason="counter_store_unavailable",
            runtime=runtime,
        )
    try:
        generation = _build_user_visible_generation_request(
            runtime=runtime,
            route_config=route_config,
            generation_config=generation_config,
            payload=payload,
            trace_id=request.headers.get("x-magi-trace-id"),
            canary_request_digest=request.headers.get("x-gate5b-canary-request-digest"),
            gate1a_bundle=gate1a_bundle,
            request_headers=request.headers,
        )
    except (ValidationError, ValueError, TypeError):
        return _fallback_response(
            status_code=422,
            status="python_error",
            reason="invalid_generation_payload",
            runtime=runtime,
        )

    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        generation,
        config=generation_config,
    )
    if not diagnostic.accepted:
        return _fallback_response(
            status_code=503,
            status="python_disabled",
            reason=diagnostic.reason,
            runtime=runtime,
        )
    reservation = shadow_config.counter_store.reserve(
        request_digest=generation.request_id_digest,
        shadow_generation_id=generation.shadow_generation_id,
        selected_bot_digest=generation.selection.bot_id_digest,
        trusted_owner_user_id_digest=generation.selection.owner_user_id_digest,
        environment=generation.selection.environment,
        max_daily_generation_runs=generation.budgets.max_daily_generation_runs,
        max_daily_generation_cost_usd=generation.budgets.max_daily_generation_cost_usd,
        max_concurrent_generation_runs=generation.budgets.max_concurrent_generation_runs,
        max_pending_generation_runs=generation.budgets.max_pending_generation_runs,
        cost_cap_usd=generation.budgets.max_cost_usd,
        cost_owner_waiver=generation_config.cost_owner_waiver,
    )
    if reservation.status != "reserved":
        failure_reason = (
            "counter_duplicate_replay"
            if reservation.status == "duplicate_replay"
            else f"counter_{reservation.reason}"
        )
        return _fallback_response(
            status_code=503,
            status="python_disabled",
            reason=failure_reason,
            runtime=runtime,
            counter_state=reservation.counter_state,
            counter_status=reservation.status,
        )
    model_attempt_digest = _model_attempt_digest(
        request_digest=generation.request_id_digest,
        provider=generation.model_routing.provider_label,
        model=generation.model_routing.model_label,
        model_call_attempted=True,
    )
    gate8_ready = _gate8_selected_authority_metadata(runtime) is not None
    gate1a_egress_context, gate1a_egress_proxy_url = (
        _build_gate1a_egress_correlation_context(
            runtime=runtime,
            request_digest=generation.request_id_digest,
            model_attempt_digest=model_attempt_digest,
            gate1a_bundle=gate1a_bundle,
            gate8_ready=gate8_ready,
        )
    )
    # MAGI_GATE5B_GOVERNANCE (default-OFF): build the control-plane plugin and
    # set up a public-event capture sink so the SAME governance the cli/engine
    # runner gets reaches this serving runner. When the flag is OFF
    # ``control_plane_plugins`` is ``[]``, the capture wrapper is NOT installed
    # (the boundary gets the caller's exact ``public_event_sink``), and the
    # boundary call is byte-identical to today.
    governance_enabled = gate5b_governance_enabled()
    control_plane_plugins = build_gate5b_control_plane_plugins(
        general_automation_receipts=getattr(
            gate1a_bundle.host, "general_automation_receipts", None
        ),
        tool_synthesis_model_label=generation.model_routing.model_label,
    )
    captured_governance_events: list[Mapping[str, object]] = []
    governance_event_sink = (
        _gate5b_governance_event_sink(public_event_sink, captured_governance_events)
        if governance_enabled
        else public_event_sink
    )
    model_call_window_start = _utc_now_iso()
    # Register this in-flight turn so the /v1/chat/interrupt route can find and
    # hard-cancel it. The gate5b boundary threads NO cooperative cancel poll, so
    # interrupt cancels ``ActiveTurn.task`` (the boundary catches CancelledError
    # → client_aborted). Fail-soft: registration must never break a normal turn,
    # and is a no-op when no session key is resolvable (nothing can address it).
    # Completions carries the session in the session-key header; interrupt/inject
    # carry the same value in the body, so both resolve to one canonical key.
    active_session_id = _resolve_session_key(payload, request)
    active_turn_id = generation.shadow_generation_id
    active_turn_registered = False
    if active_session_id:
        try:
            ACTIVE_TURNS.register(
                ActiveTurn(
                    session_id=active_session_id,
                    turn_id=active_turn_id,
                    cancel=asyncio.Event(),
                    sink=_NoopChatSink(),  # type: ignore[arg-type]
                    task=asyncio.current_task(),
                )
            )
            active_turn_registered = True
        except Exception:  # noqa: BLE001 — registration must never break a turn
            active_turn_registered = False
    try:
        boundary_result = await run_gate5b4c3_live_runner_boundary_async(
            generation,
            config=generation_config,
            adk_primitives_loader=route_config.adk_primitives_loader,
            adk_tools=gate1a_bundle.tools if gate1a_bundle.status == "ready" else (),
            gate1a_egress_correlation_context=gate1a_egress_context,
            gate1a_egress_proxy_url=gate1a_egress_proxy_url,
            public_event_sink=governance_event_sink,
            control_plane_plugins=control_plane_plugins,
        )
        model_call_window_end = _utc_now_iso()
        report_digest = _sha256_digest(
            "|".join(
                (
                    generation.request_id_digest,
                    boundary_result.status,
                    str(boundary_result.event_count),
                )
            )
        )
    except asyncio.CancelledError:
        counter_state = shadow_config.counter_store.finish(
            reservation,
            status="client_aborted",
            reason="client_aborted",
        )
        return _fallback_response(
            status_code=503,
            status="python_error",
            reason="client_aborted",
            runtime=runtime,
            counter_state=counter_state,
            counter_status="client_aborted",
        )
    except TimeoutError:
        runner_error_diagnostic = _chat_runner_error_diagnostic(
            runtime=runtime,
            generation=generation,
            gate1a_bundle=gate1a_bundle,
            stage="runner_execution",
            reason_code="runner_timeout",
            exception_class="TimeoutError",
            exception_category="runner_timeout",
            gate1a_egress_context=gate1a_egress_context,
            gate1a_egress_proxy_url=gate1a_egress_proxy_url,
        )
        counter_state = _finish_counter_error(
            shadow_config,
            reservation,
            "runner_timeout",
            runner_error_diagnostic=runner_error_diagnostic,
        )
        return _fallback_response(
            status_code=504,
            status="timeout",
            reason="runner_timeout",
            runtime=runtime,
            counter_state=counter_state,
            counter_status="error",
            runner_error_diagnostic=runner_error_diagnostic,
        )
    except Exception as exc:
        runner_error_diagnostic = _chat_runner_error_diagnostic(
            runtime=runtime,
            generation=generation,
            gate1a_bundle=gate1a_bundle,
            stage="unexpected_exception",
            reason_code="runner_boundary_exception",
            exception_class=type(exc).__name__,
            exception_category="unexpected_exception",
            gate1a_egress_context=gate1a_egress_context,
            gate1a_egress_proxy_url=gate1a_egress_proxy_url,
        )
        counter_state = _finish_counter_error(
            shadow_config,
            reservation,
            "runner_error",
            runner_error_diagnostic=runner_error_diagnostic,
        )
        return _fallback_response(
            status_code=502,
            status="python_error",
            reason="runner_error",
            runtime=runtime,
            counter_state=counter_state,
            counter_status="error",
            runner_error_diagnostic=runner_error_diagnostic,
        )
    finally:
        # Turn is no longer in flight at the runner boundary; drop it from the
        # interrupt-addressable registry. turn_id-guarded so a NEWER turn that
        # already replaced this one under the same session is not evicted.
        # Fail-soft: never let teardown break the response.
        if active_turn_registered:
            try:
                ACTIVE_TURNS.unregister(active_session_id, active_turn_id)
            except Exception:  # noqa: BLE001 — teardown must never break a response
                pass
    if (
        boundary_result.status == "completed"
        and boundary_result.output_text_internal
        and await _client_disconnected(request, route_config)
    ):
        counter_state = shadow_config.counter_store.finish(
            reservation,
            status="completed_after_client_timeout",
            reason="client_aborted_after_runner",
            report_digest=report_digest,
        )
        return _fallback_response(
            status_code=503,
            status="python_error",
            reason="client_aborted_after_runner",
            runtime=runtime,
            counter_state=counter_state,
            counter_status="completed_after_client_timeout",
            adk_invoked=boundary_result.adk_invoked,
        )
    runner_error_diagnostic = _boundary_runner_error_diagnostic(
        runtime=runtime,
        boundary_result=boundary_result,
    )
    runner_output_missing = (
        boundary_result.status == "completed"
        and not boundary_result.output_text_internal
    )
    runner_incomplete_reason = (
        None
        if runner_output_missing
        else _runner_incomplete_output_reason(boundary_result.output_text_internal)
    )
    counter_status = (
        "runner_completed"
        if (
            boundary_result.status == "completed"
            and not runner_output_missing
            and runner_incomplete_reason is None
        )
        else "error"
    )
    counter_reason = (
        "runner_output_missing"
        if runner_output_missing
        else runner_incomplete_reason or boundary_result.reason
    )
    counter_state = shadow_config.counter_store.finish(
        reservation,
        status=counter_status,
        reason=counter_reason,
        report_digest=report_digest,
        runner_error_diagnostic=runner_error_diagnostic,
    )
    if (
        boundary_result.status != "completed"
        or runner_output_missing
        or runner_incomplete_reason is not None
    ):
        return _fallback_response(
            status_code=502,
            status="python_error",
            reason=counter_reason,
            runtime=runtime,
            counter_state=counter_state,
            counter_status=counter_status,
            adk_invoked=boundary_result.adk_invoked,
            runner_error_diagnostic=runner_error_diagnostic,
        )
    model_attempt_digest = (
        model_attempt_digest
        if boundary_result.model_call_via_adk_runner_attempted
        else None
    )
    observed_egress_evidence = _collect_gate1a_observed_egress_evidence(
        runtime=runtime,
        request_digest=generation.request_id_digest,
        model_attempt_digest=model_attempt_digest,
        gate1a_bundle=gate1a_bundle,
        gate8_ready=gate8_ready,
        model_call_attempted=boundary_result.model_call_via_adk_runner_attempted,
        observed_window_start=model_call_window_start,
        observed_window_end=model_call_window_end,
    )
    if (
        gate8_ready
        and boundary_result.model_call_via_adk_runner_attempted
        and observed_egress_evidence is None
    ):
        return _fallback_response(
            status_code=503,
            status="python_error",
            reason="missing_observed_egress_evidence",
            runtime=runtime,
            counter_state=counter_state,
            counter_status=counter_status,
            adk_invoked=boundary_result.adk_invoked,
        )
    # Egress critic gate (default-OFF). When the flag is OFF this block is
    # skipped entirely so the response is byte-identical to before. When ON, for
    # fact-critical turns it grounds the draft against the real evidence view and
    # sets ``verifierEvidenceStatus`` on the payload. Fail-open: never blocks.
    verifier_evidence_status: EgressVerifierStatus | None = None
    if is_egress_gate_enabled():
        verifier_evidence_status = await _maybe_run_egress_critic_gate(
            payload=payload,
            draft_text=boundary_result.output_text_internal or "",
            gate1a_bundle=gate1a_bundle,
        )
    # MAGI_GATE5B_GOVERNANCE (default-OFF): pre-final fact-grounding gate over the
    # turn's collected tool-evidence corpus (captured public events). Reuses the
    # SAME deterministic grounding detector the cli/engine pre-final gate uses. An
    # ungrounded guess (a specific numeric/identifier value with no corroborating
    # evidence in the corpus) BLOCKS the user-visible response — exactly as
    # cli/engine blocks an ungrounded ``fact_grounding`` requirement. When the
    # flag is OFF this returns ``None`` and the response is byte-identical.
    # The reservation was already finished above (``runner_completed``); the
    # grounding gate is a pre-EGRESS block, so it does NOT re-finish the counter
    # (that would double-finish a closed reservation). It reuses the existing
    # ``counter_state`` and returns the TypeScript fallback so the ungrounded
    # draft is never emitted as a python-authority answer.
    grounding_status = gate5b_pre_final_grounding_status(
        final_text=boundary_result.output_text_internal or "",
        public_events=captured_governance_events,
    )
    if grounding_status == "ungrounded_guess":
        return _fallback_response(
            status_code=422,
            status="python_error",
            reason="gate5b_governance_ungrounded_answer",
            runtime=runtime,
            counter_state=counter_state,
            counter_status=counter_status,
            adk_invoked=boundary_result.adk_invoked,
        )
    _schedule_runtime_direct_usage_receipt(
        runtime=runtime,
        model=boundary_result.selected_model,
        usage=getattr(boundary_result, "usage_internal", None),
        turn_id=generation.request_id_digest,
    )
    return _python_ready_response(
        runtime=runtime,
        content=sanitize_gate5b_model_visible_identity_text(
            _bounded_public_text(boundary_result.output_text_internal)
        ),
        event_count=boundary_result.event_count,
        adk_invoked=boundary_result.adk_invoked,
        runner_attempted=boundary_result.runner_attempted,
        model_call_attempted=boundary_result.model_call_via_adk_runner_attempted,
        mocked_runner_invoked=False,
        provider=boundary_result.selected_provider,
        model=boundary_result.selected_model,
        counter_state=counter_state,
        counter_status="runner_completed",
        gate1a_bundle=gate1a_bundle,
        model_attempt_digest=model_attempt_digest,
        observed_egress_evidence=observed_egress_evidence,
        public_events=(
            ()
            if public_event_sink is not None
            else _gate5b_full_toolhost_public_events(gate1a_bundle)
        ),
        first_party_harness_metadata=_first_party_harness_metadata(
            payload=payload,
            gate1a_bundle=gate1a_bundle,
        ),
        verifier_evidence_status=verifier_evidence_status,
    )


def _gate5b_full_toolhost_public_events(
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
) -> tuple[Mapping[str, object], ...]:
    if not _route_tool_bundle_full(gate1a_bundle):
        return ()
    turn_id = "turn-gate5b-full-toolhost"
    events: list[Mapping[str, object]] = [
        turn_phase_event(turn_id=turn_id, phase="executing"),
        {
            "type": "llm_progress",
            "turnId": turn_id,
            "stage": "started",
            "label": "Running Python ADK",
            "detail": "Selected first-party toolhost active",
        },
    ]
    receipts = getattr(gate1a_bundle.host.counter, "receipts", ())
    for index, receipt in enumerate(receipts[:8], start=1):
        tool_id = _gate5b_full_toolhost_tool_event_id(receipt, index)
        tool_name = str(getattr(receipt, "tool_name", "") or "Tool")
        events.append(tool_start_event(tool_id=tool_id, name=tool_name))
        events.append(
            tool_progress_event(
                tool_id=tool_id,
                label=tool_name,
                status="complete",
                message="Tool receipt recorded",
            )
        )
        events.append(
            tool_end_event(
                tool_id=tool_id,
                status="ok" if getattr(receipt, "status", "") == "ok" else "error",
                output_preview=(
                    f"bytes={getattr(receipt, 'output_byte_count', 0)} "
                    f"result={getattr(receipt, 'bounded_output_digest', '')}"
                ),
                receipt_refs=(f"receipt:{getattr(receipt, 'bounded_output_digest', '')}",),
            )
        )
    events.append(turn_phase_event(turn_id=turn_id, phase="committed"))
    return tuple(events[:25])


def _gate5b_full_toolhost_tool_event_id(receipt: object, index: int) -> str:
    digest = str(getattr(receipt, "tool_call_digest", "") or "")
    if digest.startswith("sha256:") and len(digest) >= 19:
        return f"tu_{digest[7:19]}"
    return f"tu_{index}"


def _finish_counter_error(
    route_config: Gate5B4C3ShadowGenerationRouteConfig,
    reservation: Gate5B4C3ShadowCounterReservation,
    reason: str,
    *,
    runner_error_diagnostic: Mapping[str, object] | None = None,
) -> object:
    return route_config.counter_store.finish(
        reservation,
        status="error",
        reason=reason,
        runner_error_diagnostic=runner_error_diagnostic,
    )


def _boundary_runner_error_diagnostic(
    *,
    runtime: OpenMagiRuntime,
    boundary_result: object,
) -> dict[str, object] | None:
    diagnostic = getattr(boundary_result, "runner_error_diagnostic", None)
    if diagnostic is None:
        return None
    if hasattr(diagnostic, "model_dump"):
        payload = diagnostic.model_dump(by_alias=True, mode="json", warnings=False)
    elif isinstance(diagnostic, Mapping):
        payload = dict(diagnostic)
    else:
        return None
    return _augment_runner_error_diagnostic(runtime=runtime, payload=payload)


def _chat_runner_error_diagnostic(
    *,
    runtime: OpenMagiRuntime,
    generation: UserVisibleGenerationRequest,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
    stage: str,
    reason_code: str,
    exception_class: str | None,
    exception_category: str | None,
    gate1a_egress_context: Gate1AEgressCorrelationContext | None,
    gate1a_egress_proxy_url: str | None,
) -> dict[str, object]:
    correlation_ready = (
        gate1a_egress_context is not None
        and bool(str(gate1a_egress_proxy_url or "").strip())
    )
    tool_bundle_ready = _route_tool_bundle_ready(gate1a_bundle)
    payload: dict[str, object] = {
        "schemaVersion": "gate5b4c3.runnerErrorDiagnostic.v1",
        "stage": _safe_label_or_default(stage, "unexpected_exception"),
        "reasonCode": _safe_label_or_default(reason_code, "runner_error"),
        "requestDigest": generation.request_id_digest,
        "traceIdDigest": generation.trace_id_digest,
        "routeMode": "user_visible_generation",
        "gateMode": _route_tool_bundle_mode(gate1a_bundle),
        "toolsPolicy": _safe_label_or_default(
            generation.recipe_profile.tools_policy,
            "unknown",
        ),
        "routingSource": _safe_label_or_default(
            generation.model_routing.routing_source,
            "unknown",
        ),
        "correlationMode": "proxy_connect_headers" if correlation_ready else "none",
        "activeToolNames": _public_safe_tool_names(gate1a_bundle.exposed_tool_names),
        "adkInvoked": False,
        "runnerAttempted": False,
        "modelCallAttempted": False,
        "toolsEnabled": not generation.policy.tools_disabled,
        "toolHostDispatchAllowed": generation.policy.tool_host_dispatch_allowed,
        "adkPrimitivesLoaderConfigured": True,
        "gate1aEgressCorrelationContextPresent": gate1a_egress_context is not None,
        "gate1aProxyUrlConfigured": bool(str(gate1a_egress_proxy_url or "").strip()),
        "egressCorrelationHeadersConfigured": correlation_ready,
    }
    if exception_class is not None:
        payload["exceptionClass"] = _safe_label_or_default(exception_class, "Exception")
    if exception_category is not None:
        payload["exceptionCategory"] = _safe_label_or_default(
            exception_category,
            "unexpected_exception",
        )
    if gate1a_egress_context is not None:
        payload["correlationDigest"] = gate1a_egress_context.correlation_digest
        if gate1a_egress_context.model_attempt_digest is not None:
            payload["modelAttemptDigest"] = gate1a_egress_context.model_attempt_digest
    return _augment_runner_error_diagnostic(runtime=runtime, payload=payload) or payload


def _augment_runner_error_diagnostic(
    *,
    runtime: OpenMagiRuntime,
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    safe_payload = _public_safe_runner_error_diagnostic(payload)
    if safe_payload is None:
        return None
    runtime_version = _safe_label_or_none(getattr(runtime.config.build, "version", None))
    build_sha = _safe_label_or_none(getattr(runtime.config.build, "build_sha", None))
    if runtime_version is not None:
        safe_payload["runtimeVersion"] = runtime_version
    if build_sha is not None:
        safe_payload["buildSha"] = build_sha
    provider = get_observed_egress_evidence_provider(runtime)
    egress_diagnostic = observed_egress_diagnostics(provider)
    safe_payload["observedEgressEvidenceAvailable"] = bool(
        egress_diagnostic["observedEgressEvidenceAvailable"]
    )
    safe_payload["gate1aEgressEvidenceReady"] = bool(
        egress_diagnostic["gate1aEgressEvidenceReady"]
    )
    return safe_payload


def _public_safe_runner_error_diagnostic(
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    safe_payload: dict[str, object] = {}
    string_fields = {
        "schemaVersion",
        "stage",
        "reasonCode",
        "exceptionClass",
        "exceptionCategory",
        "routeMode",
        "gateMode",
        "toolsPolicy",
        "routingSource",
        "correlationMode",
    }
    digest_fields = {
        "requestDigest",
        "traceIdDigest",
        "modelAttemptDigest",
        "correlationDigest",
    }
    bool_fields = {
        "adkInvoked",
        "runnerAttempted",
        "modelCallAttempted",
        "toolsEnabled",
        "toolHostDispatchAllowed",
        "adkPrimitivesLoaderConfigured",
        "gate1aEgressCorrelationContextPresent",
        "gate1aProxyUrlConfigured",
        "egressCorrelationHeadersConfigured",
    }
    for key, value in payload.items():
        if key in string_fields and isinstance(value, str):
            safe_value = _safe_label_or_none(value)
            if safe_value is not None:
                safe_payload[key] = safe_value
            continue
        if key in digest_fields and isinstance(value, str) and _is_sha256_digest(value):
            safe_payload[key] = value
            continue
        if key in bool_fields and isinstance(value, bool):
            safe_payload[key] = value
            continue
        if key == "activeToolNames" and isinstance(value, (list, tuple)):
            tool_names = _public_safe_tool_names(value)
            if tool_names:
                safe_payload[key] = tool_names
            continue
        if key == "errorPreview" and isinstance(value, str):
            error_preview = _public_safe_error_preview_or_none(value)
            if error_preview is not None:
                safe_payload[key] = error_preview
            continue
        if key == "tracebackMarkers" and isinstance(value, (list, tuple)):
            traceback_markers = _public_safe_traceback_markers(value)
            if traceback_markers:
                safe_payload[key] = traceback_markers
    if "stage" not in safe_payload or "reasonCode" not in safe_payload:
        return None
    safe_payload["schemaVersion"] = "gate5b4c3.runnerErrorDiagnostic.v1"
    return safe_payload


def _public_safe_tool_names(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    safe_names: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if _SAFE_LABEL_RE.match(text) and text not in safe_names:
            safe_names.append(text)
    return safe_names


def _public_safe_error_preview_or_none(value: object) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text or len(text) > 256:
        return None
    if _RUNNER_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE.search(text):
        return None
    return text


def _public_safe_traceback_markers(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    safe_markers: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if _SAFE_LABEL_RE.match(text) and text not in safe_markers:
            safe_markers.append(text)
        if len(safe_markers) >= 12:
            break
    return safe_markers


def _fallback_only_scope_error(
    *,
    payload: Gate5BUserVisibleDeliveryReceiptPayload,
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
) -> str | None:
    if (
        payload.gate not in _FALLBACK_RECEIPT_SCOPE_GATES
        or payload.delivery_status != "fallback_served"
        or not payload.python_attempted
        or payload.python_counter_record_present
    ):
        return None
    if payload.selected_scope is None:
        return "selected_scope_required"
    if payload.selected_scope.selected_bot_digest != _sha256_digest(runtime.config.bot_id):
        return "selected_scope_mismatch"
    if (
        payload.selected_scope.selected_owner_user_id_digest
        != _sha256_digest(runtime.config.user_id)
    ):
        return "selected_scope_mismatch"
    if payload.selected_scope.environment != route_config.environment:
        return "selected_scope_mismatch"
    return None


async def _client_disconnected(
    request: Request,
    route_config: Gate5BUserVisibleChatRouteConfig,
) -> bool:
    if route_config.client_disconnected_probe is not None:
        value = route_config.client_disconnected_probe(request)
        if inspect.isawaitable(value):
            value = await value
        return bool(value)
    try:
        return bool(await request.is_disconnected())
    except Exception:
        return False


def _runner_incomplete_output_reason(value: object) -> str | None:
    text = _bounded_public_text(str(value or ""), max_chars=4096).strip()
    if not text:
        return None
    if _INCOMPLETE_RUNNER_OUTPUT_RE.search(text):
        return "runner_incomplete_output"
    return None


def _canary_gate_error(
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
) -> str | None:
    authority = runtime.config.authority
    if route_config.kill_switch_enabled is not False:
        return "python_disabled"
    if route_config.selected_bot_digest != _sha256_digest(runtime.config.bot_id):
        return "python_disabled"
    if route_config.selected_owner_user_id_digest != _sha256_digest(runtime.config.user_id):
        return "python_disabled"
    if not route_config.environment or route_config.environment not in route_config.environment_allowlist:
        return "python_disabled"
    if (
        runtime.config.gate8_readiness.enabled
        and _gate8_selected_authority_metadata(runtime) is None
    ):
        return "python_disabled"
    if (
        authority.user_visible_output_allowed is not True
        or authority.canary_routing_allowed is not True
    ):
        return "invalid_authority"
    for key in _FALSE_RUNTIME_AUTHORITY_KEYS:
        attr = _camel_to_snake(key).replace("writes", "write")
        if getattr(authority, attr) is not False:
            return "invalid_authority"
    return None


def gate5b_user_visible_chat_gate_active(runtime: OpenMagiRuntime) -> bool:
    route_config = _route_config(runtime)
    return route_config.enabled is True and _canary_gate_error(runtime, route_config) is None


def _model_attempt_digest(
    *,
    request_digest: str,
    provider: str,
    model: str,
    model_call_attempted: bool,
) -> str | None:
    if not model_call_attempted:
        return None
    return _sha256_digest(f"{request_digest}:{provider}:{model}:attempt:1")


def _collect_gate1a_observed_egress_evidence(
    *,
    runtime: OpenMagiRuntime,
    request_digest: str,
    model_attempt_digest: str | None,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
    gate8_ready: bool = False,
    model_call_attempted: bool,
    observed_window_start: str | None = None,
    observed_window_end: str | None = None,
) -> ObservedEgressEvidence | None:
    tool_bundle_ready = _route_tool_bundle_ready(gate1a_bundle)
    if not (tool_bundle_ready or gate8_ready) or not model_call_attempted:
        return None
    provider = get_observed_egress_evidence_provider(runtime)
    return provider.collect(
        request_digest=request_digest,
        model_attempt_digest=model_attempt_digest,
        observed_window_start=observed_window_start,
        observed_window_end=observed_window_end,
    )


def _first_party_recipe_pack_ids_from_payload(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, Mapping):
        return ()
    availability = payload.get("botScopedRecipeAvailability")
    if not isinstance(availability, Mapping):
        availability = payload.get("bot_scoped_recipe_availability")
    if not isinstance(availability, Mapping):
        return ()
    values = availability.get("availableRecipePackIds")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        values = availability.get("available_recipe_pack_ids")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return ()
    allowed = set(_FIRST_PARTY_HARNESS_RECIPE_PACK_IDS)
    selected: list[str] = []
    for value in values:
        if isinstance(value, str) and value in allowed and value not in selected:
            selected.append(value)
    return tuple(selected)


def _first_party_harness_families(pack_ids: Sequence[str]) -> tuple[str, ...]:
    ids = set(pack_ids)
    families: list[str] = []
    checks = (
        ("methodology", {"openmagi.agent-methodology", "openmagi.superpowers-compat"}),
        ("research", {"openmagi.research", "openmagi.web-acquisition"}),
        ("coding", {"openmagi.dev-coding"}),
        (
            "general_automation",
            {
                "openmagi.office-automation",
                "openmagi.spreadsheet-automation",
                "openmagi.document-review",
                "openmagi.lightweight-scripting",
            },
        ),
        ("memory", {"openmagi.memory-agentmemory"}),
        ("scheduler", {"openmagi.missions", "openmagi.scheduled-work"}),
        ("channel_delivery", {"openmagi.channel-delivery", "openmagi.artifact-delivery"}),
        ("browser", {"openmagi.browser-automation", "openmagi.web-acquisition"}),
    )
    for family, required in checks:
        if ids.intersection(required):
            families.append(family)
    return tuple(families)


def _bounded_tuple(values: Sequence[str], *, limit: int = 64) -> list[str]:
    return [str(value) for value in values[:limit]]


def _first_party_harness_metadata(
    *,
    payload: object,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
) -> dict[str, object] | None:
    pack_ids = _first_party_recipe_pack_ids_from_payload(payload)
    if not pack_ids:
        return None
    from magi_agent.recipes.kernel_recipe_packs import build_runtime_pack_registry

    registry = build_runtime_pack_registry()
    try:
        snapshot = AgentRecipeCompiler(registry).compile(
            ProfileResolutionRequest(
                recipePackConfig={"packs": {"enable": pack_ids}},
            )
        )
        plan = RecipeMaterializer.with_reliability_defaults().materialize(
            snapshot,
            modelProvider="google",
            modelLabel="gemini-3.5-flash",
        )
    except (ValidationError, ValueError, TypeError):
        return {
            "schemaVersion": "openmagi.firstPartyHarnessAdmission.v1",
            "status": "blocked",
            "reason": "first_party_harness_materialization_failed",
            "requestedPackCount": len(pack_ids),
            "selectedPackIds": [],
            "harnessFamilies": [],
        }
    toolhost_mode = "disabled"
    if _route_tool_bundle_full(gate1a_bundle):
        toolhost_mode = "selected_full_toolhost"
    elif _route_tool_bundle_readonly(gate1a_bundle):
        toolhost_mode = "shadow_readonly"
    active_toolhost = {
        "mode": toolhost_mode,
        "allowedToolNames": _route_tool_bundle_names(gate1a_bundle),
        "productionAttached": False,
    }
    return {
        "schemaVersion": "openmagi.firstPartyHarnessAdmission.v1",
        "status": "ready",
        "recipeSnapshotId": plan.recipe_snapshot_id,
        "selectedPackIds": list(plan.selected_pack_ids),
        "harnessFamilies": list(_first_party_harness_families(plan.selected_pack_ids)),
        "providerIntents": _bounded_tuple(plan.provider_intents),
        "toolIntents": _bounded_tuple(plan.tool_intents),
        "channelIntents": _bounded_tuple(plan.channel_intents),
        "artifactIntents": _bounded_tuple(plan.artifact_intents),
        "schedulerIntents": _bounded_tuple(plan.scheduler_intents),
        "evidenceRequirements": _bounded_tuple(plan.evidence_requirements),
        "approvalGates": _bounded_tuple(plan.approval_gates),
        "killSwitchRefs": _bounded_tuple(plan.kill_switch_refs),
        "rollbackRefs": _bounded_tuple(plan.rollback_refs),
        "liveAttachmentRefs": list(plan.live_attachment_refs),
        "attachmentFlags": {
            str(key): bool(value)
            for key, value in plan.attachment_flags.items()
        },
        "activeSelectedToolhost": active_toolhost,
    }


def _build_gate1a_egress_correlation_context(
    *,
    runtime: OpenMagiRuntime,
    request_digest: str,
    model_attempt_digest: str | None,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None,
    gate8_ready: bool = False,
) -> tuple[Gate1AEgressCorrelationContext | None, str | None]:
    tool_bundle_ready = _route_tool_bundle_ready(gate1a_bundle)
    if not (tool_bundle_ready or gate8_ready):
        return None, None
    if not _is_sha256_digest(request_digest) or not _is_sha256_digest(
        model_attempt_digest
    ):
        return None, None
    provider = get_observed_egress_evidence_provider(runtime)
    if (
        getattr(provider, "gate1a_egress_evidence_ready", False) is not True
        or getattr(provider, "evidence_source", "") != GATE1A_EGRESS_TELEMETRY_SOURCE
        or getattr(provider, "correlation_mode", "") != GATE1A_EGRESS_CORRELATION_MODE
    ):
        return None, None
    proxy_url = getattr(provider, "gate1a_proxy_url", None)
    if not isinstance(proxy_url, str) or not proxy_url.strip():
        return None, None
    try:
        return (
            Gate1AEgressCorrelationContext(
                request_digest=request_digest,
                correlation_digest=request_digest,
                model_attempt_digest=model_attempt_digest,
            ),
            proxy_url.strip(),
        )
    except ValueError:
        return None, None


def _python_ready_response(
    *,
    runtime: OpenMagiRuntime,
    content: str,
    event_count: int,
    adk_invoked: bool,
    runner_attempted: bool,
    model_call_attempted: bool,
    mocked_runner_invoked: bool,
    provider: str | None = None,
    model: str | None = None,
    counter_state: object | None = None,
    counter_status: str = "runner_completed",
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None = None,
    model_attempt_digest: str | None = None,
    observed_egress_evidence: ObservedEgressEvidence | None = None,
    public_events: Sequence[Mapping[str, object]] = (),
    research_first_metadata: Mapping[str, object] | None = None,
    first_party_harness_metadata: Mapping[str, object] | None = None,
    verifier_evidence_status: EgressVerifierStatus | None = None,
) -> JSONResponse:
    active_tools = _route_tool_bundle_names(gate1a_bundle)
    gate8_metadata = _gate8_selected_authority_metadata(runtime)
    gate8_ready = bool(gate8_metadata and gate8_metadata.get("readinessReady") is True)
    body: dict[str, object] = {
        "schemaVersion": "gate5b.userVisibleChatCompletion.v1",
        "status": "python_ready",
        "fallbackStatus": "none",
        "responseAuthority": "python",
        "runtime": runtime.config.runtime,
        "runtimeEngine": runtime.config.runtime_engine,
        "authority": _python_canary_authority(gate1a_bundle, gate8_ready=gate8_ready),
        "safety": _surface_safety(gate1a_bundle, gate8_ready=gate8_ready),
        "adk": {
            "available": runtime.adk_boundary.available,
            "invoked": adk_invoked,
        },
        "activeTools": active_tools,
        "runnerAttempted": runner_attempted,
        "modelCallAttempted": model_call_attempted,
        "modelAttemptCount": 1 if model_call_attempted else 0,
        "mockedRunnerInvoked": mocked_runner_invoked,
        "eventCount": event_count,
        "publicEvents": [
            dict(event)
            for event in public_events
            if isinstance(event, Mapping)
        ],
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
    }
    if provider is not None:
        body["provider"] = provider
    if model is not None:
        body["model"] = model
    if counter_state is not None and hasattr(counter_state, "model_dump"):
        body["counter"] = {
            "status": counter_status,
            "state": counter_state.model_dump(by_alias=True, mode="json"),
        }
    if _route_tool_bundle_ready(gate1a_bundle):
        body["tooling"] = _route_tooling_metadata(gate1a_bundle)
    if model_call_attempted and (
        _route_tool_bundle_ready(gate1a_bundle) or gate8_ready
    ):
        body.update(
            _gate1a_observed_egress_metadata(
                observed_egress_evidence=observed_egress_evidence,
                model_attempt_digest=model_attempt_digest,
            )
        )
    if gate8_ready and gate8_metadata is not None:
        body["gate"] = "gate8_selected_python_authority"
        body["gate8Readiness"] = gate8_metadata
    if research_first_metadata is not None:
        body["researchFirst"] = dict(research_first_metadata)
    if first_party_harness_metadata is not None:
        body["firstPartyHarness"] = dict(first_party_harness_metadata)
    # Egress critic gate signal (default-OFF). Only added to the body when the
    # gate ran AND produced a non-None status, so the off-state body is
    # byte-identical to before.
    if verifier_evidence_status is not None:
        body["verifierEvidenceStatus"] = verifier_evidence_status
    return JSONResponse(status_code=200, content=body)


def _python_canary_authority(
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None = None,
    *,
    gate8_ready: bool = False,
) -> dict[str, bool]:
    gate1a_ready = _route_tool_bundle_readonly(gate1a_bundle)
    full_toolhost_ready = _route_tool_bundle_full(gate1a_bundle)
    authority = {
        "userVisibleOutputAllowed": True,
        "canaryRoutingAllowed": True,
        **{key: False for key in _FALSE_RESPONSE_AUTHORITY_KEYS},
    }
    if gate1a_ready:
        authority["readOnlyToolDispatchAllowed"] = True
    if full_toolhost_ready:
        authority["toolDispatchAllowed"] = True
        authority["selectedWorkspaceMutationAllowed"] = True
        authority["productionWorkspaceMutationAllowed"] = False
        authority["bashCommandAllowed"] = "Bash" in _route_tool_bundle_names(gate1a_bundle)
    if gate8_ready:
        authority["readOnlyToolDispatchAllowed"] = False
        authority["backgroundTaskAllowed"] = False
        authority["selfImprovementAllowed"] = False
    return authority


def _surface_safety(
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None = None,
    *,
    gate8_ready: bool = False,
) -> dict[str, object]:
    gate1a_ready = _route_tool_bundle_readonly(gate1a_bundle)
    full_toolhost_ready = _route_tool_bundle_full(gate1a_bundle)
    safety: dict[str, object] = {
        "toolsActive": False,
        "memoryProviderActive": False,
        "browserActive": False,
        "workspaceMutationAllowed": False,
        "childExecutionAllowed": False,
        "missionRuntimeAllowed": False,
        "telegramDeliveryAllowed": False,
        "artifactChannelDeliveryAllowed": False,
        "evidenceBlockModeAllowed": False,
        "productionTranscriptWritesAllowed": False,
        "productionSseWritesAllowed": False,
        "productionDbWritesAllowed": False,
    }
    if gate1a_ready:
        safety.update(
            {
                "toolsActive": True,
                "readOnlyToolsActive": True,
                "toolHostMode": "shadow_readonly",
                "allowedReadOnlyTools": list(gate1a_bundle.exposed_tool_names),
                "writeMutationAllowed": False,
            }
        )
    if full_toolhost_ready:
        safety.update(
            {
                "toolsActive": True,
                "readOnlyToolsActive": False,
                "toolHostMode": "selected_full_toolhost",
                "allowedToolNames": _route_tool_bundle_names(gate1a_bundle),
                "selectedWorkspaceMutationAllowed": True,
                "productionWorkspaceMutationAllowed": False,
                "writeMutationAllowed": True,
                "bashCommandAllowed": "Bash" in _route_tool_bundle_names(gate1a_bundle),
            }
        )
    if gate8_ready:
        safety.update(
            {
                "readOnlyToolsActive": False,
                "toolHostMode": "disabled",
                "schedulerMutationAllowed": False,
                "backgroundTaskAllowed": False,
                "selfImprovementAllowed": False,
            }
        )
    return safety


def _disabled_surface_safety() -> dict[str, bool]:
    return {
        key: value
        for key, value in _surface_safety().items()
        if isinstance(value, bool)
    }


def _gate8_selected_authority_metadata(
    runtime: OpenMagiRuntime,
) -> dict[str, object] | None:
    gate8 = gate8_readiness_health_metadata(
        runtime.config.gate8_readiness,
        runtime.config.context_continuity,
        bot_id=runtime.config.bot_id,
        user_id=runtime.config.user_id,
        observed_egress=observed_egress_diagnostics(
            get_observed_egress_evidence_provider(runtime)
        ),
    )
    return gate8 if gate8.get("readinessReady") is True else None


def _route_tooling_metadata(
    bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
) -> dict[str, object]:
    if isinstance(bundle, Gate5BFullToolBundle):
        return _gate5b_full_tooling_metadata(bundle)
    return _gate1a_tooling_metadata(bundle)


def _gate1a_tooling_metadata(bundle: Gate1AReadOnlyToolBundle) -> dict[str, object]:
    attachment_flags = bundle.attachment_flags.model_dump(by_alias=True, mode="json")
    exposed = set(bundle.exposed_tool_names)
    forbidden = sorted(exposed.intersection(GATE1A_FORBIDDEN_TOOL_NAMES))
    return {
        "schemaVersion": "gate1a.readOnlyTooling.v1",
        "mode": "shadow_readonly",
        "toolsPolicy": "shadow_readonly",
        "allowedToolNames": list(bundle.exposed_tool_names),
        "forbiddenToolsExposed": forbidden,
        "receiptCount": bundle.host.counter.receipt_count,
        "routeAttached": attachment_flags["routeAttached"],
        "productionAttached": attachment_flags["productionAttached"],
        "attachmentFlags": attachment_flags,
        "sourceLedgerProjection": bundle.source_ledger_projection,
        "receiptLimits": {
            "maxToolCallsPerTurn": bundle.host.config.max_tool_calls_per_turn,
            "maxPerToolOutputBytes": bundle.host.config.max_per_tool_output_bytes,
            "maxAggregateOutputBytes": bundle.host.config.max_aggregate_output_bytes,
        },
    }


def _gate5b_full_tooling_metadata(bundle: Gate5BFullToolBundle) -> dict[str, object]:
    attachment_flags = bundle.attachment_flags.model_dump(by_alias=True, mode="json")
    exposed = set(bundle.exposed_tool_names)
    forbidden = sorted(
        name
        for name in exposed
        if name not in set(GATE5B_FULL_TOOLHOST_TOOL_NAMES)
    )
    return {
        "schemaVersion": "gate5b.selectedFullToolhost.v1",
        "mode": "selected_full_toolhost",
        "toolsPolicy": "selected_full_toolhost",
        "allowedToolNames": list(bundle.exposed_tool_names),
        "childRunner": child_runner_availability_metadata(
            legacy_child_execution_allowed=False,
            allowed_tool_names=bundle.exposed_tool_names,
        ),
        "forbiddenToolsExposed": forbidden,
        "receiptCount": bundle.host.counter.receipt_count,
        "routeAttached": attachment_flags["routeAttached"],
        "productionAttached": attachment_flags["productionAttached"],
        "workspaceRootDigest": bundle.workspace_root_digest,
        "attachmentFlags": attachment_flags,
        "receiptLimits": {
            "maxToolCallsPerTurn": bundle.host.config.max_tool_calls_per_turn,
            "maxPerToolOutputBytes": bundle.host.config.max_per_tool_output_bytes,
            "commandTimeoutMs": bundle.host.config.command_timeout_ms,
        },
    }


def _gate1a_observed_egress_metadata(
    *,
    observed_egress_evidence: ObservedEgressEvidence | None,
    model_attempt_digest: str | None,
) -> dict[str, object]:
    if observed_egress_evidence is None:
        metadata: dict[str, object] = {
            "egressEvidenceStatus": "missing_observed_egress_evidence",
        }
        if model_attempt_digest is not None:
            metadata["modelAttemptDigest"] = model_attempt_digest
        return metadata

    evidence = observed_egress_evidence.model_dump(by_alias=True, mode="json")
    provider_request_count = observed_egress_evidence.provider_request_count
    expected_max = (
        _GATE1A_MAX_PROVIDER_TUNNELS_PER_MODEL_ATTEMPT
        * max(provider_request_count, 1)
    )
    metadata = {
        "egressEvidenceStatus": "observed_egress_evidence_present",
        "observedEgressEvidence": evidence,
        "providerRequestCount": provider_request_count,
        "egressTunnelCount": observed_egress_evidence.egress_tunnel_count,
        "egressHostClasses": list(observed_egress_evidence.egress_host_classes),
        "egressDisciplineMode": _GATE1A_EGRESS_DISCIPLINE_MODE,
        "expectedEgressTunnelRange": {"min": 0, "max": expected_max},
        "egressEvidenceSource": observed_egress_evidence.evidence_source,
        "egressEvidenceRedactionStatus": observed_egress_evidence.redaction_status,
        "egressEvidenceDecisionReason": observed_egress_evidence.decision_reason,
        "egressWindowStartedAt": observed_egress_evidence.observed_window_start,
        "egressWindowEndedAt": observed_egress_evidence.observed_window_end,
    }
    correlation_digest = (
        observed_egress_evidence.correlation_digest
        or observed_egress_evidence.request_digest
    )
    if correlation_digest is not None:
        metadata["egressCorrelationDigest"] = correlation_digest
    if observed_egress_evidence.model_attempt_digest is not None:
        metadata["modelAttemptDigest"] = observed_egress_evidence.model_attempt_digest
    elif model_attempt_digest is not None:
        metadata["modelAttemptDigest"] = model_attempt_digest
    return metadata
