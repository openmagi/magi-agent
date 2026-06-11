"""B1 TDD tests — out-of-box Slack provider (stdlib urllib, no injection needed).

Closes the DD gap: "channels have no out-of-box send path; Slack has a gated
adapter but requires the operator to inject a SlackProviderPort".

Covers:
- gate ON + SLACK_BOT_TOKEN: default provider is built and posts the correct
  chat.postMessage payload shape (Authorization: Bearer, JSON body)
- token missing: NO provider (shadow/receipt-only behaviour unchanged)
- gate OFF: byte-identical to today (gate_off skip, no network)
- Slack API error response / network error: fail-soft strings, never raises
- token never appears in evidence or error strings (redaction)
- injected port still wins over the env fallback
- egress proxy conventions: enabled-but-invalid config fails soft and NEVER
  bypasses the proxy with direct egress

All network is faked via monkeypatched ``urllib.request.urlopen`` — no real
egress in tests, mirroring tests/test_slack_email_live_e4.py conventions.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

import pytest

_GATE_ENV = "MAGI_CHANNEL_LIVE_SLACK"
_TOKEN_ENV = "SLACK_BOT_TOKEN"
_TOKEN_ENV_MAGI = "MAGI_SLACK_BOT_TOKEN"

# Assembled at runtime so secret scanners never see a token-shaped literal.
_FAKE_TOKEN = "-".join(["xoxb", "0001", "testonlyfaketokenvalue0001"])


# ---------------------------------------------------------------------------
# Helpers — fake urlopen
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


def _install_fake_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any] | None = None,
    *,
    error: Exception | None = None,
) -> list[Any]:
    """Patch urllib.request.urlopen; return the list of captured requests."""
    calls: list[Any] = []

    def fake_urlopen(request: Any, *args: Any, **kwargs: Any) -> _FakeResponse:
        calls.append(request)
        if error is not None:
            raise error
        return _FakeResponse(payload if payload is not None else {"ok": True, "ts": "1.0"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        _GATE_ENV,
        _TOKEN_ENV,
        _TOKEN_ENV_MAGI,
        "MAGI_EGRESS_PROXY_ENABLED",
        "MAGI_EGRESS_PROXY_URL",
        "MAGI_EGRESS_PROXY_AUTH",
        "MAGI_EGRESS_PROXY_CA_CERT_PATH",
    ):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# B1-1: Default provider construction (fail-closed)
# ---------------------------------------------------------------------------

def test_builder_returns_provider_when_gate_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(_GATE_ENV, "1")
    monkeypatch.setenv(_TOKEN_ENV, _FAKE_TOKEN)
    from magi_agent.channels.providers.slack_urllib import (
        SlackUrllibProvider,
        build_default_slack_provider,
    )

    provider = build_default_slack_provider()
    assert isinstance(provider, SlackUrllibProvider)
    # Live provider must never masquerade as the audit fake.
    assert provider.openmagi_local_fake_provider is False


def test_builder_accepts_magi_prefixed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(_GATE_ENV, "1")
    monkeypatch.setenv(_TOKEN_ENV_MAGI, _FAKE_TOKEN)
    from magi_agent.channels.providers.slack_urllib import build_default_slack_provider

    assert build_default_slack_provider() is not None


def test_builder_returns_none_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(_GATE_ENV, "1")
    from magi_agent.channels.providers.slack_urllib import build_default_slack_provider

    assert build_default_slack_provider() is None


def test_builder_returns_none_when_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(_TOKEN_ENV, _FAKE_TOKEN)
    from magi_agent.channels.providers.slack_urllib import build_default_slack_provider

    assert build_default_slack_provider() is None


def test_provider_requires_token() -> None:
    from magi_agent.channels.providers.slack_urllib import SlackUrllibProvider

    with pytest.raises(ValueError):
        SlackUrllibProvider(token="   ")


# ---------------------------------------------------------------------------
# B1-2: Provider send — correct payload shape, no real network
# ---------------------------------------------------------------------------

def test_send_posts_chat_post_message_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    calls = _install_fake_urlopen(monkeypatch, {"ok": True, "ts": "123.456"})
    from magi_agent.channels.providers.slack_urllib import SlackUrllibProvider

    provider = SlackUrllibProvider(token=_FAKE_TOKEN)
    result = provider.send(channel="#general", text="hello world")

    assert result["ok"] is True
    assert len(calls) == 1
    request = calls[0]
    assert request.full_url == "https://slack.com/api/chat.postMessage"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {_FAKE_TOKEN}"
    assert "application/json" in request.get_header("Content-type", "")
    body = json.loads(request.data.decode("utf-8"))
    assert body["channel"] == "#general"
    assert body["text"] == "hello world"
    assert provider.provider_called is True


def test_send_passes_scalar_kwargs_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    calls = _install_fake_urlopen(monkeypatch)
    from magi_agent.channels.providers.slack_urllib import SlackUrllibProvider

    provider = SlackUrllibProvider(token=_FAKE_TOKEN)
    provider.send(
        channel="C012AB3CD",
        text="hi",
        thread_ts="111.222",
        nested={"not": "allowed"},
    )

    body = json.loads(calls[0].data.decode("utf-8"))
    assert body["thread_ts"] == "111.222"
    assert "nested" not in body


# ---------------------------------------------------------------------------
# B1-3: Fail-soft error handling (never raises into the channel path)
# ---------------------------------------------------------------------------

def test_send_api_error_response_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    _install_fake_urlopen(monkeypatch, {"ok": False, "error": "channel_not_found"})
    from magi_agent.channels.providers.slack_urllib import SlackUrllibProvider

    provider = SlackUrllibProvider(token=_FAKE_TOKEN)
    result = provider.send(channel="#nope", text="hi")

    assert result["ok"] is False
    assert "channel_not_found" in str(result.get("error", ""))


def test_send_network_error_fail_soft_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    _install_fake_urlopen(
        monkeypatch, error=OSError(f"connect failed for token {_FAKE_TOKEN}")
    )
    from magi_agent.channels.providers.slack_urllib import SlackUrllibProvider

    provider = SlackUrllibProvider(token=_FAKE_TOKEN)
    result = provider.send(channel="#general", text="hi")

    assert result["ok"] is False
    assert _FAKE_TOKEN not in str(result), "token must never leak into error strings"


def test_send_non_json_response_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)

    class _BadResponse:
        def read(self) -> bytes:
            return b"<html>gateway error</html>"

        def __enter__(self) -> "_BadResponse":
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _BadResponse()
    )
    from magi_agent.channels.providers.slack_urllib import SlackUrllibProvider

    provider = SlackUrllibProvider(token=_FAKE_TOKEN)
    result = provider.send(channel="#general", text="hi")
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# B1-4: deliver() fallback wiring — port=None resolves the env provider
# ---------------------------------------------------------------------------

def test_deliver_port_none_falls_back_and_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(_GATE_ENV, "1")
    monkeypatch.setenv(_TOKEN_ENV, _FAKE_TOKEN)
    calls = _install_fake_urlopen(monkeypatch, {"ok": True, "ts": "9.9"})
    from magi_agent.channels.slack_live import deliver

    evidence: dict[str, object] = {}
    result = deliver(None, "#general", "out of box", evidence=evidence)

    assert result is True
    assert len(calls) == 1
    body = json.loads(calls[0].data.decode("utf-8"))
    assert body == {"channel": "#general", "text": "out of box"}
    assert evidence.get("deliverOk") is True
    assert _FAKE_TOKEN not in str(evidence), "token must never appear in evidence"


def test_deliver_port_none_without_token_is_shadow_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(_GATE_ENV, "1")
    calls = _install_fake_urlopen(monkeypatch)
    from magi_agent.channels.slack_live import deliver

    evidence: dict[str, object] = {}
    result = deliver(None, "#general", "hello", evidence=evidence)

    assert result is False
    assert calls == [], "no token must mean no provider and no egress"
    assert evidence.get("deliverSkipped") is True
    assert evidence.get("deliverSkipReason") == "no_provider"


def test_deliver_port_none_gate_off_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(_TOKEN_ENV, _FAKE_TOKEN)  # token alone must NOT enable sends
    calls = _install_fake_urlopen(monkeypatch)
    from magi_agent.channels.slack_live import deliver

    evidence: dict[str, object] = {}
    result = deliver(None, "#general", "hello", evidence=evidence)

    assert result is False
    assert calls == []
    assert evidence.get("deliverSkipped") is True
    assert evidence.get("deliverSkipReason") == "gate_off"


def test_deliver_injected_port_wins_over_env_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(_GATE_ENV, "1")
    monkeypatch.setenv(_TOKEN_ENV, _FAKE_TOKEN)
    calls = _install_fake_urlopen(monkeypatch)
    from magi_agent.channels.slack_live import deliver

    class _Injected:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []

        def send(self, *, channel: str, text: str, **kwargs: Any) -> dict[str, object]:
            self.sent.append({"channel": channel, "text": text})
            return {"ok": True}

    injected = _Injected()
    evidence: dict[str, object] = {}
    result = deliver(injected, "#general", "hello", evidence=evidence)

    assert result is True
    assert len(injected.sent) == 1
    assert calls == [], "injected port must be used; env fallback must not fire"


def test_deliver_silent_suppressed_before_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(_GATE_ENV, "1")
    monkeypatch.setenv(_TOKEN_ENV, _FAKE_TOKEN)
    calls = _install_fake_urlopen(monkeypatch)
    from magi_agent.channels.slack_live import deliver

    evidence: dict[str, object] = {}
    result = deliver(None, "#general", "[SILENT]", evidence=evidence)

    assert result is True
    assert calls == []
    assert evidence.get("deliverSuppressed") is True


def test_deliver_api_error_records_fail_soft_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(_GATE_ENV, "1")
    monkeypatch.setenv(_TOKEN_ENV, _FAKE_TOKEN)
    _install_fake_urlopen(monkeypatch, {"ok": False, "error": "invalid_auth"})
    from magi_agent.channels.slack_live import deliver

    evidence: dict[str, object] = {}
    result = deliver(None, "#general", "hello", evidence=evidence)

    assert result is True  # provider call happened; result honesty is in evidence
    assert evidence.get("deliverOk") is False
    assert "invalid_auth" in str(evidence.get("deliverError", ""))
    assert _FAKE_TOKEN not in str(evidence)


# ---------------------------------------------------------------------------
# B1-5: Egress proxy conventions (fail-closed — never bypass with direct egress)
# ---------------------------------------------------------------------------

def test_enabled_but_invalid_egress_proxy_fails_soft_no_direct_egress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_env(monkeypatch)
    calls = _install_fake_urlopen(monkeypatch)
    from magi_agent.channels.providers.slack_urllib import SlackUrllibProvider
    from magi_agent.egress_proxy.config import EgressProxyConfig

    cfg = EgressProxyConfig(enabled=True, proxy_url=None, proxy_auth=None)
    provider = SlackUrllibProvider(token=_FAKE_TOKEN, egress_config=cfg)
    result = provider.send(channel="#general", text="hello")

    assert result["ok"] is False
    assert calls == [], "invalid proxy config must NOT fall back to direct egress"


def test_valid_egress_proxy_routes_via_proxy_opener(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    _clean_env(monkeypatch)
    direct_calls = _install_fake_urlopen(monkeypatch)

    ca = tmp_path / "ca.pem"
    ca.write_text("dummy-ca")
    monkeypatch.setattr(
        "ssl.create_default_context", lambda *a, **k: object()
    )

    captured: dict[str, Any] = {}

    class _FakeOpener:
        def open(self, request: Any, *args: Any, **kwargs: Any) -> _FakeResponse:
            captured["request"] = request
            return _FakeResponse({"ok": True})

    def fake_build_opener(*handlers: Any) -> _FakeOpener:
        captured["handlers"] = handlers
        return _FakeOpener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)

    from magi_agent.channels.providers.slack_urllib import SlackUrllibProvider
    from magi_agent.egress_proxy.config import EgressProxyConfig

    cfg = EgressProxyConfig(
        enabled=True,
        proxy_url="http://egress-proxy.local:3128",
        proxy_auth=None,
        ca_cert_path=str(ca),
    )
    provider = SlackUrllibProvider(token=_FAKE_TOKEN, egress_config=cfg)
    result = provider.send(channel="#general", text="hello")

    assert result["ok"] is True
    assert direct_calls == [], "proxied sends must not use the direct urlopen path"
    proxy_handlers = [
        h for h in captured["handlers"] if isinstance(h, urllib.request.ProxyHandler)
    ]
    assert proxy_handlers, "a ProxyHandler must be installed when the proxy is enabled"
    assert proxy_handlers[0].proxies.get("https") == "http://egress-proxy.local:3128"


def test_egress_proxy_auth_stays_out_of_proxy_urls_and_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    _clean_env(monkeypatch)
    direct_calls = _install_fake_urlopen(monkeypatch)

    ca = tmp_path / "ca.pem"
    ca.write_text("dummy-ca")
    monkeypatch.setattr("ssl.create_default_context", lambda *a, **k: object())

    captured: dict[str, Any] = {}

    class _FailingOpener:
        def open(self, request: Any, *args: Any, **kwargs: Any) -> _FakeResponse:
            captured["request"] = request
            raise OSError("proxy rejected credential agent:tok")

    def fake_build_opener(*handlers: Any) -> _FailingOpener:
        captured["handlers"] = handlers
        return _FailingOpener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)

    from magi_agent.channels.providers.slack_urllib import SlackUrllibProvider
    from magi_agent.egress_proxy.config import EgressProxyConfig

    cfg = EgressProxyConfig(
        enabled=True,
        proxy_url="http://egress-proxy.local:3128",
        proxy_auth="agent:tok",
        ca_cert_path=str(ca),
    )
    provider = SlackUrllibProvider(token=_FAKE_TOKEN, egress_config=cfg)
    result = provider.send(channel="#general", text="hello")

    assert result["ok"] is False
    assert direct_calls == [], "proxied sends must not use the direct urlopen path"
    proxy_handlers = [
        h for h in captured["handlers"] if isinstance(h, urllib.request.ProxyHandler)
    ]
    assert proxy_handlers, "a ProxyHandler must be installed when the proxy is enabled"
    proxy_url = proxy_handlers[0].proxies.get("https", "")
    assert proxy_url == "http://egress-proxy.local:3128"
    assert "agent:tok" not in proxy_url
    assert any(
        isinstance(h, urllib.request.ProxyBasicAuthHandler)
        for h in captured["handlers"]
    )
    assert "agent:tok" not in str(result)


# ---------------------------------------------------------------------------
# B1-6: Import cleanliness — slack_urllib stays stdlib-only
# ---------------------------------------------------------------------------

def test_slack_urllib_import_no_third_party_network_libs() -> None:
    import subprocess
    import sys

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.channels.providers.slack_urllib")
forbidden = ("requests", "httpx", "slack_sdk", "slack_bolt", "urllib3", "aiohttp")
loaded = [m for m in forbidden if m in sys.modules]
if loaded:
    raise AssertionError(f"slack_urllib loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
