"""Tests for magi_agent.transport.streaming_chat_route.

All tests are synchronous; async code is driven via ``asyncio.run(...)`` where
needed (matching the convention in test_streaming_driver.py).  For the streaming
endpoint (test 3) we use the ``fastapi.testclient.TestClient`` which reads the
full SSE body synchronously — the same pattern used in ``test_local_dashboard.py``.

IMPORTANT: tests that register entries in the ``ACTIVE_TURNS`` singleton MUST
clean up after themselves (``ACTIVE_TURNS._turns.clear()`` or targeted removal)
to avoid cross-test interference.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from magi_agent.transport import streaming_chat_route as streaming_chat_route_module
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveAdkPrimitives,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterStore,
)
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.protocol import ControlResponse
from magi_agent.config.models import BuildInfo, PythonRuntimeAuthorityConfig, RuntimeConfig
from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolHostConfig,
)
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.chat import Gate5BUserVisibleChatRouteConfig
from magi_agent.transport.shadow_generations import (
    Gate5B4C3ShadowGenerationRouteConfig,
)
from magi_agent.transport.active_turn import ACTIVE_TURNS, ActiveTurn
from magi_agent.transport.streaming_chat_route import (
    register_streaming_chat_routes,
    _streaming_chat_enabled,
    _extract_prompt_text,
    _local_full_access,
    _drive_selected_gate5b_stream,
)
from magi_agent.transport.streaming_sink import build_streaming_prompt_sink


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_runtime(
    *,
    gateway_token: str = "test-token",
    authority: PythonRuntimeAuthorityConfig | None = None,
) -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-stream-test",
            user_id="user-stream-test",
            gateway_token=gateway_token,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-test", build_sha="sha-test"),
            authority=authority or PythonRuntimeAuthorityConfig(),
        )
    )


def _make_app(
    runtime: OpenMagiRuntime | None = None,
    *,
    engine_builder=None,
) -> FastAPI:
    """Build a bare FastAPI app with only the streaming-chat routes mounted."""
    app = FastAPI(title="stream-test")
    rt = runtime or _make_runtime()
    register_streaming_chat_routes(app, rt, engine_builder=engine_builder)
    return app


def _auth_headers(token: str = "test-token") -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def _sha256(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ev(event_type: str, **payload: object) -> RuntimeEvent:
    return RuntimeEvent(
        type="status",
        payload={"type": event_type, **payload},
        turn_id="t-route",
    )


def _data_lines(text: str) -> list[dict]:
    """Parse every ``data: {...}`` JSON line (skipping the [DONE] sentinel)."""
    out: list[dict] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            body = line[len("data:"):].strip()
            if body == "[DONE]":
                continue
            out.append(json.loads(body))
    return out


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


class _FakeAgent:
    created_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs


class _FakeSessionService:
    pass


class _FakeGenerateContentConfig:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeRunner:
    created_kwargs: dict[str, object] = {}
    run_kwargs: dict[str, object] = {}
    event_text = "selected full-toolhost ADK stream answer"

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        yield SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text=self.event_text)])
        )


def _fake_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _FakeRunner.created_kwargs = {}
    _FakeRunner.run_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_FakeRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _selected_runtime(
    tmp_path: Path,
    *,
    mocked_runner=None,
    full_toolhost: bool = False,
    authority: PythonRuntimeAuthorityConfig | None = None,
    primitives_loader=None,
) -> OpenMagiRuntime:
    runtime = _make_runtime(
        authority=authority
        or PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    default_loader = None if mocked_runner is not None else _fake_primitives
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-stream-test"),
        selectedOwnerUserIdDigest=_sha256("user-stream-test"),
        environment="production",
        environmentAllowlist=("production",),
        mockedRunner=mocked_runner,
        adkPrimitivesLoader=(
            primitives_loader if primitives_loader is not None else default_loader
        ),
    )
    if full_toolhost:
        runtime.gate5b_full_toolhost_config = Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-stream-test"),
                "selectedOwnerDigest": _sha256("user-stream-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        )
    runtime.gate5b4c3_shadow_generation_route_config = (
        Gate5B4C3ShadowGenerationRouteConfig(
            liveRunnerBoundaryEnabled=True,
            counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
            generationConfig=Gate5B4C3ShadowGenerationConfig(
                enabled=True,
                killSwitchActive=False,
                capStateInitialized=True,
                providerProjectSpendControlsVerified=True,
                selectedBotDigest=_sha256("bot-stream-test"),
                trustedOwnerUserIdDigest=_sha256("user-stream-test"),
                environment="production",
                allowedProviderLabels=("google",),
                allowedModelLabels=("gemini-3.5-flash",),
                allowedModelRoutes=("google:gemini-3.5-flash",),
                allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
                providerCredentialBindingRequired=False,
                approvedBudgets={
                    "maxDailyGenerationRuns": 4,
                    "maxDailyGenerationCostUsd": 0.05,
                    "maxCostUsd": 0.05,
                },
            ),
        )
    )
    return runtime


# ---------------------------------------------------------------------------
# MAGI_HOSTED_STREAMING_SERVE flag parsing (08-PR3) — default-OFF, strict truthy
# ---------------------------------------------------------------------------
def test_hosted_streaming_serve_flag_default_off(monkeypatch) -> None:
    from magi_agent.config.env import is_hosted_streaming_serve_enabled

    monkeypatch.delenv("MAGI_HOSTED_STREAMING_SERVE", raising=False)
    assert is_hosted_streaming_serve_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "ON", "True"])
def test_hosted_streaming_serve_flag_truthy(value: str, monkeypatch) -> None:
    from magi_agent.config.env import is_hosted_streaming_serve_enabled

    monkeypatch.setenv("MAGI_HOSTED_STREAMING_SERVE", value)
    assert is_hosted_streaming_serve_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "off", "", "  ", "banana"])
def test_hosted_streaming_serve_flag_falsy(value: str, monkeypatch) -> None:
    from magi_agent.config.env import is_hosted_streaming_serve_enabled

    monkeypatch.setenv("MAGI_HOSTED_STREAMING_SERVE", value)
    assert is_hosted_streaming_serve_enabled() is False


def test_hosted_streaming_serve_flag_registered_default_off() -> None:
    from magi_agent.config.flags import get_flag

    spec = get_flag("MAGI_HOSTED_STREAMING_SERVE")
    assert spec.default is False
    assert spec.kind == "bool"


# ---------------------------------------------------------------------------
# Test 1 — feature flag off → 503
# ---------------------------------------------------------------------------
def test_stream_disabled_returns_503(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_STREAMING_CHAT", raising=False)
    client = TestClient(_make_app())

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 503
    assert response.json() == {"error": "streaming_chat_disabled"}


# ---------------------------------------------------------------------------
# Test 2 — unauthorized → 401 (regardless of feature flag)
# ---------------------------------------------------------------------------
def test_stream_unauthorized_401(monkeypatch) -> None:
    # Auth check runs before the feature-flag check.
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    client = TestClient(_make_app())

    # Missing auth header
    response_no_auth = client.post(
        "/v1/chat/stream",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert response_no_auth.status_code == 401
    assert response_no_auth.json()["error"] == "unauthorized"

    # Wrong token
    response_bad_token = client.post(
        "/v1/chat/stream",
        headers={"authorization": "Bearer wrong"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert response_bad_token.status_code == 401
    assert response_bad_token.json()["error"] == "unauthorized"


# ---------------------------------------------------------------------------
# Test 3 — happy path: SSE stream returns event: agent, text_delta, turn_result, [DONE]
# ---------------------------------------------------------------------------
def test_stream_returns_event_stream_and_done(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")

    class FakeEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield _ev("text_delta", delta="streamed text")
            yield EngineResult(
                terminal=Terminal.completed,
                usage={"input_tokens": 5},
                session_id="s-route",
                turn_id="t-route",
            )

    def fake_builder(session_id: str, sink: object) -> tuple[object, object]:
        return FakeEngine(), None

    client = TestClient(_make_app(engine_builder=fake_builder))

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={
            "sessionId": "s-route",
            "turnId": "t-route",
            "messages": [{"role": "user", "content": "hello streaming"}],
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    text = response.text

    # Must contain the event: agent prefix
    assert "event: agent" in text

    # text_delta event present
    assert "text_delta" in text
    assert "streamed text" in text

    # turn_result event present
    assert "turn_result" in text

    # Stream terminates with [DONE]
    assert text.rstrip().endswith("data: [DONE]")

    # Validate payloads
    payloads = _data_lines(text)
    types = [p["type"] for p in payloads]
    assert "text_delta" in types
    assert types[-1] == "turn_result"
    turn_result = payloads[-1]
    assert turn_result["terminal"] == "completed"


def test_selected_full_toolhost_stream_uses_selected_canary_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path))

    class HeadlessEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield _ev("text_delta", delta="headless stream should not run")
            yield EngineResult(
                terminal=Terminal.completed,
                session_id="s-selected",
                turn_id="t-selected",
            )

    def headless_builder(session_id: str, sink: object) -> tuple[object, object]:
        return HeadlessEngine(), None

    client = TestClient(
        _make_app(
            runtime=_selected_runtime(tmp_path, full_toolhost=True),
            engine_builder=headless_builder,
        )
    )

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={
            "sessionId": "s-selected",
            "turnId": "t-selected",
            "messages": [{"role": "user", "content": "Use the selected toolhost."}],
        },
    )

    assert response.status_code == 200, response.text
    payloads = _data_lines(response.text)
    serialized = response.text
    assert "headless stream should not run" not in serialized
    assert "selected full-toolhost ADK stream answer" in serialized
    assert "Selected first-party toolhost active" in serialized
    assert [payload["type"] for payload in payloads][-1] == "turn_result"
    assert payloads[-1]["terminal"] == "completed"


def test_selected_full_toolhost_duplicate_replay_surfaces_status_not_none(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path))
    runtime = _selected_runtime(tmp_path, full_toolhost=True)
    client = TestClient(_make_app(runtime=runtime))
    body = {
        "sessionId": "s-selected-duplicate",
        "turnId": "t-selected-duplicate",
        "messages": [{"role": "user", "content": "Repeat the selected toolhost prompt."}],
    }

    first = client.post("/v1/chat/stream", headers=_auth_headers(), json=body)
    second = client.post("/v1/chat/stream", headers=_auth_headers(), json=body)

    assert first.status_code == 200, first.text
    assert _data_lines(first.text)[-1]["terminal"] == "completed"
    assert second.status_code == 200, second.text
    second_payloads = _data_lines(second.text)
    assert any(
        payload.get("type") == "error"
        and payload.get("code") == "counter_duplicate_replay"
        for payload in second_payloads
    )
    assert second_payloads[-1]["terminal"] == "error"
    assert second_payloads[-1]["error"] == "counter_duplicate_replay"
    assert "counter_none" not in second.text


def test_selected_gate5b_stream_emits_live_sink_events_before_completion(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    release_response = asyncio.Event()

    async def fake_selected_chat_response(
        runtime: object,
        body: object,
        *,
        request: object,
        public_event_sink=None,
    ) -> JSONResponse:
        assert public_event_sink is not None
        public_event_sink({"type": "text_delta", "delta": "early live chunk"})
        await release_response.wait()
        return JSONResponse(
            status_code=200,
            content={
                "status": "python_ready",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "early live chunk final answer",
                        }
                    }
                ],
            },
        )

    monkeypatch.setattr(
        streaming_chat_route_module,
        "run_gate5b_user_visible_chat_response",
        fake_selected_chat_response,
    )

    async def _collect() -> list[dict]:
        frames = _drive_selected_gate5b_stream(
            SimpleNamespace(),
            {"messages": [{"role": "user", "content": "stream selected"}]},
            SimpleNamespace(),
            session_id="s-selected-live",
            turn_id="t-selected-live",
        )
        first_task = asyncio.create_task(anext(frames))
        first_frame = await asyncio.wait_for(first_task, timeout=1)
        first_payloads = _data_lines(first_frame.decode("utf-8"))
        release_response.set()
        remaining_frames = [frame async for frame in frames]
        remaining_payloads = [
            payload
            for frame in remaining_frames
            for payload in _data_lines(frame.decode("utf-8"))
        ]
        return first_payloads + remaining_payloads

    payloads = asyncio.run(_collect())

    assert payloads[0]["type"] == "text_delta"
    assert payloads[0]["delta"] == "early live chunk"
    assert payloads[-1]["type"] == "turn_result"
    assert payloads[-1]["terminal"] == "completed"


def test_selected_gate5b_stream_skips_posthoc_text_after_live_text(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")

    async def fake_selected_chat_response(
        runtime: object,
        body: object,
        *,
        request: object,
        public_event_sink=None,
    ) -> JSONResponse:
        assert public_event_sink is not None
        public_event_sink({"type": "text_delta", "delta": "live chunk"})
        return JSONResponse(
            status_code=200,
            content={
                "status": "python_ready",
                "publicEvents": [
                    {"type": "turn_phase", "phase": "planning"},
                    {"type": "text_delta", "delta": "live chunk final aggregate"},
                ],
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "live chunk final aggregate",
                        }
                    }
                ],
            },
        )

    monkeypatch.setattr(
        streaming_chat_route_module,
        "run_gate5b_user_visible_chat_response",
        fake_selected_chat_response,
    )

    async def _collect() -> list[dict]:
        frames = _drive_selected_gate5b_stream(
            SimpleNamespace(),
            {"messages": [{"role": "user", "content": "stream selected"}]},
            SimpleNamespace(),
            session_id="s-selected-posthoc-text",
            turn_id="t-selected-posthoc-text",
        )
        return [
            payload
            async for frame in frames
            for payload in _data_lines(frame.decode("utf-8"))
        ]

    payloads = asyncio.run(_collect())

    text_payloads = [
        payload for payload in payloads if payload.get("type") == "text_delta"
    ]
    assert [payload["delta"] for payload in text_payloads] == ["live chunk"]
    assert any(
        payload.get("type") == "turn_phase" and payload.get("phase") == "planning"
        for payload in payloads
    )
    assert payloads[-1]["type"] == "turn_result"
    assert payloads[-1]["terminal"] == "completed"


def test_selected_gate5b_stream_cancels_response_task_when_client_closes(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    cancelled = asyncio.Event()

    async def fake_selected_chat_response(
        runtime: object,
        body: object,
        *,
        request: object,
        public_event_sink=None,
    ) -> JSONResponse:
        assert public_event_sink is not None
        public_event_sink({"type": "text_delta", "delta": "first chunk"})
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(
        streaming_chat_route_module,
        "run_gate5b_user_visible_chat_response",
        fake_selected_chat_response,
    )

    async def _run() -> bool:
        frames = _drive_selected_gate5b_stream(
            SimpleNamespace(),
            {"messages": [{"role": "user", "content": "stream selected"}]},
            SimpleNamespace(),
            session_id="s-selected-close",
            turn_id="t-selected-close",
        )
        first_frame = await asyncio.wait_for(anext(frames), timeout=1)
        first_payloads = _data_lines(first_frame.decode("utf-8"))
        assert first_payloads[0]["delta"] == "first chunk"
        await frames.aclose()
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        return cancelled.is_set()

    assert asyncio.run(_run()) is True


def test_selected_stream_failure_does_not_fall_back_to_headless_success(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")

    def failing_runner(request: object) -> dict[str, object]:
        raise ValueError("selected runner failed")

    class HeadlessEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield _ev("text_delta", delta="headless success must not appear")
            yield EngineResult(
                terminal=Terminal.completed,
                session_id="s-selected-fail",
                turn_id="t-selected-fail",
            )

    def headless_builder(session_id: str, sink: object) -> tuple[object, object]:
        return HeadlessEngine(), None

    client = TestClient(
        _make_app(
            runtime=_selected_runtime(tmp_path, mocked_runner=failing_runner),
            engine_builder=headless_builder,
        )
    )

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={
            "sessionId": "s-selected-fail",
            "turnId": "t-selected-fail",
            "messages": [{"role": "user", "content": "Selected canary failure."}],
        },
    )

    assert response.status_code == 200, response.text
    payloads = _data_lines(response.text)
    assert "headless success must not appear" not in response.text
    assert any(payload["type"] == "error" for payload in payloads)
    assert payloads[-1]["type"] == "turn_result"
    assert payloads[-1]["terminal"] == "error"
    assert payloads[-1]["error"] == "mocked_runner_error"


def test_stream_selected_gate_off_uses_headless_engine(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.delenv("CORE_AGENT_PYTHON_CHAT_ROUTE", raising=False)

    class HeadlessEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield _ev("text_delta", delta="headless default stream")
            yield EngineResult(
                terminal=Terminal.completed,
                session_id="s-headless",
                turn_id="t-headless",
            )

    def headless_builder(session_id: str, sink: object) -> tuple[object, object]:
        return HeadlessEngine(), None

    client = TestClient(_make_app(engine_builder=headless_builder))

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={
            "sessionId": "s-headless",
            "turnId": "t-headless",
            "messages": [{"role": "user", "content": "Default path."}],
        },
    )

    assert response.status_code == 200
    assert "headless default stream" in response.text
    assert _data_lines(response.text)[-1]["terminal"] == "completed"


# ---------------------------------------------------------------------------
# 08-PR3 — hosted streaming serve (MAGI_HOSTED_STREAMING_SERVE)
#
# With the flag ON the stream route must refuse with completions-equivalent
# fallback JSON whenever the selected gate5b gate is not active — it must NEVER
# fall through to the local headless engine (gate/counter/receipt bypass).
# With the flag OFF behavior is byte-identical to before (local fallthrough).
# ---------------------------------------------------------------------------

class _BypassCanaryEngine:
    """Headless engine that must never serve under hosted streaming serve."""

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
        yield _ev("text_delta", delta="hosted bypass must not appear")
        yield EngineResult(
            terminal=Terminal.completed,
            session_id="s-hosted",
            turn_id="t-hosted",
        )


def _bypass_canary_builder(session_id: str, sink: object) -> tuple[object, object]:
    return _BypassCanaryEngine(), None


def test_hosted_serve_chat_route_off_returns_chat_route_disabled(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_HOSTED_STREAMING_SERVE", "1")
    monkeypatch.delenv("CORE_AGENT_PYTHON_CHAT_ROUTE", raising=False)
    client = TestClient(_make_app(engine_builder=_bypass_canary_builder))

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={"messages": [{"role": "user", "content": "hosted serve"}]},
    )

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"] == "chat_route_disabled"
    assert "runtime" in payload
    assert "runtimeEngine" in payload
    assert "hosted bypass must not appear" not in response.text


def test_hosted_serve_canary_gate_disabled_returns_python_disabled(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_HOSTED_STREAMING_SERVE", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    # Plain runtime: no gate5b route config → canary gate disabled.
    client = TestClient(_make_app(engine_builder=_bypass_canary_builder))

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={"messages": [{"role": "user", "content": "hosted serve"}]},
    )

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "python_disabled"
    assert payload["reason"] == "canary_gate_disabled"
    assert payload["fallbackStatus"] == "fallback_to_typescript"
    assert payload["responseAuthority"] == "typescript"
    assert "hosted bypass must not appear" not in response.text


def test_hosted_serve_invalid_authority_returns_409(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_HOSTED_STREAMING_SERVE", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    # Selected digests match but the runtime authority does not allow
    # user-visible output → completions answers 409 invalid_authority.
    runtime = _selected_runtime(
        tmp_path,
        authority=PythonRuntimeAuthorityConfig(),
    )
    client = TestClient(
        _make_app(runtime=runtime, engine_builder=_bypass_canary_builder)
    )

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={"messages": [{"role": "user", "content": "hosted serve"}]},
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["status"] == "invalid_authority"
    assert payload["reason"] == "authority_gate_not_satisfied"
    assert payload["responseAuthority"] == "typescript"
    assert "hosted bypass must not appear" not in response.text


def test_hosted_serve_selected_active_still_streams(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_HOSTED_STREAMING_SERVE", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path))
    client = TestClient(
        _make_app(
            runtime=_selected_runtime(tmp_path, full_toolhost=True),
            engine_builder=_bypass_canary_builder,
        )
    )

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={
            "sessionId": "s-hosted-selected",
            "turnId": "t-hosted-selected",
            "messages": [{"role": "user", "content": "Use the selected toolhost."}],
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "hosted bypass must not appear" not in response.text
    assert "selected full-toolhost ADK stream answer" in response.text
    payloads = _data_lines(response.text)
    assert payloads[-1]["type"] == "turn_result"
    assert payloads[-1]["terminal"] == "completed"


def test_hosted_serve_gate2_canary_payload_dispatches_to_gate2_chat(
    monkeypatch,
    tmp_path: Path,
) -> None:
    # Completions parity: a gate2 sandbox-workspace canary payload must reach
    # the same _run_gate2_sandbox_workspace_canary_chat boundary (JSON, not SSE).
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_HOSTED_STREAMING_SERVE", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    from magi_agent.transport.gate2_sandbox_canary import (
        Gate2SandboxWorkspaceCanaryConfig,
    )

    runtime = _selected_runtime(tmp_path)
    runtime.gate2_sandbox_workspace_canary_config = Gate2SandboxWorkspaceCanaryConfig(
        enabled=True
    )
    seen: dict[str, object] = {}

    def fake_gate2_chat(rt, config, payload, *, request):
        seen["gate"] = payload.get("gate")
        seen["enabled"] = config.enabled
        return JSONResponse(status_code=200, content={"status": "gate2_dispatched"})

    monkeypatch.setattr(
        streaming_chat_route_module,
        "_run_gate2_sandbox_workspace_canary_chat",
        fake_gate2_chat,
    )
    client = TestClient(
        _make_app(runtime=runtime, engine_builder=_bypass_canary_builder)
    )

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={
            "gate": "gate2_sandbox_workspace_canary",
            "messages": [{"role": "user", "content": "gate2 canary"}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "gate2_dispatched"}
    assert seen == {"gate": "gate2_sandbox_workspace_canary", "enabled": True}


def test_hosted_serve_gate2_absent_payload_takes_selected_stream(
    monkeypatch,
    tmp_path: Path,
) -> None:
    # No gate2 config → even a gate2-shaped payload flows down the normal
    # selected gate5b stream path (mirrors completions).
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_HOSTED_STREAMING_SERVE", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path))

    def fail_gate2_chat(rt, config, payload, *, request):  # pragma: no cover
        raise AssertionError("gate2 dispatch must not run without gate2 config")

    monkeypatch.setattr(
        streaming_chat_route_module,
        "_run_gate2_sandbox_workspace_canary_chat",
        fail_gate2_chat,
    )
    client = TestClient(
        _make_app(
            runtime=_selected_runtime(tmp_path, full_toolhost=True),
            engine_builder=_bypass_canary_builder,
        )
    )

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={
            "gate": "gate2_sandbox_workspace_canary",
            "sessionId": "s-hosted-gate2-absent",
            "turnId": "t-hosted-gate2-absent",
            "messages": [{"role": "user", "content": "Use the selected toolhost."}],
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "selected full-toolhost ADK stream answer" in response.text


def test_hosted_serve_malformed_json_returns_completions_shape(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_HOSTED_STREAMING_SERVE", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    client = TestClient(
        _make_app(
            runtime=_selected_runtime(tmp_path),
            engine_builder=_bypass_canary_builder,
        )
    )

    response = client.post(
        "/v1/chat/stream",
        headers={**_auth_headers(), "content-type": "application/json"},
        content="{not json",
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["status"] == "python_error"
    assert payload["reason"] == "malformed_json"
    assert payload["fallbackStatus"] == "fallback_to_typescript"
    assert payload["responseAuthority"] == "typescript"


def test_hosted_serve_malformed_json_route_disabled_returns_python_disabled(
    monkeypatch,
) -> None:
    # Completions checks the canary route gate BEFORE parsing the body, so a
    # malformed body on a gate-disabled pod answers 503 python_disabled.
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_HOSTED_STREAMING_SERVE", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    client = TestClient(_make_app(engine_builder=_bypass_canary_builder))

    response = client.post(
        "/v1/chat/stream",
        headers={**_auth_headers(), "content-type": "application/json"},
        content="{not json",
    )

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "python_disabled"
    assert payload["reason"] == "canary_gate_disabled"


def test_hosted_serve_malformed_json_chat_route_off_returns_chat_route_disabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_HOSTED_STREAMING_SERVE", "1")
    monkeypatch.delenv("CORE_AGENT_PYTHON_CHAT_ROUTE", raising=False)
    client = TestClient(_make_app(engine_builder=_bypass_canary_builder))

    response = client.post(
        "/v1/chat/stream",
        headers={**_auth_headers(), "content-type": "application/json"},
        content="{not json",
    )

    assert response.status_code == 503
    assert response.json()["error"] == "chat_route_disabled"


def test_malformed_json_flag_off_keeps_legacy_shape(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.delenv("MAGI_HOSTED_STREAMING_SERVE", raising=False)
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    client = TestClient(_make_app())

    response = client.post(
        "/v1/chat/stream",
        headers={**_auth_headers(), "content-type": "application/json"},
        content="{not json",
    )

    assert response.status_code == 400
    assert response.json() == {"error": "malformed_json"}


# ---------------------------------------------------------------------------
# 08-PR3 — gate/counter/receipt/critic EQUIVALENCE with /v1/chat/completions
#
# The hosted stream route serves through the same
# run_gate5b_user_visible_chat_response boundary as completions; these tests
# pin that equivalence end-to-end: identical counter reservation + finish
# receipt records, identical usage-receipt scheduling, and identical egress
# critic invocation for the same request body.
# ---------------------------------------------------------------------------

class _UsageFakeRunner(_FakeRunner):
    """Fake ADK runner whose final event carries usage metadata."""

    event_text = "selected equivalence ADK stream answer"

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        yield SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text=self.event_text)]),
            usage_metadata=SimpleNamespace(
                prompt_token_count=17,
                candidates_token_count=5,
                total_token_count=22,
            ),
        )


def _usage_fake_primitives() -> Gate5B4C3LiveAdkPrimitives:
    primitives = _fake_primitives()
    return Gate5B4C3LiveAdkPrimitives(
        Agent=primitives.Agent,
        Runner=_UsageFakeRunner,
        InMemorySessionService=primitives.InMemorySessionService,
        Content=primitives.Content,
        Part=primitives.Part,
        GenerateContentConfig=primitives.GenerateContentConfig,
    )


_COUNTER_TIMESTAMP_KEYS = {"reservedAtMs", "finishedAtMs"}


def _normalized_counters(path: Path) -> object:
    """Counter-store JSON with wall-clock timestamps masked."""

    def _mask(node: object) -> object:
        if isinstance(node, dict):
            return {
                key: (0 if key in _COUNTER_TIMESTAMP_KEYS else _mask(value))
                for key, value in node.items()
            }
        if isinstance(node, list):
            return [_mask(item) for item in node]
        return node

    return _mask(json.loads(path.read_text()))


def test_hosted_stream_counter_receipt_critic_equivalence_with_completions(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_HOSTED_STREAMING_SERVE", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")

    from magi_agent.app import create_app
    from magi_agent.transport import chat_routes as chat_routes_module

    # Capture the usage-receipt scheduling seam (sync, deterministic) and force
    # the egress critic gate ON with a capturing fake — both seams live in the
    # shared serving boundary, so both routes must hit them identically.
    receipt_calls: list[dict[str, object]] = []

    def fake_schedule_receipt(*, runtime, model, usage, turn_id) -> None:
        receipt_calls.append(
            {
                "bot_id": runtime.config.bot_id,
                "model": model,
                "usage": dict(usage) if usage else None,
                "turn_id": turn_id,
            }
        )

    critic_calls: list[dict[str, object]] = []

    async def fake_critic_gate(*, payload, draft_text, gate1a_bundle):
        critic_calls.append({"draft_text": draft_text})
        return None

    monkeypatch.setattr(
        chat_routes_module,
        "_schedule_runtime_direct_usage_receipt",
        fake_schedule_receipt,
    )
    monkeypatch.setattr(chat_routes_module, "is_egress_gate_enabled", lambda: True)
    monkeypatch.setattr(
        chat_routes_module, "_maybe_run_egress_critic_gate", fake_critic_gate
    )

    body = {
        "sessionId": "s-equivalence",
        "turnId": "t-equivalence",
        "messages": [{"role": "user", "content": "equivalence probe"}],
    }

    def _serve(path: str, store_dir: Path):
        store_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(
            "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(store_dir)
        )
        runtime = _selected_runtime(
            store_dir,
            full_toolhost=True,
            primitives_loader=_usage_fake_primitives,
        )
        client = TestClient(create_app(runtime))
        return client.post(path, headers=_auth_headers(), json=body)

    completions_dir = tmp_path / "completions"
    stream_dir = tmp_path / "stream"

    completions_response = _serve("/v1/chat/completions", completions_dir)
    completions_receipts = list(receipt_calls)
    completions_critic = list(critic_calls)
    receipt_calls.clear()
    critic_calls.clear()

    stream_response = _serve("/v1/chat/stream", stream_dir)
    stream_receipts = list(receipt_calls)
    stream_critic = list(critic_calls)

    # Both served successfully through the selected path.
    assert completions_response.status_code == 200, completions_response.text
    assert completions_response.json()["status"] == "python_ready"
    assert stream_response.status_code == 200, stream_response.text
    assert stream_response.headers["content-type"].startswith("text/event-stream")
    stream_payloads = _data_lines(stream_response.text)
    assert stream_payloads[-1]["terminal"] == "completed"

    # Counter store equivalence: same scope, same request digest, same
    # reservation cost, same shadowGenerationId, same finish status/receipt —
    # only wall-clock timestamps may differ.
    completions_counters = _normalized_counters(completions_dir / "counters.json")
    stream_counters = _normalized_counters(stream_dir / "counters.json")
    assert stream_counters == completions_counters

    # Usage-receipt scheduling equivalence (same model/usage/turn digest).
    assert completions_receipts, "completions path must schedule a usage receipt"
    assert stream_receipts == completions_receipts

    # Egress critic equivalence (same draft text reaches the critic).
    assert completions_critic, "completions path must invoke the egress critic"
    assert stream_critic == completions_critic


def test_hosted_serve_flag_off_gate_inactive_keeps_headless_fallthrough(
    monkeypatch,
) -> None:
    # Flag OFF (unset) → byte-identical legacy behavior: chat route on but the
    # canary gate inactive falls through to the local headless engine.
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.delenv("MAGI_HOSTED_STREAMING_SERVE", raising=False)
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")

    class HeadlessEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield _ev("text_delta", delta="legacy headless fallthrough")
            yield EngineResult(
                terminal=Terminal.completed,
                session_id="s-legacy",
                turn_id="t-legacy",
            )

    def headless_builder(session_id: str, sink: object) -> tuple[object, object]:
        return HeadlessEngine(), None

    client = TestClient(_make_app(engine_builder=headless_builder))

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={"messages": [{"role": "user", "content": "Default path."}]},
    )

    assert response.status_code == 200
    assert "legacy headless fallthrough" in response.text
    assert _data_lines(response.text)[-1]["terminal"] == "completed"


# ---------------------------------------------------------------------------
# Test 4 — control-response delivers to an active turn's sink
# ---------------------------------------------------------------------------
def test_control_response_delivers_to_active_turn(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    session_id = "s-ctrl-test"
    turn_id = "t-ctrl-test"

    queue: asyncio.Queue[object] = asyncio.Queue()
    sink = build_streaming_prompt_sink(queue, turn_id=turn_id)
    cancel_event = asyncio.Event()
    turn = ActiveTurn(
        session_id=session_id,
        turn_id=turn_id,
        cancel=cancel_event,
        sink=sink,
    )
    ACTIVE_TURNS.register(turn)

    try:
        from magi_agent.runtime.control import ControlRequest

        request = ControlRequest(
            requestId="req-deliver",
            turnId=turn_id,
            toolName="TestTool",
            arguments={},
            reason="test",
        )

        # Kick off an ask so deliver() resolves it.
        loop = asyncio.new_event_loop()

        async def _run() -> None:
            ask_task = loop.create_task(sink.ask(request))
            # Let the ask park.
            await asyncio.sleep(0)

            # POST /v1/chat/control-response via a fresh client
            client = TestClient(_make_app())
            response = client.post(
                "/v1/chat/control-response",
                headers=_auth_headers(),
                json={
                    "sessionId": session_id,
                    "request_id": "req-deliver",
                    "response": {"decision": "allow"},
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "delivered"
            assert body["request_id"] == "req-deliver"

            # The ask should have been resolved.
            decision = await asyncio.wait_for(ask_task, timeout=2)
            assert getattr(decision, "kind", None) == "allow"

        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()
    finally:
        ACTIVE_TURNS.unregister(session_id, turn_id)


# ---------------------------------------------------------------------------
# Test 5 — control-response with unknown session → 404
# ---------------------------------------------------------------------------
def test_control_response_no_active_turn_404(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    client = TestClient(_make_app())

    response = client.post(
        "/v1/chat/control-response",
        headers=_auth_headers(),
        json={
            "sessionId": "no-such-session-xyz",
            "request_id": "req-1",
            "response": {"decision": "allow"},
        },
    )

    assert response.status_code == 404
    assert response.json()["error"] == "no_active_turn"


# ---------------------------------------------------------------------------
# Test 6a — cancel sets the cancel event on the active turn
# ---------------------------------------------------------------------------
def test_cancel_sets_event(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    session_id = "s-cancel-test"
    turn_id = "t-cancel-test"

    queue: asyncio.Queue[object] = asyncio.Queue()
    sink = build_streaming_prompt_sink(queue, turn_id=turn_id)
    cancel_event = asyncio.Event()
    turn = ActiveTurn(
        session_id=session_id,
        turn_id=turn_id,
        cancel=cancel_event,
        sink=sink,
    )
    ACTIVE_TURNS.register(turn)

    try:
        client = TestClient(_make_app())

        response = client.post(
            "/v1/chat/cancel",
            headers=_auth_headers(),
            json={"sessionId": session_id},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "cancelling"
        assert body["activeTurnCompatible"] is True
        assert body["handoffRequested"] is False

        # The cancel event must be set.
        assert cancel_event.is_set()
    finally:
        ACTIVE_TURNS.unregister(session_id, turn_id)


# ---------------------------------------------------------------------------
# Test 6b — cancel with handoffRequested=True
# ---------------------------------------------------------------------------
def test_cancel_handoff_requested(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    session_id = "s-cancel-handoff"
    turn_id = "t-cancel-handoff"

    queue: asyncio.Queue[object] = asyncio.Queue()
    sink = build_streaming_prompt_sink(queue, turn_id=turn_id)
    cancel_event = asyncio.Event()
    turn = ActiveTurn(
        session_id=session_id,
        turn_id=turn_id,
        cancel=cancel_event,
        sink=sink,
    )
    ACTIVE_TURNS.register(turn)

    try:
        client = TestClient(_make_app())

        response = client.post(
            "/v1/chat/cancel",
            headers=_auth_headers(),
            json={"sessionId": session_id, "handoffRequested": True},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["handoffRequested"] is True
        assert cancel_event.is_set()
    finally:
        ACTIVE_TURNS.unregister(session_id, turn_id)


# ---------------------------------------------------------------------------
# Test 6c — cancel unknown session → 409
# ---------------------------------------------------------------------------
def test_cancel_unknown_session_409(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    client = TestClient(_make_app())

    response = client.post(
        "/v1/chat/cancel",
        headers=_auth_headers(),
        json={"sessionId": "no-such-session-cancel"},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "no_active_turn"
    assert body["activeTurnCompatible"] is False


# ---------------------------------------------------------------------------
# Test 7 — prompt extraction helper
# ---------------------------------------------------------------------------
def test_extract_prompt_text_string_content() -> None:
    body = {
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello world"},
        ]
    }
    result = _extract_prompt_text(body)
    assert "Hello world" in result
    assert "You are helpful." in result


def test_extract_prompt_text_block_content() -> None:
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "First block"},
                    {"type": "text", "text": "Second block"},
                ],
            }
        ]
    }
    result = _extract_prompt_text(body)
    assert "First block" in result
    assert "Second block" in result


def test_extract_prompt_text_empty_body() -> None:
    assert _extract_prompt_text({}) == ""
    assert _extract_prompt_text(None) == ""
    assert _extract_prompt_text("not a dict") == ""


# ---------------------------------------------------------------------------
# Test 8 — _streaming_chat_enabled gate
# ---------------------------------------------------------------------------
def test_streaming_chat_enabled_flag(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_STREAMING_CHAT", raising=False)
    assert not _streaming_chat_enabled()

    for truthy in ("1", "true", "yes", "on", "TRUE", "YES"):
        monkeypatch.setenv("MAGI_STREAMING_CHAT", truthy)
        assert _streaming_chat_enabled(), f"expected truthy for {truthy!r}"

    for falsy in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("MAGI_STREAMING_CHAT", falsy)
        assert not _streaming_chat_enabled(), f"expected falsy for {falsy!r}"


def test_local_full_access_only_matches_loopback_owner() -> None:
    local_runtime = OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token="local-dev-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-test", build_sha="sha-test"),
        )
    )
    hosted_runtime = _make_runtime(gateway_token="local-dev-token")

    assert _local_full_access(local_runtime)
    assert not _local_full_access(hosted_runtime)


def test_default_stream_builder_bypasses_permissions_for_local_owner(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    captured: dict[str, object] = {}

    class FakeEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield EngineResult(
                terminal=Terminal.completed,
                session_id=turn_input["session_id"],
                turn_id=turn_input["turn_id"],
            )

    def fake_build_headless_runtime(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(engine=FakeEngine(), gate=None)

    import magi_agent.cli.wiring as wiring

    monkeypatch.setattr(wiring, "build_headless_runtime", fake_build_headless_runtime)
    runtime = OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token="local-dev-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-test", build_sha="sha-test"),
        )
    )
    client = TestClient(_make_app(runtime=runtime))

    response = client.post(
        "/v1/chat/stream",
        headers={"authorization": "Bearer local-dev-token"},
        json={
            "sessionId": "s-local",
            "turnId": "t-local",
            "messages": [{"role": "user", "content": "run local tool"}],
        },
    )

    assert response.status_code == 200
    assert captured["permission_mode"] == "bypassPermissions"


def test_default_stream_builder_keeps_default_permissions_for_hosted_runtime(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    captured: dict[str, object] = {}

    class FakeEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield EngineResult(
                terminal=Terminal.completed,
                session_id=turn_input["session_id"],
                turn_id=turn_input["turn_id"],
            )

    def fake_build_headless_runtime(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(engine=FakeEngine(), gate=None)

    import magi_agent.cli.wiring as wiring

    monkeypatch.setattr(wiring, "build_headless_runtime", fake_build_headless_runtime)
    client = TestClient(_make_app())

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={
            "sessionId": "s-hosted",
            "turnId": "t-hosted",
            "messages": [{"role": "user", "content": "run hosted tool"}],
        },
    )

    assert response.status_code == 200
    assert captured["permission_mode"] == "default"


# ---------------------------------------------------------------------------
# Test 9 — malformed JSON → 400
# ---------------------------------------------------------------------------
def test_stream_malformed_json_returns_400(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    client = TestClient(_make_app())

    response = client.post(
        "/v1/chat/stream",
        headers={**_auth_headers(), "content-type": "application/json"},
        content=b"not valid json {{{",
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed_json"


def test_control_response_malformed_json_returns_400(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    client = TestClient(_make_app())

    response = client.post(
        "/v1/chat/control-response",
        headers={**_auth_headers(), "content-type": "application/json"},
        content=b"not valid json {{{",
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed_json"


def test_cancel_malformed_json_returns_400(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    client = TestClient(_make_app())

    response = client.post(
        "/v1/chat/cancel",
        headers={**_auth_headers(), "content-type": "application/json"},
        content=b"not valid json {{{",
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed_json"


# ---------------------------------------------------------------------------
# Test 10 — session_id fallback from header
# ---------------------------------------------------------------------------
def test_stream_session_id_from_header(monkeypatch) -> None:
    """When sessionId is absent from body, fall back to x-openclaw-session-key header."""
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")

    captured_ids: dict[str, str] = {}

    class FakeEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            captured_ids["session_id"] = turn_input.get("session_id", "")
            captured_ids["turn_id"] = turn_input.get("turn_id", "")
            yield EngineResult(
                terminal=Terminal.completed,
                session_id=captured_ids["session_id"],
                turn_id=captured_ids["turn_id"],
            )

    def fake_builder(session_id: str, sink: object) -> tuple[object, object]:
        return FakeEngine(), None

    client = TestClient(_make_app(engine_builder=fake_builder))

    response = client.post(
        "/v1/chat/stream",
        headers={
            **_auth_headers(),
            "x-openclaw-session-key": "header-session-id",
        },
        json={"messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert captured_ids.get("session_id") == "header-session-id"


# ---------------------------------------------------------------------------
# Test 11 — blank gateway token always → 401 (fix 1)
# ---------------------------------------------------------------------------
def test_blank_gateway_token_rejected(monkeypatch) -> None:
    """A runtime configured with an empty gateway_token must reject all requests."""
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    # Build app with empty gateway_token
    rt = _make_runtime(gateway_token="")
    client = TestClient(_make_app(runtime=rt))

    # Even sending "Bearer " (the exact match that the old buggy code would accept)
    response = client.post(
        "/v1/chat/stream",
        headers={"authorization": "Bearer "},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"

    # Also reject empty auth header
    response2 = client.post(
        "/v1/chat/stream",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response2.status_code == 401


# ---------------------------------------------------------------------------
# Test 12 — missing sessionId → 400 for control-response (fix 2)
# ---------------------------------------------------------------------------
def test_control_response_missing_session_returns_400(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    client = TestClient(_make_app())

    # No sessionId in body, no x-openclaw-session-key header
    response = client.post(
        "/v1/chat/control-response",
        headers=_auth_headers(),
        json={"request_id": "req-1", "response": {"decision": "allow"}},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "missing_session_id"


# ---------------------------------------------------------------------------
# Test 13 — missing sessionId → 400 for cancel (fix 2)
# ---------------------------------------------------------------------------
def test_cancel_missing_session_returns_400(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    client = TestClient(_make_app())

    # No sessionId in body, no x-openclaw-session-key header
    response = client.post(
        "/v1/chat/cancel",
        headers=_auth_headers(),
        json={"handoffRequested": False},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "missing_session_id"


# ---------------------------------------------------------------------------
# Test 14 — oversized control-response body → 400 (fix 4)
# ---------------------------------------------------------------------------
def test_control_response_oversize_returns_400(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    client = TestClient(_make_app())

    # Build a response dict whose JSON serialisation exceeds 8192 bytes
    oversized_value = "x" * 9000
    response = client.post(
        "/v1/chat/control-response",
        headers=_auth_headers(),
        json={
            "sessionId": "some-session",
            "request_id": "req-big",
            "response": {"data": oversized_value},
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "response_too_large"


# ---------------------------------------------------------------------------
# Test 15 — control-response disabled → 503 (fix 3)
# ---------------------------------------------------------------------------
def test_control_response_disabled_503(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_STREAMING_CHAT", raising=False)
    client = TestClient(_make_app())

    response = client.post(
        "/v1/chat/control-response",
        headers=_auth_headers(),
        json={"sessionId": "s-1", "request_id": "r-1", "response": {}},
    )
    assert response.status_code == 503
    assert response.json()["error"] == "streaming_chat_disabled"


# ---------------------------------------------------------------------------
# Test 16 — cancel disabled → 503 (fix 3)
# ---------------------------------------------------------------------------
def test_cancel_disabled_503(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_STREAMING_CHAT", raising=False)
    client = TestClient(_make_app())

    response = client.post(
        "/v1/chat/cancel",
        headers=_auth_headers(),
        json={"sessionId": "s-1"},
    )
    assert response.status_code == 503
    assert response.json()["error"] == "streaming_chat_disabled"


# ---------------------------------------------------------------------------
# Test 17 — engine build-time failure → 500 engine_build_failed (overall review)
# ---------------------------------------------------------------------------
def test_stream_engine_build_failure_returns_500(monkeypatch) -> None:
    """A build-time exception must surface as a clean JSON 500, not a bare 500.

    The engine_builder runs synchronously before the StreamingResponse begins, so
    no SSE bytes have been sent yet — returning a JSON 500 is the correct contract.
    """
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")

    def boom_builder(session_id: str, sink: object) -> tuple[object, object]:
        raise RuntimeError("engine wiring blew up at /home/ocuser/.openclaw/secret")

    client = TestClient(_make_app(engine_builder=boom_builder))

    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={
            "sessionId": "s-build-fail",
            "turnId": "t-build-fail",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 500
    assert response.json() == {"error": "engine_build_failed"}
