"""Hipocampus memory search backends (PR2, read-side, unwired).

Public surface:

  * :class:`SearchBackend` / :class:`SearchHit` / :class:`SearchCapabilities`
    — the dependency-free contract (see :mod:`base`).
  * :class:`PyBM25Backend` — pure-Python BM25, the zero-dependency DEFAULT.
  * :class:`QmdBackend` — thin wrapper over the external ``qmd`` CLI.
  * :class:`QmdHttpBackend` — HTTP client to an external qmd endpoint (sidecar).
  * :func:`select_search_backend` — pick a backend from a resolved
    ``MemoryRuntimeConfig``.

NOTE: this package is intentionally NOT imported from
:mod:`magi_agent.memory` (``memory/__init__.py``).  Importing :mod:`qmd` pulls in
``subprocess`` / ``shutil``; the memory boundary tests assert the contract /
policy / adapter modules stay process-free, so ``search`` is kept self-contained
and imported explicitly by the PR5 recall wiring only.
"""
from __future__ import annotations

from magi_agent.memory.config import MemoryRuntimeConfig

from .backend_cache import (
    bind_or_reindex,
    cached_search_backend,
    clear_search_backend_cache,
)
from .base import SearchBackend, SearchCapabilities, SearchHit
from .bm25 import PyBM25Backend
from .qmd import QmdBackend
from .qmd_http import QmdHttpBackend


def select_search_backend(
    config: MemoryRuntimeConfig, *, vector: bool = False
) -> SearchBackend:
    """Choose the search backend for a resolved memory config.

    Resolution order:

    1. **HTTP sidecar** (:class:`QmdHttpBackend`) — ONLY for the explicit-vector
       caller (``vector=True``) when ``config.qmd_endpoint`` is set AND
       ``config.vector_search`` is on.  This is the hosted path: the runtime
       container has no qmd binary and talks to a per-pod qmd sidecar over HTTP
       for semantic search.  The per-turn recall path (``vector=False``) is NEVER
       routed here — it keeps its own ``QmdClient`` HTTP tier — so this does not
       fire on the hot path.
    2. **Local qmd CLI** (:class:`QmdBackend`) when the operator prefers qmd AND
       the binary is resolvable on PATH.  The ``qmd``-availability probe is
       delegated to :attr:`QmdBackend.available` so ``subprocess``/``shutil`` stay
       confined to :mod:`qmd`.  Vector mode here engages only when ``vector=True``
       AND ``config.vector_search`` (a vsearch CLI call cold-loads the model
       ~10-40s, so it stays off the hot path — see :mod:`qmd`).
    3. **Pure-Python BM25** (:class:`PyBM25Backend`) — the always-available
       default.  It has no vector mode, so an explicit ``vector=True`` request
       degrades to BM25 rather than failing when no qmd is reachable.

    No endpoint configured ⇒ steps 2/3 only ⇒ byte-identical to the pre-sidecar
    behavior (local OSS is unaffected).

    The CLI backend is constructed with the resolved
    ``config.prefer_qmd_auto_register`` opt-in (default False) so a uniform
    ``backend.reindex(root)`` never registers a NEW global qmd collection unless
    explicitly opted in (multi-tenant safety).
    """
    if vector and config.vector_search and config.qmd_endpoint:
        return QmdHttpBackend(endpoint=config.qmd_endpoint)
    if config.prefer_qmd:
        backend = QmdBackend(
            auto_register=config.prefer_qmd_auto_register,
            vector=vector and config.vector_search,
        )
        if backend.available:
            return backend
    return PyBM25Backend()


__all__ = [
    "PyBM25Backend",
    "QmdBackend",
    "QmdHttpBackend",
    "SearchBackend",
    "SearchCapabilities",
    "SearchHit",
    "bind_or_reindex",
    "cached_search_backend",
    "clear_search_backend_cache",
    "select_search_backend",
]
