"""Process-scope search backend cache (PR-D1 / N-12).

Promotes the per-call backend construction in the per-turn recall path to a
process-lifetime cache so PyBM25Backend's H-27 mtime-signature cache and
QmdBackend's bound collection survive across turns.

Invalidation contract:
  * PyBM25Backend: corpus changes are handled by the backend itself - every
    turn still calls reindex(root), which is a stat-walk no-op on an
    unchanged tree (H-27 signature) and a rebuild when the tree changed.
  * QmdBackend: the qmd index is refreshed OFF this path only (startup /
    ``magi memory init`` / explicit maintenance). bind_or_reindex never runs
    ``qmd update`` once a collection is bound.
  * Cache entries live for the process lifetime. clear_search_backend_cache()
    exists for tests and future explicit-refresh triggers.

Thread-safety: cache CONSTRUCTION is serialised by ``_cache_guard``, but the
backend instances themselves (PyBM25 ``_docs`` etc.) are NOT thread-safe. Every
current caller runs on the single event-loop thread. A future N-47 recall
offload onto ``to_thread`` must add per-key locking around backend use before
sharing one instance across threads.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

_cache_guard = threading.Lock()
_cache: dict[tuple, object] = {}


def _cache_key(config: object, root: Path, *, vector: bool) -> tuple:
    return (
        str(Path(root).resolve()),
        bool(vector),
        bool(getattr(config, "prefer_qmd", False)),
        bool(getattr(config, "prefer_qmd_auto_register", False)),
        bool(getattr(config, "vector_search", False)),
        str(getattr(config, "qmd_endpoint", "") or ""),
    )


def cached_search_backend(
    config: object,
    root: Path,
    *,
    factory: Callable[[], object],
    vector: bool = False,
) -> object:
    """Return the cached backend for (root, config knobs), constructing via
    ``factory`` on first use. ``factory`` is the caller's (monkeypatchable)
    selector seam so tests keep injecting fakes through the existing seams."""
    key = _cache_key(config, root, vector=vector)
    with _cache_guard:
        backend = _cache.get(key)
        if backend is None:
            backend = factory()
            _cache[key] = backend
        return backend


def bind_or_reindex(backend: object, root: Path) -> None:
    """Hot-path index preparation (PR-D1 / N-13).

    qmd-like backends (expose ``bind``/``bound``): bind-first, mirroring the
    knowledge/qmd_index.py production precedent. Once bound, subsequent turns
    are a pure attribute check - ``qmd update`` NEVER runs here. When bind
    fails, fall back to a single reindex(root) (respects the instance
    auto-register opt-in; a no-op + empty search when not opted in).

    Plain backends (PyBM25Backend, fakes without ``bind``): call
    reindex(root) - the H-27 signature makes this a stat walk on an
    unchanged tree.

    Note: on a qmd config where the collection is NOT registered and
    auto-register is OFF, ``bind()`` runs one ``qmd collection list`` subprocess
    per turn (fast, but not zero); this matches the knowledge/qmd_index.py
    precedent and is accepted.
    """
    bind = getattr(backend, "bind", None)
    if callable(bind):
        if bool(getattr(backend, "bound", False)):
            return
        if bind(root):
            return
        backend.reindex(root)
        return
    backend.reindex(root)


def clear_search_backend_cache() -> None:
    with _cache_guard:
        _cache.clear()


__all__ = ["bind_or_reindex", "cached_search_backend", "clear_search_backend_cache"]
