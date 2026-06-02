from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.shadow.gate4c0_shadow_config import (
    Gate4C0DecisionReason,
    Gate4C0ShadowConfig,
    resolve_gate4c0_shadow_config,
)


Gate4C1DryRunStatus: TypeAlias = Literal["ready_pending_runner_approval", "skipped"]
Gate4C1DryRunReason: TypeAlias = Literal[
    "gate4c1_requires_runner_implementation_approval",
    "dry_run_disabled",
    "gate4c0_not_accepted",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class Gate4C1DryRunBoundaryFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_imported: Literal[False] = Field(default=False, alias="adkRunnerImported")
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    prompt_constructed: Literal[False] = Field(default=False, alias="promptConstructed")
    toolhost_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    live_tools_executed: Literal[False] = Field(default=False, alias="liveToolsExecuted")
    memory_provider_called: Literal[False] = Field(
        default=False,
        alias="memoryProviderCalled",
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
    canary_routed: Literal[False] = Field(default=False, alias="canaryRouted")

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
        "adk_runner_imported",
        "adk_runner_invoked",
        "model_called",
        "prompt_constructed",
        "toolhost_dispatched",
        "live_tools_executed",
        "memory_provider_called",
        "user_visible_output_attached",
        "production_transcript_written",
        "production_sse_written",
        "db_written",
        "channel_delivered",
        "workspace_mutated",
        "canary_routed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class Gate4C1DryRunBoundaryConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    gate4c0_config: Gate4C0ShadowConfig = Field(alias="gate4c0Config")
    dry_run_only: Literal[True] = Field(default=True, alias="dryRunOnly")
    runner_invocation_approved: Literal[False] = Field(
        default=False,
        alias="runnerInvocationApproved",
    )
    model_calls_approved: Literal[False] = Field(default=False, alias="modelCallsApproved")

    @model_validator(mode="before")
    @classmethod
    def _force_no_implementation_approval(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["dryRunOnly"] = True
        data["runnerInvocationApproved"] = False
        data["modelCallsApproved"] = False
        data.pop("dry_run_only", None)
        data.pop("runner_invocation_approved", None)
        data.pop("model_calls_approved", None)
        return data


class Gate4C1DryRunBoundaryDecision(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["gate4c1.dryRunBoundaryDecision.v1"] = Field(
        default="gate4c1.dryRunBoundaryDecision.v1",
        alias="schemaVersion",
    )
    status: Gate4C1DryRunStatus
    reason: Gate4C1DryRunReason
    gate4c0_reason: Gate4C0DecisionReason | None = Field(
        default=None,
        alias="gate4c0Reason",
    )
    dry_run_only: Literal[True] = Field(default=True, alias="dryRunOnly")
    attachment_flags: Gate4C1DryRunBoundaryFlags = Field(
        default_factory=Gate4C1DryRunBoundaryFlags,
        alias="attachmentFlags",
    )

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(self, _value: object) -> dict[str, bool]:
        return Gate4C1DryRunBoundaryFlags().model_dump(by_alias=True, mode="json")


def evaluate_gate4c1_dry_run_boundary(
    config: Gate4C1DryRunBoundaryConfig,
) -> Gate4C1DryRunBoundaryDecision:
    if not config.enabled:
        return _decision("skipped", "dry_run_disabled")

    gate4c0 = resolve_gate4c0_shadow_config(config.gate4c0_config)
    if gate4c0.status != "accepted":
        return _decision(
            "skipped",
            "gate4c0_not_accepted",
            gate4c0_reason=gate4c0.reason,
        )

    return _decision(
        "ready_pending_runner_approval",
        "gate4c1_requires_runner_implementation_approval",
    )


def _decision(
    status: Gate4C1DryRunStatus,
    reason: Gate4C1DryRunReason,
    *,
    gate4c0_reason: Gate4C0DecisionReason | None = None,
) -> Gate4C1DryRunBoundaryDecision:
    return Gate4C1DryRunBoundaryDecision(
        status=status,
        reason=reason,
        gate4c0Reason=gate4c0_reason,
    )


__all__ = [
    "Gate4C1DryRunBoundaryConfig",
    "Gate4C1DryRunBoundaryDecision",
    "Gate4C1DryRunBoundaryFlags",
    "Gate4C1DryRunReason",
    "Gate4C1DryRunStatus",
    "evaluate_gate4c1_dry_run_boundary",
]
