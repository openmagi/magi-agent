# magi_agent/benchmarks/legalbench/runner.py
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from magi_agent.benchmarks.legal_eval import AnswerRecord
from magi_agent.benchmarks.legalbench.models import LegalTask
from magi_agent.recipes.first_party.legal.recipe import (
    LegalCheckpoints,
    build_prompt,
    parse_output,
)

Complete = Callable[[str], str]


def run_subset(
    tasks: Sequence[LegalTask],
    *,
    complete: Complete,
    checkpoints: LegalCheckpoints,
) -> list[AnswerRecord]:
    records: list[AnswerRecord] = []
    for task in tasks:
        for index, example in enumerate(task.test):
            prompt = build_prompt(task, example, checkpoints=checkpoints)
            raw = complete(prompt)
            predicted = parse_output(raw, task, checkpoints=checkpoints)
            records.append(
                AnswerRecord(
                    task_id=task.task_id,
                    reasoning_type=task.reasoning_type,
                    index=index,
                    predicted=predicted,
                    gold=example.answer,
                )
            )
    return records


def baseline_checkpoints() -> LegalCheckpoints:
    return LegalCheckpoints(
        few_shot=False,
        rule_inject=False,
        prompt_variant=False,
        constrained_parse=False,
    )


@dataclass(frozen=True)
class AblationCell:
    disabled: str
    checkpoints: LegalCheckpoints


def ablation_configs(full: LegalCheckpoints) -> list[AblationCell]:
    cells: list[AblationCell] = []
    for field in ("few_shot", "rule_inject", "prompt_variant", "constrained_parse"):
        cells.append(AblationCell(disabled=field, checkpoints=replace_flag(full, field)))
    return cells


def replace_flag(checkpoints: LegalCheckpoints, field: str) -> LegalCheckpoints:
    return checkpoints.model_copy(update={field: False})
