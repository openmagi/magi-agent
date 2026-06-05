"""Live (network-capable) web FETCH provider for the research harness.

This is the FIRST real provider that performs network egress. It is injected at
runtime into ``LiveWebAcquisitionProviderPack`` — which runs the SSRF firewall
(``url_policy_error``) and the live gate BEFORE calling ``fetch``. This module is
deliberately NOT imported by ``live_provider_pack.py`` or ``research_tools.py``
(those are sealed against network imports and verified by import-boundary tests);
the operator wires this provider in only when the live network gate is on.

Security posture beyond the pack's literal-URL firewall:

* DNS-rebinding egress guard — every resolved IP is re-checked against the same
  private/metadata/reserved/CGNAT classification as ``url_policy_error`` BEFORE
  any socket is opened (``resolve_and_check_host``). The classification lives in
  ``policy.is_blocked_ip`` so the two paths cannot drift.
* Size cap — Content-Length AND streamed bytes are bounded.
* Exception wrapping — httpx/network/timeout/DNS errors never escape ``fetch``;
  they return a structured ``{"status": "timeout"|"denied", ...}`` mapping the
  pack maps to ``repair_required`` / ``no_answer``.
* Metadata redaction — emitted metadata values are scrubbed of bare hostnames,
  URLs and secrets, and the raw final URL/host is never emitted.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
import socket
from typing import Final
from urllib.parse import urlsplit

import httpx
from markdownify import markdownify as _html_to_markdown

from magi_agent.web_acquisition.policy import (
    is_blocked_ip,
    redact_public_text,
    url_policy_error,
)


# Browser-like default UA (mirrors OpenCode webfetch primary attempt).
BROWSER_UA: Final[str] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# Honest UA used on Cloudflare cf-mitigated challenge retry (OpenCode webfetch.ts:79).
HONEST_UA: Final[str] = "opencode"
# Content negotiation: markdown/html/text (mirror OpenCode webfetch).
ACCEPT_HEADER: Final[str] = (
    "text/markdown,text/html;q=0.9,application/xhtml+xml;q=0.8,text/plain;q=0.7,*/*;q=0.5"
)

_DEFAULT_TIMEOUT_S: Final[float] = 30.0
_MAX_TIMEOUT_S: Final[float] = 120.0
_DEFAULT_MAX_CONTENT_BYTES: Final[int] = 5_000_000
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
# Bare hostname / URL fragments not already caught by policy's secret/URL scrubbers.
_BARE_HOST_RE = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}\b"
)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def resolve_and_check_host(host: str) -> str | None:
    """Resolve ``host`` and verify EVERY resolved IP is an allowed egress target.

    Returns a reason code string when the host is blocked (DNS failure or any
    resolved address classified private/metadata/reserved/CGNAT/loopback), or
    ``None`` when all resolved addresses are public. This is the DNS-rebinding
    egress guard and MUST be called before opening any socket. Network I/O
    (``getaddrinfo``) lives here, never in the pure ``policy`` module.
    """
    if not host:
        return "dns_no_host"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return "dns_resolution_failed"
    except OSError:
        return "dns_resolution_failed"
    resolved: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr and isinstance(sockaddr[0], str):
            resolved.append(sockaddr[0])
    if not resolved:
        return "dns_no_address"
    for ip in resolved:
        if is_blocked_ip(ip):
            return "dns_rebind_blocked_ip"
    return None


def redact_metadata_values(meta: Mapping[str, object]) -> dict[str, object]:
    """Scrub metadata VALUES of bare hostnames, URLs and secrets.

    ``safe_metadata`` only filters by KEY denylist — it does not scrub hostnames
    that appear inside values. We additionally strip URLs and bare hostnames so a
    final-URL host can never leak through provider-emitted metadata. Non-string
    scalar values pass through unchanged.
    """
    out: dict[str, object] = {}
    for key, value in meta.items():
        if isinstance(value, str):
            scrubbed = redact_public_text(value, max_chars=512)
            scrubbed = _URL_RE.sub("[redacted-url]", scrubbed)
            scrubbed = _BARE_HOST_RE.sub("[redacted-host]", scrubbed)
            out[str(key)] = scrubbed
        elif isinstance(value, bool | int | float) or value is None:
            out[str(key)] = value
        # drop everything else (lists/dicts) to avoid nested leakage
    return out


def _url_digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _denied(reason: str) -> dict[str, object]:
    return {"status": "denied", "reason": reason, "content": ""}


def _timeout(reason: str) -> dict[str, object]:
    return {"status": "timeout", "reason": reason, "content": ""}


def _extract_title(html: str, fallback: str) -> str:
    match = _TITLE_RE.search(html)
    if match:
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        if title:
            return redact_public_text(title, max_chars=256)
    return fallback


def _html_to_md(html: str) -> str:
    stripped = _SCRIPT_STYLE_RE.sub("", html)
    try:
        markdown = _html_to_markdown(stripped, strip=["script", "style"])
    except Exception:
        markdown = stripped
    return markdown.strip()


class LiveFetchProvider:
    """httpx-backed live fetch provider with SSRF/DNS-rebinding egress hardening.

    Injected into ``LiveWebAcquisitionProviderPack`` at runtime. Declares the
    trusted live marker; the pack's gate + firewall run before ``fetch``. Tests
    inject a mock httpx transport via ``client`` and monkeypatch ``getaddrinfo``.
    """

    openmagi_live_provider = True

    def __init__(
        self,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        max_content_bytes: int = _DEFAULT_MAX_CONTENT_BYTES,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout_s = max(0.1, min(float(timeout_s), _MAX_TIMEOUT_S))
        self.max_content_bytes = max(1, int(max_content_bytes))
        self._client = client
        self._owns_client = client is None

    def fetch(self, request: object) -> Mapping[str, object]:
        """Fetch a URL → markdown/text. NEVER raises; returns a structured mapping."""
        try:
            return self._fetch_inner(request)
        except httpx.TimeoutException:
            return _timeout("request_timeout")
        except httpx.TransportError:
            # ConnectError, ReadError, network/DNS-layer failures.
            return _timeout("transport_error")
        except httpx.HTTPError:
            return _denied("http_error")
        except Exception:
            # Absolute backstop — nothing escapes fetch().
            return _denied("unexpected_error")

    # -- internals -----------------------------------------------------------

    def _fetch_inner(self, request: object) -> Mapping[str, object]:
        url = getattr(request, "url", None)
        if not isinstance(url, str) or not url.strip():
            return _denied("url_required")
        # Defensive re-run of the literal-URL firewall (pack already ran it).
        policy_error = url_policy_error(url)
        if policy_error is not None:
            return _denied(policy_error)

        host = (urlsplit(url).hostname or "").strip()
        # DNS-rebinding egress guard BEFORE opening any socket.
        rebind_error = resolve_and_check_host(host)
        if rebind_error is not None:
            return _denied(rebind_error)

        client = self._client or httpx.Client(timeout=self.timeout_s, follow_redirects=True)
        try:
            response = self._get(client, url, ua=BROWSER_UA)
            # Cloudflare challenge → retry once with honest UA.
            if response.status_code == 403 and response.headers.get("cf-mitigated") == "challenge":
                response.close()
                response = self._get(client, url, ua=HONEST_UA)
            return self._build_output(url, response)
        finally:
            if self._owns_client:
                client.close()

    def _get(self, client: httpx.Client, url: str, *, ua: str) -> httpx.Response:
        headers = {"User-Agent": ua, "Accept": ACCEPT_HEADER}
        return client.request("GET", url, headers=headers, timeout=self.timeout_s)

    def _build_output(self, requested_url: str, response: httpx.Response) -> Mapping[str, object]:
        # Content-Length pre-check.
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_content_bytes:
                    return _denied("content_too_large")
            except ValueError:
                pass

        raw = response.content  # MockTransport delivers bytes synchronously.
        if len(raw) > self.max_content_bytes:
            return _denied("content_too_large")

        content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        text = raw.decode(response.encoding or "utf-8", errors="replace")
        final_url = str(response.url) if response.url else requested_url

        if "html" in content_type or (not content_type and "<html" in text.lower()):
            title = _extract_title(text, final_url)
            body = _html_to_md(text)
        else:
            title = final_url
            body = text

        content = redact_public_text(body)[: self.max_content_bytes]
        metadata = redact_metadata_values(
            {
                "contentType": content_type or "unknown",
                "statusCode": response.status_code,
                "finalUrlRef": f"url:{_url_digest(final_url)}",
            }
        )
        return {
            # Title intentionally not the raw host when HTML; for non-HTML we
            # fall back to the requested host digest ref to avoid leaking it.
            "url": requested_url,
            "title": title if "html" in content_type else f"url:{_url_digest(final_url)}",
            "content": content,
            "metadata": metadata,
        }


__all__ = [
    "ACCEPT_HEADER",
    "BROWSER_UA",
    "HONEST_UA",
    "LiveFetchProvider",
    "redact_metadata_values",
    "resolve_and_check_host",
]
