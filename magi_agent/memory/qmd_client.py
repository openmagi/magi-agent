"""Fail-open client for live qmd memory search.

This is an OPTIONAL, GATED capability used only by the read-only recall adapter
(:mod:`magi_agent.memory.adapters.hipocampus_readonly`).  It is a SEPARATE surface
from the shadow parity contracts (which pin ``hipocampus_qmd_live_called: False``).

Design constraints
------------------
- **Stdlib only.**  Uses :mod:`urllib.request` for HTTP so importing this module
  never pulls ``requests``/``httpx`` (the adapter import-boundary test forbids
  those).  The transport import is lazy inside ``_raw_query``.
- **Fail-open.**  ``query`` never raises into a turn: any failure (no endpoint,
  network error, malformed payload) returns ``[]``.  Recall then falls back to
  the pre-computed ``qmd_results.json`` path or simply yields no qmd records.
- **No bundled qmd.**  The OSS repo has no qmd server, so ``_raw_query`` only
  does anything when ``MAGI_QMD_ENDPOINT`` is set; otherwise it raises
  :class:`QmdUnavailable`.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlparse


QMD_ENDPOINT_ENV: str = "MAGI_QMD_ENDPOINT"


class QmdUnavailable(Exception):
    """Raised internally when a live qmd query cannot be performed.

    Callers should never see this propagate out of :meth:`QmdClient.query`,
    which catches it (and any other exception) and fails open by returning ``[]``.
    """


class QmdClient:
    """Minimal fail-open client over a live qmd search endpoint.

    Results are normalized to the same shape as ``qmd_results.json`` entries::

        {"path": str, "content": str, "score": float, "context": str}

    so the recall adapter can map them through its existing ``MemoryRecord``
    construction path without special-casing the source.
    """

    def __init__(self, *, endpoint: str | None = None, timeout_s: float = 5.0) -> None:
        self.endpoint = endpoint if endpoint is not None else os.environ.get(QMD_ENDPOINT_ENV)
        self.timeout_s = timeout_s

    def query(
        self,
        text: str,
        *,
        collection: str,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> list[dict]:
        """Query qmd live, returning normalized result dicts.

        Fail-open: returns ``[]`` on ANY failure (never raises into a turn).
        Results below ``min_score`` are filtered out.  Malformed entries
        (missing/typed-wrong ``path``/``content``/``score``) are dropped.
        """
        try:
            raw = self._raw_query(
                text,
                collection=collection,
                limit=limit,
                min_score=min_score,
            )
        except Exception:
            # Fail-open: any transport/parse/availability failure yields no records.
            return []

        results = raw.get("results") if isinstance(raw, dict) else None
        if not isinstance(results, list):
            return []

        normalized: list[dict] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            content = item.get("content")
            score = item.get("score")
            if not isinstance(path, str) or not isinstance(content, str):
                continue
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                continue
            if float(score) < min_score:
                continue
            context = item.get("context")
            normalized.append(
                {
                    "path": path,
                    "content": content,
                    "score": float(score),
                    "context": context if isinstance(context, str) else "",
                }
            )
        return normalized

    def _raw_query(
        self,
        text: str,
        *,
        collection: str,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> dict:
        """Perform the actual qmd call.

        If ``MAGI_QMD_ENDPOINT`` (or the constructor ``endpoint`` arg) is set,
        POST a small JSON request and parse ``{"results": [...]}``.  Otherwise
        raise :class:`QmdUnavailable` so :meth:`query` fails open.

        Uses stdlib ``urllib.request`` (lazy import) to avoid adding any new
        dependency and to keep the module import boundary network-library-free.
        """
        if not self.endpoint:
            raise QmdUnavailable("no qmd endpoint configured")

        # Defense-in-depth: restrict to http/https to prevent file:// or ftp://
        # reads via urllib when MAGI_QMD_ENDPOINT is misconfigured.
        scheme = urlparse(self.endpoint).scheme
        if scheme not in ("http", "https"):
            raise QmdUnavailable(
                f"qmd endpoint scheme {scheme!r} is not allowed; use http or https"
            )

        # Lazy import: keep module-load import boundary free of network libs.
        from urllib import error as urllib_error
        from urllib import request as urllib_request

        payload = json.dumps(
            {
                "query": text,
                "collection": collection,
                "limit": limit,
                "minScore": min_score,
            }
        ).encode("utf-8")
        http_request = urllib_request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(http_request, timeout=self.timeout_s) as response:
                body = response.read()
        except (urllib_error.URLError, OSError, ValueError) as exc:
            raise QmdUnavailable(f"qmd request failed: {exc}") from exc

        try:
            parsed = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise QmdUnavailable(f"qmd response not JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise QmdUnavailable("qmd response is not a JSON object")
        return parsed


__all__ = ["QmdClient", "QmdUnavailable", "QMD_ENDPOINT_ENV"]
