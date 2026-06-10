"""Fail-soft audit tests — Principle 7 (P7).

Every external provider must absorb its own errors and return a soft error
mapping rather than raising.  The provider router must fall back to a working
provider when the primary fails.

Hermetic: no real network calls.  Providers are exercised with injection seams
(fake sessions, mock responses) or controlled exceptions.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Literal
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_error_mapping(result: object) -> bool:
    """Return True when *result* is a ``{"status": ...}`` error mapping."""
    return isinstance(result, Mapping) and "status" in result


def _status(result: object) -> str:
    if isinstance(result, Mapping):
        return str(result.get("status", ""))
    return ""


# ---------------------------------------------------------------------------
# ComposioMcpShimProvider — fail-soft
# ---------------------------------------------------------------------------


class _BrokenBundle:
    """Fake ComposioToolsetBundle that is active but raises on every action call."""

    active: bool = True

    def call_action(self, action_name: str, payload: object) -> object:
        raise ConnectionError("simulated Composio MCP ConnectionError")


class _InactiveBrokenBundle:
    """Inactive bundle — provider should return 'denied' without touching call_action."""

    active: bool = False

    def call_action(self, action_name: str, payload: object) -> object:
        raise RuntimeError("should not be called")


class _SearchRequest:
    query: str = "test query"


class _FetchRequest:
    url: str = "https://example.com/page"


def test_composio_shim_search_connection_error_returns_timeout() -> None:
    """ComposioMcpShimProvider.search: ConnectionError → {"status": "timeout"}."""
    from magi_agent.web_acquisition.providers.composio_mcp_shim import ComposioMcpShimProvider

    shim = ComposioMcpShimProvider(toolset_bundle=_BrokenBundle())
    result = shim.search(_SearchRequest())

    assert _is_error_mapping(result)
    assert _status(result) == "timeout"


def test_composio_shim_fetch_connection_error_returns_timeout() -> None:
    """ComposioMcpShimProvider.fetch: ConnectionError → {"status": "timeout"}."""
    from magi_agent.web_acquisition.providers.composio_mcp_shim import ComposioMcpShimProvider

    shim = ComposioMcpShimProvider(toolset_bundle=_BrokenBundle())
    result = shim.fetch(_FetchRequest())

    assert _is_error_mapping(result)
    assert _status(result) == "timeout"


def test_composio_shim_search_inactive_bundle_returns_denied() -> None:
    """ComposioMcpShimProvider.search: inactive bundle → {"status": "denied"}."""
    from magi_agent.web_acquisition.providers.composio_mcp_shim import ComposioMcpShimProvider

    shim = ComposioMcpShimProvider(toolset_bundle=_InactiveBrokenBundle())
    result = shim.search(_SearchRequest())

    assert _is_error_mapping(result)
    assert _status(result) == "denied"


def test_composio_shim_never_raises() -> None:
    """ComposioMcpShimProvider must not raise regardless of bundle state."""
    from magi_agent.web_acquisition.providers.composio_mcp_shim import ComposioMcpShimProvider

    class _ChaosBundle:
        active: bool = True

        def call_action(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("unexpected chaos error")

    shim = ComposioMcpShimProvider(toolset_bundle=_ChaosBundle())
    # Must not raise
    r1 = shim.search(_SearchRequest())
    r2 = shim.fetch(_FetchRequest())
    assert _is_error_mapping(r1)
    assert _is_error_mapping(r2)


# ---------------------------------------------------------------------------
# JinaReaderProvider — fail-soft
# ---------------------------------------------------------------------------


class _FakeJinaResponse:
    """Minimal httpx-compatible response for JinaReaderProvider tests."""

    def __init__(self, *, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {"content-type": "text/markdown"}
        self.content = text.encode("utf-8") if text else b""


class _JinaTimeoutSession:
    """httpx.Client stand-in that raises TimeoutException on every request."""

    def get(self, url: str, **kwargs: object) -> object:
        import httpx
        raise httpx.TimeoutException("simulated timeout")


def test_jina_reader_network_error_returns_error_mapping() -> None:
    """JinaReaderProvider.reader: network timeout → error mapping, never raises."""
    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    import httpx
    provider = JinaReaderProvider(client=httpx.Client(transport=None))  # type: ignore[arg-type]

    class _Req:
        url = "https://example.com/article"

    # Patch LiveFetchProvider to simulate failure
    with patch(
        "magi_agent.web_acquisition.live_fetch_provider.LiveFetchProvider.fetch",
        return_value={"status": "timeout", "reason": "simulated_timeout"},
    ):
        result = provider.reader(_Req())

    assert _is_error_mapping(result)
    # Must have a "status" key (timeout or denied)
    assert _status(result) in ("timeout", "denied")


def test_jina_reader_missing_url_returns_denied() -> None:
    """JinaReaderProvider.reader: empty url → {"status": "denied"}."""
    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    import httpx
    provider = JinaReaderProvider(client=httpx.Client())

    class _Req:
        url = ""

    result = provider.reader(_Req())
    assert _is_error_mapping(result)
    assert _status(result) == "denied"


def test_jina_reader_private_url_returns_denied() -> None:
    """JinaReaderProvider.reader: localhost URL → policy denial, never raises."""
    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    import httpx
    provider = JinaReaderProvider(client=httpx.Client())

    class _Req:
        url = "http://localhost/internal"

    result = provider.reader(_Req())
    assert _is_error_mapping(result)
    assert _status(result) == "denied"


def test_jina_reader_never_raises_on_unexpected_error() -> None:
    """JinaReaderProvider.reader: absolute backstop — no exception escapes."""
    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    import httpx
    provider = JinaReaderProvider(client=httpx.Client())

    class _BadReq:
        # This will trigger unusual handling paths
        url = "https://example.com/page"

    with patch.object(provider, "_reader_inner", side_effect=RuntimeError("chaos")):
        result = provider.reader(_BadReq())

    assert _is_error_mapping(result)
    assert _status(result) in ("denied", "timeout")


# ---------------------------------------------------------------------------
# InsaneFetchProvider — fail-soft
# ---------------------------------------------------------------------------


class _FakeSession:
    """curl_cffi session stand-in for InsaneFetchProvider tests."""

    def __init__(self, *, status_code: int = 200, content: bytes = b"Hello world") -> None:
        self._status_code = status_code
        self._content = content
        self.curl_options: dict[object, object] = {}

    def get(self, url: str, **kwargs: object) -> "_FakeSession":
        return self

    @property
    def status_code(self) -> int:
        return self._status_code

    @property
    def headers(self) -> dict[str, str]:
        return {"content-type": "text/plain"}

    @property
    def content(self) -> bytes:
        return self._content


class _RaisingSession:
    """session that raises on every .get() call."""

    def get(self, url: str, **kwargs: object) -> object:
        raise OSError("simulated connection failure")


def test_insane_fetch_transport_error_returns_timeout() -> None:
    """InsaneFetchProvider.fetch: connection error → timeout mapping, never raises."""
    from magi_agent.web_acquisition.providers.insane_fetch import InsaneFetchProvider

    provider = InsaneFetchProvider(session=_RaisingSession())

    class _Req:
        url = "https://example.com/waf-protected"

    result = provider.fetch(_Req())
    assert _is_error_mapping(result)
    assert _status(result) in ("timeout", "denied")


def test_insane_fetch_private_url_returns_denied() -> None:
    """InsaneFetchProvider.fetch: 169.254.169.254 (metadata) → denied."""
    from magi_agent.web_acquisition.providers.insane_fetch import InsaneFetchProvider

    provider = InsaneFetchProvider(session=_FakeSession())

    class _Req:
        url = "http://169.254.169.254/latest/meta-data/"

    result = provider.fetch(_Req())
    assert _is_error_mapping(result)
    assert _status(result) == "denied"


def test_insane_fetch_curl_cffi_unavailable_returns_denied() -> None:
    """InsaneFetchProvider.fetch: no session + no curl_cffi → curl_cffi_unavailable denial."""
    from magi_agent.web_acquisition.providers.insane_fetch import InsaneFetchProvider

    # No injected session; curl_cffi import is patched away.
    provider = InsaneFetchProvider()

    class _Req:
        url = "https://example.com/page"

    with patch.dict("sys.modules", {"curl_cffi": None}):
        with patch(
            "magi_agent.web_acquisition.providers.insane_fetch.InsaneFetchProvider._resolve_session",
            return_value=None,
        ):
            result = provider.fetch(_Req())

    assert _is_error_mapping(result)
    assert _status(result) == "denied"


def test_insane_fetch_never_raises() -> None:
    """InsaneFetchProvider.fetch: absolute backstop — no exception escapes."""
    from magi_agent.web_acquisition.providers.insane_fetch import InsaneFetchProvider

    provider = InsaneFetchProvider(session=_FakeSession())

    class _Req:
        url = "https://example.com/page"

    with patch.object(provider, "_fetch_inner", side_effect=RuntimeError("chaos")):
        result = provider.fetch(_Req())

    assert _is_error_mapping(result)
    assert _status(result) == "denied"


# ---------------------------------------------------------------------------
# PlatformEndpointProvider — fail-soft: non-httpx response
# ---------------------------------------------------------------------------


def test_platform_endpoint_handle_response_non_httpx_returns_timeout() -> None:
    """PlatformEndpointProvider._handle_response: non-httpx object → timeout, never raises."""
    from magi_agent.web_acquisition.providers.platform_endpoint import PlatformEndpointProvider

    provider = PlatformEndpointProvider(
        base_url="https://api.example.com",
        api_key="test-key",
        skip_dns_check=True,
    )

    class _WeirdResponse:
        # No status_code attribute at all
        pass

    result = provider._handle_response(_WeirdResponse(), "search")
    assert _is_error_mapping(result)
    assert _status(result) == "timeout"


def test_platform_endpoint_handle_response_bad_status_code_type_returns_timeout() -> None:
    """PlatformEndpointProvider._handle_response: non-int status_code → timeout."""
    from magi_agent.web_acquisition.providers.platform_endpoint import PlatformEndpointProvider

    provider = PlatformEndpointProvider(
        base_url="https://api.example.com",
        api_key="test-key",
        skip_dns_check=True,
    )

    class _BadStatusResponse:
        status_code = "not-a-number"

    result = provider._handle_response(_BadStatusResponse(), "fetch")
    assert _is_error_mapping(result)
    assert _status(result) == "timeout"


# ---------------------------------------------------------------------------
# WebAcquisitionProviderRouter — fallback to second provider when first fails
# ---------------------------------------------------------------------------


class _OkProvider:
    """Fake live provider that always returns a successful search result."""

    openmagi_live_provider = True

    def search(self, request: object) -> dict[str, object]:
        return {
            "results": [
                {
                    "url": "https://docs.example.com/ok",
                    "title": "OK Result",
                    "snippet": "Result from ok provider.",
                }
            ]
        }

    def fetch(self, request: object) -> dict[str, object]:
        return {
            "url": "https://docs.example.com/ok",
            "title": "OK Fetch",
            "content": "Content from ok provider.",
        }


class _FailProvider:
    """Fake live provider that always returns a timeout (transient failure)."""

    openmagi_live_provider = True

    def search(self, request: object) -> dict[str, object]:
        return {"status": "timeout"}

    def fetch(self, request: object) -> dict[str, object]:
        return {"status": "timeout"}


def _live_pack(provider_names: tuple[str, ...] = ("primary", "fallback")) -> object:
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionPackConfig,
        LiveWebAcquisitionProviderPack,
    )

    config = LiveWebAcquisitionPackConfig(
        enabled=True,
        liveNetworkEnabled=True,
        providerAllowlist=provider_names,
    )
    return LiveWebAcquisitionProviderPack(config)


def _make_search_request(provider_name: str = "primary") -> object:
    from magi_agent.web_acquisition.live_provider_pack import WebAcquisitionProviderRequest

    return WebAcquisitionProviderRequest(
        requestId="req-failsoft-001",
        operation="search",
        query="test query",
        providerName=provider_name,
        botIdDigest="bot:abc",
        ownerIdDigest="owner:def",
        sessionKeyDigest="session:ghi",
    )


def _make_router_with_two_providers(
    *,
    first_status: str = "timeout",
    second_status: str = "ok",
) -> tuple[object, object, object]:
    """Build a router where the first provider has *first_status* and the second *second_status*.

    Returns ``(router, first_provider, second_provider)``.
    """
    from magi_agent.web_acquisition.provider_router import (
        ProviderRouterConfig,
        WebAcquisitionProviderRouter,
    )

    first = _FailProvider() if first_status == "timeout" else _OkProvider()
    second = _OkProvider() if second_status == "ok" else _FailProvider()

    pack = _live_pack(("primary", "fallback"))
    config = ProviderRouterConfig(
        enabled=True,
        providers=("primary", "fallback"),
        max_attempts_per_provider=1,
        base_retry_delay_ms=0,
        max_retry_delay_ms=0,
    )
    router = WebAcquisitionProviderRouter(
        pack=pack,
        config=config,
        providers={"primary": first, "fallback": second},
    )
    return router, first, second


def test_router_falls_back_to_second_provider_when_first_fails() -> None:
    """Router: first provider timeout → falls back to second provider → returns ok."""
    router, _first, _second = _make_router_with_two_providers(
        first_status="timeout",
        second_status="ok",
    )
    request = _make_search_request()

    result = router.run(request, _sleep=False)

    # The router should have reached the second (ok) provider
    assert result.status in ("ok", "no_answer"), (
        f"Expected ok/no_answer status, got {result.status!r} ({result.reason_codes})"
    )


def test_router_returns_exhausted_when_all_providers_fail() -> None:
    """Router: all providers fail → returns repair_required with all_providers_exhausted."""
    router, _first, _second = _make_router_with_two_providers(
        first_status="timeout",
        second_status="timeout",
    )
    request = _make_search_request()

    result = router.run(request, _sleep=False)

    assert result.status == "repair_required"
    assert any("exhausted" in rc for rc in result.reason_codes), result.reason_codes


def test_router_returns_soft_result_on_unexpected_pack_exception() -> None:
    """Router: unexpected exception in pack.run() → returns error result, never raises."""
    from magi_agent.web_acquisition.provider_router import (
        ProviderRouterConfig,
        WebAcquisitionProviderRouter,
    )

    pack = _live_pack(("provider_a",))
    config = ProviderRouterConfig(
        enabled=True,
        providers=("provider_a",),
        max_attempts_per_provider=1,
        base_retry_delay_ms=0,
        max_retry_delay_ms=0,
    )
    provider_a = _OkProvider()
    router = WebAcquisitionProviderRouter(
        pack=pack,
        config=config,
        providers={"provider_a": provider_a},
    )

    request = _make_search_request(provider_name="provider_a")

    # Inject an unexpected exception into the pack's run method
    with patch.object(pack, "run", side_effect=RuntimeError("unexpected chaos")):
        result = router.run(request, _sleep=False)

    assert result.status == "repair_required"
    assert "router_unexpected_error" in result.reason_codes


def test_router_disabled_returns_soft_disabled_result() -> None:
    """Router: config.enabled=False → returns disabled result, never raises."""
    from magi_agent.web_acquisition.provider_router import (
        ProviderRouterConfig,
        WebAcquisitionProviderRouter,
    )
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionPackConfig,
        LiveWebAcquisitionProviderPack,
    )

    pack = LiveWebAcquisitionProviderPack(
        LiveWebAcquisitionPackConfig(enabled=True, liveNetworkEnabled=True)
    )
    config = ProviderRouterConfig(enabled=False)
    router = WebAcquisitionProviderRouter(
        pack=pack,
        config=config,
        providers={},
    )
    request = _make_search_request()
    result = router.run(request, _sleep=False)

    assert result.status == "disabled"
