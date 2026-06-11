"""GAIA agent harness — drives a single GaiaQuestion through the real ADK runner."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Callable

from google.genai import types

from benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT, extract_final_answer
from benchmarks.gaia.dataset import GaiaQuestion
from magi_agent.cli.providers import ProviderConfig
from magi_agent.cli.real_runner import CliModelRunner, build_cli_model_runner
from magi_agent.research.answer_policy import should_force_answer
from magi_agent.runtime.best_effort_answer import (
    BestEffortConfig,
    finalize_answer,
    is_non_answer,
)


def run_gaia_question(
    question: GaiaQuestion,
    *,
    workspace_root: str,
    model_factory: Callable[[ProviderConfig], object] | None = None,
    model: str = "claude-opus-4-7",
    extra_tools: list[object] | None = None,
    api_key: str = "unused-in-tests",
) -> str:
    """Run *question* through the GAIA agent harness and return the extracted answer.

    Parameters
    ----------
    question:
        The :class:`~benchmarks.gaia.dataset.GaiaQuestion` to solve.
    workspace_root:
        Directory the agent operates in. Any attachment is copied here first.
    model_factory:
        Optional injectable factory ``(ProviderConfig) -> BaseLlm``. Supplied by
        tests to avoid real provider traffic. Production callers leave it ``None``
        so the default LiteLlm path is used.
    model:
        Model identifier forwarded to :class:`~magi_agent.cli.providers.ProviderConfig`.
    extra_tools:
        Optional list of additional ADK tools to attach to the agent. When ``None``
        the runner builds the full default tool set.
    api_key:
        API key forwarded to :class:`~magi_agent.cli.providers.ProviderConfig`.
        Tests pass ``"unused-in-tests"``; production callers supply a real key.
    """

    # 1. Copy attachment into workspace_root if it exists on disk.
    if question.attachment_path and Path(question.attachment_path).exists():
        dest_name = question.file_name or Path(question.attachment_path).name
        shutil.copy2(question.attachment_path, Path(workspace_root) / dest_name)

    # 2. Build provider config.
    config = ProviderConfig(provider="anthropic", model=model, api_key=api_key)

    # 3. Build attachment note (tells the agent about the file in the workspace).
    attachment_note = ""
    file_name = question.file_name or (
        Path(question.attachment_path).name if question.attachment_path else None
    )
    if file_name:
        attachment_note = (
            f"\n\nNOTE: An attachment file '{file_name}' is present in the working "
            f"directory. Use the appropriate file tool (ImageUnderstand for images, "
            f"DocumentRead for documents/PPTX/XML/CSV, XLSXRead for spreadsheets) "
            f"to read it when answering the question."
        )

    # 4. Build runner.
    instruction = f"{GAIA_SYSTEM_PROMPT}\n\nQUESTION:\n{question.question}{attachment_note}"
    runner: CliModelRunner = build_cli_model_runner(
        config,
        instruction=instruction,
        model_factory=model_factory,
        workspace_root=workspace_root,
        tools=extra_tools,
    )

    # 5. Drive runner to completion, collecting all model text parts.
    async def _drive(message_text: str) -> list[str]:
        new_message = types.Content(role="user", parts=[types.Part(text=message_text)])
        texts: list[str] = []
        async for event in runner.run_async(
            user_id="gaia-harness",
            session_id="gaia-session",
            new_message=new_message,
        ):
            content = getattr(event, "content", None)
            for part in getattr(content, "parts", None) or []:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text:
                    texts.append(text)
        return texts

    texts = asyncio.run(_drive(question.question))
    joined = "\n".join(texts)

    # 6. Extract the final answer.
    answer = extract_final_answer(joined)

    # 7. Best-effort rescue (env-gated, default-OFF): when MAGI_ANSWER_POLICY=commit
    #    and the run produced a non-answer, drive ONE additional synthesis turn
    #    through the same runner session (at most once per question, no retry loop).
    if should_force_answer() and is_non_answer(answer):

        def _second_turn_provider(prompt: str) -> str:
            return "\n".join(asyncio.run(_drive(prompt)))

        final = finalize_answer(
            question.question,
            answer,
            joined,
            _second_turn_provider,
            config=BestEffortConfig(label_uncertainty=False),  # GAIA scorer needs bare answers
        )
        if final.synthesized:
            answer = extract_final_answer(final.text) or final.text.strip()

    return answer


__all__ = ["run_gaia_question"]
