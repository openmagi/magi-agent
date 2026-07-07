"""build_headless_runtime resolves the no-tool finalizer and exempts children."""

from __future__ import annotations

import magi_agent.cli.real_runner as real_runner
import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from magi_agent.cli.wiring import build_headless_runtime


class _FakeLlm(BaseLlm):
    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="ok")])
        )


def _build(monkeypatch, tmp_path, **kwargs):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        real_runner, "_build_litellm_model", lambda _config: _FakeLlm(model="fake")
    )
    return build_headless_runtime(
        cwd=tmp_path, session_id="s", model="claude-opus-4-1", **kwargs
    )


def test_default_full_profile_resolves_finalizer(monkeypatch, tmp_path):
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    monkeypatch.delenv("MAGI_NO_TOOL_FINALIZER_ENABLED", raising=False)
    rt = _build(monkeypatch, tmp_path)
    assert rt.engine._no_tool_finalizer is not None
    assert rt.engine._no_tool_finalizer.enabled is True


def test_kill_switch_off(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_NO_TOOL_FINALIZER_ENABLED", "0")
    rt = _build(monkeypatch, tmp_path)
    assert rt.engine._no_tool_finalizer is None


def test_child_exemption(monkeypatch, tmp_path):
    # Children pass no_tool_finalizer_allowed=False -> config None even on full profile.
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    monkeypatch.delenv("MAGI_NO_TOOL_FINALIZER_ENABLED", raising=False)
    rt = _build(monkeypatch, tmp_path, no_tool_finalizer_allowed=False)
    assert rt.engine._no_tool_finalizer is None
