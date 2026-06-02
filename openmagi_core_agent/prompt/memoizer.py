"""Section memoization for the prompt caching pipeline.

:class:`PromptSectionCache` caches computed prompt section values across turns
within a single session, avoiding redundant re-rendering of stable sections.

Design notes
------------
- **Identity sections** (soul, tools, deferral block, output rules) are stable
  across turns → high cache value.  They should *not* appear in
  ``cache_break_keys``.
- **Temporal context / session header** change every turn → pass their keys in
  ``cache_break_keys`` so they are always recomputed and never stored.
- **Memory mode** changes rarely → default-cached; callers can call
  ``invalidate("memory_mode")`` when the mode changes.
- Thread safety: this cache is per-session (not shared across sessions).
  Python's GIL provides sufficient safety for plain ``dict`` operations without
  explicit locking.
"""

from __future__ import annotations

from typing import Callable


class PromptSectionCache:
    """Caches computed prompt section values across turns within a session.

    Identity sections (soul, tools, etc.) are stable across turns → high cache
    value.  Temporal context changes every turn → never cached (listed in
    ``cache_break_keys``).  Memory mode changes rarely → cached with
    invalidation.

    Args:
        cache_break_keys: Frozenset of section keys that must **always**
            recompute (never stored in the cache dict).  Defaults to an empty
            frozenset so all keys are cacheable by default.
    """

    def __init__(self, cache_break_keys: frozenset[str] = frozenset()) -> None:
        self._cache: dict[str, str] = {}
        self._cache_break_keys: frozenset[str] = cache_break_keys

    def get_or_compute(self, key: str, compute_fn: Callable[[], str]) -> str:
        """Return the cached value for *key*, or compute, cache, and return it.

        If *key* is in ``cache_break_keys`` the value is always recomputed and
        **not** stored in the cache dict.

        Args:
            key: Section identifier (e.g. ``"soul"``, ``"temporal_context"``).
            compute_fn: Zero-argument callable that produces the section string.
                Called only when the cache does not hold a valid entry for *key*.

        Returns:
            The section string — either from cache or freshly computed.
        """
        if key in self._cache_break_keys:
            return compute_fn()
        if key not in self._cache:
            self._cache[key] = compute_fn()
        return self._cache[key]

    def invalidate(self, key: str) -> None:
        """Remove *key* from the cache so the next call recomputes it.

        Silently does nothing if *key* is not currently cached.

        Args:
            key: Section identifier to remove from the cache dict.
        """
        self._cache.pop(key, None)

    def invalidate_all(self) -> None:
        """Clear the entire cache (e.g. on ``/compact`` or ``/clear``).

        After this call ``stats["cached_keys"]`` returns ``0``.
        """
        self._cache.clear()

    @property
    def stats(self) -> dict[str, int]:
        """Diagnostic snapshot of the current cache state.

        Returns:
            A dict with a single key ``"cached_keys"`` whose value is the
            number of entries currently held in the cache dict.  Break-key
            entries are never stored, so they do not appear in this count.
        """
        return {"cached_keys": len(self._cache)}
