"""Tests for FakeLiveProvider (PR-B, providers/fake_provider.py)."""

from __future__ import annotations


def test_fake_live_provider_default_search_returns_results() -> None:
    from magi_agent.web_acquisition.providers.fake_provider import FakeLiveProvider

    p = FakeLiveProvider()
    result = p.search(None)
    assert "results" in result
    assert len(result["results"]) > 0


def test_fake_live_provider_timeout_search() -> None:
    from magi_agent.web_acquisition.providers.fake_provider import FakeLiveProvider

    p = FakeLiveProvider(search_status="timeout")
    result = p.search(None)
    assert result.get("status") == "timeout"


def test_fake_live_provider_denied_search() -> None:
    from magi_agent.web_acquisition.providers.fake_provider import FakeLiveProvider

    p = FakeLiveProvider(search_status="denied")
    result = p.search(None)
    assert result.get("status") == "denied"


def test_fake_live_provider_fetch_default_ok() -> None:
    from magi_agent.web_acquisition.providers.fake_provider import FakeLiveProvider

    p = FakeLiveProvider()
    result = p.fetch(None)
    assert "content" in result
    assert result.get("status") is None  # No status key on success


def test_fake_live_provider_fetch_timeout() -> None:
    from magi_agent.web_acquisition.providers.fake_provider import FakeLiveProvider

    p = FakeLiveProvider(fetch_status="timeout")
    result = p.fetch(None)
    assert result.get("status") == "timeout"


def test_fake_live_provider_reader_ok() -> None:
    from magi_agent.web_acquisition.providers.fake_provider import FakeLiveProvider

    p = FakeLiveProvider(reader_content="custom reader text")
    result = p.reader(None)
    assert result.get("content") == "custom reader text"


def test_fake_live_provider_call_log() -> None:
    from magi_agent.web_acquisition.providers.fake_provider import FakeLiveProvider

    log: list[str] = []
    p = FakeLiveProvider(call_log=log)
    p.search(None)
    p.fetch(None)
    p.reader(None)
    assert log == ["search", "fetch", "reader"]


def test_fake_live_provider_raise_on_search() -> None:
    import pytest
    from magi_agent.web_acquisition.providers.fake_provider import FakeLiveProvider

    p = FakeLiveProvider(raise_on=frozenset({"search"}))
    with pytest.raises(RuntimeError):
        p.search(None)


def test_fake_live_provider_has_live_marker() -> None:
    from magi_agent.web_acquisition.providers.fake_provider import FakeLiveProvider

    p = FakeLiveProvider()
    assert p.openmagi_live_provider is True


def test_fake_live_provider_is_accepted_by_live_pack() -> None:
    """FakeLiveProvider must pass the live-gate check in LiveWebAcquisitionProviderPack."""
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionPackConfig,
        LiveWebAcquisitionProviderPack,
        WebAcquisitionProviderRequest,
    )
    from magi_agent.web_acquisition.providers.fake_provider import FakeLiveProvider

    pack = LiveWebAcquisitionProviderPack(
        LiveWebAcquisitionPackConfig(
            enabled=True,
            liveNetworkEnabled=True,
            providerAllowlist=("fake-live",),
        )
    )
    provider = FakeLiveProvider()
    request = WebAcquisitionProviderRequest(
        operation="search",
        requestId="test-1",
        providerName="fake-live",
        botIdDigest="bot:abc",
        ownerIdDigest="owner:def",
        sessionKeyDigest="session:ghi",
        query="test query",
    )
    result = pack.run(request, provider=provider)
    assert result.status == "ok"
    assert result.source_records != ()
