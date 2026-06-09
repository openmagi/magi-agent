"""Thin ``qmd`` CLI wrapper backend (PR2).

When the external ``qmd`` binary is on ``PATH`` and the operator prefers it
(``MemoryRuntimeConfig.prefer_qmd``), this backend shells out to it for BM25
keyword search instead of using the pure-Python fallback.  It mirrors the legacy
TS ``QmdManager`` invocation: index the workspace memory tree, then run a lexical
query and parse the JSON result rows (``path`` / ``content`` / ``score`` —
matching the static ``memory/qmd_results.json`` shape the read-only adapter
already understands).

Degrade-gracefully contract
---------------------------
This backend must NEVER crash the caller.  If ``qmd`` is missing, errors,
times out, or emits unparseable output, the offending operation returns ``[]``
(or is a no-op for :meth:`reindex`).  ``select_search_backend`` only chooses this
backend when ``shutil.which("qmd")`` already succeeded, but the methods stay
defensive anyway.

Subprocess safety: args are always passed as a list (never ``shell=True``), with
a bounded timeout, and ``stdin`` closed.  ``subprocess`` / ``shutil`` are
imported here and **only** here within the memory subsystem, keeping the
boundary-guarded modules (contracts, policy, adapters, harness/memory_*) free of
process/network imports.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .base import SearchCapabilities, SearchHit

#: Binary name resolved on PATH.
_QMD_BINARY = "qmd"

#: Hard cap so a hung/huge index can't wedge the caller.
_TIMEOUT_SECONDS = 30


class QmdBackend:
    """Search backend that shells out to the external ``qmd`` CLI.

    Construct, :meth:`reindex` (points qmd at the workspace memory tree), then
    :meth:`search`.  All methods are fail-soft: any failure yields an empty /
    no-op result rather than raising.
    """

    def __init__(self, *, binary: str = _QMD_BINARY, timeout: int = _TIMEOUT_SECONDS) -> None:
        self._binary = binary
        self._timeout = timeout
        self._root: Path | None = None

    @property
    def capabilities(self) -> SearchCapabilities:
        return SearchCapabilities(name="qmd", supports_vector=False)

    @property
    def available(self) -> bool:
        """True when the ``qmd`` binary is resolvable on PATH."""
        return shutil.which(self._binary) is not None

    def reindex(self, root: Path) -> None:
        # Remember the root so search() can scope the query, and ask qmd to
        # (re)build its index over the memory tree.  A failure here is non-fatal:
        # qmd may auto-index on query, or search() simply returns [].
        self._root = root
        self._run([self._binary, "index", str(root)])

    def search(self, query: str, *, k: int) -> list[SearchHit]:
        if k <= 0 or not query.strip():
            return []
        args = [self._binary, "query", "--type", "lex", "--json", "--limit", str(k)]
        if self._root is not None:
            args += ["--root", str(self._root)]
        args.append(query)
        completed = self._run(args)
        if completed is None or completed.returncode != 0:
            return []
        return self._parse_hits(completed.stdout, k=k)

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str] | None:
        if shutil.which(args[0]) is None:
            return None
        try:
            return subprocess.run(  # noqa: S603 - args is a fixed list, never shell
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    @staticmethod
    def _parse_hits(stdout: str, *, k: int) -> list[SearchHit]:
        if not stdout.strip():
            return []
        try:
            parsed = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return []
        # Accept either a bare list of rows or the {"results": [...]} envelope the
        # read-only adapter's static snapshot uses.
        if isinstance(parsed, dict):
            rows = parsed.get("results")
        else:
            rows = parsed
        if not isinstance(rows, list):
            return []

        hits: list[SearchHit] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            path = row.get("path")
            content = row.get("content")
            score = row.get("score")
            if not isinstance(path, str) or not isinstance(content, str):
                continue
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                continue
            hits.append(SearchHit(path=path, content=content, score=float(score)))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:k]


__all__ = ["QmdBackend"]
