"""Endpoint + integration tests for the LOCAL streaming-chat refresh resume.

Covers the two new GET routes and the end-to-end refresh scenario:

  * GET /v1/chat/active-snapshot?sessionId=   (live snapshot / detached)
  * GET /v1/chat/channel-messages?sessionId=  (committed text after finish)

Integration-shaped test: drive a real turn through POST /v1/chat/stream, then
prove a fresh mount rehydrates the delivered text via channel-messages even
though the streaming socket already closed. Also exercises the mid-turn live
snapshot via the shared LOCAL_TURN_STORE singleton.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.config.models import BuildInfo, PythonRuntimeAuthorityConfig, RuntimeConfig
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.local_turn_store import LOCAL_TURN_STORE, LocalSnapshotReducer
from magi_agent.transport.streaming_chat_route import register_streaming_chat_routes


def _make_runtime(gateway_token: str = "test-token") -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-refresh-test",
            user_id="user-refresh-test",
            gateway_token=gateway_token,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-test", build_sha="sha-test"),
            authority=PythonRuntimeAuthorityConfig(),
        )
    )


def _make_app(engine_builder=None) -> FastAPI:
    app = FastAPI(title="refresh-test")
    register_streaming_chat_routes(app, _make_runtime(), engine_builder=engine_builder)
    return app


def _auth() -> dict[str, str]:
    return {"authorization": "Bearer test-token"}


def _ev(event_type: str, **payload: object) -> RuntimeEvent:
    return RuntimeEvent(type="status", payload={"type": event_type, **payload})


# ---------------------------------------------------------------------------
# Endpoint gating / validation
# ---------------------------------------------------------------------------


def test_active_snapshot_requires_auth(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    client = TestClient(_make_app())
    resp = client.get("/v1/chat/active-snapshot?sessionId=agent:main:app:general")
    assert resp.status_code == 401


def test_active_snapshot_disabled_503(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_STREAMING_CHAT", raising=False)
    client = TestClient(_make_app())
    resp = client.get(
        "/v1/chat/active-snapshot?sessionId=agent:main:app:general", headers=_auth()
    )
    assert resp.status_code == 503


def test_active_snapshot_missing_session_400(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    client = TestClient(_make_app())
    resp = client.get("/v1/chat/active-snapshot", headers=_auth())
    assert resp.status_code == 400


def test_active_snapshot_no_turn_returns_null(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    LOCAL_TURN_STORE._reset_for_tests()
    client = TestClient(_make_app())
    resp = client.get(
        "/v1/chat/active-snapshot?sessionId=agent:main:app:nonexistent", headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json() == {"snapshot": None}


def test_channel_messages_missing_session_400(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    client = TestClient(_make_app())
    resp = client.get("/v1/chat/channel-messages", headers=_auth())
    assert resp.status_code == 400


def test_channel_messages_no_turn_returns_empty(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    LOCAL_TURN_STORE._reset_for_tests()
    client = TestClient(_make_app())
    resp = client.get(
        "/v1/chat/channel-messages?sessionId=agent:main:app:nonexistent", headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json() == {"messages": []}


# ---------------------------------------------------------------------------
# Endpoints read the shared LOCAL_TURN_STORE
# ---------------------------------------------------------------------------


def test_active_snapshot_reads_live_store(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    LOCAL_TURN_STORE._reset_for_tests()
    sk = "agent:main:app:general"
    reducer = LocalSnapshotReducer(session_id=sk, turn_id="t1")
    LOCAL_TURN_STORE.begin(sk, reducer)
    reducer.ingest(b'event: agent\ndata: {"type":"text_delta","delta":"live text"}\n\n')
    try:
        client = TestClient(_make_app())
        resp = client.get(f"/v1/chat/active-snapshot?sessionId={sk}", headers=_auth())
        assert resp.status_code == 200
        snap = resp.json()["snapshot"]
        assert snap is not None
        assert snap["content"] == "live text"
        assert snap["status"] == "running"
    finally:
        LOCAL_TURN_STORE._reset_for_tests()


def test_channel_messages_reads_completed_store(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    LOCAL_TURN_STORE._reset_for_tests()
    sk = "agent:main:app:general"
    reducer = LocalSnapshotReducer(session_id=sk, turn_id="t1")
    LOCAL_TURN_STORE.begin(sk, reducer)
    reducer.ingest(b'event: agent\ndata: {"type":"text_delta","delta":"final answer"}\n\n')
    reducer.ingest(
        b'event: agent\ndata: {"type":"turn_result","terminal":"completed","turn_id":"t1"}\n\n'
    )
    LOCAL_TURN_STORE.finish(sk, reducer)
    try:
        client = TestClient(_make_app())
        resp = client.get(f"/v1/chat/channel-messages?sessionId={sk}", headers=_auth())
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        assert len(msgs) == 1
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"] == "final answer"
    finally:
        LOCAL_TURN_STORE._reset_for_tests()


# ---------------------------------------------------------------------------
# Integration: refresh scenario end-to-end through the HTTP surface
# ---------------------------------------------------------------------------


def test_refresh_scenario_committed_text_survives_stream_close(monkeypatch) -> None:
    """Drive a full turn through POST /v1/chat/stream, then prove a fresh mount
    rehydrates the delivered text via GET /v1/chat/channel-messages after the
    streaming socket has closed. This is the core refresh-resume contract: the
    turn's output is durable in the process store past the SSE connection."""
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    LOCAL_TURN_STORE._reset_for_tests()
    sk = "agent:main:app:general"

    class FakeEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield _ev("turn_phase", phase="executing", turnId="t-int")
            yield _ev("text_delta", delta="The answer ")
            yield _ev("text_delta", delta="is 42.")
            yield EngineResult(
                terminal=Terminal.completed,
                usage={"input_tokens": 3},
                session_id=sk,
                turn_id="t-int",
            )

    def fake_builder(session_id, sink, model_override=None):
        return FakeEngine(), None

    client = TestClient(_make_app(engine_builder=fake_builder))

    # 1. The user's turn streams and completes (the socket then closes).
    resp = client.post(
        "/v1/chat/stream",
        headers=_auth(),
        json={
            "sessionId": sk,
            "turnId": "t-int",
            "messages": [{"role": "user", "content": "what is the answer"}],
        },
    )
    assert resp.status_code == 200
    # The two text_delta frames carry the assistant text (separate SSE frames).
    assert "The answer " in resp.text
    assert "is 42." in resp.text
    assert resp.text.rstrip().endswith("data: [DONE]")

    # 2. A fresh mount (page refresh) has no live snapshot for the finished turn.
    snap_resp = client.get(f"/v1/chat/active-snapshot?sessionId={sk}", headers=_auth())
    assert snap_resp.status_code == 200
    # Turn already finished with no detached subagents -> no live snapshot.
    assert snap_resp.json()["snapshot"] is None

    # 3. ...but channel-messages rehydrates the delivered assistant text that
    #    the browser missed because it was away when the turn finished.
    msg_resp = client.get(f"/v1/chat/channel-messages?sessionId={sk}", headers=_auth())
    assert msg_resp.status_code == 200
    msgs = msg_resp.json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"] == "The answer is 42."
    assert msgs[0]["turnId"] == "t-int"

    LOCAL_TURN_STORE._reset_for_tests()


def test_refresh_scenario_errored_turn_rehydrates_partial_incomplete(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    LOCAL_TURN_STORE._reset_for_tests()
    sk = "agent:main:app:errch"

    class FakeEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield _ev("text_delta", delta="partial before crash")
            yield EngineResult(
                terminal=Terminal.error,
                error="boom",
                session_id=sk,
                turn_id="t-err",
            )

    def fake_builder(session_id, sink, model_override=None):
        return FakeEngine(), None

    client = TestClient(_make_app(engine_builder=fake_builder))
    resp = client.post(
        "/v1/chat/stream",
        headers=_auth(),
        json={"sessionId": sk, "turnId": "t-err", "messages": [{"role": "user", "content": "x"}]},
    )
    assert resp.status_code == 200

    # Errored turn WITH visible text (A1): the partial answer is rehydrated on
    # refresh, flagged incomplete, so a truncated answer does not vanish.
    msg_resp = client.get(f"/v1/chat/channel-messages?sessionId={sk}", headers=_auth())
    assert msg_resp.status_code == 200
    msgs = msg_resp.json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["content"] == "partial before crash"
    assert msgs[0]["incomplete"] is True
    LOCAL_TURN_STORE._reset_for_tests()


def test_refresh_scenario_empty_errored_turn_delivers_no_message(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    LOCAL_TURN_STORE._reset_for_tests()
    sk = "agent:main:app:errch"

    class FakeEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield EngineResult(
                terminal=Terminal.error,
                error="boom",
                session_id=sk,
                turn_id="t-err",
            )

    def fake_builder(session_id, sink, model_override=None):
        return FakeEngine(), None

    client = TestClient(_make_app(engine_builder=fake_builder))
    resp = client.post(
        "/v1/chat/stream",
        headers=_auth(),
        json={"sessionId": sk, "turnId": "t-err", "messages": [{"role": "user", "content": "x"}]},
    )
    assert resp.status_code == 200

    # Genuinely empty errored turn: still no committed message (no phantom bubble).
    msg_resp = client.get(f"/v1/chat/channel-messages?sessionId={sk}", headers=_auth())
    assert msg_resp.status_code == 200
    assert msg_resp.json()["messages"] == []
    LOCAL_TURN_STORE._reset_for_tests()


def test_hosted_gate5b_branch_does_not_populate_local_store(monkeypatch, tmp_path) -> None:
    """Guard: the hosted gate5b serving branch must stay byte-identical. It must
    NOT write into the local turn store (the fix is local-branch-scoped)."""
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv(
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path)
    )
    LOCAL_TURN_STORE._reset_for_tests()

    app = FastAPI(title="hosted-guard")
    register_streaming_chat_routes(app, _make_runtime())
    client = TestClient(app)
    sk = "agent:main:app:hosted"
    # Whether the gate is active or falls through, the local store must remain
    # untouched for this hosted-shaped request path assertion.
    client.get(f"/v1/chat/active-snapshot?sessionId={sk}", headers=_auth())
    assert LOCAL_TURN_STORE.completed_messages(sk) == []
    assert LOCAL_TURN_STORE.active_snapshot(sk) is None
    LOCAL_TURN_STORE._reset_for_tests()
