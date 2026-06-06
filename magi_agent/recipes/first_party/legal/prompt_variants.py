# magi_agent/recipes/first_party/legal/prompt_variants.py
from __future__ import annotations

from typing import Literal

Variant = Literal["plain", "technical"]

# Per-task variant chosen on the TRAIN split and frozen here. Do not tune on test.
PROMPT_VARIANTS: dict[str, Variant] = {}


def select_variant(task_id: str) -> Variant:
    return PROMPT_VARIANTS.get(task_id, "plain")


def phrase_instruction(instruction: str, *, variant: Variant) -> str:
    if variant == "technical":
        return f"As a legal expert applying the controlling rule: {instruction}"
    return f"Read carefully and answer. {instruction}"
