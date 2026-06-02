from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


Gate5B4C2ShadowStatus: TypeAlias = Literal["accepted_for_diagnostic_shadow", "skipped"]
Gate5B4C2ShadowReason: TypeAlias = Literal[
    "accepted",
    "disabled",
    "selected_scope_missing",
    "trusted_org_missing",
    "selected_scope_mismatch",
]
Gate5B4C2ModelSelectionSource: TypeAlias = Literal[
    "per_turn_injected",
    "router_resolved",
    "bot_config_fallback",
    "default_fallback",
    "invalid_or_missing",
]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ALLOWED_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})
_UNSAFE_TEXT_RE = re.compile(
    r"(?:"
    r"Authorization:\s*Bearer\s+\S+|"
    r"(?:Cookie|Set-Cookie):\s*[^;\r\n]+(?:;[^\r\n]*)?|"
    r"Bearer\s+\S+|"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"xox[a-z]-[A-Za-z0-9-]{8,}|"
    r"\b\d{5,}:[A-Za-z0-9_-]{8,}|"
    r"\b(?:gh[opusr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+)|"
    r"[\"']?(?:access[_-]?token|refresh[_-]?token|api[_-]?key|"
    r"client[_-]?secret|private[_-]?key|session[_-]?key)[\"']?\s*:"
    r"\s*[\"'][^\"'\r\n]{4,}[\"']|"
    r"\b(?:[A-Z][A-Z0-9_]*(?:_TOKEN|_SECRET|_SECRET_KEY|_PASSWORD|"
    r"_API_KEY|_SERVICE_ROLE_KEY))"
    r"\s*=\s*(?:'[^'\r\n]*'|\"[^\"\r\n]*\"|[^\s'\"`;,]+)|"
    r"\b(?:api[_-]?key|token|secret|password|service[_-]?role[_-]?key)"
    r"\s*[:=]\s*\S+|"
    r"hidden_reasoning|chain_of_thought|private_reasoning|reasoning_trace|"
    r"private_tool_preview|private_tool_input|private_tool_output|raw_tool_preview|"
    r"/(?:data/bots|workspace|var/lib/kubelet|mnt|private|Users)\S*|"
    r"\b(?:kubectl|helm|kustomize|sealed-secrets|kubeconfig)\b|"
    r"\bclawy\.pro\b\S*|"
    r"https?://\S+|"
    r"s3://\S+"
    r")",
    re.IGNORECASE,
)


class _Gate5B4C2Model(BaseModel):
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
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            name_to_alias = {
                name: field.alias or name
                for name, field in self.__class__.model_fields.items()
            }
            data.update({name_to_alias.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)

    @model_validator(mode="after")
    def _reject_unsafe_strings(self) -> Self:
        _reject_unsafe_value(self.model_dump(mode="python", by_alias=True, warnings=False))
        return self


class Gate5B4C2ShadowAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    canary_routing_allowed: Literal[False] = Field(
        default=False,
        alias="canaryRoutingAllowed",
    )
    transcript_writes_allowed: Literal[False] = Field(
        default=False,
        alias="transcriptWritesAllowed",
    )
    sse_writes_allowed: Literal[False] = Field(default=False, alias="sseWritesAllowed")
    channel_writes_allowed: Literal[False] = Field(
        default=False,
        alias="channelWritesAllowed",
    )
    db_writes_allowed: Literal[False] = Field(default=False, alias="dbWritesAllowed")
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
    child_execution_allowed: Literal[False] = Field(
        default=False,
        alias="childExecutionAllowed",
    )
    mission_runtime_allowed: Literal[False] = Field(
        default=False,
        alias="missionRuntimeAllowed",
    )
    evidence_block_mode_allowed: Literal[False] = Field(
        default=False,
        alias="evidenceBlockModeAllowed",
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
        return type(self).model_validate(
            {field.alias or name: False for name, field in self.__class__.model_fields.items()}
        )

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        allowed_keys = {
            name
            for name in cls.model_fields
        } | {
            field.alias
            for field in cls.model_fields.values()
            if field.alias is not None
        }
        for key, raw_value in value.items():
            if key not in allowed_keys:
                raise ValueError("shadow authority flags contain unsupported fields")
            _reject_unsafe_value(key)
            _reject_unsafe_value(raw_value)
        return {field.alias or name: False for name, field in cls.model_fields.items()}

    @field_serializer(
        "user_visible_output_allowed",
        "canary_routing_allowed",
        "transcript_writes_allowed",
        "sse_writes_allowed",
        "channel_writes_allowed",
        "db_writes_allowed",
        "workspace_mutation_allowed",
        "memory_write_allowed",
        "tool_dispatch_allowed",
        "child_execution_allowed",
        "mission_runtime_allowed",
        "evidence_block_mode_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class Gate5B4C2ShadowSelection(_Gate5B4C2Model):
    bot_id_digest: str = Field(alias="botIdDigest")
    owner_user_id_digest: str = Field(alias="ownerUserIdDigest")
    environment: str
    selected_target: Literal["gate5b_selected_bot"] = Field(alias="selectedTarget")
    session_key_digest: str | None = Field(default=None, alias="sessionKeyDigest")

    @model_validator(mode="after")
    def _validate_selection(self) -> Self:
        _validate_digest(self.bot_id_digest, "selected bot metadata must be a sha256 digest")
        _validate_digest(
            self.owner_user_id_digest,
            "selected trusted owner metadata must be a sha256 digest",
        )
        if self.session_key_digest is not None:
            _validate_digest(
                self.session_key_digest,
                "session correlation metadata must be a sha256 digest",
            )
        _validate_environment(self.environment)
        return self


class Gate5B4C2ShadowTurn(_Gate5B4C2Model):
    turn_id: str = Field(alias="turnId")
    turn_digest: str = Field(alias="turnDigest")
    channel_name: str | None = Field(default=None, alias="channelName")
    redacted_bundle_ref: str | None = Field(default=None, alias="redactedBundleRef")
    ts_response_correlation_id: str | None = Field(
        default=None,
        alias="tsResponseCorrelationId",
    )

    @model_validator(mode="after")
    def _validate_turn(self) -> Self:
        _validate_safe_label(self.turn_id, "turn id must be opaque public-safe metadata")
        _validate_digest(self.turn_digest, "turn metadata must be a sha256 digest")
        if self.channel_name is not None:
            _validate_safe_label(self.channel_name, "channel label must be public-safe")
        if self.redacted_bundle_ref is not None:
            _validate_safe_label(
                self.redacted_bundle_ref,
                "redacted bundle reference must be opaque public-safe metadata",
            )
        if self.ts_response_correlation_id is not None:
            _validate_safe_label(
                self.ts_response_correlation_id,
                "TypeScript correlation id must be opaque public-safe metadata",
            )
        return self


class Gate5B4C2ShadowModelRouting(_Gate5B4C2Model):
    per_turn_provider: str | None = Field(default=None, alias="perTurnProvider")
    per_turn_model: str | None = Field(default=None, alias="perTurnModel")
    router_provider: str | None = Field(default=None, alias="routerProvider")
    router_model: str | None = Field(default=None, alias="routerModel")
    bot_config_provider: str | None = Field(default=None, alias="botConfigProvider")
    bot_config_model: str | None = Field(default=None, alias="botConfigModel")
    default_provider: str | None = Field(default=None, alias="defaultProvider")
    default_model: str | None = Field(default=None, alias="defaultModel")
    provider: str = Field(default="unresolved")
    model: str = Field(default="unresolved")
    model_profile: str | None = Field(default=None, alias="modelProfile")
    routing_profile_id: str | None = Field(default=None, alias="routingProfileId")
    model_selection_source: Gate5B4C2ModelSelectionSource = Field(
        default="invalid_or_missing",
        alias="modelSelectionSource",
    )
    temperature: float | None = None
    max_output_tokens: int | None = Field(default=None, alias="maxOutputTokens")
    deadline_ms: int | None = Field(default=None, alias="deadlineMs")
    credential_ref: str | None = Field(default=None, alias="credentialRef")

    @model_validator(mode="before")
    @classmethod
    def _derive_resolved_model(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = _normalize_model_routing_keys(value)
        provider, model, source = _resolve_model_routing(data)
        data["provider"] = provider
        data["model"] = model
        data["modelSelectionSource"] = source
        return data

    @model_validator(mode="after")
    def _validate_model_routing(self) -> Self:
        for label in (
            self.per_turn_provider,
            self.per_turn_model,
            self.router_provider,
            self.router_model,
            self.bot_config_provider,
            self.bot_config_model,
            self.default_provider,
            self.default_model,
            self.provider,
            self.model,
            self.model_profile,
            self.routing_profile_id,
            self.credential_ref,
        ):
            if label is not None and label != "unresolved":
                _validate_safe_label(label, "model routing metadata must be public-safe")
        if self.max_output_tokens is not None and self.max_output_tokens < 0:
            raise ValueError("max output token budget must be non-negative")
        if self.deadline_ms is not None and self.deadline_ms < 0:
            raise ValueError("deadline metadata must be non-negative")
        return self


class Gate5B4C2ShadowRecipeProfile(_Gate5B4C2Model):
    recipe_id: str = Field(alias="recipeId")
    recipe_version: str = Field(alias="recipeVersion")
    profile_id: str = Field(alias="profileId")
    profile_version: str = Field(alias="profileVersion")
    runtime_engine: Literal["adk-python"] = Field(alias="runtimeEngine")
    tools_policy: Literal["disabled", "stubbed_no_dispatch"] = Field(alias="toolsPolicy")
    memory_mode: Literal["disabled", "read_only", "test_only"] = Field(alias="memoryMode")
    source_authority: Literal["current_turn_over_memory", "memory_disabled"] = Field(
        alias="sourceAuthority",
    )

    @model_validator(mode="after")
    def _validate_recipe_labels(self) -> Self:
        for label in (
            self.recipe_id,
            self.recipe_version,
            self.profile_id,
            self.profile_version,
        ):
            _validate_safe_label(label, "recipe metadata must be public-safe")
        return self


class Gate5B4C2ShadowPolicy(_Gate5B4C2Model):
    type_script_response_authority: Literal[True] = Field(
        default=True,
        alias="typeScriptResponseAuthority",
    )
    python_diagnostic_only: Literal[True] = Field(
        default=True,
        alias="pythonDiagnosticOnly",
    )
    output_isolation: Literal["local_diagnostic_only"] = Field(alias="outputIsolation")
    tools_disabled: Literal[True] = Field(default=True, alias="toolsDisabled")
    tool_host_dispatch_allowed: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchAllowed",
    )
    memory_provider_calls_allowed: Literal[False] = Field(
        default=False,
        alias="memoryProviderCallsAllowed",
    )
    memory_writes_allowed: Literal[False] = Field(
        default=False,
        alias="memoryWritesAllowed",
    )
    prompt_memory_injection_allowed: Literal[False] = Field(
        default=False,
        alias="promptMemoryInjectionAllowed",
    )
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    child_execution_allowed: Literal[False] = Field(
        default=False,
        alias="childExecutionAllowed",
    )
    evidence_block_mode_allowed: Literal[False] = Field(
        default=False,
        alias="evidenceBlockModeAllowed",
    )


class Gate5B4C2ShadowBudgets(_Gate5B4C2Model):
    max_input_bytes: int = Field(alias="maxInputBytes")
    max_output_preview_bytes: int = Field(alias="maxOutputPreviewBytes")
    max_receipt_bytes: int = Field(alias="maxReceiptBytes")
    chat_proxy_call_timeout_ms: int = Field(alias="chatProxyCallTimeoutMs")
    python_runner_timeout_ms: int = Field(alias="pythonRunnerTimeoutMs")
    max_concurrent_shadow_invocations: int = Field(alias="maxConcurrentShadowInvocations")
    max_pending_shadow_invocations: int = Field(alias="maxPendingShadowInvocations")
    max_daily_shadow_invocations: int = Field(alias="maxDailyShadowInvocations")
    max_cost_usd: int = Field(alias="maxCostUsd")
    retry_policy: Literal["none"] = Field(alias="retryPolicy")

    @model_validator(mode="after")
    def _validate_budgets(self) -> Self:
        for value in (
            self.max_input_bytes,
            self.max_output_preview_bytes,
            self.max_receipt_bytes,
            self.python_runner_timeout_ms,
            self.max_concurrent_shadow_invocations,
            self.max_pending_shadow_invocations,
            self.max_daily_shadow_invocations,
        ):
            if value < 0:
                raise ValueError("shadow invocation budgets must be non-negative")
        if self.chat_proxy_call_timeout_ms < 500 or self.chat_proxy_call_timeout_ms > 1000:
            raise ValueError("chat proxy timeout must be between 500 and 1000 ms")
        if self.max_cost_usd != 0:
            raise ValueError("model cost budget must remain zero for this contract slice")
        return self


class Gate5B4C2ShadowRedaction(_Gate5B4C2Model):
    status: Literal["verified"]
    sanitizer_version: str = Field(alias="sanitizerVersion")
    dropped_field_reasons: tuple[str, ...] = Field(
        default=(),
        alias="droppedFieldReasons",
    )
    unsafe_input_action: Literal["drop_shadow_invocation"] = Field(alias="unsafeInputAction")

    @model_validator(mode="after")
    def _validate_redaction_labels(self) -> Self:
        _validate_safe_label(
            self.sanitizer_version,
            "redaction sanitizer version must be public-safe",
        )
        for reason in self.dropped_field_reasons:
            _validate_safe_label(reason, "redaction reason code must be public-safe")
        return self


class Gate5B4C2ShadowInvocationRequest(_Gate5B4C2Model):
    schema_version: Literal["gate5b4c2.chatProxyShadowInvocation.v1"] = Field(
        default="gate5b4c2.chatProxyShadowInvocation.v1",
        alias="schemaVersion",
    )
    mode: Literal["shadow_diagnostic_only"]
    response_authority: Literal["typescript"] = Field(alias="responseAuthority")
    shadow_invocation_id: str = Field(alias="shadowInvocationId")
    request_id_digest: str = Field(alias="requestIdDigest")
    trace_id_digest: str = Field(alias="traceIdDigest")
    created_at: int = Field(alias="createdAt")
    selection: Gate5B4C2ShadowSelection
    turn: Gate5B4C2ShadowTurn
    model_routing: Gate5B4C2ShadowModelRouting = Field(alias="modelRouting")
    recipe_profile: Gate5B4C2ShadowRecipeProfile = Field(alias="recipeProfile")
    policy: Gate5B4C2ShadowPolicy
    budgets: Gate5B4C2ShadowBudgets
    redaction: Gate5B4C2ShadowRedaction
    authority: Gate5B4C2ShadowAuthorityFlags = Field(
        default_factory=Gate5B4C2ShadowAuthorityFlags,
    )

    @model_validator(mode="after")
    def _validate_request(self) -> Self:
        _validate_safe_label(
            self.shadow_invocation_id,
            "shadow invocation id must be opaque public-safe metadata",
        )
        _validate_digest(self.request_id_digest, "request id must be a sha256 digest")
        _validate_digest(self.trace_id_digest, "trace id must be a sha256 digest")
        if self.created_at < 0:
            raise ValueError("createdAt must be non-negative Unix milliseconds")
        return self

    @field_serializer("authority")
    def _serialize_authority(self, _value: object) -> dict[str, bool]:
        return Gate5B4C2ShadowAuthorityFlags().model_dump(by_alias=True, mode="json")


class Gate5B4C2ShadowGateConfig(_Gate5B4C2Model):
    enabled: bool = False
    selected_bot_digest: str | None = Field(default=None, alias="selectedBotDigest")
    trusted_owner_user_id_digest: str | None = Field(
        default=None,
        alias="trustedOwnerUserIdDigest",
    )
    environment: str | None = None

    @model_validator(mode="after")
    def _validate_gate_config(self) -> Self:
        if self.selected_bot_digest is not None:
            _validate_digest(self.selected_bot_digest, "selected bot config must be a digest")
        if self.trusted_owner_user_id_digest is not None:
            _validate_digest(
                self.trusted_owner_user_id_digest,
                "trusted owner config must be a digest",
            )
        if self.environment is not None:
            _validate_environment(self.environment)
        return self


class Gate5B4C2ShadowReceipt(_Gate5B4C2Model):
    schema_version: Literal["gate5b4c2.shadowInvocationReceipt.v1"] = Field(
        default="gate5b4c2.shadowInvocationReceipt.v1",
        alias="schemaVersion",
    )
    accepted: bool
    status: Gate5B4C2ShadowStatus
    reason: Gate5B4C2ShadowReason
    shadow_invocation_id: str = Field(alias="shadowInvocationId")
    response_authority: Literal["typescript"] = Field(
        default="typescript",
        alias="responseAuthority",
    )
    diagnostic_only: Literal[True] = Field(default=True, alias="diagnosticOnly")
    fail_open: Literal[True] = Field(default=True, alias="failOpen")
    runner_attempted: Literal[False] = Field(default=False, alias="runnerAttempted")
    model_call_attempted: Literal[False] = Field(
        default=False,
        alias="modelCallAttempted",
    )
    latency_ms: int = Field(default=0, alias="latencyMs")
    provider: str
    model: str
    model_selection_source: Gate5B4C2ModelSelectionSource = Field(
        alias="modelSelectionSource",
    )
    authority: Gate5B4C2ShadowAuthorityFlags = Field(
        default_factory=Gate5B4C2ShadowAuthorityFlags,
    )

    @model_validator(mode="before")
    @classmethod
    def _force_non_authoritative_fields(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["responseAuthority"] = "typescript"
        data["diagnosticOnly"] = True
        data["failOpen"] = True
        data["runnerAttempted"] = False
        data["modelCallAttempted"] = False
        return data

    @field_serializer("authority")
    def _serialize_receipt_authority(self, _value: object) -> dict[str, bool]:
        return Gate5B4C2ShadowAuthorityFlags().model_dump(by_alias=True, mode="json")


def build_gate5b4c2_shadow_invocation_receipt(
    request: Gate5B4C2ShadowInvocationRequest,
    *,
    config: Gate5B4C2ShadowGateConfig | None = None,
    latency_ms: int = 0,
) -> Gate5B4C2ShadowReceipt:
    gate_config = config or Gate5B4C2ShadowGateConfig()
    accepted, status, reason = _gate_decision(request, gate_config)
    return Gate5B4C2ShadowReceipt(
        accepted=accepted,
        status=status,
        reason=reason,
        shadowInvocationId=request.shadow_invocation_id,
        latencyMs=max(latency_ms, 0),
        provider=request.model_routing.provider,
        model=request.model_routing.model,
        modelSelectionSource=request.model_routing.model_selection_source,
    )


def _gate_decision(
    request: Gate5B4C2ShadowInvocationRequest,
    config: Gate5B4C2ShadowGateConfig,
) -> tuple[bool, Gate5B4C2ShadowStatus, Gate5B4C2ShadowReason]:
    if not config.enabled:
        return False, "skipped", "disabled"
    if config.selected_bot_digest is None or config.environment is None:
        return False, "skipped", "selected_scope_missing"
    if config.trusted_owner_user_id_digest is None:
        return False, "skipped", "trusted_org_missing"
    if (
        request.selection.bot_id_digest != config.selected_bot_digest
        or request.selection.owner_user_id_digest != config.trusted_owner_user_id_digest
        or request.selection.environment != config.environment
    ):
        return False, "skipped", "selected_scope_mismatch"
    return True, "accepted_for_diagnostic_shadow", "accepted"


def _resolve_model_routing(
    data: Mapping[str, object],
) -> tuple[str, str, Gate5B4C2ModelSelectionSource]:
    for provider_key, model_key, source in (
        ("perTurnProvider", "perTurnModel", "per_turn_injected"),
        ("routerProvider", "routerModel", "router_resolved"),
        ("botConfigProvider", "botConfigModel", "bot_config_fallback"),
        ("defaultProvider", "defaultModel", "default_fallback"),
    ):
        provider = data.get(provider_key)
        model = data.get(model_key)
        if _is_safe_label(provider) and _is_safe_label(model):
            return str(provider), str(model), source  # type: ignore[return-value]
    return "unresolved", "unresolved", "invalid_or_missing"


def _normalize_model_routing_keys(data: Mapping[str, object]) -> dict[str, object]:
    normalized = dict(data)
    field_name_to_alias = {
        "per_turn_provider": "perTurnProvider",
        "per_turn_model": "perTurnModel",
        "router_provider": "routerProvider",
        "router_model": "routerModel",
        "bot_config_provider": "botConfigProvider",
        "bot_config_model": "botConfigModel",
        "default_provider": "defaultProvider",
        "default_model": "defaultModel",
        "model_profile": "modelProfile",
        "routing_profile_id": "routingProfileId",
        "model_selection_source": "modelSelectionSource",
        "max_output_tokens": "maxOutputTokens",
        "deadline_ms": "deadlineMs",
        "credential_ref": "credentialRef",
    }
    for field_name, alias in field_name_to_alias.items():
        if field_name in normalized:
            if alias not in normalized:
                normalized[alias] = normalized[field_name]
            normalized.pop(field_name, None)
    return normalized


def _validate_digest(value: str, message: str) -> None:
    if not isinstance(value, str) or not _DIGEST_RE.match(value):
        raise ValueError(message)


def _validate_environment(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("environment labels must be public-safe and trimmed")
    if _UNSAFE_TEXT_RE.search(value) or value.strip() != value or not value:
        raise ValueError("environment labels must be public-safe and trimmed")
    if value not in _ALLOWED_ENVIRONMENTS:
        raise ValueError("environment label is not recognized")


def _validate_safe_label(value: str, message: str) -> None:
    if not _is_safe_label(value):
        raise ValueError(message)


def _is_safe_label(value: object) -> bool:
    return isinstance(value, str) and bool(_SAFE_LABEL_RE.match(value))


def _reject_unsafe_value(value: object) -> None:
    if isinstance(value, str):
        if _UNSAFE_TEXT_RE.search(value):
            raise ValueError("shadow invocation payload contains forbidden private material")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            _reject_unsafe_value(key)
            _reject_unsafe_value(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _reject_unsafe_value(child)


__all__ = [
    "Gate5B4C2ModelSelectionSource",
    "Gate5B4C2ShadowAuthorityFlags",
    "Gate5B4C2ShadowBudgets",
    "Gate5B4C2ShadowGateConfig",
    "Gate5B4C2ShadowInvocationRequest",
    "Gate5B4C2ShadowModelRouting",
    "Gate5B4C2ShadowPolicy",
    "Gate5B4C2ShadowReceipt",
    "Gate5B4C2ShadowReason",
    "Gate5B4C2ShadowRecipeProfile",
    "Gate5B4C2ShadowRedaction",
    "Gate5B4C2ShadowSelection",
    "Gate5B4C2ShadowStatus",
    "Gate5B4C2ShadowTurn",
    "build_gate5b4c2_shadow_invocation_receipt",
]
