from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.connectors.marketplace import (
    MarketplaceAuthorityFlags,
    MarketplacePromotionRequest,
    MarketplacePromotionReceipt,
    MarketplaceRevocationSnapshot,
    evaluate_marketplace_promotion_request,
    plugin_manifest_content_digest,
    validate_plugin_runtime_permission_request,
)
from magi_agent.connectors.registry import (
    ConnectorManifest,
    connector_manifest_content_digest,
)
from magi_agent.plugins.manifest import PluginManifest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = (
    PYTHON_ROOT
    / "tests/fixtures/parity/codex_class_work_agent_product_plane_matrix.json"
)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64


def _connector_manifest(**overrides: object) -> ConnectorManifest:
    data: dict[str, object] = {
        "connectorId": "connector:docs",
        "displayName": "Docs Connector",
        "version": "2.0.0",
        "publisherRef": "publisher:vendor",
        "supplyChainDigest": DIGEST_A,
        "sandboxMode": "local_fake",
        "permissions": [
            {"permissionId": "permission:docs-read", "kind": "read", "scope": "scope:docs"},
            {
                "permissionId": "permission:docs-meta",
                "kind": "metadata",
                "scope": "scope:docs",
            },
        ],
        "tools": [
            {
                "toolId": "tool:docs-read",
                "permissionRefs": ["permission:docs-read", "permission:docs-meta"],
                "audience": "audience:docs",
            }
        ],
        "policySnapshotDigest": DIGEST_B,
    }
    data.update(overrides)
    if "manifestDigest" not in overrides:
        data["manifestDigest"] = connector_manifest_content_digest(data)
    return ConnectorManifest.model_validate(data)


def _plugin_manifest(**overrides: object) -> PluginManifest:
    data: dict[str, object] = {
        "id": "vendor.docs-plugin",
        "name": "Docs Plugin",
        "kind": "custom",
        "version": "1.2.3",
        "publisher": "publisher:vendor",
        "defaultInstalled": False,
        "defaultEnabled": False,
        "optOutAllowed": True,
        "permissions": ["read", "meta"],
        "capabilities": [{"type": "tool", "name": "docs.read"}],
        "trustLevel": "verified_third_party",
        "supplyChainDigest": DIGEST_C,
        "sandbox": {
            "mode": "in_process_contract_only",
            "filesystem": "none",
            "network": "none",
            "protectedBindingAccess": "none",
            "process": "none",
            "workspaceMutation": False,
            "channelDelivery": False,
        },
    }
    data.update(overrides)
    if "manifestDigest" not in overrides:
        data["manifestDigest"] = plugin_manifest_content_digest(PluginManifest.model_validate(data))
    return PluginManifest.model_validate(data)


def _request(**overrides: object) -> MarketplacePromotionRequest:
    connector = _connector_manifest()
    plugin = _plugin_manifest()
    data: dict[str, object] = {
        "requestId": "marketplace-request:1",
        "operation": "install",
        "pluginId": plugin.plugin_id,
        "connectorId": connector.connector_id,
        "publisherRef": "publisher:vendor",
        "pluginVersionPin": plugin.version,
        "connectorVersionPin": connector.version,
        "pluginManifestDigest": plugin.manifest_digest,
        "connectorManifestDigest": connector.manifest_digest,
        "pluginSupplyChainDigest": plugin.supply_chain_digest,
        "connectorSupplyChainDigest": connector.supply_chain_digest,
        "policySnapshotDigest": connector.policy_snapshot_digest,
        "requiredSandboxMode": "in_process_contract_only",
        "requestedPluginPermissions": ["read", "meta"],
        "requestedConnectorPermissionRefs": ["permission:docs-read"],
        "metadata": {"ticketRef": "ops.ticket:PR11"},
    }
    data.update(overrides)
    return MarketplacePromotionRequest.model_validate(data)


def _allowed_receipt(**request_overrides: object) -> MarketplacePromotionReceipt:
    return evaluate_marketplace_promotion_request(
        _request(**request_overrides),
        plugin_manifest=_plugin_manifest(),
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        local_fake_enabled=True,
    )


def _encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


@pytest.mark.parametrize("operation", ("install", "update", "remove"))
def test_default_off_marketplace_operations_are_contract_only_and_non_authoritative(
    operation: str,
) -> None:
    receipt = evaluate_marketplace_promotion_request(
        _request(operation=operation),
        plugin_manifest=_plugin_manifest(),
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
    )
    projection = receipt.public_projection()

    assert receipt.status == "blocked"
    assert receipt.allowed is False
    assert receipt.contract_only is True
    assert f"marketplace_{operation}_disabled" in receipt.reason_codes
    assert set(projection["authorityFlags"].values()) == {False}
    assert projection["contractOnly"] is True
    assert projection["pluginManifestDigest"] == _plugin_manifest().manifest_digest
    assert projection["connectorManifestDigest"] == _connector_manifest().manifest_digest


def test_local_fake_evaluation_only_promotion_allows_when_all_contract_checks_pass() -> None:
    receipt = _allowed_receipt()
    projection = receipt.public_projection()

    assert receipt.status == "allowed"
    assert receipt.allowed is True
    assert receipt.contract_only is True
    assert receipt.reason_codes == ("local_fake_marketplace_promotion_allowed",)
    assert receipt.receipt_digest.startswith("sha256:")
    assert projection["sandboxMode"] == "in_process_contract_only"
    assert projection["pluginVersionPin"] == "1.2.3"
    assert projection["connectorVersionPin"] == "2.0.0"
    assert projection["requestedPluginPermissions"] == ["meta", "read"]
    assert projection["requestedConnectorPermissionRefs"] == ["permission:docs-read"]
    assert set(projection["authorityFlags"].values()) == {False}


def test_plugin_manifest_digest_is_content_bound_not_self_reported() -> None:
    original = _plugin_manifest()
    forged_plugin = _plugin_manifest(version="1.2.4", manifestDigest=original.manifest_digest)

    receipt = evaluate_marketplace_promotion_request(
        _request(pluginVersionPin="1.2.4", pluginManifestDigest=original.manifest_digest),
        plugin_manifest=forged_plugin,
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        local_fake_enabled=True,
    )

    assert receipt.status == "blocked"
    assert receipt.allowed is False
    assert "plugin_manifest_digest_mismatch" in receipt.reason_codes


@pytest.mark.parametrize(
    "field_name, value",
    (
        ("pluginVersionPin", "latest"),
        ("pluginVersionPin", "main"),
        ("pluginVersionPin", "^1.2.3"),
        ("pluginVersionPin", "1.2.*"),
        ("connectorVersionPin", "latest"),
        ("connectorVersionPin", "main"),
        ("connectorVersionPin", "~2.0.0"),
        ("connectorVersionPin", "2.*"),
    ),
)
def test_version_pins_must_be_immutable_exact_versions(field_name: str, value: str) -> None:
    with pytest.raises(ValidationError, match="immutable exact"):
        _request(**{field_name: value})


def test_local_fake_allow_path_requires_explicit_revocation_snapshot() -> None:
    receipt = evaluate_marketplace_promotion_request(
        _request(),
        plugin_manifest=_plugin_manifest(),
        connector_manifest=_connector_manifest(),
        revocations=None,
        local_fake_enabled=True,
    )

    assert receipt.status == "blocked"
    assert receipt.allowed is False
    assert "revocation_check_required" in receipt.reason_codes


@pytest.mark.parametrize(
    "publisher, reason",
    (
        (None, "plugin_publisher_ref_required"),
        ("publisher:other", "publisher_mismatch"),
    ),
)
def test_plugin_publisher_ref_must_be_declared_safe_and_match_request(
    publisher: str | None,
    reason: str,
) -> None:
    receipt = evaluate_marketplace_promotion_request(
        _request(),
        plugin_manifest=_plugin_manifest(publisher=publisher),
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        local_fake_enabled=True,
    )

    assert receipt.status == "blocked"
    assert receipt.allowed is False
    assert reason in receipt.reason_codes


@pytest.mark.parametrize(
    "override, reason",
    (
        ({"requestedPluginPermissions": ["read", "execute"]}, "plugin_permission_subset_mismatch"),
        (
            {"requestedConnectorPermissionRefs": ["permission:docs-write"]},
            "connector_permission_subset_mismatch",
        ),
    ),
)
def test_requested_permissions_outside_plugin_or_connector_manifest_fail_closed(
    override: dict[str, object],
    reason: str,
) -> None:
    receipt = evaluate_marketplace_promotion_request(
        _request(**override),
        plugin_manifest=_plugin_manifest(),
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        local_fake_enabled=True,
    )

    assert receipt.status == "blocked"
    assert receipt.allowed is False
    assert reason in receipt.reason_codes


def test_plugin_runtime_permission_overreach_is_blocked_before_execution() -> None:
    receipt = _allowed_receipt()
    accepted = validate_plugin_runtime_permission_request(
        receipt,
        plugin_manifest=_plugin_manifest(),
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        requested_permissions=("read",),
        runtime_ref="runtime:plugin-contract",
    )
    blocked = validate_plugin_runtime_permission_request(
        receipt,
        plugin_manifest=_plugin_manifest(),
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        requested_permissions=("read", "execute"),
        runtime_ref="runtime:plugin-contract",
    )

    assert accepted.status == "allowed"
    assert accepted.allowed is True
    assert "local_fake_runtime_permission_contract_allowed" in accepted.reason_codes
    assert blocked.status == "blocked"
    assert blocked.allowed is False
    assert "plugin_runtime_manifest_permission_mismatch" in blocked.reason_codes
    assert blocked.authority_flags.plugin_execution_enabled is False


def test_remove_receipt_cannot_be_converted_into_runtime_permission_grant() -> None:
    receipt = _allowed_receipt(operation="remove")

    blocked = validate_plugin_runtime_permission_request(
        receipt,
        plugin_manifest=_plugin_manifest(),
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        requested_permissions=("read",),
        runtime_ref="runtime:plugin-contract",
    )

    assert blocked.status == "blocked"
    assert blocked.allowed is False
    assert "runtime_permission_operation_not_granting" in blocked.reason_codes


def test_forged_allowed_receipt_cannot_grant_permissions_without_manifest_revalidation() -> None:
    forged = MarketplacePromotionReceipt(
        **{
            **_allowed_receipt().model_dump(by_alias=True, mode="json"),
            "requestedPluginPermissions": ["execute"],
            "reasonCodes": ("local_fake_marketplace_promotion_allowed",),
        }
    )

    blocked = validate_plugin_runtime_permission_request(
        forged,
        plugin_manifest=_plugin_manifest(),
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        requested_permissions=("execute",),
        runtime_ref="runtime:plugin-contract",
    )

    assert blocked.status == "blocked"
    assert blocked.allowed is False
    assert "plugin_runtime_promotion_receipt_untrusted" in blocked.reason_codes


def test_stale_receipt_plugin_digest_blocks_runtime_permission_validation() -> None:
    stale = MarketplacePromotionReceipt(
        **{
            **_allowed_receipt().model_dump(by_alias=True, mode="json"),
            "pluginManifestDigest": DIGEST_A,
        }
    )

    blocked = validate_plugin_runtime_permission_request(
        stale,
        plugin_manifest=_plugin_manifest(),
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        requested_permissions=("read",),
        runtime_ref="runtime:plugin-contract",
    )

    assert blocked.status == "blocked"
    assert blocked.allowed is False
    assert "plugin_runtime_promotion_receipt_untrusted" in blocked.reason_codes


def test_untrusted_manifest_cannot_validate_runtime_permissions_with_forged_allowed_receipt() -> None:
    untrusted_plugin = _plugin_manifest(trustLevel="untrusted")
    forged = MarketplacePromotionReceipt(
        **{
            **_allowed_receipt().model_dump(by_alias=True, mode="json"),
            "pluginManifestDigest": untrusted_plugin.manifest_digest,
            "pluginSupplyChainDigest": untrusted_plugin.supply_chain_digest,
        }
    )

    blocked = validate_plugin_runtime_permission_request(
        forged,
        plugin_manifest=untrusted_plugin,
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        requested_permissions=("read",),
        runtime_ref="runtime:plugin-contract",
    )

    assert blocked.status == "blocked"
    assert blocked.allowed is False
    assert "plugin_runtime_promotion_receipt_untrusted" in blocked.reason_codes


@pytest.mark.parametrize(
    "receipt_overrides",
    (
        {"connectorId": "connector:other"},
        {"connectorManifestDigest": DIGEST_A},
        {"policySnapshotDigest": DIGEST_C},
        {"revocationSnapshotDigest": DIGEST_D},
    ),
)
def test_forged_connector_policy_or_revocation_receipt_cannot_validate_runtime_permissions(
    receipt_overrides: dict[str, object],
) -> None:
    forged = MarketplacePromotionReceipt(
        **{
            **_allowed_receipt().model_dump(by_alias=True, mode="json"),
            **receipt_overrides,
        }
    )

    blocked = validate_plugin_runtime_permission_request(
        forged,
        plugin_manifest=_plugin_manifest(),
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        requested_permissions=("read",),
        runtime_ref="runtime:plugin-contract",
    )

    assert blocked.status == "blocked"
    assert blocked.allowed is False
    assert "plugin_runtime_promotion_receipt_untrusted" in blocked.reason_codes


@pytest.mark.parametrize(
    "override, reason",
    (
        ({"pluginVersionPin": None}, "plugin_version_pin_required"),
        ({"connectorVersionPin": None}, "connector_version_pin_required"),
        ({"pluginVersionPin": "1.2.4"}, "plugin_version_pin_mismatch"),
        ({"connectorVersionPin": "2.0.1"}, "connector_version_pin_mismatch"),
        ({"pluginManifestDigest": DIGEST_A}, "plugin_manifest_digest_mismatch"),
        ({"connectorManifestDigest": DIGEST_A}, "connector_manifest_digest_mismatch"),
        ({"pluginSupplyChainDigest": DIGEST_A}, "plugin_supply_chain_digest_mismatch"),
        ({"connectorSupplyChainDigest": DIGEST_C}, "connector_supply_chain_digest_mismatch"),
        ({"policySnapshotDigest": DIGEST_C}, "policy_digest_mismatch"),
        ({"requiredSandboxMode": "isolated_process"}, "sandbox_mode_mismatch"),
    ),
)
def test_missing_or_mismatched_pins_digests_and_sandbox_fail_closed(
    override: dict[str, object],
    reason: str,
) -> None:
    receipt = evaluate_marketplace_promotion_request(
        _request(**override),
        plugin_manifest=_plugin_manifest(),
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        local_fake_enabled=True,
    )

    assert receipt.status == "blocked"
    assert receipt.allowed is False
    assert reason in receipt.reason_codes


@pytest.mark.parametrize(
    "revocations, reason",
    (
        (
            MarketplaceRevocationSnapshot(revokedPluginRefs=("vendor.docs-plugin",)),
            "plugin_revoked",
        ),
        (
            MarketplaceRevocationSnapshot(revokedConnectorRefs=("connector:docs",)),
            "connector_revoked",
        ),
        (
            MarketplaceRevocationSnapshot(revokedPublisherRefs=("publisher:vendor",)),
            "publisher_revoked",
        ),
        (
            MarketplaceRevocationSnapshot(revokedSupplyChainDigests=(DIGEST_C,)),
            "supply_chain_revoked",
        ),
    ),
)
def test_plugin_connector_publisher_and_supply_chain_revocation_blocks(
    revocations: MarketplaceRevocationSnapshot,
    reason: str,
) -> None:
    receipt = evaluate_marketplace_promotion_request(
        _request(),
        plugin_manifest=_plugin_manifest(),
        connector_manifest=_connector_manifest(),
        revocations=revocations,
        local_fake_enabled=True,
    )

    assert receipt.status == "blocked"
    assert receipt.allowed is False
    assert reason in receipt.reason_codes


def test_sandbox_overreach_and_untrusted_execution_fail_closed() -> None:
    sandbox_overreach = evaluate_marketplace_promotion_request(
        _request(requestedPluginPermissions=["read", "write"]),
        plugin_manifest=_plugin_manifest(
            permissions=["read", "write"],
            sandbox={
                "mode": "in_process_contract_only",
                "filesystem": "none",
                "network": "none",
                "protectedBindingAccess": "none",
                "process": "none",
                "workspaceMutation": False,
                "channelDelivery": False,
            },
        ),
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        local_fake_enabled=True,
    )
    untrusted = evaluate_marketplace_promotion_request(
        _request(),
        plugin_manifest=_plugin_manifest(trustLevel="untrusted"),
        connector_manifest=_connector_manifest(),
        revocations=MarketplaceRevocationSnapshot(),
        local_fake_enabled=True,
    )

    assert sandbox_overreach.status == "blocked"
    assert "plugin_sandbox_overreach" in sandbox_overreach.reason_codes
    assert untrusted.status == "blocked"
    assert "untrusted_execution_not_promotable" in untrusted.reason_codes


def test_public_projection_and_validation_errors_do_not_expose_private_material() -> None:
    receipt = _allowed_receipt(metadata={"safeDigest": DIGEST_A})
    projection = receipt.public_projection()
    encoded = _encoded(projection)

    forbidden_fragments = (
        "rawPrompt",
        "rawOutput",
        "toolLog",
        "hiddenReasoning",
        "Authorization",
        "Cookie",
        "sessionKey",
        "token",
        "/Users/kevin/.config",
        "sk-test-private",
    )
    assert all(fragment not in encoded for fragment in forbidden_fragments)
    assert "safeDigest" in encoded
    assert DIGEST_A in encoded

    with pytest.raises((ValidationError, ValueError)) as exc_info:
        MarketplacePromotionRequest.model_validate(
            {
                **_request().model_dump(by_alias=True, mode="json"),
                "metadata": {
                    "raw" + "Prompt": "sk-test-private",
                    "privatePathRef": "/Users/kevin/.config/provider",
                },
            }
        )
    message = str(exc_info.value)
    assert "sk-test-private" not in message
    assert "/Users/kevin/.config/provider" not in message


@pytest.mark.parametrize(
    "metadata",
    (
        {"attempt": 1},
        {"enabled": True},
        {"nested": {"safeRef": "ticket:1"}},
    ),
)
def test_marketplace_metadata_projection_is_digest_or_ref_only(metadata: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError), match="digest or safe public refs"):
        _request(metadata=metadata)


def test_authority_flags_cannot_be_forged_by_constructor_validate_construct_or_copy() -> None:
    constructor_flags = MarketplaceAuthorityFlags(
        marketplaceLiveSyncEnabled=True,
        pluginExecutionEnabled=True,
        credentialReadEnabled=True,
        networkCallAllowed=True,
        routeOrApiAttached=True,
        productionAuthority=True,
    )
    validate_flags = MarketplaceAuthorityFlags.model_validate(
        {
            "marketplaceLiveSyncEnabled": True,
            "pluginExecutionEnabled": True,
            "credentialReadEnabled": True,
            "networkCallAllowed": True,
            "routeOrApiAttached": True,
            "productionAuthority": True,
        }
    )
    construct_flags = MarketplaceAuthorityFlags.model_construct(
        marketplaceLiveSyncEnabled=True,
        pluginExecutionEnabled=True,
        credentialReadEnabled=True,
        networkCallAllowed=True,
        routeOrApiAttached=True,
        productionAuthority=True,
    )
    copy_flags = MarketplaceAuthorityFlags().model_copy(
        update={
            "marketplaceLiveSyncEnabled": True,
            "pluginExecutionEnabled": True,
            "credentialReadEnabled": True,
            "networkCallAllowed": True,
            "routeOrApiAttached": True,
            "productionAuthority": True,
        }
    )

    for flags in (constructor_flags, validate_flags, construct_flags, copy_flags):
        assert flags.marketplace_live_sync_enabled is False
        assert flags.plugin_execution_enabled is False
        assert flags.credential_read_enabled is False
        assert flags.network_call_allowed is False
        assert flags.route_or_api_attached is False
        assert flags.production_authority is False
        assert set(flags.public_projection().values()) == {False}

    receipt = MarketplacePromotionReceipt.model_construct(
        **{
            **_allowed_receipt().model_dump(by_alias=True, mode="json"),
            "allowed": True,
            "authorityFlags": {"pluginExecutionEnabled": True, "productionAuthority": True},
        }
    )
    forged_copy = receipt.model_copy(
        update={"authorityFlags": {"pluginExecutionEnabled": True, "productionAuthority": True}}
    )
    assert set(receipt.public_projection()["authorityFlags"].values()) == {False}
    assert set(forged_copy.public_projection()["authorityFlags"].values()) == {False}


def test_marketplace_import_boundary_has_no_live_provider_or_runtime_imports() -> None:
    script = """
import sys
import magi_agent.connectors.marketplace
forbidden_prefixes = (
    'google.adk.runners',
    'google.adk.agents',
    'google.adk.tools',
    'magi_agent.adk_bridge',
    'magi_agent.runtime',
    'magi_agent.tools.dispatcher',
    'magi_agent.tools.registry',
    'magi_agent.transport.plugins',
    'magi_agent.plugins.manager',
    'stripe',
    'supabase',
    'psycopg',
    'httpx',
    'requests',
    'kubernetes',
    'keyring',
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden_prefixes)
]
if loaded:
    raise SystemExit(','.join(loaded))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_pr11_matrix_row_refs_point_to_implemented_contract_files() -> None:
    matrix = json.loads(MATRIX_PATH.read_text())
    row = next(
        item
        for item in matrix["rows"]
        if item["id"] == "connector_marketplace_plugin_runtime_promotion"
    )
    covered_paths = {
        item["path"] if isinstance(item, dict) else item for item in row["latestMainCoveredRefs"]
    }
    missing_paths = {
        item["path"] if isinstance(item, dict) else item for item in row["missingImplementation"]
    }

    assert "magi_agent/connectors/marketplace.py" in covered_paths
    assert "tests/test_connector_marketplace_promotion_contract.py" in covered_paths
    assert "magi_agent/connectors/marketplace.py" not in missing_paths
    assert "tests/test_connector_marketplace_promotion_contract.py" not in missing_paths
    assert row["implementationStatus"] == "local_fake"
