"""Session-scoped frozen snapshot cache for memory prompt projection.

Computes the memory snapshot ONCE per (session_key, memory_mode) pair and
reuses the same string for every subsequent call in that session.  This
ensures the volatile/dynamic section of the system prompt never changes
mid-session — which would invalidate the cached static prefix — while still
allowing a fresh snapshot after a session switch or /reset.

Usage::

    cache = MemorySnapshotCache(workspace_root=Path("/path/to/workspace"))
    block = cache.get(session_key, memory_mode="normal")
    # pass `block` as `memory_snapshot_block=block` to build_system_prompt(...)

    # On session switch or reset:
    cache.invalidate(session_key)
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.memory.prompt_projection import project_memory_snapshot


class MemorySnapshotCache:
    """Session-scoped frozen snapshot cache.

    Thread-safety: not thread-safe (single-threaded ADK runner assumption).
    """

    def __init__(self, *, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        # Internal dict keyed by "session_key:memory_mode"
        self._cache: dict[str, str] = {}

    def get(self, session_key: str, *, memory_mode: str = "normal") -> str:
        """Return the frozen snapshot block for this session.

        On first call for a given (session_key, memory_mode) pair, calls
        :meth:`_compute` and stores the result.  Subsequent calls return the
        cached string without re-reading the workspace.

        Returns ``""`` when the projection gate is off or memory_mode is
        ``"incognito"``.
        """
        cache_key = f"{session_key}:{memory_mode}"
        if cache_key not in self._cache:
            self._cache[cache_key] = self._compute(memory_mode=memory_mode)
        return self._cache[cache_key]

    def _compute(self, *, memory_mode: str = "normal") -> str:
        """Call the projection function and return the snapshot block.

        Returns ``""`` when the gate is off or mode is incognito.
        """
        result = project_memory_snapshot(
            workspace_root=self.workspace_root,
            memory_mode=memory_mode,
        )
        return result.snapshot_block if result.enabled else ""

    def invalidate(self, session_key: str) -> None:
        """Drop all cache entries for *session_key*.

        After invalidation, the next :meth:`get` for this session will call
        :meth:`_compute` again, picking up any MEMORY.md changes since the
        last snapshot.
        """
        prefix = f"{session_key}:"
        keys_to_drop = [k for k in self._cache if k.startswith(prefix)]
        for k in keys_to_drop:
            del self._cache[k]


__all__ = ["MemorySnapshotCache"]
