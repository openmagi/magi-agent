"""``qmd`` CLI search backend (PR2).

The external ``qmd`` binary ("Quick Markdown Search") is a *global, stateful*
personal search index: folders are registered as named collections
(``qmd collection add <dir> --name <name>``), indexed for BM25 on add, and
queried across all collections via ``qmd search "<q>" --json`` which returns rows
keyed by ``file`` = ``qmd://<collection>/<relpath>``.

To use it for a single workspace's memory tree without colliding with or polluting
the user's other collections, this backend registers the workspace ``memory/``
directory under a **deterministic per-workspace collection name**
(``magi-mem-<sha1(abspath)[:12]>``) and **client-side filters** search results to
that collection's ``qmd://<name>/`` prefix.

Verified ``qmd`` contract (probed against qmd 0.x on this platform):
  * ``qmd collection add <path> --name <name>`` registers + BM25-indexes immediately
    ("Indexed: N new"). Vector embeddings are a *separate* ``qmd embed`` step, so
    BM25 search needs only ``add`` — we never call ``embed``.
  * ``qmd update [<name>]`` refreshes an existing collection (slow — it touches
    embeddings); used only as a best-effort refresh when the collection already
    exists, never on the hot search path.
  * ``qmd collection list`` prints ``<name> (qmd://<name>/)`` lines.
  * ``qmd search "<q>" --json`` → ``[{"docid","score","file","title","snippet"}]``
    spanning every collection; we keep only rows under our prefix.

Degrade-gracefully contract: this backend must NEVER crash the caller. Missing
binary, non-zero exit, timeout, or unparseable output → ``[]`` (search) / no-op
(reindex). ``subprocess``/``shutil`` are imported here and **only** here in the
memory subsystem, keeping boundary-guarded modules process/network-free.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from .base import SearchCapabilities, SearchHit

#: Binary name resolved on PATH.
_QMD_BINARY = "qmd"

#: Hard cap so a hung/huge index operation can't wedge the caller.
_TIMEOUT_SECONDS = 30

#: Per-workspace collection name prefix.
_COLLECTION_PREFIX = "magi-mem-"


def collection_name_for(memory_dir: Path) -> str:
    """Deterministic, collision-free qmd collection name for a memory directory.

    Keyed on the resolved absolute path so two bots whose memory dirs share the
    basename ``memory`` still get distinct collections.
    """
    digest = hashlib.sha1(str(memory_dir.resolve()).encode("utf-8")).hexdigest()
    return f"{_COLLECTION_PREFIX}{digest[:12]}"


def _memory_path_from_qmd_suffix(suffix: str) -> str | None:
    if suffix == "" or suffix.startswith("/"):
        return None
    parts = suffix.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return f"memory/{suffix}"


class QmdBackend:
    """Search backend that shells out to the external ``qmd`` CLI.

    :meth:`reindex` registers/refreshes the workspace ``memory/`` tree as a
    per-workspace qmd collection; :meth:`search` runs a BM25 query and keeps only
    the rows belonging to that collection. All methods are fail-soft.
    """

    def __init__(self, *, binary: str = _QMD_BINARY, timeout: int = _TIMEOUT_SECONDS) -> None:
        self._binary = binary
        self._timeout = timeout
        self._memory_dir: Path | None = None
        self._collection: str | None = None

    @property
    def capabilities(self) -> SearchCapabilities:
        return SearchCapabilities(name="qmd", supports_vector=False)

    @property
    def available(self) -> bool:
        """True when the ``qmd`` binary is resolvable on PATH."""
        return shutil.which(self._binary) is not None

    def reindex(self, root: Path) -> None:
        memory_dir = root / "memory"
        self._memory_dir = memory_dir
        self._collection = None
        if not memory_dir.is_dir():
            # Nothing to index yet; search() will simply return [].
            return
        root_resolved = root.resolve()
        memory_resolved = memory_dir.resolve()
        try:
            memory_resolved.relative_to(root_resolved)
        except ValueError:
            # Do not register a workspace memory/ symlink that points outside the
            # workspace; qmd would index that external tree before search-time
            # filtering can help.
            return
        self._memory_dir = memory_resolved
        self._collection = collection_name_for(memory_resolved)
        if self._collection_exists(self._collection):
            # Best-effort refresh of changed files (slow; off the hot path).
            self._run([self._binary, "update", self._collection])
            return
        # First registration also performs the BM25 index (no embed needed).
        self._run(
            [self._binary, "collection", "add", str(memory_resolved), "--name", self._collection]
        )

    def search(self, query: str, *, k: int) -> list[SearchHit]:
        if k <= 0 or not query.strip() or self._collection is None:
            return []
        completed = self._run([self._binary, "search", query, "--json"])
        if completed is None or completed.returncode != 0:
            return []
        return self._parse_hits(completed.stdout, collection=self._collection, k=k)

    def _collection_exists(self, name: str) -> bool:
        completed = self._run([self._binary, "collection", "list"])
        if completed is None or completed.returncode != 0:
            return False
        return f"qmd://{name}/" in completed.stdout

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
    def _parse_hits(stdout: str, *, collection: str, k: int) -> list[SearchHit]:
        if not stdout.strip():
            return []
        try:
            parsed = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return []
        # qmd search emits a bare list; tolerate a {"results": [...]} envelope too.
        rows = parsed.get("results") if isinstance(parsed, dict) else parsed
        if not isinstance(rows, list):
            return []

        prefix = f"qmd://{collection}/"
        hits: list[SearchHit] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            file_uri = row.get("file")
            score = row.get("score")
            content = row.get("snippet")
            if not isinstance(file_uri, str) or not file_uri.startswith(prefix):
                continue
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                continue
            if not isinstance(content, str):
                content = ""
            # qmd://<name>/<relpath> → workspace-root-relative "memory/<relpath>".
            rel = file_uri[len(prefix):]
            path = _memory_path_from_qmd_suffix(rel)
            if path is None:
                continue
            hits.append(SearchHit(path=path, content=content, score=float(score)))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:k]


__all__ = ["QmdBackend", "collection_name_for"]
