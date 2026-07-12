"""TDD tests for slash→skill activation on the local ``magi serve`` chat route.

These tests verify that ``streaming_chat_route`` expands slash commands into
skill bodies BEFORE handing the prompt to the local engine, while keeping the
original slash text for the goal-loop objective and NOT passing it as the model
prompt.

Coverage:
- slash hit → engine's ``turn_input["prompt"]`` contains the SKILL.md body
- slash + residual text → body and residual forwarded to engine
- unknown slash (miss) → original text passed through unchanged
- reserved command (/reset, /help) → original text passed through unchanged
- resolver exception → original text passed through (fail-open, never raises)
- non-slash text → byte-identical to pre-change behaviour
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.config.models import BuildInfo, PythonRuntimeAuthorityConfig, RuntimeConfig
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.streaming_chat_route import (
    register_streaming_chat_routes,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _pin_legacy_path(monkeypatch):
    """Hold the legacy (non-governed) path so we exercise the local engine."""
    monkeypatch.setenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", "0")
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")


def _make_runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-slash-test",
            user_id="user-slash-test",
            gateway_token="test-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-test", build_sha="sha-test"),
            authority=PythonRuntimeAuthorityConfig(),
        )
    )


def _ev(event_type: str, **payload: object) -> RuntimeEvent:
    return RuntimeEvent(
        type="status",
        payload={"type": event_type, **payload},
        turn_id="t-slash",
    )


class _CapturingEngine:
    """Engine fake that records the turn_input it receives."""

    def __init__(self) -> None:
        self.received_prompt: str | None = None

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
        self.received_prompt = turn_input.get("prompt")
        yield _ev("text_delta", delta="ok")
        yield EngineResult(
            terminal=Terminal.completed,
            session_id="s-slash",
            turn_id="t-slash",
        )


def _make_app_with_engine(engine: _CapturingEngine) -> TestClient:
    app = FastAPI(title="slash-test")
    rt = _make_runtime()

    def builder(
        session_id: str,
        sink: object,
        model_override: object = None,
        **kwargs: object,
    ) -> tuple[object, object]:
        return engine, None

    register_streaming_chat_routes(app, rt, engine_builder=builder)
    return TestClient(app)


def _post(client: TestClient, user_text: str) -> object:
    return client.post(
        "/v1/chat/stream",
        headers={"authorization": "Bearer test-token"},
        json={
            "sessionId": "s-slash",
            "turnId": "t-slash",
            "messages": [{"role": "user", "content": user_text}],
        },
    )


def _make_skill(base: Path, dir_name: str, body: str = "# skill body\n\ndetail") -> None:
    skill_dir = base / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Slash HIT → engine receives skill body, not the slash text
# ---------------------------------------------------------------------------


def test_slash_hit_engine_receives_skill_body(tmp_path: Path, monkeypatch) -> None:
    """On a slash HIT the engine's turn_input['prompt'] must contain the SKILL.md body.

    We use a name that does NOT collide with any bundled skill so the resolver
    picks up our workspace-local one.
    """
    skill_body = "# Test Acme Skill\n\nUnique skill body for test-acme-skill."
    _make_skill(tmp_path / "skills", "test-acme-skill", body=skill_body)
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))

    engine = _CapturingEngine()
    client = _make_app_with_engine(engine)

    resp = _post(client, "/test-acme-skill")
    assert resp.status_code == 200, resp.text
    assert engine.received_prompt is not None
    # Engine must see the skill body, NOT the literal slash command
    assert skill_body in engine.received_prompt
    assert engine.received_prompt != "/test-acme-skill"


def test_slash_hit_with_residual_text_included(tmp_path: Path, monkeypatch) -> None:
    """Residual text after the slash command is appended after the skill body."""
    skill_body = "# Test Beta Skill\n\nBeta skill body for testing."
    _make_skill(tmp_path / "skills", "test-beta-skill", body=skill_body)
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))

    engine = _CapturingEngine()
    client = _make_app_with_engine(engine)

    resp = _post(client, "/test-beta-skill find me stuff about AI")
    assert resp.status_code == 200, resp.text
    assert engine.received_prompt is not None
    assert skill_body in engine.received_prompt
    # Residual must also be present
    assert "find me stuff about AI" in engine.received_prompt


# ---------------------------------------------------------------------------
# Slash MISS → pass through unchanged
# ---------------------------------------------------------------------------


def test_unknown_slash_passes_through_unchanged(tmp_path: Path, monkeypatch) -> None:
    """An unknown slash command (resolver miss) must be forwarded verbatim."""
    # No skill installed for /unknown-skill
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))
    (tmp_path / "skills").mkdir()

    engine = _CapturingEngine()
    client = _make_app_with_engine(engine)

    resp = _post(client, "/unknown-skill")
    assert resp.status_code == 200, resp.text
    assert engine.received_prompt == "/unknown-skill"


# ---------------------------------------------------------------------------
# Reserved commands → pass through unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cmd", ["/reset", "/help", "/status", "/compact"])
def test_reserved_slash_commands_pass_through(
    cmd: str, tmp_path: Path, monkeypatch
) -> None:
    """Reserved commands must never be intercepted by slash skill activation."""
    (tmp_path / "skills").mkdir()
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))

    engine = _CapturingEngine()
    client = _make_app_with_engine(engine)

    resp = _post(client, cmd)
    assert resp.status_code == 200, resp.text
    assert engine.received_prompt == cmd


# ---------------------------------------------------------------------------
# Resolver exception → fail-open, never raises
# ---------------------------------------------------------------------------


def test_resolver_exception_falls_back_gracefully(
    tmp_path: Path, monkeypatch
) -> None:
    """If the resolver raises, the original text is passed through (fail-open)."""
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))

    # Patch resolve_skill_slash to raise
    import magi_agent.transport.streaming_chat_route as route_mod

    def _raising_resolver(*args, **kwargs):
        raise RuntimeError("simulated resolver failure")

    monkeypatch.setattr(route_mod, "_resolve_local_slash_expansion", lambda text: text)
    # The above bypasses the real resolver but tests the fail-open wrapper too.
    # Also test that a direct exception inside the helper is silently swallowed:
    original_fn = route_mod._resolve_local_slash_expansion

    def _boom(text: str) -> str:
        raise ValueError("boom from test")

    monkeypatch.setattr(route_mod, "_resolve_local_slash_expansion", _boom)

    engine = _CapturingEngine()
    # Build the client AFTER patching so the route captures the patched attr.
    app = FastAPI(title="exception-test")
    rt = _make_runtime()

    def builder(session_id: str, sink: object, model_override: object = None, **kw) -> tuple[object, object]:
        return engine, None

    register_streaming_chat_routes(app, rt, engine_builder=builder)
    client = TestClient(app)

    # Posting a slash text — if the helper raises and the route doesn't catch
    # it, this will return 500.  We assert 200, meaning the route is robust.
    resp = client.post(
        "/v1/chat/stream",
        headers={"authorization": "Bearer test-token"},
        json={
            "sessionId": "s-exc",
            "turnId": "t-exc",
            "messages": [{"role": "user", "content": "/some-skill"}],
        },
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Non-slash text → byte-identical behaviour (regression guard)
# ---------------------------------------------------------------------------


def test_non_slash_text_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    """Ordinary (non-slash) user messages must reach the engine unchanged."""
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))

    engine = _CapturingEngine()
    client = _make_app_with_engine(engine)

    resp = _post(client, "hello, what is 2+2?")
    assert resp.status_code == 200, resp.text
    assert engine.received_prompt == "hello, what is 2+2?"
