"""Per-turn query-based memory recall block builder (PR-E item 3).

The static memory snapshot (``project_memory_snapshot`` → ``<memory-context>``)
is frozen per session and takes NO query.  This module adds an OPTIONAL,
per-turn, query-driven recall: given the current user message, it runs the local
``memory/search`` backend (BM25/qmd) over the workspace ``memory/`` tree and
fences the top hits as a clearly-marked ``<memory-recall hidden="true">`` block
for injection ALONGSIDE the static snapshot.

GOVERNANCE INVARIANT
--------------------
Gated, default-OFF.  The block is produced ONLY when BOTH ``recall_enabled`` and
``prefer_local_search`` are on (and the master is on, via the resolver).  When
off — the default — every call returns ``""`` and the prompt is byte-identical
to before this wiring.

Safety:
  * Incognito memory mode suppresses recall entirely (mirrors the snapshot path).
  * Each hit is run through the SAME redactor as the static snapshot
    (``_redact_snapshot_content``) so secrets / private paths never reach the
    prompt.
  * The combined block is bounded by ``recall_max_bytes`` (UTF-8 aware).
  * Fail-soft: ANY error (missing binary, subprocess failure, import error)
    returns ``""`` — a search problem must NEVER break the turn.

Hot-path note: ``search`` reindexes + queries the (small) personal memory tree
synchronously.  The corpus is tiny so this is cheap, and the qmd backend caps
its own subprocess via a timeout; but see the PR report for the bounded/offload
follow-up if the tree ever grows.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MEMORY_RECALL_OPEN = '<memory-recall hidden="true">'
MEMORY_RECALL_CLOSE = "</memory-recall>"


def select_search_backend(config: object) -> object:
    """Lazy seam for the local search backend selector.

    Wrapped (not a top-level import) so this module — which the cold-start CLI
    prompt path imports — does not pull in ``subprocess`` (via the qmd backend)
    until recall is actually invoked with the gate ON.  Tests monkeypatch this
    attribute to inject a fake backend.
    """
    from magi_agent.memory.search import (  # noqa: PLC0415
        select_search_backend as _select,
    )

    return _select(config)


def build_cli_memory_recall_block(
    *,
    workspace_root: str | None,
    query: str,
    memory_mode: str,
) -> str:
    """Return a fenced ``<memory-recall>`` block for ``query``, or ``""``.

    All early-exit conditions (no workspace, empty query, incognito, gate off,
    no hits, any error) return ``""``; the function never raises.

    Args:
        workspace_root: Absolute workspace root, or ``None`` (bare CLI → "").
        query: The current user message; used as the search query.
        memory_mode: ``"normal"`` | ``"read_only"`` | ``"incognito"``.
            Incognito suppresses recall.
    """
    if workspace_root is None or not query.strip():
        return ""
    try:
        from magi_agent.tools.memory_mode_guard import (  # noqa: PLC0415
            is_incognito_memory_mode,
        )

        if is_incognito_memory_mode(memory_mode):
            return ""

        from magi_agent.memory.config import resolve_memory_config  # noqa: PLC0415

        config = resolve_memory_config()
        # BOTH gates required: recall path active AND local search selected.
        # Both now follow the master switch (PR1 dual-gate fix), so master-on
        # alone enables this block; an explicit prefer-local override still wins.
        if not (config.recall_enabled and config.prefer_local_search):
            return ""

        return _build_block(
            workspace_root=workspace_root,
            query=query,
            recall_k=config.recall_k,
            max_bytes=config.recall_max_bytes,
            config=config,
        )
    except Exception:
        logger.debug("Memory recall failed; skipping", exc_info=True)
        return ""


def _has_indexable_memory(root: "Path") -> bool:
    """True when ``root`` holds anything the BM25 backend would index.

    Mirrors ``PyBM25Backend`` (``memory/search/bm25.py``): any ``*.md`` under
    ``memory/`` (recursive) OR a top-level ``MEMORY.md`` / ``ROOT.md``.  Used as
    a hot-path guard so an empty/fresh workspace never triggers a reindex scan.
    Fail-soft: any filesystem error reports "indexable" so the normal
    (try/except-wrapped) search path still runs rather than silently skipping.
    """
    try:
        memory_dir = root / "memory"
        if memory_dir.is_dir() and next(memory_dir.rglob("*.md"), None) is not None:
            return True
        return any((root / name).is_file() for name in ("MEMORY.md", "ROOT.md"))
    except OSError:
        return True


def _build_block(
    *,
    workspace_root: str,
    query: str,
    recall_k: int,
    max_bytes: int,
    config: object,
) -> str:
    """Inner implementation — may raise; caller wraps in try/except."""
    from pathlib import Path  # noqa: PLC0415

    from magi_agent.memory.prompt_projection import (  # noqa: PLC0415
        _redact_snapshot_content,
        _slice_utf8,
    )

    root = Path(workspace_root)
    # Cheap empty-tree guard: skip the per-turn reindex+search entirely when the
    # workspace has no indexable memory.  The PyBM25 backend indexes
    # ``memory/**/*.md`` plus top-level ``MEMORY.md`` / ``ROOT.md`` (see
    # ``memory/search/bm25.py``); if none of those exist there is nothing to
    # rank, so a fresh/empty workspace pays no scan cost on the hot path.
    if not _has_indexable_memory(root):
        return ""
    backend = select_search_backend(config)
    backend.reindex(root)
    hits = backend.search(query, k=max(int(recall_k), 1))
    if not hits:
        return ""

    headroom = len(MEMORY_RECALL_OPEN.encode()) + len(MEMORY_RECALL_CLOSE.encode()) + 4
    content_budget = max(int(max_bytes) - headroom, 0)
    if content_budget <= 0:
        return ""

    parts: list[str] = []
    remaining = content_budget
    for hit in hits:
        if remaining <= 0:
            break
        path = getattr(hit, "path", None)
        content = getattr(hit, "content", None)
        if not isinstance(path, str) or not isinstance(content, str):
            continue
        # ReDoS guard: bound raw content before redaction regexes run.
        redacted = _redact_snapshot_content(_slice_utf8(content, remaining))
        if not redacted.strip():
            continue
        part = f"<!-- {path} -->\n{redacted}"
        parts.append(part)
        remaining = max(remaining - len(part.encode("utf-8")) - 2, 0)

    if not parts:
        return ""

    combined = _slice_utf8("\n\n".join(parts), content_budget)
    if not combined.strip():
        return ""
    return f"{MEMORY_RECALL_OPEN}\n{combined}\n{MEMORY_RECALL_CLOSE}"


__all__ = [
    "MEMORY_RECALL_CLOSE",
    "MEMORY_RECALL_OPEN",
    "build_cli_memory_recall_block",
]
