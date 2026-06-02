from __future__ import annotations

import json
from pathlib import Path

from openmagi_core_agent.plugins.manifest import PluginKind, PluginManifest
from openmagi_core_agent.plugins.sandbox_policy import (
    PluginTrustLevel,
    evaluate_plugin_sandbox,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "deterministic_runtime"
PROTECTED_BINDING_ACCESS_KEY = "protectedBindingAccess"


def _manifest(**overrides: object) -> PluginManifest:
    data: dict[str, object] = {
        "id": "openmagi.test-validator",
        "name": "Test Validator",
        "kind": "native",
        "version": "1.0.0",
        "defaultInstalled": True,
        "defaultEnabled": False,
        "optOutAllowed": True,
        "permissions": ["read"],
        "capabilities": [{"type": "verifier", "name": "quoteExactMatch"}],
        "supplyChainDigest": "sha256:" + "1" * 64,
        "trustLevel": "first_party",
        "sandbox": {
            "mode": "in_process_contract_only",
            "filesystem": "none",
            "network": "none",
            PROTECTED_BINDING_ACCESS_KEY: "none",
            "process": "none",
            "workspaceMutation": False,
            "channelDelivery": False,
        },
    }
    data.update(overrides)
    return PluginManifest.model_validate(data)


def test_validator_plugin_defaults_to_no_network_credentials_process_or_write() -> None:
    manifest = _manifest()
    decision = evaluate_plugin_sandbox(manifest)

    assert decision.ok is True
    assert decision.effective_permissions == ("read",)
    assert decision.sandbox.mode == "in_process_contract_only"
    assert decision.sandbox.network == "none"
    assert decision.sandbox.protected_binding_access == "none"
    assert decision.sandbox.process == "none"
    assert decision.sandbox.workspace_mutation is False


def test_missing_sandbox_metadata_fails_closed_even_for_first_party_plugins() -> None:
    manifest = _manifest(sandbox=None)
    decision = evaluate_plugin_sandbox(manifest)

    assert decision.ok is False
    assert "sandbox_policy_required" in decision.reason_codes


def test_plugin_requesting_permission_outside_sandbox_is_denied() -> None:
    manifest = _manifest(
        permissions=["read", "net"],
        sandbox={
            "mode": "in_process_contract_only",
            "filesystem": "none",
            "network": "none",
            PROTECTED_BINDING_ACCESS_KEY: "none",
            "process": "none",
            "workspaceMutation": False,
            "channelDelivery": False,
        },
    )
    decision = evaluate_plugin_sandbox(manifest)

    assert decision.ok is False
    assert "network_permission_not_allowed_by_sandbox" in decision.reason_codes


def test_third_party_or_local_dev_requires_supply_chain_digest() -> None:
    manifest = _manifest(
        id="vendor.example-plugin",
        kind="custom",
        trustLevel="verified_third_party",
        supplyChainDigest=None,
    )
    decision = evaluate_plugin_sandbox(manifest)

    assert decision.ok is False
    assert "supply_chain_digest_required" in decision.reason_codes


def test_execute_write_or_credential_permissions_require_explicit_approval_boundary() -> None:
    manifest = _manifest(
        permissions=["read", "write", "execute"],
        secrets=[{"name": "provider-token", "source": "platform"}],
        sandbox={
            "mode": "isolated_process",
            "filesystem": "scoped_readwrite",
            "network": "none",
            PROTECTED_BINDING_ACCESS_KEY: "scoped",
            "process": "isolated",
            "workspaceMutation": False,
            "channelDelivery": False,
        },
    )
    decision = evaluate_plugin_sandbox(manifest)

    assert decision.ok is False
    assert "mutation_or_execute_requires_approval_receipt" in decision.reason_codes
    assert "protected_binding_access_requires_approval_receipt" in decision.reason_codes


def test_untrusted_plugin_cannot_be_default_enabled() -> None:
    manifest = _manifest(
        kind="custom",
        id="vendor.unsafe-plugin",
        trustLevel="untrusted",
        defaultInstalled=True,
        defaultEnabled=True,
        supplyChainDigest="sha256:" + "2" * 64,
    )
    decision = evaluate_plugin_sandbox(manifest)

    assert decision.ok is False
    assert "untrusted_plugin_cannot_be_default_enabled" in decision.reason_codes


def test_trust_level_values_are_closed() -> None:
    assert set(PluginTrustLevel.__args__) == {
        "first_party",
        "verified_third_party",
        "local_dev",
        "untrusted",
    }
    assert PluginKind.NATIVE.value == "native"


def test_plugin_sandbox_policy_fixture_is_digest_only_and_valid() -> None:
    fixture = json.loads((FIXTURE_DIR / "plugin_sandbox_policy.json").read_text())
    manifest = _manifest(**fixture["manifest"])
    decision = evaluate_plugin_sandbox(manifest)

    assert decision.model_dump(by_alias=True)["effectivePermissions"] == tuple(
        fixture["expectedDecision"]["effectivePermissions"]
    )
    assert decision.ok is True
    encoded = json.dumps(fixture, sort_keys=True).lower()
    assert "authorization" not in encoded
    assert "cookie" not in encoded
    assert "secret-token" not in encoded
