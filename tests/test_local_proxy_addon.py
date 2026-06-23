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


def test_build_injection_addon_pins_store_dir(tmp_path: Path) -> None:
    """HOSTED loop: when start_local_proxy is given a store_dir, the addon must
    read credentials AND read/write approvals from THAT dir (the sidecar store),
    not the default ~/.magi. Otherwise the producer and the admin API diverge and
    the dashboard never sees a blocked egress.
    """
    from magi_agent.credentials_admin import approvals_store, store
    from magi_agent.credentials_admin.local_proxy import _build_injection_addon

    store_dir = tmp_path / "sidecar-store"
    store_dir.mkdir()
    creds_path = store_dir / "credentials.json"
    approvals_path = store_dir / "credential_approvals.json"

    store.add_credential(
        service="notion",
        label="k",
        auth_scheme="bearer",
        status=store.STATUS_ACTIVE,
        vault_ref="ref-1",
        requires_approval=True,
        host="api.notion.com",
        path=creds_path,
    )

    addon = _build_injection_addon(vault_path=store_dir / "vault", store_dir=store_dir)

    # 1) credentials_loader reads the sidecar store (not the empty default).
    loaded = addon._credentials_loader()
    assert [c["service"] for c in loaded] == ["notion"]

    # 2) approval_enqueue writes into the sidecar approvals file.
    addon._approval_enqueue(
        credential_id="cred-x",
        requested_action="egress_credential_use",
        target_host="api.notion.com",
    )
    enqueued = approvals_store.list_approvals(path=approvals_path)
    assert [a["credential_id"] for a in enqueued] == ["cred-x"]

    # 3) approvals_lookup reads grants from the sidecar approvals file.
    assert addon._approvals_lookup("cred-x") is False  # still pending
    approvals_store.decide_approval(
        enqueued[0]["id"], approvals_store.STATUS_APPROVED, path=approvals_path
    )
    assert addon._approvals_lookup("cred-x") is True


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


# -- fail-closed: matched credential, secret unavailable → 502 BLOCK ------


def _install_fake_mitmproxy_http(monkeypatch):
    """Stub the lazily-imported ``mitmproxy.http`` so the block path is testable
    without the optional extra. Returns the captured-response constructor."""
    import sys
    import types

    fake_http = types.ModuleType("mitmproxy.http")

    class _Resp:
        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self.text = body

        @staticmethod
        def make(status, body, headers):
            return _Resp(status, body)

    fake_http.Response = _Resp
    fake_mitmproxy = types.ModuleType("mitmproxy")
    fake_mitmproxy.http = fake_http
    monkeypatch.setitem(sys.modules, "mitmproxy", fake_mitmproxy)
    monkeypatch.setitem(sys.modules, "mitmproxy.http", fake_http)
    return _Resp


def _active_cred():
    return {
        "id": "cred-1",
        "service": "notion",
        "auth_scheme": "bearer",
        "status": "active",
        "vault_ref": "ref-abc",
        "requires_approval": False,
        "host": None,
    }


@pytest.fixture(autouse=True)
def _clear_proxy_faults():
    local_proxy.clear_proxy_faults()
    yield
    local_proxy.clear_proxy_faults()


def test_addon_blocks_when_secret_missing(monkeypatch) -> None:
    """A MATCHED active credential whose secret is missing (None) must BLOCK
    upstream with 502 — never forward an unauthenticated request."""
    _install_fake_mitmproxy_http(monkeypatch)
    addon = CredentialInjectionAddon(
        credentials_loader=lambda: [_active_cred()],
        approvals_lookup=lambda _cid: False,
    )
    monkeypatch.setattr(addon._vault, "get_secret", lambda ref: None)

    flow = _FakeFlow("api.notion.com")
    addon.request(flow)

    # Upstream is NOT reached: the request header was never injected and a
    # blocking 502 response is set.
    assert flow.response is not None
    assert flow.response.status_code == 502
    assert "Authorization" not in flow.request.headers


def test_addon_blocks_when_secret_empty_string(monkeypatch) -> None:
    _install_fake_mitmproxy_http(monkeypatch)
    addon = CredentialInjectionAddon(
        credentials_loader=lambda: [_active_cred()],
        approvals_lookup=lambda _cid: False,
    )
    monkeypatch.setattr(addon._vault, "get_secret", lambda ref: "")

    flow = _FakeFlow("api.notion.com")
    addon.request(flow)

    assert flow.response is not None
    assert flow.response.status_code == 502
    assert "Authorization" not in flow.request.headers


def test_addon_blocks_when_get_secret_raises(monkeypatch) -> None:
    """Undecryptable / vault error: get_secret raising must also BLOCK, not
    forward."""
    _install_fake_mitmproxy_http(monkeypatch)
    addon = CredentialInjectionAddon(
        credentials_loader=lambda: [_active_cred()],
        approvals_lookup=lambda _cid: False,
    )

    def _boom(ref):  # noqa: ANN001
        raise RuntimeError("decrypt failed")

    monkeypatch.setattr(addon._vault, "get_secret", _boom)

    flow = _FakeFlow("api.notion.com")
    addon.request(flow)

    assert flow.response is not None
    assert flow.response.status_code == 502
    assert "Authorization" not in flow.request.headers


def test_addon_block_missing_secret_body_carries_no_secret(monkeypatch) -> None:
    _install_fake_mitmproxy_http(monkeypatch)
    addon = CredentialInjectionAddon(
        credentials_loader=lambda: [_active_cred()],
        approvals_lookup=lambda _cid: False,
    )
    monkeypatch.setattr(addon._vault, "get_secret", lambda ref: SECRET and None)

    flow = _FakeFlow("api.notion.com")
    addon.request(flow)

    assert flow.response.status_code == 502
    assert SECRET not in str(flow.response.text)
    assert "ref-abc" not in str(flow.response.text)  # no vault_ref leak


def test_addon_missing_secret_records_redacted_fault(monkeypatch) -> None:
    """The fault is recorded (redacted) so /v1/vault/status can surface it."""
    _install_fake_mitmproxy_http(monkeypatch)
    addon = CredentialInjectionAddon(
        credentials_loader=lambda: [_active_cred()],
        approvals_lookup=lambda _cid: False,
    )
    monkeypatch.setattr(addon._vault, "get_secret", lambda ref: None)

    addon.request(_FakeFlow("api.notion.com"))

    fault = local_proxy.last_proxy_fault()
    assert fault is not None
    assert fault["reasonCode"] == "secret_missing"
    assert fault["targetHost"] == "api.notion.com"
    # Redacted: only a credential-id suffix, never the full id / ref / secret.
    assert fault["credentialIdSuffix"] == "cred-1"[-4:]
    assert "vault_ref" not in fault
    assert SECRET not in str(fault)
    assert "ref-abc" not in str(fault)
    assert "createdAt" in fault


def test_addon_undecryptable_records_reason_code(monkeypatch) -> None:
    _install_fake_mitmproxy_http(monkeypatch)
    addon = CredentialInjectionAddon(
        credentials_loader=lambda: [_active_cred()],
        approvals_lookup=lambda _cid: False,
    )

    def _boom(ref):  # noqa: ANN001
        raise RuntimeError("decrypt failed")

    monkeypatch.setattr(addon._vault, "get_secret", _boom)
    addon.request(_FakeFlow("api.notion.com"))

    fault = local_proxy.last_proxy_fault()
    assert fault is not None
    assert fault["reasonCode"] == "secret_undecryptable"


def test_addon_block_missing_secret_does_not_log_secret(monkeypatch, caplog) -> None:
    _install_fake_mitmproxy_http(monkeypatch)
    addon = CredentialInjectionAddon(
        credentials_loader=lambda: [_active_cred()],
        approvals_lookup=lambda _cid: False,
    )
    monkeypatch.setattr(addon._vault, "get_secret", lambda ref: None)

    with caplog.at_level(logging.DEBUG):
        addon.request(_FakeFlow("api.notion.com"))

    assert SECRET not in caplog.text


def test_record_credential_proxy_fault_redacts(monkeypatch) -> None:
    local_proxy.clear_proxy_faults()
    local_proxy.record_credential_proxy_fault(
        credential_id="some-long-credential-id-9999",
        target_host="api.example.com",
        reason_code="secret_missing",
    )
    fault = local_proxy.last_proxy_fault()
    assert fault["credentialIdSuffix"] == "9999"
    assert "some-long-credential-id" not in str(fault)
    assert fault["targetHost"] == "api.example.com"
    assert fault["reasonCode"] == "secret_missing"
    assert "createdAt" in fault


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
