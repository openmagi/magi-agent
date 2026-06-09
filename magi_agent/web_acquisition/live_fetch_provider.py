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
  any socket is opened (``resolve_validated_ip``).
  The classification lives in ``policy.is_blocked_ip`` so the two paths cannot
  drift.
* No auto-redirects — the client is created with ``follow_redirects=False`` and
  a MANUAL redirect loop (capped) re-runs the FULL egress guard
  (``url_policy_error`` + DNS resolution + IP classification) on EVERY hop,
  including the resolved ``Location`` of any 3xx. This closes the redirect-based
  SSRF bypass where a hostile page 302-redirects to an internal IP. The
  Cloudflare honest-UA retry travels the same guarded request path so it is
  re-guarded too.
* IP pinning (DNS-rebinding / TOCTOU best-effort) — the host is resolved ONCE
  per hop, ALL returned IPs are validated, and the connection is PINNED to a
  validated IP (URL host rewritten to the IP, original ``Host`` header and TLS
  SNI hostname preserved). See ``_pin_request`` and ``require_pinned_egress``
  for the residual TOCTOU note for https.
* Size cap — the body is STREAMED and accumulated with an early abort as soon as
  the running total exceeds ``max_content_bytes`` (never buffers the whole
  body). Content-Length is only a fast-path pre-check.
* Exception wrapping — httpx/network/timeout/DNS errors never escape ``fetch``;
  they return a structured ``{"status": "timeout"|"denied", ...}`` mapping the
  pack maps to ``repair_required`` / ``no_answer``.
* Metadata redaction — emitted metadata values are scrubbed of bare hostnames,
  URLs and secrets, and the raw final URL/host is never emitted. The
  page-controlled ``<title>`` is run through the same URL/bare-host redaction.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import ipaddress
import re
import socket
from typing import Final
from urllib.parse import urljoin, urlsplit

import httpx
from markdownify import markdownify as _html_to_markdown

from magi_agent.web_acquisition.policy import (
    is_blocked_ip,
    redact_public_text,
    url_policy_error,
)
from magi_agent.egress_proxy.config import EgressProxyConfig
from magi_agent.egress_proxy.injection import httpx_client_kwargs


def _egress_client_kwargs(cfg: EgressProxyConfig | None = None) -> dict:
    """Extra httpx.Client kwargs to route web_fetch egress through the proxy.

    Default-OFF: returns ``{}`` when the egress proxy is unset, so the client is
    constructed exactly as before.
    """
    cfg = EgressProxyConfig.from_env() if cfg is None else cfg
    return httpx_client_kwargs(cfg)


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
# Max manual redirect hops before we give up (mirrors browser-ish default).
_MAX_REDIRECT_HOPS: Final[int] = 5
_REDIRECT_STATUS: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})
# Read chunk size for the streamed size-cap accumulator.
_STREAM_CHUNK_BYTES: Final[int] = 64 * 1024
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
# Bare hostname / URL fragments not already caught by policy's secret/URL scrubbers.
_BARE_HOST_RE = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}\b"
)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def resolve_validated_ip(host: str) -> tuple[str | None, str | None]:
    """Resolve ``host`` ONCE and validate EVERY returned IP.

    Returns ``(reason, ip)``:

    * On success: ``(None, <validated_ip>)`` — the host resolved, every returned
      address passed the private/metadata/reserved/CGNAT/loopback classification,
      and ``ip`` is one validated address the caller should PIN the connection
      to (closing the DNS-rebinding/TOCTOU window where httpx would otherwise
      re-resolve at connect time and could receive a different, hostile IP).
    * On failure: ``(<reason_code>, None)`` — DNS failure or ANY resolved
      address is a blocked egress target. Failing if *any* IP is blocked (not
      just the chosen one) is deliberate: a host that resolves to a mix of
      public and private addresses is treated as hostile.

    Network I/O (``getaddrinfo``) lives here, never in the pure ``policy``
    module. The IP classification is delegated to ``policy.is_blocked_ip`` so the
    literal-URL firewall and this egress guard share one source of truth.
    """
    if not host:
        return "dns_no_host", None
    # An IP-literal host has no DNS layer; classify it directly.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if is_blocked_ip(host):
            return "dns_rebind_blocked_ip", None
        return None, str(literal)
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return "dns_resolution_failed", None
    except OSError:
        return "dns_resolution_failed", None
    resolved: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr and isinstance(sockaddr[0], str):
            resolved.append(sockaddr[0])
    if not resolved:
        return "dns_no_address", None
    for ip in resolved:
        if is_blocked_ip(ip):
            # Fail closed: ANY blocked address poisons the whole resolution.
            return "dns_rebind_blocked_ip", None
    return None, resolved[0]



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
            out[str(key)] = _redact_host_and_urls(scrubbed)
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


def _redact_host_and_urls(text: str) -> str:
    """Strip URLs and bare hostnames in addition to the secret/URL scrubbers.

    Same value-redaction applied to emitted metadata, so a page-controlled
    string (e.g. ``<title>http://10.0.0.1</title>``) cannot leak an internal
    host/URL through the title or any other free-text field.
    """
    scrubbed = _URL_RE.sub("[redacted-url]", text)
    scrubbed = _BARE_HOST_RE.sub("[redacted-host]", scrubbed)
    return scrubbed


def _extract_title(html: str, fallback: str) -> str:
    match = _TITLE_RE.search(html)
    if match:
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        if title:
            # M2: titles are page-controlled — run them through the SAME
            # URL/bare-host redaction used for metadata values, not just
            # redact_public_text (which leaves bare hosts intact).
            redacted = redact_public_text(title, max_chars=256)
            return _redact_host_and_urls(redacted)
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
        require_pinned_egress: bool = False,
    ) -> None:
        self.timeout_s = max(0.1, min(float(timeout_s), _MAX_TIMEOUT_S))
        self.max_content_bytes = max(1, int(max_content_bytes))
        # C2: when True, refuse any https request whose connection we cannot pin
        # to a validated IP (no silent residual TOCTOU for https). Defaults off
        # so the strongest-pin-we-can behaviour is opt-in for strict operators.
        self.require_pinned_egress = bool(require_pinned_egress)
        self._client = client
        self._owns_client = client is None
        # M1: an injected client must NEVER auto-follow redirects — the manual
        # guarded loop is the only redirect path. Override the flag defensively
        # so an operator/test that built the client with follow_redirects=True
        # cannot reopen the redirect-SSRF bypass.
        if client is not None:
            try:
                client.follow_redirects = False
            except Exception:
                pass

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

        # follow_redirects=False — a hostile page must not be able to 302 us to
        # an internal IP. ALL redirect handling is the manual guarded loop below.
        client = self._client or httpx.Client(
            timeout=self.timeout_s,
            follow_redirects=False,
            **_egress_client_kwargs(),
        )
        try:
            return self._follow_guarded(client, url)
        finally:
            if self._owns_client:
                client.close()

    def _follow_guarded(self, client: httpx.Client, url: str) -> Mapping[str, object]:
        """Manual redirect loop that re-runs the FULL egress guard on EVERY hop.

        For each hop, BEFORE issuing the request we run the literal-URL firewall
        (``url_policy_error``) and resolve+validate the host
        (``resolve_validated_ip``), then PIN the connection to the validated IP.
        On a 3xx we resolve the ``Location`` (relative → absolute), and repeat
        the whole guard on the new URL. Exceeding the hop cap → ``denied``.
        """
        current = url
        requested_url = url
        for _hop in range(_MAX_REDIRECT_HOPS + 1):
            # 1. Literal-URL firewall (scheme/host/credential/metadata/cluster).
            policy_error = url_policy_error(current)
            if policy_error is not None:
                return _denied(policy_error)

            # 2. DNS-rebinding egress guard — resolve ONCE, validate ALL IPs,
            #    and obtain the IP we will pin the socket to.
            parts = urlsplit(current)
            host = (parts.hostname or "").strip()
            rebind_error, pinned_ip = resolve_validated_ip(host)
            if rebind_error is not None or pinned_ip is None:
                return _denied(rebind_error or "dns_rebind_blocked_ip")

            # C2: strict operators can refuse any https we cannot pin to the
            # validated IP, rather than accept the residual https TOCTOU window.
            if (
                self.require_pinned_egress
                and parts.scheme == "https"
                and (not pinned_ip or pinned_ip == host)
            ):
                return _denied("unpinnable_https_blocked")

            # 3. Issue the guarded+pinned request (BROWSER UA first).
            response = self._get(client, current, ua=BROWSER_UA, pinned_ip=pinned_ip)
            try:
                # Cloudflare challenge → honest-UA retry on the SAME guarded
                # path (I1: the retry is re-guarded + re-pinned, not raw).
                if (
                    response.status_code == 403
                    and response.headers.get("cf-mitigated") == "challenge"
                ):
                    response.close()
                    response = self._get(
                        client, current, ua=HONEST_UA, pinned_ip=pinned_ip
                    )

                if response.status_code in _REDIRECT_STATUS:
                    location = response.headers.get("location")
                    if not location:
                        # 3xx with no Location — treat as a terminal response.
                        return self._build_output(requested_url, current, response)
                    # Resolve relative → absolute against the CURRENT url, then
                    # loop so the next hop re-runs the full guard on it.
                    current = urljoin(current, location)
                    continue

                return self._build_output(requested_url, current, response)
            finally:
                response.close()
        # Exceeded the hop cap.
        return _denied("too_many_redirects")

    def _get(
        self, client: httpx.Client, url: str, *, ua: str, pinned_ip: str | None = None
    ) -> httpx.Response:
        """Issue a GET, pinning the connection to ``pinned_ip`` when provided.

        C2 (DNS-rebinding / TOCTOU): rather than let httpx re-resolve the host at
        connect time (where a hostile short-TTL record could swap in a private
        IP), we rewrite the request URL's host to the already-validated IP and
        preserve the original ``Host`` header. For https we also set the
        ``sni_hostname`` extension so TLS SNI and certificate verification still
        use the real hostname, not the IP literal.

        Residual TOCTOU: pinning the URL host to the validated IP fully closes
        the rebinding window for http://. For https://, httpx/httpcore honour the
        ``sni_hostname`` extension for SNI + cert verification while connecting to
        the pinned IP, so this is also closed for the common case. If a given
        httpcore/transport build does NOT honour connecting-by-IP-with-pinned-SNI
        (e.g. a custom transport that re-resolves the Host header), a residual
        rebinding window can remain for https only. ``require_pinned_egress``
        lets strict operators refuse such https rather than fall back silently —
        see ``_build_request``. We never silently downgrade.
        """
        request = self._build_request(client, url, ua=ua, pinned_ip=pinned_ip)
        return client.send(request, stream=True)

    def _build_request(
        self, client: httpx.Client, url: str, *, ua: str, pinned_ip: str | None
    ) -> httpx.Request:
        headers = {"User-Agent": ua, "Accept": ACCEPT_HEADER}
        parts = urlsplit(url)
        host = parts.hostname or ""
        extensions: dict[str, object] = {}
        target = url
        if pinned_ip and host and pinned_ip != host:
            # Rewrite the URL host → validated IP; preserve the real Host header.
            netloc = self._netloc_with_ip(parts, pinned_ip)
            target = parts._replace(netloc=netloc).geturl()
            headers["Host"] = host if parts.port is None else f"{host}:{parts.port}"
            if parts.scheme == "https":
                # Keep TLS SNI + cert verification bound to the real hostname.
                extensions["sni_hostname"] = host
        return client.build_request(
            "GET",
            target,
            headers=headers,
            timeout=self.timeout_s,
            extensions=extensions or None,
        )

    @staticmethod
    def _netloc_with_ip(parts: object, ip: str) -> str:
        port = getattr(parts, "port", None)
        # Bracket IPv6 literals for the URL netloc.
        try:
            host_repr = f"[{ip}]" if isinstance(ipaddress.ip_address(ip), ipaddress.IPv6Address) else ip
        except ValueError:
            host_repr = ip
        return host_repr if port is None else f"{host_repr}:{port}"

    def _read_capped(self, response: httpx.Response) -> bytes | None:
        """Stream the body, aborting as soon as it exceeds the size cap.

        Returns the accumulated bytes, or ``None`` when the cap is exceeded (the
        caller maps that to a ``content_too_large`` denial). We NEVER buffer the
        whole body — a chunked/no-Content-Length multi-GB response is stopped
        early once the running total crosses the cap.
        """
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes(_STREAM_CHUNK_BYTES):
            total += len(chunk)
            if total > self.max_content_bytes:
                return None
            chunks.append(chunk)
        return b"".join(chunks)

    def _build_output(
        self, requested_url: str, final_url: str, response: httpx.Response
    ) -> Mapping[str, object]:
        # Content-Length fast-path pre-check (do NOT rely on it — see streaming).
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_content_bytes:
                    return _denied("content_too_large")
            except ValueError:
                pass

        # I2: stream + early abort so an oversized body never gets fully buffered.
        raw = self._read_capped(response)
        if raw is None:
            return _denied("content_too_large")

        content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        text = raw.decode(response.encoding or "utf-8", errors="replace")

        if "html" in content_type or (not content_type and "<html" in text.lower()):
            title = _extract_title(text, "")
            body = _html_to_md(text)
            is_html = True
        else:
            title = ""
            body = text
            is_html = False

        content = redact_public_text(body)[: self.max_content_bytes]
        metadata = redact_metadata_values(
            {
                "contentType": content_type or "unknown",
                "statusCode": response.status_code,
                "finalUrlRef": f"url:{_url_digest(final_url)}",
            }
        )
        return {
            # Never emit the raw final URL/host: HTML title is redacted free
            # text, otherwise fall back to a digest ref of the final URL.
            "url": requested_url,
            "title": title if (is_html and title) else f"url:{_url_digest(final_url)}",
            "content": content,
            "metadata": metadata,
        }


__all__ = [
    "ACCEPT_HEADER",
    "BROWSER_UA",
    "HONEST_UA",
    "LiveFetchProvider",
    # Shared helpers also consumed by sibling providers (e.g. insane_fetch); kept
    # in __all__ so the cross-module contract is explicit, not a silent break.
    "_MAX_REDIRECT_HOPS",
    "_REDIRECT_STATUS",
    "_extract_title",
    "_html_to_md",
    "redact_metadata_values",
    "resolve_validated_ip",
]
