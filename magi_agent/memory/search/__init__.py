"""Hipocampus memory search backends (PR2, read-side, unwired).

Public surface:

  * :class:`SearchBackend` / :class:`SearchHit` / :class:`SearchCapabilities`
    — the dependency-free contract (see :mod:`base`).
  * :class:`PyBM25Backend` — pure-Python BM25, the zero-dependency DEFAULT.
  * :class:`QmdBackend` — thin wrapper over the external ``qmd`` CLI.
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

from .base import SearchBackend, SearchCapabilities, SearchHit
from .bm25 import PyBM25Backend
from .qmd import QmdBackend


def select_search_backend(config: MemoryRuntimeConfig) -> SearchBackend:
    """Choose the search backend for a resolved memory config.

    Returns :class:`QmdBackend` when the operator prefers qmd AND the binary is
    resolvable on PATH; otherwise the pure-Python :class:`PyBM25Backend` (which
    always works with no external dependency).

    The ``qmd``-availability probe is delegated to :attr:`QmdBackend.available`
    so ``subprocess``/``shutil`` stay confined to :mod:`qmd` within this package.

    Vector search is out of scope for PR2 — ``config.vector_search`` does not
    change the selection here; both backends report ``supports_vector=False``.

    The qmd backend is constructed with the resolved
    ``config.prefer_qmd_auto_register`` opt-in (default False) so that a uniform
    ``backend.reindex(root)`` call never registers a NEW global qmd collection
    unless the operator explicitly opted in (multi-tenant safety).
    """
    if config.prefer_qmd:
        backend = QmdBackend(auto_register=config.prefer_qmd_auto_register)
        if backend.available:
            return backend
    return PyBM25Backend()


__all__ = [
    "PyBM25Backend",
    "QmdBackend",
    "SearchBackend",
    "SearchCapabilities",
    "SearchHit",
    "select_search_backend",
]
