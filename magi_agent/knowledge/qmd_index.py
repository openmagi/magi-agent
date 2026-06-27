"""Optional ``qmd``-accelerated search over the workspace knowledge base.

The dependency-free :func:`magi_agent.knowledge.local_index.search_local_knowledge`
keyword scan is always available. When the operator has run ``magi knowledge init``
(or enabled lazy auto-registration), the ``knowledge/`` (and ``.magi/knowledge/``)
subtrees are registered as per-workspace ``qmd`` collections (``magi-kb-<sha1>``),
giving BM25 ranking and scale that the linear scan can't. This module is the thin
bridge that searches those collections and registers them.

It reuses the generalized :class:`~magi_agent.memory.search.qmd.QmdBackend`
(``subdir="knowledge"`` / prefix ``magi-kb-``) so all ``qmd`` subprocess handling
stays confined to that module. Everything here is fail-soft: when ``qmd`` is
absent or no collection is registered, the search helper returns ``None`` so the
caller falls back to the linear scan.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from magi_agent.knowledge.local_index import KNOWLEDGE_DIR_NAMES

#: Per-workspace qmd collection-name prefix for KB subtrees (distinct from the
#: ``magi-mem-`` memory namespace so the two never collide in a shared index).
KB_COLLECTION_PREFIX = "magi-kb-"


def _backend(subdir: str):  # -> QmdBackend
    from magi_agent.memory.search.qmd import QmdBackend  # noqa: PLC0415

    return QmdBackend(subdir=subdir, collection_prefix=KB_COLLECTION_PREFIX)


def qmd_available() -> bool:
    """True when the ``qmd`` binary is resolvable on PATH."""
    return _backend("knowledge").available


def register_knowledge_collections(root: Path) -> list[str]:
    """Register each existing KB subtree under ``root`` as a qmd collection.

    Uses the explicit auto-register opt-in (BM25 index on add). Returns the list
    of collection names that were registered/refreshed. Fail-soft: returns ``[]``
    when qmd is absent or no KB dir exists yet.
    """
    if not qmd_available():
        return []
    from magi_agent.memory.search.qmd import collection_name_for  # noqa: PLC0415

    names: list[str] = []
    for subdir in KNOWLEDGE_DIR_NAMES:
        if not (root / subdir).is_dir():
            continue
        backend = _backend(subdir)
        backend.reindex(root, allow_auto_register=True)
        if backend.bound:
            names.append(
                collection_name_for(
                    (root / subdir).resolve(), prefix=KB_COLLECTION_PREFIX
                )
            )
    return names


def search_knowledge_via_qmd(
    roots: Sequence[Path],
    query: str,
    *,
    k: int = 5,
    auto_register: bool = False,
) -> list[dict[str, object]] | None:
    """Search the KB qmd collections, newest-relevance first.

    Returns provider-shaped records (the same shape as
    :func:`~magi_agent.knowledge.local_index.search_local_knowledge`) when a qmd
    collection is bound, or ``None`` when qmd is unavailable / no collection is
    registered so the caller falls back to the linear scan. An empty list means
    "qmd searched and matched nothing" (authoritative, do not fall back).
    """
    if not query.strip() or k <= 0 or not qmd_available():
        return None

    scored: list[tuple[float, dict[str, object]]] = []
    bound_any = False
    for root in roots:
        for subdir in KNOWLEDGE_DIR_NAMES:
            if not (root / subdir).is_dir():
                continue
            backend = _backend(subdir)
            if not backend.bind(root):
                if auto_register:
                    backend.reindex(root, allow_auto_register=True)
                if not backend.bound:
                    continue
            bound_any = True
            for hit in backend.search(query, k=k):
                scored.append((hit.score, _record_from_hit(hit.path, hit.content)))

    if not bound_any:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return [record for _score, record in scored[:k]]


def _record_from_hit(path: str, content: str) -> dict[str, object]:
    return {
        "sourceRef": f"knowledge:{path}",
        # The provider boundary opacifies slashed source refs but surfaces
        # ``title`` and ``publicPreview`` verbatim for public-safe records.
        "title": path,
        "publicPreview": content,
        "snippet": content,
        "content": content,
        "metadata": {
            "path": path,
            "visibility": "public-safe",
            "publicSafe": True,
        },
    }


__all__ = [
    "KB_COLLECTION_PREFIX",
    "qmd_available",
    "register_knowledge_collections",
    "search_knowledge_via_qmd",
]
