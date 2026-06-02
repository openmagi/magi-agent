"""Within-run result cache for the workflow executor — PR3.

Scope: within a single ``execute_workflow`` call / session only.
No durable storage, no cross-session persistence, no SQLite, no files.

Design:
- ``WorkflowResultCache`` is a plain in-memory dict keyed by a stable
  sha1-based cache key (see below).
- ``CachedChildResult`` stores the status of a completed child (only
  ``"accepted"`` results are written; error/blocked/disabled results are NOT
  cached so they are always re-dispatched on resume).
- The cache is passed into ``execute_workflow(result_cache=...)`` by the
  caller who manages the run/session lifetime.  When ``None`` is passed
  (the default), the executor behaves byte-identically to PR1/PR2.
- Observability: the executor emits ``runtime_trace_event`` events via
  ``_trace_event`` for each cache-hit and cache-store.  When an optional
  ``event_sink`` callable is passed to ``execute_workflow``, each emitted
  event dict is forwarded to the sink, making observability testable without
  file I/O, SQLite, or Redis.

Cache key:
    A sha1 digest of ``(workflow_id, version, recipe_index, recipe_id)``
    formatted as ``"wf-cache-<hex16>"``.  This digest is computed in the
    executor via ``_child_cache_key()`` and is stable across re-runs of the
    same workflow contract.  The ``ResearchChildTaskSpec.task_id`` field is
    NOT used as the cache key because the recipe runner's ``_PRIVATE_TEXT_RE``
    sanitizer can collapse distinct task identifiers into the same string.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Cached result record
# ---------------------------------------------------------------------------

CachedChildStatus = Literal["accepted"]


@dataclass(frozen=True)
class CachedChildResult:
    """Sanitised record of a completed child result stored in the cache.

    Only ``status="accepted"`` results are cached.  The task_id is the
    stable child ref key; no raw transcript or tool logs are stored here.
    """

    task_id: str
    status: CachedChildStatus


# ---------------------------------------------------------------------------
# Cache store
# ---------------------------------------------------------------------------

class WorkflowResultCache:
    """Within-run in-memory result cache keyed by stable sha1 cache key.

    The cache key is a ``"wf-cache-<hex16>"`` sha1 digest computed from
    ``(workflow_id, version, recipe_index, recipe_id)`` via
    ``_child_cache_key()`` in the executor.  This is NOT the
    ``ResearchChildTaskSpec.task_id`` field (which can be collapsed by the
    recipe runner's ``_PRIVATE_TEXT_RE`` sanitizer).

    Caller-owned: the caller creates one instance per run/session and passes
    it to ``execute_workflow``.  Lifetime is tied to the caller's run scope.
    """

    def __init__(self) -> None:
        self._store: dict[str, CachedChildResult] = {}

    def get(self, task_id: str) -> CachedChildResult | None:
        """Return the cached result for *task_id*, or ``None`` if absent."""
        return self._store.get(task_id)

    def store(self, task_id: str, result: CachedChildResult) -> None:
        """Store *result* for *task_id*.  Idempotent (overwrites on collision)."""
        self._store[task_id] = result

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, task_id: object) -> bool:
        return task_id in self._store


__all__ = [
    "CachedChildResult",
    "CachedChildStatus",
    "WorkflowResultCache",
]
