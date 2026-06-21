from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from magi_agent.ops.authority import FalseOnlyAuthorityModel
from magi_agent.ops.safety import (
    FrozenContractModel,
    canonical_digest,
    require_digest,
    require_safe_ref,
    safe_metadata,
    serialize_safe_value,
)


TenantEnvironment = Literal["local", "test", "preview", "staging", "production"]


def _digest_payload(payload: Mapping[str, object]) -> str:
    return canonical_digest(payload)


class _TenancyModel(FrozenContractModel):
    """Frozen tenancy contract base (collapsed onto the shared kernel)."""


class TenantRuntimeAuthorityFlags(FalseOnlyAuthorityModel):
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    live_billing_calls_enabled: Literal[False] = Field(
        default=False,
        alias="liveBillingCallsEnabled",
    )
    stripe_attached: Literal[False] = Field(default=False, alias="stripeAttached")
    supabase_attached: Literal[False] = Field(default=False, alias="supabaseAttached")
    quota_mutation_attached: Literal[False] = Field(
        default=False,
        alias="quotaMutationAttached",
    )
    spend_commit_attached: Literal[False] = Field(default=False, alias="spendCommitAttached")
    user_visible_output_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEnabled",
    )


class AuthorityScope(_TenancyModel):
    schema_version: Literal["openmagi.tenancy.authority_scope.v1"] = Field(
        default="openmagi.tenancy.authority_scope.v1",
        alias="schemaVersion",
    )
    scope_id: str = Field(alias="scopeId")
    tenant_id: str = Field(alias="tenantId")
    owner_user_id: str = Field(alias="ownerUserId")
    bot_id: str = Field(alias="botId")
    env: TenantEnvironment
    allowed_operations: tuple[str, ...] = Field(alias="allowedOperations")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    authority_flags: TenantRuntimeAuthorityFlags = Field(
        default_factory=TenantRuntimeAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("scope_id", "tenant_id", "owner_user_id", "bot_id")
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("allowed_operations")
    @classmethod
    def _validate_operations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("authority scope requires at least one operation ref")
        return tuple(require_safe_ref(item, field_name="allowedOperations") for item in value)

    @field_validator("policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @property
    def scope_digest(self) -> str:
        return _digest_payload(
            {
                "schemaVersion": self.schema_version,
                "scopeId": self.scope_id,
                "tenantId": self.tenant_id,
                "ownerUserId": self.owner_user_id,
                "botId": self.bot_id,
                "env": self.env,
                "allowedOperations": list(self.allowed_operations),
                "policySnapshotDigest": self.policy_snapshot_digest,
            }
        )

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.tenancy.authority_scope.public.v1",
            "scopeId": self.scope_id,
            "scopeDigest": self.scope_digest,
            "tenantId": self.tenant_id,
            "ownerUserId": self.owner_user_id,
            "botId": self.bot_id,
            "env": self.env,
            "allowedOperations": list(self.allowed_operations),
            "policySnapshotDigest": self.policy_snapshot_digest,
            "authorityFlags": self.authority_flags.public_projection(),
        }


class TenantContext(_TenancyModel):
    schema_version: Literal["openmagi.tenancy.context.v1"] = Field(
        default="openmagi.tenancy.context.v1",
        alias="schemaVersion",
    )
    tenant_id: str = Field(alias="tenantId")
    owner_user_id: str = Field(alias="ownerUserId")
    bot_id: str = Field(alias="botId")
    env: TenantEnvironment
    authority_scope: AuthorityScope = Field(alias="authorityScope")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
    authority_flags: TenantRuntimeAuthorityFlags = Field(
        default_factory=TenantRuntimeAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("tenant_id", "owner_user_id", "bot_id")
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return safe_metadata(value)

    @model_validator(mode="after")
    def _validate_scope_matches_context(self) -> Self:
        if self.authority_scope.tenant_id != self.tenant_id:
            raise ValueError("authority scope tenant must match tenant context")
        if self.authority_scope.owner_user_id != self.owner_user_id:
            raise ValueError("authority scope owner must match tenant context")
        if self.authority_scope.bot_id != self.bot_id:
            raise ValueError("authority scope bot must match tenant context")
        if self.authority_scope.env != self.env:
            raise ValueError("authority scope env must match tenant context")
        if self.authority_scope.policy_snapshot_digest != self.policy_snapshot_digest:
            raise ValueError("authority scope policy snapshot must match tenant context")
        return self

    @property
    def context_digest(self) -> str:
        return _digest_payload(
            {
                "schemaVersion": self.schema_version,
                "tenantId": self.tenant_id,
                "ownerUserId": self.owner_user_id,
                "botId": self.bot_id,
                "env": self.env,
                "authorityScopeDigest": self.authority_scope.scope_digest,
                "policySnapshotDigest": self.policy_snapshot_digest,
                "metadata": dict(sorted(self.metadata.items())),
            }
        )

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.tenancy.context.public.v1",
            "tenantId": self.tenant_id,
            "ownerUserId": self.owner_user_id,
            "botId": self.bot_id,
            "env": self.env,
            "contextDigest": self.context_digest,
            "policySnapshotDigest": self.policy_snapshot_digest,
            "authorityScope": self.authority_scope.public_projection(),
            "authorityFlags": self.authority_flags.public_projection(),
            "metadata": {
                key: serialize_safe_value(item)
                for key, item in safe_metadata(self.metadata).items()
            },
            "createdAt": self.created_at.isoformat(),
        }
