"""TDD tests for magi_agent.transport.hosted_engine_result.

PR3 of the flip series: async collector that consumes run_governed_turn's
event stream and assembles Gate5B4C3LiveRunnerBoundaryResult.

Test inventory:
1. Successful text-only run → status="completed", event_count, output_text.
2. Mixed tool/text run → event_count correct, text aggregated from text_delta only.
3. Empty run (no text_delta) → output_text_internal=None, user_visible_output=None.
4. Error terminal → status="error", reason="runner_error".
5. max_turns terminal → status="error", reason="runner_incomplete".
6. Provider/model fields pass through from generation.model_routing.
7. Frozen pydantic shape: instance is Gate5B4C3LiveRunnerBoundaryResult,
   adk_invoked=True, fail_open=True.
8. Usage translation from snake_case engine keys to camelCase boundary keys.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveRunnerBoundaryResult,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
)
from magi_agent.transport.hosted_engine_result import collect_engine_to_boundary_result

# ---------------------------------------------------------------------------
# Shared fixtures — reuse payload pattern from test_gate5b4c3_live_runner_boundary
# ---------------------------------------------------------------------------

_BOT_DIGEST = "sha256:" + "a" * 64
_OWNER_DIGEST = "sha256:" + "b" * 64
_TURN_DIGEST = "sha256:" + "c" * 64
_REQUEST_DIGEST = "sha256:" + "d" * 64
_TRACE_DIGEST = "sha256:" + "e" * 64
_SESSION_DIGEST = "sha256:" + "f" * 64
_SANITIZED_DIGEST = "sha256:" + "1" * 64
_ROUTER_DIGEST = "sha256:" + "2" * 64
_PROFILE_DIGEST = "sha256:" + "3" * 64
_BOT_CONFIG_DIGEST = "sha256:" + "4" * 64


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
        "shadowGenerationId": "shadow_gen_test_001",
        "requestIdDigest": _REQUEST_DIGEST,
        "traceIdDigest": _TRACE_DIGEST,
        "createdAt": 1779200000000,
        "selection": {
            "botIdDigest": _BOT_DIGEST,
            "ownerUserIdDigest": _OWNER_DIGEST,
            "environment": "production",
            "selectedTarget": "gate5b_selected_bot",
            "sessionKeyDigest": _SESSION_DIGEST,
        },
        "turn": {
            "turnId": "turn_test_001",
            "turnDigest": _TURN_DIGEST,
            "sanitizedCurrentTurnText": "Summarize the test fixture.",
            "sanitizedInputTextDigest": _SANITIZED_DIGEST,
            "channelName": "app_channel",
            "tsResponseCorrelationId": "ts_corr_test_001",
        },
        "modelRouting": {
            "routingSource": "per_turn_injected",
            "providerLabel": "anthropic",
            "modelLabel": "claude-3-5-sonnet-latest",
            "routerDecisionDigest": _ROUTER_DIGEST,
            "routingProfileDigest": _PROFILE_DIGEST,
            "botConfigModelDigest": _BOT_CONFIG_DIGEST,
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
            "sanitizedPayloadDigest": _SANITIZED_DIGEST,
        },
        "authority": {},
    }
    base.update(overrides)
    return base


def _request(**overrides: object) -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(_payload(**overrides))


def _config() -> Gate5B4C3ShadowGenerationConfig:
    return Gate5B4C3ShadowGenerationConfig()


def _diagnostic(generation: Gate5B4C3ShadowGenerationRequest):  # type: ignore[return]
    return build_gate5b4c3_shadow_generation_diagnostic(generation, config=_config())


# ---------------------------------------------------------------------------
# Helpers: fake async generators
# ---------------------------------------------------------------------------


async def _fake_gen(
    events: list[object],
    terminal: EngineResult,
) -> AsyncGenerator[object, None]:
    """Yield events then the terminal (per the engine's convention)."""
    for evt in events:
        yield evt
    yield terminal  # terminal is the final yielded item


def _text_delta(delta: str) -> dict[str, object]:
    return {"type": "text_delta", "delta": delta}


def _tool_start(tool_id: str = "call_1", name: str = "ReadFile") -> dict[str, object]:
    return {"type": "tool_start", "id": tool_id, "name": name}


def _tool_end(tool_id: str = "call_1") -> dict[str, object]:
    return {"type": "tool_end", "id": tool_id}


def _ok_terminal(usage: dict | None = None) -> EngineResult:
    return EngineResult(terminal=Terminal.completed, usage=usage or {})


def _error_terminal() -> EngineResult:
    return EngineResult(terminal=Terminal.error, usage={}, error="runner_exploded")


def _max_turns_terminal() -> EngineResult:
    return EngineResult(terminal=Terminal.max_turns, usage={})


def _aborted_terminal() -> EngineResult:
    return EngineResult(terminal=Terminal.aborted, usage={})


# ---------------------------------------------------------------------------
# Test 1: Successful text-only run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_only_success() -> None:
    """3 text_delta events + EngineResult terminal → status=completed, text assembled."""
    gen = Gate5B4C3ShadowGenerationRequest
    generation = _request()
    diag = _diagnostic(generation)

    events = [
        _text_delta("Hello "),
        _text_delta("from "),
        _text_delta("the engine."),
    ]
    terminal = _ok_terminal()
    started = time.monotonic()
    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen(events, terminal),
        started_at_monotonic=started,
        timeout_ms=30_000,
    )

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    # event_count counts RuntimeEvents (the 3 text_delta dicts), not the terminal
    assert result.event_count == 3
    assert result.output_text_internal == "Hello from the engine."
    assert result.latency_ms >= 0
    assert result.timeout_ms == 30_000


# ---------------------------------------------------------------------------
# Test 2: Mixed tool/text run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_tool_text_run() -> None:
    """tool_start / tool_end / text_delta mix → count=4, text only from text_delta."""
    generation = _request()
    diag = _diagnostic(generation)

    events = [
        _tool_start("call_a", "ReadFile"),
        _text_delta("File contents: "),
        _tool_end("call_a"),
        _text_delta("all done."),
    ]
    terminal = _ok_terminal()
    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen(events, terminal),
        started_at_monotonic=time.monotonic(),
    )

    assert result.status == "completed"
    assert result.event_count == 4
    # only text_delta payloads contribute to output_text_internal
    assert result.output_text_internal == "File contents: all done."


# ---------------------------------------------------------------------------
# Test 3: Empty run (no text_delta)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_run_no_text() -> None:
    """No text_delta events → output_text_internal=None, user_visible_output=None."""
    generation = _request()
    diag = _diagnostic(generation)

    events = [_tool_start("call_b"), _tool_end("call_b")]
    terminal = _ok_terminal()
    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen(events, terminal),
        started_at_monotonic=time.monotonic(),
    )

    assert result.output_text_internal is None
    # model_validator forces userVisibleOutput=None regardless
    assert result.user_visible_output is None
    assert result.event_count == 2


# ---------------------------------------------------------------------------
# Test 4: Error terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_terminal_mapping() -> None:
    """Terminal.error → status='error', reason='runner_error'."""
    generation = _request()
    diag = _diagnostic(generation)

    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen([], _error_terminal()),
        started_at_monotonic=time.monotonic(),
    )

    assert result.status == "error"
    assert result.reason == "runner_error"
    assert result.runner_error_diagnostic is None  # None on basic error


# ---------------------------------------------------------------------------
# Test 4b: max_turns terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_turns_terminal_mapping() -> None:
    """Terminal.max_turns → status='error', reason='runner_incomplete'."""
    generation = _request()
    diag = _diagnostic(generation)

    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen([], _max_turns_terminal()),
        started_at_monotonic=time.monotonic(),
    )

    assert result.status == "error"
    assert result.reason == "runner_incomplete"


# ---------------------------------------------------------------------------
# Test 4c: aborted terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aborted_terminal_mapping() -> None:
    """Terminal.aborted → status='error', reason='runner_error'."""
    generation = _request()
    diag = _diagnostic(generation)

    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen([], _aborted_terminal()),
        started_at_monotonic=time.monotonic(),
    )

    assert result.status == "error"
    assert result.reason == "runner_error"


# ---------------------------------------------------------------------------
# Test 5: Provider/model pass-through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_model_passthrough() -> None:
    """selected_provider / selected_model / routing_source come from generation."""
    generation = _request()
    diag = _diagnostic(generation)

    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen([], _ok_terminal()),
        started_at_monotonic=time.monotonic(),
    )

    assert result.selected_provider == "anthropic"
    assert result.selected_model == "claude-3-5-sonnet-latest"
    assert result.routing_source == "per_turn_injected"


# ---------------------------------------------------------------------------
# Test 6: Frozen pydantic shape / engine flags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pydantic_shape_and_engine_flags() -> None:
    """Result is Gate5B4C3LiveRunnerBoundaryResult with engine flags True."""
    generation = _request()
    diag = _diagnostic(generation)

    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen([_text_delta("hi")], _ok_terminal()),
        started_at_monotonic=time.monotonic(),
    )

    assert isinstance(result, Gate5B4C3LiveRunnerBoundaryResult)
    assert result.adk_invoked is True
    assert result.runner_attempted is True
    assert result.model_call_via_adk_runner_attempted is True
    assert result.fail_open is True
    # Non-authoritative fields forced by model_validator
    assert result.response_authority == "typescript"
    assert result.diagnostic_only is True
    assert result.local_only is True


# ---------------------------------------------------------------------------
# Test 7: Usage translation (snake_case → camelCase)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_translation() -> None:
    """Engine usage dict (snake_case) is translated to camelCase for boundary."""
    generation = _request()
    diag = _diagnostic(generation)

    terminal = EngineResult(
        terminal=Terminal.completed,
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 20,
        },
    )
    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen([], terminal),
        started_at_monotonic=time.monotonic(),
    )

    assert result.usage_internal == {
        "inputTokens": 100,
        "outputTokens": 50,
        "cacheReadTokens": 20,
    }


@pytest.mark.asyncio
async def test_usage_empty_becomes_none() -> None:
    """Empty engine usage dict → usage_internal=None."""
    generation = _request()
    diag = _diagnostic(generation)

    terminal = EngineResult(terminal=Terminal.completed, usage={})
    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen([], terminal),
        started_at_monotonic=time.monotonic(),
    )

    assert result.usage_internal is None


# ---------------------------------------------------------------------------
# Test 8: asyncio.CancelledError propagates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelled_error_propagates() -> None:
    """asyncio.CancelledError from the generator propagates to the caller."""

    async def _cancelling_gen() -> AsyncGenerator[object, None]:
        yield _text_delta("partial")
        raise asyncio.CancelledError

    generation = _request()
    diag = _diagnostic(generation)

    with pytest.raises(asyncio.CancelledError):
        await collect_engine_to_boundary_result(
            generation=generation,
            config=_config(),
            diagnostic=diag,
            event_stream=_cancelling_gen(),
            started_at_monotonic=time.monotonic(),
        )


# ---------------------------------------------------------------------------
# Test 9: RuntimeEvent unwrap regression lock (PR3 bug fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_event_text_aggregation() -> None:
    """RuntimeEvent.payload is correctly unwrapped — regression lock for PR3 bug fix.

    The PR5 shadow-comparison harness uncovered that ``collect_engine_to_boundary_result``
    was using ``isinstance(evt, dict)`` to extract text deltas, but ``drain()`` returns
    ``RuntimeEvent`` objects — so ``output_text_internal`` was silently always None on the
    governed-turn path. This test exercises the corrected RuntimeEvent unwrap directly.
    """
    from magi_agent.runtime.events import RuntimeEvent  # noqa: PLC0415

    generation = _request()
    diag = _diagnostic(generation)
    events = [
        RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "Hello "}),
        RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "world."}),
    ]
    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen(events, _ok_terminal()),
        started_at_monotonic=time.monotonic(),
    )
    assert result.output_text_internal == "Hello world."


# ---------------------------------------------------------------------------
# Test 10 (B8): live public-event forwarding to the 1-arg SSE sink
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_forwards_public_events_live_to_sink() -> None:
    """B8: each engine event's public payload is forwarded to ``public_event_sink``
    (1-arg) AS it is consumed, in order, while the buffered result is unchanged.

    The projected public event reused here is the SAME payload dict the local
    streaming path frames (``RuntimeEvent.payload`` for engine events; the dict
    itself for the plain-dict test double), so the hosted SSE route's
    ``_enqueue_public_event`` consumes them exactly like the legacy path.
    """
    from magi_agent.runtime.events import RuntimeEvent  # noqa: PLC0415

    generation = _request()
    diag = _diagnostic(generation)
    events = [
        RuntimeEvent(type="token", payload=_text_delta("Hel")),
        RuntimeEvent(type="tool", payload=_tool_start("c1", "ReadFile")),
        RuntimeEvent(type="token", payload=_text_delta("lo")),
        RuntimeEvent(type="tool", payload=_tool_end("c1")),
    ]
    forwarded: list[object] = []
    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen(events, _ok_terminal()),
        started_at_monotonic=time.monotonic(),
        public_event_sink=forwarded.append,
    )

    # Each event was forwarded live, in order, as the raw public payload dict.
    assert forwarded == [evt.payload for evt in events]
    # The terminal in the stream is NOT forwarded (only RuntimeEvent items are).
    assert all(item.get("type") != "runner_completed" for item in forwarded)
    # Buffered result is byte-identical to the no-sink path.
    assert result.output_text_internal == "Hello"
    assert result.event_count == 4


@pytest.mark.asyncio
async def test_collect_forwards_plain_dict_events_live() -> None:
    """The plain-dict test double (as used by the other collector tests) is
    forwarded verbatim: the dict itself is the public event."""
    generation = _request()
    diag = _diagnostic(generation)
    events = [_text_delta("a"), _tool_start("c2"), _text_delta("b")]
    forwarded: list[object] = []
    await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen(events, _ok_terminal()),
        started_at_monotonic=time.monotonic(),
        public_event_sink=forwarded.append,
    )
    assert forwarded == events


@pytest.mark.asyncio
async def test_collect_none_sink_byte_identical() -> None:
    """``public_event_sink=None`` (and the absent-kwarg default) forwards nothing
    and returns a result byte-identical to today's drain-only path."""
    generation = _request()
    diag = _diagnostic(generation)

    events_a = [_text_delta("a"), _text_delta("b")]
    r_none = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen(events_a, _ok_terminal()),
        started_at_monotonic=time.monotonic(),
        public_event_sink=None,
    )
    events_b = [_text_delta("a"), _text_delta("b")]
    r_absent = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen(events_b, _ok_terminal()),
        started_at_monotonic=time.monotonic(),
    )

    assert r_none.output_text_internal == r_absent.output_text_internal == "ab"
    assert r_none.event_count == r_absent.event_count == 2
    assert r_none.status == r_absent.status == "completed"


@pytest.mark.asyncio
async def test_collect_sink_fault_never_breaks_collection() -> None:
    """A raising ``public_event_sink`` must never corrupt the buffered drain:
    forwarding is additive and fail-open."""
    generation = _request()
    diag = _diagnostic(generation)

    def _boom(_event: object) -> None:
        raise RuntimeError("sink exploded")

    events = [_text_delta("x"), _text_delta("y")]
    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen(events, _ok_terminal()),
        started_at_monotonic=time.monotonic(),
        public_event_sink=_boom,
    )
    assert result.output_text_internal == "xy"
    assert result.event_count == 2
    assert result.status == "completed"
