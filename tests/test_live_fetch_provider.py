from __future__ import annotations

import json

import httpx
import pytest

from magi_agent.web_acquisition.live_fetch_provider import (
    HONEST_UA,
    LiveFetchProvider,
    redact_metadata_values,
    resolve_validated_ip,
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


def test_resolve_validated_ip_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    def _resolves_to(ip: str):  # type: ignore[no-untyped-def]
        def _fake(host, *args, **kwargs):  # type: ignore[no-untyped-def]
            return [(2, 1, 6, "", (ip, 0))]

        return _fake

    # Public IP allowed: reason is None and ip is returned.
    monkeypatch.setattr(
        "magi_agent.web_acquisition.live_fetch_provider.socket.getaddrinfo",
        _resolves_to("93.184.216.34"),
    )
    reason, ip = resolve_validated_ip("docs.example.com")
    assert reason is None
    assert ip == "93.184.216.34"

    # Private/metadata/CGNAT/reserved/loopback IPs are all blocked.
    for blocked_ip in ("10.0.0.5", "192.168.1.1", "169.254.169.254", "100.64.0.1", "127.0.0.1", "240.0.0.1"):
        monkeypatch.setattr(
            "magi_agent.web_acquisition.live_fetch_provider.socket.getaddrinfo",
            _resolves_to(blocked_ip),
        )
        reason, ip = resolve_validated_ip("public.example.com")
        assert reason is not None, blocked_ip
        assert ip is None, blocked_ip


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


def _host_map_getaddrinfo(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, str]) -> None:
    """Monkeypatch getaddrinfo to resolve hosts per ``mapping`` (host → IP)."""

    def _fake(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        ip = mapping.get(host)
        if ip is None:
            import socket as _socket

            raise _socket.gaierror("unknown host")
        return [(2, 1, 6, "", (ip, 0))]

    monkeypatch.setattr(
        "magi_agent.web_acquisition.live_fetch_provider.socket.getaddrinfo",
        _fake,
    )


# -- C1: redirect-based SSRF bypass --------------------------------------------


def test_redirect_to_internal_ip_is_denied_and_never_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Public landing host resolves public; the internal-IP redirect target host
    # ("169.254.169.254") resolves to itself (the metadata IP).
    _host_map_getaddrinfo(
        monkeypatch,
        {
            "landing.example.com": "93.184.216.34",
            "169.254.169.254": "169.254.169.254",
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "169.254.169.254":  # pragma: no cover
            raise AssertionError("internal redirect target must never be requested")
        # First (and only allowed) hop 302s toward the metadata service.
        return httpx.Response(
            302,
            headers={"location": "http://169.254.169.254/latest/meta-data/"},
        )

    client, spy = _client_with(handler)
    provider = LiveFetchProvider(client=client)

    output = provider.fetch(_request(url="https://landing.example.com/start"))

    assert output["status"] == "denied"
    # Only the initial public hop was ever issued; the internal target never was.
    assert len(spy.requests) == 1
    for req in spy.requests:
        assert req.url.host not in {"169.254.169.254"}


def test_legal_public_redirect_chain_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _host_map_getaddrinfo(
        monkeypatch,
        {
            "a.example.com": "93.184.216.34",
            "b.example.com": "93.184.216.35",
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("host", "").startswith("a.example.com") or "a.example.com" in str(
            request.url
        ):
            return httpx.Response(302, headers={"location": "https://b.example.com/final"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>Final</title></head><body><p>Arrived.</p></body></html>",
        )

    client, spy = _client_with(handler)
    provider = LiveFetchProvider(client=client)

    output = provider.fetch(_request(url="https://a.example.com/start"))

    # Success records carry no "status" key (only denials/timeouts do).
    assert output.get("status") != "denied"
    assert "Arrived" in str(output["content"])
    # Two guarded hops were issued (302 then 200).
    assert len(spy.requests) == 2


def test_redirect_hop_cap_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    _host_map_getaddrinfo(monkeypatch, {"loop.example.com": "93.184.216.34"})

    def handler(request: httpx.Request) -> httpx.Response:
        # Always redirect back to a public host → exhausts the hop cap.
        return httpx.Response(302, headers={"location": "https://loop.example.com/next"})

    client, spy = _client_with(handler)
    provider = LiveFetchProvider(client=client)

    output = provider.fetch(_request(url="https://loop.example.com/start"))

    assert output["status"] == "denied"
    assert "redirect" in str(output["reason"]).lower()
    # Bounded number of hops, not infinite.
    assert 1 <= len(spy.requests) <= 6


# -- I1: Cloudflare honest-UA retry travels the guarded path -------------------


def test_cloudflare_retry_is_pinned_to_validated_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    _host_map_getaddrinfo(monkeypatch, {"cf.example.com": "93.184.216.34"})
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
            text="<html><head><title>OK</title></head><body><p>through</p></body></html>",
        )

    client, spy = _client_with(handler)
    provider = LiveFetchProvider(client=client)

    output = provider.fetch(_request(url="https://cf.example.com/x"))

    assert state["calls"] == 2
    # Both attempts went out pinned to the validated IP (closes I1 — the retry
    # is re-guarded + re-pinned, not a raw hostname request).
    for req in spy.requests:
        assert req.url.host == "93.184.216.34"
        assert req.headers.get("host", "").startswith("cf.example.com")
    assert "through" in str(output["content"])


# -- I2: streamed size cap aborts early ----------------------------------------


def test_streamed_body_over_cap_aborts_early(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)
    emitted = {"bytes": 0}
    cap = 1000

    def _gen():  # type: ignore[no-untyped-def]
        # Emit far more than the cap in chunks; assert we stop reading early.
        for _ in range(1000):
            chunk = b"A" * 1024
            emitted["bytes"] += len(chunk)
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        # No content-length → must rely on streamed accumulation, not pre-check.
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=_gen(),
        )

    client, _spy = _client_with(handler)
    provider = LiveFetchProvider(client=client, max_content_bytes=cap)

    output = provider.fetch(_request())

    assert output["status"] == "denied"
    assert "too_large" in str(output["reason"]).lower() or "size" in str(output["reason"]).lower()
    # We stopped early: nowhere near the full ~1MB body was pulled.
    assert emitted["bytes"] < 200_000


# -- C2: pinning chooses a validated IP / Host preserved / all-blocked denied ---


def test_pinning_uses_validated_ip_and_preserves_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _host_map_getaddrinfo(monkeypatch, {"pin.example.com": "93.184.216.34"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>Pinned</title></head><body><p>ok</p></body></html>",
        )

    client, spy = _client_with(handler)
    provider = LiveFetchProvider(client=client)

    output = provider.fetch(_request(url="https://pin.example.com/p"))

    assert output.get("status") != "denied"
    assert len(spy.requests) == 1
    req = spy.requests[0]
    # Connection pinned to the validated IP; Host header preserves the hostname.
    assert req.url.host == "93.184.216.34"
    assert req.headers.get("host", "").startswith("pin.example.com")


def test_pinning_all_resolved_ips_blocked_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        # Mix of public + private; ANY blocked address poisons the resolution.
        return [(2, 1, 6, "", ("93.184.216.34", 0)), (2, 1, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(
        "magi_agent.web_acquisition.live_fetch_provider.socket.getaddrinfo",
        _fake,
    )

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not connect when any resolved IP is blocked")

    client, spy = _client_with(handler)
    provider = LiveFetchProvider(client=client)

    output = provider.fetch(_request(url="https://mixed.example.com/x"))

    assert output["status"] == "denied"
    assert spy.requests == []


def test_resolve_validated_ip_returns_validated_ip() -> None:
    # Helper returns (reason, ip): success → (None, ip), blocked → (reason, None).
    import socket as _socket

    import magi_agent.web_acquisition.live_fetch_provider as mod

    orig = mod.socket.getaddrinfo
    try:
        mod.socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
        reason, ip = resolve_validated_ip("ok.example.com")
        assert reason is None
        assert ip == "93.184.216.34"

        mod.socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 0))]
        reason, ip = resolve_validated_ip("evil.example.com")
        assert reason is not None
        assert ip is None
    finally:
        mod.socket.getaddrinfo = orig
        _ = _socket


def test_require_pinned_egress_refuses_unpinnable_https(monkeypatch: pytest.MonkeyPatch) -> None:
    # An IP-literal https host has no DNS layer to pin beyond itself; with the
    # strict flag set, the provider refuses rather than accept residual TOCTOU.
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("strict mode must refuse before connecting")

    client, spy = _client_with(handler)
    provider = LiveFetchProvider(client=client, require_pinned_egress=True)

    # 93.184.216.34 is public, but pinned_ip == host → unpinnable under strict.
    output = provider.fetch(_request(url="https://93.184.216.34/x"))
    assert output["status"] == "denied"
    assert "unpinnable" in str(output["reason"]).lower()
    assert spy.requests == []


# -- M1: injected client must not auto-redirect --------------------------------


def test_injected_client_redirects_are_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _host_map_getaddrinfo(
        monkeypatch,
        {"r.example.com": "93.184.216.34", "169.254.169.254": "169.254.169.254"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "169.254.169.254":  # pragma: no cover
            raise AssertionError("auto-redirect to internal IP must not happen")
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    spy = _TransportSpy(handler)
    # Operator/test builds the client with auto-redirects ON — provider must
    # defensively override it to False.
    client = httpx.Client(transport=spy, follow_redirects=True)  # type: ignore[arg-type]
    provider = LiveFetchProvider(client=client)
    assert client.follow_redirects is False  # overridden at construction

    output = provider.fetch(_request(url="https://r.example.com/start"))
    assert output["status"] == "denied"
    assert all(req.url.host != "169.254.169.254" for req in spy.requests)


# -- M2: page-controlled <title> host leak -------------------------------------


def test_title_host_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>http://10.0.0.1/admin secret.internal</title></head>"
            "<body><p>body</p></body></html>",
        )

    client, _spy = _client_with(handler)
    provider = LiveFetchProvider(client=client)

    output = provider.fetch(_request())
    title = str(output["title"])
    assert "10.0.0.1" not in title
    assert "secret.internal" not in title
    assert "http://" not in title
