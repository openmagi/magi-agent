"""Pydantic models for the LegalBench lean harness data layer."""
from __future__ import annotations

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

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    extra="forbid",
)


class Example(BaseModel):
    model_config = _MODEL_CONFIG
    fields: dict[str, str]
    answer: str


class LegalTask(BaseModel):
    model_config = _MODEL_CONFIG
    task_id: str
    reasoning_type: ReasoningType
    base_prompt: str
    train: tuple[Example, ...]
    test: tuple[Example, ...]
    labels: tuple[str, ...]
