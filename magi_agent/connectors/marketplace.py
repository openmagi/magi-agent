from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.connectors.registry import ConnectorManifest
from magi_agent.ops.safety import (
    canonical_digest,
    require_digest,
    require_safe_key,
    require_safe_ref,
    reject_private_text,
    serialize_safe_value,
)
from magi_agent.plugins.manifest import PermissionClass, PluginManifest
from magi_agent.plugins.sandbox_policy import SandboxMode, evaluate_plugin_sandbox


MarketplaceOperation = Literal["install", "update", "remove"]
MarketplacePromotionStatus = Literal["allowed", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_AUTHORITY_FLAG_FIELDS = frozenset({
    "marketplace_live_sync_enabled",
    "plugin_execution_enabled",
    "credential_read_enabled",
    "live_secret_read",
    "network_call_allowed",
    "route_or_api_attached",
    "production_authority",
    "model_called",
    "toolhost_dispatched",
    "runtime_activation_allowed",
})
_IMMUTABLE_VERSION_RE = re.compile(
    r"^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?$"
)


def _digest_payload(payload: Mapping[str, object]) -> str:
    return canonical_digest(payload)


def plugin_manifest_content_digest(value: PluginManifest | Mapping[str, object]) -> str:
    """Return the canonical digest for authority-relevant plugin manifest content.

    ``PluginManifest`` treats ``manifestDigest`` as metadata for compatibility
    with existing fixtures. Marketplace promotion needs a content-bound digest,
    so this helper excludes the self-reported digest and binds the plugin id,
    version, publisher, permissions, trust, sandbox, capabilities, hooks, tools,
    secrets, and supply-chain metadata.
    """

    if isinstance(value, PluginManifest):
        payload = value.model_dump(by_alias=True, mode="json")
    else:
        payload = dict(value)
    payload.pop("manifestDigest", None)
    payload.pop("manifest_digest", None)
    return _digest_payload(payload)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _dedupe_sorted_refs(value: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    refs = tuple(require_safe_ref(item, field_name=field_name) for item in value)
    if len(set(refs)) != len(refs):
        raise ValueError(f"{field_name} must be unique")
    return tuple(sorted(refs))


def _dedupe_sorted_permissions(value: tuple[PermissionClass, ...]) -> tuple[PermissionClass, ...]:
    permissions = tuple(value)
    if len(set(permissions)) != len(permissions):
        raise ValueError("requestedPluginPermissions must be unique")
    return tuple(sorted(permissions))


def _safe_version_pin(value: str, *, field_name: str) -> str:
    clean = value.strip()
    if not clean or len(clean) > 120:
        raise ValueError(f"{field_name} must be a safe version pin")
    if _IMMUTABLE_VERSION_RE.fullmatch(clean) is None:
        raise ValueError(f"{field_name} must be an immutable exact version pin")
    reject_private_text(clean, field_name=field_name)
    return clean


def _digest_ref_metadata(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
        if not isinstance(key, str):
            raise ValueError("metadata keys must be strings")
        safe[require_safe_key(key, field_name="metadata")] = _digest_ref_metadata_value(item)
    return safe


def _digest_ref_metadata_value(value: object) -> object:
    if isinstance(value, str):
        if value.startswith("sha256:"):
            return require_digest(value)
        return require_safe_ref(value, field_name="metadata")
    if isinstance(value, tuple | list):
        return tuple(_digest_ref_metadata_value(item) for item in value)
    raise ValueError("metadata must contain only digest or safe public refs")


class _MarketplaceModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        data = self.model_dump(by_alias=True, mode="json")
        if update:
            data.update(update)
        _ = deep
        return type(self).model_validate(data)


class MarketplaceAuthorityFlags(_MarketplaceModel):
    marketplace_live_sync_enabled: Literal[False] = Field(
        default=False,
        alias="marketplaceLiveSyncEnabled",
    )
    plugin_execution_enabled: Literal[False] = Field(
        default=False,
        alias="pluginExecutionEnabled",
    )
    credential_read_enabled: Literal[False] = Field(
        default=False,
        alias="credentialReadEnabled",
    )
    live_secret_read: Literal[False] = Field(default=False, alias="liveSecretRead")
    network_call_allowed: Literal[False] = Field(default=False, alias="networkCallAllowed")
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    toolhost_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    runtime_activation_allowed: Literal[False] = Field(
        default=False,
        alias="runtimeActivationAllowed",
    )

    def __getattribute__(self, name: str) -> object:
        if name in _AUTHORITY_FLAG_FIELDS:
            return False
        return super().__getattribute__(name)

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field_name, field in cls.model_fields.items():
            payload[field.alias or field_name] = False
            payload.pop(field_name, None)
        return payload

    @field_serializer(
        "marketplace_live_sync_enabled",
        "plugin_execution_enabled",
        "credential_read_enabled",
        "live_secret_read",
        "network_call_allowed",
        "route_or_api_attached",
        "production_authority",
        "model_called",
        "toolhost_dispatched",
        "runtime_activation_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    def public_projection(self) -> dict[str, bool]:
        return {
            "marketplaceLiveSyncEnabled": False,
            "pluginExecutionEnabled": False,
            "credentialReadEnabled": False,
            "liveSecretRead": False,
            "networkCallAllowed": False,
            "routeOrApiAttached": False,
            "productionAuthority": False,
            "modelCalled": False,
            "toolHostDispatched": False,
            "runtimeActivationAllowed": False,
        }


class MarketplaceRevocationSnapshot(_MarketplaceModel):
    revoked_plugin_refs: tuple[str, ...] = Field(default=(), alias="revokedPluginRefs")
    revoked_connector_refs: tuple[str, ...] = Field(default=(), alias="revokedConnectorRefs")
    revoked_publisher_refs: tuple[str, ...] = Field(default=(), alias="revokedPublisherRefs")
    revoked_supply_chain_digests: tuple[str, ...] = Field(
        default=(),
        alias="revokedSupplyChainDigests",
    )

    @field_validator("revoked_plugin_refs", "revoked_connector_refs", "revoked_publisher_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        return _dedupe_sorted_refs(value, field_name=getattr(info, "field_name", "revokedRefs"))

    @field_validator("revoked_supply_chain_digests")
    @classmethod
    def _validate_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        digests = tuple(require_digest(item) for item in value)
        if len(set(digests)) != len(digests):
            raise ValueError("revokedSupplyChainDigests must be unique")
        return tuple(sorted(digests))

    @property
    def snapshot_digest(self) -> str:
        return _digest_payload(
            {
                "schemaVersion": "openmagi.connector.marketplace.revocations.v1",
                "revokedPluginRefs": list(self.revoked_plugin_refs),
                "revokedConnectorRefs": list(self.revoked_connector_refs),
                "revokedPublisherRefs": list(self.revoked_publisher_refs),
                "revokedSupplyChainDigests": list(self.revoked_supply_chain_digests),
            }
        )


class MarketplacePromotionRequest(_MarketplaceModel):
    schema_version: Literal["openmagi.connector.marketplace_promotion_request.v1"] = Field(
        default="openmagi.connector.marketplace_promotion_request.v1",
        alias="schemaVersion",
    )
    request_id: str = Field(alias="requestId")
    operation: MarketplaceOperation
    plugin_id: str = Field(alias="pluginId")
    connector_id: str = Field(alias="connectorId")
    publisher_ref: str = Field(alias="publisherRef")
    plugin_version_pin: str | None = Field(alias="pluginVersionPin")
    connector_version_pin: str | None = Field(alias="connectorVersionPin")
    plugin_manifest_digest: str | None = Field(alias="pluginManifestDigest")
    connector_manifest_digest: str | None = Field(alias="connectorManifestDigest")
    plugin_supply_chain_digest: str | None = Field(alias="pluginSupplyChainDigest")
    connector_supply_chain_digest: str | None = Field(alias="connectorSupplyChainDigest")
    policy_snapshot_digest: str | None = Field(alias="policySnapshotDigest")
    required_sandbox_mode: SandboxMode | None = Field(alias="requiredSandboxMode")
    requested_plugin_permissions: tuple[PermissionClass, ...] = Field(
        alias="requestedPluginPermissions",
    )
    requested_connector_permission_refs: tuple[str, ...] = Field(
        alias="requestedConnectorPermissionRefs",
    )
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("request_id", "plugin_id", "connector_id", "publisher_ref")
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("plugin_version_pin", "connector_version_pin")
    @classmethod
    def _validate_optional_pin(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _safe_version_pin(value, field_name=getattr(info, "field_name", "versionPin"))

    @field_validator(
        "plugin_manifest_digest",
        "connector_manifest_digest",
        "plugin_supply_chain_digest",
        "connector_supply_chain_digest",
        "policy_snapshot_digest",
    )
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("requested_plugin_permissions")
    @classmethod
    def _validate_plugin_permissions(
        cls,
        value: tuple[PermissionClass, ...],
    ) -> tuple[PermissionClass, ...]:
        if not value:
            raise ValueError("requestedPluginPermissions are required")
        return _dedupe_sorted_permissions(value)

    @field_validator("requested_connector_permission_refs")
    @classmethod
    def _validate_connector_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("requestedConnectorPermissionRefs are required")
        return _dedupe_sorted_refs(value, field_name="requestedConnectorPermissionRefs")

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _digest_ref_metadata(value)

    @property
    def request_digest(self) -> str:
        return _digest_payload(
            {
                "schemaVersion": self.schema_version,
                "requestId": self.request_id,
                "operation": self.operation,
                "pluginId": self.plugin_id,
                "connectorId": self.connector_id,
                "publisherRef": self.publisher_ref,
                "pluginVersionPin": self.plugin_version_pin,
                "connectorVersionPin": self.connector_version_pin,
                "pluginManifestDigest": self.plugin_manifest_digest,
                "connectorManifestDigest": self.connector_manifest_digest,
                "pluginSupplyChainDigest": self.plugin_supply_chain_digest,
                "connectorSupplyChainDigest": self.connector_supply_chain_digest,
                "policySnapshotDigest": self.policy_snapshot_digest,
                "requiredSandboxMode": self.required_sandbox_mode,
                "requestedPluginPermissions": list(self.requested_plugin_permissions),
                "requestedConnectorPermissionRefs": list(self.requested_connector_permission_refs),
                "metadata": dict(sorted(self.metadata.items())),
            }
        )


class MarketplacePromotionReceipt(_MarketplaceModel):
    schema_version: Literal["openmagi.connector.marketplace_promotion_receipt.v1"] = Field(
        default="openmagi.connector.marketplace_promotion_receipt.v1",
        alias="schemaVersion",
    )
    request_id: str = Field(alias="requestId")
    operation: MarketplaceOperation
    status: MarketplacePromotionStatus
    allowed: bool
    contract_only: Literal[True] = Field(default=True, alias="contractOnly")
    plugin_id: str = Field(alias="pluginId")
    connector_id: str = Field(alias="connectorId")
    publisher_ref: str = Field(alias="publisherRef")
    plugin_version_pin: str | None = Field(default=None, alias="pluginVersionPin")
    connector_version_pin: str | None = Field(default=None, alias="connectorVersionPin")
    plugin_manifest_digest: str | None = Field(default=None, alias="pluginManifestDigest")
    connector_manifest_digest: str | None = Field(default=None, alias="connectorManifestDigest")
    plugin_supply_chain_digest: str | None = Field(default=None, alias="pluginSupplyChainDigest")
    connector_supply_chain_digest: str | None = Field(
        default=None,
        alias="connectorSupplyChainDigest",
    )
    policy_snapshot_digest: str | None = Field(default=None, alias="policySnapshotDigest")
    sandbox_mode: SandboxMode | None = Field(default=None, alias="sandboxMode")
    requested_plugin_permissions: tuple[PermissionClass, ...] = Field(
        default=(),
        alias="requestedPluginPermissions",
    )
    requested_connector_permission_refs: tuple[str, ...] = Field(
        default=(),
        alias="requestedConnectorPermissionRefs",
    )
    revocation_snapshot_digest: str = Field(alias="revocationSnapshotDigest")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    request_digest: str = Field(alias="requestDigest")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
    metadata: Mapping[str, object] = Field(default_factory=dict)
    authority_flags: MarketplaceAuthorityFlags = Field(
        default_factory=MarketplaceAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_contract_only(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["contractOnly"] = True
        payload.pop("contract_only", None)
        return payload

    @field_validator("request_id", "plugin_id", "connector_id", "publisher_ref")
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("plugin_version_pin", "connector_version_pin")
    @classmethod
    def _validate_optional_pin(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _safe_version_pin(value, field_name=getattr(info, "field_name", "versionPin"))

    @field_validator(
        "plugin_manifest_digest",
        "connector_manifest_digest",
        "plugin_supply_chain_digest",
        "connector_supply_chain_digest",
        "policy_snapshot_digest",
        "revocation_snapshot_digest",
        "request_digest",
    )
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("requested_plugin_permissions")
    @classmethod
    def _validate_plugin_permissions(
        cls,
        value: tuple[PermissionClass, ...],
    ) -> tuple[PermissionClass, ...]:
        return _dedupe_sorted_permissions(value)

    @field_validator("requested_connector_permission_refs")
    @classmethod
    def _validate_connector_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _dedupe_sorted_refs(value, field_name="requestedConnectorPermissionRefs")

    @field_validator("reason_codes")
    @classmethod
    def _validate_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("marketplace promotion receipt requires reason codes")
        return _dedupe_sorted_refs(value, field_name="reasonCodes")

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _digest_ref_metadata(value)

    @field_validator("created_at")
    @classmethod
    def _validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("createdAt must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _validate_status(self) -> Self:
        if self.allowed != (self.status == "allowed"):
            raise ValueError("marketplace promotion allowed flag must match status")
        return self

    @property
    def receipt_digest(self) -> str:
        return _digest_payload(
            {
                "schemaVersion": self.schema_version,
                "requestId": self.request_id,
                "operation": self.operation,
                "status": self.status,
                "allowed": self.allowed,
                "contractOnly": True,
                "pluginId": self.plugin_id,
                "connectorId": self.connector_id,
                "publisherRef": self.publisher_ref,
                "pluginVersionPin": self.plugin_version_pin,
                "connectorVersionPin": self.connector_version_pin,
                "pluginManifestDigest": self.plugin_manifest_digest,
                "connectorManifestDigest": self.connector_manifest_digest,
                "pluginSupplyChainDigest": self.plugin_supply_chain_digest,
                "connectorSupplyChainDigest": self.connector_supply_chain_digest,
                "policySnapshotDigest": self.policy_snapshot_digest,
                "sandboxMode": self.sandbox_mode,
                "requestedPluginPermissions": list(self.requested_plugin_permissions),
                "requestedConnectorPermissionRefs": list(self.requested_connector_permission_refs),
                "revocationSnapshotDigest": self.revocation_snapshot_digest,
                "reasonCodes": list(self.reason_codes),
                "requestDigest": self.request_digest,
            }
        )

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.connector.marketplace_promotion.public.v1",
            "requestId": self.request_id,
            "operation": self.operation,
            "status": self.status,
            "allowed": self.allowed,
            "contractOnly": True,
            "pluginId": self.plugin_id,
            "connectorId": self.connector_id,
            "publisherRef": self.publisher_ref,
            "pluginVersionPin": self.plugin_version_pin,
            "connectorVersionPin": self.connector_version_pin,
            "pluginManifestDigest": self.plugin_manifest_digest,
            "connectorManifestDigest": self.connector_manifest_digest,
            "pluginSupplyChainDigest": self.plugin_supply_chain_digest,
            "connectorSupplyChainDigest": self.connector_supply_chain_digest,
            "policySnapshotDigest": self.policy_snapshot_digest,
            "sandboxMode": self.sandbox_mode,
            "requestedPluginPermissions": list(self.requested_plugin_permissions),
            "requestedConnectorPermissionRefs": list(self.requested_connector_permission_refs),
            "revocationSnapshotDigest": self.revocation_snapshot_digest,
            "reasonCodes": list(self.reason_codes),
            "requestDigest": self.request_digest,
            "receiptDigest": self.receipt_digest,
            "createdAt": _utc_iso(self.created_at),
            "metadata": {
                key: serialize_safe_value(item)
                for key, item in _digest_ref_metadata(self.metadata).items()
            },
            "authorityFlags": self.authority_flags.public_projection(),
        }


def evaluate_marketplace_promotion_request(
    request: MarketplacePromotionRequest,
    *,
    plugin_manifest: PluginManifest,
    connector_manifest: ConnectorManifest,
    revocations: MarketplaceRevocationSnapshot | None = None,
    local_fake_enabled: bool = False,
) -> MarketplacePromotionReceipt:
    if type(local_fake_enabled) is not bool:
        raise ValueError("local_fake_enabled must be an explicit bool")

    safe_request = MarketplacePromotionRequest.model_validate(
        request.model_dump(by_alias=True, mode="json")
    )
    safe_plugin = PluginManifest.model_validate(plugin_manifest.model_dump(by_alias=True, mode="json"))
    safe_connector = ConnectorManifest.model_validate(
        connector_manifest.model_dump(by_alias=True, mode="json")
    )
    safe_revocations = (
        None
        if revocations is None
        else MarketplaceRevocationSnapshot.model_validate(
            revocations.model_dump(by_alias=True, mode="json")
        )
    )

    if not local_fake_enabled:
        return _receipt(
            safe_request,
            revocations=safe_revocations or MarketplaceRevocationSnapshot(),
            status="blocked",
            reason_codes=(f"marketplace_{safe_request.operation}_disabled",),
        )
    if safe_revocations is None:
        return _receipt(
            safe_request,
            revocations=MarketplaceRevocationSnapshot(),
            status="blocked",
            reason_codes=("revocation_check_required",),
        )

    reasons = _promotion_block_reasons(
        safe_request,
        plugin_manifest=safe_plugin,
        connector_manifest=safe_connector,
        revocations=safe_revocations,
    )
    if reasons:
        return _receipt(
            safe_request,
            revocations=safe_revocations,
            status="blocked",
            reason_codes=tuple(reasons),
        )
    return _receipt(
        safe_request,
        revocations=safe_revocations,
        status="allowed",
        reason_codes=("local_fake_marketplace_promotion_allowed",),
    )


def validate_plugin_runtime_permission_request(
    receipt: MarketplacePromotionReceipt,
    *,
    plugin_manifest: PluginManifest,
    connector_manifest: ConnectorManifest,
    revocations: MarketplaceRevocationSnapshot,
    requested_permissions: tuple[PermissionClass, ...],
    runtime_ref: str,
) -> MarketplacePromotionReceipt:
    safe_runtime_ref = require_safe_ref(runtime_ref, field_name="runtimeRef")
    _ = safe_runtime_ref
    safe_receipt = MarketplacePromotionReceipt.model_validate(
        receipt.model_dump(by_alias=True, mode="json")
    )
    safe_plugin = PluginManifest.model_validate(plugin_manifest.model_dump(by_alias=True, mode="json"))
    safe_connector = ConnectorManifest.model_validate(
        connector_manifest.model_dump(by_alias=True, mode="json")
    )
    safe_revocations = MarketplaceRevocationSnapshot.model_validate(
        revocations.model_dump(by_alias=True, mode="json")
    )
    safe_permissions = _dedupe_sorted_permissions(tuple(requested_permissions))
    manifest_digest = plugin_manifest_content_digest(safe_plugin)
    sandbox_decision = evaluate_plugin_sandbox(safe_plugin)
    manifest_permissions = set(safe_plugin.permissions)
    effective_permissions = set(sandbox_decision.effective_permissions)
    receipt_permissions = set(safe_receipt.requested_plugin_permissions)
    trusted_receipt = _trusted_promotion_receipt(
        safe_receipt,
        plugin_manifest=safe_plugin,
        connector_manifest=safe_connector,
        revocations=safe_revocations,
    )
    if trusted_receipt.status != "allowed" or trusted_receipt.receipt_digest != safe_receipt.receipt_digest:
        return safe_receipt.model_copy(
            update={
                "status": "blocked",
                "allowed": False,
                "reasonCodes": ("plugin_runtime_promotion_receipt_untrusted",),
                "requestedPluginPermissions": safe_permissions,
            }
        )
    if (
        safe_receipt.operation not in {"install", "update"}
        or safe_receipt.status != "allowed"
        or not safe_receipt.allowed
    ):
        return safe_receipt.model_copy(
            update={
                "status": "blocked",
                "allowed": False,
                "reasonCodes": ("runtime_permission_operation_not_granting",),
                "requestedPluginPermissions": safe_permissions,
            }
        )
    if (
        safe_receipt.plugin_id != safe_plugin.plugin_id
        or safe_receipt.plugin_manifest_digest != manifest_digest
        or safe_plugin.manifest_digest != manifest_digest
    ):
        return safe_receipt.model_copy(
            update={
                "status": "blocked",
                "allowed": False,
                "reasonCodes": ("plugin_runtime_manifest_digest_mismatch",),
                "requestedPluginPermissions": safe_permissions,
            }
        )
    if (
        safe_receipt.plugin_version_pin != safe_plugin.version
        or safe_receipt.plugin_supply_chain_digest != safe_plugin.supply_chain_digest
    ):
        return safe_receipt.model_copy(
            update={
                "status": "blocked",
                "allowed": False,
                "reasonCodes": ("plugin_runtime_manifest_mismatch",),
                "requestedPluginPermissions": safe_permissions,
            }
        )
    if not sandbox_decision.ok:
        return safe_receipt.model_copy(
            update={
                "status": "blocked",
                "allowed": False,
                "reasonCodes": ("plugin_runtime_sandbox_overreach",),
                "requestedPluginPermissions": safe_permissions,
            }
        )
    if safe_plugin.trust_level == "untrusted" or sandbox_decision.trust_level == "untrusted":
        return safe_receipt.model_copy(
            update={
                "status": "blocked",
                "allowed": False,
                "reasonCodes": ("plugin_runtime_untrusted_not_promotable",),
                "requestedPluginPermissions": safe_permissions,
            }
        )
    if (
        not set(safe_permissions).issubset(manifest_permissions)
        or not set(safe_permissions).issubset(effective_permissions)
        or not receipt_permissions.issubset(manifest_permissions)
        or not receipt_permissions.issubset(effective_permissions)
    ):
        return safe_receipt.model_copy(
            update={
                "status": "blocked",
                "allowed": False,
                "reasonCodes": ("plugin_runtime_manifest_permission_mismatch",),
                "requestedPluginPermissions": safe_permissions,
            }
        )
    if not set(safe_permissions).issubset(receipt_permissions):
        return safe_receipt.model_copy(
            update={
                "status": "blocked",
                "allowed": False,
                "reasonCodes": ("plugin_runtime_permission_overreach",),
                "requestedPluginPermissions": safe_permissions,
            }
        )
    return safe_receipt.model_copy(
        update={
            "reasonCodes": ("local_fake_runtime_permission_contract_allowed",),
            "requestedPluginPermissions": safe_permissions,
        }
    )


def _trusted_promotion_receipt(
    receipt: MarketplacePromotionReceipt,
    *,
    plugin_manifest: PluginManifest,
    connector_manifest: ConnectorManifest,
    revocations: MarketplaceRevocationSnapshot,
) -> MarketplacePromotionReceipt:
    request = MarketplacePromotionRequest(
        requestId=receipt.request_id,
        operation=receipt.operation,
        pluginId=receipt.plugin_id,
        connectorId=receipt.connector_id,
        publisherRef=receipt.publisher_ref,
        pluginVersionPin=receipt.plugin_version_pin,
        connectorVersionPin=receipt.connector_version_pin,
        pluginManifestDigest=receipt.plugin_manifest_digest,
        connectorManifestDigest=receipt.connector_manifest_digest,
        pluginSupplyChainDigest=receipt.plugin_supply_chain_digest,
        connectorSupplyChainDigest=receipt.connector_supply_chain_digest,
        policySnapshotDigest=receipt.policy_snapshot_digest,
        requiredSandboxMode=receipt.sandbox_mode,
        requestedPluginPermissions=receipt.requested_plugin_permissions,
        requestedConnectorPermissionRefs=receipt.requested_connector_permission_refs,
        metadata=receipt.metadata,
    )
    return evaluate_marketplace_promotion_request(
        request,
        plugin_manifest=plugin_manifest,
        connector_manifest=connector_manifest,
        revocations=revocations,
        local_fake_enabled=True,
    )


def _promotion_block_reasons(
    request: MarketplacePromotionRequest,
    *,
    plugin_manifest: PluginManifest,
    connector_manifest: ConnectorManifest,
    revocations: MarketplaceRevocationSnapshot,
) -> tuple[str, ...]:
    reasons: list[str] = []
    plugin_manifest_digest = plugin_manifest.manifest_digest
    computed_plugin_manifest_digest = plugin_manifest_content_digest(plugin_manifest)
    plugin_supply_chain_digest = plugin_manifest.supply_chain_digest
    plugin_sandbox = plugin_manifest.sandbox
    sandbox_mode = plugin_sandbox.mode if plugin_sandbox is not None else None

    if request.plugin_id != plugin_manifest.plugin_id:
        reasons.append("plugin_mismatch")
    if request.connector_id != connector_manifest.connector_id:
        reasons.append("connector_mismatch")
    if request.publisher_ref != connector_manifest.publisher_ref:
        reasons.append("publisher_mismatch")
    if plugin_manifest.publisher is None:
        reasons.append("plugin_publisher_ref_required")
    elif request.publisher_ref != require_safe_ref(plugin_manifest.publisher, field_name="pluginPublisherRef"):
        reasons.append("publisher_mismatch")

    if request.plugin_version_pin is None:
        reasons.append("plugin_version_pin_required")
    elif request.plugin_version_pin != plugin_manifest.version:
        reasons.append("plugin_version_pin_mismatch")
    if request.connector_version_pin is None:
        reasons.append("connector_version_pin_required")
    elif request.connector_version_pin != connector_manifest.version:
        reasons.append("connector_version_pin_mismatch")

    if request.plugin_manifest_digest is None or plugin_manifest_digest is None:
        reasons.append("plugin_manifest_digest_required")
    elif (
        request.plugin_manifest_digest != computed_plugin_manifest_digest
        or plugin_manifest_digest != computed_plugin_manifest_digest
    ):
        reasons.append("plugin_manifest_digest_mismatch")
    if request.connector_manifest_digest is None:
        reasons.append("connector_manifest_digest_required")
    elif request.connector_manifest_digest != connector_manifest.manifest_digest:
        reasons.append("connector_manifest_digest_mismatch")
    if request.plugin_supply_chain_digest is None or plugin_supply_chain_digest is None:
        reasons.append("plugin_supply_chain_digest_required")
    elif request.plugin_supply_chain_digest != plugin_supply_chain_digest:
        reasons.append("plugin_supply_chain_digest_mismatch")
    if request.connector_supply_chain_digest is None:
        reasons.append("connector_supply_chain_digest_required")
    elif request.connector_supply_chain_digest != connector_manifest.supply_chain_digest:
        reasons.append("connector_supply_chain_digest_mismatch")
    if request.policy_snapshot_digest is None:
        reasons.append("policy_digest_required")
    elif request.policy_snapshot_digest != connector_manifest.policy_snapshot_digest:
        reasons.append("policy_digest_mismatch")

    if request.required_sandbox_mode is None:
        reasons.append("sandbox_mode_required")
    elif request.required_sandbox_mode != sandbox_mode:
        reasons.append("sandbox_mode_mismatch")
    if connector_manifest.sandbox_mode != "local_fake":
        reasons.append("connector_local_fake_sandbox_required")

    sandbox_decision = evaluate_plugin_sandbox(plugin_manifest)
    if not sandbox_decision.ok:
        reasons.append("plugin_sandbox_overreach")
    if plugin_manifest.trust_level == "untrusted":
        reasons.append("untrusted_execution_not_promotable")

    if not set(request.requested_plugin_permissions).issubset(set(plugin_manifest.permissions)):
        reasons.append("plugin_permission_subset_mismatch")
    if sandbox_decision.ok and not set(request.requested_plugin_permissions).issubset(
        set(sandbox_decision.effective_permissions)
    ):
        reasons.append("plugin_permission_subset_mismatch")

    connector_permissions = {permission.permission_id for permission in connector_manifest.permissions}
    if not set(request.requested_connector_permission_refs).issubset(connector_permissions):
        reasons.append("connector_permission_subset_mismatch")

    if request.plugin_id in revocations.revoked_plugin_refs:
        reasons.append("plugin_revoked")
    if request.connector_id in revocations.revoked_connector_refs:
        reasons.append("connector_revoked")
    if request.publisher_ref in revocations.revoked_publisher_refs:
        reasons.append("publisher_revoked")
    if request.plugin_supply_chain_digest in revocations.revoked_supply_chain_digests:
        reasons.append("supply_chain_revoked")
    if request.connector_supply_chain_digest in revocations.revoked_supply_chain_digests:
        reasons.append("supply_chain_revoked")

    return tuple(dict.fromkeys(reasons))


def _receipt(
    request: MarketplacePromotionRequest,
    *,
    revocations: MarketplaceRevocationSnapshot,
    status: MarketplacePromotionStatus,
    reason_codes: tuple[str, ...],
) -> MarketplacePromotionReceipt:
    return MarketplacePromotionReceipt(
        requestId=request.request_id,
        operation=request.operation,
        status=status,
        allowed=status == "allowed",
        contractOnly=True,
        pluginId=request.plugin_id,
        connectorId=request.connector_id,
        publisherRef=request.publisher_ref,
        pluginVersionPin=request.plugin_version_pin,
        connectorVersionPin=request.connector_version_pin,
        pluginManifestDigest=request.plugin_manifest_digest,
        connectorManifestDigest=request.connector_manifest_digest,
        pluginSupplyChainDigest=request.plugin_supply_chain_digest,
        connectorSupplyChainDigest=request.connector_supply_chain_digest,
        policySnapshotDigest=request.policy_snapshot_digest,
        sandboxMode=request.required_sandbox_mode,
        requestedPluginPermissions=request.requested_plugin_permissions,
        requestedConnectorPermissionRefs=request.requested_connector_permission_refs,
        revocationSnapshotDigest=revocations.snapshot_digest,
        reasonCodes=reason_codes,
        requestDigest=request.request_digest,
        metadata=request.metadata,
    )


__all__ = [
    "MarketplaceAuthorityFlags",
    "MarketplaceOperation",
    "MarketplacePromotionReceipt",
    "MarketplacePromotionRequest",
    "MarketplacePromotionStatus",
    "MarketplaceRevocationSnapshot",
    "evaluate_marketplace_promotion_request",
    "plugin_manifest_content_digest",
    "validate_plugin_runtime_permission_request",
]
