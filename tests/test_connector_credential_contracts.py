from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.connectors.credential_lease import (
    ConnectorCredentialLeaseRequest,
    ConnectorCredentialLeaseReceipt,
    CredentialLeaseReplayLedger,
    CredentialLeaseAuthorityFlags,
    issue_credential_lease,
)
from magi_agent.connectors.registry import (
    ConnectorManifest,
    ConnectorPermission,
    ConnectorRegistry,
    ConnectorRegistryReceipt,
    ConnectorToolRef,
    connector_manifest_content_digest,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _manifest(**overrides: object) -> ConnectorManifest:
    data: dict[str, object] = {
        "connectorId": "connector:docs",
        "displayName": "Docs Connector",
        "version": "1.0.0",
        "publisherRef": "publisher:openmagi",
        "supplyChainDigest": DIGEST_A,
        "sandboxMode": "metadata_only",
        "permissions": [
            {"permissionId": "permission:docs-read", "kind": "read", "scope": "scope:docs"},
        ],
        "tools": [
            {
                "toolId": "tool:docs-read",
                "permissionRefs": ["permission:docs-read"],
                "audience": "audience:docs",
            }
        ],
        "policySnapshotDigest": DIGEST_A,
    }
    data.update(overrides)
    if "manifestDigest" not in overrides:
        data["manifestDigest"] = connector_manifest_content_digest(data)
    return ConnectorManifest.model_validate(data)


def _lease_manifest(**overrides: object) -> ConnectorManifest:
    return _manifest(sandboxMode="local_fake", **overrides)


def _multi_permission_lease_manifest() -> ConnectorManifest:
    return _manifest(
        sandboxMode="local_fake",
        permissions=[
            {"permissionId": "permission:docs-read", "kind": "read", "scope": "scope:docs"},
            {
                "permissionId": "permission:docs-meta",
                "kind": "metadata",
                "scope": "scope:docs",
            },
        ],
        tools=[
            {
                "toolId": "tool:docs-read",
                "permissionRefs": ["permission:docs-read", "permission:docs-meta"],
                "audience": "audience:docs",
            }
        ],
    )


def _lease_request(**overrides: object) -> ConnectorCredentialLeaseRequest:
    data: dict[str, object] = {
        "requestId": "lease-request:1",
        "tenantId": "tenant:alpha",
        "ownerUserId": "user:owner-1",
        "botId": "bot:assistant-1",
        "connectorId": "connector:docs",
        "toolId": "tool:docs-read",
        "audience": "audience:docs",
        "ttlSeconds": 300,
        "policySnapshotDigest": DIGEST_A,
        "connectorManifestDigest": _lease_manifest().manifest_digest,
        "requestedPermissionRefs": ["permission:docs-read"],
        "nonce": "nonce:" + "1" * 32,
    }
    data.update(overrides)
    return ConnectorCredentialLeaseRequest.model_validate(data)


def test_connector_manifest_projects_metadata_only_without_secrets() -> None:
    manifest = _manifest()
    projection = manifest.public_projection()

    assert projection["connectorId"] == "connector:docs"
    assert projection["manifestDigest"] == manifest.manifest_digest
    assert projection["supplyChainDigest"] == DIGEST_A
    assert projection["sandboxMode"] == "metadata_only"
    assert projection["authorityFlags"]["pluginExecutionEnabled"] is False
    assert projection["authorityFlags"]["credentialReadEnabled"] is False
    encoded = json.dumps(projection, sort_keys=True)
    assert "raw" + "Prompt" not in encoded
    assert "Authorization" not in encoded
    assert "Cookie" not in encoded
    assert "unsafe" not in encoded


def test_connector_manifest_rejects_secret_material_and_unsafe_refs() -> None:
    with pytest.raises((ValidationError, ValueError)):
        _manifest(metadata={"auth" + "Header": "Bearer unsafe"})
    with pytest.raises((ValidationError, ValueError)):
        _manifest(displayName="Docs with Bearer unsafe")
    with pytest.raises((ValidationError, ValueError)):
        _manifest(version="1.0.0-/Users/example")
    with pytest.raises((ValidationError, ValueError)):
        _manifest(connectorId="/Users/example/.config/provider")
    with pytest.raises(ValidationError):
        ConnectorPermission(permissionId="permission:bad", kind="read", scope="Bearer unsafe")


def test_connector_manifest_digest_is_bound_to_canonical_content() -> None:
    manifest = _manifest()
    assert manifest.manifest_digest == connector_manifest_content_digest(
        manifest.model_dump(by_alias=True, mode="json")
    )

    with pytest.raises(ValidationError, match="canonical manifest content"):
        _manifest(manifestDigest=DIGEST_B)

    stale_digest = manifest.manifest_digest
    with pytest.raises(ValidationError, match="canonical manifest content"):
        _manifest(displayName="Changed Connector", manifestDigest=stale_digest)


def test_connector_manifest_rejects_duplicate_permission_ids() -> None:
    with pytest.raises(ValidationError, match="permission ids must be unique"):
        _manifest(
            permissions=[
                {"permissionId": "permission:docs-read", "kind": "read", "scope": "scope:docs"},
                {
                    "permissionId": "permission:docs-read",
                    "kind": "metadata",
                    "scope": "scope:other",
                },
            ],
            tools=[
                {
                    "toolId": "tool:docs-read",
                    "permissionRefs": ["permission:docs-read"],
                    "audience": "audience:docs",
                }
            ],
        )


def test_connector_manifest_rejects_duplicate_tool_permission_refs() -> None:
    with pytest.raises(ValidationError, match="permission refs must be unique"):
        _manifest(
            tools=[
                {
                    "toolId": "tool:docs-read",
                    "permissionRefs": ["permission:docs-read", "permission:docs-read"],
                    "audience": "audience:docs",
                }
            ],
        )


def test_connector_registry_is_disabled_by_default_and_blocks_unregistered_lookup() -> None:
    registry = ConnectorRegistry(enabled=True)
    assert registry.enabled is False
    assert registry.public_projection()["authorityFlags"]["registryLiveSyncEnabled"] is False
    assert registry.public_projection()["authorityFlags"]["productionAuthority"] is False

    missing = registry.lookup("connector:missing")
    assert missing.status == "missing"
    assert missing.allowed is False
    assert "connector_not_registered" in missing.reason_codes


def test_connector_registry_registration_is_blocked_until_local_fake_mode() -> None:
    registry = ConnectorRegistry()
    manifest = _manifest()
    blocked = registry.register(manifest)

    assert blocked.status == "blocked"
    assert blocked.allowed is False
    assert "registry_disabled" in blocked.reason_codes
    assert registry.public_projection()["registeredConnectorCount"] == 0


def test_connector_registry_requires_explicit_bool_for_local_fake_mode() -> None:
    with pytest.raises(ValueError, match="explicit bool"):
        ConnectorRegistry(local_fake_enabled="false")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="explicit bool"):
        ConnectorRegistry(local_fake_enabled=1)  # type: ignore[arg-type]


def test_connector_registry_registers_local_fake_metadata_and_rejects_duplicates() -> None:
    registry = ConnectorRegistry(local_fake_enabled=True)
    manifest = _manifest()
    registered = registry.register(manifest)

    assert registered.status == "registered"
    assert registered.manifest_digest == manifest.manifest_digest
    assert "local_fake" in registered.reason_codes[0]
    assert registry.lookup("connector:docs").allowed is True
    with pytest.raises(ValueError, match="already registered"):
        registry.register(manifest)


def test_connector_registry_receipt_rejects_forged_allowed_state() -> None:
    with pytest.raises(ValidationError, match="allowed flag"):
        ConnectorRegistryReceipt(
            connectorId="connector:docs",
            status="blocked",
            allowed=True,
            manifestDigest=_manifest().manifest_digest,
            reasonCodes=("registry_disabled",),
        )
    with pytest.raises(ValidationError, match="allowed flag"):
        ConnectorRegistryReceipt(
            connectorId="connector:docs",
            status="missing",
            allowed=True,
            reasonCodes=("connector_not_registered",),
        )
    with pytest.raises(ValidationError, match="allowed flag"):
        ConnectorRegistryReceipt(
            connectorId="connector:docs",
            status="registered",
            allowed=False,
            manifestDigest=_manifest().manifest_digest,
            reasonCodes=("local_fake_connector_registered",),
        )


def test_credential_lease_default_off_fails_closed_without_secret_read() -> None:
    receipt = issue_credential_lease(_lease_request(), manifest=_lease_manifest())

    assert receipt.status == "fail_closed"
    assert receipt.lease_ref is None
    assert receipt.secret_material_present is False
    assert receipt.live_secret_read is False
    assert "lease_disabled" in receipt.reason_codes


def test_credential_lease_local_fake_issues_metadata_only_scoped_receipt() -> None:
    request = _lease_request(requestId="lease-request:issued", nonce="nonce:" + "2" * 32)
    receipt = issue_credential_lease(
        request,
        manifest=_lease_manifest(),
        local_fake_enabled=True,
        now=datetime(2026, 5, 25, tzinfo=UTC),
        replay_ledger=CredentialLeaseReplayLedger(),
    )
    projection = receipt.public_projection()

    assert receipt.status == "issued"
    assert receipt.lease_ref is not None
    assert receipt.lease_ref.startswith("lease:connector-docs-tool-docs-read-")
    assert request.request_digest.removeprefix("sha256:")[:16] in receipt.lease_ref
    assert receipt.expires_at == datetime(2026, 5, 25, 0, 5, tzinfo=UTC)
    assert projection["ttlSeconds"] == 300
    assert projection["redactionStatus"] == "metadata_only"
    assert projection["leaseDigest"].startswith("sha256:")
    assert projection["secretMaterialPresent"] is False
    assert projection["liveSecretRead"] is False
    encoded = json.dumps(projection, sort_keys=True)
    assert "raw" + "Prompt" not in encoded
    assert "session" + "Key" not in encoded
    assert "Cookie" not in encoded


@pytest.mark.parametrize(
    "override, reason",
    (
        ({"connectorId": "connector:other"}, "connector"),
        ({"toolId": "tool:other"}, "tool"),
        ({"audience": "audience:other"}, "audience"),
        ({"policySnapshotDigest": DIGEST_B}, "policy"),
        ({"requestedPermissionRefs": ["permission:write"]}, "permission"),
    ),
)
def test_credential_lease_request_must_match_manifest_scope(
    override: dict[str, object],
    reason: str,
) -> None:
    receipt = issue_credential_lease(
        _lease_request(**override),
        manifest=_lease_manifest(),
        local_fake_enabled=True,
        replay_ledger=CredentialLeaseReplayLedger(),
    )

    assert receipt.status == "fail_closed"
    assert receipt.lease_ref is None
    assert any(reason in code for code in receipt.reason_codes)


def test_credential_lease_request_rejects_ttl_over_contract_limit() -> None:
    with pytest.raises(ValidationError):
        _lease_request(ttlSeconds=601)


def test_credential_lease_requires_local_fake_manifest_sandbox() -> None:
    for sandbox_mode in ("metadata_only", "hosted_disabled"):
        manifest = _manifest(sandboxMode=sandbox_mode)
        request = _lease_request(connectorManifestDigest=manifest.manifest_digest)
        receipt = issue_credential_lease(
            request,
            manifest=manifest,
            local_fake_enabled=True,
            replay_ledger=CredentialLeaseReplayLedger(),
        )

        assert receipt.status == "fail_closed"
        assert receipt.lease_ref is None
        assert "local_fake_sandbox_required" in receipt.reason_codes


def test_credential_lease_requires_explicit_bool_for_local_fake_mode() -> None:
    with pytest.raises(ValueError, match="explicit bool"):
        issue_credential_lease(
            _lease_request(requestId="lease-request:string-bool", nonce="nonce:" + "a" * 32),
            manifest=_lease_manifest(),
            local_fake_enabled="false",  # type: ignore[arg-type]
            replay_ledger=CredentialLeaseReplayLedger(),
        )
    with pytest.raises(ValueError, match="explicit bool"):
        issue_credential_lease(
            _lease_request(requestId="lease-request:int-bool", nonce="nonce:" + "b" * 32),
            manifest=_lease_manifest(),
            local_fake_enabled=1,  # type: ignore[arg-type]
            replay_ledger=CredentialLeaseReplayLedger(),
        )


def test_credential_lease_nonce_requires_high_entropy_and_replay_is_blocked() -> None:
    with pytest.raises(ValidationError, match="128 bits"):
        _lease_request(nonce="nonce:lease-1")

    ledger = CredentialLeaseReplayLedger()
    request = _lease_request(requestId="lease-request:replay", nonce="nonce:" + "3" * 32)
    replay_with_mutable_fields = _lease_request(
        requestId="lease-request:replay-other",
        nonce="nonce:" + "3" * 32,
        metadata={"safeRef": "connector:docs"},
    )
    first = issue_credential_lease(
        request,
        manifest=_lease_manifest(),
        local_fake_enabled=True,
        now=datetime(2026, 5, 25, tzinfo=UTC),
        replay_ledger=ledger,
    )
    second = issue_credential_lease(
        replay_with_mutable_fields,
        manifest=_lease_manifest(),
        local_fake_enabled=True,
        now=datetime(2026, 5, 25, 0, 1, tzinfo=UTC),
        replay_ledger=ledger,
    )

    assert first.status == "issued"
    assert second.status == "fail_closed"
    assert second.lease_ref is None
    assert "lease_replay_detected" in second.reason_codes


def test_credential_lease_replay_scope_canonicalizes_permission_ref_order() -> None:
    manifest = _multi_permission_lease_manifest()
    ledger = CredentialLeaseReplayLedger()
    first_request = _lease_request(
        requestId="lease-request:ordered",
        connectorManifestDigest=manifest.manifest_digest,
        requestedPermissionRefs=["permission:docs-read", "permission:docs-meta"],
        nonce="nonce:" + "8" * 32,
    )
    reordered_request = _lease_request(
        requestId="lease-request:reordered",
        connectorManifestDigest=manifest.manifest_digest,
        requestedPermissionRefs=["permission:docs-meta", "permission:docs-read"],
        nonce="nonce:" + "8" * 32,
    )

    first = issue_credential_lease(
        first_request,
        manifest=manifest,
        local_fake_enabled=True,
        now=datetime(2026, 5, 25, tzinfo=UTC),
        replay_ledger=ledger,
    )
    second = issue_credential_lease(
        reordered_request,
        manifest=manifest,
        local_fake_enabled=True,
        now=datetime(2026, 5, 25, 0, 1, tzinfo=UTC),
        replay_ledger=ledger,
    )

    assert first.status == "issued"
    assert second.status == "fail_closed"
    assert "lease_replay_detected" in second.reason_codes


def test_credential_lease_replay_scope_ignores_ttl_variation() -> None:
    ledger = CredentialLeaseReplayLedger()
    first_request = _lease_request(requestId="lease-request:ttl-300", nonce="nonce:" + "9" * 32)
    replay_request = _lease_request(
        requestId="lease-request:ttl-299",
        ttlSeconds=299,
        nonce="nonce:" + "9" * 32,
    )

    first = issue_credential_lease(
        first_request,
        manifest=_lease_manifest(),
        local_fake_enabled=True,
        now=datetime(2026, 5, 25, tzinfo=UTC),
        replay_ledger=ledger,
    )
    second = issue_credential_lease(
        replay_request,
        manifest=_lease_manifest(),
        local_fake_enabled=True,
        now=datetime(2026, 5, 25, 0, 1, tzinfo=UTC),
        replay_ledger=ledger,
    )

    assert first.status == "issued"
    assert second.status == "fail_closed"
    assert "lease_replay_detected" in second.reason_codes


def test_credential_lease_request_rejects_duplicate_permission_refs() -> None:
    with pytest.raises(ValidationError, match="permission refs must be unique"):
        _lease_request(
            requestedPermissionRefs=["permission:docs-read", "permission:docs-read"],
        )


def test_credential_lease_digest_binds_validity_window_and_rejects_naive_times() -> None:
    request = _lease_request(requestId="lease-request:window-1", nonce="nonce:" + "4" * 32)
    first = issue_credential_lease(
        request,
        manifest=_lease_manifest(),
        local_fake_enabled=True,
        now=datetime(2026, 5, 25, tzinfo=UTC),
        replay_ledger=CredentialLeaseReplayLedger(),
    )
    second = issue_credential_lease(
        _lease_request(requestId="lease-request:window-2", nonce="nonce:" + "5" * 32),
        manifest=_lease_manifest(),
        local_fake_enabled=True,
        now=datetime(2026, 5, 25, 0, 1, tzinfo=UTC),
        replay_ledger=CredentialLeaseReplayLedger(),
    )

    assert first.lease_digest != second.lease_digest
    with pytest.raises(ValidationError, match="exactly match ttl"):
        ConnectorCredentialLeaseReceipt(
            requestId="lease-request:short-window",
            tenantId="tenant:alpha",
            ownerUserId="user:owner-1",
            botId="bot:assistant-1",
            connectorId="connector:docs",
            toolId="tool:docs-read",
            audience="audience:docs",
            status="issued",
            ttlSeconds=300,
            issuedAt=datetime(2026, 5, 25, tzinfo=UTC),
            expiresAt=datetime(2026, 5, 25, 0, 1, tzinfo=UTC),
            policySnapshotDigest=DIGEST_A,
            connectorManifestDigest=_lease_manifest().manifest_digest,
            reasonCodes=("local_fake_lease_issued",),
            leaseRef="lease:docs",
            requestDigest=request.request_digest,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        issue_credential_lease(
            _lease_request(requestId="lease-request:naive", nonce="nonce:" + "6" * 32),
            manifest=_lease_manifest(),
            local_fake_enabled=True,
            now=datetime(2026, 5, 25),
            replay_ledger=CredentialLeaseReplayLedger(),
        )


def test_credential_lease_rejects_raw_secret_projection_and_authority_forgery() -> None:
    with pytest.raises(ValidationError):
        ConnectorCredentialLeaseRequest.model_validate(
            {
                **_lease_request().model_dump(by_alias=True, mode="json"),
                "metadata": {"connector" + "Token": "unsafe"},
            }
        )
    with pytest.raises(ValidationError):
        ConnectorCredentialLeaseReceipt(
            requestId="lease-request:raw",
            tenantId="tenant:alpha",
            ownerUserId="user:owner-1",
            botId="bot:assistant-1",
            connectorId="connector:docs",
            toolId="tool:docs-read",
            audience="audience:docs",
            status="issued",
            ttlSeconds=300,
            issuedAt=datetime.now(UTC),
            expiresAt=datetime.now(UTC) + timedelta(seconds=300),
            policySnapshotDigest=DIGEST_A,
            connectorManifestDigest=_lease_manifest().manifest_digest,
            reasonCodes=("local_fake_lease_issued",),
            leaseRef="lease:docs",
            **{"raw" + "Secret" + "Material": "unsafe"},
        )

    flags = CredentialLeaseAuthorityFlags.model_validate(
        {
            "credentialReadEnabled": True,
            "liveSecretRead": True,
            "pluginExecutionEnabled": True,
            "productionAuthority": True,
        }
    )
    assert set(flags.public_projection().values()) == {False}
    with pytest.raises(ValueError):
        flags.model_copy(update={"credentialReadEnabled": True})


def test_credential_lease_metadata_record_is_digest_only_for_durable_store() -> None:
    receipt = issue_credential_lease(
        _lease_request(requestId="lease-request:durable", nonce="nonce:" + "7" * 32),
        manifest=_lease_manifest(),
        local_fake_enabled=True,
        now=datetime(2026, 5, 25, tzinfo=UTC),
        replay_ledger=CredentialLeaseReplayLedger(),
    )
    durable = receipt.to_durable_metadata_record(record_id="lease-docs-1")
    payload = durable.storage_payload()
    encoded = json.dumps(payload, sort_keys=True)

    assert durable.collection == "credential_lease_metadata"
    assert durable.content_digest == receipt.lease_digest
    assert payload["recordId"] == "lease-ref:" + receipt.lease_digest
    assert "lease-docs-1" not in encoded
    assert receipt.lease_ref not in encoded
    assert "connector:docs" not in encoded
    assert "tool:docs-read" not in encoded
    assert receipt.status not in encoded
    assert "raw" + "Secret" not in encoded
    assert "Cookie" not in encoded


def test_connectors_import_boundary_has_no_live_secret_or_provider_imports() -> None:
    script = """
import sys
import magi_agent.connectors.registry
import magi_agent.connectors.credential_lease
for name in (
    'stripe',
    'supabase',
    'psycopg',
    'httpx',
    'requests',
    'kubernetes',
    'keyring',
    'google.adk.runners',
):
    if name in sys.modules:
        raise SystemExit(name)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
