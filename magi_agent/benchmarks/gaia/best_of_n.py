"""Deterministic Best-of-N answer selection by normalized majority vote."""
from __future__ import annotations

from collections.abc import Sequence

from magi_agent.benchmarks.gaia.scorer import normalize_str


def majority_vote(answers: Sequence[str]) -> str:
    counts: dict[str, int] = {}
    rep: dict[str, str] = {}
    order: list[str] = []
    for a in answers:
        key = normalize_str(a)
        if key not in counts:
            counts[key] = 0
            rep[key] = a
            order.append(key)
        counts[key] += 1
    if not order:
        return ""
    best = max(order, key=lambda k: counts[k])
    return rep[best]


__all__ = ["majority_vote"]
