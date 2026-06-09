"""SearchBackend abstraction for Hipocampus memory (PR2, read-side).

This module defines the minimal, dependency-free contract that every memory
search backend implements.  It is **not wired into the agent loop** — PR5 owns
the live recall path that consumes :meth:`SearchBackend.search`.  Here we only
declare the Protocol + value types so :mod:`bm25` and :mod:`qmd` can implement
against a shared seam and ``select_search_backend`` can pick one.

GOVERNANCE INVARIANT
--------------------
A flag gates *activation*, never *capability*.  These backends are pure search
machinery: when the memory subsystem is OFF nobody constructs them, but when ON
they must actually work.  Vector search is an opt-in that is OUT OF SCOPE for
PR2 — backends advertise ``supports_vector=False`` and implement BM25 keyword
search only; no embeddings.

This module imports only stdlib + typing — no network/provider/subprocess deps —
so it is safe to import anywhere.  (The qmd backend pulls in ``subprocess`` /
``shutil`` itself; keep that confined to :mod:`magi_agent.memory.search.qmd`.)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A single ranked search result.

    ``content`` is a snippet (or the indexed body) of the matched document;
    callers (PR5) are responsible for any further redaction / capping before the
    text reaches a prompt.  ``score`` is backend-relative and only meaningful for
    descending ordering within one backend's result set.
    """

    path: str
    content: str
    score: float


@dataclass(frozen=True, slots=True)
class SearchCapabilities:
    """Static description of what a backend can do."""

    name: str
    supports_vector: bool = False


@runtime_checkable
class SearchBackend(Protocol):
    """Read-side keyword search over the Hipocampus ``memory/`` tree.

    Implementations index ``*.md`` files under a workspace ``memory/`` directory
    and answer BM25 keyword queries.  The contract is intentionally tiny: build
    an index, query it, and describe yourself.
    """

    @property
    def capabilities(self) -> SearchCapabilities:
        """Static capabilities (name, vector support)."""

    def reindex(self, root: Path) -> None:
        """(Re)scan ``root`` and build the in-memory index.

        ``root`` is the workspace root; the backend scans the ``memory/`` tree
        beneath it.  Re-scanning on every call is acceptable — the corpus is
        small (YAGNI: no persisted index file).
        """

    def search(self, query: str, *, k: int) -> list[SearchHit]:
        """Return up to ``k`` hits for ``query``, ranked by score descending."""


__all__ = [
    "SearchBackend",
    "SearchCapabilities",
    "SearchHit",
]
