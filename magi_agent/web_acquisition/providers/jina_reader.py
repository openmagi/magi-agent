"""Jina Reader live provider for the research harness.

Calls https://r.jina.ai/<target_url> to retrieve a clean markdown rendering of
the target page.  Delegates ALL HTTP egress to ``LiveFetchProvider`` so the
existing SSRF-hardened fetch machinery (DNS-rebinding guard, IP pinning, manual
redirect guard, streamed size cap, exception wrapping, metadata redaction) is
fully reused — this module does NOT reimplement any of it.

Security posture:
* SSRF pre-check — ``url_policy_error(target_url)`` fires on the TARGET url
  BEFORE building the jina endpoint, so internal/metadata/localhost targets are
  rejected without ever making a network call.
* Jina endpoint is formed by appending the full target URL to the reader base:
  ``https://r.jina.ai/<target_url>`` (e.g.
  ``https://r.jina.ai/https://example.com/page``).
* All HTTP egress goes through ``LiveFetchProvider.fetch()``, which re-runs the
  same SSRF firewall and DNS-rebinding guard on the jina endpoint itself.
* The ``"url"`` key in successful results is ALWAYS overridden to the original
  target URL (not the jina endpoint), so the caller sees the canonical source.

Default-OFF / import boundary:
  This module is safe to import at any time (no side-effects), but it is only
  LAZILY imported by the registration site (Task 3).  It must NOT be added to
  any package ``__init__`` imported by sealed modules at module level.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

import httpx

from magi_agent.web_acquisition.live_fetch_provider import LiveFetchProvider
from magi_agent.web_acquisition.policy import url_policy_error


_JINA_READER_BASE: str = "https://r.jina.ai/"


class JinaReaderProvider:
    """Live provider backed by the Jina Reader API (https://r.jina.ai).

    Parameters
    ----------
    api_key:
        Optional Jina API key.  When supplied, sets
        ``Authorization: Bearer <key>`` on every request.  The Jina free tier
        works without a key (lower rate limits apply).
    client:
        Injection seam for tests.  When provided, it is passed directly to the
        internal ``LiveFetchProvider`` so tests can use ``httpx.MockTransport``
        without network access.  When ``None`` a default httpx.Client is built
        with the auth/return-format headers and ``follow_redirects=False``.
    timeout_s:
        Request timeout in seconds (default 30, forwarded to
        ``LiveFetchProvider``).
    """

    openmagi_live_provider: Literal[True] = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._timeout_s = float(timeout_s)

        # Build a default client when none is injected (test seam).
        default_headers: dict[str, str] = {"X-Return-Format": "markdown"}
        if api_key:
            default_headers["Authorization"] = f"Bearer {api_key}"
        resolved_client = client if client is not None else httpx.Client(
            headers=default_headers,
            follow_redirects=False,
        )
        self._fetch_provider = LiveFetchProvider(
            client=resolved_client,
            timeout_s=self._timeout_s,
        )

    def reader(self, request: object) -> Mapping[str, object]:
        """Fetch ``request.url`` via Jina Reader → ``{"url", "title", "content", "metadata"}``.

        NEVER raises — all errors are returned as structured
        ``{"status": "denied"|"timeout", "reason": ..., "content": ""}`` mappings.
        """
        try:
            return self._reader_inner(request)
        except Exception:
            # Absolute backstop — nothing escapes reader().
            return {"status": "denied", "reason": "unexpected_error", "content": ""}

    # -- internals -----------------------------------------------------------

    def _reader_inner(self, request: object) -> Mapping[str, object]:
        target_url = getattr(request, "url", None)
        if not isinstance(target_url, str) or not target_url.strip():
            return {"status": "denied", "reason": "url_required", "content": ""}

        target_url = target_url.strip()

        # SSRF pre-check on the TARGET url before we ever build a jina request.
        policy_error = url_policy_error(target_url)
        if policy_error is not None:
            return {"status": "denied", "reason": policy_error, "content": ""}

        # Build the jina endpoint: https://r.jina.ai/<full-target-url>
        jina_endpoint = _JINA_READER_BASE + target_url

        # Wrap the jina endpoint in a minimal request duck-type for LiveFetchProvider.
        class _Req:
            url = jina_endpoint

        result = self._fetch_provider.fetch(_Req())

        # On error, pass through unchanged (status key present).
        if "status" in result:
            return result

        # On success, override "url" to the ORIGINAL target (not the jina endpoint).
        return {
            "url": target_url,
            "title": result.get("title", ""),
            "content": result.get("content", ""),
            "metadata": result.get("metadata", {}),
        }


__all__ = ["JinaReaderProvider"]
