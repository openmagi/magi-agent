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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

logger = logging.getLogger(__name__)

# A2: the recall fence carries ``continuity="background"`` so recalled long-term
# memory is presented to the model as BACKGROUND reference material, not as the
# user's current request / a conversation turn (mirrors the snapshot-path
# continuity policy in ``memory/continuity_policy.py``).
MEMORY_RECALL_OPEN = '<memory-recall hidden="true" continuity="background">'
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


def rerank_hits(
    *,
    hits: object,
    query: str,
    memory_dir: "Path",
    config: object,
    env: "Mapping[str, str] | None" = None,
) -> object:
    """Lazy seam for the optional cheap-model re-rank (PR3).

    Wrapped (not a top-level import) so the cold-start prompt path does not pull
    in the LiteLlm builder / re-rank machinery until recall runs with BOTH the
    recall gates AND ``MAGI_MEMORY_RECALL_RERANK_ENABLED`` on.  Default-OFF and
    fail-open inside the callee: when the rerank flag is off (or anything fails)
    it returns the BM25 order UNCHANGED, so the block stays byte-identical to the
    pre-PR3 output.  Tests monkeypatch this attribute to inject a fake selector.

    ``env`` is an optional injectable environment forwarded to the gate read
    (``MAGI_MEMORY_RECALL_RERANK_ENABLED``); default ``None`` keeps every existing
    caller byte-identical (the callee falls back to ``os.environ``). Design: WS2
    PR2c (hermetic ON-path).
    """
    from magi_agent.cli.memory_recall_rerank import (  # noqa: PLC0415
        rerank_hits as _rerank,
    )

    return _rerank(
        hits=hits, query=query, memory_dir=memory_dir, config=config, env=env
    )


def build_cli_memory_recall_block(
    *,
    workspace_root: str | None,
    query: str,
    memory_mode: str,
    env: "Mapping[str, str] | None" = None,
    projection_text: str | None = None,
) -> str:
    """Return a fenced ``<memory-recall>`` block for ``query``, or ``""``.

    All early-exit conditions (no workspace, empty query, incognito, gate off,
    no hits, any error) return ``""``; the function never raises.

    Args:
        workspace_root: Absolute workspace root, or ``None`` (bare CLI → "").
        query: The current user message; used as the search query.
        memory_mode: ``"normal"`` | ``"read_only"`` | ``"incognito"``.
            Incognito suppresses recall.
        env: Optional injectable environment threaded into the rerank/staleness
            gate reads (``MAGI_MEMORY_RECALL_RERANK_ENABLED``). Default ``None``
            -> ``os.environ``, so existing callers stay byte-identical. Design:
            WS2 PR2c (hermetic ON-path / SC-9).
        projection_text: Optional assembled memory snapshot block (the COMBINED
            projection + learning-recall block, per ``tool_runtime``). When
            provided, recall hits whose content already appears in it are omitted
            so the same memory is never injected twice. Default ``None`` -> no
            dedup (byte-identical to existing callers). Design: WS2 PR2c, finding
            14.
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
            env=env,
            projection_text=projection_text,
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
    env: "Mapping[str, str] | None" = None,
    projection_text: str | None = None,
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

    # PR3: optional cheap-model semantic re-rank over the BM25 candidates.
    # Default-OFF + fail-open inside the callee: when the rerank gate is off (or
    # anything fails) this returns the SAME BM25 order, so the emitted block is
    # byte-identical to the pre-PR3 output.  Never drops a candidate.
    memory_dir = root / "memory"
    try:
        hits = rerank_hits(
            hits=hits, query=query, memory_dir=memory_dir, config=config, env=env
        )
    except Exception:  # noqa: BLE001 — re-rank must never break recall
        logger.debug("Memory recall re-rank seam failed; using BM25 order", exc_info=True)

    # WS2 PR2c (finding 14): drop any recalled hit whose content already appears
    # in the assembled snapshot block (projection + learning-recall) the caller
    # passes.  Identity when ``projection_text`` is None/empty (fail-open: never
    # silently drops everything).  Strictly more dedup; never removes distinct
    # content.  Done AFTER rerank so the kept order reflects the final ranking.
    hits = _dedup_against_projection(hits, projection_text)

    # PR3: stale-pick markers — entries older than one day get a trailing
    # <system-reminder> staleness note appended INSIDE their part.  Built only
    # when rerank is active (it surfaces older docs the model chose); off-path
    # leaves ``stale_paths`` empty so the output is unchanged.
    stale_paths = _stale_recall_paths(memory_dir, env=env)

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
        if path in stale_paths:
            part = f"{part}\n{_staleness_note()}"
        parts.append(part)
        remaining = max(remaining - len(part.encode("utf-8")) - 2, 0)

    if not parts:
        return ""

    combined = _slice_utf8("\n\n".join(parts), content_budget)
    if not combined.strip():
        return ""
    return f"{MEMORY_RECALL_OPEN}\n{combined}\n{MEMORY_RECALL_CLOSE}"


# Minimum collapsed-content length before a recall hit may be deduped against
# the projection, so a pathologically short (one-token) memory doc is not
# coincidentally dropped because its single word appears in an unrelated
# projection line.
_DEDUP_MIN_MATCH_CHARS = 24


def _dedup_against_projection(hits: object, projection_text: str | None) -> list:
    """Drop hits whose content already appears in ``projection_text`` (WS2 PR2c).

    Pure + side-effect-free: returns a new list. Identity (the full input order,
    materialised to a list) when ``projection_text`` is None/empty so the caller
    can never silently drop everything (fail-open). Comparison is on COLLAPSED
    whitespace of the rendered hit content vs the rendered snapshot block text
    only (no reach into projection internals), so re-wrapping does not cause a
    false-negative. A hit whose content normalises to empty is never matched (it
    is left for the downstream empty-content filter, not dropped here).

    Design: WS2 memory-continuity design, section "PR2c" / finding 14 (dedup
    against the FULL combined snapshot is strictly more dedup and never removes
    distinct content).
    """
    ordered = list(hits)
    if not projection_text or not projection_text.strip():
        return ordered
    haystack = " ".join(projection_text.split())
    kept: list = []
    for hit in ordered:
        content = getattr(hit, "content", None)
        if isinstance(content, str):
            needle = " ".join(content.split())
            if len(needle) >= _DEDUP_MIN_MATCH_CHARS and needle in haystack:
                continue
        kept.append(hit)
    return kept


def _staleness_note() -> str:
    """The trailing staleness reminder appended to a stale recall pick (PR3)."""
    return (
        "<system-reminder>This recalled memory is stale "
        "(older than 1 day); verify it still holds before relying on it."
        "</system-reminder>"
    )


def _stale_recall_paths(
    memory_dir: "Path",
    env: "Mapping[str, str] | None" = None,
) -> set[str]:
    """Return the set of WORKSPACE-relative hit paths that are stale (>1 day).

    Only computed when the re-rank gate is ON, so the default recall path pays no
    manifest-scan cost and its output stays byte-identical to pre-PR3.  Manifest
    paths are relative to ``memory/`` (e.g. ``daily/x.md``); BM25 hit paths are
    workspace-relative (``memory/daily/x.md``), so each stale entry is re-prefixed
    to match.  Fail-soft: any error returns an empty set (no notes appended).

    ``env`` is the optional injectable environment for the gate read (WS2 PR2c,
    the previously-unenumerated FIFTH gate). Default ``None`` -> ``os.environ``,
    so existing callers stay byte-identical; an ON-path test injects ``env`` so
    the gate honours the threaded env rather than a developer-exported
    ``MAGI_MEMORY_RECALL_RERANK_ENABLED`` (SC-9 hermeticity).
    """
    try:
        from magi_agent.cli.memory_recall_rerank import (  # noqa: PLC0415
            _rerank_gate_open,
        )

        if not _rerank_gate_open(env):
            return set()
        from magi_agent.cli.memory_manifest import (  # noqa: PLC0415
            build_memory_manifest,
        )

        stale: set[str] = set()
        for entry in build_memory_manifest(memory_dir):
            if entry.stale:
                stale.add(f"memory/{entry.path}")
        return stale
    except Exception:  # noqa: BLE001 — staleness notes are best-effort
        return set()


__all__ = [
    "MEMORY_RECALL_CLOSE",
    "MEMORY_RECALL_OPEN",
    "build_cli_memory_recall_block",
    "rerank_hits",
]
