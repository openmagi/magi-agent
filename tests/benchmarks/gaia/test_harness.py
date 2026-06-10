from __future__ import annotations

from typing import AsyncGenerator

from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from benchmarks.gaia.dataset import GaiaQuestion
from benchmarks.gaia.harness import run_gaia_question


class _ScriptedLlm(BaseLlm):
    async def generate_content_async(
        self, llm_request: object, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="reasoning...\nFINAL ANSWER: egalitarian")],
            )
        )


def test_run_extracts_final_answer_with_fake_model(tmp_path) -> None:
    q = GaiaQuestion(
        task_id="a", question="What word?", level=1, final_answer="egalitarian"
    )
    answer = run_gaia_question(
        q,
        workspace_root=str(tmp_path),
        model_factory=lambda cfg: _ScriptedLlm(model="fake"),
    )
    assert answer == "egalitarian"


def test_attachment_is_copied_into_workspace(tmp_path) -> None:
    # Create a fake attachment file in a separate source dir.
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    attachment = src_dir / "data.txt"
    attachment.write_text("attachment contents\n", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    q = GaiaQuestion(
        task_id="b",
        question="What is in the file?",
        level=1,
        final_answer="",
        file_name="data.txt",
        attachment_path=str(attachment),
    )

    run_gaia_question(
        q,
        workspace_root=str(workspace),
        model_factory=lambda cfg: _ScriptedLlm(model="fake"),
    )

    copied = workspace / "data.txt"
    assert copied.exists(), "attachment was not copied into workspace_root"
    assert copied.read_text(encoding="utf-8") == "attachment contents\n"
