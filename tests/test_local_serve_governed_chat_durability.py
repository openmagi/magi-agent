"""Tests for local-serve governed-chat durability (F1/F1b/F2/F3).

Design ref: docs/plans/2026-07-15-local-serve-governed-chat-durability-design.md

Covers:
  * F1  -- the governed gate5b local-serve turn registers a live snapshot in
           LOCAL_TURN_STORE while streaming and a completed record after finish
           (routes through the detached pump like the LOCAL branch).
  * F1  -- setting the pump cancel event mid-stream aborts the gate5b response
           task and yields an aborted-terminal frame.
  * F1b -- the pump writes durable user + assistant rows to a ChannelMessageStore
           (incomplete flag on error terminal); no rows / no crash when the
           store accessor returns None.
  * F2  -- GET full=1 falls back to in-memory completed_messages when the durable
           store is empty; merges a distinct in-memory turnId; dedupes on match.
  * F3  -- an assembled assistant "message" transcript record is emitted when the
           sink is active; nothing when inactive.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from magi_agent.config.models import (
    BuildInfo,
    PythonRuntimeAuthorityConfig,
    RuntimeConfig,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.storage.channel_message_store import (
    ChannelMessageStore,
    _reset_channel_message_store_singletons_for_tests,
)
from magi_agent.transport import streaming_chat_route as streaming_chat_route_module
from magi_agent.transport.local_turn_pump import drive_detached_local_stream
from magi_agent.transport.local_turn_store import (
    LOCAL_TURN_STORE,
    LocalSnapshotReducer,
    LocalTurnStore,
)
from magi_agent.transport.streaming_chat_route import (
    _drive_selected_gate5b_stream,
    register_streaming_chat_routes,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_runtime(gateway_token: str = "test-token") -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-durability-test",
            user_id="user-durability-test",
            gateway_token=gateway_token,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-test", build_sha="sha-test"),
            authority=PythonRuntimeAuthorityConfig(),
        )
    )


def _make_app() -> FastAPI:
    app = FastAPI(title="durability-test")
    register_streaming_chat_routes(app, _make_runtime())
    return app


def _auth() -> dict[str, str]:
    return {"authorization": "Bearer test-token"}


def _agent_frame(payload: dict) -> bytes:
    return f"event: agent\ndata: {json.dumps(payload)}\n\n".encode()


def _data_lines(text: str) -> list[dict]:
    out: list[dict] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            body = line[len("data:") :].strip()
            if body == "[DONE]":
                continue
            out.append(json.loads(body))
    return out


# ---------------------------------------------------------------------------
# F1 -- gate5b local-serve turn goes through the detached pump
# ---------------------------------------------------------------------------


def test_gate5b_stream_through_pump_registers_snapshot_and_completed(monkeypatch) -> None:
    """A governed gate5b stream wrapped in the detached pump registers a live
    snapshot while streaming and a completed record after finish, so the refresh
    endpoints can rehydrate."""
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    store = LocalTurnStore()
    sk = "agent:main:app:general"
    release = asyncio.Event()
    snapshot_seen: dict[str, object] = {}

    async def fake_selected_chat_response(
        runtime, body, *, request, public_event_sink=None
    ) -> JSONResponse:
        assert public_event_sink is not None
        public_event_sink({"type": "text_delta", "delta": "gate5b answer"})
        await release.wait()
        return JSONResponse(
            status_code=200,
            content={
                "status": "python_ready",
                "choices": [
                    {"message": {"role": "assistant", "content": "gate5b answer"}}
                ],
            },
        )

    monkeypatch.setattr(
        streaming_chat_route_module,
        "run_gate5b_user_visible_chat_response",
        fake_selected_chat_response,
    )

    async def _run() -> None:
        cancel = asyncio.Event()
        inner = _drive_selected_gate5b_stream(
            SimpleNamespace(),
            {"messages": [{"role": "user", "content": "hi gate5b"}]},
            SimpleNamespace(),
            session_id=sk,
            turn_id="t-g5b",
            cancel=cancel,
        )
        gen = drive_detached_local_stream(
            inner,
            session_id=sk,
            turn_id="t-g5b",
            cancel=cancel,
            store=store,
        )
        # Consume the first live frame -> the pump has begun the turn.
        first = await asyncio.wait_for(anext(gen), timeout=2)
        assert "gate5b answer" in first.decode("utf-8")
        # Mid-stream: a live snapshot exists in the store.
        snapshot_seen["live"] = store.active_snapshot(sk)
        # Let the response finish and drain the rest.
        release.set()
        async for _ in gen:
            pass

    asyncio.run(_run())

    assert snapshot_seen["live"] is not None
    msgs = store.completed_messages(sk)
    assert len(msgs) == 1
    assert msgs[0]["content"] == "gate5b answer"
    assert msgs[0]["turnId"] == "t-g5b"


def test_gate5b_stream_cancel_event_aborts_response_task(monkeypatch) -> None:
    """Setting the shared cancel event mid-stream cancels the gate5b response
    task and yields an aborted-terminal frame (the idle-abort watchdog path)."""
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    cancel = asyncio.Event()
    cancelled = {"value": False}
    released = asyncio.Event()

    async def fake_selected_chat_response(
        runtime, body, *, request, public_event_sink=None
    ) -> JSONResponse:
        assert public_event_sink is not None
        public_event_sink({"type": "text_delta", "delta": "early chunk"})
        try:
            await released.wait()
        except asyncio.CancelledError:
            cancelled["value"] = True
            raise
        return JSONResponse(status_code=200, content={"status": "python_ready"})

    monkeypatch.setattr(
        streaming_chat_route_module,
        "run_gate5b_user_visible_chat_response",
        fake_selected_chat_response,
    )

    async def _collect() -> list[dict]:
        frames = _drive_selected_gate5b_stream(
            SimpleNamespace(),
            {"messages": [{"role": "user", "content": "stuck turn"}]},
            SimpleNamespace(),
            session_id="s-cancel",
            turn_id="t-cancel",
            cancel=cancel,
        )
        first = await asyncio.wait_for(anext(frames), timeout=2)
        assert _data_lines(first.decode("utf-8"))[0]["type"] == "text_delta"
        # Watchdog fires: set the cancel event, then drain.
        cancel.set()
        payloads: list[dict] = []
        async for frame in frames:
            payloads.extend(_data_lines(frame.decode("utf-8")))
        return payloads

    payloads = asyncio.run(_collect())

    assert cancelled["value"] is True
    # An error frame (idle_abort) and an aborted terminal were emitted.
    assert any(p.get("code") == "idle_abort" for p in payloads)
    assert any(
        p.get("type") == "turn_result" and p.get("terminal") == "aborted"
        for p in payloads
    )


# ---------------------------------------------------------------------------
# F1b -- durable channel-history writes at the pump seam
# ---------------------------------------------------------------------------


def test_pump_durable_writes_user_and_assistant_rows(tmp_path: Path) -> None:
    async def run() -> None:
        turn_store = LocalTurnStore()
        cancel = asyncio.Event()
        sk = "agent:main:app:general"
        ch_store = ChannelMessageStore(workspace_root=tmp_path)
        frames = [
            _agent_frame({"type": "text_delta", "delta": "durable body"}),
            _agent_frame(
                {"type": "turn_result", "terminal": "completed", "turn_id": "t1"}
            ),
            b"data: [DONE]\n\n",
        ]

        async def _source():
            for f in frames:
                yield f
                await asyncio.sleep(0)

        gen = drive_detached_local_stream(
            _source(),
            session_id=sk,
            turn_id="t1",
            cancel=cancel,
            store=turn_store,
            user_message="what did I ask",
            channel="general",
            store_accessor=lambda: ch_store,
        )
        async for _ in gen:
            pass

        rows = await ch_store.list_messages(session_id=sk)
        assert [r["role"] for r in rows] == ["user", "assistant"]
        assert rows[0]["content"] == "what did I ask"
        assert rows[0]["channel"] == "general"
        assert rows[1]["content"] == "durable body"
        assert rows[1]["incomplete"] is False
        assert rows[1]["terminal"] is None

    asyncio.run(run())


def test_pump_durable_assistant_row_incomplete_on_error(tmp_path: Path) -> None:
    async def run() -> None:
        turn_store = LocalTurnStore()
        cancel = asyncio.Event()
        sk = "agent:main:app:errch"
        ch_store = ChannelMessageStore(workspace_root=tmp_path)
        frames = [
            _agent_frame({"type": "text_delta", "delta": "partial answer"}),
            _agent_frame(
                {"type": "turn_result", "terminal": "error", "error": "boom", "turn_id": "t2"}
            ),
            b"data: [DONE]\n\n",
        ]

        async def _source():
            for f in frames:
                yield f
                await asyncio.sleep(0)

        gen = drive_detached_local_stream(
            _source(),
            session_id=sk,
            turn_id="t2",
            cancel=cancel,
            store=turn_store,
            user_message="ask",
            channel="errch",
            store_accessor=lambda: ch_store,
        )
        async for _ in gen:
            pass

        rows = await ch_store.list_messages(session_id=sk)
        assistant = [r for r in rows if r["role"] == "assistant"]
        assert len(assistant) == 1
        assert assistant[0]["content"] == "partial answer"
        assert assistant[0]["incomplete"] is True
        assert assistant[0]["terminal"] == "error"

    asyncio.run(run())


def test_pump_no_store_accessor_no_rows_no_crash(tmp_path: Path) -> None:
    """store_accessor returning None -> no durable rows, no crash; the turn
    still streams and lands a completed in-memory record."""

    async def run() -> None:
        turn_store = LocalTurnStore()
        cancel = asyncio.Event()
        sk = "agent:main:app:general"
        frames = [
            _agent_frame({"type": "text_delta", "delta": "body"}),
            b"data: [DONE]\n\n",
        ]

        async def _source():
            for f in frames:
                yield f
                await asyncio.sleep(0)

        gen = drive_detached_local_stream(
            _source(),
            session_id=sk,
            turn_id="t3",
            cancel=cancel,
            store=turn_store,
            user_message="ask",
            channel="general",
            store_accessor=lambda: None,
        )
        out = [chunk async for chunk in gen]
        assert b"body" in b"".join(out)
        # In-memory completed record still lands.
        assert turn_store.completed_messages(sk)[0]["content"] == "body"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# F2 -- GET full=1 legacy fallback + turnId dedupe merge
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_channel_registry():
    _reset_channel_message_store_singletons_for_tests()
    yield
    _reset_channel_message_store_singletons_for_tests()


def _seed_completed(sk: str, *, turn_id: str, content: str) -> None:
    reducer = LocalSnapshotReducer(session_id=sk, turn_id=turn_id)
    LOCAL_TURN_STORE.begin(sk, reducer)
    reducer.ingest(
        _agent_frame({"type": "text_delta", "delta": content})
    )
    reducer.ingest(
        _agent_frame({"type": "turn_result", "terminal": "completed", "turn_id": turn_id})
    )
    LOCAL_TURN_STORE.finish(sk, reducer)


def test_full_empty_durable_store_falls_back_to_completed_messages(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_LOCAL_CHANNEL_HISTORY_ENABLED", "1")
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))
    LOCAL_TURN_STORE._reset_for_tests()
    sk = "agent:main:app:general"
    _seed_completed(sk, turn_id="t-mem", content="in-memory answer")
    try:
        client = TestClient(_make_app())
        resp = client.get(
            f"/v1/chat/channel-messages?sessionId={sk}&full=1", headers=_auth()
        )
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        # Durable store empty -> falls through to LOCAL_TURN_STORE.
        assert len(msgs) == 1
        assert msgs[0]["content"] == "in-memory answer"
        assert msgs[0]["turnId"] == "t-mem"
    finally:
        LOCAL_TURN_STORE._reset_for_tests()


def test_full_nonempty_durable_merges_distinct_in_memory_turn(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_LOCAL_CHANNEL_HISTORY_ENABLED", "1")
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))
    LOCAL_TURN_STORE._reset_for_tests()
    sk = "agent:main:app:general"
    # Durable row from an earlier turn.
    ch_store = ChannelMessageStore(workspace_root=tmp_path)
    ch_store.append_message_sync(
        message_id="m-durable",
        session_id=sk,
        role="assistant",
        content="durable answer",
        turn_id="t-durable",
    )
    # In-memory completed record from a DIFFERENT, later turn (durable write not landed).
    _seed_completed(sk, turn_id="t-live", content="live answer")
    try:
        client = TestClient(_make_app())
        resp = client.get(
            f"/v1/chat/channel-messages?sessionId={sk}&full=1", headers=_auth()
        )
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        contents = [m["content"] for m in msgs]
        assert "durable answer" in contents
        assert "live answer" in contents
        assert len(msgs) == 2
    finally:
        LOCAL_TURN_STORE._reset_for_tests()


def test_full_nonempty_durable_dedupes_same_turn_id(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_LOCAL_CHANNEL_HISTORY_ENABLED", "1")
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))
    LOCAL_TURN_STORE._reset_for_tests()
    sk = "agent:main:app:general"
    ch_store = ChannelMessageStore(workspace_root=tmp_path)
    ch_store.append_message_sync(
        message_id="m-durable",
        session_id=sk,
        role="assistant",
        content="durable answer",
        turn_id="t-same",
    )
    # In-memory record with the SAME turnId -> durable wins, no dup.
    _seed_completed(sk, turn_id="t-same", content="stale in-memory copy")
    try:
        client = TestClient(_make_app())
        resp = client.get(
            f"/v1/chat/channel-messages?sessionId={sk}&full=1", headers=_auth()
        )
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        assert len(msgs) == 1
        assert msgs[0]["content"] == "durable answer"
    finally:
        LOCAL_TURN_STORE._reset_for_tests()


# ---------------------------------------------------------------------------
# F3 -- transcript record emission at pump finish
# ---------------------------------------------------------------------------


def test_pump_emits_assembled_message_when_sink_active() -> None:
    from magi_agent.observability import transcript as transcript_module

    captured: list[tuple[dict, object, object]] = []

    def _sink(event, session_id, turn_id) -> None:
        captured.append((event, session_id, turn_id))

    async def run() -> None:
        turn_store = LocalTurnStore()
        cancel = asyncio.Event()
        sk = "agent:main:app:general"
        frames = [
            _agent_frame({"type": "text_delta", "delta": "assembled body"}),
            _agent_frame(
                {"type": "turn_result", "terminal": "completed", "turn_id": "t1"}
            ),
            b"data: [DONE]\n\n",
        ]

        async def _source():
            for f in frames:
                yield f
                await asyncio.sleep(0)

        gen = drive_detached_local_stream(
            _source(),
            session_id=sk,
            turn_id="t1",
            cancel=cancel,
            store=turn_store,
        )
        async for _ in gen:
            pass

    transcript_module.set_active_transcript_sink(_sink)
    try:
        asyncio.run(run())
    finally:
        transcript_module.set_active_transcript_sink(None)

    message_records = [
        e for (e, _s, _t) in captured if e.get("type") == "message"
    ]
    assert len(message_records) == 1
    rec = message_records[0]
    assert rec["role"] == "assistant"
    assert rec["content"] == "assembled body"
    assert rec["terminal"] == "completed"


def test_pump_emits_nothing_when_sink_inactive() -> None:
    from magi_agent.observability import transcript as transcript_module

    async def run() -> None:
        turn_store = LocalTurnStore()
        cancel = asyncio.Event()
        sk = "agent:main:app:general"
        frames = [
            _agent_frame({"type": "text_delta", "delta": "body"}),
            b"data: [DONE]\n\n",
        ]

        async def _source():
            for f in frames:
                yield f
                await asyncio.sleep(0)

        gen = drive_detached_local_stream(
            _source(),
            session_id=sk,
            turn_id="t1",
            cancel=cancel,
            store=turn_store,
        )
        async for _ in gen:
            pass

    # Sink inactive (default) -> emit_transcript_record no-ops; assert no raise.
    transcript_module.set_active_transcript_sink(None)
    asyncio.run(run())
