from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from openmagi_core_agent.runtime.provider_receipts import build_provider_receipt
from openmagi_core_agent.tools.manifest import Budget, ToolManifest, ToolSource
from openmagi_core_agent.tools.output_budget import BudgetedToolResult, budget_tool_result
from openmagi_core_agent.tools.result import ToolResult
from openmagi_core_agent.tools.schema_projection import (
    contains_private_schema_text,
    is_sensitive_schema_key,
    project_public_tool_schema,
    redact_public_schema_text,
)


McpListStatus = Literal["disabled", "ok", "auth_required", "blocked"]
McpCallStatus = Literal["disabled", "ok", "auth_required", "error", "blocked"]
McpTrustLevel = Literal["first_party", "verified_third_party", "local_dev"]
McpSandboxMode = Literal["in_process_contract_only", "isolated_process", "external_sandbox"]
PermissionName = Literal["read", "write", "execute", "net", "meta"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_]+")


class McpAuthError(Exception):
    """Raised by local fake providers when MCP credentials are absent or invalid."""


class McpProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def list_tools(self, server_ref: str) -> Sequence[Mapping[str, object]]: ...

    def call_tool(
        self,
        server_ref: str,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]: ...


class McpAdapterConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_provider_enabled: bool = Field(default=False, alias="localFakeProviderEnabled")
    max_tools: int = Field(default=32, alias="maxTools", ge=1, le=128)
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    live_provider_attached: Literal[False] = Field(default=False, alias="liveProviderAttached")


class McpAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    mcp_server_attached: Literal[False] = Field(default=False, alias="mcpServerAttached")
    external_tool_execution_enabled: Literal[False] = Field(
        default=False,
        alias="externalToolExecutionEnabled",
    )
    live_tool_execution_enabled: Literal[False] = Field(default=False, alias="liveToolExecutionEnabled")
    credential_used: Literal[False] = Field(default=False, alias="credentialUsed")
    network_egress_enabled: Literal[False] = Field(default=False, alias="networkEgressEnabled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    user_visible_output_allowed: Literal[False] = Field(default=False, alias="userVisibleOutputAllowed")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "mcp_server_attached",
        "external_tool_execution_enabled",
        "live_tool_execution_enabled",
        "credential_used",
        "network_egress_enabled",
        "route_attached",
        "user_visible_output_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class McpServerSecurityManifest(BaseModel):
    model_config = _MODEL_CONFIG

    server_ref: str = Field(alias="serverRef")
    trust_level: McpTrustLevel = Field(alias="trustLevel")
    sandbox_mode: McpSandboxMode = Field(alias="sandboxMode")
    allowed_permissions: tuple[PermissionName, ...] = Field(alias="allowedPermissions")
    supply_chain_digest: str | None = Field(default=None, alias="supplyChainDigest")

    @field_validator("server_ref")
    @classmethod
    def _validate_server_ref(cls, value: str) -> str:
        return _safe_public_ref(value, prefix="mcp")

    @field_validator("allowed_permissions")
    @classmethod
    def _dedupe_permissions(cls, value: tuple[PermissionName, ...]) -> tuple[PermissionName, ...]:
        return tuple(dict.fromkeys(value))

    @field_validator("supply_chain_digest")
    @classmethod
    def _validate_supply_chain_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        suffix = value.removeprefix("sha256:")
        if not value.startswith("sha256:") or len(suffix) != 64 or any(
            char not in "0123456789abcdef" for char in suffix
        ):
            raise ValueError("MCP supply chain digest must be a sha256 digest")
        return value


class McpListDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: McpListStatus
    manifests: tuple[ToolManifest, ...] = ()
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: McpAuthorityFlags = Field(default_factory=McpAuthorityFlags, alias="authorityFlags")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = McpAuthorityFlags()
        return cls(**values)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "manifestRefs": [f"tool:{_digest(manifest.name)[:16]}" for manifest in self.manifests],
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": McpAuthorityFlags().model_dump(by_alias=True),
        }


class McpCallDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: McpCallStatus
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    budgeted_result: BudgetedToolResult | None = Field(default=None, alias="budgetedResult")
    receipt_ref: str | None = Field(default=None, alias="receiptRef")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: McpAuthorityFlags = Field(default_factory=McpAuthorityFlags, alias="authorityFlags")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = McpAuthorityFlags()
        return cls(**values)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasonCodes": list(self.reason_codes),
            "result": None if self.budgeted_result is None else self.budgeted_result.public_projection(),
            "receiptRef": self.receipt_ref,
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": McpAuthorityFlags().model_dump(by_alias=True),
        }


class McpAdapter:
    """Default-off MCP adapter for fake/local descriptor projection.

    This boundary converts MCP ``tools/list`` descriptors into deferred
    ``ToolManifest`` records. It does not import MCP clients, start servers,
    open sockets, call external MCP tools, attach ToolHost handlers, or enable
    user-visible output.
    """

    def __init__(self, config: McpAdapterConfig) -> None:
        self.config = config

    def list_tools(
        self,
        server_ref: str,
        *,
        provider: McpProviderPort | None = None,
        security_manifest: McpServerSecurityManifest | Mapping[str, object] | None = None,
    ) -> McpListDecision:
        safe_server_ref = _safe_public_ref(server_ref, prefix="mcp")
        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeProviderEnabled": self.config.local_fake_provider_enabled,
            "serverRef": safe_server_ref,
        }
        if not self.config.enabled:
            return _list_decision("disabled", ("mcp_adapter_disabled",), diagnostics)
        manifest, manifest_reasons = _coerce_security_manifest(safe_server_ref, security_manifest)
        if manifest_reasons:
            return _list_decision("blocked", manifest_reasons, diagnostics)
        if not self.config.local_fake_provider_enabled or provider is None:
            return _list_decision("blocked", ("local_fake_mcp_provider_required",), diagnostics)
        if getattr(provider, "openmagi_local_fake_provider", False) is not True:
            return _list_decision("blocked", ("local_fake_mcp_provider_untrusted",), diagnostics)

        try:
            raw_tools = provider.list_tools(safe_server_ref)
        except McpAuthError as exc:
            return _list_decision(
                "auth_required",
                ("mcp_auth_required",),
                {**diagnostics, "providerErrorDigest": _digest(_safe_text(str(exc)))[:16]},
            )
        except Exception as exc:
            return _list_decision(
                "blocked",
                ("mcp_provider_list_failed",),
                {**diagnostics, "providerErrorDigest": _digest(_safe_text(str(exc)))[:16]},
            )

        manifests: list[ToolManifest] = []
        for raw_tool in raw_tools[: self.config.max_tools]:
            if not isinstance(raw_tool, Mapping):
                continue
            tool_manifest = _tool_manifest_from_mcp(safe_server_ref, raw_tool)
            if tool_manifest.permission not in manifest.allowed_permissions:
                return _list_decision(
                    "blocked",
                    ("mcp_tool_permission_not_allowed_by_manifest",),
                    {
                        **diagnostics,
                        "toolRef": f"tool:{_digest(tool_manifest.name)[:16]}",
                        "permission": tool_manifest.permission,
                    },
                )
            manifests.append(tool_manifest)
        return _list_decision("ok", (), diagnostics, manifests=tuple(manifests))

    def call_tool(
        self,
        manifest: ToolManifest,
        arguments: Mapping[str, object],
        *,
        provider: McpProviderPort | None = None,
        server_ref: str | None = None,
        security_manifest: McpServerSecurityManifest | Mapping[str, object] | None = None,
    ) -> McpCallDecision:
        safe_server_ref = _safe_public_ref(server_ref or manifest.source.package, prefix="mcp")
        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeProviderEnabled": self.config.local_fake_provider_enabled,
            "serverRef": safe_server_ref,
            "toolRef": f"tool:{_digest(manifest.name)[:16]}",
        }
        if not self.config.enabled:
            return _call_decision("disabled", ("mcp_adapter_disabled",), diagnostic_metadata=diagnostics)
        if _safe_public_ref(manifest.source.package, prefix="mcp") != safe_server_ref:
            return _call_decision(
                "blocked",
                ("mcp_tool_server_ref_mismatch",),
                diagnostic_metadata=diagnostics,
            )
        security, manifest_reasons = _coerce_security_manifest(safe_server_ref, security_manifest)
        if manifest_reasons:
            return _call_decision("blocked", manifest_reasons, diagnostic_metadata=diagnostics)
        if manifest.permission not in security.allowed_permissions:
            return _call_decision(
                "blocked",
                ("mcp_tool_permission_not_allowed_by_manifest",),
                diagnostic_metadata={**diagnostics, "permission": manifest.permission},
            )
        if manifest.permission != "read":
            return _call_decision(
                "blocked",
                ("mcp_call_tool_readonly_permission_required",),
                diagnostic_metadata={**diagnostics, "permission": manifest.permission},
            )
        if not self.config.local_fake_provider_enabled or provider is None:
            return _call_decision(
                "blocked",
                ("local_fake_mcp_provider_required",),
                diagnostic_metadata=diagnostics,
            )
        if getattr(provider, "openmagi_local_fake_provider", False) is not True:
            return _call_decision(
                "blocked",
                ("local_fake_mcp_provider_untrusted",),
                diagnostic_metadata=diagnostics,
            )
        try:
            raw_result = provider.call_tool(safe_server_ref, manifest.name, dict(arguments))
        except McpAuthError as exc:
            return _call_decision(
                "auth_required",
                ("mcp_auth_required",),
                diagnostic_metadata={**diagnostics, "providerErrorDigest": _digest(_safe_text(str(exc)))[:16]},
            )
        except Exception as exc:
            return _call_decision(
                "error",
                ("mcp_provider_call_failed",),
                diagnostic_metadata={**diagnostics, "providerErrorDigest": _digest(_safe_text(str(exc)))[:16]},
            )

        public_output = _extract_public_mcp_output(raw_result)
        tool_result = ToolResult(
            status="ok",
            output=public_output,
            llmOutput=public_output,
            transcriptOutput=public_output,
            metadata={
                "provider": "mcp",
                "serverRef": safe_server_ref,
                "toolRef": f"tool:{_digest(manifest.name)[:16]}",
            },
        )
        budgeted = budget_tool_result(tool_result, budget=manifest.budget)
        receipt = build_provider_receipt(
            provider_name="mcp",
            operation="tool_call",
            status="ok",
            request_payload={
                "serverRef": safe_server_ref,
                "toolRef": f"tool:{_digest(manifest.name)[:16]}",
                "argumentsDigest": _digest(arguments),
            },
            response_payload=budgeted.public_projection(),
            duration_ms=0,
        )
        return _call_decision(
            "ok",
            (),
            budgeted_result=budgeted,
            receipt_ref=receipt.receipt_id,
            diagnostic_metadata=diagnostics,
        )


def _list_decision(
    status: McpListStatus,
    reason_codes: tuple[str, ...],
    diagnostic_metadata: Mapping[str, object],
    *,
    manifests: tuple[ToolManifest, ...] = (),
) -> McpListDecision:
    return McpListDecision(
        status=status,
        manifests=manifests,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostic_metadata),
        authorityFlags=McpAuthorityFlags(),
    )


def _call_decision(
    status: McpCallStatus,
    reason_codes: tuple[str, ...],
    *,
    budgeted_result: BudgetedToolResult | None = None,
    receipt_ref: str | None = None,
    diagnostic_metadata: Mapping[str, object] | None = None,
) -> McpCallDecision:
    return McpCallDecision(
        status=status,
        reasonCodes=reason_codes,
        budgetedResult=budgeted_result,
        receiptRef=receipt_ref,
        diagnosticMetadata=_safe_metadata(diagnostic_metadata or {}),
        authorityFlags=McpAuthorityFlags(),
    )


def _coerce_security_manifest(
    server_ref: str,
    manifest: McpServerSecurityManifest | Mapping[str, object] | None,
) -> tuple[McpServerSecurityManifest, tuple[str, ...]]:
    if manifest is None:
        return _empty_security_manifest(server_ref), ("mcp_security_manifest_required",)
    try:
        parsed = (
            manifest
            if isinstance(manifest, McpServerSecurityManifest)
            else McpServerSecurityManifest.model_validate(manifest)
        )
    except Exception:
        return _empty_security_manifest(server_ref), ("mcp_security_manifest_invalid",)
    if parsed.server_ref != server_ref:
        return parsed, ("mcp_security_manifest_server_mismatch",)
    if parsed.sandbox_mode != "in_process_contract_only":
        return parsed, ("mcp_sandbox_mode_not_available",)
    if parsed.trust_level in {"verified_third_party", "local_dev"} and parsed.supply_chain_digest is None:
        return parsed, ("mcp_supply_chain_digest_required",)
    if not parsed.allowed_permissions:
        return parsed, ("mcp_allowed_permissions_required",)
    return parsed, ()


def _empty_security_manifest(server_ref: str) -> McpServerSecurityManifest:
    return McpServerSecurityManifest(
        serverRef=server_ref,
        trustLevel="first_party",
        sandboxMode="in_process_contract_only",
        allowedPermissions=("read",),
    )


def _tool_manifest_from_mcp(server_ref: str, raw_tool: Mapping[str, object]) -> ToolManifest:
    raw_name = raw_tool.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raw_name = "unnamed"
    annotations = raw_tool.get("annotations")
    if not isinstance(annotations, Mapping):
        annotations = {}
    permission = _permission_from_annotations(annotations)
    destructive = permission != "read" or annotations.get("destructiveHint") is True
    open_world = permission == "net" or annotations.get("openWorldHint") is True
    side_effect = "none" if permission == "read" and not open_world else "external"
    return ToolManifest(
        name=f"{_server_namespace(server_ref)}.{_safe_tool_segment(raw_name)}",
        description=_safe_text(str(raw_tool.get("description") or "MCP tool")),
        kind="external",
        source=ToolSource(kind="external", package=server_ref),
        permission=permission,
        inputSchema=_safe_schema(raw_tool.get("inputSchema")),
        outputSchema=None,
        dangerous=destructive,
        isConcurrencySafe=permission == "read",
        mutatesWorkspace=False,
        tags=("mcp",),
        shouldDefer=True,
        capabilityTags=tuple(
            item
            for item in (
                "mcp",
                server_ref,
                "open_world" if open_world else None,
                "destructive" if destructive else None,
            )
            if item is not None
        ),
        sideEffectClass=side_effect,  # type: ignore[arg-type]
        parallelSafety="readonly" if permission == "read" else "unsafe",
        adkToolType="FunctionTool",
        timeoutMs=5000,
        budget=Budget(outputChars=4000, transcriptChars=1200),
        plugin_id=f"mcp.{_safe_tool_segment(server_ref.removeprefix('mcp:'))}",
        enabled_by_default=False,
        opt_out=True,
    )


def _permission_from_annotations(annotations: Mapping[str, object]) -> PermissionName:
    if annotations.get("openWorldHint") is True:
        return "net"
    if annotations.get("destructiveHint") is True or annotations.get("writeHint") is True:
        return "write"
    if annotations.get("readOnlyHint") is True:
        return "read"
    return "net"


def _safe_schema(value: object) -> dict[str, object]:
    return project_public_tool_schema(value)


def _extract_public_mcp_output(raw_result: Mapping[str, object]) -> object:
    content = raw_result.get("content")
    if isinstance(content, Sequence) and not isinstance(content, str | bytes | bytearray):
        texts: list[str] = []
        for item in content:
            if not isinstance(item, Mapping):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                clean = _safe_text(str(item["text"]))
                if clean:
                    texts.append(clean)
        return "\n".join(texts)
    return _safe_payload(raw_result)


def _safe_payload(value: object) -> object:
    if isinstance(value, Mapping):
        safe: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                continue
            safe[_safe_payload_key(key_text)] = _safe_payload(item)
        return safe
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_safe_payload(item) for item in value]
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return _safe_text(repr(value))


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if _is_sensitive_key(key_text):
            continue
        clean_key = _safe_payload_key(key_text)
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean:
                safe[clean_key] = clean[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[clean_key] = value
    return safe


def _safe_payload_key(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.:-]", "_", value).strip("._:-")
    if not clean or _contains_private_text(clean):
        return f"key:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"
    return clean[:80]


def _safe_text(value: str) -> str:
    return redact_public_schema_text(value)


def _safe_public_ref(value: str, *, prefix: str) -> str:
    clean = _safe_text(str(value).strip())
    if clean and _SAFE_REF_RE.fullmatch(clean) and not _contains_private_text(clean):
        return clean
    return f"{prefix}:{hashlib.sha1(str(value).encode('utf-8')).hexdigest()[:16]}"


def _server_namespace(server_ref: str) -> str:
    tail = server_ref.removeprefix("mcp:")
    return f"mcp.{_safe_tool_segment(tail)}"


def _safe_tool_segment(value: str) -> str:
    if _contains_private_text(value):
        return f"tool_{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"
    clean = _SAFE_NAME_RE.sub("_", value.strip()).strip("_").lower()
    return clean[:80] if clean else "tool"


def _contains_private_text(value: str) -> bool:
    return contains_private_schema_text(value)


def _is_sensitive_key(value: str) -> bool:
    return is_sensitive_schema_key(value)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "McpAdapter",
    "McpAdapterConfig",
    "McpAuthError",
    "McpAuthorityFlags",
    "McpCallDecision",
    "McpListDecision",
    "McpProviderPort",
    "McpServerSecurityManifest",
]
