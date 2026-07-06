"""``build_headless_runtime`` forwards ``session_service_factory`` to the runner.

This pins the wiring pass-through so the local serve path (which supplies a
process-level reuse factory) reaches ``build_cli_model_runner`` unchanged.

See docs/plans/2026-07-06-local-serve-session-continuity-fix-design.md.
"""

from __future__ import annotations

import magi_agent.cli.real_runner as real_runner
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from magi_agent.adk_bridge.session_service import WorkspaceSessionService
from magi_agent.cli.wiring import build_headless_runtime


class _FakeLlm(BaseLlm):
    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="ok")])
        )


def test_headless_runtime_forwards_session_service_factory(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        real_runner, "_build_litellm_model", lambda _config: _FakeLlm(model="fake")
    )

    sentinel = WorkspaceSessionService(app_name="magi-cli")
    seen: list[str] = []

    def factory(app_name: str) -> object:
        seen.append(app_name)
        return sentinel

    runtime = build_headless_runtime(
        cwd=tmp_path,
        session_id="sid-reuse",
        model="claude-opus-4-1",
        session_service_factory=factory,
    )

    assert runtime.engine.runner._session_service is sentinel
    assert seen == ["magi-cli"]


def test_headless_runtime_omitting_factory_builds_fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        real_runner, "_build_litellm_model", lambda _config: _FakeLlm(model="fake")
    )

    runtime = build_headless_runtime(
        cwd=tmp_path,
        session_id="sid-fresh",
        model="claude-opus-4-1",
    )

    # Default path still constructs a private in-memory service.
    assert isinstance(runtime.engine.runner._session_service, WorkspaceSessionService)
