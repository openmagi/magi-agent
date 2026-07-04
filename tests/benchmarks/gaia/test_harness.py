from __future__ import annotations

from typing import AsyncGenerator

import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from benchmarks.gaia.dataset import GaiaQuestion
from benchmarks.gaia.harness import run_gaia_question


@pytest.fixture(autouse=True)
def _isolate_memory_write_promotion_env(monkeypatch) -> None:
    """Clear the memory-write "live" promotion env vars for every test here.

    These tests root the agent at a ``.../workspace`` directory, which the
    read-only memory guard's production-path regex intentionally rejects. When
    a sibling test leaks ``MAGI_MEMORY_LOCAL_DEV`` +
    ``MAGI_MEMORY_WRITE_READINESS_ENABLED`` + ``MAGI_MEMORY_WRITE_ENABLED`` into
    ``os.environ`` (un-restored), the runner promotes the memory-write stack to
    "live" and builds a ``LocalFileMemoryProvider`` on that path, raising
    ``UnsafeMemoryPathError`` before the harness assertions run (test-isolation
    failure under xdist). Deleting them keeps these harness tests hermetic
    regardless of worker or order without weakening the guard itself.
    """
    for var in (
        "MAGI_MEMORY_LOCAL_DEV",
        "MAGI_MEMORY_WRITE_READINESS_ENABLED",
        "MAGI_MEMORY_WRITE_ENABLED",
        "MAGI_MEMORY_ALLOW_PRODUCTION_WORKSPACE",
    ):
        monkeypatch.delenv(var, raising=False)


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


# ---------------------------------------------------------------------------
# Best-effort finalization wiring (MAGI_ANSWER_POLICY, default abstain)
# ---------------------------------------------------------------------------


def _abstain_then_answer_llm(calls: list[object]) -> BaseLlm:
    """Scripted LLM: abstains on turn 1, commits on turn 2; records each call."""

    class _AbstainThenAnswerLlm(BaseLlm):
        async def generate_content_async(
            self, llm_request: object, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            calls.append(llm_request)
            text = (
                "reasoning... cannot determine"
                if len(calls) == 1
                else "FINAL ANSWER: egalitarian"
            )
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text=text)])
            )

    return _AbstainThenAnswerLlm(model="fake")


def test_commit_policy_rescues_abstaining_run(tmp_path, monkeypatch) -> None:
    """MAGI_ANSWER_POLICY=commit → a second synthesis turn rescues the answer."""
    monkeypatch.setenv("MAGI_ANSWER_POLICY", "commit")
    calls: list[object] = []
    q = GaiaQuestion(
        task_id="c", question="What word?", level=1, final_answer="egalitarian"
    )
    answer = run_gaia_question(
        q,
        workspace_root=str(tmp_path),
        model_factory=lambda cfg: _abstain_then_answer_llm(calls),
    )
    assert answer == "egalitarian"
    assert len(calls) == 2, "rescue must run exactly one extra turn"


def test_default_env_unset_keeps_empty_answer(tmp_path, monkeypatch) -> None:
    """Default-OFF proof: env unset → empty answer exactly as before, one turn only."""
    monkeypatch.delenv("MAGI_ANSWER_POLICY", raising=False)
    calls: list[object] = []
    q = GaiaQuestion(
        task_id="d", question="What word?", level=1, final_answer="egalitarian"
    )
    answer = run_gaia_question(
        q,
        workspace_root=str(tmp_path),
        model_factory=lambda cfg: _abstain_then_answer_llm(calls),
    )
    assert answer == ""
    assert len(calls) == 1, "default abstain must not run a second turn"


def test_harness_instruction_advertises_format_adherence(tmp_path, monkeypatch) -> None:
    # The GAIA harness must advertise the format-adherence note in the instruction
    # it forwards to the runner. Capture the instruction via build_cli_model_runner.
    import benchmarks.gaia.harness as harness_mod
    from benchmarks.gaia.answer import GAIA_FORMAT_ADHERENCE_NOTE

    captured: dict[str, str] = {}
    real_build = harness_mod.build_cli_model_runner

    def _spy(config, *, instruction, **kwargs):
        captured["instruction"] = instruction
        return real_build(config, instruction=instruction, **kwargs)

    monkeypatch.setattr(harness_mod, "build_cli_model_runner", _spy)

    q = GaiaQuestion(
        task_id="c", question="What word?", level=1, final_answer="egalitarian"
    )
    run_gaia_question(
        q,
        workspace_root=str(tmp_path),
        model_factory=lambda cfg: _ScriptedLlm(model="fake"),
    )
    assert GAIA_FORMAT_ADHERENCE_NOTE in captured["instruction"]
