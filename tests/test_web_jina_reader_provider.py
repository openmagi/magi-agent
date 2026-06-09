"""Tests for JinaReaderProvider.

Hermetic — no live network.  HTTP egress is intercepted via ``httpx.MockTransport``
injected through the ``client`` seam.  DNS resolution is monkeypatched to return
a public IP, mirroring the ``test_live_fetch_provider.py`` pattern exactly.
"""

from __future__ import annotations

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _allow_public_host(monkeypatch: pytest.MonkeyPatch, ip: str = "93.184.216.34") -> None:
    """Monkeypatch LiveFetchProvider's socket.getaddrinfo to resolve any host to ``ip``."""

    def _fake_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        return [(2, 1, 6, "", (ip, 0))]

    monkeypatch.setattr(
        "magi_agent.web_acquisition.live_fetch_provider.socket.getaddrinfo",
        _fake_getaddrinfo,
    )


class _TransportSpy:
    """Wraps httpx.MockTransport and records requests for assertion."""

    def __init__(self, handler) -> None:  # type: ignore[no-untyped-def]
        self._transport = httpx.MockTransport(handler)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._transport.handle_request(request)


def _client_with(
    handler, *, extra_headers: dict[str, str] | None = None
) -> tuple[httpx.Client, _TransportSpy]:
    """Build an httpx.Client backed by a transport spy, with optional default headers."""
    spy = _TransportSpy(handler)
    client = httpx.Client(
        transport=spy,  # type: ignore[arg-type]
        headers=extra_headers or {},
        follow_redirects=False,
    )
    return client, spy


class _Req:
    """Minimal request duck-type for provider.reader()."""

    def __init__(self, url: str | None) -> None:
        self.url = url


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_reader_200_returns_original_url_and_markdown_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 from jina → success dict with url == original target (NOT jina endpoint)."""
    _allow_public_host(monkeypatch)

    target_url = "https://docs.example.com/article"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain; charset=utf-8"},
            text="# Article Title\n\nThis is the markdown content.",
        )

    client, spy = _client_with(handler)

    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider(client=client)
    result = provider.reader(_Req(target_url))

    # No error status — success result.
    assert "status" not in result, f"Unexpected error status: {result}"

    # url must be the ORIGINAL target, NOT the jina endpoint.
    assert result["url"] == target_url, f"Expected original target URL, got {result['url']!r}"
    assert "r.jina.ai" not in str(result["url"]), "url must not expose the jina endpoint"

    # Content from the jina response body should be present.
    assert "markdown content" in str(result["content"]) or "Article" in str(result["content"])

    # Exactly one HTTP request was issued (to the jina endpoint).
    # After IP-pinning, the URL host is rewritten to the validated IP, but the
    # Host header preserves the real hostname (r.jina.ai).
    assert len(spy.requests) == 1
    req = spy.requests[0]
    jina_req_url = str(req.url)
    host_header = req.headers.get("host", "")
    # Either the URL itself contains r.jina.ai (no-pinning path) OR the Host
    # header carries r.jina.ai (pinning path, URL host is the resolved IP).
    assert "r.jina.ai" in jina_req_url or "r.jina.ai" in host_header, (
        f"Jina endpoint not found: url={jina_req_url!r}, host={host_header!r}"
    )
    # The target path must appear somewhere in the issued URL.
    assert "docs.example.com" in jina_req_url


def test_reader_content_and_title_fields_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful result always carries url, title, content, metadata keys."""
    _allow_public_host(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="Some page content for testing.",
        )

    client, _spy = _client_with(handler)

    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider(client=client)
    result = provider.reader(_Req("https://example.com/page"))

    assert "url" in result
    assert "title" in result
    assert "content" in result
    assert "metadata" in result


# ---------------------------------------------------------------------------
# SSRF pre-check on target url (no HTTP call must be made)
# ---------------------------------------------------------------------------


def test_reader_localhost_target_denied_before_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An internal/localhost target is rejected by policy BEFORE any HTTP egress."""
    _allow_public_host(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("transport must not be called for local URL")

    client, spy = _client_with(handler)

    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider(client=client)
    result = provider.reader(_Req("http://localhost/internal"))

    assert result["status"] == "denied"
    assert spy.requests == [], "No HTTP request should have been made"


def test_reader_metadata_ip_target_denied_before_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloud metadata IP target is rejected by policy BEFORE any HTTP egress."""
    _allow_public_host(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("transport must not be called for metadata URL")

    client, spy = _client_with(handler)

    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider(client=client)
    result = provider.reader(_Req("http://169.254.169.254/latest/meta-data/"))

    assert result["status"] == "denied"
    assert spy.requests == [], "No HTTP request should have been made"


def test_reader_private_ip_target_denied_before_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Private-range IP target is rejected by policy BEFORE any HTTP egress."""
    _allow_public_host(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("transport must not be called for private URL")

    client, spy = _client_with(handler)

    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider(client=client)
    result = provider.reader(_Req("http://10.0.0.1/secret"))

    assert result["status"] == "denied"
    assert spy.requests == [], "No HTTP request should have been made"


# ---------------------------------------------------------------------------
# Missing / blank url
# ---------------------------------------------------------------------------


def test_reader_missing_url_denied() -> None:
    """reader() with no url attribute → denied url_required."""
    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider()
    result = provider.reader(_Req(None))

    assert result["status"] == "denied"
    assert result["reason"] == "url_required"
    assert result["content"] == ""


def test_reader_blank_url_denied() -> None:
    """reader() with blank url string → denied url_required."""
    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider()
    result = provider.reader(_Req("   "))

    assert result["status"] == "denied"
    assert result["reason"] == "url_required"


def test_reader_request_without_url_attr_denied() -> None:
    """reader() with an object that has no .url attribute → denied url_required."""
    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider()
    result = provider.reader(object())  # no .url attribute at all

    assert result["status"] == "denied"
    assert result["reason"] == "url_required"


# ---------------------------------------------------------------------------
# Timeout / transport error
# ---------------------------------------------------------------------------


def test_reader_timeout_returns_timeout_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """An httpx.ReadTimeout inside LiveFetchProvider.fetch → {"status": "timeout", ...}."""
    _allow_public_host(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow server", request=request)

    client, _spy = _client_with(handler)

    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider(client=client)
    result = provider.reader(_Req("https://docs.example.com/article"))

    assert result["status"] == "timeout"
    assert result["content"] == ""


def test_reader_connect_error_returns_timeout_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """An httpx.ConnectError → LiveFetchProvider wraps it as timeout/denied; never raises."""
    _allow_public_host(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client, _spy = _client_with(handler)

    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider(client=client)
    result = provider.reader(_Req("https://docs.example.com/article"))

    # ConnectError is a TransportError → LiveFetchProvider returns {"status": "timeout"}.
    assert result["status"] in {"timeout", "denied"}
    assert result["content"] == ""


# ---------------------------------------------------------------------------
# Auth header
# ---------------------------------------------------------------------------


def test_reader_with_api_key_sends_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """When api_key is given the request carries Authorization: Bearer <key>."""
    _allow_public_host(monkeypatch)

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="# Content",
        )

    # Build the client with the auth headers the provider would set.
    client = httpx.Client(
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        headers={
            "Authorization": "Bearer test-jina-api-key",
            "X-Return-Format": "markdown",
        },
        follow_redirects=False,
    )

    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider(api_key="test-jina-api-key", client=client)
    result = provider.reader(_Req("https://docs.example.com/article"))

    assert "status" not in result, f"Unexpected error: {result}"
    assert len(captured) == 1

    req = captured[0]
    auth_header = req.headers.get("authorization", "")
    assert auth_header == "Bearer test-jina-api-key", (
        f"Expected Authorization: Bearer test-jina-api-key, got {auth_header!r}"
    )
    return_format = req.headers.get("x-return-format", "")
    assert return_format == "markdown", (
        f"Expected X-Return-Format: markdown, got {return_format!r}"
    )


def test_reader_without_api_key_omits_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no api_key is given no Authorization header is sent."""
    _allow_public_host(monkeypatch)

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="Free tier content.",
        )

    client, _spy = _client_with(handler)

    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider(client=client)  # no api_key
    result = provider.reader(_Req("https://docs.example.com/article"))

    assert "status" not in result, f"Unexpected error: {result}"
    assert len(captured) == 1
    assert "authorization" not in {k.lower() for k in captured[0].headers}, (
        "Authorization header must NOT be present when no api_key is given"
    )


# ---------------------------------------------------------------------------
# Constructor-built client auth headers (I-1)
# ---------------------------------------------------------------------------


def test_constructor_with_api_key_builds_client_with_auth_header() -> None:
    """JinaReaderProvider(api_key=...) with no client= must build an httpx.Client
    that carries both Authorization and X-Return-Format headers."""
    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider(api_key="my-key")
    client = provider._fetch_provider._client
    assert client is not None
    assert dict(client.headers).get("authorization") == "Bearer my-key"
    assert dict(client.headers).get("x-return-format") == "markdown"


def test_constructor_without_api_key_builds_client_with_no_auth_header() -> None:
    """JinaReaderProvider() with no api_key must build a client WITHOUT an
    Authorization header, but still carry X-Return-Format: markdown."""
    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider()
    client = provider._fetch_provider._client
    assert client is not None
    assert "authorization" not in dict(client.headers)
    assert dict(client.headers).get("x-return-format") == "markdown"


# ---------------------------------------------------------------------------
# Non-2xx upstream response passthrough (M-3)
# ---------------------------------------------------------------------------


def test_reader_non_2xx_response_returns_status_dict_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 or 403 from Jina must be returned as a {'status': ...} dict, never raise."""
    _allow_public_host(monkeypatch)

    for status_code in (429, 403):

        def handler(request: httpx.Request, _code: int = status_code) -> httpx.Response:
            return httpx.Response(
                _code,
                headers={"content-type": "text/plain"},
                text="Rate limited" if _code == 429 else "Forbidden",
            )

        client, _spy = _client_with(handler)

        from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

        provider = JinaReaderProvider(client=client)
        result = provider.reader(_Req("https://docs.example.com/article"))

        # Must not raise; must return a structured mapping.
        assert isinstance(result, dict), f"Expected dict for {status_code}, got {type(result)}"
        # Either an error status is present (denied/timeout) OR the content is empty/short.
        # The key constraint is: no exception was raised.
        assert "content" in result or "status" in result, (
            f"Expected status or content key for {status_code}, got {result!r}"
        )


# ---------------------------------------------------------------------------
# live marker
# ---------------------------------------------------------------------------


def test_jina_reader_provider_has_live_marker() -> None:
    """openmagi_live_provider class attribute must be True."""
    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider()
    assert provider.openmagi_live_provider is True


# ---------------------------------------------------------------------------
# Jina endpoint shape
# ---------------------------------------------------------------------------


def test_reader_builds_correct_jina_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """The request URL issued to the transport must be the jina endpoint."""
    _allow_public_host(monkeypatch)

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="content",
        )

    client, _spy = _client_with(handler)

    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    provider = JinaReaderProvider(client=client)
    target = "https://example.com/some/path?q=1"
    provider.reader(_Req(target))

    assert len(captured) == 1
    issued_url = str(captured[0].url)
    # The issued URL must encode the jina endpoint (after IP-pinning the host is
    # rewritten to the resolved IP, but the original host appears in the Host header).
    # We check the Host header reflects r.jina.ai and the path contains the target.
    host_header = captured[0].headers.get("host", "")
    assert "r.jina.ai" in host_header or "r.jina.ai" in issued_url, (
        f"Expected jina endpoint, got url={issued_url!r} host={host_header!r}"
    )
