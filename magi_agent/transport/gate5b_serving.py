"""Gate5B user-visible serving engine, pure move out of
magi_agent/transport/chat_routes.py (PR-G5).

The hosted user-visible serving hot path: gate1a/gate5b config + bundle
builders, mocked and live chat runners, usage-receipt scheduling, egress
evidence correlation and the public gate-active predicate. Bodies are moved
verbatim (source order preserved). chat_routes re-imports every name so import
paths and object identity are preserved. This module name deliberately avoids
the magi_agent.transport.chat prefix so it stays outside the shadow canary
import-boundary denylist, and it depends downward only (chat_shared,
chat_authority, chat_routes_local, and original sources) never on chat_routes.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi_agent.memory.config import MemoryMode
from fastapi import Request
from fastapi.responses import JSONResponse
from magi_agent.config.env import is_egress_gate_enabled
from magi_agent.config.flags import flag_bool, flag_str
from magi_agent.evidence.gate1a_egress_correlation import GATE1A_EGRESS_CORRELATION_MODE, GATE1A_EGRESS_TELEMETRY_SOURCE, Gate1AEgressCorrelationContext
from magi_agent.evidence.observed_egress import ObservedEgressEvidence, get_observed_egress_evidence_provider
from magi_agent.gates.gate1a_readonly_tools import Gate1AReadOnlyToolBundle, Gate1AReadOnlyToolConfig, build_gate1a_readonly_tool_bundle
from magi_agent.gates.gate5b_full_toolhost import Gate5BFullToolBundle, Gate5BFullToolHostConfig, build_gate5b_full_toolhost_bundle
from magi_agent.introspection.egress_gate import EgressVerifierStatus
from magi_agent.recipes.compiler import AgentRecipeCompiler, ProfileResolutionRequest
from magi_agent.recipes.materializer import RecipeMaterializer
from magi_agent.research.research_first_canary import build_research_first_selected_response, research_first_selected_canary_active
from magi_agent.runtime.governed_turn import run_governed_turn
from magi_agent.runtime.hosted_runtime import build_hosted_runtime
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.runtime.public_events import tool_end_event, tool_progress_event, tool_start_event, turn_phase_event
from magi_agent.runtime.session_identity import _memory_mode_from_header
from magi_agent.shadow.gate5b4c3_live_runner_boundary import _gate1a_correlated_model_or_label, run_gate5b4c3_live_runner_boundary_async
from magi_agent.shadow.gate5b4c3_runner_input_adapter import build_gate5b4c3_runner_input
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import build_gate5b4c3_shadow_generation_diagnostic
from magi_agent.transport.active_turn import ACTIVE_TURNS, ActiveTurn, ActiveTurnClaim
from magi_agent.transport.chat_authority import _boundary_runner_error_diagnostic, _canary_gate_error, _chat_runner_error_diagnostic, _finish_counter_error, _gate8_selected_authority_metadata, _python_ready_response, _runner_incomplete_output_reason
from magi_agent.transport.chat_routes_local import _NoopChatSink
from magi_agent.transport.chat_shared import Gate5BUserVisibleChatRouteConfig, _bounded_public_text, _context_continuity_chat_diagnostic, _fallback_response, _is_sha256_digest, _local_chat_string, _reason_for_gate_error, _resolve_session_key, _route_config, _route_tool_bundle_full, _route_tool_bundle_names, _route_tool_bundle_readonly, _route_tool_bundle_ready, _sha256_digest, _shadow_generation_route_config
from magi_agent.transport.egress_critic import _maybe_run_egress_critic_gate
from magi_agent.transport.gate5b_governance import build_gate5b_control_plane_plugins, gate5b_governance_enabled, gate5b_pre_final_grounding_status
from magi_agent.transport.generation_request import _build_user_visible_generation_request, build_gate5b_user_visible_canary_runner_request, sanitize_gate5b_model_visible_identity_text
from magi_agent.transport.hosted_engine_result import collect_engine_to_boundary_result
from magi_agent.transport.hosted_turn_context import hosted_request_to_turn_context
from magi_agent.transport.usage_receipt_emit import emit_runtime_direct_usage_receipt, usage_receipt_enabled
from pathlib import Path
from pydantic import ValidationError


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
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
    # I-4 follow-up: hosted workspace root flows through the registry typed
    # reader. ``flag_str`` returns ``""`` (FlagSpec default) when the env is
    # unset or empty; the historical fallback to ``Path.cwd()`` is preserved
    # byte-identically (non-empty env wins, empty/unset falls back to cwd).
    configured = flag_str("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT")
    if configured:
        return Path(configured)
    return Path.cwd()


def _gate1a_workspace_root() -> Path:
    # I-4 follow-up: see ``_gate5b_full_toolhost_workspace_root`` above; same
    # registry-backed semantics, parallel hosted-scope FlagSpec.
    configured = flag_str("CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT")
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
    active_turn_claim: ActiveTurnClaim | None = None
    if active_session_id:
        try:
            # Claim the (session, turn) slot; ``try_register`` refuses to clobber
            # a live turn already holding the key (no last-writer-wins). A None
            # return means the slot is held — leave it untouched.
            active_turn_claim = ACTIVE_TURNS.try_register(
                ActiveTurn(
                    session_id=active_session_id,
                    turn_id=active_turn_id,
                    cancel=asyncio.Event(),
                    sink=_NoopChatSink(),  # type: ignore[arg-type]
                    task=asyncio.current_task(),
                )
            )
        except Exception:  # noqa: BLE001 — registration must never break a turn
            active_turn_claim = None
    try:
        # MAGI_HOSTED_GOVERNED_TURN_ENABLED (default-OFF, hosted scope):
        # When ON, route through run_governed_turn → MagiEngineDriver (Phase 2
        # flip) instead of gate5b4c3._invoke_async_turn. The result shim
        # (collect_engine_to_boundary_result) produces a wire-compatible
        # Gate5B4C3LiveRunnerBoundaryResult so all downstream code is unchanged.
        # Flag-OFF (default) = byte-identical to today: the legacy boundary call
        # is taken without any additional overhead.
        if flag_bool("MAGI_HOSTED_GOVERNED_TURN_ENABLED"):
            # 1. Build the runner input (input adapter + policy checks).
            runner_input_result = build_gate5b4c3_runner_input(generation)
            if runner_input_result.status != "accepted" or runner_input_result.runner_input is None:
                # Input adapter dropped the request — fall through to the legacy
                # boundary so its error-result path handles the response exactly
                # as today (the boundary emits the same error result for drops).
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
            else:
                runner_input = runner_input_result.runner_input
                adk_tools = gate1a_bundle.tools if gate1a_bundle.status == "ready" else ()
                # 2. Build model (gate1a-correlated — caller-responsibility per PR1).
                model_for_agent = _gate1a_correlated_model_or_label(
                    provider_label=runner_input.provider_label,
                    model_label=runner_input.model_label,
                    context=gate1a_egress_context,
                    proxy_url=gate1a_egress_proxy_url,
                )
                # 3. Build generate_content_config via the ADK primitives loader
                # (matches gate5b4c3's own construction path).
                primitives = route_config.adk_primitives_loader()
                generate_content_config = primitives.GenerateContentConfig(
                    maxOutputTokens=runner_input.max_output_tokens,
                )
                # 4. Assemble HostedRuntime (PR1).
                hosted_rt = build_hosted_runtime(
                    adk_primitives_loader=route_config.adk_primitives_loader,
                    adk_tools=adk_tools,
                    model=model_for_agent,
                    instruction=runner_input.system_instruction,
                    generate_content_config=generate_content_config,
                    control_plane_plugins=control_plane_plugins,
                    public_event_sink=governance_event_sink,
                    app_name="openmagi-hosted-governed-turn",
                )
                # 5. Build TurnContext (PR2).
                ctx = hosted_request_to_turn_context(generation)
                # 6. Reuse the already-built diagnostic (built earlier in the function;
                # same object the legacy boundary would compute). Variable name: diagnostic.
                # 7. Drive the turn via run_governed_turn.
                started_at_monotonic = time.monotonic()
                # Cancel event: the active_turn_claim holds the registered turn;
                # look it up to get the cooperative cancel handle (None is safe —
                # task-level CancelledError still aborts the turn).
                cancel_event: asyncio.Event | None = None
                if active_turn_claim is not None and active_session_id:
                    registered = ACTIVE_TURNS.get(active_session_id, active_turn_id)
                    cancel_event = registered.cancel if registered is not None else None
                event_stream = run_governed_turn(ctx, runtime=hosted_rt, cancel=cancel_event)
                # 8. Collect result (PR3).
                boundary_result = await collect_engine_to_boundary_result(
                    generation=generation,
                    config=generation_config,
                    diagnostic=diagnostic,  # the already-built diagnostic (pre-try-block)
                    event_stream=event_stream,
                    started_at_monotonic=started_at_monotonic,
                    timeout_ms=getattr(generation.budgets, "python_runner_timeout_ms", 0),
                )
        else:
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
        # interrupt-addressable registry. Release via the claim (owner-guarded so
        # a NEWER turn that already replaced this one under the same session is
        # not evicted). Fail-soft: never let teardown break the response.
        if active_turn_claim is not None:
            try:
                ACTIVE_TURNS.unregister(active_turn_claim)
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
