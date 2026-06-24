"""Phase 4: in-chat credential grants EXPIRE (TTL) instead of lasting forever.

A grant from an in-chat approval is written with a ``granted_until`` expiry
(``MAGI_CREDENTIAL_GRANT_TTL_S`` seconds ahead); the egress proxy and the
resolver both treat an expired grant as not-granted, so the credential re-prompts
on a later turn. A "remember" (persistent) approval writes a non-expiring grant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from magi_agent.credentials_admin import approvals_store, local_proxy, store
from magi_agent.credentials_admin.approval_resolver import (
    LocalCredentialApprovalResolver,
    _grant_expiry,
)


def _past() -> str:
    return (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")


def _future() -> str:
    return (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")


# -- grant_is_active ----------------------------------------------------------


def test_grant_is_active_matrix():
    assert approvals_store.grant_is_active({"status": "approved", "granted_until": None})
    assert approvals_store.grant_is_active(
        {"status": "approved", "granted_until": _future()}
    )
    assert not approvals_store.grant_is_active(
        {"status": "approved", "granted_until": _past()}
    )
    assert not approvals_store.grant_is_active({"status": "pending", "granted_until": None})


def test_granted_until_survives_store_round_trip(tmp_path):
    approvals = tmp_path / "a.json"
    approvals_store.add_approval(
        credential_id="c1",
        requested_action="egress_credential_use",
        target_host="",
        granted_until=_future(),
        path=approvals,
    )
    rows = approvals_store.list_approvals(path=approvals)
    assert rows[0]["granted_until"] == _future() or rows[0]["granted_until"].startswith(
        _future()[:13]
    )


# -- _grant_expiry ------------------------------------------------------------


def test_grant_expiry_persistent_is_none():
    assert _grant_expiry(persistent=True) is None


def test_grant_expiry_zero_ttl_is_none(monkeypatch):
    monkeypatch.setenv("MAGI_CREDENTIAL_GRANT_TTL_S", "0")
    assert _grant_expiry(persistent=False) is None


def test_grant_expiry_positive_ttl_is_future(monkeypatch):
    monkeypatch.setenv("MAGI_CREDENTIAL_GRANT_TTL_S", "3600")
    expiry = _grant_expiry(persistent=False)
    assert expiry is not None
    assert expiry > datetime.now(UTC).isoformat().replace("+00:00", "Z")


# -- resolver grant honors TTL ------------------------------------------------


def _resolver(tmp_path: Path) -> LocalCredentialApprovalResolver:
    creds = tmp_path / "credentials.json"
    store.add_credential(
        service="svc",
        label="k",
        auth_scheme="api_key",
        status=store.STATUS_ACTIVE,
        vault_ref="vault://r",
        requires_approval=True,
        host="api.example.com",
        path=creds,
    )
    return LocalCredentialApprovalResolver(
        credentials_path=creds, approvals_path=tmp_path / "a.json"
    )


def test_persistent_grant_is_granted(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CREDENTIAL_GRANT_TTL_S", "3600")
    resolver = _resolver(tmp_path)
    need = resolver.needs_approval("api.example.com")
    resolver.grant(need.credential_id, persistent=True)
    assert resolver.is_granted(need.credential_id) is True


def test_ttl_grant_is_granted_now(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CREDENTIAL_GRANT_TTL_S", "3600")
    resolver = _resolver(tmp_path)
    need = resolver.needs_approval("api.example.com")
    resolver.grant(need.credential_id, persistent=False)
    assert resolver.is_granted(need.credential_id) is True


def test_expired_grant_is_not_granted(tmp_path):
    resolver = _resolver(tmp_path)
    need = resolver.needs_approval("api.example.com")
    # Write an approved-but-expired grant directly.
    created = approvals_store.add_approval(
        credential_id=need.credential_id,
        requested_action="egress_credential_use",
        target_host="",
        granted_until=_past(),
        path=tmp_path / "a.json",
    )
    approvals_store.decide_approval(
        created["id"], approvals_store.STATUS_APPROVED, path=tmp_path / "a.json"
    )
    assert resolver.is_granted(need.credential_id) is False
    # The egress proxy honors the same expiry.
    assert (
        local_proxy._approval_granted(
            need.credential_id, approvals_path=tmp_path / "a.json"
        )
        is False
    )


def test_proxy_honors_active_grant(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CREDENTIAL_GRANT_TTL_S", "3600")
    resolver = _resolver(tmp_path)
    need = resolver.needs_approval("api.example.com")
    resolver.grant(need.credential_id, persistent=False)
    assert (
        local_proxy._approval_granted(
            need.credential_id, approvals_path=tmp_path / "a.json"
        )
        is True
    )
