"""Pure-Python Okapi BM25 backend (PR2) — the DEFAULT search backend.

Zero external dependencies (stdlib only) so a fresh install searches Hipocampus
memory with no ``qmd`` binary present.  The corpus is small (a personal
``memory/`` tree), so :meth:`PyBM25Backend.reindex` simply re-scans every file;
there is deliberately **no** persisted index (YAGNI).

Indexed corpus (recursive under the workspace ``memory/`` directory)::

    memory/**/*.md      # daily/, weekly/, monthly/, and any nested *.md
    MEMORY.md           # top-level digest, if present
    ROOT.md             # top-level root, if present

The two top-level files (``MEMORY.md`` / ``ROOT.md``) are included because the
Hipocampus protocol treats them as first-class memory; they live at the
workspace root rather than under ``memory/``.

Ranking: standard Okapi BM25 with ``k1=1.5`` and ``b=0.75``.  Tokenisation is
word-boundary based, lowercased, and Unicode-word aware (so Korean/CJK runs are
kept as tokens).  Documents that contain none of the query terms score 0 and are
excluded from results.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from .base import SearchCapabilities, SearchHit

# BM25 free parameters (textbook Okapi defaults).
_K1 = 1.5
_B = 0.75

#: Unicode-word tokeniser: keeps alphanumerics and CJK/Hangul runs as tokens.
_TOKEN_RE = re.compile(r"[\w가-힣]+", re.UNICODE)

#: File patterns indexed under the workspace memory tree, plus the two
#: top-level Hipocampus files.  Documented in the module docstring.
_MEMORY_DIR = "memory"
_TOPLEVEL_FILES = ("MEMORY.md", "ROOT.md")


def _tokenize(text: str) -> list[str]:
    return [match.group(0) for match in _TOKEN_RE.finditer(text.lower())]


def _resolve_existing(path: Path) -> Path | None:
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_allowed_memory_target(
    resolved_path: Path,
    *,
    resolved_root: Path,
    resolved_memory_dir: Path | None,
) -> bool:
    if not _is_relative_to(resolved_path, resolved_root):
        return False
    if resolved_memory_dir is not None and _is_relative_to(resolved_path, resolved_memory_dir):
        return True
    return resolved_path in {resolved_root / name for name in _TOPLEVEL_FILES}


class _Document:
    """One indexed file: its workspace-relative path, raw text, and token bag."""

    __slots__ = ("path", "content", "term_freqs", "length")

    def __init__(self, path: str, content: str, tokens: list[str]) -> None:
        self.path = path
        self.content = content
        self.term_freqs: Counter[str] = Counter(tokens)
        self.length = len(tokens)


class PyBM25Backend:
    """Pure-Python BM25 keyword search over the Hipocampus ``memory/`` tree.

    Construct, call :meth:`reindex` with the workspace root, then :meth:`search`.
    ``search`` before any successful ``reindex`` returns ``[]``.
    """

    def __init__(self) -> None:
        self._docs: list[_Document] = []
        self._doc_freqs: Counter[str] = Counter()
        self._avg_doc_len: float = 0.0
        # H-27: ``_index_signature`` keys the built index on
        # ``(resolved_root, max(mtime), file_count)``. A subsequent reindex
        # over an unchanged tree returns immediately without re-reading or
        # re-tokenising — ``hipocampus_readonly._local_search_results`` calls
        # ``reindex(...)`` before every search, so the cache turns N recall
        # turns over an unchanged corpus into 1 tokenisation pass + N stat
        # walks. The signature is None until the first successful reindex.
        self._index_signature: tuple[Path, float, int] | None = None

    @property
    def capabilities(self) -> SearchCapabilities:
        return SearchCapabilities(name="pybm25", supports_vector=False)

    def reindex(self, root: Path) -> None:
        signature = self._compute_signature(root)
        if signature is not None and signature == self._index_signature:
            # H-27 cache hit: tree unchanged since the last successful
            # reindex; the prior ``_docs``/``_doc_freqs``/``_avg_doc_len``
            # are still authoritative.
            return

        docs: list[_Document] = []
        for path in self._iter_memory_files(root):
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
            docs.append(_Document(rel, content, _tokenize(content)))

        doc_freqs: Counter[str] = Counter()
        for doc in docs:
            doc_freqs.update(doc.term_freqs.keys())

        total_len = sum(doc.length for doc in docs)
        self._docs = docs
        self._doc_freqs = doc_freqs
        self._avg_doc_len = (total_len / len(docs)) if docs else 0.0
        # Stamp the cache only after the build succeeds; if signature
        # computation returned ``None`` (root unresolvable) the cache
        # stays cleared so the next call retries.
        self._index_signature = signature

    def _compute_signature(self, root: Path) -> tuple[Path, float, int] | None:
        """Cheap one-pass walk that returns the cache key for ``root`` or
        ``None`` when the root cannot be resolved. Reuses the same
        whitelist-aware traversal as :meth:`_iter_memory_files` so the
        signature exactly tracks the set of files the build would index.
        """
        resolved_root = _resolve_existing(root)
        if resolved_root is None or not resolved_root.is_dir():
            return None
        max_mtime = 0.0
        count = 0
        for path in self._iter_memory_files(root):
            try:
                stat = path.stat()
            except OSError:
                continue
            count += 1
            if stat.st_mtime > max_mtime:
                max_mtime = stat.st_mtime
        return (resolved_root, max_mtime, count)

    def search(self, query: str, *, k: int) -> list[SearchHit]:
        if k <= 0 or not self._docs:
            return []
        query_terms = _tokenize(query)
        if not query_terms:
            return []

        # Pre-compute idf per unique query term.
        n = len(self._docs)
        idf: dict[str, float] = {}
        for term in set(query_terms):
            df = self._doc_freqs.get(term, 0)
            # Okapi BM25 idf with +1 inside the log so it stays non-negative even
            # for terms appearing in every document.
            idf[term] = math.log(1.0 + (n - df + 0.5) / (df + 0.5))

        scored: list[SearchHit] = []
        for doc in self._docs:
            score = self._score_doc(doc, query_terms, idf)
            if score > 0.0:
                scored.append(SearchHit(path=doc.path, content=doc.content, score=score))

        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:k]

    def _score_doc(
        self,
        doc: _Document,
        query_terms: list[str],
        idf: dict[str, float],
    ) -> float:
        if doc.length == 0 or self._avg_doc_len == 0.0:
            return 0.0
        score = 0.0
        len_norm = _K1 * (1.0 - _B + _B * (doc.length / self._avg_doc_len))
        for term in set(query_terms):
            tf = doc.term_freqs.get(term, 0)
            if tf == 0:
                continue
            score += idf[term] * (tf * (_K1 + 1.0)) / (tf + len_norm)
        return score

    @staticmethod
    def _iter_memory_files(root: Path):
        resolved_root = _resolve_existing(root)
        if resolved_root is None or not resolved_root.is_dir():
            return

        memory_dir = root / _MEMORY_DIR
        resolved_memory_dir = _resolve_existing(memory_dir)
        if (
            resolved_memory_dir is not None
            and resolved_memory_dir.is_dir()
            and _is_relative_to(resolved_memory_dir, resolved_root)
        ):
            for path in sorted(memory_dir.rglob("*.md")):
                resolved_path = _resolve_existing(path)
                if (
                    resolved_path is not None
                    and _is_allowed_memory_target(
                        resolved_path,
                        resolved_root=resolved_root,
                        resolved_memory_dir=resolved_memory_dir,
                    )
                    and resolved_path.is_file()
                ):
                    yield path
        for name in _TOPLEVEL_FILES:
            candidate = root / name
            resolved_candidate = _resolve_existing(candidate)
            if (
                resolved_candidate is not None
                and _is_allowed_memory_target(
                    resolved_candidate,
                    resolved_root=resolved_root,
                    resolved_memory_dir=resolved_memory_dir,
                )
                and resolved_candidate.is_file()
            ):
                yield candidate


__all__ = ["PyBM25Backend"]
