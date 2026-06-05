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

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.protocol import ControlResponse
from magi_agent.config.models import BuildInfo, PythonRuntimeAuthorityConfig, RuntimeConfig
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.active_turn import ACTIVE_TURNS, ActiveTurn
from magi_agent.transport.streaming_chat_route import (
    register_streaming_chat_routes,
    _streaming_chat_enabled,
    _extract_prompt_text,
)
from magi_agent.transport.streaming_sink import build_streaming_prompt_sink


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_runtime(*, gateway_token: str = "test-token") -> OpenMagiRuntime:
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
            authority=PythonRuntimeAuthorityConfig(),
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


# ---------------------------------------------------------------------------
# Test 4 — control-response delivers to an active turn's sink
# ---------------------------------------------------------------------------
def test_control_response_delivers_to_active_turn(monkeypatch) -> None:
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
def test_control_response_no_active_turn_404() -> None:
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
def test_cancel_sets_event() -> None:
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
def test_cancel_handoff_requested() -> None:
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
def test_cancel_unknown_session_409() -> None:
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


def test_control_response_malformed_json_returns_400() -> None:
    client = TestClient(_make_app())

    response = client.post(
        "/v1/chat/control-response",
        headers={**_auth_headers(), "content-type": "application/json"},
        content=b"not valid json {{{",
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed_json"


def test_cancel_malformed_json_returns_400() -> None:
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
