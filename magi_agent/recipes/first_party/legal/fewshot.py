# magi_agent/recipes/first_party/legal/fewshot.py
from __future__ import annotations

import random

from magi_agent.benchmarks.legalbench.models import Example, LegalTask


def select_fewshot(
    task: LegalTask,
    *,
    k: int,
    seed: int,
    curated_indices: tuple[int, ...] | None = None,
) -> tuple[Example, ...]:
    if curated_indices is not None:
        return tuple(task.train[i] for i in curated_indices)
    if k >= len(task.train):
        return task.train
    rng = random.Random(seed)
    idx = sorted(rng.sample(range(len(task.train)), k))
    return tuple(task.train[i] for i in idx)
