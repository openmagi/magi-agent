from __future__ import annotations

from collections.abc import Mapping
import math
import re
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


Gate4C0DecisionStatus: TypeAlias = Literal["accepted", "skipped", "dropped"]
Gate4C0ModelSelectionSource: TypeAlias = Literal[
    "per_turn_injected",
    "router_resolved",
    "bot_config_fallback",
    "default_fallback",
    "invalid_or_missing",
]
Gate4C0DecisionReason: TypeAlias = Literal[
    "ready_for_gate4c1_runner_approval",
    "shadow_disabled",
    "kill_switch_enabled",
    "missing_bot_allowlist",
    "missing_org_allowlist",
    "missing_environment_allowlist",
    "bot_not_allowlisted",
    "org_not_allowlisted",
    "environment_not_allowlisted",
    "redaction_not_verified",
    "input_too_large",
    "event_count_too_large",
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
_UNSAFE_TEXT_RE = re.compile(
    r"(?:"
    r"Authorization:\s*Bearer\s+\S+|"
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
    r"\bclawy\.pro\b\S*"
    r")",
    re.IGNORECASE,
)


class _Gate4C0Model(BaseModel):
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


class Gate4C0AuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    live_model_prompt_constructed: Literal[False] = Field(
        default=False,
        alias="liveModelPromptConstructed",
    )
    user_visible_output_attached: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAttached",
    )
    production_transcript_written: Literal[False] = Field(
        default=False,
        alias="productionTranscriptWritten",
    )
    production_sse_written: Literal[False] = Field(
        default=False,
        alias="productionSseWritten",
    )
    db_written: Literal[False] = Field(default=False, alias="dbWritten")
    channel_delivered: Literal[False] = Field(default=False, alias="channelDelivered")
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    memory_written: Literal[False] = Field(default=False, alias="memoryWritten")
    memory_provider_called: Literal[False] = Field(
        default=False,
        alias="memoryProviderCalled",
    )
    toolhost_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    live_tools_executed: Literal[False] = Field(default=False, alias="liveToolsExecuted")
    canary_routed: Literal[False] = Field(default=False, alias="canaryRouted")
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    production_queue_enqueued: Literal[False] = Field(
        default=False,
        alias="productionQueueEnqueued",
    )
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    evidence_block_enabled: Literal[False] = Field(
        default=False,
        alias="evidenceBlockEnabled",
    )
    child_execution_attached: Literal[False] = Field(
        default=False,
        alias="childExecutionAttached",
    )
    mission_scheduler_attached: Literal[False] = Field(
        default=False,
        alias="missionSchedulerAttached",
    )
    billing_auth_mutated: Literal[False] = Field(default=False, alias="billingAuthMutated")
    model_routing_mutated: Literal[False] = Field(
        default=False,
        alias="modelRoutingMutated",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{key: False for key in cls.model_fields})

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
        "adk_runner_invoked",
        "model_called",
        "live_model_prompt_constructed",
        "user_visible_output_attached",
        "production_transcript_written",
        "production_sse_written",
        "db_written",
        "channel_delivered",
        "workspace_mutated",
        "memory_written",
        "memory_provider_called",
        "toolhost_dispatched",
        "live_tools_executed",
        "canary_routed",
        "production_storage_written",
        "production_queue_enqueued",
        "telegram_attached",
        "evidence_block_enabled",
        "child_execution_attached",
        "mission_scheduler_attached",
        "billing_auth_mutated",
        "model_routing_mutated",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class Gate4C0AllowlistMetadata(_Gate4C0Model):
    selected_bot_digest: str = Field(alias="selectedBotDigest")
    selected_org_digest: str = Field(alias="selectedOrgDigest")
    environment: str
    bot_allowlist_digests: tuple[str, ...] = Field(alias="botAllowlistDigests")
    org_allowlist_digests: tuple[str, ...] = Field(alias="orgAllowlistDigests")
    environment_allowlist: tuple[str, ...] = Field(alias="environmentAllowlist")

    @field_validator("selected_bot_digest", "selected_org_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        _reject_unsafe_text(value)
        if not _DIGEST_RE.match(value):
            raise ValueError("Gate 4C-0 selected IDs must be sha256 digests")
        return value

    @field_validator("bot_allowlist_digests", "org_allowlist_digests")
    @classmethod
    def _validate_digest_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _DIGEST_RE.match(item):
                raise ValueError("Gate 4C-0 allowlist IDs must be sha256 digests")
        return value

    @field_validator("environment", "environment_allowlist")
    @classmethod
    def _validate_environment(cls, value: object) -> object:
        if isinstance(value, str):
            _reject_unsafe_text(value)
        elif isinstance(value, tuple):
            for item in value:
                _reject_unsafe_text(item)
        return value


class Gate4C0ModelRoutingMetadata(_Gate4C0Model):
    provider: str
    model: str
    model_profile: str = Field(alias="modelProfile")
    routing_profile_id: str = Field(alias="routingProfileId")
    credential_ref: str = Field(alias="credentialRef")
    model_selection_source: Gate4C0ModelSelectionSource = Field(
        default="invalid_or_missing",
        alias="modelSelectionSource",
    )
    per_turn_provider: str | None = Field(default=None, alias="perTurnProvider")
    per_turn_model: str | None = Field(default=None, alias="perTurnModel")
    router_provider: str | None = Field(default=None, alias="routerProvider")
    router_model: str | None = Field(default=None, alias="routerModel")
    bot_config_provider: str | None = Field(default=None, alias="botConfigProvider")
    bot_config_model: str | None = Field(default=None, alias="botConfigModel")
    default_provider: str | None = Field(default=None, alias="defaultProvider")
    default_model: str | None = Field(default=None, alias="defaultModel")
    production_equivalent: Literal[True] = Field(
        default=True,
        alias="productionEquivalent",
    )

    @field_validator(
        "provider",
        "model",
        "model_profile",
        "routing_profile_id",
        "credential_ref",
        "per_turn_provider",
        "per_turn_model",
        "router_provider",
        "router_model",
        "bot_config_provider",
        "bot_config_model",
        "default_provider",
        "default_model",
    )
    @classmethod
    def _validate_safe_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        _reject_unsafe_text(value)
        return value

    @model_validator(mode="after")
    def _validate_selection_source_consistency(self) -> Self:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("Gate 4C-0 provider/model must be non-empty")
        provider_invalid = self.provider == "invalid_or_missing"
        model_invalid = self.model == "invalid_or_missing"
        invalid_pair = provider_invalid and model_invalid
        if provider_invalid != model_invalid:
            raise ValueError("Gate 4C-0 provider/model invalid sentinel must be paired")
        if self.model_selection_source == "invalid_or_missing" and not invalid_pair:
            raise ValueError("Gate 4C-0 invalid/missing source requires invalid/missing model")
        if self.model_selection_source != "invalid_or_missing" and invalid_pair:
            raise ValueError("Gate 4C-0 concrete selection source requires concrete model")
        return self


class Gate4C0RecipeProfileMetadata(_Gate4C0Model):
    recipe_snapshot_id: str = Field(alias="recipeSnapshotId")
    profile_id: str = Field(alias="profileId")
    profile_snapshot_digest: str = Field(alias="profileSnapshotDigest")
    selected_pack_ids: tuple[str, ...] = Field(alias="selectedPackIds")

    @field_validator("recipe_snapshot_id", "profile_id")
    @classmethod
    def _validate_safe_text(cls, value: str) -> str:
        _reject_unsafe_text(value)
        return value

    @field_validator("profile_snapshot_digest")
    @classmethod
    def _validate_profile_digest(cls, value: str) -> str:
        if not _DIGEST_RE.match(value):
            raise ValueError("Gate 4C-0 profile snapshot must be a sha256 digest")
        return value

    @field_validator("selected_pack_ids")
    @classmethod
    def _validate_pack_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _reject_unsafe_text(item)
        return value


class Gate4C0InputEnvelopeMetadata(_Gate4C0Model):
    source: Literal[
        "gate4b_local_shadow_handoff",
        "gate4_isolated_shadow_bundle",
    ]
    bundle_id_digest: str = Field(alias="bundleIdDigest")
    session_id_digest: str = Field(alias="sessionIdDigest")
    turn_id: str = Field(alias="turnId")
    schema_version: str = Field(alias="schemaVersion")
    redaction_verified: bool = Field(alias="redactionVerified")
    input_size_bytes: int = Field(ge=0, alias="inputSizeBytes")
    event_count: int = Field(ge=0, alias="eventCount")

    @field_validator("bundle_id_digest", "session_id_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.match(value):
            raise ValueError("Gate 4C-0 input identity must be a sha256 digest")
        return value

    @field_validator("turn_id", "schema_version")
    @classmethod
    def _validate_safe_text(cls, value: str) -> str:
        _reject_unsafe_text(value)
        return value


class Gate4C0RedactionPolicy(_Gate4C0Model):
    redaction_required: Literal[True] = Field(default=True, alias="redactionRequired")
    max_input_bytes: int = Field(ge=1, alias="maxInputBytes")
    max_event_count: int = Field(ge=1, alias="maxEventCount")
    unsafe_input_action: Literal["drop"] = Field(default="drop", alias="unsafeInputAction")


class Gate4C0ToolPolicy(_Gate4C0Model):
    mode: Literal["disabled", "stubbed"] = "disabled"
    live_toolhost_dispatch_attached: Literal[False] = Field(
        default=False,
        alias="toolhostDispatchAttached",
    )
    function_tools_attached: Literal[False] = Field(
        default=False,
        alias="functionToolsAttached",
    )
    long_running_tools_attached: Literal[False] = Field(
        default=False,
        alias="longRunningToolsAttached",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false_authority(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["toolhostDispatchAttached"] = False
        data["functionToolsAttached"] = False
        data["longRunningToolsAttached"] = False
        data.pop("live_toolhost_dispatch_attached", None)
        data.pop("function_tools_attached", None)
        data.pop("long_running_tools_attached", None)
        return data


class Gate4C0MemoryPolicy(_Gate4C0Model):
    mode: Literal["disabled", "read_only"] = "disabled"
    memory_writes_enabled: Literal[False] = Field(default=False, alias="memoryWritesEnabled")
    prompt_injection_enabled: Literal[False] = Field(
        default=False,
        alias="promptInjectionEnabled",
    )
    provider_calls_enabled: Literal[False] = Field(default=False, alias="providerCallsEnabled")

    @model_validator(mode="before")
    @classmethod
    def _force_false_authority(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["memoryWritesEnabled"] = False
        data["promptInjectionEnabled"] = False
        data["providerCallsEnabled"] = False
        data.pop("memory_writes_enabled", None)
        data.pop("prompt_injection_enabled", None)
        data.pop("provider_calls_enabled", None)
        return data


class Gate4C0OutputIsolationPolicy(_Gate4C0Model):
    output_mode: Literal["local_diagnostic_artifacts_only"] = Field(
        default="local_diagnostic_artifacts_only",
        alias="outputMode",
    )
    user_visible_output_attached: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAttached",
    )
    production_transcript_written: Literal[False] = Field(
        default=False,
        alias="productionTranscriptWritten",
    )
    production_sse_written: Literal[False] = Field(
        default=False,
        alias="productionSseWritten",
    )
    db_written: Literal[False] = Field(default=False, alias="dbWritten")
    channel_delivered: Literal[False] = Field(default=False, alias="channelDelivered")
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    memory_written: Literal[False] = Field(default=False, alias="memoryWritten")
    canary_routed: Literal[False] = Field(default=False, alias="canaryRouted")

    @model_validator(mode="before")
    @classmethod
    def _force_false_authority(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        for name, field in cls.model_fields.items():
            if name == "output_mode":
                continue
            data[field.alias or name] = False
            data.pop(name, None)
        return data


class Gate4C0BudgetPolicy(_Gate4C0Model):
    max_latency_ms: int = Field(ge=0, alias="maxLatencyMs")
    max_queue_depth: int = Field(ge=0, alias="maxQueueDepth")
    max_daily_shadow_runs: int = Field(ge=0, alias="maxDailyShadowRuns")
    max_cost_usd: float = Field(ge=0, alias="maxCostUsd")
    fail_open: Literal[True] = Field(default=True, alias="failOpen")

    @field_validator("max_cost_usd")
    @classmethod
    def _validate_finite_cost(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Gate 4C-0 cost limit must be finite")
        return value


class Gate4C0KillSwitchMetadata(_Gate4C0Model):
    kill_switch_enabled: bool = Field(default=True, alias="killSwitchEnabled")
    fail_open_on_skip: Literal[True] = Field(default=True, alias="failOpenOnSkip")


class Gate4C0ShadowConfig(_Gate4C0Model):
    schema_version: Literal["gate4c0.productionEquivalentShadowConfig.v1"] = Field(
        default="gate4c0.productionEquivalentShadowConfig.v1",
        alias="schemaVersion",
    )
    enabled: bool = False
    production_equivalent_inputs: Literal[True] = Field(
        default=True,
        alias="productionEquivalentInputs",
    )
    allowlist: Gate4C0AllowlistMetadata
    model_routing: Gate4C0ModelRoutingMetadata = Field(alias="modelRouting")
    recipe_profile: Gate4C0RecipeProfileMetadata = Field(alias="recipeProfile")
    input_envelope: Gate4C0InputEnvelopeMetadata = Field(alias="inputEnvelope")
    redaction_policy: Gate4C0RedactionPolicy = Field(alias="redactionPolicy")
    tool_policy: Gate4C0ToolPolicy = Field(alias="toolPolicy")
    memory_policy: Gate4C0MemoryPolicy = Field(alias="memoryPolicy")
    output_isolation: Gate4C0OutputIsolationPolicy = Field(alias="outputIsolation")
    budget: Gate4C0BudgetPolicy
    kill_switch: Gate4C0KillSwitchMetadata = Field(alias="killSwitch")
    attachment_flags: Gate4C0AuthorityFlags = Field(
        default_factory=Gate4C0AuthorityFlags,
        alias="attachmentFlags",
    )

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(self, _value: object) -> dict[str, bool]:
        return Gate4C0AuthorityFlags().model_dump(by_alias=True, mode="json")


class Gate4C0ShadowDecision(_Gate4C0Model):
    status: Gate4C0DecisionStatus
    reason: Gate4C0DecisionReason
    production_equivalent_inputs: bool = Field(alias="productionEquivalentInputs")
    attachment_flags: Gate4C0AuthorityFlags = Field(
        default_factory=Gate4C0AuthorityFlags,
        alias="attachmentFlags",
    )


def resolve_gate4c0_shadow_config(config: Gate4C0ShadowConfig) -> Gate4C0ShadowDecision:
    if not config.enabled:
        return _decision("skipped", "shadow_disabled")
    if config.kill_switch.kill_switch_enabled:
        return _decision("skipped", "kill_switch_enabled")

    allowlist = config.allowlist
    if not allowlist.bot_allowlist_digests:
        return _decision("skipped", "missing_bot_allowlist")
    if not allowlist.org_allowlist_digests:
        return _decision("skipped", "missing_org_allowlist")
    if not allowlist.environment_allowlist:
        return _decision("skipped", "missing_environment_allowlist")
    if allowlist.selected_bot_digest not in allowlist.bot_allowlist_digests:
        return _decision("skipped", "bot_not_allowlisted")
    if allowlist.selected_org_digest not in allowlist.org_allowlist_digests:
        return _decision("skipped", "org_not_allowlisted")
    if allowlist.environment not in allowlist.environment_allowlist:
        return _decision("skipped", "environment_not_allowlisted")

    if not config.input_envelope.redaction_verified:
        return _decision("dropped", "redaction_not_verified")
    if config.input_envelope.input_size_bytes > config.redaction_policy.max_input_bytes:
        return _decision("dropped", "input_too_large")
    if config.input_envelope.event_count > config.redaction_policy.max_event_count:
        return _decision("dropped", "event_count_too_large")

    return _decision("accepted", "ready_for_gate4c1_runner_approval")


def resolve_gate4c0_turn_scoped_model_routing(
    *,
    perTurnProvider: str | None = None,
    perTurnModel: str | None = None,
    routerProvider: str | None = None,
    routerModel: str | None = None,
    botConfigProvider: str | None = None,
    botConfigModel: str | None = None,
    defaultProvider: str | None = None,
    defaultModel: str | None = None,
    modelProfile: str,
    routingProfileId: str,
    credentialRef: str,
) -> Gate4C0ModelRoutingMetadata:
    candidates: tuple[
        tuple[Gate4C0ModelSelectionSource, str | None, str | None],
        ...,
    ] = (
        ("per_turn_injected", perTurnProvider, perTurnModel),
        ("router_resolved", routerProvider, routerModel),
        ("bot_config_fallback", botConfigProvider, botConfigModel),
        ("default_fallback", defaultProvider, defaultModel),
    )
    for source, provider, model in candidates:
        if _is_valid_model_selection(provider, model):
            return Gate4C0ModelRoutingMetadata(
                provider=provider,
                model=model,
                modelProfile=modelProfile,
                routingProfileId=routingProfileId,
                credentialRef=credentialRef,
                modelSelectionSource=source,
                perTurnProvider=_safe_optional_selection(perTurnProvider),
                perTurnModel=_safe_optional_selection(perTurnModel),
                routerProvider=_safe_optional_selection(routerProvider),
                routerModel=_safe_optional_selection(routerModel),
                botConfigProvider=_safe_optional_selection(botConfigProvider),
                botConfigModel=_safe_optional_selection(botConfigModel),
                defaultProvider=_safe_optional_selection(defaultProvider),
                defaultModel=_safe_optional_selection(defaultModel),
            )

    return Gate4C0ModelRoutingMetadata(
        provider="invalid_or_missing",
        model="invalid_or_missing",
        modelProfile=modelProfile,
        routingProfileId=routingProfileId,
        credentialRef=credentialRef,
        modelSelectionSource="invalid_or_missing",
        perTurnProvider=_safe_optional_selection(perTurnProvider),
        perTurnModel=_safe_optional_selection(perTurnModel),
        routerProvider=_safe_optional_selection(routerProvider),
        routerModel=_safe_optional_selection(routerModel),
        botConfigProvider=_safe_optional_selection(botConfigProvider),
        botConfigModel=_safe_optional_selection(botConfigModel),
        defaultProvider=_safe_optional_selection(defaultProvider),
        defaultModel=_safe_optional_selection(defaultModel),
    )


def _decision(
    status: Gate4C0DecisionStatus,
    reason: Gate4C0DecisionReason,
) -> Gate4C0ShadowDecision:
    return Gate4C0ShadowDecision(
        status=status,
        reason=reason,
        productionEquivalentInputs=status == "accepted",
    )


def _reject_unsafe_text(value: str) -> None:
    if _UNSAFE_TEXT_RE.search(value):
        raise ValueError("Gate 4C-0 metadata must be sanitized and public-safe")


def _is_valid_model_selection(provider: str | None, model: str | None) -> bool:
    return _safe_optional_selection(provider) is not None and _safe_optional_selection(model) is not None


def _safe_optional_selection(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        _reject_unsafe_text(cleaned)
    except ValueError:
        return None
    return cleaned


__all__ = [
    "Gate4C0AllowlistMetadata",
    "Gate4C0AuthorityFlags",
    "Gate4C0BudgetPolicy",
    "Gate4C0DecisionReason",
    "Gate4C0DecisionStatus",
    "Gate4C0InputEnvelopeMetadata",
    "Gate4C0KillSwitchMetadata",
    "Gate4C0MemoryPolicy",
    "Gate4C0ModelSelectionSource",
    "Gate4C0ModelRoutingMetadata",
    "Gate4C0OutputIsolationPolicy",
    "Gate4C0RecipeProfileMetadata",
    "Gate4C0RedactionPolicy",
    "Gate4C0ShadowConfig",
    "Gate4C0ShadowDecision",
    "Gate4C0ToolPolicy",
    "resolve_gate4c0_shadow_config",
    "resolve_gate4c0_turn_scoped_model_routing",
]
