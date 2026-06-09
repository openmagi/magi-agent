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

    @property
    def capabilities(self) -> SearchCapabilities:
        return SearchCapabilities(name="pybm25", supports_vector=False)

    def reindex(self, root: Path) -> None:
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
        memory_dir = root / _MEMORY_DIR
        if memory_dir.is_dir():
            for path in sorted(memory_dir.rglob("*.md")):
                if path.is_file():
                    yield path
        for name in _TOPLEVEL_FILES:
            candidate = root / name
            if candidate.is_file():
                yield candidate


__all__ = ["PyBM25Backend"]
