from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import re
from typing import Literal, Self

from pydantic import Field, field_serializer, field_validator, model_validator

from magi_agent.connectors.registry import ConnectorManifest
from magi_agent.ops.authority import FalseOnlyAuthorityModel
from magi_agent.ops.safety import (
    canonical_digest,
    require_digest,
    require_safe_ref,
    safe_metadata,
)
from magi_agent.storage.durable_store import DurableRecord


CredentialLeaseStatus = Literal["issued", "fail_closed"]
CredentialRedactionStatus = Literal["metadata_only"]

_SAFE_LEASE_PART_RE = re.compile(r"[^a-z0-9]+")
_NONCE_RE = re.compile(r"^nonce:[0-9a-f]{32,128}$")


def _digest_payload(payload: Mapping[str, object]) -> str:
    return canonical_digest(payload)


def _lease_part(value: str) -> str:
    cleaned = _SAFE_LEASE_PART_RE.sub("-", value.lower()).strip("-")
    return cleaned or "ref"


def _utc_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _utc_iso(value: datetime) -> str:
    return _utc_datetime(value, field_name="timestamp").isoformat().replace("+00:00", "Z")


class CredentialLeaseReplayLedger:
    """In-process replay reservation ledger for local/fake-provider tests.

    Production canary activation must replace this with a shared store. The
    contract reserves by nonce/scope digest and TTL so local callers cannot
    accidentally issue repeat leases by changing mutable request metadata.
    """

    def __init__(self) -> None:
        self._reservations: dict[str, datetime] = {}

    def reserve(self, nonce_scope_digest: str, *, now: datetime, expires_at: datetime) -> bool:
        safe_digest = require_digest(nonce_scope_digest)
        safe_now = _utc_datetime(now, field_name="now")
        safe_expires_at = _utc_datetime(expires_at, field_name="expiresAt")
        self._reservations = {
            digest: expiry
            for digest, expiry in self._reservations.items()
            if expiry > safe_now
        }
        if safe_digest in self._reservations:
            return False
        self._reservations[safe_digest] = safe_expires_at
        return True


_DEFAULT_REPLAY_LEDGER = CredentialLeaseReplayLedger()


class _LeaseModel(FalseOnlyAuthorityModel):
    """Frozen credential-lease base on the canonical ``FalseOnlyAuthorityModel``
    (C-4 PR-C), with the pre-PR-C escape-hatch raising on ``model_construct`` /
    ``model_copy(update=...)`` preserved.

    Like ``_ConnectorModel`` (registry side), credential-lease models fail-loudly
    on illegal escape-hatch construction or in-place mutation: this is asserted
    by the ``CredentialLeaseAuthorityFlags`` golden
    (``model_construct_dump: null``) and the
    ``ConnectorCredentialLeaseReceipt`` golden (both dumps null because
    ``model_construct`` raises and ``model_validate`` rejects missing required
    fields), as well as
    ``tests/test_connector_credential_contracts.py`` which asserts
    ``CredentialLeaseAuthorityFlags.model_copy(update=...)`` raises
    ``ValueError``. Per the C-4 PR-B precedent on per-class raising semantics
    (``_UNSAFE_CONSTRUCT_COPY_FIELDS``), the raising shape is preserved at this
    base via the two surface overrides rather than dropped.
    """

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError(f"model_construct is disabled for {cls.__name__}")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError(f"model_copy update is disabled for {type(self).__name__}")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


class CredentialLeaseAuthorityFlags(_LeaseModel):
    credential_read_enabled: Literal[False] = Field(
        default=False,
        alias="credentialReadEnabled",
    )
    live_secret_read: Literal[False] = Field(default=False, alias="liveSecretRead")
    plugin_execution_enabled: Literal[False] = Field(
        default=False,
        alias="pluginExecutionEnabled",
    )
    network_call_allowed: Literal[False] = Field(default=False, alias="networkCallAllowed")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")


class ConnectorCredentialLeaseRequest(_LeaseModel):
    schema_version: Literal["openmagi.connector.credential_lease_request.v1"] = Field(
        default="openmagi.connector.credential_lease_request.v1",
        alias="schemaVersion",
    )
    request_id: str = Field(alias="requestId")
    tenant_id: str = Field(alias="tenantId")
    owner_user_id: str = Field(alias="ownerUserId")
    bot_id: str = Field(alias="botId")
    connector_id: str = Field(alias="connectorId")
    tool_id: str = Field(alias="toolId")
    audience: str
    ttl_seconds: int = Field(alias="ttlSeconds", ge=1, le=600)
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    connector_manifest_digest: str = Field(alias="connectorManifestDigest")
    requested_permission_refs: tuple[str, ...] = Field(alias="requestedPermissionRefs")
    nonce: str
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator(
        "request_id",
        "tenant_id",
        "owner_user_id",
        "bot_id",
        "connector_id",
        "tool_id",
        "audience",
    )
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("nonce")
    @classmethod
    def _validate_nonce(cls, value: str) -> str:
        safe_value = require_safe_ref(value, field_name="nonce")
        if _NONCE_RE.fullmatch(safe_value) is None:
            raise ValueError("credential lease nonce must contain at least 128 bits of hex entropy")
        return safe_value

    @field_validator("policy_snapshot_digest", "connector_manifest_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("requested_permission_refs")
    @classmethod
    def _validate_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("credential lease request requires permission refs")
        safe_refs = tuple(
            require_safe_ref(item, field_name="requestedPermissionRefs") for item in value
        )
        if len(set(safe_refs)) != len(safe_refs):
            raise ValueError("credential lease request permission refs must be unique")
        return safe_refs

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return safe_metadata(value)

    @property
    def request_digest(self) -> str:
        return _digest_payload(
            {
                "schemaVersion": self.schema_version,
                "requestId": self.request_id,
                "tenantId": self.tenant_id,
                "ownerUserId": self.owner_user_id,
                "botId": self.bot_id,
                "connectorId": self.connector_id,
                "toolId": self.tool_id,
                "audience": self.audience,
                "ttlSeconds": self.ttl_seconds,
                "policySnapshotDigest": self.policy_snapshot_digest,
                "connectorManifestDigest": self.connector_manifest_digest,
                "requestedPermissionRefs": list(self.requested_permission_refs),
                "nonce": self.nonce,
                "metadata": dict(sorted(self.metadata.items())),
            }
        )

    @property
    def nonce_scope_digest(self) -> str:
        return _digest_payload(
            {
                "schemaVersion": "openmagi.connector.credential_lease_nonce_scope.v1",
                "tenantId": self.tenant_id,
                "ownerUserId": self.owner_user_id,
                "botId": self.bot_id,
                "connectorId": self.connector_id,
                "toolId": self.tool_id,
                "audience": self.audience,
                "policySnapshotDigest": self.policy_snapshot_digest,
                "connectorManifestDigest": self.connector_manifest_digest,
                "requestedPermissionRefs": sorted(self.requested_permission_refs),
                "nonce": self.nonce,
            }
        )


class ConnectorCredentialLeaseReceipt(_LeaseModel):
    schema_version: Literal["openmagi.connector.credential_lease_receipt.v1"] = Field(
        default="openmagi.connector.credential_lease_receipt.v1",
        alias="schemaVersion",
    )
    request_id: str = Field(alias="requestId")
    tenant_id: str = Field(alias="tenantId")
    owner_user_id: str = Field(alias="ownerUserId")
    bot_id: str = Field(alias="botId")
    connector_id: str = Field(alias="connectorId")
    tool_id: str = Field(alias="toolId")
    audience: str
    status: CredentialLeaseStatus
    ttl_seconds: int = Field(alias="ttlSeconds", ge=1, le=600)
    issued_at: datetime = Field(alias="issuedAt")
    expires_at: datetime = Field(alias="expiresAt")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    connector_manifest_digest: str = Field(alias="connectorManifestDigest")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    lease_ref: str | None = Field(default=None, alias="leaseRef")
    request_digest: str | None = Field(default=None, alias="requestDigest")
    redaction_status: CredentialRedactionStatus = Field(
        default="metadata_only",
        alias="redactionStatus",
    )
    secret_material_present: Literal[False] = Field(
        default=False,
        alias="secretMaterialPresent",
    )
    live_secret_read: Literal[False] = Field(default=False, alias="liveSecretRead")
    raw_secret_material: Literal[None] = Field(default=None, alias="rawSecretMaterial")
    authority_flags: CredentialLeaseAuthorityFlags = Field(
        default_factory=CredentialLeaseAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_no_secret_material(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["secretMaterialPresent"] = False
        payload.pop("secret_material_present", None)
        payload["liveSecretRead"] = False
        payload.pop("live_secret_read", None)
        if payload.get("rawSecretMaterial") is not None or payload.get("raw_secret_material") is not None:
            raise ValueError("credential lease receipts must not carry raw secret material")
        payload["rawSecretMaterial"] = None
        payload.pop("raw_secret_material", None)
        payload["redactionStatus"] = "metadata_only"
        payload.pop("redaction_status", None)
        return payload

    @field_validator(
        "request_id",
        "tenant_id",
        "owner_user_id",
        "bot_id",
        "connector_id",
        "tool_id",
        "audience",
    )
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("policy_snapshot_digest", "connector_manifest_digest", "request_digest")
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("lease_ref")
    @classmethod
    def _validate_lease_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_safe_ref(value, field_name="leaseRef")

    @field_validator("reason_codes")
    @classmethod
    def _validate_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("credential lease receipt requires reason codes")
        return tuple(require_safe_ref(item, field_name="reasonCodes") for item in value)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _validate_timestamp(cls, value: datetime, info: object) -> datetime:
        return _utc_datetime(value, field_name=getattr(info, "field_name", "timestamp"))

    @model_validator(mode="after")
    def _validate_receipt(self) -> Self:
        if self.status == "issued" and self.lease_ref is None:
            raise ValueError("issued credential lease requires lease ref")
        if self.status == "fail_closed" and self.lease_ref is not None:
            raise ValueError("failed credential lease must not include lease ref")
        if self.expires_at <= self.issued_at:
            raise ValueError("credential lease expiry must be after issue time")
        if self.expires_at - self.issued_at != timedelta(seconds=self.ttl_seconds):
            raise ValueError("credential lease expiry must exactly match ttl")
        return self

    # ``secret_material_present`` / ``live_secret_read`` (both Literal[False])
    # are force-falsed by ``FalseOnlyAuthorityModel``'s annotation-based
    # serializer; no per-field serializer needed here.
    @field_serializer("raw_secret_material")
    def _serialize_no_secret(self, _value: object) -> None:
        # Literal[None] is NOT handled by ``FalseOnlyAuthorityModel`` (that base
        # is force-FALSE on Literal[False], not Literal[None]). Preserve the
        # no-secret invariant on the serializer surface explicitly here. The
        # ``_force_no_secret_material`` validator above also raises on any
        # non-None inbound ``rawSecretMaterial``.
        return None

    @property
    def lease_digest(self) -> str:
        return _digest_payload(
            {
                "schemaVersion": self.schema_version,
                "requestId": self.request_id,
                "tenantId": self.tenant_id,
                "ownerUserId": self.owner_user_id,
                "botId": self.bot_id,
                "connectorId": self.connector_id,
                "toolId": self.tool_id,
                "audience": self.audience,
                "status": self.status,
                "ttlSeconds": self.ttl_seconds,
                "issuedAt": _utc_iso(self.issued_at),
                "expiresAt": _utc_iso(self.expires_at),
                "policySnapshotDigest": self.policy_snapshot_digest,
                "connectorManifestDigest": self.connector_manifest_digest,
                "reasonCodes": list(self.reason_codes),
                "leaseRef": self.lease_ref,
                "requestDigest": self.request_digest,
                "redactionStatus": self.redaction_status,
            }
        )

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.connector.credential_lease.public.v1",
            "requestId": self.request_id,
            "tenantId": self.tenant_id,
            "ownerUserId": self.owner_user_id,
            "botId": self.bot_id,
            "connectorId": self.connector_id,
            "toolId": self.tool_id,
            "audience": self.audience,
            "status": self.status,
            "ttlSeconds": self.ttl_seconds,
            "issuedAt": _utc_iso(self.issued_at),
            "expiresAt": _utc_iso(self.expires_at),
            "policySnapshotDigest": self.policy_snapshot_digest,
            "connectorManifestDigest": self.connector_manifest_digest,
            "reasonCodes": list(self.reason_codes),
            "leaseRef": self.lease_ref,
            "requestDigest": self.request_digest,
            "leaseDigest": self.lease_digest,
            "redactionStatus": "metadata_only",
            "secretMaterialPresent": False,
            "liveSecretRead": False,
            "authorityFlags": self.authority_flags.public_projection(),
        }

    def to_durable_metadata_record(self, *, record_id: str) -> DurableRecord:
        _ = record_id
        return DurableRecord(
            collection="credential_lease_metadata",
            recordId="lease-ref:" + self.lease_digest,
            contentDigest=self.lease_digest,
            policySnapshotDigest=self.policy_snapshot_digest,
            metadata={
                "requestDigest": self.request_digest,
                "connectorManifestDigest": self.connector_manifest_digest,
            },
        )


def issue_credential_lease(
    request: ConnectorCredentialLeaseRequest,
    *,
    manifest: ConnectorManifest,
    local_fake_enabled: bool = False,
    now: datetime | None = None,
    replay_ledger: CredentialLeaseReplayLedger | None = None,
) -> ConnectorCredentialLeaseReceipt:
    if type(local_fake_enabled) is not bool:
        raise ValueError("local_fake_enabled must be an explicit bool")
    safe_request = ConnectorCredentialLeaseRequest.model_validate(
        request.model_dump(by_alias=True, mode="json")
    )
    safe_manifest = ConnectorManifest.model_validate(manifest.model_dump(by_alias=True, mode="json"))
    issued_at = _utc_datetime(now or datetime.now(UTC), field_name="now")
    expires_at = issued_at + timedelta(seconds=safe_request.ttl_seconds)

    if not local_fake_enabled:
        return _receipt(
            safe_request,
            status="fail_closed",
            reason_codes=("lease_disabled",),
            issued_at=issued_at,
            expires_at=expires_at,
        )
    reason = _scope_mismatch_reason(safe_request, safe_manifest)
    if reason is not None:
        return _receipt(
            safe_request,
            status="fail_closed",
            reason_codes=(reason,),
            issued_at=issued_at,
            expires_at=expires_at,
        )
    ledger = replay_ledger or _DEFAULT_REPLAY_LEDGER
    if not ledger.reserve(
        safe_request.nonce_scope_digest,
        now=issued_at,
        expires_at=expires_at,
    ):
        return _receipt(
            safe_request,
            status="fail_closed",
            reason_codes=("lease_replay_detected",),
            issued_at=issued_at,
            expires_at=expires_at,
        )
    request_fragment = safe_request.request_digest.removeprefix("sha256:")[:16]
    return _receipt(
        safe_request,
        status="issued",
        reason_codes=("local_fake_lease_issued",),
        issued_at=issued_at,
        expires_at=expires_at,
        lease_ref=(
            "lease:"
            + _lease_part(safe_request.connector_id)
            + "-"
            + _lease_part(safe_request.tool_id)
            + "-"
            + request_fragment
        ),
    )


def _scope_mismatch_reason(
    request: ConnectorCredentialLeaseRequest,
    manifest: ConnectorManifest,
) -> str | None:
    if request.ttl_seconds > 600:
        return "ttl_exceeds_limit"
    if manifest.sandbox_mode != "local_fake":
        return "local_fake_sandbox_required"
    if request.connector_id != manifest.connector_id:
        return "connector_mismatch"
    if request.connector_manifest_digest != manifest.manifest_digest:
        return "connector_manifest_digest_mismatch"
    if request.policy_snapshot_digest != manifest.policy_snapshot_digest:
        return "policy_snapshot_digest_mismatch"
    tool = manifest.tool_by_id(request.tool_id)
    if tool is None:
        return "tool_not_declared"
    if request.audience != tool.audience:
        return "audience_mismatch"
    allowed_permissions = set(tool.permission_refs)
    if not set(request.requested_permission_refs).issubset(allowed_permissions):
        return "permission_subset_mismatch"
    return None


def _receipt(
    request: ConnectorCredentialLeaseRequest,
    *,
    status: CredentialLeaseStatus,
    reason_codes: tuple[str, ...],
    issued_at: datetime,
    expires_at: datetime,
    lease_ref: str | None = None,
) -> ConnectorCredentialLeaseReceipt:
    return ConnectorCredentialLeaseReceipt(
        requestId=request.request_id,
        tenantId=request.tenant_id,
        ownerUserId=request.owner_user_id,
        botId=request.bot_id,
        connectorId=request.connector_id,
        toolId=request.tool_id,
        audience=request.audience,
        status=status,
        ttlSeconds=request.ttl_seconds,
        issuedAt=issued_at,
        expiresAt=expires_at,
        policySnapshotDigest=request.policy_snapshot_digest,
        connectorManifestDigest=request.connector_manifest_digest,
        reasonCodes=reason_codes,
        leaseRef=lease_ref,
        requestDigest=request.request_digest,
    )
