"""Tests for the credential-injection addon + lifecycle gating.

mitmproxy is OPTIONAL and not required at test time. The addon module itself
imports without mitmproxy (mitmproxy is lazily imported only in the 403-block
path and ``start_local_proxy``). We therefore:

* test the INJECT path with a fake flow object + monkeypatched ``get_secret`` —
  no mitmproxy needed;
* test the BLOCK path's approval-enqueue with a fake flow whose
  ``http.Response.make`` is stubbed, guarding the real ``mitmproxy.http`` import
  with ``pytest.importorskip``;
* test the gating helpers and the ``LocalProxyUnavailable`` install hint by
  simulating the missing import.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from magi_agent.credentials_admin import local_proxy
from magi_agent.credentials_admin.local_proxy import (
    CredentialInjectionAddon,
    LocalProxyUnavailable,
    start_local_proxy,
)
from magi_agent.credentials_admin.local_proxy_decision import Inject
from magi_agent.credentials_admin.vault_local import local_vault_proxy_enabled

SECRET = "sk-live-abcd1234EFGH5678ijkl9012MNOP3456"


class _FakeHeaders(dict):
    """A case-sensitive dict-like stand-in for mitmproxy's Headers."""


class _FakeRequest:
    def __init__(self, host: str, headers: dict | None = None) -> None:
        self.pretty_host = host
        self.headers = _FakeHeaders(headers or {})


class _FakeFlow:
    def __init__(self, host: str, headers: dict | None = None) -> None:
        self.request = _FakeRequest(host, headers)
        self.response = None


# -- inject path (no mitmproxy needed) ------------------------------------


def test_addon_injects_secret_into_header(monkeypatch) -> None:
    creds = [
        {
            "id": "cred-1",
            "service": "notion",
            "auth_scheme": "bearer",
            "status": "active",
            "vault_ref": "ref-abc",
            "requires_approval": False,
            "host": None,
        }
    ]
    addon = CredentialInjectionAddon(
        credentials_loader=lambda: creds,
        approvals_lookup=lambda _cid: False,
    )
    # The ONLY place the plaintext appears is inside the addon's get_secret call.
    monkeypatch.setattr(addon._vault, "get_secret", lambda ref: SECRET)

    flow = _FakeFlow("api.notion.com")
    addon.request(flow)

    assert flow.request.headers["Authorization"] == "Bearer " + SECRET
    assert flow.response is None  # not blocked


def test_addon_strips_preexisting_auth_before_injecting(monkeypatch) -> None:
    creds = [
        {
            "id": "cred-1",
            "service": "notion",
            "auth_scheme": "bearer",
            "status": "active",
            "vault_ref": "ref-abc",
            "requires_approval": False,
            "host": None,
        }
    ]
    addon = CredentialInjectionAddon(
        credentials_loader=lambda: creds,
        approvals_lookup=lambda _cid: False,
    )
    monkeypatch.setattr(addon._vault, "get_secret", lambda ref: SECRET)

    # The bot tried to supply its own bogus token.
    flow = _FakeFlow("api.notion.com", headers={"Authorization": "Bearer agent-bogus"})
    addon.request(flow)

    assert flow.request.headers["Authorization"] == "Bearer " + SECRET
    assert "agent-bogus" not in flow.request.headers["Authorization"]


def test_addon_pass_through_leaves_request_untouched(monkeypatch) -> None:
    addon = CredentialInjectionAddon(
        credentials_loader=lambda: [],
        approvals_lookup=lambda _cid: False,
    )
    called = {"get_secret": False}

    def _tripwire(ref):  # noqa: ANN001
        called["get_secret"] = True
        return SECRET

    monkeypatch.setattr(addon._vault, "get_secret", _tripwire)

    flow = _FakeFlow("api.notion.com")
    addon.request(flow)

    assert "Authorization" not in flow.request.headers
    assert flow.response is None
    assert called["get_secret"] is False  # no decryption on pass-through


def test_addon_never_logs_secret(monkeypatch, caplog) -> None:
    creds = [
        {
            "id": "cred-1",
            "service": "notion",
            "auth_scheme": "bearer",
            "status": "active",
            "vault_ref": "ref-abc",
            "requires_approval": False,
            "host": None,
        }
    ]
    addon = CredentialInjectionAddon(
        credentials_loader=lambda: creds,
        approvals_lookup=lambda _cid: False,
    )
    monkeypatch.setattr(addon._vault, "get_secret", lambda ref: SECRET)

    with caplog.at_level(logging.DEBUG):
        addon.request(_FakeFlow("api.notion.com"))

    assert SECRET not in caplog.text


# -- block path: enqueues an approval -------------------------------------


def test_addon_block_enqueues_approval_and_403() -> None:
    """A requires_approval credential with no approval → 403 + an approval is
    enqueued via the (metadata-only) approvals store."""
    pytest.importorskip("mitmproxy")  # the 403 path imports mitmproxy.http

    enqueued: list[dict] = []

    creds = [
        {
            "id": "cred-1",
            "service": "notion",
            "auth_scheme": "bearer",
            "status": "active",
            "vault_ref": "ref-abc",
            "requires_approval": True,
            "host": None,
        }
    ]
    addon = CredentialInjectionAddon(
        credentials_loader=lambda: creds,
        approvals_lookup=lambda _cid: False,
        approval_enqueue=lambda **kw: enqueued.append(kw) or kw,
    )

    flow = _FakeFlow("api.notion.com")
    addon.request(flow)

    assert flow.response is not None
    assert flow.response.status_code == 403
    assert len(enqueued) == 1
    assert enqueued[0]["credential_id"] == "cred-1"
    assert enqueued[0]["target_host"] == "api.notion.com"
    # No secret in the approval enqueue payload.
    assert SECRET not in str(enqueued[0])


def test_addon_block_enqueue_payload_carries_no_secret_with_fake_http(monkeypatch) -> None:
    """Block path enqueues an approval even without mitmproxy by stubbing the
    lazy ``mitmproxy.http`` import, proving (b) the enqueue is metadata-only."""
    import sys
    import types

    fake_http = types.ModuleType("mitmproxy.http")

    class _Resp:
        def __init__(self, status_code):
            self.status_code = status_code

        @staticmethod
        def make(status, body, headers):
            return _Resp(status)

    fake_http.Response = _Resp
    fake_mitmproxy = types.ModuleType("mitmproxy")
    fake_mitmproxy.http = fake_http
    monkeypatch.setitem(sys.modules, "mitmproxy", fake_mitmproxy)
    monkeypatch.setitem(sys.modules, "mitmproxy.http", fake_http)

    enqueued: list[dict] = []
    creds = [
        {
            "id": "cred-9",
            "service": "stripe",
            "auth_scheme": "bearer",
            "status": "active",
            "vault_ref": "ref-xyz",
            "requires_approval": True,
            "host": None,
        }
    ]
    addon = CredentialInjectionAddon(
        credentials_loader=lambda: creds,
        approvals_lookup=lambda _cid: False,
        approval_enqueue=lambda **kw: enqueued.append(kw) or kw,
    )
    flow = _FakeFlow("api.stripe.com")
    addon.request(flow)

    assert flow.response.status_code == 403
    assert enqueued and enqueued[0]["credential_id"] == "cred-9"
    assert SECRET not in str(enqueued[0])


# -- gating: default-on only under the local sentinel ---------------------


def test_proxy_flag_default_on_under_local_full_runtime_profile() -> None:
    from magi_agent.runtime.local_defaults import apply_local_full_runtime_defaults

    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    assert env["MAGI_LOCAL_VAULT_PROXY_ENABLED"] == "1"
    assert env["MAGI_LOCAL_VAULT_ENABLED"] == "1"
    assert local_vault_proxy_enabled(env) is True


def test_proxy_flag_not_applied_under_safe_profile() -> None:
    from magi_agent.runtime.local_defaults import apply_local_full_runtime_defaults

    env = {"MAGI_RUNTIME_PROFILE": "safe"}
    apply_local_full_runtime_defaults(env)
    assert "MAGI_LOCAL_VAULT_PROXY_ENABLED" not in env
    assert local_vault_proxy_enabled(env) is False


def test_proxy_disabled_on_hosted_no_local_overlay() -> None:
    # A hosted-shaped env never runs the local overlay; the helper default is OFF.
    assert local_vault_proxy_enabled({}) is False
    # Flag set but local vault not enabled → still off.
    assert local_vault_proxy_enabled({"MAGI_LOCAL_VAULT_PROXY_ENABLED": "1"}) is False


def test_proxy_disabled_when_vault_admin_url_set() -> None:
    env = {
        "MAGI_LOCAL_VAULT_ENABLED": "1",
        "MAGI_LOCAL_VAULT_PROXY_ENABLED": "1",
        "MAGI_VAULT_ADMIN_URL": "https://vault.example.com",
    }
    assert local_vault_proxy_enabled(env) is False


# -- optional dependency: clear install hint when missing -----------------


def test_start_local_proxy_raises_unavailable_when_mitmproxy_missing(
    monkeypatch, tmp_path: Path
) -> None:
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name.startswith("mitmproxy"):
            raise ImportError("No module named 'mitmproxy'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(LocalProxyUnavailable) as excinfo:
        start_local_proxy(tmp_path)
    assert "magi-agent[vault]" in str(excinfo.value)


def test_local_proxy_module_imports_without_mitmproxy() -> None:
    # The module must import in a core-only environment (mitmproxy is optional).
    assert hasattr(local_proxy, "CredentialInjectionAddon")
    assert hasattr(local_proxy, "start_local_proxy")
