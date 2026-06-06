# magi_agent/recipes/first_party/legal/recipe.py
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from magi_agent.benchmarks.legalbench.models import Example, LegalTask
from magi_agent.recipes.first_party.legal.fewshot import select_fewshot
from magi_agent.recipes.first_party.legal.output_parser import parse_answer
from magi_agent.recipes.first_party.legal.prompt_variants import (
    phrase_instruction,
    select_variant,
)
from magi_agent.recipes.first_party.legal.rule_inject import inject_rule


class LegalCheckpoints(BaseModel):
    model_config = ConfigDict(frozen=True)
    few_shot: bool = True
    rule_inject: bool = True
    prompt_variant: bool = True
    constrained_parse: bool = True
    k: int = 4
    seed: int = 0


def _render(base_prompt: str, example: Example, *, task_id: str) -> str:
    try:
        return base_prompt.format(**example.fields)
    except KeyError as exc:
        raise KeyError(
            f"base_prompt references field {exc} not in example.fields "
            f"(task={task_id!r}, available={sorted(example.fields)})"
        ) from exc


def _fewshot_block(task: LegalTask, checkpoints: LegalCheckpoints) -> str:
    shots = select_fewshot(task, k=checkpoints.k, seed=checkpoints.seed)
    rendered = [f"{_render(task.base_prompt, ex, task_id=task.task_id)} {ex.answer}" for ex in shots]
    return "\n\n".join(rendered)


def build_prompt(
    task: LegalTask, example: Example, *, checkpoints: LegalCheckpoints
) -> str:
    body = _render(task.base_prompt, example, task_id=task.task_id)
    if checkpoints.prompt_variant:
        variant = select_variant(task.task_id)
        body = phrase_instruction(body, variant=variant)
    if checkpoints.few_shot:
        body = f"{_fewshot_block(task, checkpoints)}\n\n{body}"
    if checkpoints.rule_inject:
        body = inject_rule(body, task_id=task.task_id)
    return body


def parse_output(
    raw: str, task: LegalTask, *, checkpoints: LegalCheckpoints
) -> str | None:
    if not checkpoints.constrained_parse:
        return raw.strip()
    return parse_answer(raw, labels=task.labels)
