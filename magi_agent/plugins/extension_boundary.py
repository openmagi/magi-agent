from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


ExtensionOperation = Literal[
    "skill.load",
    "external_tool.load",
    "runtime_hook.load",
    "mcp_server.load",
    "mcp_tool.project",
]
ExtensionStatus = Literal["disabled", "extension_intent", "projected_local_fake", "blocked"]
ExtensionKind = Literal["skill", "external_tool", "runtime_hook", "mcp_server", "mcp_tool"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_SENSITIVE_KEY_MARKERS = (
    "raw",
    "token",
    "secret",
    "credential",
    "password",
    "cookie",
    "path",
    "prompt",
    "transcript",
)


class ExtensionProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def preview_extension(self, request: ExtensionBoundaryRequest) -> Mapping[str, object]: ...


class ExtensionBoundaryConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_extension_provider_enabled: bool = Field(
        default=False,
        alias="localFakeExtensionProviderEnabled",
    )
    protected_runtime_hook_loading_enabled: Literal[False] = Field(
        default=False,
        alias="protectedRuntimeHookLoadingEnabled",
    )
    mcp_server_attached: Literal[False] = Field(default=False, alias="mcpServerAttached")
    external_code_execution_enabled: Literal[False] = Field(
        default=False,
        alias="externalCodeExecutionEnabled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class ExtensionAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    external_loader_attached: Literal[False] = Field(default=False, alias="externalLoaderAttached")
    mcp_server_attached: Literal[False] = Field(default=False, alias="mcpServerAttached")
    runtime_hook_attached: Literal[False] = Field(default=False, alias="runtimeHookAttached")
    external_code_executed: Literal[False] = Field(default=False, alias="externalCodeExecuted")
    credential_used: Literal[False] = Field(default=False, alias="credentialUsed")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "external_loader_attached",
        "mcp_server_attached",
        "runtime_hook_attached",
        "external_code_executed",
        "credential_used",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ExtensionBoundaryRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: ExtensionOperation
    extension_id: str = Field(alias="extensionId")
    kind: ExtensionKind | None = None
    protected_runtime_hook: bool = Field(default=False, alias="protectedRuntimeHook")
    requested_capabilities: tuple[str, ...] = Field(default=(), alias="requestedCapabilities")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("extension_id")
    @classmethod
    def _validate_extension_id(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("requested_capabilities")
    @classmethod
    def _sanitize_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)


class ExtensionPreview(BaseModel):
    model_config = _MODEL_CONFIG

    extension_id: str = Field(alias="extensionId")
    kind: ExtensionKind
    manifest_ref: str = Field(alias="manifestRef")
    capabilities: tuple[str, ...] = ()
    protected_runtime_hook: bool = Field(default=False, alias="protectedRuntimeHook")
    evidence_ref: str = Field(alias="evidenceRef")

    @field_validator("extension_id", "manifest_ref", "evidence_ref")
    @classmethod
    def _validate_refs(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)

    def public_projection(self) -> dict[str, object]:
        return {
            "extensionId": _public_ref(self.extension_id, "extension"),
            "kind": self.kind,
            "manifestRef": _public_ref(self.manifest_ref, "manifest"),
            "capabilities": [_public_ref(item, "capability") for item in self.capabilities],
            "protectedRuntimeHook": bool(self.protected_runtime_hook),
            "evidenceRef": _public_ref(self.evidence_ref, "evidence"),
        }


class ExtensionBoundaryDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: ExtensionStatus
    operation: ExtensionOperation
    preview: ExtensionPreview | None = None
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: ExtensionAuthorityFlags = Field(
        default_factory=ExtensionAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = ExtensionAuthorityFlags()
        return cls.model_validate(values)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "operation": self.operation,
            "preview": None if self.preview is None else self.preview.public_projection(),
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class ExtensionBoundary:
    """Default-off extension/MCP preview boundary.

    The boundary models SkillLoader, ExternalToolLoader, runtime-hook, and MCP
    provider surfaces without importing extension code, starting MCP servers, or
    attaching hooks/tools.
    """

    def __init__(self, config: ExtensionBoundaryConfig) -> None:
        self.config = config

    def execute(
        self,
        request: ExtensionBoundaryRequest,
        *,
        provider: ExtensionProviderPort | None = None,
    ) -> ExtensionBoundaryDecision:
        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeExtensionProviderEnabled": self.config.local_fake_extension_provider_enabled,
            "protectedRuntimeHookLoadingEnabled": False,
            "mcpServerAttached": False,
            "externalCodeExecutionEnabled": False,
            "routeAttached": False,
            **dict(request.metadata),
        }
        if not self.config.enabled:
            return _decision(request, "disabled", ("extension_boundary_disabled",), diagnostics)
        if request.protected_runtime_hook:
            return _decision(request, "blocked", ("protected_runtime_hook_blocked",), diagnostics)
        if not self.config.local_fake_extension_provider_enabled or provider is None:
            return _decision(request, "extension_intent", ("local_extension_provider_disabled",), diagnostics)
        if getattr(provider, "openmagi_local_fake_provider", False) is not True:
            return _decision(request, "blocked", ("local_fake_extension_provider_untrusted",), diagnostics)
        try:
            raw = provider.preview_extension(request)
        except Exception as exc:
            return _decision(
                request,
                "blocked",
                ("local_fake_extension_provider_error",),
                {**diagnostics, "providerError": _safe_text(str(exc)) or "[redacted-provider-error]"},
            )
        preview = _preview_from_raw(request, raw)
        return _decision(
            request,
            "projected_local_fake",
            ("local_fake_extension_preview_only",),
            diagnostics,
            preview=preview,
        )


def _decision(
    request: ExtensionBoundaryRequest,
    status: ExtensionStatus,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    *,
    preview: ExtensionPreview | None = None,
) -> ExtensionBoundaryDecision:
    return ExtensionBoundaryDecision(
        status=status,
        operation=request.operation,
        preview=preview,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=ExtensionAuthorityFlags(),
    )


def _preview_from_raw(request: ExtensionBoundaryRequest, raw: Mapping[str, object]) -> ExtensionPreview:
    kind = raw.get("kind") if raw.get("kind") in {"skill", "external_tool", "runtime_hook", "mcp_server", "mcp_tool"} else request.kind
    if kind is None:
        kind = _kind_for_operation(request.operation)
    capabilities = raw.get("capabilities")
    if not isinstance(capabilities, Sequence) or isinstance(capabilities, str | bytes | bytearray):
        capabilities = request.requested_capabilities
    return ExtensionPreview(
        extensionId=str(raw.get("extensionId") or request.extension_id),
        kind=kind,  # type: ignore[arg-type]
        manifestRef=str(raw.get("manifestRef") or f"manifest:{_digest(request.extension_id)}"),
        capabilities=tuple(str(item) for item in capabilities if isinstance(item, str))[:16],
        protectedRuntimeHook=bool(raw.get("protectedRuntimeHook") is True),
        evidenceRef=str(raw.get("evidenceRef") or f"evidence:{_digest(request.extension_id)}"),
    )


def _kind_for_operation(operation: ExtensionOperation) -> ExtensionKind:
    return {
        "skill.load": "skill",
        "external_tool.load": "external_tool",
        "runtime_hook.load": "runtime_hook",
        "mcp_server.load": "mcp_server",
        "mcp_tool.project": "mcp_tool",
    }[operation]


def _safe_ref(value: str) -> str:
    clean = _safe_text(value.strip())
    if not clean or _REF_RE.fullmatch(clean) is None:
        raise ValueError("extension refs must be public identifiers")
    return clean[:180]


def _public_ref(value: str, prefix: str) -> str:
    try:
        return _safe_ref(str(value))
    except ValueError:
        return f"{prefix}:{_digest(str(value))}"


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS):
            continue
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean:
                safe[str(key)] = clean[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[str(key)] = value
    return safe


def _safe_text(value: str) -> str:
    clean = _SECRET_TEXT_RE.sub("[redacted]", value)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean.strip()


def _digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "ExtensionBoundary",
    "ExtensionBoundaryConfig",
    "ExtensionBoundaryDecision",
    "ExtensionBoundaryRequest",
    "ExtensionPreview",
]
