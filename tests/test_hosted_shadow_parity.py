"""Governed-path scenario fixtures - legacy comparison retired in P5-M1b.

Prior to P5-M1b this file drove BOTH the legacy gate5b4c3 boundary path and the
governed-turn path over identical scenarios and asserted response / event parity.
The legacy engine (``Gate5B4C3LiveRunnerBoundary`` /
``run_gate5b4c3_live_runner_boundary_async`` / ``_build_user_message_parts``) was
deleted in P5-M1b.  All ``test_shadow_parity_*`` and ``test_divergence_*`` tests
that drove the legacy branch have been retired - their coverage of the
governed-side event ordering (tool_start / tool_end / tool_progress / text_delta),
native-tool round-trips, and text-only status / events is provided by:

* tests/test_gate5b_serving_observability.py
* tests/test_gate5b_governance_wiring.py
* tests/test_streaming_chat_route.py

This module now only provides governed-path scenario fixtures and helpers used
by tests/test_hosted_governed_flip_readiness.py:

- ``_config_full_toolhost`` / ``_request_full_toolhost`` - request / config builders
- ``_drive_governed_turn_path`` - async helper that runs run_governed_turn and
  collects (public_events, boundary_result) for a given engine runner
- ``_assert_boundary_result_parity`` / ``_assert_public_event_parity`` - shared
  assertion helpers (now used only cross-file from flip_readiness)
- ``_make_tool_then_final_legacy_primitives`` - primitives factory used by
  flip_readiness test helpers that still need the gate5b4c3 ADK primitives shape
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pytest

from magi_agent.cli.headless import drain
from magi_agent.runtime.governed_turn import run_governed_turn
from magi_agent.runtime.hosted_runtime import build_hosted_runtime
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveAdkPrimitives,
    Gate5B4C3LiveRunnerBoundaryResult,
)
from magi_agent.shadow.gate5b4c3_runner_input_adapter import build_gate5b4c3_runner_input
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationDiagnostic,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
)
from magi_agent.transport.hosted_engine_result import collect_engine_to_boundary_result
from magi_agent.transport.hosted_turn_context import hosted_request_to_turn_context
from tests.support.gate5b4c3_fakes import (
    _FakeAgent,
    _FakeContent,
    _FakeGenerateContentConfig,
    _FakePart,
    _FakeRunner,
    _FakeSessionService,
    _FunctionCallOnlyEvent,
    _FunctionCallThenFinalRunner,
    _ManualCalculationTool,
)
from tests.support.engine_fakes import MockRunner, call_event, response_event, text_event


# ---------------------------------------------------------------------------
# Shared request / config fixtures
# ---------------------------------------------------------------------------

BOT_DIGEST = "sha256:" + "a" * 64
OWNER_DIGEST = "sha256:" + "b" * 64
TURN_DIGEST = "sha256:" + "c" * 64
REQUEST_DIGEST = "sha256:" + "d" * 64
TRACE_DIGEST = "sha256:" + "e" * 64
SESSION_DIGEST = "sha256:" + "f" * 64
SANITIZED_DIGEST = "sha256:" + "1" * 64


def _base_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
        "shadowGenerationId": "shadow_parity_001",
        "requestIdDigest": REQUEST_DIGEST,
        "traceIdDigest": TRACE_DIGEST,
        "createdAt": 1779200000000,
        "selection": {
            "botIdDigest": BOT_DIGEST,
            "ownerUserIdDigest": OWNER_DIGEST,
            "environment": "production",
            "selectedTarget": "gate5b_selected_bot",
            "sessionKeyDigest": SESSION_DIGEST,
        },
        "turn": {
            "turnId": "turn_parity_001",
            "turnDigest": TURN_DIGEST,
            "sanitizedCurrentTurnText": "Parity test prompt.",
            "sanitizedInputTextDigest": SANITIZED_DIGEST,
            "channelName": "app_channel",
            "tsResponseCorrelationId": "ts_corr_parity_001",
        },
        "modelRouting": {
            "routingSource": "per_turn_injected",
            "providerLabel": "anthropic",
            "modelLabel": "claude-3-5-sonnet-latest",
            "routerDecisionDigest": "sha256:" + "2" * 64,
            "routingProfileDigest": "sha256:" + "3" * 64,
            "botConfigModelDigest": "sha256:" + "4" * 64,
            "shadowCredentialRef": "server-shadow-ref",
            "credentialRefSource": "server_config",
            "temperature": 0.2,
            "maxOutputTokens": 512,
        },
        "recipeProfile": {
            "recipeId": "office-assistant",
            "recipeVersion": "2026-05-19",
            "profileId": "selected-bot-shadow",
            "profileVersion": "v1",
            "runtimeEngine": "adk-python",
            "toolsPolicy": "disabled",
            "memoryMode": "disabled",
            "sourceAuthority": "current_turn_only",
        },
        "policy": {
            "typeScriptResponseAuthority": True,
            "pythonDiagnosticOnly": True,
            "outputIsolation": "local_diagnostic_only",
            "toolsDisabled": True,
            "toolHostDispatchAllowed": False,
            "memoryProviderCallsAllowed": False,
            "memoryWritesAllowed": False,
            "promptMemoryInjectionAllowed": False,
            "workspaceMutationAllowed": False,
            "childExecutionAllowed": False,
            "missionRuntimeAllowed": False,
            "evidenceBlockModeAllowed": False,
        },
        "budgets": {},
        "redaction": {
            "sanitizerId": "chat-proxy-sanitizer",
            "sanitizerVersion": "v1",
            "policyId": "gate5b4c3-redaction",
            "status": "passed",
            "redactedAt": 1779200000001,
            "redactedByteCount": 47,
            "forbiddenFieldScan": "passed",
            "sanitizedPayloadDigest": SANITIZED_DIGEST,
        },
        "authority": {},
    }
    base.update(overrides)
    return base


def _request_text_only() -> Gate5B4C3ShadowGenerationRequest:
    """Simple text request (no tools)."""
    return Gate5B4C3ShadowGenerationRequest.model_validate(_base_payload())


def _request_full_toolhost() -> Gate5B4C3ShadowGenerationRequest:
    """Selected full toolhost request for manual tool scenarios."""
    return Gate5B4C3ShadowGenerationRequest.model_validate(
        _base_payload(
            recipeProfile={
                **_base_payload()["recipeProfile"],  # type: ignore[arg-type]
                "toolsPolicy": "selected_full_toolhost",
            },
            policy={
                **_base_payload()["policy"],  # type: ignore[arg-type]
                "toolsDisabled": False,
                "toolHostDispatchAllowed": True,
            },
        )
    )


def _config_text_only() -> Gate5B4C3ShadowGenerationConfig:
    return Gate5B4C3ShadowGenerationConfig(
        enabled=True,
        killSwitchActive=False,
        capStateInitialized=True,
        providerProjectSpendControlsVerified=True,
        selectedBotDigest=BOT_DIGEST,
        trustedOwnerUserIdDigest=OWNER_DIGEST,
        environment="production",
        allowedProviderLabels=("anthropic",),
        allowedModelLabels=("claude-3-5-sonnet-latest",),
        allowedModelRoutes=("anthropic:claude-3-5-sonnet-latest",),
        allowedShadowCredentialRefs=("server-shadow-ref",),
    )


def _config_full_toolhost() -> Gate5B4C3ShadowGenerationConfig:
    return _config_text_only()


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def _normalize_duration(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace volatile durationMs with sentinel - matches wire_profile_parity helper."""
    result = []
    for evt in events:
        evt = dict(evt)
        if "durationMs" in evt:
            evt["durationMs"] = "<normalized>"
        result.append(evt)
    return result


def _normalize_input_preview(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip ``input_preview`` from tool_start events."""
    result = []
    for evt in events:
        if evt.get("type") == "tool_start" and "input_preview" in evt:
            evt = {k: v for k, v in evt.items() if k != "input_preview"}
        result.append(evt)
    return result


def _filter_turn_phase(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove turn_phase events."""
    return [e for e in events if e.get("type") != "turn_phase"]


def _filter_lifecycle(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove engine-only lifecycle events."""
    lifecycle_types = {"turn_start", "turn_end", "thinking_delta", "output_continuation"}
    return [e for e in events if e.get("type") not in lifecycle_types]


def _comparable_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize events for parity comparison: strip turn_phase, lifecycle, durationMs, input_preview."""
    filtered = _filter_turn_phase(events)
    filtered = _filter_lifecycle(filtered)
    filtered = _normalize_duration(filtered)
    filtered = _normalize_input_preview(filtered)
    return filtered


# ---------------------------------------------------------------------------
# Governed-turn path driver
# ---------------------------------------------------------------------------


async def _drive_governed_turn_path(
    request: Gate5B4C3ShadowGenerationRequest,
    config: Gate5B4C3ShadowGenerationConfig,
    *,
    engine_runner: object,
    adk_tools: tuple = (),
    no_tool_finalizer: object | None = None,
) -> tuple[list[dict[str, Any]], Gate5B4C3LiveRunnerBoundaryResult]:
    """Drive the governed-turn path and return (public_events, result).

    This path:
    1. Builds a TurnContext from the generation request.
    2. Builds a HostedRuntime wrapping the engine-compatible fake runner.
    3. Runs run_governed_turn(ctx, runtime=rt).
    4. Drains the event stream to collect RuntimeEvent payloads as public_events.
    5. Builds the boundary result via collect_engine_to_boundary_result.

    NOTE: public events in the governed-turn path flow through RuntimeEvent.payload
    objects yielded by run_turn_stream -- NOT through the event_sink kwarg passed to
    build_hosted_runtime (which is the MagiEngineDriver observability sink and has
    a different 3-argument signature: (payload, session_id, turn_id)).  We capture
    events by draining the stream manually here and passing the same collected
    events to collect_engine_to_boundary_result.
    """
    from magi_agent.cli.contracts import RuntimeEvent, EngineResult
    from magi_agent.cli.headless import drain as _drain

    # Build runner_input to extract model/instruction/config from the request.
    runner_input_result = build_gate5b4c3_runner_input(request)
    assert runner_input_result.status == "accepted" and runner_input_result.runner_input is not None, (
        f"runner_input_result not accepted: {runner_input_result.reason}"
    )
    runner_input = runner_input_result.runner_input

    # Build a fake primitives loader wrapping the engine runner.
    # build_hosted_runtime calls primitives.Agent(...), primitives.Runner(...), etc.
    # We supply fakes that delegate runner construction to engine_runner.
    _engine_runner = engine_runner

    class _EngineRunnerWrapper:
        """When build_hosted_runtime calls primitives.Runner(**kwargs), return engine_runner."""
        def __new__(cls, **_kwargs: object) -> object:  # type: ignore[misc]
            return _engine_runner

    def _primitives_loader() -> Gate5B4C3LiveAdkPrimitives:
        return Gate5B4C3LiveAdkPrimitives(
            Agent=_FakeAgent,
            Runner=_EngineRunnerWrapper,
            InMemorySessionService=_FakeSessionService,
            Content=_FakeContent,
            Part=_FakePart,
            GenerateContentConfig=_FakeGenerateContentConfig,
        )

    # Resolve model label -- gate5b4c3 wraps anthropic models in CacheAwareClaude;
    # for the governed-turn path we pass the raw model label since the engine
    # handles model resolution internally.  Use the raw label here (no cache wrapper
    # for parity test -- the fake runner ignores the model field entirely).
    model_label = runner_input.model_label

    hosted_rt = build_hosted_runtime(
        adk_primitives_loader=_primitives_loader,
        adk_tools=list(adk_tools),
        model=model_label,
        instruction=runner_input.system_instruction,
        generate_content_config=_FakeGenerateContentConfig(
            maxOutputTokens=runner_input.max_output_tokens
        ),
        control_plane_plugins=(),
        public_event_sink=None,  # event_sink is MagiEngineDriver observability sink (3-arg);
                                 # public events are captured from RuntimeEvent.payload below
        no_tool_finalizer=no_tool_finalizer,
    )

    ctx = hosted_request_to_turn_context(request)
    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(request, config=config)

    started_at = time.monotonic()

    # Drain the stream manually to capture RuntimeEvent payloads as public_events.
    # run_governed_turn yields RuntimeEvent items + a terminal EngineResult.
    # collect_engine_to_boundary_result needs the same stream -- we reproduce it
    # from the drained events using an async generator.
    raw_stream = run_governed_turn(ctx, runtime=hosted_rt)
    drained_events, terminal = await _drain(raw_stream)  # type: ignore[arg-type]

    # Extract public events: RuntimeEvent objects carry a .payload dict.
    public_events: list[dict[str, Any]] = [
        dict(evt.payload)  # type: ignore[union-attr]
        for evt in drained_events
        if isinstance(evt, RuntimeEvent)
    ]

    # Re-create the stream for collect_engine_to_boundary_result from drained items.
    async def _replay_stream():  # type: ignore[return]
        for evt in drained_events:
            yield evt
        yield terminal

    result = await collect_engine_to_boundary_result(
        generation=request,
        config=config,
        diagnostic=diagnostic,
        event_stream=_replay_stream(),  # type: ignore[arg-type]
        started_at_monotonic=started_at,
        timeout_ms=runner_input.runner_timeout_ms,
    )
    return public_events, result


# ---------------------------------------------------------------------------
# Shared assertion helpers
# ---------------------------------------------------------------------------


def _assert_boundary_result_parity(
    result_a: Gate5B4C3LiveRunnerBoundaryResult,
    result_b: Gate5B4C3LiveRunnerBoundaryResult,
    *,
    scenario: str,
    allow_event_count_offset: bool = True,
) -> None:
    """Assert that both boundary results agree on the critical fields."""
    assert result_a.status == result_b.status, (
        f"[{scenario}] status mismatch: legacy={result_a.status!r}, governed={result_b.status!r}"
    )
    assert result_a.selected_provider == result_b.selected_provider, (
        f"[{scenario}] selected_provider mismatch: "
        f"legacy={result_a.selected_provider!r}, governed={result_b.selected_provider!r}"
    )
    assert result_a.selected_model == result_b.selected_model, (
        f"[{scenario}] selected_model mismatch: "
        f"legacy={result_a.selected_model!r}, governed={result_b.selected_model!r}"
    )
    assert result_a.output_text_internal == result_b.output_text_internal, (
        f"[{scenario}] output_text_internal mismatch:\n"
        f"  legacy:   {result_a.output_text_internal!r}\n"
        f"  governed: {result_b.output_text_internal!r}"
    )
    # event_count: documented offset-acceptable -- both must be >= 1 for success paths.
    # gate5b4c3 counts ADK stream events; engine counts RuntimeEvent bridge objects.
    if not allow_event_count_offset:
        assert result_a.event_count == result_b.event_count, (
            f"[{scenario}] event_count mismatch: "
            f"legacy={result_a.event_count}, governed={result_b.event_count}"
        )
    else:
        assert result_a.event_count >= 1, f"[{scenario}] legacy event_count must be >= 1"
        assert result_b.event_count >= 1, f"[{scenario}] governed event_count must be >= 1"


def _assert_public_event_parity(
    events_a: list[dict[str, Any]],
    events_b: list[dict[str, Any]],
    *,
    scenario: str,
    event_types: set[str] | None = None,
) -> None:
    """Assert comparable public events are equal between both paths.

    Applies _comparable_events normalisation (strips turn_phase, lifecycle,
    normalizes durationMs) then optionally filters to specific event types.
    """
    norm_a = _comparable_events(events_a)
    norm_b = _comparable_events(events_b)
    if event_types is not None:
        norm_a = [e for e in norm_a if e.get("type") in event_types]
        norm_b = [e for e in norm_b if e.get("type") in event_types]
    assert norm_a == norm_b, (
        f"[{scenario}] public event parity failed.\n"
        f"  legacy ({len(norm_a)} events):   {norm_a}\n"
        f"  governed ({len(norm_b)} events): {norm_b}"
    )


# ---------------------------------------------------------------------------
# gate5b4c3 ADK primitives factory - used by flip_readiness helpers
# ---------------------------------------------------------------------------


def _make_tool_then_final_legacy_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _FunctionCallThenFinalRunner.created_kwargs = {}
    _FunctionCallThenFinalRunner.run_kwargs = {}
    _FunctionCallThenFinalRunner.calls = []
    _FunctionCallThenFinalRunner.event_factory = _FunctionCallOnlyEvent
    _ManualCalculationTool.calls = []
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_FunctionCallThenFinalRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )
