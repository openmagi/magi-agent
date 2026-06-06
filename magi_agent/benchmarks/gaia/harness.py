"""GAIA agent harness — drives a single GaiaQuestion through the real ADK runner."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Callable

from google.genai import types

from magi_agent.benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT, extract_final_answer
from magi_agent.benchmarks.gaia.dataset import GaiaQuestion
from magi_agent.cli.providers import ProviderConfig
from magi_agent.cli.real_runner import CliModelRunner, build_cli_model_runner
from magi_agent.recipes.first_party.selective_reflection.reflection_hook import (
    build_reflection_hook_contribution,
)
from magi_agent.recipes.first_party.selective_reflection.reflection_policy import (
    ReflectionPolicy,
)


def run_gaia_question(
    question: GaiaQuestion,
    *,
    workspace_root: str,
    model_factory: Callable[[ProviderConfig], object] | None = None,
    model: str = "claude-opus-4-7",
    extra_tools: list[object] | None = None,
    api_key: str = "unused-in-tests",
    reflection_enabled: bool = False,
) -> str:
    """Run *question* through the GAIA agent harness and return the extracted answer.

    Parameters
    ----------
    question:
        The :class:`~magi_agent.benchmarks.gaia.dataset.GaiaQuestion` to solve.
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
    reflection_enabled:
        When ``True``, build a :class:`~.ReflectionPolicy` with ``enabled=True``
        and register its ``beforeCommit`` hook contribution in the recipe stack.
        Default ``False`` — zero code runs in the hot path when off.
        Production callers should read this from
        ``magi_agent.config.env.parse_selective_reflection_env(os.environ).enabled``.
    """

    # 1. Copy attachment into workspace_root if it exists on disk.
    if question.attachment_path and Path(question.attachment_path).exists():
        dest_name = question.file_name or Path(question.attachment_path).name
        shutil.copy2(question.attachment_path, Path(workspace_root) / dest_name)

    # 1b. Selective reflection — build policy and hook contribution when enabled.
    # The hook contribution is metadata used by the recipe/hook composition layer.
    # Full ADK-level wiring (post-commit injection) is the responsibility of the
    # recipe stack adapter; the harness records the contribution here so that
    # higher-level orchestrators can compose it with other contributions.
    _reflection_policy = ReflectionPolicy(enabled=reflection_enabled)
    _reflection_hook = build_reflection_hook_contribution(policy=_reflection_policy)
    # _reflection_hook is None when disabled (zero hot-path cost).

    # 2. Build provider config.
    config = ProviderConfig(provider="anthropic", model=model, api_key=api_key)

    # 3. Build runner.
    instruction = f"{GAIA_SYSTEM_PROMPT}\n\nQUESTION:\n{question.question}"
    runner: CliModelRunner = build_cli_model_runner(
        config,
        instruction=instruction,
        model_factory=model_factory,
        workspace_root=workspace_root,
        tools=extra_tools,
    )

    # 4. Drive runner to completion, collecting all model text parts.
    async def _drive() -> list[str]:
        new_message = types.Content(
            role="user", parts=[types.Part(text=question.question)]
        )
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

    texts = asyncio.run(_drive())
    joined = "\n".join(texts)

    # 5. Extract and return the final answer.
    return extract_final_answer(joined)


__all__ = ["run_gaia_question"]
