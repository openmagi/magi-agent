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
# 1. Flag explicitly OFF ("0") — gate5b4c3 boundary called, governed-turn NOT
#    called. The flag is now profile-aware default-ON, so the legacy path is the
#    explicit escape hatch (an explicit "0" or a safe-family profile).
# ---------------------------------------------------------------------------


def test_flag_explicit_off_uses_gate5b4c3_boundary(monkeypatch, tmp_path: Any) -> None:
    """Flag explicitly OFF ("0"): only run_gate5b4c3_live_runner_boundary_async
    is called. The legacy boundary is the escape hatch now that the flag defaults
    ON under the full/lab profile."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", "0")

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

    monkeypatch.setattr("magi_agent.transport.gate5b_serving.run_gate5b4c3_live_runner_boundary_async", fake_boundary)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.collect_engine_to_boundary_result", fake_collect)

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

    monkeypatch.setattr("magi_agent.transport.gate5b_serving.run_gate5b4c3_live_runner_boundary_async", fail_boundary)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.collect_engine_to_boundary_result", fake_collect)

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
# 1b. Profile-aware default: flag UNSET under a non-safe/full profile resolves
#     ON (governed path); a safe-family profile resolves OFF (legacy boundary).
# ---------------------------------------------------------------------------


def _count_route_paths(monkeypatch, tmp_path: Any, *, digest: str) -> dict[str, int]:
    """Wire the boundary/governed/collect fakes and issue one canary request;
    return the per-path call counts. The caller sets the env that selects the
    route."""
    counts: dict[str, int] = {"boundary": 0, "governed": 0, "collect": 0, "status": 0}

    async def fake_boundary(*args: object, **kwargs: object) -> Gate5B4C3LiveRunnerBoundaryResult:
        counts["boundary"] += 1
        return _make_boundary_result()

    async def _noop_gen():  # noqa: ANN202
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        counts["governed"] += 1
        return _noop_gen()

    async def fake_collect(*args: object, **kwargs: object) -> Gate5B4C3LiveRunnerBoundaryResult:
        counts["collect"] += 1
        return _make_boundary_result(output_text="governed answer")

    monkeypatch.setattr("magi_agent.transport.gate5b_serving.run_gate5b4c3_live_runner_boundary_async", fake_boundary)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.collect_engine_to_boundary_result", fake_collect)

    runtime = _make_canary_runtime(tmp_path)
    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers(digest),
        json=_CANARY_BODY,
    )
    counts["status"] = response.status_code
    return counts


def test_flag_unset_full_profile_uses_governed_turn(monkeypatch, tmp_path: Any) -> None:
    """Profile-aware default-ON: with the flag UNSET and no safe-family profile
    (the hosted/full default), the served turn routes through run_governed_turn,
    NOT the legacy gate5b4c3 boundary."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.delenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)

    counts = _count_route_paths(monkeypatch, tmp_path, digest="e" * 64)

    assert counts["status"] == 200, counts
    assert counts["governed"] == 1, "governed path must run when flag unset under full profile"
    assert counts["collect"] == 1
    assert counts["boundary"] == 0, "legacy boundary must NOT run when the default resolves ON"


def test_flag_unset_safe_profile_uses_gate5b4c3_boundary(monkeypatch, tmp_path: Any) -> None:
    """Escape hatch parity: under a safe-family runtime profile the unset flag
    resolves OFF, so the served turn takes the legacy gate5b4c3 boundary."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.delenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")

    counts = _count_route_paths(monkeypatch, tmp_path, digest="f" * 64)

    assert counts["status"] == 200, counts
    assert counts["boundary"] == 1, "legacy boundary must run under a safe-family profile"
    assert counts["governed"] == 0, "governed path must NOT run when the safe profile forces OFF"
    assert counts["collect"] == 0


def test_governed_fork_fronts_durable_session_service_when_db_flag_on(
    monkeypatch, tmp_path: Any
) -> None:
    """PR-3 parity: the governed fork must front the durable SqliteSessionService
    (not a fresh per-turn InMemory) so flipping the governed flag on does not
    regress server-side continuity.

    U3: durable fronting now flows through the single-flight session lease. A
    stable ``sessionId`` in the body yields a non-empty ``session_key_digest``
    so the lease engages (an empty digest would bypass the registry entirely,
    per the legacy boundary rule) and the miss returns the durable singleton.
    """
    from magi_agent.shadow.hosted_session_substrate import (
        reset_durable_hosted_session_service,
    )
    from magi_agent.shadow.session_service_registry import (
        reset_default_session_service_registry,
    )

    reset_durable_hosted_session_service()
    reset_default_session_service_registry()
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", "1")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_DB", "1")
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))

    captured: dict[str, object] = {}

    def fake_build_hosted_runtime(**kwargs: object) -> object:
        captured["session_service"] = kwargs.get("session_service")
        return SimpleNamespace()

    async def _noop_gen():  # noqa: ANN202
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        return _noop_gen()

    async def fake_collect(*args: object, **kwargs: object) -> Gate5B4C3LiveRunnerBoundaryResult:
        return _make_boundary_result(output_text="governed answer")

    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.build_hosted_runtime",
        fake_build_hosted_runtime,
    )
    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn
    )
    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.collect_engine_to_boundary_result",
        fake_collect,
    )

    runtime = _make_canary_runtime(tmp_path)
    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers("b" * 64),
        json={**_CANARY_BODY, "sessionId": "sess-durable-front"},
    )
    assert response.status_code == 200, response.json()

    from magi_agent.shadow.hosted_session_substrate import (
        get_durable_hosted_session_service,
    )

    durable = get_durable_hosted_session_service(str(tmp_path / "adk_sessions.db"))
    assert captured["session_service"] is durable
    assert captured["session_service"] is not None
    reset_durable_hosted_session_service()
    reset_default_session_service_registry()


def test_governed_fork_builds_fresh_in_memory_service_when_db_flag_off(
    monkeypatch, tmp_path: Any
) -> None:
    """DB flag OFF with no session key: no durable service is wired.

    U3: the governed branch acquires through the session lease, but a request
    with no ``sessionId`` has an empty ``session_key_digest`` so the lease
    bypasses the registry (``None``) and the branch builds a fresh in-memory
    service explicitly (mirroring the legacy boundary bypass), rather than
    passing ``None`` down to ``build_hosted_runtime``. Either way the turn runs
    against a fresh per-turn in-memory service with no durable continuity.
    """
    from magi_agent.shadow.session_service_registry import (
        default_session_service_registry,
        reset_default_session_service_registry,
    )

    reset_default_session_service_registry()
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_DB", "0")

    captured: dict[str, object] = {"session_service": "unset"}

    def fake_build_hosted_runtime(**kwargs: object) -> object:
        captured["session_service"] = kwargs.get("session_service")
        return SimpleNamespace()

    async def _noop_gen():  # noqa: ANN202
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        return _noop_gen()

    async def fake_collect(*args: object, **kwargs: object) -> Gate5B4C3LiveRunnerBoundaryResult:
        return _make_boundary_result(output_text="governed answer")

    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.build_hosted_runtime",
        fake_build_hosted_runtime,
    )
    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn
    )
    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.collect_engine_to_boundary_result",
        fake_collect,
    )

    runtime = _make_canary_runtime(tmp_path)
    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers("b" * 64),
        json=_CANARY_BODY,
    )
    assert response.status_code == 200, response.json()
    # DB flag OFF + no session key: the lease bypasses the registry, so the
    # branch builds a fresh in-memory service (a _FakeSessionService instance
    # from the fake primitives) rather than passing None. It is NOT the durable
    # substrate, and the registry was never touched.
    assert isinstance(captured["session_service"], _FakeSessionService)
    assert len(default_session_service_registry()) == 0


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

    monkeypatch.setattr("magi_agent.transport.gate5b_serving.run_gate5b4c3_live_runner_boundary_async", fake_boundary)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.collect_engine_to_boundary_result", fake_collect)

    # --- Flag explicitly OFF ("0"; the flag now defaults ON, so force legacy) ---
    monkeypatch.setenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", "0")
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


# ---------------------------------------------------------------------------
# 1c. Regression (2026-07-11 canary): the governed branch must not assume the
#     route config carries an ADK primitives loader. The env builder
#     (build_gate5b_user_visible_chat_route_config_from_env) NEVER sets one, so
#     on a real hosted bot route_config.adk_primitives_loader is None and the
#     pre-fix code died with `TypeError: 'NoneType' object is not callable`
#     before the runner started (502 runner_error on every governed turn).
#     Every earlier test injected adkPrimitivesLoader=_fake_primitives, which is
#     exactly why the crash was never caught. This test omits the loader, the
#     way the env builder does.
# ---------------------------------------------------------------------------


def test_governed_turn_defaults_primitives_loader_when_route_config_has_none(
    monkeypatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", "1")

    governed_called: dict[str, int] = {"count": 0}
    default_loader_called: dict[str, int] = {"count": 0}

    async def _noop_gen():  # noqa: ANN202
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        governed_called["count"] += 1
        return _noop_gen()

    async def fake_collect(*args: object, **kwargs: object) -> Gate5B4C3LiveRunnerBoundaryResult:
        return _make_boundary_result(output_text="governed answer")

    def fake_default_loader() -> Gate5B4C3LiveAdkPrimitives:
        default_loader_called["count"] += 1
        return _fake_primitives()

    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn
    )
    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.collect_engine_to_boundary_result",
        fake_collect,
    )
    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.load_gate5b4c3_live_adk_primitives",
        fake_default_loader,
    )

    runtime = _make_canary_runtime(tmp_path)
    # The env-built shape: no loader on the route config.
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        # adkPrimitivesLoader intentionally OMITTED (None).
    )
    assert runtime.gate5b_user_visible_chat_route_config.adk_primitives_loader is None

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers("c" * 64),
        json=_CANARY_BODY,
    )

    assert response.status_code == 200, response.json()
    assert governed_called["count"] == 1, "governed turn must run despite a None loader"
    assert default_loader_called["count"] >= 1, (
        "the governed branch must fall back to load_gate5b4c3_live_adk_primitives"
    )


# ---------------------------------------------------------------------------
# 1d. P5-M1a: governed branch + dropped runner input -> the drop refusal is
#     synthesized by build_gate5b4c3_input_drop_boundary_result WITHOUT calling
#     the legacy run_gate5b4c3_live_runner_boundary_async. Pre-M1a the governed
#     drop path fell through to that boundary call (the last governed ->
#     legacy-engine call); this test locks the decoupling.
# ---------------------------------------------------------------------------


def test_governed_dropped_input_uses_shim_not_legacy_boundary(
    monkeypatch, tmp_path: Any
) -> None:
    from magi_agent.shadow.gate5b4c3_runner_input_adapter import (
        Gate5B4C3RunnerInputAdapterResult,
    )

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", "1")

    boundary_called: dict[str, int] = {"count": 0}
    governed_called: dict[str, int] = {"count": 0}
    shim_called: dict[str, object] = {"count": 0, "drop_reason": None}

    async def fail_boundary(*args: object, **kwargs: object) -> object:
        # The drop path must NOT reach the legacy boundary under M1a.
        boundary_called["count"] += 1
        raise AssertionError(
            "run_gate5b4c3_live_runner_boundary_async must NOT be called on the "
            "governed drop path (M1a)"
        )

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        # A dropped input must never start a governed turn either.
        governed_called["count"] += 1
        raise AssertionError("run_governed_turn must NOT run for a dropped input")

    def fake_build_runner_input(generation: object) -> Gate5B4C3RunnerInputAdapterResult:
        # Force the input adapter to drop (an accepted-diagnostic request that the
        # adapter rejects, e.g. input_token_budget_exceeded).
        return Gate5B4C3RunnerInputAdapterResult(
            status="dropped",
            reason="input_token_budget_exceeded",
        )

    from magi_agent.shadow import gate5b4c3_live_runner_boundary as _boundary_mod

    real_shim_fn = _boundary_mod.build_gate5b4c3_input_drop_boundary_result

    def spy_shim(request: object, **kwargs: object):  # noqa: ANN201
        shim_called["count"] = int(shim_called["count"]) + 1  # type: ignore[arg-type]
        shim_called["drop_reason"] = kwargs.get("drop_reason")
        return real_shim_fn(request, **kwargs)

    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.run_gate5b4c3_live_runner_boundary_async",
        fail_boundary,
    )
    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn
    )
    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.build_gate5b4c3_runner_input",
        fake_build_runner_input,
    )
    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.build_gate5b4c3_input_drop_boundary_result",
        spy_shim,
    )

    runtime = _make_canary_runtime(tmp_path)
    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers("f" * 64),
        json=_CANARY_BODY,
    )

    # A dropped input surfaces as the SAME structured refusal the legacy boundary
    # produced pre-M1a: HTTP 502, python_error, reason input_adapter_drop,
    # fallback_to_typescript. (Verified against the pre-M1a boundary path.)
    assert response.status_code == 502, response.json()
    assert boundary_called["count"] == 0, "legacy boundary must not be called on a drop"
    assert governed_called["count"] == 0, "no governed turn for a dropped input"
    assert shim_called["count"] == 1, "the drop shim must build the refusal exactly once"
    assert shim_called["drop_reason"] == "input_token_budget_exceeded"
    body = response.json()
    assert body.get("status") == "python_error", body
    assert body.get("reason") == "input_adapter_drop", body
    assert body.get("fallbackStatus") == "fallback_to_typescript", body
