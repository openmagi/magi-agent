"""Session-service reuse: turn-to-turn continuity across engine rebuilds.

The local serve path rebuilds the whole engine (a new ``build_cli_model_runner``
call) every turn. Continuity therefore depends on the two builds sharing ONE
``session_service`` so ADK session events accumulate across turns. These tests
pin the ``session_service_factory`` seam that makes that sharing possible.

See docs/plans/2026-07-06-local-serve-session-continuity-fix-design.md.
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from magi_agent.adk_bridge.session_service import WorkspaceSessionService
from magi_agent.cli.providers import ProviderConfig
from magi_agent.cli.real_runner import build_cli_model_runner

_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
    "MAGI_PROVIDER",
    "MAGI_MODEL",
)


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch, tmp_path) -> None:
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


def _config() -> ProviderConfig:
    return ProviderConfig(
        provider="anthropic", model="claude-sonnet-4-5", api_key="sk-test"
    )


class _CapturingLlm(BaseLlm):
    """Records each call's request contents, returns a canned reply."""

    def __init__(self, sink: list[list[object]]) -> None:
        super().__init__(model="fake")
        self._sink = sink

    async def generate_content_async(
        self, llm_request: object, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        contents = list(getattr(llm_request, "contents", None) or [])
        self._sink.append(contents)
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="ECHO ok")])
        )


def _all_texts(contents: list[object]) -> list[str]:
    texts: list[str] = []
    for content in contents:
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                texts.append(text)
    return texts


async def _drive(runner, text: str) -> None:
    new_message = types.Content(role="user", parts=[types.Part(text=text)])
    async for _event in runner.run_async(
        user_id="cli", session_id="s1", new_message=new_message
    ):
        pass


def test_factory_is_used_and_receives_app_name() -> None:
    sentinel = WorkspaceSessionService(app_name="magi-cli")
    seen: list[str] = []

    def factory(app_name: str) -> object:
        seen.append(app_name)
        return sentinel

    runner = build_cli_model_runner(
        _config(),
        model_factory=lambda cfg: _CapturingLlm([]),
        session_service_factory=factory,
    )
    assert runner._session_service is sentinel
    assert seen == ["magi-cli"]


def test_default_builds_fresh_service_per_call() -> None:
    a = build_cli_model_runner(_config(), model_factory=lambda cfg: _CapturingLlm([]))
    b = build_cli_model_runner(_config(), model_factory=lambda cfg: _CapturingLlm([]))
    assert a._session_service is not b._session_service


def test_shared_factory_preserves_history_across_rebuilds() -> None:
    shared = WorkspaceSessionService(app_name="magi-cli")
    captured: list[list[object]] = []

    def factory(app_name: str) -> object:
        return shared

    # Turn A: establish a fact through one engine build.
    runner_a = build_cli_model_runner(
        _config(),
        model_factory=lambda cfg: _CapturingLlm(captured),
        session_service_factory=factory,
    )
    asyncio.run(_drive(runner_a, "My project codename is BLUEFIN."))

    # Turn B: a SEPARATE engine build sharing only the session service.
    runner_b = build_cli_model_runner(
        _config(),
        model_factory=lambda cfg: _CapturingLlm(captured),
        session_service_factory=factory,
    )
    asyncio.run(_drive(runner_b, "What is my project codename?"))

    # Turn B's LLM request must contain turn A's user message (continuity).
    turn_b_contents = captured[-1]
    turn_b_texts = _all_texts(turn_b_contents)
    assert any("BLUEFIN" in text for text in turn_b_texts), turn_b_texts
