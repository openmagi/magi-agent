from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


Gate5B4C3ShadowGenerationStatus: TypeAlias = Literal["accepted", "skipped", "dropped"]
Gate5B4C3ShadowGenerationReason: TypeAlias = Literal[
    "accepted",
    "disabled",
    "kill_switch_active",
    "selected_scope_missing",
    "trusted_org_missing",
    "selected_scope_mismatch",
    "cap_state_uninitialized",
    "budget_exhausted",
    "model_routing_source_not_allowed",
    "model_routing_not_allowlisted",
    "shadow_credential_ref_not_allowlisted",
    "provider_credential_binding_missing",
]
Gate5B4C3ModelRoutingSource: TypeAlias = Literal[
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
_MODEL_ROUTE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_ALLOWED_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})
_ALLOWED_CHANNELS = frozenset({"app_channel", "telegram", "discord", "web", "unknown"})
_UNSAFE_FIELD_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "setcookie",
        "endpointurl",
        "outputpath",
        "callerprovidedoutputpath",
        "messages",
        "rawusertext",
        "fulltranscript",
        "privatememory",
        "memoryrecall",
        "rawtoolargs",
        "rawtoolresult",
        "rawtooloutput",
        "workspacepath",
        "k8spath",
        "deploypath",
        "kubeconfig",
        "telegramtoken",
        "childprompt",
        "childoutput",
        "evidenceblockmode",
        "productionwritedirective",
        "runtimeselectordirective",
        "uservisibleresponseauthority",
        "privatetool",
        "hiddenreasoning",
        "chainofthought",
        "privatereasoning",
        "reasoningtrace",
    }
)
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


class _Gate5B4C3Model(BaseModel):
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
    def _reject_private_material(self) -> Self:
        _reject_unsafe_value(self.model_dump(mode="python", by_alias=True, warnings=False))
        return self


class Gate5B4C3ShadowGenerationAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    canary_routing_allowed: Literal[False] = Field(default=False, alias="canaryRoutingAllowed")
    transcript_writes_allowed: Literal[False] = Field(
        default=False,
        alias="transcriptWritesAllowed",
    )
    sse_writes_allowed: Literal[False] = Field(default=False, alias="sseWritesAllowed")
    channel_writes_allowed: Literal[False] = Field(default=False, alias="channelWritesAllowed")
    db_writes_allowed: Literal[False] = Field(default=False, alias="dbWritesAllowed")
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    tool_dispatch_allowed: Literal[False] = Field(default=False, alias="toolDispatchAllowed")
    child_execution_allowed: Literal[False] = Field(default=False, alias="childExecutionAllowed")
    mission_runtime_allowed: Literal[False] = Field(default=False, alias="missionRuntimeAllowed")
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
                raise ValueError("shadow generation authority flags contain unsupported fields")
            _reject_unsafe_key(key)
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


class Gate5B4C3ShadowGenerationSelection(_Gate5B4C3Model):
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


class Gate5B4C3ShadowGenerationAttachmentMetadata(_Gate5B4C3Model):
    kind: str
    count: int | None = None
    digest: str | None = None

    @model_validator(mode="after")
    def _validate_attachment_metadata(self) -> Self:
        _validate_safe_label(self.kind, "attachment metadata kind must be public-safe")
        if self.count is not None and self.count < 0:
            raise ValueError("attachment metadata count must be non-negative")
        if self.digest is not None:
            _validate_digest(self.digest, "attachment metadata digest must be sha256")
        return self


class Gate5B4C3ShadowGenerationTurn(_Gate5B4C3Model):
    turn_id: str = Field(alias="turnId")
    turn_digest: str = Field(alias="turnDigest")
    sanitized_current_turn_text: str = Field(alias="sanitizedCurrentTurnText")
    sanitized_input_text_digest: str = Field(alias="sanitizedInputTextDigest")
    redacted_bundle_ref: str | None = Field(default=None, alias="redactedBundleRef")
    channel_name: str | None = Field(default=None, alias="channelName")
    channel_digest: str | None = Field(default=None, alias="channelDigest")
    attachment_metadata: tuple[Gate5B4C3ShadowGenerationAttachmentMetadata, ...] = Field(
        default=(),
        alias="attachmentMetadata",
    )
    ts_response_correlation_id: str | None = Field(
        default=None,
        alias="tsResponseCorrelationId",
    )

    @model_validator(mode="after")
    def _validate_turn(self) -> Self:
        _validate_safe_label(self.turn_id, "turn id must be opaque public-safe metadata")
        _validate_digest(self.turn_digest, "turn metadata must be a sha256 digest")
        _validate_digest(
            self.sanitized_input_text_digest,
            "sanitized turn text digest must be a sha256 digest",
        )
        if len(self.sanitized_current_turn_text.encode("utf-8")) > 8192:
            raise ValueError("sanitized current turn text exceeds first-slice byte limit")
        if self.redacted_bundle_ref is not None:
            _validate_safe_label(
                self.redacted_bundle_ref,
                "redacted bundle reference must be opaque public-safe metadata",
            )
        if self.channel_name is not None and self.channel_name not in _ALLOWED_CHANNELS:
            raise ValueError("channel metadata must be a constrained public-safe value")
        if self.channel_digest is not None:
            _validate_digest(self.channel_digest, "channel metadata must be a sha256 digest")
        if self.ts_response_correlation_id is not None:
            _validate_safe_label(
                self.ts_response_correlation_id,
                "TypeScript correlation id must be opaque public-safe metadata",
            )
        return self


class Gate5B4C3ShadowGenerationModelRouting(_Gate5B4C3Model):
    routing_source: Gate5B4C3ModelRoutingSource = Field(alias="routingSource")
    provider_label: str = Field(alias="providerLabel")
    model_label: str = Field(alias="modelLabel")
    router_decision_digest: str | None = Field(default=None, alias="routerDecisionDigest")
    routing_profile_digest: str | None = Field(default=None, alias="routingProfileDigest")
    bot_config_model_digest: str | None = Field(default=None, alias="botConfigModelDigest")
    fallback_reason: str | None = Field(default=None, alias="fallbackReason")
    fallback_approved: bool = Field(default=False, alias="fallbackApproved")
    shadow_credential_ref: str | None = Field(default=None, alias="shadowCredentialRef")
    credential_ref_source: Literal["server_config"] = Field(alias="credentialRefSource")
    temperature: float | None = None
    max_output_tokens: int | None = Field(default=None, alias="maxOutputTokens")

    @model_validator(mode="after")
    def _validate_model_routing(self) -> Self:
        _validate_safe_label(
            self.provider_label,
            "model routing provider metadata must be public-safe",
        )
        _validate_model_route_component(
            self.provider_label,
            "model routing provider metadata must be public-safe and unambiguous",
        )
        _validate_model_route_component(
            self.model_label,
            "model routing model metadata must be public-safe and unambiguous",
        )
        for digest in (
            self.router_decision_digest,
            self.routing_profile_digest,
            self.bot_config_model_digest,
        ):
            if digest is not None:
                _validate_digest(digest, "model routing digest metadata must be sha256")
        if self.routing_source in {"per_turn_injected", "router_resolved"}:
            if self.router_decision_digest is None:
                raise ValueError("turn-scoped model routing requires router decision digest")
            if self.routing_profile_digest is None:
                raise ValueError("turn-scoped model routing requires routing profile digest")
        if self.routing_source in {
            "bot_config_fallback",
            "default_fallback",
            "invalid_or_missing",
        }:
            if self.fallback_reason is None:
                raise ValueError("model routing fallback requires a reason code")
            _validate_safe_label(self.fallback_reason, "fallback reason must be public-safe")
        if self.routing_source == "bot_config_fallback":
            if self.bot_config_model_digest is None or not self.fallback_approved:
                raise ValueError("bot config fallback requires digest and approval")
        if self.routing_source == "default_fallback" and not self.fallback_approved:
            raise ValueError("default model fallback requires explicit approval")
        if self.routing_source == "invalid_or_missing":
            raise ValueError("invalid model routing cannot be accepted for generation")
        if self.shadow_credential_ref is not None:
            _validate_safe_label(
                self.shadow_credential_ref,
                "shadow credential reference must be opaque public-safe metadata",
            )
        if self.temperature is not None and (self.temperature < 0 or self.temperature > 2):
            raise ValueError("temperature metadata must be between 0 and 2")
        if self.max_output_tokens is not None:
            if self.max_output_tokens < 1 or self.max_output_tokens > 512:
                raise ValueError("max output token metadata exceeds first-slice limit")
        return self


class Gate5B4C3ShadowGenerationRecipeProfile(_Gate5B4C3Model):
    recipe_id: str = Field(alias="recipeId")
    recipe_version: str = Field(alias="recipeVersion")
    profile_id: str = Field(alias="profileId")
    profile_version: str = Field(alias="profileVersion")
    runtime_engine: Literal["adk-python"] = Field(alias="runtimeEngine")
    tools_policy: Literal[
        "disabled",
        "shadow_readonly",
        "selected_full_toolhost",
    ] = Field(alias="toolsPolicy")
    memory_mode: Literal["disabled"] = Field(alias="memoryMode")
    source_authority: Literal["current_turn_only"] = Field(alias="sourceAuthority")

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


class Gate5B4C3ShadowGenerationPolicy(_Gate5B4C3Model):
    type_script_response_authority: Literal[True] = Field(
        default=True,
        alias="typeScriptResponseAuthority",
    )
    python_diagnostic_only: Literal[True] = Field(default=True, alias="pythonDiagnosticOnly")
    output_isolation: Literal["local_diagnostic_only"] = Field(alias="outputIsolation")
    tools_disabled: bool = Field(default=True, alias="toolsDisabled")
    tool_host_dispatch_allowed: bool = Field(default=False, alias="toolHostDispatchAllowed")
    memory_provider_calls_allowed: Literal[False] = Field(
        default=False,
        alias="memoryProviderCallsAllowed",
    )
    memory_writes_allowed: Literal[False] = Field(default=False, alias="memoryWritesAllowed")
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
    mission_runtime_allowed: Literal[False] = Field(default=False, alias="missionRuntimeAllowed")
    evidence_block_mode_allowed: Literal[False] = Field(
        default=False,
        alias="evidenceBlockModeAllowed",
    )


MAX_PYTHON_RUNNER_TIMEOUT_MS = 120_000


class Gate5B4C3ShadowGenerationBudgets(_Gate5B4C3Model):
    chat_proxy_call_timeout_ms: int = Field(default=750, alias="chatProxyCallTimeoutMs")
    python_runner_timeout_ms: int = Field(default=30_000, alias="pythonRunnerTimeoutMs")
    max_sanitized_input_bytes: int = Field(default=8192, alias="maxSanitizedInputBytes")
    max_estimated_input_tokens: int = Field(default=2048, alias="maxEstimatedInputTokens")
    max_sanitized_history_messages: int = Field(
        default=0,
        alias="maxSanitizedHistoryMessages",
    )
    max_output_tokens: int = Field(default=512, alias="maxOutputTokens")
    max_total_estimated_tokens: int = Field(default=2560, alias="maxTotalEstimatedTokens")
    max_diagnostic_output_preview_bytes: int = Field(
        default=2048,
        alias="maxDiagnosticOutputPreviewBytes",
    )
    max_diagnostic_artifact_bytes: int = Field(
        default=16_384,
        alias="maxDiagnosticArtifactBytes",
    )
    max_concurrent_generation_runs: int = Field(
        default=1,
        alias="maxConcurrentGenerationRuns",
    )
    max_pending_generation_runs: int = Field(default=1, alias="maxPendingGenerationRuns")
    max_daily_generation_runs: int = Field(default=10, alias="maxDailyGenerationRuns")
    retry_policy: Literal["none"] = Field(default="none", alias="retryPolicy")
    max_cost_usd: float = Field(default=0.05, alias="maxCostUsd")
    max_daily_generation_cost_usd: float = Field(
        default=0.50,
        alias="maxDailyGenerationCostUsd",
    )

    @model_validator(mode="after")
    def _validate_budgets(self) -> Self:
        for value in (
            self.chat_proxy_call_timeout_ms,
            self.python_runner_timeout_ms,
            self.max_sanitized_input_bytes,
            self.max_estimated_input_tokens,
            self.max_sanitized_history_messages,
            self.max_output_tokens,
            self.max_total_estimated_tokens,
            self.max_diagnostic_output_preview_bytes,
            self.max_diagnostic_artifact_bytes,
            self.max_concurrent_generation_runs,
            self.max_pending_generation_runs,
            self.max_daily_generation_runs,
        ):
            if value < 0:
                raise ValueError("shadow generation budgets must be non-negative")
        if self.chat_proxy_call_timeout_ms != 750:
            raise ValueError("generation acceptance timeout must remain 750 ms")
        if self.python_runner_timeout_ms > MAX_PYTHON_RUNNER_TIMEOUT_MS:
            raise ValueError("Python generation timeout exceeds selected runner limit")
        if self.max_sanitized_input_bytes > 8192:
            raise ValueError("sanitized input budget exceeds first-slice limit")
        if self.max_estimated_input_tokens > 2048:
            raise ValueError("input token budget exceeds first-slice limit")
        if self.max_sanitized_history_messages != 0:
            raise ValueError("first generation slice cannot include history")
        if self.max_output_tokens > 512:
            raise ValueError("output token budget exceeds first-slice limit")
        if self.max_total_estimated_tokens > 2560:
            raise ValueError("total token budget exceeds first-slice limit")
        if self.max_diagnostic_output_preview_bytes > 2048:
            raise ValueError("diagnostic preview budget exceeds first-slice limit")
        if self.max_diagnostic_artifact_bytes > 16_384:
            raise ValueError("diagnostic artifact budget exceeds first-slice limit")
        if self.max_concurrent_generation_runs > 1:
            raise ValueError("concurrency cap exceeds first-slice limit")
        if self.max_pending_generation_runs > 1:
            raise ValueError("pending cap exceeds first-slice limit")
        if self.max_daily_generation_runs > 10:
            raise ValueError("daily generation cap exceeds first-slice limit")
        if self.retry_policy != "none":
            raise ValueError("first generation slice cannot retry")
        if self.max_cost_usd < 0 or self.max_cost_usd > 0.05:
            raise ValueError("per-generation cost cap exceeds first-slice limit")
        if self.max_daily_generation_cost_usd < 0 or self.max_daily_generation_cost_usd > 0.50:
            raise ValueError("daily generation cost cap exceeds first-slice limit")
        return self


class Gate5B4C3ShadowGenerationRedaction(_Gate5B4C3Model):
    sanitizer_id: str = Field(alias="sanitizerId")
    sanitizer_version: str = Field(alias="sanitizerVersion")
    policy_id: str = Field(alias="policyId")
    status: Literal["passed"]
    redacted_at: int = Field(alias="redactedAt")
    redacted_byte_count: int = Field(alias="redactedByteCount")
    forbidden_field_scan: Literal["passed"] = Field(alias="forbiddenFieldScan")
    sanitized_payload_digest: str = Field(alias="sanitizedPayloadDigest")
    dropped_field_reasons: tuple[str, ...] = Field(
        default=(),
        alias="droppedFieldReasons",
    )

    @model_validator(mode="after")
    def _validate_redaction(self) -> Self:
        for label in (self.sanitizer_id, self.sanitizer_version, self.policy_id):
            _validate_safe_label(label, "redaction proof metadata must be public-safe")
        if self.redacted_at < 0 or self.redacted_byte_count < 0:
            raise ValueError("redaction proof metadata must be non-negative")
        _validate_digest(self.sanitized_payload_digest, "sanitized payload digest must be sha256")
        for reason in self.dropped_field_reasons:
            _validate_safe_label(reason, "redaction reason code must be public-safe")
        return self


class Gate5B4C3ShadowGenerationComparison(_Gate5B4C3Model):
    type_script_final_answer_digest: str | None = Field(
        default=None,
        alias="typeScriptFinalAnswerDigest",
    )
    type_script_terminal_status: str | None = Field(
        default=None,
        alias="typeScriptTerminalStatus",
    )

    @model_validator(mode="after")
    def _validate_comparison(self) -> Self:
        if self.type_script_final_answer_digest is not None:
            _validate_digest(
                self.type_script_final_answer_digest,
                "TypeScript answer digest must be sha256",
            )
        if self.type_script_terminal_status is not None:
            _validate_safe_label(
                self.type_script_terminal_status,
                "TypeScript terminal status must be public-safe",
            )
        return self


class Gate5B4C3ShadowGenerationRequest(_Gate5B4C3Model):
    schema_version: Literal["gate5b4c3.chatProxyShadowGeneration.v1"] = Field(
        default="gate5b4c3.chatProxyShadowGeneration.v1",
        alias="schemaVersion",
    )
    mode: Literal["shadow_generation_diagnostic"]
    response_authority: Literal["typescript"] = Field(alias="responseAuthority")
    shadow_generation_id: str = Field(alias="shadowGenerationId")
    request_id_digest: str = Field(alias="requestIdDigest")
    trace_id_digest: str = Field(alias="traceIdDigest")
    created_at: int = Field(alias="createdAt")
    selection: Gate5B4C3ShadowGenerationSelection
    turn: Gate5B4C3ShadowGenerationTurn
    model_routing: Gate5B4C3ShadowGenerationModelRouting = Field(alias="modelRouting")
    recipe_profile: Gate5B4C3ShadowGenerationRecipeProfile = Field(alias="recipeProfile")
    policy: Gate5B4C3ShadowGenerationPolicy
    budgets: Gate5B4C3ShadowGenerationBudgets = Field(
        default_factory=Gate5B4C3ShadowGenerationBudgets,
    )
    redaction: Gate5B4C3ShadowGenerationRedaction
    comparison: Gate5B4C3ShadowGenerationComparison | None = None
    authority: Gate5B4C3ShadowGenerationAuthorityFlags = Field(
        default_factory=Gate5B4C3ShadowGenerationAuthorityFlags,
    )

    @model_validator(mode="after")
    def _validate_request(self) -> Self:
        _validate_safe_label(
            self.shadow_generation_id,
            "shadow generation id must be opaque public-safe metadata",
        )
        _validate_digest(self.request_id_digest, "request id must be a sha256 digest")
        _validate_digest(self.trace_id_digest, "trace id must be a sha256 digest")
        if self.created_at < 0:
            raise ValueError("createdAt must be non-negative Unix milliseconds")
        if self.redaction.sanitized_payload_digest != self.turn.sanitized_input_text_digest:
            raise ValueError("sanitizer proof digest must match sanitized turn input digest")
        if (
            len(self.turn.sanitized_current_turn_text.encode("utf-8"))
            > self.budgets.max_sanitized_input_bytes
        ):
            raise ValueError("sanitized input exceeds configured generation budget")
        return self

    @field_serializer("authority")
    def _serialize_authority(self, _value: object) -> dict[str, bool]:
        return Gate5B4C3ShadowGenerationAuthorityFlags().model_dump(
            by_alias=True,
            mode="json",
        )


class Gate5B4C3ShadowGenerationProviderCredentialBinding(_Gate5B4C3Model):
    provider_label: str = Field(alias="providerLabel")
    credential_ref: str = Field(alias="credentialRef")
    credential_source: Literal["env_presence", "env_presence_and_project"] = Field(
        alias="credentialSource",
    )
    required_env_vars: tuple[str, ...] = Field(alias="requiredEnvVars")
    present_env_vars: tuple[str, ...] = Field(default=(), alias="presentEnvVars")
    project_id_digest: str | None = Field(default=None, alias="projectIdDigest")
    adk_native: bool = Field(default=True, alias="adkNative")

    @model_validator(mode="after")
    def _validate_binding(self) -> Self:
        _validate_model_route_component(
            self.provider_label,
            "provider credential binding provider must be public-safe",
        )
        _validate_safe_label(
            self.credential_ref,
            "provider credential binding ref must be opaque public-safe metadata",
        )
        if not self.required_env_vars:
            raise ValueError("provider credential binding requires at least one env presence proof")
        for env_name in self.required_env_vars + self.present_env_vars:
            if not isinstance(env_name, str) or not _SAFE_ENV_NAME_RE.match(env_name):
                raise ValueError("provider credential binding env names must be public-safe names")
        if not set(self.required_env_vars).issubset(set(self.present_env_vars)):
            raise ValueError("provider credential binding is missing required env presence proof")
        if self.project_id_digest is not None:
            _validate_digest(
                self.project_id_digest,
                "provider project metadata must be represented as a digest",
            )
        return self


class Gate5B4C3ShadowGenerationConfig(_Gate5B4C3Model):
    enabled: bool = False
    kill_switch_active: bool = Field(default=True, alias="killSwitchActive")
    cap_state_initialized: bool = Field(default=False, alias="capStateInitialized")
    generation_budget_exhausted: bool = Field(
        default=False,
        alias="generationBudgetExhausted",
    )
    provider_project_spend_controls_verified: bool = Field(
        default=False,
        alias="providerProjectSpendControlsVerified",
    )
    cost_owner_waiver: bool = Field(default=False, alias="costOwnerWaiver")
    in_flight_generation_runs: int = Field(default=0, alias="inFlightGenerationRuns")
    pending_generation_runs: int = Field(default=0, alias="pendingGenerationRuns")
    daily_generation_runs_used: int = Field(default=0, alias="dailyGenerationRunsUsed")
    daily_generation_cost_usd_used: float = Field(
        default=0,
        alias="dailyGenerationCostUsdUsed",
    )
    selected_bot_digest: str | None = Field(default=None, alias="selectedBotDigest")
    trusted_owner_user_id_digest: str | None = Field(
        default=None,
        alias="trustedOwnerUserIdDigest",
    )
    environment: str | None = None
    allowed_provider_labels: tuple[str, ...] = Field(
        default=(),
        alias="allowedProviderLabels",
    )
    allowed_model_labels: tuple[str, ...] = Field(default=(), alias="allowedModelLabels")
    allowed_model_routes: tuple[str, ...] = Field(default=(), alias="allowedModelRoutes")
    allowed_shadow_credential_refs: tuple[str, ...] = Field(
        default=(),
        alias="allowedShadowCredentialRefs",
    )
    provider_credential_bindings: tuple[
        Gate5B4C3ShadowGenerationProviderCredentialBinding,
        ...,
    ] = Field(default=(), alias="providerCredentialBindings")
    provider_credential_binding_required: bool = Field(
        default=False,
        alias="providerCredentialBindingRequired",
    )
    bot_config_fallback_allowed: bool = Field(
        default=False,
        alias="botConfigFallbackAllowed",
    )
    bot_config_fallback_approval_digest: str | None = Field(
        default=None,
        alias="botConfigFallbackApprovalDigest",
    )
    approved_budgets: Gate5B4C3ShadowGenerationBudgets = Field(
        default_factory=Gate5B4C3ShadowGenerationBudgets,
        alias="approvedBudgets",
    )

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
        for label in self.allowed_provider_labels + self.allowed_model_labels:
            _validate_model_route_component(
                label,
                "model allowlist labels must be public-safe and unambiguous",
            )
        for route in self.allowed_model_routes:
            if route.count(":") != 1:
                raise ValueError("model route allowlist entries must be provider:model")
            provider, model = route.split(":", 1)
            _validate_model_route_component(
                provider,
                "model route provider must be public-safe and unambiguous",
            )
            _validate_model_route_component(
                model,
                "model route model must be public-safe and unambiguous",
            )
        for ref in self.allowed_shadow_credential_refs:
            _validate_safe_label(ref, "shadow credential allowlist refs must be public-safe")
        if self.bot_config_fallback_approval_digest is not None:
            _validate_digest(
                self.bot_config_fallback_approval_digest,
                "bot config fallback approval metadata must be a digest",
            )
        for value in (
            self.in_flight_generation_runs,
            self.pending_generation_runs,
            self.daily_generation_runs_used,
        ):
            if value < 0:
                raise ValueError("generation cap counters must be non-negative")
        if self.daily_generation_cost_usd_used < 0:
            raise ValueError("generation cost counter must be non-negative")
        return self


class Gate5B4C3ShadowGenerationOutputMetadata(_Gate5B4C3Model):
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    output_preview_included: Literal[False] = Field(
        default=False,
        alias="outputPreviewIncluded",
    )
    output_hash_included: Literal[False] = Field(default=False, alias="outputHashIncluded")
    comparison_artifact_included: Literal[False] = Field(
        default=False,
        alias="comparisonArtifactIncluded",
    )


class Gate5B4C3ShadowGenerationDiagnostic(_Gate5B4C3Model):
    schema_version: Literal["gate5b4c3.shadowGenerationDiagnostic.v1"] = Field(
        default="gate5b4c3.shadowGenerationDiagnostic.v1",
        alias="schemaVersion",
    )
    accepted: bool
    status: Gate5B4C3ShadowGenerationStatus
    reason: Gate5B4C3ShadowGenerationReason
    shadow_generation_id: str = Field(alias="shadowGenerationId")
    response_authority: Literal["typescript"] = Field(
        default="typescript",
        alias="responseAuthority",
    )
    diagnostic_only: Literal[True] = Field(default=True, alias="diagnosticOnly")
    fail_open: Literal[True] = Field(default=True, alias="failOpen")
    adk_invoked: Literal[False] = Field(default=False, alias="adkInvoked")
    runner_attempted: Literal[False] = Field(default=False, alias="runnerAttempted")
    model_call_attempted: Literal[False] = Field(default=False, alias="modelCallAttempted")
    latency_ms: int = Field(default=0, alias="latencyMs")
    provider: str
    model: str
    routing_source: Gate5B4C3ModelRoutingSource = Field(alias="routingSource")
    fallback_reason: str | None = Field(default=None, alias="fallbackReason")
    output_metadata: Gate5B4C3ShadowGenerationOutputMetadata = Field(
        default_factory=Gate5B4C3ShadowGenerationOutputMetadata,
        alias="outputMetadata",
    )
    authority: Gate5B4C3ShadowGenerationAuthorityFlags = Field(
        default_factory=Gate5B4C3ShadowGenerationAuthorityFlags,
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
        data["adkInvoked"] = False
        data["runnerAttempted"] = False
        data["modelCallAttempted"] = False
        return data

    @model_validator(mode="after")
    def _validate_diagnostic_consistency(self) -> Self:
        if self.accepted and self.status != "accepted":
            raise ValueError("accepted diagnostics must have accepted status")
        if self.status == "accepted" and (not self.accepted or self.reason != "accepted"):
            raise ValueError("accepted status requires accepted reason")
        if self.status != "accepted" and self.reason == "accepted":
            raise ValueError("accepted reason requires accepted status")
        return self

    @field_serializer("authority")
    def _serialize_diagnostic_authority(self, _value: object) -> dict[str, bool]:
        return Gate5B4C3ShadowGenerationAuthorityFlags().model_dump(
            by_alias=True,
            mode="json",
        )


def build_gate5b4c3_shadow_generation_diagnostic(
    request: Gate5B4C3ShadowGenerationRequest,
    *,
    config: Gate5B4C3ShadowGenerationConfig | None = None,
    latency_ms: int = 0,
) -> Gate5B4C3ShadowGenerationDiagnostic:
    gate_config = config or Gate5B4C3ShadowGenerationConfig()
    accepted, status, reason = _gate_decision(request, gate_config)
    return Gate5B4C3ShadowGenerationDiagnostic(
        accepted=accepted,
        status=status,
        reason=reason,
        shadowGenerationId=request.shadow_generation_id,
        latencyMs=max(latency_ms, 0),
        provider=request.model_routing.provider_label,
        model=request.model_routing.model_label,
        routingSource=request.model_routing.routing_source,
        fallbackReason=request.model_routing.fallback_reason,
    )


def _gate_decision(
    request: Gate5B4C3ShadowGenerationRequest,
    config: Gate5B4C3ShadowGenerationConfig,
) -> tuple[bool, Gate5B4C3ShadowGenerationStatus, Gate5B4C3ShadowGenerationReason]:
    if not config.enabled:
        return False, "skipped", "disabled"
    if config.selected_bot_digest is None or config.environment is None:
        return False, "dropped", "selected_scope_missing"
    if config.trusted_owner_user_id_digest is None:
        return False, "dropped", "trusted_org_missing"
    if config.kill_switch_active:
        return False, "skipped", "kill_switch_active"
    if not config.cap_state_initialized:
        return False, "dropped", "cap_state_uninitialized"
    if config.generation_budget_exhausted:
        return False, "skipped", "budget_exhausted"
    if (
        not config.provider_project_spend_controls_verified
        and not config.cost_owner_waiver
    ):
        return False, "dropped", "budget_exhausted"
    if (
        request.selection.bot_id_digest != config.selected_bot_digest
        or request.selection.owner_user_id_digest != config.trusted_owner_user_id_digest
        or request.selection.environment != config.environment
    ):
        return False, "dropped", "selected_scope_mismatch"
    if not _routing_source_allowed(request, config):
        return False, "dropped", "model_routing_source_not_allowed"
    if (
        not config.allowed_provider_labels
        or request.model_routing.provider_label not in config.allowed_provider_labels
    ):
        return False, "dropped", "model_routing_not_allowlisted"
    if (
        not config.allowed_model_labels
        or request.model_routing.model_label not in config.allowed_model_labels
    ):
        return False, "dropped", "model_routing_not_allowlisted"
    model_route = f"{request.model_routing.provider_label}:{request.model_routing.model_label}"
    if not config.allowed_model_routes or model_route not in config.allowed_model_routes:
        return False, "dropped", "model_routing_not_allowlisted"
    if (
        request.model_routing.shadow_credential_ref is None
        or not config.allowed_shadow_credential_refs
        or request.model_routing.shadow_credential_ref not in config.allowed_shadow_credential_refs
    ):
        return False, "dropped", "shadow_credential_ref_not_allowlisted"
    if (
        config.provider_credential_binding_required
        or bool(config.provider_credential_bindings)
    ) and not _has_matching_provider_credential_binding(request, config):
        return False, "dropped", "provider_credential_binding_missing"
    if _request_exceeds_approved_budgets(request.budgets, config.approved_budgets):
        return False, "dropped", "budget_exhausted"
    if config.in_flight_generation_runs >= request.budgets.max_concurrent_generation_runs:
        return False, "dropped", "budget_exhausted"
    if config.pending_generation_runs >= request.budgets.max_pending_generation_runs:
        return False, "dropped", "budget_exhausted"
    if config.daily_generation_runs_used >= request.budgets.max_daily_generation_runs:
        return False, "dropped", "budget_exhausted"
    if config.daily_generation_cost_usd_used + request.budgets.max_cost_usd > (
        request.budgets.max_daily_generation_cost_usd
    ):
        return False, "dropped", "budget_exhausted"
    return True, "accepted", "accepted"


def _routing_source_allowed(
    request: Gate5B4C3ShadowGenerationRequest,
    config: Gate5B4C3ShadowGenerationConfig,
) -> bool:
    routing = request.model_routing
    if routing.routing_source == "per_turn_injected":
        return True
    if routing.routing_source != "bot_config_fallback":
        return False
    return (
        config.bot_config_fallback_allowed
        and config.bot_config_fallback_approval_digest is not None
        and routing.bot_config_model_digest == config.bot_config_fallback_approval_digest
        and routing.fallback_approved is True
    )


def _has_matching_provider_credential_binding(
    request: Gate5B4C3ShadowGenerationRequest,
    config: Gate5B4C3ShadowGenerationConfig,
) -> bool:
    credential_ref = request.model_routing.shadow_credential_ref
    if credential_ref is None:
        return False
    for binding in config.provider_credential_bindings:
        if (
            binding.provider_label == request.model_routing.provider_label
            and binding.credential_ref == credential_ref
            and set(binding.required_env_vars).issubset(set(binding.present_env_vars))
        ):
            return True
    return False


def _request_exceeds_approved_budgets(
    requested: Gate5B4C3ShadowGenerationBudgets,
    approved: Gate5B4C3ShadowGenerationBudgets,
) -> bool:
    return (
        requested.python_runner_timeout_ms > approved.python_runner_timeout_ms
        or requested.max_sanitized_input_bytes > approved.max_sanitized_input_bytes
        or requested.max_estimated_input_tokens > approved.max_estimated_input_tokens
        or requested.max_output_tokens > approved.max_output_tokens
        or requested.max_total_estimated_tokens > approved.max_total_estimated_tokens
        or requested.max_diagnostic_output_preview_bytes
        > approved.max_diagnostic_output_preview_bytes
        or requested.max_diagnostic_artifact_bytes > approved.max_diagnostic_artifact_bytes
        or requested.max_concurrent_generation_runs > approved.max_concurrent_generation_runs
        or requested.max_pending_generation_runs > approved.max_pending_generation_runs
        or requested.max_daily_generation_runs > approved.max_daily_generation_runs
        or requested.max_cost_usd > approved.max_cost_usd
        or requested.max_daily_generation_cost_usd > approved.max_daily_generation_cost_usd
        or requested.retry_policy != approved.retry_policy
    )


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


def _validate_model_route_component(value: str, message: str) -> None:
    if not isinstance(value, str) or not _MODEL_ROUTE_COMPONENT_RE.match(value):
        raise ValueError(message)


def _is_safe_label(value: object) -> bool:
    return isinstance(value, str) and bool(_SAFE_LABEL_RE.match(value))


def _reject_unsafe_value(value: object) -> None:
    if isinstance(value, str):
        if _UNSAFE_TEXT_RE.search(value):
            raise ValueError("shadow generation payload contains forbidden private material")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            _reject_unsafe_key(key)
            _reject_unsafe_value(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _reject_unsafe_value(child)


def _reject_unsafe_key(value: object) -> None:
    if not isinstance(value, str):
        _reject_unsafe_value(value)
        return
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    if normalized in _UNSAFE_FIELD_NAMES or _UNSAFE_TEXT_RE.search(value):
        raise ValueError("shadow generation payload contains forbidden private material")


__all__ = [
    "Gate5B4C3ModelRoutingSource",
    "Gate5B4C3ShadowGenerationAttachmentMetadata",
    "Gate5B4C3ShadowGenerationAuthorityFlags",
    "Gate5B4C3ShadowGenerationBudgets",
    "Gate5B4C3ShadowGenerationComparison",
    "Gate5B4C3ShadowGenerationConfig",
    "Gate5B4C3ShadowGenerationDiagnostic",
    "Gate5B4C3ShadowGenerationModelRouting",
    "Gate5B4C3ShadowGenerationOutputMetadata",
    "Gate5B4C3ShadowGenerationPolicy",
    "Gate5B4C3ShadowGenerationProviderCredentialBinding",
    "Gate5B4C3ShadowGenerationReason",
    "Gate5B4C3ShadowGenerationRecipeProfile",
    "Gate5B4C3ShadowGenerationRedaction",
    "Gate5B4C3ShadowGenerationRequest",
    "Gate5B4C3ShadowGenerationSelection",
    "Gate5B4C3ShadowGenerationStatus",
    "Gate5B4C3ShadowGenerationTurn",
    "build_gate5b4c3_shadow_generation_diagnostic",
]
