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

Verified ``qmd`` contract (probed against qmd 2.x on this platform):
  * ``qmd collection add <path> --name <name>`` registers + BM25-indexes immediately
    ("Indexed: N new"). Vector embeddings are a *separate* ``qmd embed`` step, so
    BM25 search needs only ``add`` — we never call ``embed`` from this backend
    (the install-time ``magi memory init --vector`` step owns embedding generation).
  * ``qmd update [<name>]`` refreshes an existing collection (slow — it touches
    embeddings); used only as a best-effort refresh when the collection already
    exists, never on the hot search path.
  * ``qmd collection list`` prints ``<name> (qmd://<name>/)`` lines.
  * ``qmd search "<q>" --json`` → ``[{"docid","score","file","title","snippet"}]``
    spanning every collection; we keep only rows under our prefix. BM25 keyword,
    no model load (~1s) — the default for the per-turn recall hot path.
  * ``qmd vsearch "<q>" --json`` → SAME JSON shape, but pure vector similarity.
    Requires the collection to have been embedded (``qmd embed``). Each invocation
    cold-loads the embedding model (~10-40s), so this is OPT-IN and reserved for
    EXPLICIT, latency-tolerant search surfaces — never the per-turn hot path.

Vector mode (``vector=True``) is selected only by the explicit-search seam
(``select_search_backend(config, vector=True)`` with ``config.vector_search`` ON);
the per-turn recall callers construct this backend with ``vector=False`` so they
keep BM25's sub-second latency.

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

#: Vector search cold-loads the embedding model per invocation (~10-40s on a
#: warm disk, more on first run). Give the subprocess generous headroom so a
#: legitimate (slow) vector query is not killed by the BM25-tuned cap above.
_VECTOR_TIMEOUT_SECONDS = 90

#: Per-workspace collection name prefix.
_COLLECTION_PREFIX = "magi-mem-"


def collection_name_for(memory_dir: Path, *, prefix: str = _COLLECTION_PREFIX) -> str:
    """Deterministic, collision-free qmd collection name for an indexed directory.

    Keyed on the resolved absolute path so two bots whose dirs share the basename
    still get distinct collections. ``prefix`` lets non-memory subtrees (e.g. the
    workspace ``knowledge/`` KB) get their own namespace.
    """
    digest = hashlib.sha1(str(memory_dir.resolve()).encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:12]}"


def _memory_path_from_qmd_suffix(suffix: str, *, subdir: str = "memory") -> str | None:
    if suffix == "" or suffix.startswith("/"):
        return None
    parts = suffix.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return f"{subdir}/{suffix}"


class QmdBackend:
    """Search backend that shells out to the external ``qmd`` CLI.

    :meth:`reindex` registers/refreshes the workspace ``memory/`` tree as a
    per-workspace qmd collection; :meth:`search` runs a BM25 query and keeps only
    the rows belonging to that collection. All methods are fail-soft.
    """

    def __init__(
        self,
        *,
        binary: str = _QMD_BINARY,
        timeout: int | None = None,
        auto_register: bool = False,
        vector: bool = False,
        subdir: str = "memory",
        collection_prefix: str = _COLLECTION_PREFIX,
    ) -> None:
        self._binary = binary
        #: Workspace-relative subtree this backend indexes (``memory`` by default;
        #: e.g. ``knowledge`` for the first-party KB). Drives both the registered
        #: directory and the ``qmd://<name>/<rel>`` -> ``<subdir>/<rel>`` mapping.
        self._subdir = subdir
        self._collection_prefix = collection_prefix
        #: When ``vector`` is on, :meth:`search` runs ``qmd vsearch`` (pure vector
        #: similarity) instead of ``qmd search`` (BM25). It cold-loads the
        #: embedding model, so the timeout defaults higher unless overridden.
        self._vector = vector
        self._timeout = (
            timeout
            if timeout is not None
            else (_VECTOR_TIMEOUT_SECONDS if vector else _TIMEOUT_SECONDS)
        )
        #: Instance default for :meth:`reindex`'s ``allow_auto_register`` — set by
        #: ``select_search_backend`` from ``config.prefer_qmd_auto_register`` so
        #: callers can invoke the uniform ``reindex(root)`` protocol method and
        #: still honor the multi-tenant opt-in.
        self._auto_register = auto_register
        self._memory_dir: Path | None = None
        self._collection: str | None = None

    @property
    def capabilities(self) -> SearchCapabilities:
        return SearchCapabilities(name="qmd", supports_vector=self._vector)

    @property
    def available(self) -> bool:
        """True when the ``qmd`` binary is resolvable on PATH."""
        return shutil.which(self._binary) is not None

    @property
    def bound(self) -> bool:
        """True when :meth:`reindex` resolved an existing/registered collection.

        Lets callers distinguish "no collection, fall back to another backend"
        from "collection exists but matched nothing" (``search`` returns ``[]``
        in both cases).
        """
        return self._collection is not None

    def bind(self, root: Path) -> bool:
        """Bind to an ALREADY-registered collection for this root, without indexing.

        Unlike :meth:`reindex` (which may run a slow ``qmd update``/``add``), this
        only probes ``collection list`` and binds ``self._collection`` when the
        collection already exists, so :meth:`search` can run on the hot path
        without paying a refresh. Returns True when bound. Fail-soft.
        """
        self._collection = None
        target = root / self._subdir
        if not target.is_dir():
            return False
        resolved = target.resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            return False
        self._memory_dir = resolved
        name = collection_name_for(resolved, prefix=self._collection_prefix)
        if self._collection_exists(name):
            self._collection = name
            return True
        return False

    def reindex(self, root: Path, *, allow_auto_register: bool | None = None) -> None:
        """(Re)index the workspace ``{subdir}/`` tree (``memory`` by default).

        ``allow_auto_register`` gates the ONLY operation that mutates the user's
        GLOBAL qmd index — registering a brand-new collection via
        ``qmd collection add``.  Default False (multi-tenant safe): on a
        shared/multi-bot host, turning memory on must not silently pollute a
        global qmd index.  When the collection does NOT already exist and
        ``allow_auto_register`` is False, this is a no-op and :meth:`search` will
        simply return ``[]`` (fail-soft).  Refreshing an ALREADY-registered
        collection via ``update`` is always allowed (it touches only our own
        collection), regardless of the flag.

        ``allow_auto_register=None`` (the default) falls back to the instance
        default set at construction (``select_search_backend`` threads
        ``config.prefer_qmd_auto_register`` there).
        """
        if allow_auto_register is None:
            allow_auto_register = self._auto_register
        memory_dir = root / self._subdir
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
            # Do not register a workspace subtree symlink that points outside the
            # workspace; qmd would index that external tree before search-time
            # filtering can help.
            return
        self._memory_dir = memory_resolved
        collection = collection_name_for(memory_resolved, prefix=self._collection_prefix)
        if self._collection_exists(collection):
            # Already ours: best-effort refresh (slow; off the hot path). Bind
            # the collection so search() scopes to it.
            self._collection = collection
            self._run([self._binary, "update", collection])
            return
        if not allow_auto_register:
            # Multi-tenant safety: do NOT register a new global collection.
            # Leave _collection unbound so search() returns [] (fail-soft).
            return
        # Explicit opt-in: first registration also performs the BM25 index
        # (no embed needed). Bind the collection so search() scopes to it.
        self._collection = collection
        self._run(
            [self._binary, "collection", "add", str(memory_resolved), "--name", collection]
        )

    def search(self, query: str, *, k: int) -> list[SearchHit]:
        if k <= 0 or not query.strip() or self._collection is None:
            return []
        # ``vsearch`` = pure vector similarity (opt-in, slow, needs embeddings);
        # ``search`` = BM25 keyword (default, fast). Both emit the same JSON shape
        # spanning all collections, so ``_parse_hits`` scopes to ours either way.
        command = "vsearch" if self._vector else "search"
        completed = self._run([self._binary, command, query, "--json"])
        if completed is None or completed.returncode != 0:
            return []
        return self._parse_hits(
            completed.stdout, collection=self._collection, k=k, subdir=self._subdir
        )

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
    def _parse_hits(
        stdout: str, *, collection: str, k: int, subdir: str = "memory"
    ) -> list[SearchHit]:
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
            # qmd://<name>/<relpath> → workspace-root-relative "<subdir>/<relpath>".
            rel = file_uri[len(prefix):]
            path = _memory_path_from_qmd_suffix(rel, subdir=subdir)
            if path is None:
                continue
            hits.append(SearchHit(path=path, content=content, score=float(score)))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:k]


__all__ = ["QmdBackend", "collection_name_for"]
