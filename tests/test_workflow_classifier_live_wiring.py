"""TDD for C5 (doc 03 PR-5): model-backed channel-workflow classifier seam.

The channel dynamic-workflow code is all merged; the remaining gap is a live,
model-backed classifier so auto-detect stops being inert ("general") once a
provider is configured. This module adds ``build_live_classifier_if_configured``
which returns a model-backed :class:`TaskKindClassifier` when a provider is
configured, else the inert default (auto-detect stays "general").

All tests are network-free: providers are injected via env/fakes.
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.channels.taskkind_classifier import TaskKindClassifier
from magi_agent.channels.workflow_classifier_live import (
    build_live_classifier_if_configured,
)


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


class _FakeModel:
    def __init__(self, text: str) -> None:
        self._text = text

    async def generate_content_async(self, request, stream=False):  # noqa: ANN001
        yield _FakeResp(self._text)


def test_no_provider_configured_returns_inert_classifier(monkeypatch):
    # No model_factory injected and no provider resolvable → inert.
    monkeypatch.setattr(
        "magi_agent.channels.workflow_classifier_live._resolve_provider_config",
        lambda: None,
    )
    classifier = build_live_classifier_if_configured()
    assert isinstance(classifier, TaskKindClassifier)
    # Auto-detect inert: returns "general" with no model behind it.
    assert asyncio.run(classifier.aclassify("compare A vs B")) == "general"


def test_provider_configured_uses_model_backed_classifier(monkeypatch):
    # A configured provider → a model-backed classifier that returns the model's label.
    sentinel_config = object()
    monkeypatch.setattr(
        "magi_agent.channels.workflow_classifier_live._resolve_provider_config",
        lambda: sentinel_config,
    )
    monkeypatch.setattr(
        "magi_agent.channels.workflow_classifier_live._build_model_for_config",
        lambda config: _FakeModel("source_sensitive_research"),
    )
    classifier = build_live_classifier_if_configured()
    assert isinstance(classifier, TaskKindClassifier)
    assert (
        asyncio.run(classifier.aclassify("compare A vs B"))
        == "source_sensitive_research"
    )


def test_explicit_factory_overrides_resolution(monkeypatch):
    # An injected model_factory takes precedence over provider resolution.
    monkeypatch.setattr(
        "magi_agent.channels.workflow_classifier_live._resolve_provider_config",
        lambda: None,  # would be inert if resolution were consulted
    )
    classifier = build_live_classifier_if_configured(
        model_factory=lambda: _FakeModel("complex_synthesis")
    )
    assert asyncio.run(classifier.aclassify("x")) == "complex_synthesis"


def test_model_build_failure_falls_back_to_inert(monkeypatch):
    # Provider resolves, but building the model raises → fail-safe to "general"
    # (model_factory returns None inside TaskKindClassifier).
    monkeypatch.setattr(
        "magi_agent.channels.workflow_classifier_live._resolve_provider_config",
        lambda: object(),
    )

    def _boom(config):
        raise RuntimeError("litellm unavailable")

    monkeypatch.setattr(
        "magi_agent.channels.workflow_classifier_live._build_model_for_config",
        _boom,
    )
    classifier = build_live_classifier_if_configured()
    assert asyncio.run(classifier.aclassify("x")) == "general"


def test_provider_resolution_exception_is_inert(monkeypatch):
    def _boom():
        raise RuntimeError("config blew up")

    monkeypatch.setattr(
        "magi_agent.channels.workflow_classifier_live._resolve_provider_config",
        _boom,
    )
    classifier = build_live_classifier_if_configured()
    assert isinstance(classifier, TaskKindClassifier)
    assert asyncio.run(classifier.aclassify("x")) == "general"


def test_module_is_import_clean_of_http():
    # Boundary discipline: the classifier-live module must not import HTTP/SMTP
    # clients at module scope (model build is lazy via readonly_classifier helper).
    import magi_agent.channels.workflow_classifier_live as mod

    src = mod.__file__
    with open(src, "r", encoding="utf-8") as handle:
        text = handle.read()
    assert "import httpx" not in text
    assert "import requests" not in text


# ---------------------------------------------------------------------------
# Route-level wiring: the default (no injected classifier) path must use the
# live builder, staying inert when no provider is configured.
# ---------------------------------------------------------------------------


def _runtime():
    from magi_agent.config.models import (
        BuildInfo,
        PythonRuntimeAuthorityConfig,
        RuntimeConfig,
    )
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="b",
            user_id="u",
            gateway_token="test-token",
            api_proxy_url="http://x",
            chat_proxy_url="http://x",
            redis_url="redis://x:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-test", build_sha="sha"),
            authority=PythonRuntimeAuthorityConfig(),
        )
    )


class _FakeEngine:
    def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):  # noqa: ANN001
        async def _gen():
            from magi_agent.cli.contracts import EngineResult, Terminal

            yield EngineResult(terminal=Terminal.completed)

        return _gen()


def _fake_builder(session_id, sink):  # noqa: ANN001
    return _FakeEngine(), None


def test_route_default_classifier_inert_without_provider(monkeypatch):
    """No injected classifier + no provider → channel route auto-detect is inert
    (returns "general"), so a multi-step message is NOT routed to a workflow."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from magi_agent.channels.workflow_confirm_store import (
        InMemoryPendingConfirmationStore,
    )
    from magi_agent.transport.active_turn import ACTIVE_TURNS
    from magi_agent.transport.streaming_chat_route import (
        register_streaming_chat_routes,
    )

    # Force the live builder to resolve no provider → inert default classifier.
    monkeypatch.setattr(
        "magi_agent.channels.workflow_classifier_live._resolve_provider_config",
        lambda: None,
    )
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", "1")

    store = InMemoryPendingConfirmationStore()
    app = FastAPI()
    register_streaming_chat_routes(
        app, _runtime(), engine_builder=_fake_builder, confirm_store=store
    )
    client = TestClient(app)
    try:
        r = client.post(
            "/v1/chat/stream",
            json={
                "sessionId": "sX",
                "messages": [{"role": "user", "content": "compare A vs B in depth"}],
            },
            headers={"authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        # Auto-detect inert → no pending confirmation stored.
        assert store.pop("sX") is None
    finally:
        ACTIVE_TURNS._turns.clear()


def test_route_default_classifier_live_with_provider(monkeypatch):
    """No injected classifier but a provider IS configured → the default
    classifier is model-backed and auto-detect routes a multi-step message to a
    workflow (pending confirmation stored)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from magi_agent.channels.workflow_confirm_store import (
        InMemoryPendingConfirmationStore,
    )
    from magi_agent.transport.active_turn import ACTIVE_TURNS
    from magi_agent.transport.streaming_chat_route import (
        register_streaming_chat_routes,
    )

    monkeypatch.setattr(
        "magi_agent.channels.workflow_classifier_live._resolve_provider_config",
        lambda: object(),
    )
    monkeypatch.setattr(
        "magi_agent.channels.workflow_classifier_live._build_model_for_config",
        lambda config: _FakeModel("source_sensitive_research"),
    )
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", "1")

    store = InMemoryPendingConfirmationStore()
    app = FastAPI()
    register_streaming_chat_routes(
        app, _runtime(), engine_builder=_fake_builder, confirm_store=store
    )
    client = TestClient(app)
    try:
        r = client.post(
            "/v1/chat/stream",
            json={
                "sessionId": "sY",
                "messages": [{"role": "user", "content": "compare A vs B in depth"}],
            },
            headers={"authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        # Model-backed auto-detect → eligible → pending confirmation stored.
        assert store.pop("sY") is not None
    finally:
        ACTIVE_TURNS._turns.clear()
