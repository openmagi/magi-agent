from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from magi_agent.plugins.manifest import PluginManifest


PluginTrustLevel = Literal["first_party", "verified_third_party", "local_dev", "untrusted"]
SandboxMode = Literal["in_process_contract_only", "isolated_process", "external_sandbox"]
FilesystemAccess = Literal["none", "scoped_readonly", "scoped_readwrite"]
NetworkAccess = Literal["none", "allowlisted"]
ProtectedBindingAccess = Literal["none", "scoped"]
ProcessAccess = Literal["none", "isolated"]
_PROTECTED_BINDING_ACCESS_ALIAS = "protectedBindingAccess"


class PluginSandboxPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    mode: SandboxMode
    filesystem: FilesystemAccess = "none"
    network: NetworkAccess = "none"
    protected_binding_access: ProtectedBindingAccess = Field(
        default="none",
        alias=_PROTECTED_BINDING_ACCESS_ALIAS,
    )
    process: ProcessAccess = "none"
    workspace_mutation: bool = Field(default=False, alias="workspaceMutation")
    channel_delivery: bool = Field(default=False, alias="channelDelivery")


class PluginSandboxDecision(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    ok: bool
    trust_level: PluginTrustLevel = Field(alias="trustLevel")
    sandbox: PluginSandboxPolicy
    effective_permissions: tuple[str, ...] = Field(default=(), alias="effectivePermissions")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


_DEFAULT_DENY_SANDBOX = PluginSandboxPolicy(
    mode="in_process_contract_only",
    filesystem="none",
    network="none",
    protected_binding_access="none",
    process="none",
    workspaceMutation=False,
    channelDelivery=False,
)


def evaluate_plugin_sandbox(manifest: "PluginManifest") -> PluginSandboxDecision:
    sandbox = manifest.sandbox or _DEFAULT_DENY_SANDBOX
    reason_codes: list[str] = []
    permissions = tuple(manifest.permissions)

    if manifest.sandbox is None:
        reason_codes.append("sandbox_policy_required")

    if manifest.trust_level in {"verified_third_party", "local_dev", "untrusted"}:
        if manifest.supply_chain_digest is None:
            reason_codes.append("supply_chain_digest_required")

    if manifest.trust_level == "untrusted" and manifest.default_enabled:
        reason_codes.append("untrusted_plugin_cannot_be_default_enabled")

    if "net" in permissions and sandbox.network == "none":
        reason_codes.append("network_permission_not_allowed_by_sandbox")
    if "write" in permissions and sandbox.filesystem != "scoped_readwrite":
        reason_codes.append("write_permission_not_allowed_by_sandbox")
    if "execute" in permissions and sandbox.process == "none":
        reason_codes.append("execute_permission_not_allowed_by_sandbox")
    if manifest.secrets and sandbox.protected_binding_access == "none":
        reason_codes.append("protected_binding_access_not_allowed_by_sandbox")

    if any(permission in permissions for permission in ("write", "execute")):
        reason_codes.append("mutation_or_execute_requires_approval_receipt")
    if manifest.secrets:
        reason_codes.append("protected_binding_access_requires_approval_receipt")
    if sandbox.workspace_mutation:
        reason_codes.append("workspace_mutation_requires_approval_receipt")
    if sandbox.channel_delivery:
        reason_codes.append("channel_delivery_requires_approval_receipt")

    return PluginSandboxDecision(
        ok=not reason_codes,
        trustLevel=manifest.trust_level,
        sandbox=sandbox,
        effectivePermissions=permissions if not reason_codes else (),
        reasonCodes=tuple(dict.fromkeys(reason_codes)),
    )
