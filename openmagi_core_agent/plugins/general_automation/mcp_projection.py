from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


PermissionClass = Literal["read", "write", "execute", "net", "meta"]
McpProjectionStatus = Literal["projected_metadata", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,220}$")
_TOOL_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_]+")
_SENSITIVE_SCHEMA_KEYS = (
    "auth",
    "credential",
    "key",
    "password",
    "secret",
    "token",
)


class McpProjectionAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    mcp_server_attached: Literal[False] = Field(default=False, alias="mcpServerAttached")
    external_tool_execution_enabled: Literal[False] = Field(
        default=False,
        alias="externalToolExecutionEnabled",
    )
    credential_used: Literal[False] = Field(default=False, alias="credentialUsed")
    network_egress_enabled: Literal[False] = Field(
        default=False,
        alias="networkEgressEnabled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        return type(self)()


class McpToolProjectionRequest(BaseModel):
    model_config = _MODEL_CONFIG

    server_ref: str = Field(alias="serverRef")
    tool_name: str = Field(alias="toolName")
    permission_class: PermissionClass = Field(alias="permissionClass")
    allowed_permissions: tuple[PermissionClass, ...] = Field(alias="allowedPermissions")
    policy_ref: str = Field(alias="policyRef")
    input_schema: Mapping[str, object] = Field(default_factory=dict, alias="inputSchema")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("server_ref", "policy_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("tool_name")
    @classmethod
    def _validate_tool_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("toolName is required")
        return value.strip()

    @field_validator("allowed_permissions")
    @classmethod
    def _dedupe_permissions(
        cls,
        value: tuple[PermissionClass, ...],
    ) -> tuple[PermissionClass, ...]:
        return tuple(dict.fromkeys(value))


class McpToolProjection(BaseModel):
    model_config = _MODEL_CONFIG

    status: McpProjectionStatus
    tool_ref: str = Field(alias="toolRef")
    server_ref: str = Field(alias="serverRef")
    tool_name: str = Field(alias="toolName")
    permission_class: PermissionClass = Field(alias="permissionClass")
    policy_ref: str = Field(alias="policyRef")
    metadata_digest: str = Field(alias="metadataDigest")
    adk_tool: Mapping[str, object] | None = Field(default=None, alias="adkTool")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    authority_flags: McpProjectionAuthorityFlags = Field(
        default_factory=McpProjectionAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("tool_ref", "server_ref", "policy_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("metadata_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not re.fullmatch(r"^sha256:[a-f0-9]{64}$", value):
            raise ValueError("metadataDigest must be sha256")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "toolRef": self.tool_ref,
            "serverRef": self.server_ref,
            "toolName": self.tool_name,
            "permissionClass": self.permission_class,
            "policyRef": self.policy_ref,
            "metadataDigest": self.metadata_digest,
            "adkTool": None if self.adk_tool is None else dict(self.adk_tool),
            "reasonCodes": self.reason_codes,
            "adkBoundary": {
                "functionTool": "FunctionTool",
                "functionToolName": self.tool_name,
                "pluginLifecycle": "plugin lifecycle",
                "externalToolMetadataOnly": True,
            },
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


def project_mcp_tool_metadata(request: McpToolProjectionRequest) -> McpToolProjection:
    function_name = _function_name(request.server_ref, request.tool_name)
    tool_ref = _tool_ref(request, function_name)
    metadata_digest = _digest(request.metadata)
    if request.permission_class not in request.allowed_permissions:
        return McpToolProjection(
            status="blocked",
            toolRef=tool_ref,
            serverRef=request.server_ref,
            toolName=function_name,
            permissionClass=request.permission_class,
            policyRef=request.policy_ref,
            metadataDigest=metadata_digest,
            reasonCodes=("mcp_permission_not_allowed_by_policy",),
        )
    return McpToolProjection(
        status="projected_metadata",
        toolRef=tool_ref,
        serverRef=request.server_ref,
        toolName=function_name,
        permissionClass=request.permission_class,
        policyRef=request.policy_ref,
        metadataDigest=metadata_digest,
        adkTool=_function_tool_metadata(
            function_name,
            input_schema=_public_schema(request.input_schema),
        ),
        reasonCodes=("mcp_function_tool_metadata_only",),
    )


def _function_tool_metadata(
    name: str,
    *,
    input_schema: Mapping[str, object],
) -> dict[str, object]:
    return {
        "name": name,
        "adkToolType": "FunctionTool",
        "enabledByDefault": False,
        "handlerAttached": False,
        "mcpServerAttached": False,
        "description": "Metadata-only MCP tool projection through OpenMagi policy.",
        "inputSchema": dict(input_schema),
    }


def _public_schema(schema: Mapping[str, object]) -> dict[str, object]:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return {"type": "object", "properties": {}, "required": (), "additionalProperties": False}

    public_properties: dict[str, object] = {}
    for key, value in properties.items():
        key_text = str(key)
        if _is_sensitive_key(key_text):
            continue
        public_properties[key_text] = _public_property(value)

    required = schema.get("required", ())
    if not isinstance(required, tuple | list):
        required = ()
    return {
        "type": "object",
        "properties": public_properties,
        "required": tuple(item for item in required if str(item) in public_properties),
        "additionalProperties": False,
    }


def _public_property(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {"type": "string"}
    property_type = value.get("type")
    if isinstance(property_type, str) and property_type in {
        "array",
        "boolean",
        "integer",
        "number",
        "object",
        "string",
    }:
        return {"type": property_type}
    return {"type": "string"}


def _function_name(server_ref: str, tool_name: str) -> str:
    server_tail = server_ref.removeprefix("mcp:")
    return f"mcp.{_safe_segment(server_tail)}.{_safe_segment(tool_name)}"


def _tool_ref(request: McpToolProjectionRequest, function_name: str) -> str:
    return "tool:mcp-projection:" + _digest(
        {
            "serverRef": request.server_ref,
            "functionName": function_name,
            "permissionClass": request.permission_class,
            "policyRef": request.policy_ref,
        }
    )


def _safe_segment(value: str) -> str:
    clean = _TOOL_SEGMENT_RE.sub("_", value.strip()).strip("_").lower()
    return clean[:80] if clean else "tool"


def _is_sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(marker in normalized for marker in _SENSITIVE_SCHEMA_KEYS)


def _safe_ref(value: str) -> str:
    if not value or not _REF_RE.fullmatch(value):
        raise ValueError("ref must be a safe public reference")
    return value


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


__all__ = [
    "McpProjectionAuthorityFlags",
    "McpToolProjection",
    "McpToolProjectionRequest",
    "project_mcp_tool_metadata",
]
