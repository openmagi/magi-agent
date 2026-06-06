# magi_agent/benchmarks/legalbench/models.py
from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

ReasoningType = Literal[
    "issue",
    "rule-recall",
    "rule-application",
    "rule-conclusion",
    "interpretation",
    "rhetorical",
]

_FROZEN = ConfigDict(frozen=True)


class Example(BaseModel):
    model_config = _FROZEN
    fields: Mapping[str, str]
    answer: str


class LegalTask(BaseModel):
    model_config = _FROZEN
    task_id: str
    reasoning_type: ReasoningType
    base_prompt: str
    train: tuple[Example, ...]
    test: tuple[Example, ...]
    labels: tuple[str, ...]
