"""WAF-bypass live FETCH provider backed by ``curl_cffi`` browser impersonation.

This is a sibling of ``LiveFetchProvider`` for targets that block plain httpx
egress with TLS/JA3 fingerprinting WAFs (Cloudflare, Akamai, PerimeterX, …).
Instead of the honest-UA dance, it impersonates a real browser's full TLS +
HTTP/2 fingerprint via ``curl_cffi.requests`` (``impersonate="chrome"``), which
*is* the bypass.

It deliberately does NOT delegate to ``LiveFetchProvider`` (that one is bound to
``httpx``); it re-implements the same manual, fully-re-guarded redirect loop, but
reuses ``LiveFetchProvider``'s module-level SSRF helpers verbatim so the egress
classification cannot drift:

* ``url_policy_error`` — literal-URL firewall (scheme/host/credential/metadata).
* ``resolve_validated_ip`` — DNS-rebinding guard: resolve ONCE per hop, validate
  EVERY returned IP, return one validated IP to PIN the connection to.
* IP pinning — closes the DNS-rebinding / TOCTOU window by handing curl the
  validated IP via curl's ``--resolve`` (``resolve=["host:port:ip"]``) so libcurl
  connects to the pre-validated address rather than re-resolving at connect time.
* Manual capped redirect loop — ``allow_redirects=False`` plus a loop that
  re-runs the FULL egress guard on the resolved ``Location`` of EVERY 3xx,
  blocking redirect-to-internal SSRF.
* Size cap — streamed accumulation with early abort (when the session exposes
  ``iter_content``) plus a Content-Length pre-check and a hard body-length check.
* Output normalization — html→markdown + redacted title via the shared
  ``_html_to_md`` / ``_extract_title`` helpers; content + metadata scrubbed via
  ``redact_public_text`` / ``redact_metadata_values``; the raw final URL/host is
  never emitted (digest ref only).
* Never raises — every error returns a structured ``{"status", "reason", ...}``.

Default-OFF / import boundary:
  ``curl_cffi`` is a LAZY import inside ``fetch`` (never at module top-level), so
  importing this module does not require ``curl_cffi`` to be installed and adds no
  network side-effects. It is wired in only by the (lazy) registration site
  (Task 3) and MUST NOT be added to any package ``__init__`` imported by sealed
  modules.

Divergences from ``LiveFetchProvider`` (deliberate, not omissions):

* No ``require_pinned_egress`` strict-mode flag. ``LiveFetchProvider`` exposes
  that flag because httpx/httpcore can leave a residual https DNS-rebinding/TOCTOU
  window when a transport does not honour connecting-by-IP with pinned SNI. Here
  the IP pin is handed to libcurl via ``resolve=["host:port:ip"]``, which is
  honoured at the transport layer and closes the rebinding/TOCTOU window for https
  at connect time — strictly stronger than httpx's residual-window case — so there
  is nothing for a strict flag to guard against.
* No Cloudflare ``cf-mitigated`` honest-UA retry. ``LiveFetchProvider`` retries a
  cf-challenge 403 with an honest UA because its plain-httpx request can trip the
  WAF. Here the browser ``impersonate=`` profile (full TLS/JA3 + HTTP/2
  fingerprint) IS the WAF bypass, so a 403 is treated as a real denial and mapped
  directly to a ``denied`` status rather than retried.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urljoin, urlsplit

from magi_agent.web_acquisition.live_fetch_provider import (
    ACCEPT_HEADER,
    _MAX_REDIRECT_HOPS,
    _REDIRECT_STATUS,
    _extract_title,
    _html_to_md,
    redact_metadata_values,
    resolve_validated_ip,
)
from magi_agent.web_acquisition.policy import redact_public_text, url_policy_error


# Default browser profile for curl_cffi impersonation (TLS/JA3 fingerprint).
_DEFAULT_IMPERSONATE: str = "chrome"
_DEFAULT_TIMEOUT_S: float = 30.0
_MAX_TIMEOUT_S: float = 120.0
_DEFAULT_MAX_CONTENT_BYTES: int = 5_000_000
_STREAM_CHUNK_BYTES: int = 64 * 1024


def _url_digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _denied(reason: str) -> dict[str, object]:
    return {"status": "denied", "reason": reason, "content": ""}


def _timeout(reason: str) -> dict[str, object]:
    return {"status": "timeout", "reason": reason, "content": ""}


class InsaneFetchProvider:
    """curl_cffi-backed WAF-bypass fetch provider with SSRF/DNS-rebinding hardening.

    Parameters
    ----------
    impersonate:
        curl_cffi browser profile (TLS/JA3 + HTTP/2 fingerprint), default
        ``"chrome"``. This is what bypasses fingerprinting WAFs.
    timeout_s:
        Per-request timeout in seconds (clamped to a sane ceiling).
    max_content_bytes:
        Hard cap on response body size; oversized bodies are denied
        (``content_too_large``).
    session:
        Test-injection seam. When provided it is used directly (must expose a
        ``get(url, *, headers, resolve, allow_redirects, impersonate, timeout,
        stream)`` method returning a response with ``.status_code``, ``.headers``
        mapping, and ``.content`` / optional ``.iter_content``). When ``None`` a
        real ``curl_cffi.requests.Session`` is built LAZILY on first use; if
        ``curl_cffi`` is not installed, ``fetch`` returns a structured
        ``curl_cffi_unavailable`` denial rather than raising.
    """

    openmagi_live_provider: Literal[True] = True

    def __init__(
        self,
        *,
        impersonate: str = _DEFAULT_IMPERSONATE,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        max_content_bytes: int = _DEFAULT_MAX_CONTENT_BYTES,
        session: object | None = None,
    ) -> None:
        self.impersonate = impersonate or _DEFAULT_IMPERSONATE
        self.timeout_s = max(0.1, min(float(timeout_s), _MAX_TIMEOUT_S))
        self.max_content_bytes = max(1, int(max_content_bytes))
        self._session = session

    def fetch(self, request: object) -> Mapping[str, object]:
        """Fetch a URL → markdown/text. NEVER raises; returns a structured mapping."""
        try:
            return self._fetch_inner(request)
        except Exception:
            # Absolute backstop — nothing escapes fetch().
            return _denied("unexpected_error")

    # -- internals -----------------------------------------------------------

    def _fetch_inner(self, request: object) -> Mapping[str, object]:
        url = getattr(request, "url", None)
        if not isinstance(url, str) or not url.strip():
            return _denied("url_required")
        url = url.strip()

        session = self._resolve_session()
        if session is None:
            return _denied("curl_cffi_unavailable")

        return self._follow_guarded(session, url)

    def _resolve_session(self) -> object | None:
        """Return the injected session, or lazily build a real curl_cffi one.

        The lazy import is intentional: importing this module must not require
        ``curl_cffi``. An import failure is NOT raised out of ``fetch`` — the
        caller maps a ``None`` session to ``curl_cffi_unavailable``.
        """
        if self._session is not None:
            return self._session
        try:
            from curl_cffi import requests as curl_requests  # noqa: PLC0415
        except Exception:
            return None
        try:
            return curl_requests.Session()
        except Exception:
            return None

    def _follow_guarded(self, session: object, url: str) -> Mapping[str, object]:
        """Manual redirect loop that re-runs the FULL egress guard on EVERY hop.

        For each hop, BEFORE issuing the request we run the literal-URL firewall
        (``url_policy_error``) and resolve+validate the host
        (``resolve_validated_ip``), then PIN the connection to the validated IP
        via curl's ``--resolve``. On a 3xx we resolve the ``Location`` (relative
        → absolute) and repeat the whole guard. Exceeding the hop cap → denied.
        """
        current = url
        requested_url = url
        for _hop in range(_MAX_REDIRECT_HOPS + 1):
            # 1. Literal-URL firewall (scheme/host/credential/metadata/cluster).
            policy_error = url_policy_error(current)
            if policy_error is not None:
                return _denied(policy_error)

            # 2. DNS-rebinding egress guard — resolve ONCE, validate ALL IPs.
            parts = urlsplit(current)
            host = (parts.hostname or "").strip()
            rebind_error, pinned_ip = resolve_validated_ip(host)
            if rebind_error is not None or pinned_ip is None:
                # dns_resolution_failed is transient → timeout; everything else
                # (rebind/blocked IP/no host) is a hard deny.
                if rebind_error == "dns_resolution_failed":
                    return _timeout("dns_resolution_failed")
                return _denied(rebind_error or "dns_rebind_blocked_ip")

            # 3. Pin the connection to the validated IP (curl --resolve), closing
            #    the DNS-rebinding/TOCTOU window. port = explicit, else scheme dflt.
            port = parts.port or (443 if parts.scheme == "https" else 80)
            resolve_pin = [f"{host}:{port}:{pinned_ip}"]

            # 4. Issue the guarded+pinned request (impersonation IS the bypass).
            outcome = self._do_get(session, current, resolve_pin)
            if isinstance(outcome, dict) and "status" in outcome:
                # Transport/timeout/connection error already mapped.
                return outcome
            response = outcome

            status_code = _response_status(response)

            # 5. Redirect → re-guard the resolved Location on the next hop.
            if status_code in _REDIRECT_STATUS:
                location = _response_header(response, "location")
                if not location:
                    return self._build_output(requested_url, current, response)
                current = urljoin(current, location)
                continue

            # 6. Terminal response → status mapping + build output.
            if status_code in {401, 403}:
                return _denied("http_status_denied")
            if status_code == 429 or status_code >= 500:
                return _timeout("http_status_retryable")
            if status_code < 200 or status_code >= 300:
                return _timeout("http_status_unexpected")

            return self._build_output(requested_url, current, response)

        # Exceeded the hop cap.
        return _denied("too_many_redirects")

    def _do_get(
        self, session: object, url: str, resolve_pin: list[str]
    ) -> object | dict[str, object]:
        """Single GET through the session adapter; maps transport errors → status.

        Returns the response object on success, or a ``{"status": ...}`` mapping
        when the underlying client raises a timeout/connection error.
        """
        with _temporary_curl_resolve(session, resolve_pin):
            try:
                return session.get(  # type: ignore[attr-defined]
                    url,
                    headers={"Accept": ACCEPT_HEADER},
                    allow_redirects=False,
                    impersonate=self.impersonate,
                    timeout=self.timeout_s,
                    stream=True,
                )
            except TypeError:
                # A session/fake that does not accept stream=True — retry without it
                # (we still enforce the size cap on the buffered body below).
                try:
                    return session.get(  # type: ignore[attr-defined]
                        url,
                        headers={"Accept": ACCEPT_HEADER},
                        allow_redirects=False,
                        impersonate=self.impersonate,
                        timeout=self.timeout_s,
                    )
                except Exception:
                    return _timeout("transport_error")
            except Exception:
                # curl_cffi timeouts/connection errors (or any client error) →
                # transient timeout, never escapes fetch().
                return _timeout("transport_error")

    def _read_capped(self, response: object) -> bytes | None:
        """Read the body honoring the size cap; ``None`` when it is exceeded.

        Prefers streamed accumulation (``iter_content``) with early abort so an
        oversized body is never fully buffered; falls back to ``.content`` with a
        hard length check.
        """
        iter_content = getattr(response, "iter_content", None)
        if callable(iter_content):
            chunks: list[bytes] = []
            total = 0
            try:
                iterator = iter_content(_STREAM_CHUNK_BYTES)
            except TypeError:
                iterator = iter_content()
            for chunk in iterator:
                if not chunk:
                    continue
                data = chunk if isinstance(chunk, bytes) else bytes(chunk)
                total += len(data)
                if total > self.max_content_bytes:
                    return None
                chunks.append(data)
            return b"".join(chunks)

        raw = getattr(response, "content", b"") or b""
        if isinstance(raw, str):
            raw = raw.encode("utf-8", errors="replace")
        if len(raw) > self.max_content_bytes:
            return None
        return raw

    def _build_output(
        self, requested_url: str, final_url: str, response: object
    ) -> Mapping[str, object]:
        # Content-Length fast-path pre-check (never relied on — see streaming).
        declared = _response_header(response, "content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_content_bytes:
                    return _denied("content_too_large")
            except (TypeError, ValueError):
                pass

        raw = self._read_capped(response)
        if raw is None:
            return _denied("content_too_large")

        content_type = (
            (_response_header(response, "content-type") or "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        text = raw.decode("utf-8", errors="replace")

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
                "statusCode": _response_status(response),
                "finalUrlRef": f"url:{_url_digest(final_url)}",
            }
        )
        return {
            # Never emit the raw final URL/host: HTML title is redacted free text,
            # otherwise fall back to a digest ref of the final URL.
            "url": requested_url,
            "title": title if (is_html and title) else f"url:{_url_digest(final_url)}",
            "content": content,
            "metadata": metadata,
        }


def _response_status(response: object) -> int:
    status = getattr(response, "status_code", None)
    try:
        return int(status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _response_header(response: object, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    # curl_cffi response headers are a case-insensitive mapping; fall back to a
    # manual case-insensitive scan for plain-dict fakes.
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value is not None:
            return str(value)
        value = getter(name.lower())
        if value is not None:
            return str(value)
        value = getter(name.title())
        if value is not None:
            return str(value)
    try:
        for key, value in headers.items():  # type: ignore[attr-defined]
            if str(key).lower() == name.lower():
                return str(value)
    except Exception:
        return None
    return None


class _temporary_curl_resolve:
    """Temporarily set curl_cffi's CURLOPT_RESOLVE session option.

    ``curl_cffi.requests.Session.get`` does not accept a ``resolve=`` keyword.
    IP pinning is configured through ``Session.curl_options`` instead, so each
    guarded hop installs its validated ``host:port:ip`` pin for the duration of
    exactly one request and then restores the caller's previous options.
    """

    def __init__(self, session: object, resolve_pin: list[str]) -> None:
        self._session = session
        self._resolve_pin = resolve_pin
        self._had_options = False
        self._previous_options: object = None

    def __enter__(self) -> None:
        key = _curl_resolve_option_key()
        self._had_options = hasattr(self._session, "curl_options")
        self._previous_options = getattr(self._session, "curl_options", None)

        options: dict[object, object]
        if isinstance(self._previous_options, Mapping):
            options = dict(self._previous_options)
        else:
            options = {}
        options[key] = list(self._resolve_pin)
        setattr(self._session, "curl_options", options)

    def __exit__(self, *_exc: object) -> None:
        if self._had_options:
            setattr(self._session, "curl_options", self._previous_options)
            return
        try:
            delattr(self._session, "curl_options")
        except AttributeError:
            pass


def _curl_resolve_option_key() -> object:
    """Return curl_cffi's RESOLVE option key without importing at module load."""
    try:
        from curl_cffi import CurlOpt  # noqa: PLC0415

        return CurlOpt.RESOLVE
    except Exception:
        return "RESOLVE"


__all__ = ["InsaneFetchProvider"]
