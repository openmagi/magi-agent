"""Phase 2: wire credential-use approval into the tool-permission decision.

When a tool call would egress to a host guarded by a require-approval Agent Vault
credential with no current grant, `decide()` returns `action="ask"` with a
`credentialApproval` marker; the kernel records the grant on the approval resume
via `apply_credential_grant`. Inert when no local vault / no matching credential,
so default deployments are unchanged.
"""

from __future__ import annotations

from pathlib import Path

from magi_agent.credentials_admin import store
from magi_agent.credentials_admin.approval_resolver import (
    LocalCredentialApprovalResolver,
    NullCredentialApprovalResolver,
    default_credential_approval_resolver,
)
from magi_agent.plugins.manager import resolve_plugin_state
from magi_agent.plugins.native_catalog import native_plugin_manifests
from magi_agent.plugins.tool_projection import project_native_plugin_tool_manifests
from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest
from magi_agent.tools.permission import ToolPermissionPolicy


def _web_manifest(name: str = "WebFetch") -> ToolManifest:
    state = resolve_plugin_state(native_plugin_manifests())
    manifests = {m.name: m for m in project_native_plugin_tool_manifests(state)}
    return manifests[name]


def _context(scope_mode: str = "default") -> ToolContext:
    return ToolContext(
        bot_id="bot-1",
        turn_id="turn-1",
        workspace_root="/tmp/ws",
        permission_scope={"mode": scope_mode, "source": "fail_closed"},
    )


def _seed_resolver(tmp_path: Path, *, host: str = "api.example.com", approval: bool = True):
    creds = tmp_path / "credentials.json"
    store.add_credential(
        service="test-vault-service",
        label="test_key",
        auth_scheme="api_key",
        status=store.STATUS_ACTIVE,
        vault_ref="vault://ref-1",
        requires_approval=approval,
        host=host,
        path=creds,
    )
    return LocalCredentialApprovalResolver(
        credentials_path=creds, approvals_path=tmp_path / "approvals.json"
    )


def _policy(resolver) -> ToolPermissionPolicy:
    return ToolPermissionPolicy(credential_resolver=resolver)


def test_decide_asks_for_guarded_credential_host(tmp_path):
    resolver = _seed_resolver(tmp_path)
    decision = _policy(resolver).decide(
        _web_manifest(),
        {"url": "https://api.example.com/v1/x"},
        _context(),
        mode="act",
    )
    assert decision.action == "ask"
    marker = decision.metadata.get("credentialApproval")
    assert isinstance(marker, dict)
    assert marker["service"] == "test-vault-service"
    assert marker["label"] == "test_key"
    assert marker["host"] == "api.example.com"
    assert marker["credentialId"]
    assert "controlRequest" in decision.metadata
    # No secret / vault_ref leaks into the decision the user will see.
    assert "vault://" not in str(decision.metadata)


def test_decide_allows_unguarded_host(tmp_path):
    resolver = _seed_resolver(tmp_path)
    decision = _policy(resolver).decide(
        _web_manifest(),
        {"url": "https://api.other.com/y"},
        _context(),
        mode="act",
    )
    assert "credentialApproval" not in decision.metadata
    assert decision.action == "allow"


def test_decide_allows_non_approval_credential(tmp_path):
    resolver = _seed_resolver(tmp_path, approval=False)
    decision = _policy(resolver).decide(
        _web_manifest(),
        {"url": "https://api.example.com/x"},
        _context(),
        mode="act",
    )
    assert "credentialApproval" not in decision.metadata
    assert decision.action == "allow"


def test_decide_allows_after_grant(tmp_path):
    resolver = _seed_resolver(tmp_path)
    need = resolver.needs_approval("api.example.com")
    resolver.grant(need.credential_id, persistent=True)
    decision = _policy(resolver).decide(
        _web_manifest(),
        {"url": "https://api.example.com/x"},
        _context(),
        mode="act",
    )
    assert "credentialApproval" not in decision.metadata
    assert decision.action == "allow"


def test_null_resolver_never_gates(tmp_path):
    decision = ToolPermissionPolicy(
        credential_resolver=NullCredentialApprovalResolver()
    ).decide(
        _web_manifest(),
        {"url": "https://api.example.com/x"},
        _context(),
        mode="act",
    )
    assert "credentialApproval" not in decision.metadata
    assert decision.action == "allow"


def test_credential_ask_beats_bypass_scope(tmp_path):
    """An explicit per-credential 'require approval' is honored even under bypass:
    the credential check runs before the bypass/preapproval short-circuits."""
    resolver = _seed_resolver(tmp_path)
    decision = _policy(resolver).decide(
        _web_manifest(),
        {"url": "https://api.example.com/x"},
        _context(scope_mode="bypass"),
        mode="act",
    )
    assert decision.action == "ask"
    assert decision.metadata.get("credentialApproval")


def test_apply_credential_grant_records_and_is_idempotent_noop(tmp_path):
    resolver = _seed_resolver(tmp_path)
    policy = _policy(resolver)
    decision = policy.decide(
        _web_manifest(),
        {"url": "https://api.example.com/x"},
        _context(),
        mode="act",
    )
    need = resolver.needs_approval("api.example.com")
    assert resolver.is_granted(need.credential_id) is False

    policy.apply_credential_grant(decision.metadata, persistent=False)
    assert resolver.is_granted(need.credential_id) is True

    # No marker -> no-op, no crash.
    policy.apply_credential_grant({}, persistent=False)
    policy.apply_credential_grant("not-a-dict", persistent=False)


def test_default_resolver_selection(monkeypatch):
    monkeypatch.setenv("MAGI_LOCAL_VAULT_ENABLED", "1")
    monkeypatch.delenv("MAGI_VAULT_ADMIN_URL", raising=False)
    assert isinstance(
        default_credential_approval_resolver(), LocalCredentialApprovalResolver
    )

    monkeypatch.delenv("MAGI_LOCAL_VAULT_ENABLED", raising=False)
    assert isinstance(
        default_credential_approval_resolver(), NullCredentialApprovalResolver
    )
