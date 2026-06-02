from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from openmagi_core_agent.shadow.gate4c0_shadow_config import (
    Gate4C0ModelRoutingMetadata,
    Gate4C0ModelSelectionSource,
)


Gate5BCanaryMode: TypeAlias = Literal[
    "disabled",
    "shadow_only",
    "candidate_user_visible",
    "active_user_visible",
]
Gate5BStatus: TypeAlias = Literal[
    "disabled",
    "skipped",
    "pending_approval",
    "blocked",
]
Gate5BReason: TypeAlias = Literal[
    "canary_disabled",
    "kill_switch_enabled",
    "runtime_routing_not_authorized",
    "missing_bot_allowlist",
    "missing_org_allowlist",
    "missing_environment_allowlist",
    "bot_not_allowlisted",
    "org_not_allowlisted",
    "environment_not_allowlisted",
    "unsafe_metadata",
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
_APPROVAL_MARKER = "future-approval:gate5b-runtime-routing"
_ALLOWED_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})
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
    r"\bclawy\.pro\b\S*"
    r")",
    re.IGNORECASE,
)


class _Gate5BModel(BaseModel):
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


class Gate5BNoMemoryRoutingCanaryAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    user_visible_output_attached: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAttached",
    )
    runtime_selector_active: Literal[False] = Field(
        default=False,
        alias="runtimeSelectorActive",
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
    canary_routed: Literal[False] = Field(default=False, alias="canaryRouted")

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
        "user_visible_output_attached",
        "runtime_selector_active",
        "production_transcript_written",
        "production_sse_written",
        "db_written",
        "channel_delivered",
        "workspace_mutated",
        "memory_written",
        "memory_provider_called",
        "toolhost_dispatched",
        "live_tools_executed",
        "evidence_block_enabled",
        "child_execution_attached",
        "mission_scheduler_attached",
        "billing_auth_mutated",
        "model_routing_mutated",
        "canary_routed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class Gate5BNoMemoryRoutingCanaryPolicy(_Gate5BModel):
    no_memory_required: Literal[True] = Field(default=True, alias="noMemoryRequired")
    tools_disabled_required: Literal[True] = Field(
        default=True,
        alias="toolsDisabledRequired",
    )
    memory_disabled_required: Literal[True] = Field(
        default=True,
        alias="memoryDisabledRequired",
    )
    workspace_mutation_disabled: Literal[True] = Field(
        default=True,
        alias="workspaceMutationDisabled",
    )
    child_execution_disabled: Literal[True] = Field(
        default=True,
        alias="childExecutionDisabled",
    )
    evidence_block_disabled: Literal[True] = Field(
        default=True,
        alias="evidenceBlockDisabled",
    )
    python_response_adoption_disabled: Literal[True] = Field(
        default=True,
        alias="pythonResponseAdoptionDisabled",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{field.alias or name: True for name, field in cls.model_fields.items()})

    @model_validator(mode="before")
    @classmethod
    def _force_true_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return {field.alias or name: True for name, field in cls.model_fields.items()}


class Gate5BNoMemoryRoutingCanaryRuntimeSelector(_Gate5BModel):
    desired_state: Literal["inactive"] = Field(default="inactive", alias="desiredState")
    runtime_selector_active: Literal[False] = Field(
        default=False,
        alias="runtimeSelectorActive",
    )
    active_routing_percentage: Literal[0] = Field(
        default=0,
        alias="activeRoutingPercentage",
    )
    planned_routing_percentage: int = Field(
        default=0,
        ge=0,
        le=100,
        alias="plannedRoutingPercentage",
    )
    planned_fixed_target_digests: tuple[str, ...] = Field(
        default=(),
        alias="plannedFixedTargetDigests",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_inactive_runtime_selector(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["desiredState"] = "inactive"
        data["runtimeSelectorActive"] = False
        data["activeRoutingPercentage"] = 0
        data.pop("desired_state", None)
        data.pop("runtime_selector_active", None)
        data.pop("active_routing_percentage", None)
        return data

    @model_validator(mode="after")
    def _validate_planned_targets(self) -> Self:
        for value in self.planned_fixed_target_digests:
            _validate_digest(value, "Gate 5B planned target digests must be sha256 digests")
        return self


class Gate5BNoMemoryRoutingCanaryApprovalChecklist(_Gate5BModel):
    product_approved: bool = Field(default=False, alias="productApproved")
    security_approved: bool = Field(default=False, alias="securityApproved")
    infra_approved: bool = Field(default=False, alias="infraApproved")
    support_approved: bool = Field(default=False, alias="supportApproved")
    runtime_owner_approved: bool = Field(default=False, alias="runtimeOwnerApproved")
    rollback_owner_named: bool = Field(default=False, alias="rollbackOwnerNamed")
    transcript_sse_contract_approved: bool = Field(
        default=False,
        alias="transcriptSseContractApproved",
    )


class Gate5BNoMemoryRoutingCanaryObservabilityChecklist(_Gate5BModel):
    numeric_thresholds_defined: bool = Field(
        default=False,
        alias="numericThresholdsDefined",
    )
    alert_owner_named: bool = Field(default=False, alias="alertOwnerNamed")
    rollback_rto_defined: bool = Field(default=False, alias="rollbackRtoDefined")
    kill_switch_verified: bool = Field(default=False, alias="killSwitchVerified")
    redaction_miss_target_zero: Literal[True] = Field(
        default=True,
        alias="redactionMissTargetZero",
    )


class Gate5BNoMemoryRoutingCanaryConfig(_Gate5BModel):
    enabled: bool = False
    canary_mode: Gate5BCanaryMode = Field(default="disabled", alias="canaryMode")
    kill_switch_enabled: bool = Field(default=True, alias="killSwitchEnabled")
    selected_bot_digest: str = Field(alias="selectedBotDigest")
    selected_org_digest: str = Field(alias="selectedOrgDigest")
    environment: str
    bot_allowlist_digests: tuple[str, ...] = Field(default=(), alias="botAllowlistDigests")
    org_allowlist_digests: tuple[str, ...] = Field(default=(), alias="orgAllowlistDigests")
    environment_allowlist: tuple[str, ...] = Field(default=(), alias="environmentAllowlist")
    model_routing: Gate4C0ModelRoutingMetadata = Field(alias="modelRouting")
    runtime_selector: Gate5BNoMemoryRoutingCanaryRuntimeSelector = Field(
        default_factory=Gate5BNoMemoryRoutingCanaryRuntimeSelector,
        alias="runtimeSelector",
    )
    policy: Gate5BNoMemoryRoutingCanaryPolicy = Field(
        default_factory=Gate5BNoMemoryRoutingCanaryPolicy,
    )
    approval_checklist: Gate5BNoMemoryRoutingCanaryApprovalChecklist = Field(
        default_factory=Gate5BNoMemoryRoutingCanaryApprovalChecklist,
        alias="approvalChecklist",
    )
    observability_checklist: Gate5BNoMemoryRoutingCanaryObservabilityChecklist = Field(
        default_factory=Gate5BNoMemoryRoutingCanaryObservabilityChecklist,
        alias="observabilityChecklist",
    )
    rollback_reason: str | None = Field(default=None, alias="rollbackReason")
    future_approval_marker: str | None = Field(default=None, alias="futureApprovalMarker")

    @model_validator(mode="after")
    def _validate_metadata_and_approval(self) -> Self:
        _validate_digest(
            self.selected_bot_digest,
            "Gate 5B selected IDs must be sha256 digests",
        )
        _validate_digest(
            self.selected_org_digest,
            "Gate 5B selected IDs must be sha256 digests",
        )
        for value in (*self.bot_allowlist_digests, *self.org_allowlist_digests):
            _validate_digest(value, "Gate 5B allowlist IDs must be sha256 digests")
        for value in (self.environment, *self.environment_allowlist):
            _validate_environment(value)
        if self.rollback_reason is not None:
            _reject_unsafe_text(self.rollback_reason)
        if (
            self.canary_mode == "active_user_visible"
            and self.future_approval_marker != _APPROVAL_MARKER
        ):
            raise ValueError("Gate 5B active user-visible mode requires future approval")
        return self


class Gate5BNoMemoryRoutingCanaryStatus(_Gate5BModel):
    schema_version: Literal["gate5b.noMemoryRoutingCanaryStatus.v1"] = Field(
        default="gate5b.noMemoryRoutingCanaryStatus.v1",
        alias="schemaVersion",
    )
    canary_mode: Gate5BCanaryMode = Field(alias="canaryMode")
    status: Gate5BStatus
    reason: Gate5BReason
    runtime_selector: Gate5BNoMemoryRoutingCanaryRuntimeSelector = Field(
        alias="runtimeSelector",
    )
    policy: Gate5BNoMemoryRoutingCanaryPolicy
    approval_checklist: Gate5BNoMemoryRoutingCanaryApprovalChecklist = Field(
        alias="approvalChecklist",
    )
    observability_checklist: Gate5BNoMemoryRoutingCanaryObservabilityChecklist = Field(
        alias="observabilityChecklist",
    )
    model_selection_source: Gate4C0ModelSelectionSource = Field(
        alias="modelSelectionSource",
    )
    selected_provider: str = Field(alias="selectedProvider")
    selected_model: str = Field(alias="selectedModel")
    authority_flags: Gate5BNoMemoryRoutingCanaryAuthorityFlags = Field(
        default_factory=Gate5BNoMemoryRoutingCanaryAuthorityFlags,
        alias="authorityFlags",
    )

    @field_serializer("authority_flags")
    def _serialize_authority_flags(self, _value: object) -> dict[str, bool]:
        return Gate5BNoMemoryRoutingCanaryAuthorityFlags().model_dump(
            by_alias=True,
            mode="json",
        )


def resolve_gate5b_no_memory_routing_canary_status(
    config: Gate5BNoMemoryRoutingCanaryConfig,
) -> Gate5BNoMemoryRoutingCanaryStatus:
    if not config.enabled or config.canary_mode == "disabled":
        return _status(config, "disabled", "canary_disabled")
    if config.kill_switch_enabled:
        return _status(config, "skipped", "kill_switch_enabled")
    if not config.bot_allowlist_digests:
        return _status(config, "skipped", "missing_bot_allowlist")
    if not config.org_allowlist_digests:
        return _status(config, "skipped", "missing_org_allowlist")
    if not config.environment_allowlist:
        return _status(config, "skipped", "missing_environment_allowlist")
    if config.selected_bot_digest not in config.bot_allowlist_digests:
        return _status(config, "skipped", "bot_not_allowlisted")
    if config.selected_org_digest not in config.org_allowlist_digests:
        return _status(config, "skipped", "org_not_allowlisted")
    if config.environment not in config.environment_allowlist:
        return _status(config, "skipped", "environment_not_allowlisted")
    return _status(config, "pending_approval", "runtime_routing_not_authorized")


def _status(
    config: Gate5BNoMemoryRoutingCanaryConfig,
    status: Gate5BStatus,
    reason: Gate5BReason,
) -> Gate5BNoMemoryRoutingCanaryStatus:
    return Gate5BNoMemoryRoutingCanaryStatus(
        canaryMode=config.canary_mode,
        status=status,
        reason=reason,
        runtimeSelector=config.runtime_selector,
        policy=config.policy,
        approvalChecklist=config.approval_checklist,
        observabilityChecklist=config.observability_checklist,
        modelSelectionSource=config.model_routing.model_selection_source,
        selectedProvider=config.model_routing.provider,
        selectedModel=config.model_routing.model,
    )


def _validate_digest(value: str, message: str) -> None:
    if not _DIGEST_RE.match(value):
        raise ValueError(message)


def _reject_unsafe_text(value: str) -> None:
    if _UNSAFE_TEXT_RE.search(value):
        raise ValueError("Gate 5B metadata must be sanitized and public-safe")


def _validate_environment(value: str) -> None:
    _reject_unsafe_text(value)
    if value.strip() != value or not value:
        raise ValueError("Gate 5B environment labels must be non-empty and trimmed")
    if value not in _ALLOWED_ENVIRONMENTS:
        raise ValueError("Gate 5B environment label is not recognized")


__all__ = [
    "Gate5BNoMemoryRoutingCanaryApprovalChecklist",
    "Gate5BNoMemoryRoutingCanaryAuthorityFlags",
    "Gate5BNoMemoryRoutingCanaryConfig",
    "Gate5BNoMemoryRoutingCanaryObservabilityChecklist",
    "Gate5BNoMemoryRoutingCanaryPolicy",
    "Gate5BNoMemoryRoutingCanaryRuntimeSelector",
    "Gate5BNoMemoryRoutingCanaryStatus",
    "Gate5BCanaryMode",
    "Gate5BReason",
    "Gate5BStatus",
    "resolve_gate5b_no_memory_routing_canary_status",
]
