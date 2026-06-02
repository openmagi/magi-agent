from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .manifest import PluginHookRef, PluginKind, PluginManifest, PluginSecretRef, PluginToolRef


OptOutScope = Literal["bot", "org"]


class PluginOptOutRecord(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    plugin_id: str = Field(alias="pluginId")
    scope: OptOutScope
    actor: str
    reason: str | None = None
    ts: str
    effective_runtime_version: str | None = Field(default=None, alias="effectiveRuntimeVersion")
    affected_tools: tuple[str, ...] = Field(default=(), alias="affectedTools")
    affected_hooks: tuple[str, ...] = Field(default=(), alias="affectedHooks")
    affected_harness_rules: tuple[str, ...] = Field(default=(), alias="affectedHarnessRules")

    @field_validator("actor", "ts")
    @classmethod
    def _reject_blank_required_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("actor and ts must be non-empty")
        return value


class PluginStatus(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    plugin_id: str = Field(alias="pluginId")
    kind: PluginKind
    version: str
    installed: bool
    enabled: bool
    opted_out: bool = Field(alias="optedOut")
    default_installed: bool = Field(alias="defaultInstalled")
    default_enabled: bool = Field(alias="defaultEnabled")
    opt_out_allowed: bool = Field(alias="optOutAllowed")
    security_critical: bool = Field(alias="securityCritical")
    audit_required: bool = Field(alias="auditRequired")
    status_reason: str = Field(alias="statusReason")
    tools: tuple[PluginToolRef, ...] = ()
    hooks: tuple[PluginHookRef, ...] = ()
    harness_rules: tuple[str, ...] = Field(default=(), alias="harnessRules")
    secrets: tuple[PluginSecretRef, ...] = ()
    permissions: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")


class ResolvedPluginState(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    plugins: tuple[PluginStatus, ...]
    active_tools: tuple[str, ...] = Field(alias="activeTools")
    active_hooks: tuple[str, ...] = Field(alias="activeHooks")
    active_harness_rules: tuple[str, ...] = Field(alias="activeHarnessRules")
    opt_outs: tuple[PluginOptOutRecord, ...] = Field(default=(), alias="optOuts")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")


def resolve_plugin_state(
    manifests: tuple[PluginManifest, ...],
    opt_outs: tuple[PluginOptOutRecord, ...] = (),
    *,
    runtime_version: str = "0.1.0-adk-scaffold",
) -> ResolvedPluginState:
    manifest_by_id: dict[str, PluginManifest] = {}
    for manifest in manifests:
        if manifest.plugin_id in manifest_by_id:
            raise ValueError(f"duplicate plugin id: {manifest.plugin_id}")
        manifest_by_id[manifest.plugin_id] = manifest

    opt_out_by_id: dict[str, PluginOptOutRecord] = {}
    for opt_out in opt_outs:
        if opt_out.plugin_id in opt_out_by_id:
            raise ValueError(f"duplicate opt-out plugin id: {opt_out.plugin_id}")
        manifest = manifest_by_id.get(opt_out.plugin_id)
        if manifest is None:
            raise ValueError(f"unknown plugin opt-out: {opt_out.plugin_id}")
        if manifest.security_critical or not manifest.opt_out_allowed:
            raise ValueError(f"plugin cannot be opted out: {opt_out.plugin_id}")
        opt_out_by_id[opt_out.plugin_id] = _derive_opt_out_record(
            opt_out,
            manifest,
            runtime_version=runtime_version,
        )

    statuses: list[PluginStatus] = []
    active_tools: set[str] = set()
    active_hooks: set[str] = set()
    active_harness_rules: set[str] = set()

    for manifest in sorted(manifests, key=lambda item: item.plugin_id):
        installed = manifest.default_installed
        opted_out = manifest.plugin_id in opt_out_by_id
        enabled = installed and manifest.default_enabled and not opted_out
        if opted_out:
            status_reason = "opted_out"
        elif not installed:
            status_reason = "not_default_installed"
        elif not manifest.default_enabled:
            status_reason = "default_disabled"
        else:
            status_reason = "enabled"

        if enabled:
            active_tools.update(tool.name for tool in manifest.tools)
            active_hooks.update(hook.name for hook in manifest.hooks)
            active_harness_rules.update(manifest.harness_rules)

        statuses.append(
            PluginStatus(
                pluginId=manifest.plugin_id,
                kind=manifest.kind,
                version=manifest.version,
                installed=installed,
                enabled=enabled,
                optedOut=opted_out,
                defaultInstalled=manifest.default_installed,
                defaultEnabled=manifest.default_enabled,
                optOutAllowed=manifest.opt_out_allowed,
                securityCritical=manifest.security_critical,
                auditRequired=manifest.audit_required,
                statusReason=status_reason,
                tools=manifest.tools,
                hooks=manifest.hooks,
                harnessRules=manifest.harness_rules,
                secrets=manifest.secrets,
                permissions=manifest.permissions,
                services=manifest.services,
            )
        )

    return ResolvedPluginState(
        plugins=tuple(statuses),
        activeTools=tuple(sorted(active_tools)),
        activeHooks=tuple(sorted(active_hooks)),
        activeHarnessRules=tuple(sorted(active_harness_rules)),
        optOuts=tuple(opt_out_by_id[plugin_id] for plugin_id in sorted(opt_out_by_id)),
    )


def _derive_opt_out_record(
    opt_out: PluginOptOutRecord,
    manifest: PluginManifest,
    *,
    runtime_version: str,
) -> PluginOptOutRecord:
    return opt_out.model_copy(
        update={
            "effective_runtime_version": opt_out.effective_runtime_version or runtime_version,
            "affected_tools": opt_out.affected_tools or _affected_tool_names(manifest),
            "affected_hooks": opt_out.affected_hooks or tuple(hook.name for hook in manifest.hooks),
            "affected_harness_rules": opt_out.affected_harness_rules or manifest.harness_rules,
        }
    )


def _affected_tool_names(manifest: PluginManifest) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for name in (
        *(tool.name for tool in manifest.tools),
        *(capability.name for capability in manifest.capabilities if capability.type == "tool"),
    ):
        if name not in seen:
            names.append(name)
            seen.add(name)
    return tuple(names)


__all__ = [
    "PluginOptOutRecord",
    "PluginStatus",
    "ResolvedPluginState",
    "resolve_plugin_state",
]
