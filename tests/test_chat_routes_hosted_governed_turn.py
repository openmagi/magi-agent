"""Tests for the hosted governed-turn path in chat_routes (P5-M1b state).

P5-M1b: the legacy run_gate5b4c3_live_runner_boundary_async engine and the
MAGI_HOSTED_GOVERNED_TURN_ENABLED flag are deleted. Hosted turns now route
unconditionally through run_governed_turn -> MagiEngineDriver, collected by
collect_engine_to_boundary_result. This suite covers:

1. Governed path is called (run_governed_turn + collect called, no legacy boundary).
2. Profile-aware default: with flag unset and no safe-family profile, governed runs.
3. Same response shape as the pre-M1b flag-ON leg (the OFF leg is gone).
4. Durable session fronting via the single-flight session lease.
5. Regression: governed branch must default the primitives loader when route config
   has none.
6. P5-M1a regression: dropped runner input uses the drop shim, not the legacy boundary.
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
# 1. Governed path is called unconditionally (P5-M1b: no flag, no legacy engine)
# ---------------------------------------------------------------------------


def test_flag_on_uses_governed_turn(monkeypatch, tmp_path: Any) -> None:
    """Governed path: run_governed_turn + collect are called unconditionally."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")

    governed_called: dict[str, int] = {"count": 0}
    collect_called: dict[str, int] = {"count": 0}
    turn_ctx_seen: list[object] = []

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

    monkeypatch.setattr("magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.collect_engine_to_boundary_result", fake_collect)

    runtime = _make_canary_runtime(tmp_path)
    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers("b" * 64),
        json=_CANARY_BODY,
    )

    assert governed_called["count"] == 1, "run_governed_turn must be called exactly once"
    assert collect_called["count"] == 1, "collect_engine_to_boundary_result must be called"
    # Verify TurnContext type was passed
    assert len(turn_ctx_seen) == 1
    from magi_agent.runtime.turn_context import TurnContext
    assert isinstance(turn_ctx_seen[0], TurnContext), (
        f"run_governed_turn must receive a TurnContext, got {type(turn_ctx_seen[0])}"
    )
    assert response.status_code == 200, response.json()


# ---------------------------------------------------------------------------
# 1b. The governed path runs regardless of runtime profile (P5-M1b: the legacy
#     boundary and the MAGI_HOSTED_GOVERNED_TURN_ENABLED escape hatch are gone,
#     so even a safe-family profile now serves through run_governed_turn).
# ---------------------------------------------------------------------------


def _count_route_paths(monkeypatch, tmp_path: Any, *, digest: str) -> dict[str, int]:
    """Wire the governed/collect fakes and issue one canary request; return the
    per-path call counts. The caller sets the env that selects the profile."""
    counts: dict[str, int] = {"governed": 0, "collect": 0, "status": 0}

    async def _noop_gen():  # noqa: ANN202
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        counts["governed"] += 1
        return _noop_gen()

    async def fake_collect(*args: object, **kwargs: object) -> Gate5B4C3LiveRunnerBoundaryResult:
        counts["collect"] += 1
        return _make_boundary_result(output_text="governed answer")

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


def test_full_profile_uses_governed_turn(monkeypatch, tmp_path: Any) -> None:
    """Under the full/default profile the served turn routes through
    run_governed_turn (governed is the only hosted engine)."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)

    counts = _count_route_paths(monkeypatch, tmp_path, digest="e" * 64)

    assert counts["status"] == 200, counts
    assert counts["governed"] == 1, "governed path must run under the full profile"
    assert counts["collect"] == 1


def test_safe_profile_still_uses_governed_turn(monkeypatch, tmp_path: Any) -> None:
    """P5-M1b: the safe-family escape hatch to the legacy boundary is gone, so a
    safe-family runtime profile also serves through the governed path."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")

    counts = _count_route_paths(monkeypatch, tmp_path, digest="f" * 64)

    assert counts["status"] == 200, counts
    assert counts["governed"] == 1, "governed path runs even under a safe-family profile"
    assert counts["collect"] == 1


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


def test_governed_response_has_expected_top_level_shape(monkeypatch) -> None:
    """The governed path produces the expected top-level response key structure.

    (Pre-M1b this compared the flag-OFF legacy path to the flag-ON governed path;
    the legacy path is gone, so this now locks the governed response shape.)"""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")

    async def _noop_gen():  # noqa: ANN202
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        return _noop_gen()

    async def fake_collect(*args: object, **kwargs: object) -> Gate5B4C3LiveRunnerBoundaryResult:
        return _make_boundary_result(output_text="shape test answer")

    monkeypatch.setattr("magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.collect_engine_to_boundary_result", fake_collect)

    tmp = pathlib.Path(tempfile.mkdtemp())
    runtime = _make_canary_runtime(tmp)
    resp = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers("d" * 64),
        json=_CANARY_BODY,
    )

    assert resp.status_code == 200, resp.json()
    body = resp.json()

    # Assert the critical top-level fields are present with the expected types.
    for key in ("status", "fallbackStatus", "responseAuthority", "adk", "counter"):
        assert key in body, f"Missing key: {key}"


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

    governed_called: dict[str, int] = {"count": 0}
    shim_called: dict[str, object] = {"count": 0, "drop_reason": None}

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        # A dropped input must never start a governed turn.
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

    # A dropped input surfaces as a structured refusal built by the drop shim:
    # HTTP 502, python_error, reason input_adapter_drop, fallback_to_typescript.
    # (Pre-M1a this refusal came from the legacy boundary's error-result path;
    # M1a moved it to the shim and M1b retired the boundary entirely.)
    assert response.status_code == 502, response.json()
    assert governed_called["count"] == 0, "no governed turn for a dropped input"
    assert shim_called["count"] == 1, "the drop shim must build the refusal exactly once"
    assert shim_called["drop_reason"] == "input_token_budget_exceeded"
    body = response.json()
    assert body.get("status") == "python_error", body
    assert body.get("reason") == "input_adapter_drop", body
    assert body.get("fallbackStatus") == "fallback_to_typescript", body
