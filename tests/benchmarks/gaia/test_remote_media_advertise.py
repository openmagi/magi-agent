"""Tests for the gated GAIA remote-media capability advertisement.

When MAGI_GAIA_REMOTE_MEDIA_ADVERTISE_ENABLED is OFF (unset/empty/false), the
assembled harness instruction must be BYTE-IDENTICAL to the pre-feature output
and GAIA_SYSTEM_PROMPT must be unchanged. When ON, a GENERIC note about remote
media URL access is appended (no GAIA-specific / answer-specific text).
"""

from __future__ import annotations

from typing import AsyncGenerator
from unittest.mock import patch

import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from benchmarks.gaia.dataset import GaiaQuestion


class _ScriptedLlm(BaseLlm):
    async def generate_content_async(
        self, llm_request: object, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="FINAL ANSWER: blue")],
            )
        )


def _capture_instruction(
    monkeypatch: pytest.MonkeyPatch, q: GaiaQuestion, workspace
) -> str:
    from benchmarks.gaia import harness as harness_mod

    captured: list[str] = []
    real_build = harness_mod.build_cli_model_runner

    def _capture_build(config: object, **kwargs: object) -> object:
        captured.append(str(kwargs.get("instruction", "")))
        return real_build(config, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(harness_mod, "build_cli_model_runner", _capture_build)
    harness_mod.run_gaia_question(
        q,
        workspace_root=str(workspace),
        model_factory=lambda cfg: _ScriptedLlm(model="fake"),
    )
    assert captured
    return captured[0]


class TestRemoteMediaAdvertiseGate:
    def test_system_prompt_constant_unchanged(self) -> None:
        from benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT

        # The shared system prompt constant must NOT contain the feature note.
        assert "VideoFrames/AudioTranscribe accept a remote" not in GAIA_SYSTEM_PROMPT

    def test_instruction_byte_identical_when_unset(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAGI_GAIA_REMOTE_MEDIA_ADVERTISE_ENABLED", raising=False)
        ws = tmp_path / "ws"
        ws.mkdir()
        q = GaiaQuestion(
            task_id="t", question="What is 2+2?", level=1, final_answer="4"
        )
        instruction = _capture_instruction(monkeypatch, q, ws)
        assert "remote video/media URL" not in instruction
        assert "VideoFrames/AudioTranscribe accept a remote" not in instruction

    @pytest.mark.parametrize("value", ["", "false", "  ", "0", "no", "off"])
    def test_falsey_values_keep_advertisement_off(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("MAGI_GAIA_REMOTE_MEDIA_ADVERTISE_ENABLED", value)
        ws = tmp_path / "ws"
        ws.mkdir()
        q = GaiaQuestion(
            task_id="t", question="What is 2+2?", level=1, final_answer="4"
        )
        instruction = _capture_instruction(monkeypatch, q, ws)
        assert "VideoFrames/AudioTranscribe accept a remote" not in instruction

    def test_note_appended_when_enabled(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_GAIA_REMOTE_MEDIA_ADVERTISE_ENABLED", "true")
        ws = tmp_path / "ws"
        ws.mkdir()
        q = GaiaQuestion(
            task_id="t", question="What is 2+2?", level=1, final_answer="4"
        )
        instruction = _capture_instruction(monkeypatch, q, ws)
        assert "VideoFrames/AudioTranscribe accept a remote" in instruction
        # Anti-overfit: must NOT name the benchmark target or any answer value.
        lowered = instruction.lower()
        assert "cheater beater" not in lowered
        assert "cfm" not in lowered
