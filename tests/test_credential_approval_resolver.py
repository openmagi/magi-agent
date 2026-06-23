"""CredentialApprovalResolver seam (in-chat credential-use approval, phase 1).

Pure read-side + host extraction, no permission wiring yet. The resolver answers
two questions the tool-permission layer will ask before a tool egresses:

* `needs_approval(host)` -> the matching active, require-approval credential (or
  None) for an outbound host,
* `is_granted(credential_id)` -> whether a current grant lets it through,

and `grant(...)` records an approval so the egress proxy injects. Host matching
reuses the SAME `local_proxy_decision.resolve_host` the proxy uses so the two
never disagree.
"""

from __future__ import annotations

from pathlib import Path

from magi_agent.credentials_admin import approvals_store, store
from magi_agent.credentials_admin.approval_resolver import (
    CredentialApprovalNeeded,
    LocalCredentialApprovalResolver,
    NullCredentialApprovalResolver,
    extract_egress_host,
)


# -- host extraction ----------------------------------------------------------


def test_extract_host_from_url_argument():
    assert extract_egress_host("WebFetch", {"url": "https://api.example.com/v1/x"}) == (
        "api.example.com"
    )


def test_extract_host_lowercases_and_strips_port():
    assert (
        extract_egress_host("WebFetch", {"url": "https://API.Example.COM:8443/y"})
        == "api.example.com"
    )


def test_extract_host_from_bare_host_value():
    assert extract_egress_host("WebFetch", {"url": "api.example.com"}) == "api.example.com"


def test_extract_host_none_when_no_url_like_arg():
    assert extract_egress_host("FileRead", {"path": "/etc/hosts"}) is None
    assert extract_egress_host("WebFetch", {}) is None


# -- resolver: needs_approval -------------------------------------------------


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    creds = tmp_path / "credentials.json"
    store.add_credential(
        service="test-vault-service",
        label="test_key",
        auth_scheme="api_key",
        status=store.STATUS_ACTIVE,
        vault_ref="vault://ref-1",
        requires_approval=True,
        host="api.example.com",
        path=creds,
    )
    store.add_credential(
        service="open",
        label="noauth",
        auth_scheme="bearer",
        status=store.STATUS_ACTIVE,
        vault_ref="vault://ref-2",
        requires_approval=False,  # no approval gate
        host="api.open.com",
        path=creds,
    )
    return creds, tmp_path / "credential_approvals.json"


def _resolver(tmp_path: Path) -> LocalCredentialApprovalResolver:
    creds, approvals = _seed(tmp_path)
    return LocalCredentialApprovalResolver(
        credentials_path=creds, approvals_path=approvals
    )


def test_needs_approval_matches_require_approval_credential(tmp_path):
    need = _resolver(tmp_path).needs_approval("api.example.com")
    assert isinstance(need, CredentialApprovalNeeded)
    assert need.service == "test-vault-service"
    assert need.label == "test_key"
    assert need.host == "api.example.com"
    assert need.credential_id


def test_needs_approval_none_for_non_approval_credential(tmp_path):
    assert _resolver(tmp_path).needs_approval("api.open.com") is None


def test_needs_approval_none_for_unmatched_host(tmp_path):
    assert _resolver(tmp_path).needs_approval("api.unknown.com") is None
    assert _resolver(tmp_path).needs_approval("") is None


def test_needs_approval_ignores_revoked(tmp_path):
    creds = tmp_path / "credentials.json"
    store.add_credential(
        service="test-vault-service",
        label="old",
        auth_scheme="api_key",
        status=store.STATUS_REVOKED,
        vault_ref="vault://ref-r",
        requires_approval=True,
        host="api.example.com",
        path=creds,
    )
    resolver = LocalCredentialApprovalResolver(
        credentials_path=creds, approvals_path=tmp_path / "a.json"
    )
    assert resolver.needs_approval("api.example.com") is None


# -- resolver: is_granted / grant ---------------------------------------------


def test_is_granted_false_then_true_after_grant(tmp_path):
    resolver = _resolver(tmp_path)
    need = resolver.needs_approval("api.example.com")
    assert need is not None
    assert resolver.is_granted(need.credential_id) is False

    resolver.grant(need.credential_id, persistent=True)
    assert resolver.is_granted(need.credential_id) is True


def test_grant_writes_an_approved_row_the_proxy_honors(tmp_path):
    creds, approvals = _seed(tmp_path)
    resolver = LocalCredentialApprovalResolver(
        credentials_path=creds, approvals_path=approvals
    )
    need = resolver.needs_approval("api.example.com")
    resolver.grant(need.credential_id, persistent=True)

    # The proxy decides via approvals_store APPROVED rows for the credential.
    granted = approvals_store.list_approvals(
        status=approvals_store.STATUS_APPROVED, path=approvals
    )
    assert any(a.get("credential_id") == need.credential_id for a in granted)


# -- null resolver ------------------------------------------------------------


def test_null_resolver_never_gates():
    null = NullCredentialApprovalResolver()
    assert null.needs_approval("api.example.com") is None
    assert null.is_granted("anything") is True
