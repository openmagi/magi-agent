from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.ops.safety import (
    require_digest,
    require_safe_ref,
    reject_private_text,
    safe_metadata,
    serialize_safe_value,
)


ConnectorPermissionKind = Literal["read", "write", "execute", "network", "metadata"]
ConnectorSandboxMode = Literal["metadata_only", "local_fake", "hosted_disabled"]
ConnectorRegistryStatus = Literal["registered", "missing", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


def _digest_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _mapping_value(value: object, alias: str, field_name: str, default: object | None = None) -> object:
    if isinstance(value, Mapping):
        return value.get(alias, value.get(field_name, default))
    return getattr(value, field_name, default)


def _normalize_permission_payload(value: object) -> dict[str, object]:
    permission_id = require_safe_ref(
        str(_mapping_value(value, "permissionId", "permission_id")),
        field_name="permissionId",
    )
    scope = require_safe_ref(str(_mapping_value(value, "scope", "scope")), field_name="scope")
    return {
        "permissionId": permission_id,
        "kind": str(_mapping_value(value, "kind", "kind")),
        "scope": scope,
    }


def _normalize_tool_payload(value: object) -> dict[str, object]:
    tool_id = require_safe_ref(
        str(_mapping_value(value, "toolId", "tool_id")),
        field_name="toolId",
    )
    audience = require_safe_ref(
        str(_mapping_value(value, "audience", "audience")),
        field_name="audience",
    )
    raw_refs = _mapping_value(value, "permissionRefs", "permission_refs", ())
    if not isinstance(raw_refs, (list, tuple)):
        raise ValueError("connector tool permission refs must be a sequence")
    return {
        "toolId": tool_id,
        "permissionRefs": [
            require_safe_ref(str(item), field_name="permissionRefs") for item in raw_refs
        ],
        "audience": audience,
    }


def connector_manifest_content_digest(value: Mapping[str, object]) -> str:
    """Return the content-bound digest for a connector manifest.

    The digest intentionally excludes ``manifestDigest`` itself and includes all
    authority-relevant connector metadata, permissions, tools, sandbox posture,
    and policy snapshot references.
    """

    permissions = [
        _normalize_permission_payload(item)
        for item in _mapping_value(value, "permissions", "permissions", ())
    ]
    tools = [
        _normalize_tool_payload(item) for item in _mapping_value(value, "tools", "tools", ())
    ]
    return _digest_payload(
        {
            "schemaVersion": _mapping_value(
                value,
                "schemaVersion",
                "schema_version",
                "openmagi.connector.manifest.v1",
            ),
            "connectorId": require_safe_ref(
                str(_mapping_value(value, "connectorId", "connector_id")),
                field_name="connectorId",
            ),
            "displayName": str(_mapping_value(value, "displayName", "display_name")).strip(),
            "version": str(_mapping_value(value, "version", "version")).strip(),
            "publisherRef": require_safe_ref(
                str(_mapping_value(value, "publisherRef", "publisher_ref")),
                field_name="publisherRef",
            ),
            "supplyChainDigest": require_digest(
                str(_mapping_value(value, "supplyChainDigest", "supply_chain_digest"))
            ),
            "sandboxMode": _mapping_value(
                value,
                "sandboxMode",
                "sandbox_mode",
                "metadata_only",
            ),
            "permissions": sorted(permissions, key=lambda item: str(item["permissionId"])),
            "tools": sorted(tools, key=lambda item: str(item["toolId"])),
            "policySnapshotDigest": require_digest(
                str(_mapping_value(value, "policySnapshotDigest", "policy_snapshot_digest"))
            ),
            "metadata": safe_metadata(_mapping_value(value, "metadata", "metadata", {}) or {}),
        }
    )


class _ConnectorModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError(f"model_construct is disabled for {cls.__name__}")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError(f"model_copy update is disabled for {type(self).__name__}")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


class ConnectorAuthorityFlags(_ConnectorModel):
    registry_live_sync_enabled: Literal[False] = Field(
        default=False,
        alias="registryLiveSyncEnabled",
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
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field_name, field in cls.model_fields.items():
            payload[field.alias or field_name] = False
            payload.pop(field_name, None)
        return payload

    @field_serializer(
        "registry_live_sync_enabled",
        "plugin_execution_enabled",
        "credential_read_enabled",
        "live_secret_read",
        "network_call_allowed",
        "production_authority",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    def public_projection(self) -> dict[str, bool]:
        return {
            "registryLiveSyncEnabled": False,
            "pluginExecutionEnabled": False,
            "credentialReadEnabled": False,
            "liveSecretRead": False,
            "networkCallAllowed": False,
            "productionAuthority": False,
        }


class ConnectorPermission(_ConnectorModel):
    permission_id: str = Field(alias="permissionId")
    kind: ConnectorPermissionKind
    scope: str

    @field_validator("permission_id", "scope")
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    def public_projection(self) -> dict[str, object]:
        return {
            "permissionId": self.permission_id,
            "kind": self.kind,
            "scope": self.scope,
        }


class ConnectorToolRef(_ConnectorModel):
    tool_id: str = Field(alias="toolId")
    permission_refs: tuple[str, ...] = Field(alias="permissionRefs")
    audience: str

    @field_validator("tool_id", "audience")
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("permission_refs")
    @classmethod
    def _validate_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("connector tool requires at least one permission ref")
        safe_refs = tuple(require_safe_ref(item, field_name="permissionRefs") for item in value)
        if len(set(safe_refs)) != len(safe_refs):
            raise ValueError("connector tool permission refs must be unique")
        return safe_refs

    def public_projection(self) -> dict[str, object]:
        return {
            "toolId": self.tool_id,
            "permissionRefs": list(self.permission_refs),
            "audience": self.audience,
        }


class ConnectorManifest(_ConnectorModel):
    schema_version: Literal["openmagi.connector.manifest.v1"] = Field(
        default="openmagi.connector.manifest.v1",
        alias="schemaVersion",
    )
    connector_id: str = Field(alias="connectorId")
    display_name: str = Field(alias="displayName")
    version: str
    publisher_ref: str = Field(alias="publisherRef")
    supply_chain_digest: str = Field(alias="supplyChainDigest")
    manifest_digest: str = Field(alias="manifestDigest")
    sandbox_mode: ConnectorSandboxMode = Field(default="metadata_only", alias="sandboxMode")
    permissions: tuple[ConnectorPermission, ...]
    tools: tuple[ConnectorToolRef, ...]
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)
    authority_flags: ConnectorAuthorityFlags = Field(
        default_factory=ConnectorAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("connector_id", "publisher_ref")
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("display_name", "version")
    @classmethod
    def _validate_label(cls, value: str, info: object) -> str:
        stripped = value.strip()
        if not stripped or len(stripped) > 120:
            raise ValueError(f"{getattr(info, 'field_name', 'label')} must be a safe label")
        reject_private_text(stripped, field_name=getattr(info, "field_name", "label"))
        return stripped

    @field_validator("supply_chain_digest", "manifest_digest", "policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return safe_metadata(value)

    @model_validator(mode="after")
    def _validate_manifest_contract(self) -> Self:
        permission_ids = {permission.permission_id for permission in self.permissions}
        if not permission_ids:
            raise ValueError("connector manifest requires permissions")
        if len(permission_ids) != len(self.permissions):
            raise ValueError("connector permission ids must be unique")
        tool_ids: set[str] = set()
        for tool in self.tools:
            if tool.tool_id in tool_ids:
                raise ValueError("connector tool ids must be unique")
            tool_ids.add(tool.tool_id)
            missing = set(tool.permission_refs) - permission_ids
            if missing:
                raise ValueError("connector tool permission refs must be declared")
        expected_digest = connector_manifest_content_digest(
            self.model_dump(by_alias=True, mode="json")
        )
        if self.manifest_digest != expected_digest:
            raise ValueError("connector manifest digest must match canonical manifest content")
        return self

    @property
    def connector_digest(self) -> str:
        return _digest_payload(
            {
                "schemaVersion": self.schema_version,
                "connectorId": self.connector_id,
                "version": self.version,
                "publisherRef": self.publisher_ref,
                "supplyChainDigest": self.supply_chain_digest,
                "manifestDigest": self.manifest_digest,
                "sandboxMode": self.sandbox_mode,
                "policySnapshotDigest": self.policy_snapshot_digest,
                "permissions": [permission.public_projection() for permission in self.permissions],
                "tools": [tool.public_projection() for tool in self.tools],
            }
        )

    def tool_by_id(self, tool_id: str) -> ConnectorToolRef | None:
        return next((tool for tool in self.tools if tool.tool_id == tool_id), None)

    def permission_ids_for_tool(self, tool_id: str) -> tuple[str, ...]:
        tool = self.tool_by_id(tool_id)
        return () if tool is None else tool.permission_refs

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.connector.manifest.public.v1",
            "connectorId": self.connector_id,
            "displayName": self.display_name,
            "version": self.version,
            "publisherRef": self.publisher_ref,
            "supplyChainDigest": self.supply_chain_digest,
            "manifestDigest": self.manifest_digest,
            "connectorDigest": self.connector_digest,
            "sandboxMode": self.sandbox_mode,
            "permissions": [permission.public_projection() for permission in self.permissions],
            "tools": [tool.public_projection() for tool in self.tools],
            "policySnapshotDigest": self.policy_snapshot_digest,
            "metadata": {
                key: serialize_safe_value(item)
                for key, item in safe_metadata(self.metadata).items()
            },
            "authorityFlags": self.authority_flags.public_projection(),
        }


class ConnectorRegistryReceipt(_ConnectorModel):
    connector_id: str = Field(alias="connectorId")
    status: ConnectorRegistryStatus
    allowed: bool
    manifest_digest: str | None = Field(default=None, alias="manifestDigest")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    authority_flags: ConnectorAuthorityFlags = Field(
        default_factory=ConnectorAuthorityFlags,
        alias="authorityFlags",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")

    @field_validator("connector_id")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return require_safe_ref(value, field_name="connectorId")

    @field_validator("manifest_digest")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("reason_codes")
    @classmethod
    def _validate_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("connector registry receipt requires reason codes")
        return tuple(require_safe_ref(item, field_name="reasonCodes") for item in value)

    @model_validator(mode="after")
    def _validate_status_authority(self) -> Self:
        if self.status == "missing" and self.manifest_digest is not None:
            raise ValueError("missing connector receipt must not include manifest digest")
        if self.status == "registered" and self.manifest_digest is None:
            raise ValueError("registered connector receipt requires manifest digest")
        if self.allowed != (self.status == "registered"):
            raise ValueError("connector registry receipt allowed flag must match status")
        return self

    @property
    def receipt_digest(self) -> str:
        return _digest_payload(
            {
                "connectorId": self.connector_id,
                "status": self.status,
                "allowed": self.allowed,
                "manifestDigest": self.manifest_digest,
                "reasonCodes": list(self.reason_codes),
            }
        )

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.connector.registry_receipt.public.v1",
            "connectorId": self.connector_id,
            "status": self.status,
            "allowed": self.allowed,
            "manifestDigest": self.manifest_digest,
            "reasonCodes": list(self.reason_codes),
            "receiptDigest": self.receipt_digest,
            "authorityFlags": self.authority_flags.public_projection(),
            "createdAt": self.created_at.isoformat(),
        }


class ConnectorRegistry:
    def __init__(self, *, enabled: bool = False, local_fake_enabled: bool = False) -> None:
        _ = enabled
        self.enabled = False
        if type(local_fake_enabled) is not bool:
            raise ValueError("local_fake_enabled must be an explicit bool")
        self.local_fake_enabled = local_fake_enabled
        self._manifests: dict[str, ConnectorManifest] = {}

    def register(self, manifest: ConnectorManifest) -> ConnectorRegistryReceipt:
        stored = ConnectorManifest.model_validate(manifest.model_dump(by_alias=True, mode="json"))
        if not self.local_fake_enabled:
            return ConnectorRegistryReceipt(
                connectorId=stored.connector_id,
                status="blocked",
                allowed=False,
                manifestDigest=stored.manifest_digest,
                reasonCodes=("registry_disabled",),
            )
        if stored.connector_id in self._manifests:
            raise ValueError("connector already registered")
        self._manifests[stored.connector_id] = stored
        return ConnectorRegistryReceipt(
            connectorId=stored.connector_id,
            status="registered",
            allowed=True,
            manifestDigest=stored.manifest_digest,
            reasonCodes=("local_fake_metadata_manifest_registered",),
        )

    def lookup(self, connector_id: str) -> ConnectorRegistryReceipt:
        safe_connector_id = require_safe_ref(connector_id, field_name="connectorId")
        manifest = self._manifests.get(safe_connector_id)
        if manifest is None:
            return ConnectorRegistryReceipt(
                connectorId=safe_connector_id,
                status="missing",
                allowed=False,
                manifestDigest=None,
                reasonCodes=("connector_not_registered",),
            )
        if not self.local_fake_enabled:
            return ConnectorRegistryReceipt(
                connectorId=manifest.connector_id,
                status="blocked",
                allowed=False,
                manifestDigest=manifest.manifest_digest,
                reasonCodes=("registry_disabled",),
            )
        return ConnectorRegistryReceipt(
            connectorId=manifest.connector_id,
            status="registered",
            allowed=True,
            manifestDigest=manifest.manifest_digest,
            reasonCodes=("local_fake_connector_registered",),
        )

    def manifest_for(self, connector_id: str) -> ConnectorManifest | None:
        manifest = self._manifests.get(require_safe_ref(connector_id, field_name="connectorId"))
        if manifest is None:
            return None
        return ConnectorManifest.model_validate(manifest.model_dump(by_alias=True, mode="json"))

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.connector.registry.public.v1",
            "enabled": False,
            "localFakeEnabled": self.local_fake_enabled,
            "registeredConnectorCount": len(self._manifests),
            "connectorIds": sorted(self._manifests),
            "authorityFlags": ConnectorAuthorityFlags().public_projection(),
        }
