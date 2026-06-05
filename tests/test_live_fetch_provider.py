from __future__ import annotations

import json

import httpx
import pytest

from magi_agent.web_acquisition.live_fetch_provider import (
    HONEST_UA,
    LiveFetchProvider,
    redact_metadata_values,
    resolve_and_check_host,
)
from magi_agent.web_acquisition.live_provider_pack import (
    LiveWebAcquisitionPackConfig,
    LiveWebAcquisitionProviderPack,
    WebAcquisitionProviderRequest,
)


def _request(**overrides: object) -> WebAcquisitionProviderRequest:
    payload: dict[str, object] = {
        "operation": "fetch",
        "requestId": "web-1",
        "providerName": "live-fetch",
        "botIdDigest": "bot:abc",
        "ownerIdDigest": "owner:def",
        "sessionKeyDigest": "session:ghi",
        "url": "https://docs.example.com/article",
    }
    payload.update(overrides)
    return WebAcquisitionProviderRequest(**payload)


def _pack_config(**overrides: object) -> LiveWebAcquisitionPackConfig:
    payload: dict[str, object] = {
        "enabled": True,
        "liveNetworkEnabled": True,
        "providerAllowlist": ("live-fetch",),
    }
    payload.update(overrides)
    return LiveWebAcquisitionPackConfig(**payload)


def _allow_public_host(monkeypatch: pytest.MonkeyPatch, ip: str = "93.184.216.34") -> None:
    def _fake_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        return [(2, 1, 6, "", (ip, 0))]

    monkeypatch.setattr(
        "magi_agent.web_acquisition.live_fetch_provider.socket.getaddrinfo",
        _fake_getaddrinfo,
    )


class _TransportSpy:
    def __init__(self, handler) -> None:  # type: ignore[no-untyped-def]
        self._transport = httpx.MockTransport(handler)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._transport.handle_request(request)


def _client_with(handler) -> tuple[httpx.Client, _TransportSpy]:  # type: ignore[no-untyped-def]
    spy = _TransportSpy(handler)
    client = httpx.Client(transport=spy)  # type: ignore[arg-type]
    return client, spy


def test_html_fetch_returns_markdown_and_ok_record(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><head><title>Doc Title</title>"
            "<style>.x{color:red}</style></head>"
            "<body><h1>Heading</h1><p>Hello <b>world</b>.</p>"
            "<script>alert(1)</script></body></html>",
        )

    client, spy = _client_with(handler)
    provider = LiveFetchProvider(client=client)
    pack = LiveWebAcquisitionProviderPack(_pack_config())

    result = pack.run(_request(), provider=provider)

    assert result.status == "ok"
    assert len(spy.requests) == 1
    record = result.source_records[0]
    assert record.title == "Doc Title"
    assert "Heading" in (record.public_preview or "")
    assert "world" in (record.public_preview or "")
    # script/style stripped
    assert "alert(1)" not in (record.public_preview or "")
    assert "color:red" not in (record.public_preview or "")


def test_dns_rebinding_blocks_before_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    # Public-looking host that resolves to the cloud metadata IP.
    _allow_public_host(monkeypatch, ip="169.254.169.254")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("transport must not be called when host is blocked")

    client, spy = _client_with(handler)
    provider = LiveFetchProvider(client=client)

    output = provider.fetch(_request(url="https://metadata.evil.example.com/x"))

    assert output["status"] == "denied"
    assert "dns" in str(output["reason"]).lower() or "metadata" in str(output["reason"]).lower()
    assert spy.requests == []

    # And the pack maps the denial to no_answer.
    pack = LiveWebAcquisitionProviderPack(_pack_config())
    result = pack.run(_request(url="https://metadata.evil.example.com/x"), provider=provider)
    assert result.status == "no_answer"


def test_resolve_and_check_host_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    def _resolves_to(ip: str):  # type: ignore[no-untyped-def]
        def _fake(host, *args, **kwargs):  # type: ignore[no-untyped-def]
            return [(2, 1, 6, "", (ip, 0))]

        return _fake

    # Public IP allowed.
    monkeypatch.setattr(
        "magi_agent.web_acquisition.live_fetch_provider.socket.getaddrinfo",
        _resolves_to("93.184.216.34"),
    )
    assert resolve_and_check_host("docs.example.com") is None

    for blocked_ip in ("10.0.0.5", "192.168.1.1", "169.254.169.254", "100.64.0.1", "127.0.0.1", "240.0.0.1"):
        monkeypatch.setattr(
            "magi_agent.web_acquisition.live_fetch_provider.socket.getaddrinfo",
            _resolves_to(blocked_ip),
        )
        assert resolve_and_check_host("public.example.com") is not None, blocked_ip


def test_cloudflare_challenge_retries_with_honest_ua(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            return httpx.Response(
                403,
                headers={"cf-mitigated": "challenge", "content-type": "text/html"},
                text="<html><body>blocked</body></html>",
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>Real</title></head><body><p>Allowed now.</p></body></html>",
        )

    client, spy = _client_with(handler)
    provider = LiveFetchProvider(client=client)

    output = provider.fetch(_request())

    assert state["calls"] == 2
    # Second request used the honest opencode-style UA.
    assert spy.requests[1].headers.get("user-agent") == HONEST_UA
    assert spy.requests[0].headers.get("user-agent") != HONEST_UA
    assert "Allowed now" in str(output["content"])


def test_size_cap_content_length_returns_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain", "content-length": "9999999"},
            text="x",
        )

    client, _spy = _client_with(handler)
    provider = LiveFetchProvider(client=client, max_content_bytes=1000)

    output = provider.fetch(_request())
    assert output["status"] == "denied"
    assert "size" in str(output["reason"]).lower() or "too_large" in str(output["reason"]).lower()


def test_size_cap_streamed_body_returns_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        # No content-length, but a big body.
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="A" * 5000)

    client, _spy = _client_with(handler)
    provider = LiveFetchProvider(client=client, max_content_bytes=1000)

    output = provider.fetch(_request())
    assert output["status"] == "denied"


def test_connect_error_is_wrapped_as_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client, _spy = _client_with(handler)
    provider = LiveFetchProvider(client=client)

    output = provider.fetch(_request())
    assert output["status"] in {"timeout", "denied"}
    # Pack maps timeout -> repair_required, denied -> no_answer; both non-ok, no raise.
    pack = LiveWebAcquisitionProviderPack(_pack_config())
    result = pack.run(_request(), provider=provider)
    assert result.status in {"repair_required", "no_answer"}


def test_timeout_exception_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client, _spy = _client_with(handler)
    provider = LiveFetchProvider(client=client)

    output = provider.fetch(_request())
    assert output["status"] == "timeout"


def test_metadata_values_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>T</title></head><body><p>hi</p></body></html>",
        )

    client, _spy = _client_with(handler)
    provider = LiveFetchProvider(client=client)

    output = provider.fetch(_request(url="https://internal-host.corp.example.com/secret-path"))
    encoded = json.dumps(dict(output), sort_keys=True)
    # Raw final URL / host must never appear in metadata.
    assert "internal-host.corp.example.com" not in json.dumps(output.get("metadata", {}), sort_keys=True)
    # Projected through the pack, still redacted.
    pack = LiveWebAcquisitionProviderPack(_pack_config())
    result = pack.run(_request(url="https://internal-host.corp.example.com/secret-path"), provider=provider)
    projection = json.dumps(result.public_projection(), sort_keys=True)
    assert "internal-host.corp.example.com" not in projection
    _ = encoded


def test_redact_metadata_values_strips_hostnames_and_secrets() -> None:
    meta = {
        "contentType": "text/html",
        "statusCode": 200,
        "note": "see https://leak.example.com/path?token=abc and host evil.internal here",
        "bearer": "Bearer abcdefgh12345678",
    }
    redacted = redact_metadata_values(meta)
    blob = json.dumps(redacted, sort_keys=True)
    assert "leak.example.com" not in blob
    assert "evil.internal" not in blob
    assert "abcdefgh12345678" not in blob
    assert redacted["contentType"] == "text/html"
    assert redacted["statusCode"] == 200
