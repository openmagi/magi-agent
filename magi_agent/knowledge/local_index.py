"""Read-only scan + keyword search over the workspace knowledge directory.

The local self-host runtime keeps its first-party knowledge base on disk under
``<workspace>/knowledge/`` (and the alternate ``<workspace>/.magi/knowledge/``).
Each immediate subdirectory is a *collection*; the files within it are the
documents.  The dashboard already exposes this tree read-only over
``GET /v1/app/knowledge`` in the transport layer; this module gives the native
``KnowledgeSearch`` tool the SAME view so the agent can actually query the
install-directory KB instead of receiving a canned placeholder.

The search is deliberately dependency-free (substring/keyword scan), mirroring
the transport endpoint's ``_search_files`` behaviour.  An optional ``qmd``-backed
index over ``knowledge/`` is a separate follow-up (it parallels the per-workspace
``memory/`` qmd backend, which is hard-wired to the ``memory/`` subtree today).

Everything here is pure and fail-soft: unreadable files are skipped, never
raised, so a malformed document cannot wedge a turn.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

#: Workspace-relative directories that hold the first-party knowledge base.
KNOWLEDGE_DIR_NAMES: tuple[str, ...] = ("knowledge", ".magi/knowledge")

#: Cap how much of a document is read for matching so a huge file cannot wedge
#: the scan (matches the transport endpoint's search budget intent).
_MAX_READ_BYTES = 256_000

#: Characters of surrounding context returned in a snippet.
_SNIPPET_CHARS = 320

#: Leading context kept before the match inside the snippet window.
_SNIPPET_LEAD = 80

#: Extensions that are safe to read as text for keyword matching.
_TEXT_EXTENSIONS = frozenset(
    {
        "md",
        "markdown",
        "txt",
        "csv",
        "tsv",
        "json",
        "yaml",
        "yml",
        "log",
        "xml",
        "html",
        "htm",
        "rst",
        "toml",
        "ini",
        "cfg",
        "conf",
    }
)


def _is_text_file(path: Path) -> bool:
    suffix = path.suffix.lower().lstrip(".")
    return suffix in _TEXT_EXTENSIONS


def _read_text(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(_MAX_READ_BYTES)
    except OSError:
        return None


def _snippet(text: str, anchor_lower: str) -> str:
    idx = text.lower().find(anchor_lower)
    if idx < 0:
        return text[:_SNIPPET_CHARS].strip()
    start = max(0, idx - _SNIPPET_LEAD)
    return text[start : start + _SNIPPET_CHARS].strip()


def _iter_documents(roots: Sequence[Path]) -> list[tuple[str, Path]]:
    """Yield ``(collection_name, file_path)`` for every KB document under roots.

    Deduplicates by workspace-relative path so overlapping roots (primary plus
    a hosted legacy fallback) do not surface the same file twice.
    """
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for root in roots:
        for dir_name in KNOWLEDGE_DIR_NAMES:
            base = root / dir_name
            if not base.is_dir():
                continue
            for coll_dir in sorted(p for p in base.iterdir() if p.is_dir()):
                for path in sorted(coll_dir.rglob("*")):
                    if not path.is_file():
                        continue
                    try:
                        rel = path.relative_to(root).as_posix()
                    except ValueError:
                        continue
                    if rel in seen:
                        continue
                    seen.add(rel)
                    out.append((coll_dir.name, path))
    return out


def search_local_knowledge(
    roots: Sequence[Path],
    query: str,
    *,
    limit: int = 5,
) -> list[dict[str, object]]:
    """Keyword-search the workspace knowledge dirs, newest matches first.

    Returns provider-shaped records consumable by ``KnowledgeBoundary`` /
    ``_LocalKnowledgeProvider``: ``sourceRef`` / ``title`` / ``snippet`` plus
    ``metadata`` marking the record public-safe (it is the operator's own
    on-disk KB, not external web content).  Returns ``[]`` when the query is
    blank, no KB dirs exist, or nothing matches.
    """
    tokens = [tok for tok in query.strip().lower().split() if tok]
    if not tokens or limit <= 0:
        return []

    matches: list[tuple[float, dict[str, object]]] = []
    for collection, path in _iter_documents(roots):
        if not _is_text_file(path):
            continue
        text = _read_text(path)
        if text is None:
            continue
        lowered = text.lower()
        # Keyword AND-match: every query token must appear in the document.
        if not all(tok in lowered for tok in tokens):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        # Use the source path so the agent can follow up with a file read.
        rel = path.name
        for root in roots:
            try:
                rel = path.relative_to(root).as_posix()
                break
            except ValueError:
                continue
        snippet = _snippet(text, tokens[0])
        matches.append(
            (
                mtime,
                {
                    "sourceRef": f"knowledge:{rel}",
                    # The provider boundary opacifies slashed source refs but
                    # surfaces ``title`` and ``publicPreview`` verbatim for
                    # public-safe records, so carry the locator in the title and
                    # the matched context in the public preview.
                    "title": rel,
                    "publicPreview": snippet,
                    "snippet": snippet,
                    "content": snippet,
                    "metadata": {
                        "collection": collection,
                        "path": rel,
                        "visibility": "public-safe",
                        "publicSafe": True,
                    },
                },
            )
        )

    matches.sort(key=lambda item: item[0], reverse=True)
    return [record for _mtime, record in matches[:limit]]


__all__ = ["KNOWLEDGE_DIR_NAMES", "search_local_knowledge"]
