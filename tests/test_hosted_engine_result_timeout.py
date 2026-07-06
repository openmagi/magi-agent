"""U7 (B7): wall-clock timeout enforcement in collect_engine_to_boundary_result.

Three test groups:
(a) Unit -- collector: hanging generator + timeout_ms=50 raises TimeoutError quickly;
    generator is closed (no task leak).
(b) Unit -- no-op path: timeout_ms=0/None with a fast stream produces a normal
    result byte-identical to the un-timed path.
(c) Serving -- governed path: flag-ON + hanging stream + small budget produces
    a 504 response with status="timeout", reason="runner_timeout" matching the
    legacy path's shape.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import pytest
import httpx
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.config.models import (
    BuildInfo,
    PythonRuntimeAuthorityConfig,
    RuntimeConfig,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveAdkPrimitives,
    Gate5B4C3LiveRunnerBoundaryResult,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationBudgets,
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationDiagnostic,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterStore,
)
from magi_agent.transport.shadow_generations import (
    Gate5B4C3ShadowGenerationRouteConfig,
)
from magi_agent.transport.chat import (
    Gate5BUserVisibleChatRouteConfig,
)
from magi_agent.transport.hosted_engine_result import collect_engine_to_boundary_result


# ---------------------------------------------------------------------------
# Digest constants and base payload (mirrors test_hosted_engine_result.py)
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
        "shadowGenerationId": "shadow_gen_timeout_001",
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
            "turnId": "turn_timeout_001",
            "turnDigest": _TURN_DIGEST,
            "sanitizedCurrentTurnText": "Timeout test.",
            "sanitizedInputTextDigest": _SANITIZED_DIGEST,
            "channelName": "app_channel",
            "tsResponseCorrelationId": "ts_corr_timeout_001",
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
# Fast-stream helper (completes immediately)
# ---------------------------------------------------------------------------


async def _fast_gen(text: str = "hello") -> AsyncGenerator[object, None]:
    yield EngineResult(terminal=Terminal.completed, usage={})


# ---------------------------------------------------------------------------
# (a) Hanging generator + timeout_ms=50: TimeoutError raised quickly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_raises_timeout_error_on_hanging_stream() -> None:
    """A never-terminating stream + timeout_ms=50 must raise TimeoutError within 500ms.

    The outer asyncio.wait_for(timeout=5.0) is a test-suite guard: it prevents
    the suite from hanging in RED (when the inner timeout is absent). In RED the
    outer guard fires at 5s and the timing assertion (elapsed < 0.5) fails,
    confirming the inner timeout does not exist. In GREEN the inner 50ms timeout
    fires and elapsed < 0.5 passes.
    """
    closed: dict[str, bool] = {"value": False}

    async def _hanging_gen() -> AsyncGenerator[object, None]:
        event = asyncio.Event()
        try:
            while True:
                await event.wait()  # never set: blocks forever
                yield None
        finally:
            closed["value"] = True

    generation = _request()
    diag = _diagnostic(generation)

    start = time.monotonic()
    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await asyncio.wait_for(
            collect_engine_to_boundary_result(
                generation=generation,
                config=_config(),
                diagnostic=diag,
                event_stream=_hanging_gen(),
                started_at_monotonic=start,
                timeout_ms=50,
            ),
            timeout=5.0,  # outer guard: prevents infinite hang in RED
        )
    elapsed = time.monotonic() - start
    # Must fire near the 50ms inner timeout, not the 5s outer guard.
    assert elapsed < 0.5, (
        f"TimeoutError took {elapsed:.3f}s; expected inner 50ms timeout to fire. "
        "Did you forget to add asyncio.timeout() in collect_engine_to_boundary_result?"
    )
    # Generator must be closed (no resource leak).
    assert closed["value"], "Async generator was not closed after timeout"


# ---------------------------------------------------------------------------
# (b) timeout_ms=0 / None: byte-identical to un-timed path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_zero_no_timeout_armed() -> None:
    """timeout_ms=0 leaves behavior byte-identical: completes normally, no timeout."""
    generation = _request()
    diag = _diagnostic(generation)
    started = time.monotonic()

    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fast_gen("hello"),
        started_at_monotonic=started,
        timeout_ms=0,  # must NOT arm any timeout
    )

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert result.timeout_ms == 0


@pytest.mark.asyncio
async def test_timeout_default_no_timeout_armed() -> None:
    """Default timeout_ms (omitted) leaves behavior byte-identical: completes normally."""
    generation = _request()
    diag = _diagnostic(generation)
    started = time.monotonic()

    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fast_gen("world"),
        started_at_monotonic=started,
        # timeout_ms omitted -- default is 0
    )

    assert result.status == "completed"
    assert result.timeout_ms == 0


# ---------------------------------------------------------------------------
# Serving helpers
# ---------------------------------------------------------------------------


def _sha256(value: str) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


class _FakePart:
    @classmethod
    def from_text(cls, *, text: str) -> "_FakePart":
        return cls()


class _FakeContent:
    def __init__(self, *, parts: list[object], role: str | None = None) -> None:
        self.parts = parts
        self.role = role


class _FakeGenerateContentConfig:
    def __init__(self, **kwargs: object) -> None:
        pass


class _FakeSessionService:
    pass


class _FakeAgent:
    def __init__(self, **kwargs: object) -> None:
        pass


class _FakeRunner:
    def __init__(self, **kwargs: object) -> None:
        pass

    async def run_async(self, **kwargs: object):  # noqa: ANN201
        yield SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text="adk answer")])
        )


def _fake_primitives() -> Gate5B4C3LiveAdkPrimitives:
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_FakeRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


_DIAGNOSTIC_DUMP = Gate5B4C3ShadowGenerationDiagnostic(
    accepted=True,
    status="accepted",
    reason="accepted",
    shadowGenerationId="test-sg-id-timeout",
    provider="google",
    model="gemini-3.5-flash",
    routingSource="per_turn_injected",
).model_dump(by_alias=True, mode="python", warnings=False)


def _make_canary_runtime_with_small_timeout(
    tmp_path: Any,
    python_runner_timeout_ms: int = 100,
) -> OpenMagiRuntime:
    """Build a canary runtime where approved_budgets has a small runner timeout."""
    import pathlib

    runtime = OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-timeout-test",
            user_id="user-timeout-test",
            gateway_token="gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gemini-3.5-flash",
            build=BuildInfo(version="0.1.0-u7", build_sha="sha-u7"),
            authority=PythonRuntimeAuthorityConfig(
                userVisibleOutputAllowed=True,
                canaryRoutingAllowed=True,
            ),
        )
    )
    counter_path = pathlib.Path(str(tmp_path)) / "counters_timeout.json"
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-timeout-test"),
        selectedOwnerUserIdDigest=_sha256("user-timeout-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(counter_path),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-timeout-test"),
            trustedOwnerUserIdDigest=_sha256("user-timeout-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 100,
                "maxDailyGenerationCostUsd": 5.0,
                "maxCostUsd": 0.05,
                "pythonRunnerTimeoutMs": python_runner_timeout_ms,
            },
        ),
    )
    return runtime


def _canary_headers(digest_suffix: str = "a" * 64) -> dict[str, str]:
    return {
        "authorization": "Bearer gateway-token",
        "x-gate5b-canary-request-digest": f"sha256:{digest_suffix}",
    }


_CANARY_BODY = {"messages": [{"role": "user", "content": "timeout test prompt"}]}


# ---------------------------------------------------------------------------
# (c) Serving-level: flag-ON + hanging stream + small budget -> runner_timeout
#
# Uses httpx.AsyncClient + asyncio.wait_for as an outer guard so the test
# terminates in RED (when the inner timeout does not exist).
#
# RED path: asyncio.wait_for fires at 5s; the except-block calls pytest.fail().
# GREEN path: collect_engine_to_boundary_result raises TimeoutError at ~100ms;
#             the serving layer returns 504 with status="timeout"; elapsed < 2s.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_governed_path_timeout_produces_runner_timeout_response(
    monkeypatch: Any, tmp_path: Any
) -> None:
    """Flag ON + hanging governed stream + small python_runner_timeout_ms budget.

    The serving layer must catch the TimeoutError raised by
    collect_engine_to_boundary_result and return a 504 response whose body
    contains status='timeout', reason='runner_timeout' -- identical to the
    shape the legacy path produces for a timed-out runner.

    collect_engine_to_boundary_result is NOT mocked here; the real
    implementation enforces the timeout and raises TimeoutError, which the
    existing except-TimeoutError handler in gate5b_serving.py catches.
    """
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", "1")

    async def _hanging_governed_gen() -> AsyncGenerator[object, None]:
        event = asyncio.Event()
        while True:
            await event.wait()  # never set
            yield None

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        return _hanging_governed_gen()

    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.run_governed_turn",
        fake_governed_turn,
    )
    # DO NOT monkeypatch collect_engine_to_boundary_result -- the real one must run.

    runtime = _make_canary_runtime_with_small_timeout(
        tmp_path, python_runner_timeout_ms=100
    )
    app = create_app(runtime)

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            timeout=10.0,
        ) as client:
            response = await asyncio.wait_for(
                client.post(
                    "/v1/chat/completions",
                    headers=_canary_headers("c" * 64),
                    json=_CANARY_BODY,
                ),
                timeout=5.0,  # outer guard: prevents infinite hang in RED
            )
    except (asyncio.TimeoutError, TimeoutError):
        elapsed = time.monotonic() - start
        pytest.fail(
            f"Request never returned after {elapsed:.2f}s (outer 5s guard fired). "
            "Expected the 100ms runner timeout budget to produce a 504 response "
            "within ~200ms. Did collect_engine_to_boundary_result implement "
            "asyncio.timeout()?"
        )

    elapsed = time.monotonic() - start

    # Must respond with 504 and the runner_timeout shape.
    assert response.status_code == 504, f"Expected 504, got {response.status_code}: {response.text}"
    body = response.json()
    assert body.get("status") == "timeout", f"Expected status='timeout', got: {body}"
    assert body.get("reason") == "runner_timeout", f"Expected reason='runner_timeout', got: {body}"

    # Should complete well before any outer safety margin.
    assert elapsed < 2.0, (
        f"Serving path took {elapsed:.3f}s; expected timeout within ~100ms. "
        "Check that collect_engine_to_boundary_result enforces timeout_ms."
    )
