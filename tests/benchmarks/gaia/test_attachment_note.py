"""Tests for Fix D — attachment note in GAIA harness instruction.

The harness (run_gaia_question in harness.py) must include an attachment note
in the instruction when the question has a file_name, so the agent knows to
look for the file in the workspace.

We patch build_cli_model_runner to capture the instruction kwarg without
needing a real ADK runner.
"""

from __future__ import annotations

from typing import AsyncGenerator
from unittest.mock import MagicMock, patch

import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from benchmarks.gaia.dataset import GaiaQuestion


# ---------------------------------------------------------------------------
# Minimal fake LLM + runner for harness tests
# ---------------------------------------------------------------------------


class _ScriptedLlm(BaseLlm):
    async def generate_content_async(
        self, llm_request: object, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="reasoning...\nFINAL ANSWER: blue")],
            )
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunGaiaQuestionAttachmentNote:
    def test_instruction_includes_attachment_note_when_file_name_present(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When question.file_name is set, the instruction passed to the runner
        must mention the attachment file name.
        """
        from benchmarks.gaia import harness as harness_mod

        captured: list[str] = []
        real_build = harness_mod.build_cli_model_runner

        def _capture_build(config: object, **kwargs: object) -> object:
            instruction = kwargs.get("instruction", "")
            captured.append(str(instruction))
            return real_build(config, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(harness_mod, "build_cli_model_runner", _capture_build)

        # Create a fake attachment
        src = tmp_path / "photo.png"
        src.write_bytes(b"\x89PNG\r\n" + b"\x00" * 30)

        workspace = tmp_path / "ws"
        workspace.mkdir()

        q = GaiaQuestion(
            task_id="attach-test",
            question="What is the color?",
            level=1,
            final_answer="blue",
            file_name="photo.png",
            attachment_path=str(src),
        )

        harness_mod.run_gaia_question(
            q,
            workspace_root=str(workspace),
            model_factory=lambda cfg: _ScriptedLlm(model="fake"),
        )

        assert captured, "build_cli_model_runner was never called"
        full_instruction = captured[0]
        assert "photo.png" in full_instruction, (
            "Expected 'photo.png' in the instruction when file_name is set; "
            f"instruction: {full_instruction!r}"
        )

    def test_instruction_includes_file_tool_hint_when_file_name_present(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When file_name is set, instruction should hint to use file tools."""
        from benchmarks.gaia import harness as harness_mod

        captured: list[str] = []
        real_build = harness_mod.build_cli_model_runner

        def _capture_build(config: object, **kwargs: object) -> object:
            captured.append(str(kwargs.get("instruction", "")))
            return real_build(config, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(harness_mod, "build_cli_model_runner", _capture_build)

        src = tmp_path / "data.xlsx"
        src.write_bytes(b"\x50\x4b\x03\x04" + b"\x00" * 20)

        workspace = tmp_path / "ws2"
        workspace.mkdir()

        q = GaiaQuestion(
            task_id="attach-test-2",
            question="What value is in cell A1?",
            level=1,
            final_answer="42",
            file_name="data.xlsx",
            attachment_path=str(src),
        )

        harness_mod.run_gaia_question(
            q,
            workspace_root=str(workspace),
            model_factory=lambda cfg: _ScriptedLlm(model="fake"),
        )

        assert captured
        full_instruction = captured[0].lower()
        has_hint = any(
            kw in full_instruction
            for kw in (
                "file tool",
                "imageunderstand",
                "documentread",
                "xlsxread",
                "attachment",
                "working directory",
                "workspace",
            )
        )
        assert has_hint, (
            "Expected file-tool hint in instruction when file_name is set; "
            f"got: {captured[0]!r}"
        )

    def test_instruction_does_not_include_attachment_note_when_no_file(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When question has no file_name, instruction should not have attachment noise."""
        from benchmarks.gaia import harness as harness_mod

        captured: list[str] = []
        real_build = harness_mod.build_cli_model_runner

        def _capture_build(config: object, **kwargs: object) -> object:
            captured.append(str(kwargs.get("instruction", "")))
            return real_build(config, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(harness_mod, "build_cli_model_runner", _capture_build)

        workspace = tmp_path / "ws3"
        workspace.mkdir()

        q = GaiaQuestion(
            task_id="no-attach",
            question="What is 2+2?",
            level=1,
            final_answer="4",
        )

        harness_mod.run_gaia_question(
            q,
            workspace_root=str(workspace),
            model_factory=lambda cfg: _ScriptedLlm(model="fake"),
        )

        assert captured
        full_instruction = captured[0]
        assert "NOTE: An attachment" not in full_instruction, (
            "Unexpected attachment note for question with no file_name"
        )
