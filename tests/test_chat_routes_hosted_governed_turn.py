"""Tests for the MAGI_HOSTED_GOVERNED_TURN_ENABLED flag-gated branch in chat_routes.

PR4 flip: when the flag is ON, chat_routes routes hosted turns through
run_governed_turn + collect_engine_to_boundary_result instead of
run_gate5b4c3_live_runner_boundary_async. Flag-OFF (default) must be byte-
identical to today.

Three test groups:
1. Flag OFF (default): gate5b4c3 boundary is called; governed-turn path is NOT.
2. Flag ON: run_governed_turn + collect_engine_to_boundary_result ARE called;
   gate5b4c3 boundary is NOT called (happy path with accepted runner_input).
3. Same response shape: both paths produce a compatible response for downstream code.
"""
from __future__ import annotations

import pathlib
import tempfile
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from magi_agent.adk_bridge.primitives import AdkPrimitiveBoundary
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
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationDiagnostic,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterStore,
)
from magi_agent.transport.shadow_generations import (
    Gate5B4C3ShadowGenerationRouteConfig,
)
from magi_agent.transport import chat_routes as chat_routes_module
from magi_agent.transport.chat import (
    Gate5BUserVisibleChatRouteConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def make_runtime(
    *,
    authority: PythonRuntimeAuthorityConfig | None = None,
) -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-test",
            user_id="user-test",
            gateway_token="gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gemini-3.5-flash",
            build=BuildInfo(version="0.1.0-flip-pr4", build_sha="sha-flip-pr4"),
            authority=authority or PythonRuntimeAuthorityConfig(),
        )
    )


# ---------------------------------------------------------------------------
# ADK primitive fakes
# ---------------------------------------------------------------------------


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text

    @classmethod
    def from_text(cls, *, text: str) -> "_FakePart":
        return cls(text)


class _FakeContent:
    def __init__(self, *, parts: list[_FakePart], role: str | None = None) -> None:
        self.parts = parts
        self.role = role


class _FakeGenerateContentConfig:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


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
            content=SimpleNamespace(parts=[SimpleNamespace(text="fake ADK answer")])
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


# ---------------------------------------------------------------------------
# Minimal Gate5B4C3LiveRunnerBoundaryResult builder
# ---------------------------------------------------------------------------

_DIAGNOSTIC_DUMP = Gate5B4C3ShadowGenerationDiagnostic(
    accepted=True,
    status="accepted",
    reason="accepted",
    shadowGenerationId="test-sg-id",
    provider="google",
    model="gemini-3.5-flash",
    routingSource="per_turn_injected",
).model_dump(by_alias=True, mode="python", warnings=False)


def _make_boundary_result(
    *,
    output_text: str | None = "test answer",
) -> Gate5B4C3LiveRunnerBoundaryResult:
    """Build a minimal valid Gate5B4C3LiveRunnerBoundaryResult for test use."""
    return Gate5B4C3LiveRunnerBoundaryResult(
        diagnostic=_DIAGNOSTIC_DUMP,
        status="completed",
        reason="runner_completed",
        selectedProvider="google",
        selectedModel="gemini-3.5-flash",
        routingSource="per_turn_injected",
        latencyMs=42,
        timeoutMs=30000,
        adkInvoked=True,
        runnerAttempted=True,
        modelCallViaAdkRunnerAttempted=True,
        failOpen=True,
        eventCount=1,
        agentKwargsKeys=(),
        runnerKwargsKeys=(),
        runAsyncKwargsKeys=(),
        errorClass=None,
        errorPreview=None,
        runnerErrorDiagnostic=None,
        outputTextInternal=output_text,
        usageInternal=None,
        userVisibleOutput=None,
    )


# ---------------------------------------------------------------------------
# Runtime builder
# ---------------------------------------------------------------------------


def _make_canary_runtime(tmp_path: Any) -> OpenMagiRuntime:
    """Build a fully-wired runtime with live-runner boundary enabled."""
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    counter_path = pathlib.Path(str(tmp_path)) / "counters.json"
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
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
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
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
            },
        ),
    )
    return runtime


def _canary_headers(digest_suffix: str = "a" * 64) -> dict[str, str]:
    return {
        "authorization": "Bearer gateway-token",
        "x-gate5b-canary-request-digest": f"sha256:{digest_suffix}",
    }


_CANARY_BODY = {"messages": [{"role": "user", "content": "test prompt"}]}


# ---------------------------------------------------------------------------
# 1. Flag OFF (default) — gate5b4c3 boundary called, governed-turn NOT called
# ---------------------------------------------------------------------------


def test_flag_off_uses_gate5b4c3_boundary(monkeypatch, tmp_path: Any) -> None:
    """Flag OFF (default): only run_gate5b4c3_live_runner_boundary_async is called."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.delenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", raising=False)

    boundary_called: dict[str, int] = {"count": 0}
    governed_called: dict[str, int] = {"count": 0}
    collect_called: dict[str, int] = {"count": 0}

    async def fake_boundary(*args: object, **kwargs: object) -> Gate5B4C3LiveRunnerBoundaryResult:
        boundary_called["count"] += 1
        return _make_boundary_result()

    async def _noop_gen():  # noqa: ANN202
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        # Track the call synchronously (generator body is lazy — track at call site).
        governed_called["count"] += 1
        return _noop_gen()

    async def fake_collect(*args: object, **kwargs: object) -> object:
        collect_called["count"] += 1
        return _make_boundary_result()

    monkeypatch.setattr("magi_agent.transport.chat_routes.run_gate5b4c3_live_runner_boundary_async", fake_boundary)
    monkeypatch.setattr("magi_agent.transport.chat_routes.run_governed_turn", fake_governed_turn)
    monkeypatch.setattr("magi_agent.transport.chat_routes.collect_engine_to_boundary_result", fake_collect)

    runtime = _make_canary_runtime(tmp_path)
    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers("a" * 64),
        json=_CANARY_BODY,
    )

    assert boundary_called["count"] == 1, "gate5b4c3 boundary must be called (flag OFF)"
    assert governed_called["count"] == 0, "run_governed_turn must NOT be called (flag OFF)"
    assert collect_called["count"] == 0, "collect_engine_to_boundary_result must NOT be called (flag OFF)"
    assert response.status_code == 200, response.json()


# ---------------------------------------------------------------------------
# 2. Flag ON — governed-turn path called, gate5b4c3 boundary NOT called
# ---------------------------------------------------------------------------


def test_flag_on_uses_governed_turn(monkeypatch, tmp_path: Any) -> None:
    """Flag ON: run_governed_turn + collect are called; gate5b4c3 boundary is NOT."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", "1")

    boundary_called: dict[str, int] = {"count": 0}
    governed_called: dict[str, int] = {"count": 0}
    collect_called: dict[str, int] = {"count": 0}
    turn_ctx_seen: list[object] = []

    async def fail_boundary(*args: object, **kwargs: object) -> object:
        boundary_called["count"] += 1
        raise AssertionError("gate5b4c3 boundary must NOT be called when flag is ON")

    async def _noop_gen():  # noqa: ANN202
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        # Track the call synchronously (generator body is lazy — track at call site).
        governed_called["count"] += 1
        turn_ctx_seen.append(ctx)
        return _noop_gen()

    async def fake_collect(*args: object, **kwargs: object) -> Gate5B4C3LiveRunnerBoundaryResult:
        collect_called["count"] += 1
        return _make_boundary_result(output_text="governed answer")

    monkeypatch.setattr("magi_agent.transport.chat_routes.run_gate5b4c3_live_runner_boundary_async", fail_boundary)
    monkeypatch.setattr("magi_agent.transport.chat_routes.run_governed_turn", fake_governed_turn)
    monkeypatch.setattr("magi_agent.transport.chat_routes.collect_engine_to_boundary_result", fake_collect)

    runtime = _make_canary_runtime(tmp_path)
    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers("b" * 64),
        json=_CANARY_BODY,
    )

    assert boundary_called["count"] == 0, "gate5b4c3 boundary must NOT be called (flag ON)"
    assert governed_called["count"] == 1, "run_governed_turn must be called exactly once (flag ON)"
    assert collect_called["count"] == 1, "collect_engine_to_boundary_result must be called (flag ON)"
    # Verify TurnContext type was passed
    assert len(turn_ctx_seen) == 1
    from magi_agent.runtime.turn_context import TurnContext
    assert isinstance(turn_ctx_seen[0], TurnContext), (
        f"run_governed_turn must receive a TurnContext, got {type(turn_ctx_seen[0])}"
    )
    assert response.status_code == 200, response.json()


# ---------------------------------------------------------------------------
# 3. Same response shape — both paths produce compatible JSON for downstream
# ---------------------------------------------------------------------------


def test_flag_off_and_on_produce_same_response_top_level_shape(monkeypatch) -> None:
    """Both paths produce responses with the same top-level key structure."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")

    async def fake_boundary(*args: object, **kwargs: object) -> Gate5B4C3LiveRunnerBoundaryResult:
        return _make_boundary_result(output_text="shape test answer OFF")

    async def _noop_gen():  # noqa: ANN202
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        return _noop_gen()

    async def fake_collect(*args: object, **kwargs: object) -> Gate5B4C3LiveRunnerBoundaryResult:
        return _make_boundary_result(output_text="shape test answer ON")

    monkeypatch.setattr("magi_agent.transport.chat_routes.run_gate5b4c3_live_runner_boundary_async", fake_boundary)
    monkeypatch.setattr("magi_agent.transport.chat_routes.run_governed_turn", fake_governed_turn)
    monkeypatch.setattr("magi_agent.transport.chat_routes.collect_engine_to_boundary_result", fake_collect)

    # --- Flag OFF ---
    monkeypatch.delenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", raising=False)
    tmp_off = pathlib.Path(tempfile.mkdtemp())
    runtime_off = _make_canary_runtime(tmp_off)
    resp_off = TestClient(create_app(runtime_off)).post(
        "/v1/chat/completions",
        headers=_canary_headers("c" * 64),
        json=_CANARY_BODY,
    )

    # --- Flag ON ---
    monkeypatch.setenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", "1")
    tmp_on = pathlib.Path(tempfile.mkdtemp())
    runtime_on = _make_canary_runtime(tmp_on)
    resp_on = TestClient(create_app(runtime_on)).post(
        "/v1/chat/completions",
        headers=_canary_headers("d" * 64),
        json=_CANARY_BODY,
    )

    assert resp_off.status_code == 200, resp_off.json()
    assert resp_on.status_code == 200, resp_on.json()

    body_off = resp_off.json()
    body_on = resp_on.json()

    # Assert top-level key sets match exactly
    assert set(body_off.keys()) == set(body_on.keys()), (
        f"Response key mismatch.\nOFF keys: {sorted(body_off.keys())}\n"
        f"ON keys:  {sorted(body_on.keys())}"
    )

    # Assert critical shared fields have the same type/structure
    for key in ("status", "fallbackStatus", "responseAuthority", "adk", "counter"):
        assert key in body_off and key in body_on, f"Missing key: {key}"
        assert type(body_off[key]) == type(body_on[key]), (  # noqa: E721
            f"Type mismatch for key {key!r}: "
            f"OFF={type(body_off[key]).__name__}, ON={type(body_on[key]).__name__}"
        )
