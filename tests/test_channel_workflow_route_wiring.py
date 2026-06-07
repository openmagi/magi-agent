from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_agent.config.models import BuildInfo, PythonRuntimeAuthorityConfig, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.active_turn import ACTIVE_TURNS
from magi_agent.transport.streaming_chat_route import register_streaming_chat_routes
from magi_agent.channels.workflow_confirm_store import InMemoryPendingConfirmationStore


def _runtime():
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="b", user_id="u", gateway_token="test-token",
            api_proxy_url="http://x", chat_proxy_url="http://x",
            redis_url="redis://x:6379/0", model="gpt-5.2",
            build=BuildInfo(version="0.1.0-test", build_sha="sha"),
            authority=PythonRuntimeAuthorityConfig(),
        )
    )


class _FixedAsyncClassifier:
    def __init__(self, label: str) -> None:
        self._label = label
    async def aclassify(self, message_text: str) -> str:
        return self._label


class _FakeEngine:
    def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        async def _gen():
            from magi_agent.cli.contracts import EngineResult, Terminal
            yield EngineResult(terminal=Terminal.completed)
        return _gen()


def _fake_builder(session_id, sink):
    return _FakeEngine(), None


def _app(store, classifier):
    app = FastAPI()
    register_streaming_chat_routes(
        app, _runtime(), engine_builder=_fake_builder,
        confirm_store=store, eligibility_classifier=classifier,
    )
    return app


def _hdr():
    return {"authorization": "Bearer test-token"}


def _body(text, session="s1"):
    return {"sessionId": session, "messages": [{"role": "user", "content": text}]}


def teardown_function():
    ACTIVE_TURNS._turns.clear()


def test_channel_off_is_passthrough(monkeypatch):
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.delenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", raising=False)
    store = InMemoryPendingConfirmationStore()
    client = TestClient(_app(store, _FixedAsyncClassifier("source_sensitive_research")))
    r = client.post("/v1/chat/stream", json=_body("compare A vs B"), headers=_hdr())
    # passthrough → SSE stream (not a workflow JSON)
    assert r.status_code == 200
    assert "workflow" not in r.text or "awaiting_confirmation" not in r.text
    assert store.pop("s1") is None  # nothing stored when channel off


def test_eligible_auto_detect_returns_confirm(monkeypatch):
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", "1")
    store = InMemoryPendingConfirmationStore()
    client = TestClient(_app(store, _FixedAsyncClassifier("source_sensitive_research")))
    r = client.post("/v1/chat/stream", json=_body("compare A vs B", "sA"), headers=_hdr())
    assert r.status_code == 200
    assert r.json()["workflow"] == "awaiting_confirmation"
    assert store.pop("sA") is not None  # pending stored


def test_ineligible_passes_through(monkeypatch):
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", "1")
    store = InMemoryPendingConfirmationStore()
    client = TestClient(_app(store, _FixedAsyncClassifier("general")))
    r = client.post("/v1/chat/stream", json=_body("hi", "sB"), headers=_hdr())
    assert r.status_code == 200
    # general → normal_llm → SSE passthrough, nothing stored
    assert store.pop("sB") is None


def test_research_prefix_returns_confirm(monkeypatch):
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", "1")
    store = InMemoryPendingConfirmationStore()
    client = TestClient(_app(store, _FixedAsyncClassifier("general")))
    r = client.post("/v1/chat/stream", json=_body("/research compare A vs B", "sC"), headers=_hdr())
    assert r.json()["workflow"] == "awaiting_confirmation"
    assert store.pop("sC") is not None


def test_second_message_resolves_pending_decline_when_executor_off(monkeypatch):
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", "1")
    monkeypatch.delenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", raising=False)
    store = InMemoryPendingConfirmationStore()
    client = TestClient(_app(store, _FixedAsyncClassifier("general")))
    # 1) start a pending via /research
    client.post("/v1/chat/stream", json=_body("/research X", "sD"), headers=_hdr())
    # 2) answer "예" → resolve; executor OFF → declined (never executes)
    r = client.post("/v1/chat/stream", json=_body("예", "sD"), headers=_hdr())
    assert r.json()["workflow"] == "declined"
