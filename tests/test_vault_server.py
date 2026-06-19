"""Tests for the standalone Agent Vault server (hosted sidecar).

The admin API + store tests MUST pass without mitmproxy installed: only the
CA-bootstrap test (which starts the real proxy to materialize the CA) is guarded
with ``pytest.importorskip("mitmproxy")``.

Security-critical invariants exercised here:
* the plaintext secret is NEVER in an admin response,
* NEVER in the redacted metadata store,
* and ``secrets.enc`` holds only Fernet ciphertext (not the plaintext),
* the CA private key is 0600 and only the CA *cert* is copied to the shared dir.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi_agent.credentials_admin import vault_server

_TOKEN = "test-admin-token"
_SECRET = "super-secret-value-AKIA12345678EXAMPLE"


def _make_client(tmp_path: Path) -> TestClient:
    store_dir = tmp_path / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    app = vault_server.build_vault_admin_app(
        admin_token=_TOKEN,
        store_dir=store_dir,
    )
    return TestClient(app)


def _store_dir_of(client: TestClient) -> Path:
    return Path(client.app.state.vault_store_dir)  # type: ignore[attr-defined]


# -- admin auth ---------------------------------------------------------------


def test_status_requires_token(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    resp = client.get("/v1/vault/status")
    assert resp.status_code == 401


def test_status_with_token(tmp_path: Path) -> None:
    from magi_agent.credentials_admin import local_proxy

    local_proxy.clear_proxy_faults()
    client = _make_client(tmp_path)
    resp = client.get(
        "/v1/vault/status", headers={"x-gateway-token": _TOKEN}
    )
    assert resp.status_code == 200
    body = resp.json()
    # No fault recorded → lastProxyFault is absent (or null).
    assert {"present", "healthy"} <= set(body)
    assert body.get("lastProxyFault") is None
    assert body["present"] is True


def test_status_surfaces_redacted_last_proxy_fault(tmp_path: Path) -> None:
    """When the credential proxy hit a missing/undecryptable secret, the admin
    status surfaces a REDACTED lastProxyFault (no secret material / vault ref)."""
    from magi_agent.credentials_admin import local_proxy

    local_proxy.clear_proxy_faults()
    try:
        local_proxy.record_credential_proxy_fault(
            credential_id="cred-secret-9999",
            target_host="api.notion.com",
            reason_code="secret_missing",
        )
        client = _make_client(tmp_path)
        resp = client.get(
            "/v1/vault/status", headers={"x-gateway-token": _TOKEN}
        )
        assert resp.status_code == 200
        fault = resp.json()["lastProxyFault"]
        assert fault["reasonCode"] == "secret_missing"
        assert fault["targetHost"] == "api.notion.com"
        assert fault["credentialIdSuffix"] == "9999"
        assert "cred-secret" not in str(fault)
        assert "createdAt" in fault
    finally:
        local_proxy.clear_proxy_faults()


def test_create_requires_token(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    resp = client.post(
        "/v1/vault/credentials",
        json={
            "service": "github",
            "label": "ci",
            "auth_scheme": "bearer",
            "secret": _SECRET,
        },
    )
    assert resp.status_code == 401


# -- register / secret containment --------------------------------------------


def test_create_returns_vault_ref_and_hides_secret(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    resp = client.post(
        "/v1/vault/credentials",
        headers={"x-gateway-token": _TOKEN},
        json={
            "service": "github",
            "label": "ci",
            "auth_scheme": "bearer",
            "secret": _SECRET,
            "requires_approval": False,
            "host": "api.github.com",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    vault_ref = body["vault_ref"]
    assert isinstance(vault_ref, str) and vault_ref

    # SECRET CONTAINMENT 1: never echoed in the response.
    assert _SECRET not in resp.text


def test_secret_absent_from_metadata_store(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    resp = client.post(
        "/v1/vault/credentials",
        headers={"x-gateway-token": _TOKEN},
        json={
            "service": "github",
            "label": "ci",
            "auth_scheme": "bearer",
            "secret": _SECRET,
        },
    )
    assert resp.status_code == 200, resp.text

    store_dir = _store_dir_of(client)
    metadata = (store_dir / "credentials.json").read_text(encoding="utf-8")
    # SECRET CONTAINMENT 2: plaintext is not in the redacted metadata store.
    assert _SECRET not in metadata
    # the redacted projection still records the credential as active w/ a ref.
    assert '"status": "active"' in metadata
    assert '"vault_ref"' in metadata


def test_secret_is_ciphertext_in_secrets_enc(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    resp = client.post(
        "/v1/vault/credentials",
        headers={"x-gateway-token": _TOKEN},
        json={
            "service": "github",
            "label": "ci",
            "auth_scheme": "bearer",
            "secret": _SECRET,
        },
    )
    assert resp.status_code == 200, resp.text

    store_dir = _store_dir_of(client)
    enc = (store_dir / "vault" / "secrets.enc").read_bytes()
    # SECRET CONTAINMENT 3: secrets.enc holds Fernet ciphertext, not plaintext.
    assert enc, "secrets.enc must be written"
    assert _SECRET.encode("utf-8") not in enc


def test_no_get_secret_endpoint(tmp_path: Path) -> None:
    """The internal decryption path is never exposed over HTTP."""
    client = _make_client(tmp_path)
    routes = {getattr(r, "path", "") for r in client.app.routes}  # type: ignore[attr-defined]
    assert not any("secret" in p and "{" in p for p in routes)
    # explicit: there must be no resolve/reveal-style path.
    assert not any(
        p.endswith("/reveal") or p.endswith("/secret") for p in routes
    )


# -- revoke -------------------------------------------------------------------


def test_revoke_marks_metadata_revoked(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    created = client.post(
        "/v1/vault/credentials",
        headers={"x-gateway-token": _TOKEN},
        json={
            "service": "github",
            "label": "ci",
            "auth_scheme": "bearer",
            "secret": _SECRET,
        },
    ).json()
    vault_ref = created["vault_ref"]

    resp = client.post(
        f"/v1/vault/credentials/{vault_ref}/revoke",
        headers={"x-gateway-token": _TOKEN},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["credential"]["status"] == "revoked"

    # the secret entry is gone from the encrypted store after revoke.
    store_dir = _store_dir_of(client)
    enc_path = store_dir / "vault" / "secrets.enc"
    if enc_path.exists():
        import json as _json

        data = _json.loads(enc_path.read_text(encoding="utf-8"))
        assert vault_ref not in data


def test_revoke_unknown_ref_404(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    resp = client.post(
        "/v1/vault/credentials/does-not-exist/revoke",
        headers={"x-gateway-token": _TOKEN},
    )
    assert resp.status_code == 404


# -- approvals ----------------------------------------------------------------


def test_approvals_list_and_resolve(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    store_dir = _store_dir_of(client)

    # seed a pending approval directly through the reused store (same dir the
    # admin app is wired to).
    from magi_agent.credentials_admin import approvals_store

    approval = approvals_store.add_approval(
        credential_id="cred-1",
        requested_action="egress_credential_use",
        target_host="api.github.com",
        path=store_dir / "credential_approvals.json",
    )

    listed = client.get(
        "/v1/vault/approvals", headers={"x-gateway-token": _TOKEN}
    )
    assert listed.status_code == 200
    ids = [a["id"] for a in listed.json()["approvals"]]
    assert approval["id"] in ids

    # filter by status
    pending = client.get(
        "/v1/vault/approvals?status=pending",
        headers={"x-gateway-token": _TOKEN},
    )
    assert pending.status_code == 200
    assert all(a["status"] == "pending" for a in pending.json()["approvals"])

    resolved = client.post(
        f"/v1/vault/approvals/{approval['id']}",
        headers={"x-gateway-token": _TOKEN},
        json={"decision": "approved"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["approval"]["status"] == "approved"


def test_approvals_resolve_requires_token(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    resp = client.post(
        "/v1/vault/approvals/whatever", json={"decision": "approved"}
    )
    assert resp.status_code == 401


def test_approvals_invalid_decision_400(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    resp = client.post(
        "/v1/vault/approvals/whatever",
        headers={"x-gateway-token": _TOKEN},
        json={"decision": "maybe"},
    )
    assert resp.status_code == 400


# -- run_vault_server fail-fast -----------------------------------------------


def test_run_vault_server_requires_proxy_auth(tmp_path: Path) -> None:
    env = {
        "AGENT_VAULT_STORE_DIR": str(tmp_path / "store"),
        "AGENT_VAULT_CA_DIR": str(tmp_path / "ca"),
        # AGENT_VAULT_PROXY_AUTH intentionally unset.
    }
    with pytest.raises(vault_server.VaultServerConfigError):
        vault_server.run_vault_server(env=env, _serve=False)


# -- CA bootstrap (requires mitmproxy) ----------------------------------------


def test_bootstrap_ca_writes_cert_and_protects_key(tmp_path: Path) -> None:
    pytest.importorskip("mitmproxy")
    ca_dir = tmp_path / "ca"
    confdir = tmp_path / "confdir"
    cert_path = vault_server.bootstrap_ca(ca_dir=ca_dir, confdir=confdir)

    # the shared CA cert exists, is world-readable (0644), and is a cert.
    assert cert_path == ca_dir / "ca.pem"
    assert cert_path.is_file()
    cert_mode = stat.S_IMODE(os.stat(cert_path).st_mode)
    assert cert_mode == 0o644, oct(cert_mode)
    cert_text = cert_path.read_text(encoding="utf-8")
    assert "BEGIN CERTIFICATE" in cert_text
    # the cert must NOT contain a private key.
    assert "PRIVATE KEY" not in cert_text

    # the CA private key stays in the confdir at 0600 and is NOT copied out.
    key_path = confdir / "mitmproxy-ca.pem"
    assert key_path.is_file()
    key_mode = stat.S_IMODE(os.stat(key_path).st_mode)
    assert key_mode == 0o600, oct(key_mode)
    # no private key material leaked into the shared CA dir.
    for child in ca_dir.iterdir():
        assert "PRIVATE KEY" not in child.read_text(encoding="utf-8", errors="ignore")
