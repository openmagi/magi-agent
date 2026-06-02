from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


Gate5B4EndpointMode: TypeAlias = Literal[
    "health_only",
    "shadow_diagnostic_only",
    "candidate_user_visible_pending_approval",
]
Gate5B4HealthStatus: TypeAlias = Literal["healthy"]
Gate5B4ReadinessStatus: TypeAlias = Literal["ready", "not_ready"]
Gate5B4CapabilityStatus: TypeAlias = Literal[
    "health_only",
    "shadow_diagnostic_only",
    "pending_approval",
]
Gate5B4ResponseAuthority: TypeAlias = Literal["none", "diagnostic_only"]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_ALLOWED_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})
_MAX_DIAGNOSTIC_PREVIEW_CHARS = 240
_UNSAFE_TEXT_RE = re.compile(
    r"(?:"
    r"Authorization:\s*Bearer\s+\S+|"
    r"(?:Cookie|Set-Cookie):\s*[^;\r\n]+(?:;[^\r\n]*)?|"
    r"Bearer\s+\S+|"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"xox[a-z]-[A-Za-z0-9-]{8,}|"
    r"\b(?:gh[opusr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+)|"
    r"[\"']?(?:access[_-]?token|refresh[_-]?token|api[_-]?key|"
    r"client[_-]?secret|private[_-]?key)[\"']?\s*:\s*[\"'][^\"'\r\n]{4,}[\"']|"
    r"\b(?:[A-Z][A-Z0-9_]*(?:_TOKEN|_SECRET|_SECRET_KEY|_PASSWORD|"
    r"_API_KEY|_SERVICE_ROLE_KEY))"
    r"\s*=\s*(?:'[^'\r\n]*'|\"[^\"\r\n]*\"|[^\s'\"`;,]+)|"
    r"\b(?:api[_-]?key|token|secret|password|service[_-]?role[_-]?key)"
    r"\s*[:=]\s*\S+|"
    r"hidden_reasoning|chain_of_thought|private_reasoning|reasoning_trace|"
    r"private_tool_preview|private_tool_input|private_tool_output|raw_tool_preview|"
    r"/(?:data/bots|workspace|var/lib/kubelet|mnt|private|Users)\S*|"
    r"\bmagi\.pro\b\S*"
    r")",
    re.IGNORECASE,
)


class _Gate5B4Model(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class Gate5B4EndpointAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    transcript_write_allowed: Literal[False] = Field(
        default=False,
        alias="transcriptWriteAllowed",
    )
    sse_write_allowed: Literal[False] = Field(default=False, alias="sseWriteAllowed")
    channel_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAllowed",
    )
    db_write_allowed: Literal[False] = Field(default=False, alias="dbWriteAllowed")
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    memory_write_allowed: Literal[False] = Field(
        default=False,
        alias="memoryWriteAllowed",
    )
    tool_dispatch_allowed: Literal[False] = Field(
        default=False,
        alias="toolDispatchAllowed",
    )
    canary_routing_allowed: Literal[False] = Field(
        default=False,
        alias="canaryRoutingAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{field.alias or name: False for name, field in cls.model_fields.items()})

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied.model_dump(by_alias=True, mode="python"))

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return {field.alias or name: False for name, field in cls.model_fields.items()}

    @field_serializer(
        "user_visible_output_allowed",
        "transcript_write_allowed",
        "sse_write_allowed",
        "channel_delivery_allowed",
        "db_write_allowed",
        "workspace_mutation_allowed",
        "memory_write_allowed",
        "tool_dispatch_allowed",
        "canary_routing_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class Gate5B4EndpointContractConfig(_Gate5B4Model):
    mode: Gate5B4EndpointMode = "health_only"
    selected_bot_digest: str = Field(alias="selectedBotDigest")
    selected_org_digest: str = Field(alias="selectedOrgDigest")
    environment: str
    diagnostic_preview: str | None = Field(default=None, alias="diagnosticPreview")

    @model_validator(mode="after")
    def _validate_config(self) -> Self:
        _validate_digest(
            self.selected_bot_digest,
            "Gate 5B-4 selected bot metadata must be sha256 digests",
        )
        _validate_digest(
            self.selected_org_digest,
            "Gate 5B-4 selected org metadata must be sha256 digests",
        )
        _validate_environment(self.environment)
        return self


class Gate5B4InternalEndpointContract(_Gate5B4Model):
    schema_version: Literal["gate5b4.internalEndpointContract.v1"] = Field(
        default="gate5b4.internalEndpointContract.v1",
        alias="schemaVersion",
    )
    health_status: Gate5B4HealthStatus = Field(default="healthy", alias="healthStatus")
    readiness_status: Gate5B4ReadinessStatus = Field(alias="readinessStatus")
    canary_capability_status: Gate5B4CapabilityStatus = Field(
        alias="canaryCapabilityStatus",
    )
    supported_modes: tuple[Gate5B4EndpointMode, ...] = Field(alias="supportedModes")
    mode: Gate5B4EndpointMode
    response_authority: Gate5B4ResponseAuthority = Field(alias="responseAuthority")
    selected_bot_digest: str = Field(alias="selectedBotDigest")
    selected_org_digest: str = Field(alias="selectedOrgDigest")
    environment: str
    metadata_source: Literal["validated_config"] = Field(
        default="validated_config",
        alias="metadataSource",
    )
    diagnostic_preview_public: str | None = Field(
        default=None,
        alias="diagnosticPreviewPublic",
    )
    authority_flags: Gate5B4EndpointAuthorityFlags = Field(
        default_factory=Gate5B4EndpointAuthorityFlags,
        alias="authorityFlags",
    )
    user_visible_response_envelope_possible: Literal[False] = Field(
        default=False,
        alias="userVisibleResponseEnvelopePossible",
    )
    chat_proxy_call_allowed: Literal[False] = Field(
        default=False,
        alias="chatProxyCallAllowed",
    )
    runtime_selector_activation_allowed: Literal[False] = Field(
        default=False,
        alias="runtimeSelectorActivationAllowed",
    )
    model_call_endpoint_exposed: Literal[False] = Field(
        default=False,
        alias="modelCallEndpointExposed",
    )
    adk_runner_endpoint_exposed: Literal[False] = Field(
        default=False,
        alias="adkRunnerEndpointExposed",
    )
    public_route_exposed: Literal[False] = Field(default=False, alias="publicRouteExposed")

    @model_validator(mode="before")
    @classmethod
    def _force_internal_only_fields(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        mode = data.get("mode", "health_only")
        if mode in {
            "health_only",
            "shadow_diagnostic_only",
            "candidate_user_visible_pending_approval",
        }:
            readiness, capability, authority = _mode_status(mode)
            data["readinessStatus"] = readiness
            data["canaryCapabilityStatus"] = capability
            data["responseAuthority"] = authority
        data["userVisibleResponseEnvelopePossible"] = False
        data["chatProxyCallAllowed"] = False
        data["runtimeSelectorActivationAllowed"] = False
        data["modelCallEndpointExposed"] = False
        data["adkRunnerEndpointExposed"] = False
        data["publicRouteExposed"] = False
        preview = data.get("diagnosticPreviewPublic", data.get("diagnostic_preview_public"))
        data["diagnosticPreviewPublic"] = _sanitize_preview(preview) if preview is not None else None
        data.pop("user_visible_response_envelope_possible", None)
        data.pop("chat_proxy_call_allowed", None)
        data.pop("runtime_selector_activation_allowed", None)
        data.pop("model_call_endpoint_exposed", None)
        data.pop("adk_runner_endpoint_exposed", None)
        data.pop("public_route_exposed", None)
        data.pop("readiness_status", None)
        data.pop("canary_capability_status", None)
        data.pop("response_authority", None)
        data.pop("diagnostic_preview_public", None)
        return data

    @model_validator(mode="after")
    def _validate_contract_metadata(self) -> Self:
        _validate_digest(
            self.selected_bot_digest,
            "Gate 5B-4 selected bot metadata must be sha256 digests",
        )
        _validate_digest(
            self.selected_org_digest,
            "Gate 5B-4 selected org metadata must be sha256 digests",
        )
        _validate_environment(self.environment)
        return self

    @field_serializer("authority_flags")
    def _serialize_authority_flags(self, _value: object) -> dict[str, bool]:
        return Gate5B4EndpointAuthorityFlags().model_dump(by_alias=True, mode="json")


def build_gate5b4_internal_endpoint_contract(
    config: Gate5B4EndpointContractConfig,
) -> Gate5B4InternalEndpointContract:
    readiness, capability, authority = _mode_status(config.mode)
    return Gate5B4InternalEndpointContract(
        readinessStatus=readiness,
        canaryCapabilityStatus=capability,
        supportedModes=(
            "health_only",
            "shadow_diagnostic_only",
            "candidate_user_visible_pending_approval",
        ),
        mode=config.mode,
        responseAuthority=authority,
        selectedBotDigest=config.selected_bot_digest,
        selectedOrgDigest=config.selected_org_digest,
        environment=config.environment,
        diagnosticPreviewPublic=_sanitize_preview(config.diagnostic_preview),
    )


def _mode_status(
    mode: Gate5B4EndpointMode,
) -> tuple[Gate5B4ReadinessStatus, Gate5B4CapabilityStatus, Gate5B4ResponseAuthority]:
    if mode == "shadow_diagnostic_only":
        return "ready", "shadow_diagnostic_only", "diagnostic_only"
    if mode == "candidate_user_visible_pending_approval":
        return "not_ready", "pending_approval", "diagnostic_only"
    return "not_ready", "health_only", "none"


def _validate_digest(value: str, message: str) -> None:
    if not isinstance(value, str) or not _DIGEST_RE.match(value):
        raise ValueError(message)


def _validate_environment(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("Gate 5B-4 environment labels must be public-safe and trimmed")
    if _UNSAFE_TEXT_RE.search(value) or value.strip() != value or not value:
        raise ValueError("Gate 5B-4 environment labels must be public-safe and trimmed")
    if value not in _ALLOWED_ENVIRONMENTS:
        raise ValueError("Gate 5B-4 environment label is not recognized")


def _sanitize_preview(value: str | None) -> str | None:
    if value is None:
        return None
    safe = _UNSAFE_TEXT_RE.sub("[redacted]", value)
    return safe[:_MAX_DIAGNOSTIC_PREVIEW_CHARS]


__all__ = [
    "Gate5B4CapabilityStatus",
    "Gate5B4EndpointAuthorityFlags",
    "Gate5B4EndpointContractConfig",
    "Gate5B4EndpointMode",
    "Gate5B4HealthStatus",
    "Gate5B4InternalEndpointContract",
    "Gate5B4ReadinessStatus",
    "Gate5B4ResponseAuthority",
    "build_gate5b4_internal_endpoint_contract",
]
