from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .manager import PluginStatus, ResolvedPluginState
from .manifest import PermissionClass, SecretSource


class PluginAuditSecret(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    name: str
    source: SecretSource


class PluginAuditEntry(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    plugin_id: str = Field(alias="pluginId")
    enabled: bool
    status_reason: str = Field(alias="statusReason")
    audit_required: bool = Field(alias="auditRequired")
    security_critical: bool = Field(alias="securityCritical")
    opt_out_allowed: bool = Field(alias="optOutAllowed")
    opted_out: bool = Field(alias="optedOut")
    permissions: tuple[PermissionClass, ...] = ()
    services: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    hooks: tuple[str, ...] = ()
    harness_rules: tuple[str, ...] = Field(default=(), alias="harnessRules")
    declared_secrets: tuple[PluginAuditSecret, ...] = Field(default=(), alias="declaredSecrets")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")


class PluginAuditSummary(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    plugin_count: int = Field(alias="pluginCount")
    enabled_plugin_count: int = Field(alias="enabledPluginCount")
    opted_out_plugin_count: int = Field(alias="optedOutPluginCount")
    permission_classes: tuple[PermissionClass, ...] = Field(default=(), alias="permissionClasses")
    declared_secret_names: tuple[str, ...] = Field(default=(), alias="declaredSecretNames")
    declared_secret_sources: tuple[SecretSource, ...] = Field(default=(), alias="declaredSecretSources")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")


class PluginAuditSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    entries: tuple[PluginAuditEntry, ...]
    summary: PluginAuditSummary
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")


def build_plugin_audit_snapshot(state: ResolvedPluginState) -> PluginAuditSnapshot:
    entries = tuple(_entry_from_status(status) for status in state.plugins)

    return PluginAuditSnapshot(
        entries=entries,
        summary=_build_summary(entries),
        trafficAttached=False,
        executionAttached=False,
    )


def _entry_from_status(status: PluginStatus) -> PluginAuditEntry:
    return PluginAuditEntry(
        pluginId=status.plugin_id,
        enabled=status.enabled,
        statusReason=status.status_reason,
        auditRequired=status.audit_required,
        securityCritical=status.security_critical,
        optOutAllowed=status.opt_out_allowed,
        optedOut=status.opted_out,
        permissions=status.permissions,
        services=status.services,
        tools=tuple(tool.name for tool in status.tools),
        hooks=tuple(hook.name for hook in status.hooks),
        harnessRules=status.harness_rules,
        declaredSecrets=tuple(
            PluginAuditSecret(name=secret.name, source=secret.source) for secret in status.secrets
        ),
        trafficAttached=False,
        executionAttached=False,
    )


def _build_summary(entries: tuple[PluginAuditEntry, ...]) -> PluginAuditSummary:
    return PluginAuditSummary(
        pluginCount=len(entries),
        enabledPluginCount=sum(1 for entry in entries if entry.enabled),
        optedOutPluginCount=sum(1 for entry in entries if entry.opted_out),
        permissionClasses=tuple(
            sorted({permission for entry in entries for permission in entry.permissions})
        ),
        declaredSecretNames=tuple(
            sorted({secret.name for entry in entries for secret in entry.declared_secrets})
        ),
        declaredSecretSources=tuple(
            sorted({secret.source for entry in entries for secret in entry.declared_secrets})
        ),
        trafficAttached=False,
        executionAttached=False,
    )


__all__ = [
    "PluginAuditEntry",
    "PluginAuditSecret",
    "PluginAuditSnapshot",
    "PluginAuditSummary",
    "build_plugin_audit_snapshot",
]
