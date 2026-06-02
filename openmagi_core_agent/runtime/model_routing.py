from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RoutingSource: TypeAlias = Literal["per_turn_injected", "router_resolved"]
ResolvedRoutingSource: TypeAlias = Literal[
    "per_turn_injected",
    "router_resolved",
    "bot_config_fallback",
]
CredentialRefSource: TypeAlias = Literal["server_config"]
DecisionStatus: TypeAlias = Literal["accepted", "skipped", "rejected"]
DecisionReason: TypeAlias = Literal[
    "accepted",
    "disabled",
    "request_controlled_escalation",
    "fallback_disabled",
    "provider_not_allowed",
    "model_not_allowed",
    "provider_mismatch",
    "route_not_allowed",
    "credential_ref_not_allowed",
]
FutureInvocationSurface: TypeAlias = Literal["adk_agent_runner_only"]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_TURN_ID_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_TURN_ID_MAX_LENGTH = 255
_PROVIDER_LABEL_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET_OR_PATH_RE = re.compile(
    r"(?:"
    r"^\s*$|"
    r"\s|"
    r"[\\/'\"`$=]|"
    r"\.\.|"
    r"~|"
    r"://|"
    r"^sk-|"
    r"^xox[a-z]-|"
    r"^gh[opusr]_|"
    r"^github_pat_|"
    r"^AIza|"
    r"^gw_|"
    r"\bbearer\b|"
    r"api[_-]?key|"
    r"secret|"
    r"token|"
    r"password|"
    r"private[_-]?key"
    r")",
    re.IGNORECASE,
)


class _MetadataModel(BaseModel):
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


class ModelRouteMetadata(_MetadataModel):
    routing_source: RoutingSource = Field(alias="routingSource")
    provider_label: str = Field(alias="providerLabel")
    model_label: str = Field(alias="modelLabel")
    credential_ref: str | None = Field(default=None, alias="credentialRef")
    credential_ref_source: CredentialRefSource = Field(
        default="server_config",
        alias="credentialRefSource",
    )
    router_decision_digest: str | None = Field(default=None, alias="routerDecisionDigest")

    @field_validator("provider_label")
    @classmethod
    def _validate_provider_label(cls, value: str) -> str:
        return _validate_provider_label(value, "providerLabel")

    @field_validator("model_label")
    @classmethod
    def _validate_model_label(cls, value: str) -> str:
        return _validate_model_label(value, "modelLabel")

    @field_validator("credential_ref")
    @classmethod
    def _validate_credential_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_credential_ref(value, "credentialRef")

    @field_validator("router_decision_digest")
    @classmethod
    def _validate_router_decision_digest(cls, value: str | None) -> str | None:
        if value is not None and not _DIGEST_RE.fullmatch(value):
            raise ValueError("routerDecisionDigest must be a sha256 digest")
        return value


class ServerSideBotConfigFallback(_MetadataModel):
    provider_label: str = Field(alias="providerLabel")
    model_label: str = Field(alias="modelLabel")
    credential_ref: str | None = Field(default=None, alias="credentialRef")
    credential_ref_source: CredentialRefSource = Field(
        default="server_config",
        alias="credentialRefSource",
    )
    bot_config_digest: str | None = Field(default=None, alias="botConfigDigest")

    @field_validator("provider_label")
    @classmethod
    def _validate_provider_label(cls, value: str) -> str:
        return _validate_provider_label(value, "providerLabel")

    @field_validator("model_label")
    @classmethod
    def _validate_model_label(cls, value: str) -> str:
        return _validate_model_label(value, "modelLabel")

    @field_validator("credential_ref")
    @classmethod
    def _validate_credential_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_credential_ref(value, "credentialRef")

    @field_validator("bot_config_digest")
    @classmethod
    def _validate_bot_config_digest(cls, value: str | None) -> str | None:
        if value is not None and not _DIGEST_RE.fullmatch(value):
            raise ValueError("botConfigDigest must be a sha256 digest")
        return value


class RequestControlledRoutingMetadata(_MetadataModel):
    provider_label: str | None = Field(default=None, alias="providerLabel")
    model_label: str | None = Field(default=None, alias="modelLabel")
    credential_ref: str | None = Field(default=None, alias="credentialRef")

    @property
    def has_escalation_fields(self) -> bool:
        return any(
            isinstance(value, str) and value.strip()
            for value in (self.provider_label, self.model_label, self.credential_ref)
        )


class ModelRoutingPolicyConfig(_MetadataModel):
    enabled: bool = False
    bot_config_fallback_approved: bool = Field(
        default=False,
        alias="botConfigFallbackApproved",
    )
    allowed_provider_labels: tuple[str, ...] = Field(
        default=(),
        alias="allowedProviderLabels",
    )
    allowed_model_labels: tuple[str, ...] = Field(default=(), alias="allowedModelLabels")
    allowed_model_routes: tuple[str, ...] = Field(default=(), alias="allowedModelRoutes")
    allowed_credential_refs: tuple[str, ...] = Field(
        default=(),
        alias="allowedCredentialRefs",
    )

    @field_validator("allowed_provider_labels")
    @classmethod
    def _validate_allowed_provider_labels(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique(value, "allowedProviderLabels", _validate_provider_label)

    @field_validator("allowed_model_labels")
    @classmethod
    def _validate_allowed_model_labels(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique(value, "allowedModelLabels", _validate_model_label)

    @field_validator("allowed_model_routes")
    @classmethod
    def _validate_allowed_model_routes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique(value, "allowedModelRoutes", _validate_model_route)

    @field_validator("allowed_credential_refs")
    @classmethod
    def _validate_allowed_credential_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique(value, "allowedCredentialRefs", _validate_credential_ref)


class ModelRoutingResolutionRequest(_MetadataModel):
    turn_id: str = Field(alias="turnId")
    routing_metadata: ModelRouteMetadata | None = Field(
        default=None,
        alias="routingMetadata",
    )
    bot_config_fallback: ServerSideBotConfigFallback | None = Field(
        default=None,
        alias="botConfigFallback",
    )
    request_controlled_routing: RequestControlledRoutingMetadata | None = Field(
        default=None,
        alias="requestControlledRouting",
    )

    @field_validator("turn_id")
    @classmethod
    def _validate_turn_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("turnId must not have surrounding whitespace")
        if len(value) > _TURN_ID_MAX_LENGTH:
            raise ValueError("turnId must be at most 255 characters")
        if _SECRET_OR_PATH_RE.search(value):
            raise ValueError("turnId must not contain path or secret material")
        if not all(_TURN_ID_SEGMENT_RE.fullmatch(segment) for segment in value.split("::")):
            raise ValueError("turnId must be an opaque non-path label")
        return value


class ModelRoutingDecision(_MetadataModel):
    schema_version: Literal["priorityA.modelRoutingDecision.v1"] = Field(
        default="priorityA.modelRoutingDecision.v1",
        alias="schemaVersion",
    )
    accepted: bool
    status: DecisionStatus
    reason: DecisionReason
    selected_provider_label: str | None = Field(
        default=None,
        alias="selectedProviderLabel",
    )
    selected_model_label: str | None = Field(default=None, alias="selectedModelLabel")
    selected_credential_ref: str | None = Field(
        default=None,
        alias="selectedCredentialRef",
    )
    routing_source: ResolvedRoutingSource | None = Field(default=None, alias="routingSource")
    used_bot_config_fallback: bool = Field(
        default=False,
        alias="usedBotConfigFallback",
    )
    authoritative_runtime_model_header_required: bool = Field(
        default=False,
        alias="authoritativeRuntimeModelHeaderRequired",
    )
    future_invocation_surface: FutureInvocationSurface = Field(
        default="adk_agent_runner_only",
        alias="futureInvocationSurface",
    )
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    route_activation_allowed: Literal[False] = Field(
        default=False,
        alias="routeActivationAllowed",
    )
    provider_call_allowed: Literal[False] = Field(
        default=False,
        alias="providerCallAllowed",
    )
    adk_runner_invocation_allowed: Literal[False] = Field(
        default=False,
        alias="adkRunnerInvocationAllowed",
    )
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    production_write_allowed: Literal[False] = Field(
        default=False,
        alias="productionWriteAllowed",
    )

    @model_validator(mode="after")
    def _validate_decision_consistency(self) -> Self:
        if self.accepted and self.status != "accepted":
            raise ValueError("accepted decisions must use status=accepted")
        if not self.accepted and self.status == "accepted":
            raise ValueError("status=accepted requires accepted=True")
        if self.accepted:
            if self.reason != "accepted":
                raise ValueError("accepted decisions must use reason=accepted")
            if self.selected_provider_label is None or self.selected_model_label is None:
                raise ValueError("accepted decisions must include selected provider and model")
            if self.routing_source is None:
                raise ValueError("accepted decisions must include routingSource")
            if not self.authoritative_runtime_model_header_required:
                raise ValueError("accepted decisions require authoritative runtime model metadata")
        else:
            if (
                self.selected_provider_label is not None
                or self.selected_model_label is not None
                or self.selected_credential_ref is not None
            ):
                raise ValueError("rejected/skipped decisions must not expose selected route labels")
            if self.authoritative_runtime_model_header_required:
                raise ValueError("non-accepted decisions cannot require authoritative headers")
        return self


def build_turn_model_routing_decision(
    request: ModelRoutingResolutionRequest,
    *,
    config: ModelRoutingPolicyConfig | None = None,
) -> ModelRoutingDecision:
    policy = config or ModelRoutingPolicyConfig()

    if not policy.enabled:
        return _skipped("disabled")

    if request.request_controlled_routing is not None:
        if request.request_controlled_routing.has_escalation_fields:
            return _rejected("request_controlled_escalation")

    if request.routing_metadata is not None:
        return _resolve_candidate(
            provider_label=request.routing_metadata.provider_label,
            model_label=request.routing_metadata.model_label,
            credential_ref=request.routing_metadata.credential_ref,
            routing_source=request.routing_metadata.routing_source,
            used_bot_config_fallback=False,
            config=policy,
        )

    if request.bot_config_fallback is None or not policy.bot_config_fallback_approved:
        return _rejected("fallback_disabled")

    return _resolve_candidate(
        provider_label=request.bot_config_fallback.provider_label,
        model_label=request.bot_config_fallback.model_label,
        credential_ref=request.bot_config_fallback.credential_ref,
        routing_source="bot_config_fallback",
        used_bot_config_fallback=True,
        config=policy,
    )


def _resolve_candidate(
    *,
    provider_label: str,
    model_label: str,
    credential_ref: str | None,
    routing_source: ResolvedRoutingSource,
    used_bot_config_fallback: bool,
    config: ModelRoutingPolicyConfig,
) -> ModelRoutingDecision:
    inferred_provider = _infer_provider_from_model_label(model_label)
    if inferred_provider is not None and inferred_provider != provider_label:
        return _rejected("provider_mismatch")
    if provider_label not in config.allowed_provider_labels:
        return _rejected("provider_not_allowed")
    if model_label not in config.allowed_model_labels:
        return _rejected("model_not_allowed")
    if f"{provider_label}:{model_label}" not in config.allowed_model_routes:
        return _rejected("route_not_allowed")
    if credential_ref is not None and credential_ref not in config.allowed_credential_refs:
        return _rejected("credential_ref_not_allowed")

    return ModelRoutingDecision(
        accepted=True,
        status="accepted",
        reason="accepted",
        selectedProviderLabel=provider_label,
        selectedModelLabel=model_label,
        selectedCredentialRef=credential_ref,
        routingSource=routing_source,
        usedBotConfigFallback=used_bot_config_fallback,
        authoritativeRuntimeModelHeaderRequired=True,
    )


def _skipped(reason: Literal["disabled"]) -> ModelRoutingDecision:
    return ModelRoutingDecision(accepted=False, status="skipped", reason=reason)


def _rejected(reason: DecisionReason) -> ModelRoutingDecision:
    return ModelRoutingDecision(accepted=False, status="rejected", reason=reason)


def _validate_provider_label(value: str, field_name: str) -> str:
    value = _validate_safe_label(value, field_name)
    if not _PROVIDER_LABEL_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase provider label")
    return value


def _validate_model_label(value: str, field_name: str) -> str:
    value = _validate_safe_label(value, field_name)
    if not _LABEL_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase model label")
    return value


def _validate_credential_ref(value: str, field_name: str) -> str:
    value = _validate_safe_label(value, field_name)
    if not _LABEL_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be an opaque server credential reference")
    return value


def _validate_model_route(value: str, field_name: str) -> str:
    if value.count(":") != 1:
        raise ValueError(f"{field_name} entries must use provider:model")
    provider_label, model_label = value.split(":", 1)
    return f"{_validate_provider_label(provider_label, field_name)}:{_validate_model_label(model_label, field_name)}"


def _validate_safe_label(value: str, field_name: str) -> str:
    if value != value.strip():
        raise ValueError(f"{field_name} must not have surrounding whitespace")
    if _SECRET_OR_PATH_RE.search(value):
        raise ValueError(f"{field_name} must not contain path or secret-shaped material")
    return value


def _validate_unique(
    value: tuple[str, ...],
    field_name: str,
    validator: Any,
) -> tuple[str, ...]:
    normalized = tuple(validator(item, field_name) for item in value)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


def _infer_provider_from_model_label(model_label: str) -> str | None:
    if model_label.startswith("claude-"):
        return "anthropic"
    if model_label.startswith("gpt-") or model_label.startswith("o1") or model_label.startswith("o3"):
        return "openai"
    if model_label.startswith("gemini-"):
        return "google"
    if model_label.startswith("kimi-") or model_label.startswith("minimax-"):
        return "fireworks"
    return None


__all__ = [
    "ModelRouteMetadata",
    "ModelRoutingDecision",
    "ModelRoutingPolicyConfig",
    "ModelRoutingResolutionRequest",
    "RequestControlledRoutingMetadata",
    "ServerSideBotConfigFallback",
    "build_turn_model_routing_decision",
]
