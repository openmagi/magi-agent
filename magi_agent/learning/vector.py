"""Learning KB — vector index abstraction.

Provides a Protocol for plug-in vector storage and a minimal in-process
brute-force cosine implementation for OSS use without external dependencies.
"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable


@runtime_checkable
class LearningVectorIndex(Protocol):
    """Minimal protocol for a learning vector index."""

    def add(self, item_id: str, embedding: list[float]) -> None:
        """Store *embedding* keyed by *item_id*."""
        ...

    def query(self, embedding: list[float], *, k: int = 8) -> list[tuple[str, float]]:
        """Return up to *k* (item_id, score) pairs ranked by cosine similarity."""
        ...


class BruteForceVectorIndex:
    """In-process brute-force cosine index.

    Suitable for small to medium OSS deployments (hundreds of learnings).
    Thread-safety is not guaranteed — callers must synchronise externally
    if concurrent access is needed.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[float]] = {}

    def add(self, item_id: str, embedding: list[float]) -> None:
        self._store[item_id] = list(embedding)

    def query(self, embedding: list[float], *, k: int = 8) -> list[tuple[str, float]]:
        if not self._store:
            return []

        query_norm = _l2_norm(embedding)
        if query_norm == 0.0:
            return []

        scores: list[tuple[str, float]] = []
        for item_id, stored in self._store.items():
            stored_norm = _l2_norm(stored)
            if stored_norm == 0.0:
                continue
            dot = sum(a * b for a, b in zip(embedding, stored))
            cosine = dot / (query_norm * stored_norm)
            scores.append((item_id, cosine))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:k]


def _l2_norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


__all__ = [
    "BruteForceVectorIndex",
    "LearningVectorIndex",
]
