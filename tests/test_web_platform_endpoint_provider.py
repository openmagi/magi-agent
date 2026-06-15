"""Tests for PlatformEndpointProvider (PR-B).

All HTTP calls are intercepted via ``respx`` (or httpx mock transport) — no live
network.  DNS-rebinding guard is tested by monkeypatching ``socket.getaddrinfo``.

The tests use ``httpx_mock`` / ``respx`` style via ``httpx``'s built-in test
transport so we do not need ``pytest-httpx`` (which may not be installed).
"""

from __future__ import annotations

import socket
from collections.abc import Mapping
from unittest.mock import patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _provider(
    *,
    base_url: str = "https://platform.example.com",
    api_key: str = "test-api-key",
    skip_dns_check: bool = True,
) -> object:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    return PlatformEndpointProvider(
        base_url=base_url,
        api_key=api_key,
        skip_dns_check=skip_dns_check,
    )


class _MockRequest:
    """Minimal stand-in for WebAcquisitionProviderRequest in provider method calls."""

    def __init__(self, *, query: str | None = None, url: str | None = None) -> None:
        self.query = query
        self.url = url
        self.metadata: Mapping[str, object] = {}


def _transport_with_response(
    path: str,
    *,
    status_code: int = 200,
    json_body: dict[str, object] | None = None,
) -> httpx.MockTransport:
    """Return an httpx transport that answers one specific path with a JSON body."""

    def handler(request: httpx.Request) -> httpx.Response:
        if json_body is not None:
            return httpx.Response(status_code, json=json_body)
        return httpx.Response(status_code)

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


def test_search_200_normalises_results() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    transport = _transport_with_response(
        "/v1/search",
        json_body={
            "results": [
                {
                    "url": "https://docs.example.com/result-1",
                    "title": "Result One",
                    "snippet": "First snippet.",
                }
            ]
        },
    )
    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com",
        api_key="test-key",
        skip_dns_check=True,
    )
    # Monkeypatch _client() to use mock transport.
    def _mock_client() -> httpx.Client:
        return httpx.Client(transport=transport)

    provider._client = _mock_client  # type: ignore[method-assign]

    result = provider.search(_MockRequest(query="current docs"))

    assert isinstance(result, Mapping)
    results = result.get("results")
    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0]["url"] == "https://docs.example.com/result-1"
    assert results[0]["snippet"] == "First snippet."


def test_search_request_body_sends_query_field() -> None:
    """The /v1/search request body MUST carry ``query`` (the field the platform
    api-proxy reads via ``const { query } = JSON.parse(body)``). Sending only the
    Serper-style ``q`` makes the deployed endpoint 400 ``Missing query field`` and
    WebSearch silently fails. We keep ``q`` too for Serper-style backends."""
    import json as _json

    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(_json.loads((request.content or b"{}").decode() or "{}"))
        return httpx.Response(200, json={"results": []})

    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com",
        api_key="test-key",
        skip_dns_check=True,
    )
    provider._client = lambda: httpx.Client(  # type: ignore[method-assign]
        transport=httpx.MockTransport(handler)
    )

    provider.search(_MockRequest(query="tesla 10-k"))

    assert captured.get("query") == "tesla 10-k"
    assert captured.get("q") == "tesla 10-k"


def test_search_401_returns_denied() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    transport = _transport_with_response("/v1/search", status_code=401)
    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com", api_key="bad-key", skip_dns_check=True
    )
    provider._client = lambda: httpx.Client(transport=transport)  # type: ignore[method-assign]

    result = provider.search(_MockRequest(query="query"))
    assert result.get("status") == "denied"


def test_search_403_returns_denied() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    transport = _transport_with_response("/v1/search", status_code=403)
    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com", api_key="bad-key", skip_dns_check=True
    )
    provider._client = lambda: httpx.Client(transport=transport)  # type: ignore[method-assign]

    result = provider.search(_MockRequest(query="query"))
    assert result.get("status") == "denied"


def test_search_429_returns_timeout_retryable() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    transport = _transport_with_response("/v1/search", status_code=429)
    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com", api_key="key", skip_dns_check=True
    )
    provider._client = lambda: httpx.Client(transport=transport)  # type: ignore[method-assign]

    result = provider.search(_MockRequest(query="query"))
    assert result.get("status") == "timeout"


def test_search_500_returns_timeout() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    transport = _transport_with_response("/v1/search", status_code=500)
    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com", api_key="key", skip_dns_check=True
    )
    provider._client = lambda: httpx.Client(transport=transport)  # type: ignore[method-assign]

    result = provider.search(_MockRequest(query="query"))
    assert result.get("status") == "timeout"


def test_search_timeout_exception_returns_timeout() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    def _timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com", api_key="key", skip_dns_check=True
    )
    provider._client = lambda: httpx.Client(transport=httpx.MockTransport(_timeout_handler))  # type: ignore[method-assign]

    result = provider.search(_MockRequest(query="query"))
    assert result.get("status") == "timeout"


def test_search_empty_query_returns_denied() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com", api_key="key", skip_dns_check=True
    )
    result = provider.search(_MockRequest(query=""))
    assert result.get("status") == "denied"


# ---------------------------------------------------------------------------
# fetch()
# ---------------------------------------------------------------------------


def test_fetch_200_normalises_response() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    transport = _transport_with_response(
        "/v1/fetch",
        json_body={
            "url": "https://docs.example.com/page",
            "title": "Docs Page",
            "content": "Full page content here.",
            "status": 200,
        },
    )
    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com", api_key="key", skip_dns_check=True
    )
    provider._client = lambda: httpx.Client(transport=transport)  # type: ignore[method-assign]

    result = provider.fetch(_MockRequest(url="https://docs.example.com/page"))

    assert isinstance(result, Mapping)
    assert result.get("content") == "Full page content here."
    assert result.get("url") == "https://docs.example.com/page"


def test_fetch_401_returns_denied() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    transport = _transport_with_response("/v1/fetch", status_code=401)
    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com", api_key="key", skip_dns_check=True
    )
    provider._client = lambda: httpx.Client(transport=transport)  # type: ignore[method-assign]

    result = provider.fetch(_MockRequest(url="https://docs.example.com/page"))
    assert result.get("status") == "denied"


def test_fetch_timeout_returns_timeout() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    def _timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com", api_key="key", skip_dns_check=True
    )
    provider._client = lambda: httpx.Client(transport=httpx.MockTransport(_timeout_handler))  # type: ignore[method-assign]

    result = provider.fetch(_MockRequest(url="https://docs.example.com/page"))
    assert result.get("status") == "timeout"


def test_fetch_empty_url_returns_denied() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com", api_key="key", skip_dns_check=True
    )
    result = provider.fetch(_MockRequest(url=None))
    assert result.get("status") == "denied"


# ---------------------------------------------------------------------------
# reader()
# ---------------------------------------------------------------------------


def test_reader_200_maps_markdown_to_content() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    transport = _transport_with_response(
        "/v1/scrape",
        json_body={
            "url": "https://docs.example.com/page",
            "title": "Docs",
            "markdown": "# Heading\n\nFull markdown content.",
            "statusCode": 200,
        },
    )
    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com", api_key="key", skip_dns_check=True
    )
    provider._client = lambda: httpx.Client(transport=transport)  # type: ignore[method-assign]

    result = provider.reader(_MockRequest(url="https://docs.example.com/page"))

    assert isinstance(result, Mapping)
    assert result.get("content") == "# Heading\n\nFull markdown content."


def test_reader_200_falls_back_to_content_field() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    transport = _transport_with_response(
        "/v1/scrape",
        json_body={
            "url": "https://docs.example.com/page",
            "content": "Fallback content.",
        },
    )
    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com", api_key="key", skip_dns_check=True
    )
    provider._client = lambda: httpx.Client(transport=transport)  # type: ignore[method-assign]

    result = provider.reader(_MockRequest(url="https://docs.example.com/page"))
    assert result.get("content") == "Fallback content."


# ---------------------------------------------------------------------------
# DNS-rebinding guard
# ---------------------------------------------------------------------------


def _mock_getaddrinfo(ip: str) -> list[tuple]:
    """Fake getaddrinfo returning a single A record for the given IP."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]


def test_dns_rebinding_guard_blocks_localhost_ip() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com",
        api_key="key",
        skip_dns_check=False,  # Enable DNS check for this test
    )

    with patch("magi_agent.web_acquisition.providers.platform_endpoint._check_dns_rebinding") as mock_check:
        mock_check.return_value = "dns_rebinding_local_blocked"
        result = provider.fetch(_MockRequest(url="https://evil.corp/api"))

    assert result.get("status") == "denied"


def test_dns_rebinding_guard_blocks_metadata_ip() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com",
        api_key="key",
        skip_dns_check=False,
    )

    with patch("magi_agent.web_acquisition.providers.platform_endpoint._check_dns_rebinding") as mock_check:
        mock_check.return_value = "dns_rebinding_metadata_blocked"
        result = provider.fetch(_MockRequest(url="https://metadata-evil.corp/creds"))

    assert result.get("status") == "denied"


def test_dns_rebinding_guard_passes_public_ip() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    transport = _transport_with_response(
        "/v1/fetch",
        json_body={"url": "https://docs.example.com/page", "content": "ok"},
    )
    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com",
        api_key="key",
        skip_dns_check=False,
    )
    provider._client = lambda: httpx.Client(transport=transport)  # type: ignore[method-assign]

    with patch("magi_agent.web_acquisition.providers.platform_endpoint._check_dns_rebinding") as mock_check:
        mock_check.return_value = None  # public IP — safe
        result = provider.fetch(_MockRequest(url="https://docs.example.com/page"))

    # Should proceed to make the request.
    assert result.get("status") != "denied"


def test_check_dns_rebinding_blocks_127_0_0_1() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import _check_dns_rebinding

    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("127.0.0.1")):
        error = _check_dns_rebinding("evil.localhost.corp")
    assert error is not None
    assert "local" in error or "rebinding" in error


def test_check_dns_rebinding_blocks_169_254_169_254() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import _check_dns_rebinding

    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("169.254.169.254")):
        error = _check_dns_rebinding("evil-metadata.corp")
    assert error is not None
    assert "metadata" in error or "rebinding" in error


def test_check_dns_rebinding_blocks_private_10_x() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import _check_dns_rebinding

    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("10.0.0.1")):
        error = _check_dns_rebinding("intranet.corp")
    assert error is not None


def test_check_dns_rebinding_passes_public_ip() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import _check_dns_rebinding

    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("93.184.216.34")):  # example.com
        error = _check_dns_rebinding("docs.example.com")
    assert error is None


def test_check_dns_rebinding_dns_failure_returns_error() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import _check_dns_rebinding

    def _raise(*args: object, **kwargs: object) -> None:
        raise OSError("DNS failure")

    with patch("socket.getaddrinfo", side_effect=_raise):
        error = _check_dns_rebinding("nxdomain.example.invalid")
    assert error == "dns_resolution_failed"


# ---------------------------------------------------------------------------
# openmagi_live_provider marker
# ---------------------------------------------------------------------------


def test_platform_endpoint_provider_has_live_marker() -> None:
    from magi_agent.web_acquisition.providers.platform_endpoint import (
        PlatformEndpointProvider,
    )

    provider = PlatformEndpointProvider(
        base_url="https://platform.example.com", api_key="key"
    )
    assert provider.openmagi_live_provider is True
