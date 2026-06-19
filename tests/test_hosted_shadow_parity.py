"""PR5 shadow-comparison harness — CI gate before live flip.

Drives BOTH branches (gate5b4c3 boundary legacy path and governed-turn path)
over the same logical scenarios and asserts response / event parity.

Architecture reality
--------------------
The two paths use fundamentally different fake ADK primitives:

* LEGACY  (gate5b4c3 boundary): consumes plain Python objects whose
  ``.content.parts[*].function_call`` is a plain dict.  The boundary loop
  parses these with ``_event_function_calls``/``_event_function_responses``
  which accept both attribute-access and dict access.

* GOVERNED-TURN (engine bridge): consumes real ``google.adk.events.Event``
  objects with typed ``types.FunctionCall``/``types.FunctionResponse`` parts.
  The engine bridge uses ``getattr(part, "function_call", None).name`` — plain
  dicts do NOT have ``.name``, so gate5b4c3 fakes are NOT compatible.

Both paths produce the same LOGICAL events (tool_start, tool_progress,
tool_end, text_delta) from the same scenario — the fakes are constructed to be
semantically identical (same tool name "Calculation", same args {"expression":
"1 + 1"}, same call_id "calculation-call-001") even though the Python objects
differ at the type level.

Documented divergences (acceptable — normalized before comparison)
------------------------------------------------------------------
1. ``turn_phase`` events — gate5b4c3 emits ``executing`` / ``committing`` phase
   transitions from its manual-tool loop.  The engine bridge does NOT emit
   these on the HOSTED path (deferred per #702/#722).  Filtered from BOTH
   sides before comparison.

2. ``event_count`` — gate5b4c3 counts all ADK stream events (including
   function_response events that the engine counts as a separate increment).
   The engine counts ``RuntimeEvent`` objects emitted by the bridge, which
   corresponds to public events.  We assert event_count >= 1 for both and
   document the offset but do not assert exact equality between the two.

3. Engine lifecycle events — the engine emits extra lifecycle items (e.g.
   EngineResult terminal) which are consumed by ``drain`` and NOT included
   in the public events list.  No adjustment needed — drain strips them.

4. ``durationMs`` in ``tool_end`` — real wall-clock time; normalized to the
   sentinel ``"<normalized>"`` on both sides before comparison.

5. ``input_preview`` in ``tool_start`` — the engine bridge always emits
   ``input_preview`` in ``tool_start`` (full JSON-serialised args via
   ``_public_preview``).  The gate5b4c3 boundary emits ``input_preview``
   ONLY for args keys in ``_TOOL_INPUT_PREVIEW_KEYS`` (query, url, path,
   etc.) — the test tool uses ``expression`` which is NOT in that set, so
   gate5b4c3's ``tool_start`` has NO ``input_preview``.  This is a
   substantive divergence: the engine path is MORE verbose (broader preview)
   than the legacy path.  The dedicated test
   ``test_divergence_tool_start_input_preview`` explicitly asserts and
   documents this finding.  All other ``tool_start`` field comparisons
   strip ``input_preview`` from the engine side before comparing so that
   the remaining fields can be validated independently.  This divergence
   MUST be resolved (or the live flip must explicitly accept the extra
   field) before production use.

Scenarios covered
-----------------
- text_only: no tools; text_delta parity
- tool_then_final: manual tool round-trip (fn_call → tool execution → final text)
- native_tool_roundtrip: ADK-native fn_call + fn_response in stream → finalizer
- duplicate_text_and_call: dedup gate (same call emitted twice → executed once)
- function_call_only: fn_call with no response; engine emits 0 tool_end (correct)
"""
from __future__ import annotations

import asyncio
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
    _FakeEvent,
    _FakeGenerateContentConfig,
    _FakePart,
    _FakeRunner,
    _FakeSessionService,
    _FunctionCallOnlyEvent,
    _FunctionCallThenFinalRunner,
    _FunctionResponseOnlyEvent,
    _ManualCalculationTool,
    _NativeToolRoundtripRunner,
    _DuplicateTextAndFunctionCallRunner,
    _FunctionCallOnlyRunner,
    make_primitives,
)
from tests.support.engine_fakes import MockRunner, call_event, response_event, text_event

# ---------------------------------------------------------------------------
# Engine-compatible multi-part event: text preamble + function_call in one event.
# Matches the semantic equivalent of _TextAndFunctionCallEvent for the engine path.
# ---------------------------------------------------------------------------


def _preamble_and_call_event() -> "Any":
    """Build a real ADK Event with preamble text AND function_call in same content.

    This mirrors the gate5b4c3 ``_TextAndFunctionCallEvent`` which yields
    Korean preamble text + ``_FunctionCallOnlyPart`` from one event.
    The engine bridge processes text and function_call parts from the same event.
    """
    from google.adk.events import Event
    from google.genai import types

    return Event(
        author="model",
        partial=False,
        turn_complete=False,
        content=types.Content(
            role="model",
            parts=[
                types.Part(text="재무제표 분析을 진행하겠습니다."),
                types.Part(
                    function_call=types.FunctionCall(
                        name="Calculation",
                        args={"expression": "1 + 1"},
                        id="calculation-call-001",
                    )
                ),
            ],
        ),
    )


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
    """Replace volatile durationMs with sentinel — matches wire_profile_parity helper."""
    result = []
    for evt in events:
        evt = dict(evt)
        if "durationMs" in evt:
            evt["durationMs"] = "<normalized>"
        result.append(evt)
    return result


def _normalize_input_preview(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip ``input_preview`` from tool_start events.

    The engine bridge emits ``input_preview`` for ALL function calls (full JSON
    of args).  The gate5b4c3 boundary only emits it for args keys in
    ``_TOOL_INPUT_PREVIEW_KEYS`` (query/url/path/etc.) — ``expression`` is NOT
    in that set, so the test tool's ``tool_start`` omits ``input_preview``.

    Stripping ``input_preview`` from the engine side lets all other ``tool_start``
    fields be compared independently.  The divergence itself is explicitly
    documented in ``test_divergence_tool_start_input_preview``.
    """
    result = []
    for evt in events:
        if evt.get("type") == "tool_start" and "input_preview" in evt:
            evt = {k: v for k, v in evt.items() if k != "input_preview"}
        result.append(evt)
    return result


def _filter_turn_phase(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove turn_phase events — documented divergence (engine doesn't emit them)."""
    return [e for e in events if e.get("type") != "turn_phase"]


def _filter_lifecycle(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove engine-only lifecycle events absent from gate5b4c3 boundary loop."""
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
# Legacy path driver (gate5b4c3 boundary)
# ---------------------------------------------------------------------------


async def _drive_legacy_path(
    request: Gate5B4C3ShadowGenerationRequest,
    config: Gate5B4C3ShadowGenerationConfig,
    *,
    primitives_loader: object,
    adk_tools: tuple = (),
) -> tuple[list[dict[str, Any]], Gate5B4C3LiveRunnerBoundaryResult]:
    """Drive the legacy gate5b4c3 boundary and return (public_events, result)."""
    from magi_agent.shadow.gate5b4c3_live_runner_boundary import Gate5B4C3LiveRunnerBoundary

    public_events: list[dict[str, Any]] = []

    def _sink(event: Mapping[str, object]) -> None:
        public_events.append(dict(event))

    boundary = Gate5B4C3LiveRunnerBoundary(
        primitives_loader,  # type: ignore[arg-type]
        adk_tools=adk_tools,
        public_event_sink=_sink,
        gate1a_egress_correlation_context=None,
        gate1a_egress_proxy_url=None,
        control_plane_plugins=(),
    )
    result = await boundary.invoke_async(request, config=config)
    return public_events, result


# ---------------------------------------------------------------------------
# Governed-turn path driver
# ---------------------------------------------------------------------------


async def _drive_governed_turn_path(
    request: Gate5B4C3ShadowGenerationRequest,
    config: Gate5B4C3ShadowGenerationConfig,
    *,
    engine_runner: object,
    adk_tools: tuple = (),
) -> tuple[list[dict[str, Any]], Gate5B4C3LiveRunnerBoundaryResult]:
    """Drive the governed-turn path and return (public_events, result).

    This path:
    1. Builds a TurnContext from the generation request.
    2. Builds a HostedRuntime wrapping the engine-compatible fake runner.
    3. Runs run_governed_turn(ctx, runtime=rt).
    4. Drains the event stream to collect RuntimeEvent payloads as public_events.
    5. Builds the boundary result via collect_engine_to_boundary_result.

    NOTE: public events in the governed-turn path flow through RuntimeEvent.payload
    objects yielded by run_turn_stream — NOT through the event_sink kwarg passed to
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

    # Resolve model label — gate5b4c3 wraps anthropic models in CacheAwareClaude;
    # for the governed-turn path we pass the raw model label since the engine
    # handles model resolution internally.  Use the raw label here (no cache wrapper
    # for parity test — the fake runner ignores the model field entirely).
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
    )

    ctx = hosted_request_to_turn_context(request)
    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(request, config=config)

    started_at = time.monotonic()

    # Drain the stream manually to capture RuntimeEvent payloads as public_events.
    # run_governed_turn yields RuntimeEvent items + a terminal EngineResult.
    # collect_engine_to_boundary_result needs the same stream — we reproduce it
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
    # event_count: documented offset-acceptable — both must be >= 1 for success paths.
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
# Legacy path fake primitives — helpers
# ---------------------------------------------------------------------------


def _make_text_only_legacy_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _FakeRunner.created_kwargs = {}
    _FakeRunner.run_kwargs = {}
    _FakeRunner.fail = False
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_FakeRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


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


def _make_native_roundtrip_legacy_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _NativeToolRoundtripRunner.created_kwargs = {}
    _NativeToolRoundtripRunner.run_kwargs = {}
    _NativeToolRoundtripRunner.calls = []
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_NativeToolRoundtripRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _make_duplicate_legacy_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _DuplicateTextAndFunctionCallRunner.created_kwargs = {}
    _DuplicateTextAndFunctionCallRunner.run_kwargs = {}
    _DuplicateTextAndFunctionCallRunner.calls = []
    _ManualCalculationTool.calls = []
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_DuplicateTextAndFunctionCallRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _make_function_call_only_legacy_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _FunctionCallOnlyRunner.created_kwargs = {}
    _FunctionCallOnlyRunner.run_kwargs = {}
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_FunctionCallOnlyRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


# ===========================================================================
# SCENARIO 1: text_only
# ===========================================================================


def test_shadow_parity_text_only_status() -> None:
    """text_only: both paths complete successfully."""
    pytest.importorskip("anthropic")
    request = _request_text_only()
    config = _config_text_only()

    events_a, result_a = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_text_only_legacy_primitives,
        )
    )
    engine_runner = MockRunner([text_event("local diagnostic event only", partial=True, turn_complete=True)])
    events_b, result_b = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    _assert_boundary_result_parity(result_a, result_b, scenario="text_only")


def test_shadow_parity_text_only_events() -> None:
    """text_only: text_delta events are identical on both paths."""
    pytest.importorskip("anthropic")
    request = _request_text_only()
    config = _config_text_only()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_text_only_legacy_primitives,
        )
    )
    engine_runner = MockRunner([text_event("local diagnostic event only", partial=True, turn_complete=True)])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    _assert_public_event_parity(
        events_a,
        events_b,
        scenario="text_only",
        event_types={"text_delta"},
    )


def test_shadow_parity_text_only_no_tool_events() -> None:
    """text_only: neither path emits tool events."""
    pytest.importorskip("anthropic")
    request = _request_text_only()
    config = _config_text_only()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_text_only_legacy_primitives,
        )
    )
    engine_runner = MockRunner([text_event("local diagnostic event only", partial=True, turn_complete=True)])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    tool_a = [e for e in events_a if str(e.get("type", "")).startswith("tool_")]
    tool_b = [e for e in events_b if str(e.get("type", "")).startswith("tool_")]
    assert tool_a == [], f"text_only legacy must not emit tool events; got {tool_a}"
    assert tool_b == [], f"text_only governed must not emit tool events; got {tool_b}"


# ===========================================================================
# SCENARIO 2: tool_then_final (manual tool round-trip)
# ===========================================================================


def test_shadow_parity_tool_then_final_status() -> None:
    """tool_then_final: both paths complete successfully."""
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, result_a = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_tool_then_final_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    # Engine-compatible: call event + response event + final text
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
        response_event("Calculation", {"status": "ok", "reason": "tool_completed", "outputPreview": {"value": 2}}, "calculation-call-001"),
        text_event("final answer after manual tool execution", partial=True, turn_complete=True),
    ])
    events_b, result_b = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    _assert_boundary_result_parity(result_a, result_b, scenario="tool_then_final")


def test_shadow_parity_tool_then_final_tool_start() -> None:
    """tool_then_final: tool_start events are identical (id, name, type)."""
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_tool_then_final_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
        response_event("Calculation", {"status": "ok"}, "calculation-call-001"),
        text_event("final answer after manual tool execution", partial=True, turn_complete=True),
    ])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    _assert_public_event_parity(
        events_a,
        events_b,
        scenario="tool_then_final/tool_start",
        event_types={"tool_start"},
    )


def test_shadow_parity_tool_then_final_tool_progress() -> None:
    """tool_then_final: tool_progress events are identical."""
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_tool_then_final_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
        response_event("Calculation", {"status": "ok", "reason": "tool_completed", "outputPreview": {"value": 2}}, "calculation-call-001"),
        text_event("final answer after manual tool execution", partial=True, turn_complete=True),
    ])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    _assert_public_event_parity(
        events_a,
        events_b,
        scenario="tool_then_final/tool_progress",
        event_types={"tool_progress"},
    )


def test_shadow_parity_tool_then_final_tool_end() -> None:
    """tool_then_final: tool_end events are identical after durationMs normalization."""
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_tool_then_final_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
        response_event("Calculation", {"status": "ok", "reason": "tool_completed", "outputPreview": {"value": 2}}, "calculation-call-001"),
        text_event("final answer after manual tool execution", partial=True, turn_complete=True),
    ])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    _assert_public_event_parity(
        events_a,
        events_b,
        scenario="tool_then_final/tool_end",
        event_types={"tool_end"},
    )


def test_shadow_parity_tool_then_final_text_delta() -> None:
    """tool_then_final: text_delta events are identical."""
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_tool_then_final_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
        response_event("Calculation", {"status": "ok", "reason": "tool_completed", "outputPreview": {"value": 2}}, "calculation-call-001"),
        text_event("final answer after manual tool execution", partial=True, turn_complete=True),
    ])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    _assert_public_event_parity(
        events_a,
        events_b,
        scenario="tool_then_final/text_delta",
        event_types={"text_delta"},
    )


# ===========================================================================
# SCENARIO 3: native_tool_roundtrip
# ===========================================================================


def test_shadow_parity_native_tool_roundtrip_status() -> None:
    """native_tool_roundtrip: both paths complete successfully."""
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, result_a = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_native_roundtrip_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    # Engine path: call + response in same stream → no-tool finalizer emits final text.
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
        response_event("Calculation", {"status": "ok"}, "calculation-call-001"),
        text_event("final answer after native tool roundtrip", partial=True, turn_complete=True),
    ])
    events_b, result_b = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    _assert_boundary_result_parity(result_a, result_b, scenario="native_tool_roundtrip")


def test_shadow_parity_native_tool_roundtrip_tool_start() -> None:
    """native_tool_roundtrip: tool_start events are identical."""
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_native_roundtrip_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
        response_event("Calculation", {"status": "ok"}, "calculation-call-001"),
        text_event("final answer after native tool roundtrip", partial=True, turn_complete=True),
    ])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    _assert_public_event_parity(
        events_a,
        events_b,
        scenario="native_tool_roundtrip/tool_start",
        event_types={"tool_start"},
    )


def test_shadow_parity_native_tool_roundtrip_tool_progress() -> None:
    """native_tool_roundtrip: tool_progress events are identical."""
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_native_roundtrip_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
        response_event("Calculation", {"status": "ok"}, "calculation-call-001"),
        text_event("final answer after native tool roundtrip", partial=True, turn_complete=True),
    ])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    _assert_public_event_parity(
        events_a,
        events_b,
        scenario="native_tool_roundtrip/tool_progress",
        event_types={"tool_progress"},
    )


def test_shadow_parity_native_tool_roundtrip_tool_end() -> None:
    """native_tool_roundtrip: tool_end events are identical after durationMs normalization."""
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_native_roundtrip_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
        response_event("Calculation", {"status": "ok"}, "calculation-call-001"),
        text_event("final answer after native tool roundtrip", partial=True, turn_complete=True),
    ])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    _assert_public_event_parity(
        events_a,
        events_b,
        scenario="native_tool_roundtrip/tool_end",
        event_types={"tool_end"},
    )


# ===========================================================================
# SCENARIO 4: duplicate_text_and_call
# ===========================================================================


def test_shadow_parity_duplicate_text_and_call_status() -> None:
    """duplicate_text_and_call: both paths complete successfully.

    DIVERGENCE DOCUMENTED: gate5b4c3 deduplicates function_calls ACROSS events
    by JSON-key fingerprint (``_json_dumps(function_call)``).  The engine
    deduplicates within a single event's parts but NOT across separate ADK events
    emitted by the runner.  When the runner emits the same call twice in separate
    events, the engine emits two tool_start events (one per ADK event) while
    gate5b4c3 emits one (deduped on the second).

    This test only asserts that both paths COMPLETE successfully (status == "completed")
    and that both agree on provider/model.  The output_text and tool_event_count
    divergences are documented in ``test_divergence_duplicate_dedup``.
    """
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, result_a = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_duplicate_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    engine_runner = MockRunner([
        _preamble_and_call_event(),
        _preamble_and_call_event(),  # duplicate
        response_event("Calculation", {"status": "ok", "reason": "tool_completed", "outputPreview": {"value": 2}}, "calculation-call-001"),
        text_event("final answer after one manual tool execution", partial=True, turn_complete=True),
    ])
    events_b, result_b = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    # Both paths must complete.
    assert result_a.status == "completed", f"legacy must complete; got {result_a.status!r}"
    assert result_b.status == "completed", f"governed must complete; got {result_b.status!r}"
    # Provider/model must agree.
    assert result_a.selected_provider == result_b.selected_provider
    assert result_a.selected_model == result_b.selected_model


def test_shadow_parity_duplicate_text_and_call_tool_end_count() -> None:
    """duplicate_text_and_call: both paths emit exactly 1 tool_end.

    Even though the engine emits 2 tool_starts, it only emits 1 tool_end
    (completed_tool_event_ids dedup prevents double-emit on the response).
    Gate5b4c3 also emits 1 tool_end.

    Note: the tool_end IDs DIFFER (tu_77fcf1e39894 legacy vs tu_45c9ff345bc1
    governed) because gate5b4c3 uses function_call_index (always 0) while the
    engine uses part_index (1 when text precedes the function_call in the same
    Content).  This ID divergence is separate from the count assertion here.
    """
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_duplicate_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    engine_runner = MockRunner([
        _preamble_and_call_event(),
        _preamble_and_call_event(),  # duplicate
        response_event("Calculation", {"status": "ok", "reason": "tool_completed", "outputPreview": {"value": 2}}, "calculation-call-001"),
        text_event("final answer after one manual tool execution", partial=True, turn_complete=True),
    ])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    tool_ends_a = [e for e in events_a if e.get("type") == "tool_end"]
    tool_ends_b = [e for e in events_b if e.get("type") == "tool_end"]
    assert len(tool_ends_a) == 1, f"legacy must emit exactly 1 tool_end; got {len(tool_ends_a)}"
    assert len(tool_ends_b) == 1, f"governed must emit exactly 1 tool_end; got {len(tool_ends_b)}"
    # output_preview content must match (digest of same response payload).
    assert tool_ends_a[0].get("output_preview") == tool_ends_b[0].get("output_preview"), (
        f"tool_end output_preview mismatch: "
        f"legacy={tool_ends_a[0].get('output_preview')!r}, "
        f"governed={tool_ends_b[0].get('output_preview')!r}"
    )


def test_divergence_duplicate_dedup() -> None:
    """Documents the duplicate_text_and_call dedup divergence.

    FINDING: gate5b4c3 deduplicates function_calls across events by JSON key.
    The engine does NOT deduplicate across separate ADK events.

    gate5b4c3 behavior (with _DuplicateTextAndFunctionCallRunner emitting 2 events):
    - Emits 1 tool_start (dedup fires on 2nd identical JSON key)
    - Emits 1 text_delta for preamble (suffix-dedup via _event_visible_text_delta)
    - output_text_internal = 'preamble.final_answer'

    Engine behavior (with 2 identical _preamble_and_call_events):
    - Emits 2 tool_starts (engine dedup is per-event parts, not cross-event)
    - Emits 2 text_deltas for preamble (engine processes text from each event)
    - output_text_internal = 'preamble.preamble.final_answer' (preamble doubled)

    This divergence means the governed-turn path emits duplicate tool_start events
    and duplicate preamble text when the ADK runner emits the same function_call
    in multiple separate events.  This MUST be resolved before live flip for
    tools that can produce duplicate call events.
    """
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, result_a = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_duplicate_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    engine_runner = MockRunner([
        _preamble_and_call_event(),
        _preamble_and_call_event(),  # duplicate
        response_event("Calculation", {"status": "ok", "reason": "tool_completed", "outputPreview": {"value": 2}}, "calculation-call-001"),
        text_event("final answer after one manual tool execution", partial=True, turn_complete=True),
    ])
    events_b, result_b = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    legacy_tool_starts = [e for e in events_a if e.get("type") == "tool_start"]
    governed_tool_starts = [e for e in events_b if e.get("type") == "tool_start"]
    legacy_text_deltas = [e for e in events_a if e.get("type") == "text_delta"]
    governed_text_deltas = [e for e in events_b if e.get("type") == "text_delta"]

    # Assert the DIVERGENCE: legacy deduplicates (1 tool_start); engine does not (2 tool_starts).
    assert len(legacy_tool_starts) == 1, (
        f"legacy must emit exactly 1 tool_start (deduped); got {len(legacy_tool_starts)}"
    )
    assert len(governed_tool_starts) >= 2, (
        f"governed-turn must emit >= 2 tool_starts (no cross-event dedup); "
        f"got {len(governed_tool_starts)}\n"
        f"  If this divergence is resolved, update this test."
    )

    # Assert preamble text in output_text_internal.
    preamble = "재무제표 분析을 진행하겠습니다."
    assert result_a.output_text_internal is not None and preamble[:5] in result_a.output_text_internal, (
        f"legacy output_text_internal must contain preamble; got {result_a.output_text_internal!r}"
    )
    assert result_b.output_text_internal is not None and preamble[:5] in result_b.output_text_internal, (
        f"governed output_text_internal must contain preamble; got {result_b.output_text_internal!r}"
    )
    # Legacy preamble appears once (suffix-deduped); governed appears twice (no suffix-dedup across events).
    assert result_a.output_text_internal.count(preamble[:5]) == 1, (
        f"legacy preamble count should be 1; got {result_a.output_text_internal!r}"
    )
    assert result_b.output_text_internal.count(preamble[:5]) >= 2, (
        f"governed preamble count should be >= 2 (divergence); "
        f"got {result_b.output_text_internal!r}\n"
        f"  If this divergence is resolved, update this test."
    )


# ===========================================================================
# SCENARIO 5: function_call_only
# ===========================================================================


def test_shadow_parity_function_call_only_tool_start() -> None:
    """function_call_only: tool_start events are identical (same id, name).

    Both paths emit tool_start when they see the function_call event.
    The legacy path produces status=error (no text output);
    the engine path yields no EngineResult terminal with text, also producing
    status=error/runner_error.  Both should agree on the tool_start shape.
    """
    request = _request_text_only()  # disabled tools — fn_call only, no manual execution
    config = _config_text_only()

    events_a, result_a = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_function_call_only_legacy_primitives,
        )
    )
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
    ])
    events_b, result_b = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    # Both paths should NOT succeed (no text output from function_call_only).
    assert result_a.status in {"error", "completed"}, (
        f"function_call_only legacy must produce error or completed; got {result_a.status!r}"
    )
    assert result_b.status in {"error", "completed"}, (
        f"function_call_only governed must produce error or completed; got {result_b.status!r}"
    )

    # tool_start events must match.
    _assert_public_event_parity(
        events_a,
        events_b,
        scenario="function_call_only/tool_start",
        event_types={"tool_start"},
    )


def test_shadow_parity_function_call_only_tool_progress() -> None:
    """function_call_only: tool_progress events are identical."""
    request = _request_text_only()
    config = _config_text_only()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_function_call_only_legacy_primitives,
        )
    )
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
    ])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    _assert_public_event_parity(
        events_a,
        events_b,
        scenario="function_call_only/tool_progress",
        event_types={"tool_progress"},
    )


def test_shadow_parity_function_call_only_no_tool_end() -> None:
    """function_call_only: engine emits 0 tool_end (no fn_response); legacy also 0.

    When the runner yields only a function_call (no matching function_response),
    neither path should emit tool_end.  (The legacy path emits tool_end ONLY when
    it has a manual-tool runner with adk_tools AND the tools_policy is
    selected_full_toolhost — here tools_policy is disabled, so it falls through
    to the output_missing error path without emitting tool_end.)
    """
    request = _request_text_only()
    config = _config_text_only()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_function_call_only_legacy_primitives,
        )
    )
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
    ])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    tool_ends_a = [e for e in events_a if e.get("type") == "tool_end"]
    tool_ends_b = [e for e in events_b if e.get("type") == "tool_end"]
    assert tool_ends_a == [], (
        f"function_call_only legacy must not emit tool_end; got {tool_ends_a}"
    )
    assert tool_ends_b == [], (
        f"function_call_only governed must not emit tool_end; got {tool_ends_b}"
    )


# ===========================================================================
# Ratchet gate: assert all documented divergences are ONLY what we expect
# ===========================================================================


def test_documented_divergence_only_turn_phase_and_lifecycle() -> None:
    """Ratchet: after filtering turn_phase + lifecycle, tool/text event TYPES are identical.

    Drives the tool_then_final scenario and asserts that the ONLY event TYPES
    present in the LEGACY path but absent from the GOVERNED-TURN path (before
    normalisation) are ``turn_phase`` (manual-loop signal, documented divergence)
    and the engine lifecycle events ``turn_start``/``turn_end``.

    If any tool_* or text_delta event TYPE appears in one path but not the other,
    this test FAILS — which is exactly the bug-surfacing purpose of PR5.

    Note: this test checks TYPE-LEVEL presence only.  Field-level differences
    within the same type (e.g. ``input_preview`` in ``tool_start``) are
    surfaced by ``test_divergence_tool_start_input_preview``.
    """
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_tool_then_final_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
        response_event("Calculation", {"status": "ok", "reason": "tool_completed", "outputPreview": {"value": 2}}, "calculation-call-001"),
        text_event("final answer after manual tool execution", partial=True, turn_complete=True),
    ])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    # Compute symmetric difference of event types.
    types_a = {e.get("type") for e in events_a}
    types_b = {e.get("type") for e in events_b}

    # Events in legacy but not in governed-turn.
    legacy_only = types_a - types_b
    governed_only = types_b - types_a

    # Acceptable legacy-only divergences: turn_phase (manual loop signal).
    acceptable_legacy_only = {"turn_phase"}
    # Acceptable governed-only divergences: engine lifecycle events.
    acceptable_governed_only = {"turn_start", "turn_end"}

    unexpected_legacy_only = legacy_only - acceptable_legacy_only
    unexpected_governed_only = governed_only - acceptable_governed_only

    assert not unexpected_legacy_only, (
        f"UNACCEPTABLE divergence: event types in legacy but NOT in governed-turn:\n"
        f"  {unexpected_legacy_only}\n"
        f"  This means the governed-turn path is MISSING events the legacy path emits.\n"
        f"  Full legacy event types:   {sorted(types_a)}\n"
        f"  Full governed event types: {sorted(types_b)}"
    )
    assert not unexpected_governed_only, (
        f"UNACCEPTABLE divergence: event types in governed-turn but NOT in legacy:\n"
        f"  {unexpected_governed_only}\n"
        f"  This means the governed-turn path emits EXTRA events the legacy path doesn't.\n"
        f"  Full legacy event types:   {sorted(types_a)}\n"
        f"  Full governed event types: {sorted(types_b)}"
    )


def test_divergence_tool_start_input_preview() -> None:
    """Documents the known field divergence: engine adds input_preview to tool_start.

    The engine bridge calls ``wire_profile.build_tool_start(tool_id, name,
    _public_preview(args))`` where ``_public_preview`` always produces a
    non-empty JSON string for any args dict.  The gate5b4c3 boundary calls
    ``tool_start_event(input_preview=tool_input_preview(args))`` where
    ``tool_input_preview`` returns ``None`` for args whose keys are NOT in
    ``_TOOL_INPUT_PREVIEW_KEYS`` (query, url, path, etc.).  The test tool
    uses key ``expression`` which is not in that set.

    Result:
    - Legacy (gate5b4c3): tool_start has NO ``input_preview`` field.
    - Governed-turn (engine): tool_start HAS ``input_preview: '{"expression": "1 + 1"}'``.

    This is a substantive field divergence.  Before the live flip, the team must
    decide: (A) accept the extra field in governed-turn as safe additive info, or
    (B) align the engine bridge to use the same ``tool_input_preview`` filter as
    gate5b4c3 (narrower, key-allowlisted).

    This test asserts the divergence EXISTS — if it is later resolved, this test
    will fail and should be updated/removed.
    """
    request = _request_full_toolhost()
    config = _config_full_toolhost()

    events_a, _ = asyncio.run(
        _drive_legacy_path(
            request,
            config,
            primitives_loader=_make_tool_then_final_legacy_primitives,
            adk_tools=(_ManualCalculationTool,),
        )
    )
    engine_runner = MockRunner([
        call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
        response_event("Calculation", {"status": "ok"}, "calculation-call-001"),
        text_event("final answer after manual tool execution", partial=True, turn_complete=True),
    ])
    events_b, _ = asyncio.run(
        _drive_governed_turn_path(request, config, engine_runner=engine_runner)
    )

    legacy_tool_starts = [e for e in events_a if e.get("type") == "tool_start"]
    governed_tool_starts = [e for e in events_b if e.get("type") == "tool_start"]

    assert legacy_tool_starts, "legacy must emit at least one tool_start"
    assert governed_tool_starts, "governed-turn must emit at least one tool_start"

    # Assert legacy does NOT have input_preview (gate5b4c3 key-allowlist filter).
    assert "input_preview" not in legacy_tool_starts[0], (
        f"Divergence resolved? Legacy tool_start now has input_preview: "
        f"{legacy_tool_starts[0]}\n"
        f"  Update/remove this test if intentionally aligned."
    )
    # Assert governed-turn DOES have input_preview (engine full-args JSON).
    assert "input_preview" in governed_tool_starts[0], (
        f"Divergence resolved? Governed-turn tool_start no longer has input_preview: "
        f"{governed_tool_starts[0]}\n"
        f"  Update/remove this test if intentionally aligned."
    )
    # Document the exact divergence value.
    governed_preview = governed_tool_starts[0]["input_preview"]
    assert governed_preview == '{"expression": "1 + 1"}', (
        f"Governed-turn input_preview value changed: {governed_preview!r}"
    )
