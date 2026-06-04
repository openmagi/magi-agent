from __future__ import annotations


class SpyStubLiveProvider:
    """StubLiveProvider wrapper that records every operation call for assertions."""

    openmagi_live_provider = True

    def __init__(self) -> None:
        from magi_agent.web_acquisition.live_provider_pack import StubLiveProvider

        self._stub = StubLiveProvider()
        self.calls: list[object] = []

    def search(self, request: object) -> object:
        self.calls.append(("search", request))
        return self._stub.search(request)

    def fetch(self, request: object) -> object:
        self.calls.append(("fetch", request))
        return self._stub.fetch(request)

    def reader(self, request: object) -> object:
        self.calls.append(("reader", request))
        return self._stub.reader(request)

    def browser_fallback(self, request: object) -> object:
        self.calls.append(("browser_fallback", request))
        return self._stub.browser_fallback(request)


class UntrustedProvider:
    """Live-shaped provider that lacks the openmagi_live_provider trust marker."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def search(self, request: object) -> dict[str, object]:
        self.calls.append(("search", request))
        return {"results": [{"url": "https://docs.example.com/x", "snippet": "x"}]}


def _live_config(**overrides: object) -> object:
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionPackConfig,
    )

    payload: dict[str, object] = {
        "enabled": True,
        "liveNetworkEnabled": True,
        "providerAllowlist": ("live-web",),
    }
    payload.update(overrides)
    return LiveWebAcquisitionPackConfig(**payload)


def _request(**overrides: object) -> object:
    from magi_agent.web_acquisition.live_provider_pack import (
        WebAcquisitionProviderRequest,
    )

    payload: dict[str, object] = {
        "operation": "search",
        "requestId": "live-1",
        "providerName": "live-web",
        "botIdDigest": "bot:abc",
        "ownerIdDigest": "owner:def",
        "sessionKeyDigest": "session:ghi",
        "query": "current docs",
    }
    payload.update(overrides)
    return WebAcquisitionProviderRequest(**payload)


def test_disabled_config_returns_disabled_without_provider_calls() -> None:
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionPackConfig,
        LiveWebAcquisitionProviderPack,
    )

    provider = SpyStubLiveProvider()
    pack = LiveWebAcquisitionProviderPack(LiveWebAcquisitionPackConfig())

    result = pack.run(_request(), provider=provider)

    assert result.status == "disabled"
    assert result.reason_codes == ("live_web_acquisition_pack_disabled",)
    assert result.source_records == ()
    assert provider.calls == []


def test_untrusted_provider_is_blocked_without_provider_calls() -> None:
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionProviderPack,
    )

    provider = UntrustedProvider()
    pack = LiveWebAcquisitionProviderPack(_live_config())

    result = pack.run(_request(), provider=provider)

    assert result.status == "blocked"
    assert result.reason_codes == ("live_provider_untrusted",)
    assert provider.calls == []


def test_live_network_disabled_is_blocked_without_provider_calls() -> None:
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionProviderPack,
    )

    provider = SpyStubLiveProvider()
    pack = LiveWebAcquisitionProviderPack(_live_config(liveNetworkEnabled=False))

    result = pack.run(_request(), provider=provider)

    assert result.status == "blocked"
    assert result.reason_codes == ("live_network_disabled",)
    assert provider.calls == []


def test_provider_not_in_allowlist_is_blocked() -> None:
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionProviderPack,
    )

    provider = SpyStubLiveProvider()
    pack = LiveWebAcquisitionProviderPack(_live_config(providerAllowlist=("other",)))

    result = pack.run(_request(providerName="live-web"), provider=provider)

    assert result.status == "blocked"
    assert result.reason_codes == ("provider_not_allowlisted",)
    assert provider.calls == []


def test_ssrf_blocked_url_is_rejected_in_front_of_provider() -> None:
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionProviderPack,
    )

    provider = SpyStubLiveProvider()
    pack = LiveWebAcquisitionProviderPack(_live_config())

    metadata = pack.run(
        _request(operation="fetch", url="http://169.254.169.254/latest/meta-data"),
        provider=provider,
    )
    private = pack.run(
        _request(operation="fetch", url="http://10.0.0.1/private"),
        provider=provider,
    )

    assert metadata.status == "blocked"
    assert metadata.reason_codes == ("metadata_url_blocked",)
    assert private.status == "blocked"
    assert private.reason_codes == ("private_url_blocked",)
    assert provider.calls == []


def test_empty_search_query_is_blocked() -> None:
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionProviderPack,
    )

    provider = SpyStubLiveProvider()
    pack = LiveWebAcquisitionProviderPack(_live_config())

    result = pack.run(_request(operation="search", query="   "), provider=provider)

    assert result.status == "blocked"
    assert result.reason_codes == ("query_required",)
    assert provider.calls == []


def test_happy_path_search_returns_ok_records_from_stub_output() -> None:
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionProviderPack,
    )

    provider = SpyStubLiveProvider()
    pack = LiveWebAcquisitionProviderPack(_live_config())

    result = pack.run(_request(operation="search", query="current docs"), provider=provider)

    assert result.status == "ok"
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == "search"
    assert result.source_records != ()
    record = result.source_records[0]
    assert record.method == "search"
    assert record.provider == "live-web"
    assert record.proof_type == "observed"
    # No raw/private leakage in the public projection.
    rendered = result.public_projection()
    assert rendered["sourceRecords"][0]["url"] == "[redacted]"
    assert rendered["authorityFlags"]["networkFetched"] is False


def test_happy_path_fetch_returns_opened_proof() -> None:
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionProviderPack,
    )

    provider = SpyStubLiveProvider()
    pack = LiveWebAcquisitionProviderPack(_live_config())

    result = pack.run(
        _request(operation="fetch", url="https://docs.example.com/current"),
        provider=provider,
    )

    assert result.status == "ok"
    assert provider.calls[0][0] == "fetch"
    assert result.source_records[0].proof_type == "opened"


def test_reader_operation_ssrf_blocked_url_does_not_call_provider() -> None:
    """reader requests with blocked URLs must be rejected before reaching the provider."""
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionProviderPack,
    )

    provider = SpyStubLiveProvider()
    pack = LiveWebAcquisitionProviderPack(_live_config())

    metadata = pack.run(
        _request(operation="reader", url="http://169.254.169.254/latest/meta-data"),
        provider=provider,
    )
    private = pack.run(
        _request(operation="reader", url="http://10.0.0.1/internal"),
        provider=provider,
    )

    assert metadata.status == "blocked"
    assert metadata.reason_codes == ("metadata_url_blocked",)
    assert private.status == "blocked"
    assert private.reason_codes == ("private_url_blocked",)
    assert provider.calls == []


def test_empty_allowlist_with_live_network_enabled_is_denied() -> None:
    """An empty provider_allowlist on the live path must DENY, not allow-all.

    Operators who enable live_network_enabled but omit provider_allowlist must
    get 'provider_allowlist_required', not an open door to any provider name.
    """
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionPackConfig,
        LiveWebAcquisitionProviderPack,
    )

    provider = SpyStubLiveProvider()
    pack = LiveWebAcquisitionProviderPack(
        LiveWebAcquisitionPackConfig(enabled=True, liveNetworkEnabled=True)
        # providerAllowlist intentionally omitted → defaults to ()
    )

    result = pack.run(_request(providerName="live-web"), provider=provider)

    assert result.status == "blocked"
    assert result.reason_codes == ("provider_allowlist_required",)
    assert provider.calls == []
