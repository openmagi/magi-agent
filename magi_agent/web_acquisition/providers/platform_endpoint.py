"""Platform-endpoint live provider for web search, fetch, and reader.

Calls the hosted platform's own API proxy endpoints:

  POST /v1/search  — Serper (primary) / Brave (secondary) backend
  POST /v1/fetch   — Firecrawl fetch
  POST /v1/scrape  — Firecrawl scrape (full markdown, used as "reader")

Auth: ``Authorization: Bearer <api_key>`` where ``api_key`` is
``MAGI_PLATFORM_API_KEY`` — the agent's own platform API key, not a raw
third-party key.

SSRF defence (defence-in-depth):
  1. ``url_policy_error`` already fires before this provider is called
     (in ``_validate_live_request`` inside ``LiveWebAcquisitionProviderPack``).
  2. This provider adds a DNS-rebinding guard: it resolves the target
     hostname before the first real ``httpx`` call and re-checks the resolved IP
     against the private / loopback / link-local / metadata blocklist.
     (See ``_check_dns_rebinding``.)

Note on sync vs async:
  ``LiveWebAcquisitionProviderPack.run()`` is synchronous and rejects awaitable
  provider output with ``"async_provider_not_supported"`` (see
  ``live_provider_pack.py:452-456``).  This provider therefore uses
  ``httpx.Client`` (sync).  Async support would require ``LiveWebAcquisitionProviderPack``
  to be made async — a separate PR.
"""

from __future__ import annotations

import socket
from collections.abc import Mapping
from ipaddress import ip_address
from typing import Literal

from magi_agent.web_acquisition.policy import url_policy_error


_METADATA_IPS: frozenset[str] = frozenset({"169.254.169.254"})
_CGNAT_PREFIX = "100."  # rough pre-filter before ip_address() parsing


def _check_dns_rebinding(hostname: str) -> str | None:
    """Resolve *hostname* and check every returned IP against the blocklist.

    Returns an error string if any IP is private/loopback/link-local/metadata,
    or ``None`` if all IPs are safe public addresses.

    This is a best-effort guard — not a substitute for server-side controls.
    The DNS lookup is synchronous and uses the system resolver.  It can fail
    (NXDOMAIN, timeout) in which case the error is treated as a transient
    provider failure so the router can fall back.
    """
    try:
        results = socket.getaddrinfo(hostname, None)
    except OSError:
        # DNS failure — treat as transient (caller maps to {"status": "timeout"}).
        return "dns_resolution_failed"

    for _family, _type, _proto, _canon, sockaddr in results:
        raw_ip = sockaddr[0]
        try:
            parsed = ip_address(raw_ip)
        except ValueError:
            continue
        ip_str = str(parsed)
        if ip_str in _METADATA_IPS:
            return "dns_rebinding_metadata_blocked"
        if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
            return "dns_rebinding_local_blocked"
        if parsed.is_private or parsed.is_reserved or parsed.is_multicast:
            return "dns_rebinding_private_blocked"
        if not parsed.is_global:
            return "dns_rebinding_non_global_blocked"
    return None


class PlatformEndpointProvider:
    """Live provider backed by the hosted platform's API proxy.

    Parameters
    ----------
    base_url:
        Base URL of the platform proxy, e.g. ``https://api.openmagi.ai``.
        No trailing slash.
    api_key:
        Bearer token sent in ``Authorization: Bearer <api_key>``.
    timeout_s:
        ``httpx`` request timeout in seconds.  Defaults to 30.
    skip_dns_check:
        Internal flag for unit tests that use an ``httpx`` mock and therefore
        do not need real DNS resolution.  **Never set to ``True`` in production.**
    """

    openmagi_live_provider: Literal[True] = True

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_s: float = 30.0,
        skip_dns_check: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._skip_dns_check = skip_dns_check

    # ------------------------------------------------------------------
    # Provider protocol methods
    # ------------------------------------------------------------------

    def search(self, request: object) -> Mapping[str, object]:
        """POST /v1/search → normalised ``{"results": [...]}``."""
        import httpx

        query = _get_str(request, "query")
        if not query:
            return {"status": "denied"}
        max_results = _get_int(request, "metadata", "maxResults") or 5
        # Send both contract shapes: ``query``/``count`` (platform api-proxy Brave
        # endpoint reads ``body.query``) and ``q``/``num`` (Serper-style backends).
        # Sending only ``q`` makes the deployed /v1/search 400 "Missing query field".
        body: dict[str, object] = {
            "query": query,
            "q": query,
            "count": max_results,
            "num": max_results,
        }
        try:
            resp = self._client().post(f"{self._base_url}/v1/search", json=body)
        except httpx.TimeoutException:
            return {"status": "timeout"}
        except Exception:
            return {"status": "timeout"}
        return self._handle_response(resp, "search")

    def fetch(self, request: object) -> Mapping[str, object]:
        """POST /v1/fetch → normalised ``{"url": ..., "content": ...}``."""
        import httpx

        url = _get_str(request, "url")
        if not url:
            return {"status": "denied"}
        rebind_err = self._dns_check(url)
        if rebind_err is not None:
            return {"status": "denied" if rebind_err != "dns_resolution_failed" else "timeout"}
        body: dict[str, object] = {"url": url}
        try:
            resp = self._client().post(f"{self._base_url}/v1/fetch", json=body)
        except httpx.TimeoutException:
            return {"status": "timeout"}
        except Exception:
            return {"status": "timeout"}
        return self._handle_response(resp, "fetch")

    def reader(self, request: object) -> Mapping[str, object]:
        """POST /v1/scrape → normalised ``{"url": ..., "content": ...}``."""
        import httpx

        url = _get_str(request, "url")
        if not url:
            return {"status": "denied"}
        rebind_err = self._dns_check(url)
        if rebind_err is not None:
            return {"status": "denied" if rebind_err != "dns_resolution_failed" else "timeout"}
        body: dict[str, object] = {"url": url, "formats": ["markdown"]}
        try:
            resp = self._client().post(f"{self._base_url}/v1/scrape", json=body)
        except httpx.TimeoutException:
            return {"status": "timeout"}
        except Exception:
            return {"status": "timeout"}
        return self._handle_response(resp, "reader")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _client(self) -> object:
        """Return a fresh ``httpx.Client`` for this request.

        A new client is created per-call (not shared) to avoid holding open
        connections longer than necessary and to keep state isolation simple.
        ``httpx.Client`` is used (sync) because ``LiveWebAcquisitionProviderPack``
        rejects async provider output.
        """
        import httpx

        return httpx.Client(
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout_s,
        )

    def _dns_check(self, url: str) -> str | None:
        """Return a rebinding error code, or ``None`` if the URL is safe.

        Skipped when ``self._skip_dns_check`` is ``True`` (tests only).
        """
        if self._skip_dns_check:
            return None
        try:
            from urllib.parse import urlsplit

            hostname = (urlsplit(url).hostname or "").strip()
        except Exception:
            return "dns_resolution_failed"
        if not hostname:
            return "dns_resolution_failed"
        return _check_dns_rebinding(hostname)

    def _handle_response(self, resp: object, operation: str) -> Mapping[str, object]:
        """Normalise an ``httpx.Response`` to the envelope that ``_records_from_output`` expects.

        Status codes:
          2xx → parse body and normalise.
          401 / 403 → ``{"status": "denied"}``.
          429 → ``{"status": "timeout"}`` (rate limit; retryable).
          Other 4xx / 5xx → ``{"status": "timeout"}`` (transient).

        Fail-soft: if *resp* is not a recognised response object (e.g. a test
        double that lacks ``.status_code``), returns ``{"status": "timeout"}``
        rather than raising.
        """
        raw_status = getattr(resp, "status_code", None)
        if raw_status is None:
            return {"status": "timeout"}
        try:
            status_code: int = int(raw_status)
        except (TypeError, ValueError):
            return {"status": "timeout"}
        if status_code in {401, 403}:
            return {"status": "denied"}
        if status_code == 429 or status_code >= 500:
            return {"status": "timeout"}
        if status_code < 200 or status_code >= 300:
            # Non-2xx, non-429, non-5xx: treat as transient.
            return {"status": "timeout"}

        try:
            data = resp.json()
        except Exception:
            return {"status": "timeout"}

        if not isinstance(data, dict):
            return {"status": "timeout"}

        return _normalise_response(data, operation)


# ------------------------------------------------------------------
# Response normalisation
# ------------------------------------------------------------------


def _normalise_response(data: dict[str, object], operation: str) -> Mapping[str, object]:
    """Map platform response shapes to the envelope ``_records_from_output`` consumes.

    /v1/search response:
        ``{"results": [{"url":..., "title":..., "snippet":...}]}``

    /v1/fetch response:
        ``{"url":..., "title":..., "content":..., "status": 200}``

    /v1/scrape response:
        ``{"url":..., "markdown":..., "title":..., "statusCode": 200}``
    """
    if operation == "search":
        results = data.get("results")
        if not isinstance(results, list):
            # Brave-style payload (what the platform api-proxy returns): the
            # results live under ``web.results`` rather than the top level.
            web = data.get("web")
            if isinstance(web, dict) and isinstance(web.get("results"), list):
                results = web["results"]
            else:
                return {"results": []}
        normalised = []
        for item in results:
            if not isinstance(item, dict):
                continue
            normalised.append(
                {
                    # Serper uses "url"/"link"; Brave uses "url".
                    "url": _str_or_none(item.get("url"))
                    or _str_or_none(item.get("link"))
                    or "",
                    "title": _str_or_none(item.get("title")),
                    # Serper="snippet", Brave="description"; content/body as fallback.
                    "snippet": _str_or_none(item.get("snippet"))
                    or _str_or_none(item.get("description"))
                    or _str_or_none(item.get("content"))
                    or _str_or_none(item.get("body"))
                    or "",
                }
            )
        return {"results": normalised}

    if operation == "reader":
        # /v1/scrape: map "markdown" field to "content".
        return {
            "url": _str_or_none(data.get("url")) or "",
            "title": _str_or_none(data.get("title")),
            "content": _str_or_none(data.get("markdown"))
            or _str_or_none(data.get("content"))
            or "",
        }

    # fetch: pass through the expected fields.
    return {
        "url": _str_or_none(data.get("url")) or "",
        "title": _str_or_none(data.get("title")),
        "content": _str_or_none(data.get("content"))
        or _str_or_none(data.get("body"))
        or "",
    }


# ------------------------------------------------------------------
# Attribute helpers (work on both pydantic models and plain dicts)
# ------------------------------------------------------------------


def _get_str(obj: object, *attrs: str) -> str | None:
    for attr in attrs:
        value = getattr(obj, attr, None) if not isinstance(obj, Mapping) else obj.get(attr)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _get_int(obj: object, *attrs: str) -> int | None:
    """Walk a chain of attribute names, stopping at the first non-None hit."""
    current: object = obj
    for attr in attrs:
        if isinstance(current, Mapping):
            current = current.get(attr)
        else:
            current = getattr(current, attr, None)
        if current is None:
            return None
    if isinstance(current, bool):
        return None
    if isinstance(current, int):
        return current
    return None


def _str_or_none(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


__all__ = ["PlatformEndpointProvider"]
