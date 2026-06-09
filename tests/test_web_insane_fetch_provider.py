"""Tests for InsaneFetchProvider (curl_cffi WAF-bypass).

Hermetic — no live network and curl_cffi need NOT be installed. HTTP egress is
intercepted via an injected fake session; DNS resolution is monkeypatched so the
shared ``resolve_validated_ip`` returns a public IP (mirroring the jina /
live_fetch test pattern exactly).
"""

from __future__ import annotations

import pytest


_PUBLIC_IP = "93.184.216.34"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _allow_public_host(monkeypatch: pytest.MonkeyPatch, ip: str = _PUBLIC_IP) -> None:
    """Monkeypatch live_fetch_provider's socket.getaddrinfo → resolve any host to ``ip``."""

    def _fake_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        return [(2, 1, 6, "", (ip, 0))]

    monkeypatch.setattr(
        "magi_agent.web_acquisition.live_fetch_provider.socket.getaddrinfo",
        _fake_getaddrinfo,
    )


class _FakeResponse:
    """Canned response duck-type matching curl_cffi.requests.Response.

    ``iter_content`` is only attached when ``iter_chunks`` is supplied, so we can
    exercise BOTH the streaming path and the ``.content`` fallback path.
    """

    def __init__(
        self,
        *,
        status_code: int,
        headers: dict[str, str] | None = None,
        content: bytes = b"",
        iter_chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        if iter_chunks is not None:
            chunks = list(iter_chunks)

            def _iter_content(_chunk_size: int | None = None):  # type: ignore[no-untyped-def]
                yield from chunks

            self.iter_content = _iter_content  # type: ignore[attr-defined]


class _FakeSession:
    """Records each .get() call and returns queued canned responses.

    ``responses`` may be a single response (returned for every call) or a list
    consumed in order. ``raises`` (when set) is raised on the first call.
    """

    def __init__(
        self,
        responses: _FakeResponse | list[_FakeResponse] | None = None,
        *,
        raises: BaseException | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self._raises = raises
        if isinstance(responses, list):
            self._queue = list(responses)
            self._single = None
        else:
            self._queue = None
            self._single = responses

    def get(self, url, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append({"url": url, **kwargs})
        if self._raises is not None:
            raise self._raises
        if self._queue is not None:
            return self._queue.pop(0)
        return self._single


class _Req:
    """Minimal request duck-type for provider.fetch()."""

    def __init__(self, url: str | None) -> None:
        self.url = url


def _provider(session: object, **kwargs):  # type: ignore[no-untyped-def]
    from magi_agent.web_acquisition.providers.insane_fetch import InsaneFetchProvider

    return InsaneFetchProvider(session=session, **kwargs)


# ---------------------------------------------------------------------------
# Success path + pin/impersonate assertions
# ---------------------------------------------------------------------------


def test_200_html_returns_normalized_and_pins_ip_and_impersonates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_public_host(monkeypatch)
    html = b"<html><head><title>Hello World</title></head><body><p>Body text</p></body></html>"
    session = _FakeSession(
        _FakeResponse(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=html,
        )
    )
    provider = _provider(session, impersonate="chrome")
    result = provider.fetch(_Req("https://example.com/page"))

    assert "status" not in result, f"Unexpected error status: {result}"
    assert result["url"] == "https://example.com/page"
    assert result["title"] == "Hello World"
    assert "Body text" in str(result["content"])

    # Exactly one egress call, pinned to the validated IP and impersonating.
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["resolve"] == [f"example.com:443:{_PUBLIC_IP}"], call["resolve"]
    assert call["impersonate"] == "chrome"
    assert call["allow_redirects"] is False


def test_200_uses_explicit_port_in_resolve_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)
    session = _FakeSession(
        _FakeResponse(status_code=200, headers={"content-type": "text/plain"}, content=b"ok")
    )
    provider = _provider(session)
    provider.fetch(_Req("https://example.com:8443/page"))

    assert session.calls[0]["resolve"] == [f"example.com:8443:{_PUBLIC_IP}"]


def test_200_plain_text_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)
    session = _FakeSession(
        _FakeResponse(
            status_code=200,
            headers={"content-type": "text/plain"},
            content=b"plain text body",
        )
    )
    provider = _provider(session)
    result = provider.fetch(_Req("https://example.com/page"))
    assert "status" not in result
    assert "plain text body" in str(result["content"])
    assert "url" in result and "title" in result and "metadata" in result


# ---------------------------------------------------------------------------
# SSRF: blocked target → no egress at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/internal",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/secret",
    ],
)
def test_ssrf_target_denied_before_any_egress(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    _allow_public_host(monkeypatch)
    session = _FakeSession(
        _FakeResponse(status_code=200, headers={"content-type": "text/plain"}, content=b"x")
    )
    provider = _provider(session)
    result = provider.fetch(_Req(url))

    assert result["status"] == "denied", result
    assert session.calls == [], "No egress must occur for an SSRF target"


def test_ssrf_dns_rebind_to_private_ip_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """Public-looking host that RESOLVES to a private IP → denied, no egress."""
    _allow_public_host(monkeypatch, ip="10.1.2.3")
    session = _FakeSession(
        _FakeResponse(status_code=200, headers={"content-type": "text/plain"}, content=b"x")
    )
    provider = _provider(session)
    result = provider.fetch(_Req("https://evil.example.com/x"))

    assert result["status"] == "denied"
    assert session.calls == []


# ---------------------------------------------------------------------------
# Redirect handling (guard re-runs each hop)
# ---------------------------------------------------------------------------


def test_redirect_to_internal_ip_denied_and_not_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """302 → internal IP: the next-hop guard blocks; internal URL never fetched."""
    _allow_public_host(monkeypatch)
    redirect = _FakeResponse(
        status_code=302,
        headers={"content-type": "text/html", "location": "http://169.254.169.254/"},
        content=b"",
    )
    session = _FakeSession([redirect])
    provider = _provider(session)
    result = provider.fetch(_Req("https://example.com/start"))

    assert result["status"] == "denied", result
    # Only the first (public) hop was issued; the internal target was never hit.
    assert len(session.calls) == 1
    assert session.calls[0]["url"] == "https://example.com/start"


def test_redirect_to_public_url_follows_and_returns_final_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_public_host(monkeypatch)
    redirect = _FakeResponse(
        status_code=302,
        headers={"content-type": "text/html", "location": "https://example.org/final"},
        content=b"",
    )
    final = _FakeResponse(
        status_code=200,
        headers={"content-type": "text/plain"},
        content=b"final destination body",
    )
    session = _FakeSession([redirect, final])
    provider = _provider(session)
    result = provider.fetch(_Req("https://example.com/start"))

    assert "status" not in result, result
    assert "final destination body" in str(result["content"])
    assert len(session.calls) == 2
    assert session.calls[1]["url"] == "https://example.org/final"
    # The original requested url is preserved in the output.
    assert result["url"] == "https://example.com/start"


def test_too_many_redirects_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)
    # Always redirect to a fresh public URL → exceeds the hop cap.
    redirects = [
        _FakeResponse(
            status_code=302,
            headers={"content-type": "text/html", "location": f"https://example.com/h{i}"},
            content=b"",
        )
        for i in range(20)
    ]
    session = _FakeSession(redirects)
    provider = _provider(session)
    result = provider.fetch(_Req("https://example.com/start"))
    assert result["status"] == "denied"
    assert result["reason"] == "too_many_redirects"


# ---------------------------------------------------------------------------
# Size cap
# ---------------------------------------------------------------------------


def test_oversized_streamed_body_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)
    big = [b"a" * 1000, b"b" * 1000, b"c" * 1000]  # 3000 bytes
    session = _FakeSession(
        _FakeResponse(
            status_code=200,
            headers={"content-type": "text/plain"},
            iter_chunks=big,
        )
    )
    provider = _provider(session, max_content_bytes=1500)
    result = provider.fetch(_Req("https://example.com/big"))
    assert result["status"] == "denied"
    assert result["reason"] == "content_too_large"


def test_oversized_content_length_precheck_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)
    session = _FakeSession(
        _FakeResponse(
            status_code=200,
            headers={"content-type": "text/plain", "content-length": "9999999"},
            content=b"small body but big declared length",
        )
    )
    provider = _provider(session, max_content_bytes=1000)
    result = provider.fetch(_Req("https://example.com/big"))
    assert result["status"] == "denied"
    assert result["reason"] == "content_too_large"


def test_oversized_buffered_body_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """No iter_content → .content fallback still hard-caps the body length."""
    _allow_public_host(monkeypatch)
    session = _FakeSession(
        _FakeResponse(
            status_code=200,
            headers={"content-type": "text/plain"},
            content=b"x" * 5000,
        )
    )
    provider = _provider(session, max_content_bytes=1000)
    result = provider.fetch(_Req("https://example.com/big"))
    assert result["status"] == "denied"
    assert result["reason"] == "content_too_large"


# ---------------------------------------------------------------------------
# Transport / timeout / status mapping
# ---------------------------------------------------------------------------


def test_session_raises_returns_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)
    session = _FakeSession(raises=ConnectionError("connection reset"))
    provider = _provider(session)
    result = provider.fetch(_Req("https://example.com/x"))
    assert result["status"] == "timeout"
    assert result["content"] == ""


def test_403_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)
    session = _FakeSession(
        _FakeResponse(status_code=403, headers={"content-type": "text/html"}, content=b"nope")
    )
    provider = _provider(session)
    result = provider.fetch(_Req("https://example.com/x"))
    assert result["status"] == "denied"


def test_500_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)
    session = _FakeSession(
        _FakeResponse(status_code=500, headers={"content-type": "text/html"}, content=b"boom")
    )
    provider = _provider(session)
    result = provider.fetch(_Req("https://example.com/x"))
    assert result["status"] == "timeout"


def test_429_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_host(monkeypatch)
    session = _FakeSession(
        _FakeResponse(status_code=429, headers={"content-type": "text/html"}, content=b"slow")
    )
    provider = _provider(session)
    result = provider.fetch(_Req("https://example.com/x"))
    assert result["status"] == "timeout"


# ---------------------------------------------------------------------------
# Missing url
# ---------------------------------------------------------------------------


def test_missing_url_denied() -> None:
    provider = _provider(_FakeSession())
    result = provider.fetch(_Req(None))
    assert result["status"] == "denied"
    assert result["reason"] == "url_required"
    assert result["content"] == ""


def test_blank_url_denied() -> None:
    provider = _provider(_FakeSession())
    result = provider.fetch(_Req("   "))
    assert result["status"] == "denied"
    assert result["reason"] == "url_required"


def test_request_without_url_attr_denied() -> None:
    provider = _provider(_FakeSession())
    result = provider.fetch(object())
    assert result["status"] == "denied"
    assert result["reason"] == "url_required"


# ---------------------------------------------------------------------------
# curl_cffi unavailable (lazy import fails)
# ---------------------------------------------------------------------------


def test_curl_cffi_unavailable_returns_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """session=None and the lazy curl_cffi import raises ImportError → denied, never raises."""
    _allow_public_host(monkeypatch)

    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "curl_cffi" or name.startswith("curl_cffi."):
            raise ImportError("No module named 'curl_cffi'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    from magi_agent.web_acquisition.providers.insane_fetch import InsaneFetchProvider

    provider = InsaneFetchProvider()  # session=None → lazy import path
    result = provider.fetch(_Req("https://example.com/x"))
    assert result["status"] == "denied"
    assert result["reason"] == "curl_cffi_unavailable"
    assert result["content"] == ""


# ---------------------------------------------------------------------------
# live marker
# ---------------------------------------------------------------------------


def test_insane_fetch_provider_has_live_marker() -> None:
    from magi_agent.web_acquisition.providers.insane_fetch import InsaneFetchProvider

    provider = InsaneFetchProvider()
    assert provider.openmagi_live_provider is True


# ---------------------------------------------------------------------------
# never-raises backstop
# ---------------------------------------------------------------------------


def test_metadata_never_leaks_final_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The emitted output must not contain the raw final host/URL."""
    _allow_public_host(monkeypatch)
    session = _FakeSession(
        _FakeResponse(
            status_code=200,
            headers={"content-type": "text/plain"},
            content=b"body",
        )
    )
    provider = _provider(session)
    result = provider.fetch(_Req("https://secret-host.example.com/page"))
    meta = result.get("metadata", {})
    assert "secret-host.example.com" not in str(meta)
    assert str(meta.get("finalUrlRef", "")).startswith("url:")
