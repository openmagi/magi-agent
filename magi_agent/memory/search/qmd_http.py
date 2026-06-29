"""HTTP qmd search backend — talks to an external qmd endpoint (e.g. a per-pod
qmd sidecar) over HTTP instead of shelling out to a local ``qmd`` binary.

WHY THIS EXISTS
---------------
In a sidecar topology the runtime container has no ``qmd`` binary, so the CLI
:class:`~magi_agent.memory.search.qmd.QmdBackend` cannot reach qmd.  This backend
implements the same :class:`~magi_agent.memory.search.base.SearchBackend`
contract over HTTP, so the EXPLICIT vector-search surfaces (``magi memory search
--vector`` and the dashboard ``/v1/app/memory/search?vector=1``) can run semantic
search against the sidecar's warm embedding model.

SCOPE / GOVERNANCE
------------------
- Used ONLY by the explicit-vector caller path via ``select_search_backend(...,
  vector=True)`` when ``config.qmd_endpoint`` is set AND ``config.vector_search``
  is on.  The per-turn recall path is NOT routed here (it keeps its existing
  ``QmdClient`` HTTP tier) — see the memory subsystem design.
- ``reindex`` is a deliberate **no-op**: embeddings are owned and refreshed by the
  sidecar's own lifecycle, never driven per-query by the runtime.
- ``search`` POSTs to ``<endpoint>/vsearch`` (semantic) using the same JSON shape
  as :class:`~magi_agent.memory.qmd_client.QmdClient`
  (``{query,collection,limit,minScore}`` → ``{results:[{path,content,score}]}``).
- The sidecar owns a single fixed collection and ignores the inbound
  ``collection`` field, so this backend sends a stable placeholder.

Degrade-gracefully: this backend must NEVER crash the caller.  Missing/invalid
endpoint, network error, non-2xx, or unparseable output → ``[]``.  Uses stdlib
``urllib`` (lazy import) to add no dependency and keep the import boundary
network-library-free, mirroring :mod:`magi_agent.memory.qmd_client`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from .base import SearchCapabilities, SearchHit

logger = logging.getLogger(__name__)

#: Path appended to the configured endpoint for semantic (vector) search.
_VSEARCH_PATH = "vsearch"

#: Collection field sent to the sidecar.  The sidecar scopes to its own fixed
#: collection and ignores this, so the value is a stable, non-sensitive marker
#: (the per-workspace sha1 name would differ across containers — do not use it).
_COLLECTION_MARKER = "memory"

#: Default request timeout (s).  Generous: even a warm sidecar vsearch is slower
#: than BM25, and this is an explicit, latency-tolerant surface.
_TIMEOUT_SECONDS = 30.0


class QmdHttpBackend:
    """Search backend that queries an external qmd endpoint over HTTP.

    Construct with the resolved endpoint (``select_search_backend`` threads
    ``config.qmd_endpoint``).  :meth:`reindex` is a no-op; :meth:`search` POSTs to
    ``<endpoint>/vsearch``.  All methods are fail-soft.
    """

    def __init__(self, *, endpoint: str, timeout_s: float = _TIMEOUT_SECONDS) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._timeout_s = timeout_s

    @property
    def capabilities(self) -> SearchCapabilities:
        # This backend exists specifically to serve semantic search.
        return SearchCapabilities(name="qmd-http", supports_vector=True)

    def reindex(self, root: Path) -> None:
        """No-op: the sidecar owns index build/embed on its own lifecycle.

        The runtime must never drive a remote embed per query (it would fire on
        every explicit search).  ``root`` is accepted for protocol compatibility.
        """
        del root

    def search(self, query: str, *, k: int) -> list[SearchHit]:
        if k <= 0 or not query.strip():
            return []
        url = f"{self._endpoint}/{_VSEARCH_PATH}"
        scheme = urlparse(url).scheme
        if scheme not in ("http", "https"):
            logger.debug("qmd-http: endpoint scheme %r not allowed", scheme)
            return []

        # Lazy import: keep module-load import boundary free of network libs.
        from urllib import error as urllib_error  # noqa: PLC0415
        from urllib import request as urllib_request  # noqa: PLC0415

        payload = json.dumps(
            {
                "query": query,
                "collection": _COLLECTION_MARKER,
                "limit": int(k),
                "minScore": 0.0,
            }
        ).encode("utf-8")
        http_request = urllib_request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(http_request, timeout=self._timeout_s) as response:
                body = response.read()
        except (urllib_error.URLError, OSError, ValueError):
            logger.debug("qmd-http: request to %s failed", url, exc_info=True)
            return []

        return self._parse_hits(body, k=k)

    @staticmethod
    def _parse_hits(body: bytes, *, k: int) -> list[SearchHit]:
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return []
        if not isinstance(parsed, dict):
            return []
        rows = parsed.get("results")
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


__all__ = ["QmdHttpBackend"]
