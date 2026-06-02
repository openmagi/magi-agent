from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openmagi_core_agent.evidence.types import validate_evidence_type_name

PermissionClass = Literal["read", "write", "execute", "net", "meta"]
RuntimeMode = Literal["plan", "act"]
ToolKind = Literal["core", "native", "custom", "external", "skill-compat"]
ToolSourceKind = Literal["builtin", "native-plugin", "custom-plugin", "external", "skill", "runtime"]
SideEffectClass = Literal["none", "local_workspace", "local_process", "external", "local_and_external"]
ParallelSafety = Literal["unsafe", "readonly", "concurrency_safe"]
CostClass = Literal["free", "low", "medium", "high", "metered"]
LatencyClass = Literal["inline", "interactive", "background", "long_running"]
AdkToolType = Literal["FunctionTool", "LongRunningFunctionTool"]

_LONG_RUNNING_LATENCY_CLASSES = {"background", "long_running"}


class ToolSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ToolSourceKind
    package: str


class Budget(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    max_calls_per_turn: int | None = None
    max_parallel: int | None = None
    output_chars: int | None = Field(default=None, alias="outputChars")
    transcript_chars: int | None = Field(default=None, alias="transcriptChars")


class ToolManifest(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    name: str
    description: str
    kind: ToolKind
    source: ToolSource
    permission: PermissionClass
    input_schema: dict[str, object] = Field(alias="inputSchema")
    output_schema: dict[str, object] | None = Field(default=None, alias="outputSchema")
    dangerous: bool = False
    is_concurrency_safe: bool = Field(default=False, alias="isConcurrencySafe")
    mutates_workspace: bool = Field(default=False, alias="mutatesWorkspace")
    available_in_modes: tuple[RuntimeMode, ...] = Field(default=("plan", "act"), alias="availableInModes")
    tags: tuple[str, ...] = ()
    should_defer: bool = Field(default=False, alias="shouldDefer")
    capability_tags: tuple[str, ...] = Field(default=(), alias="capabilityTags")
    side_effect_class: SideEffectClass = Field(default="none", alias="sideEffectClass")
    parallel_safety: ParallelSafety = Field(default="unsafe", alias="parallelSafety")
    emits_evidence_types: tuple[str, ...] = Field(default=(), alias="emitsEvidenceTypes")
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    transient_failure_classes: tuple[str, ...] = Field(default=(), alias="transientFailureClasses")
    cost_class: CostClass = Field(default="free", alias="costClass")
    latency_class: LatencyClass = Field(default="inline", alias="latencyClass")
    deterministic_requirement_types: tuple[str, ...] = Field(
        default=(),
        alias="deterministicRequirementTypes",
    )
    can_satisfy_deterministic_requirement: bool = Field(
        default=False,
        alias="canSatisfyDeterministicRequirement",
    )
    adk_tool_type: AdkToolType = Field(default="FunctionTool", alias="adkToolType")
    timeout_ms: int = Field(alias="timeoutMs")
    budget: Budget = Field(default_factory=Budget)
    plugin_id: str | None = None
    enabled_by_default: bool = False
    opt_out: bool = True

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

    @model_validator(mode="before")
    @classmethod
    def _infer_structured_policy_defaults(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value

        data = dict(value)
        has_side_effect = "sideEffectClass" in data or "side_effect_class" in data
        if not has_side_effect:
            if _truthy(data.get("mutatesWorkspace", data.get("mutates_workspace", False))):
                data["sideEffectClass"] = "local_workspace"
            elif _truthy(data.get("dangerous", False)):
                data["sideEffectClass"] = "local_process"
        return data

    @field_validator("emits_evidence_types")
    @classmethod
    def _validate_emitted_evidence_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_evidence_type_name(item) for item in value)

    @model_validator(mode="after")
    def _validate_structured_policy_metadata(self) -> Self:
        if self.side_effect_class == "none" and (self.dangerous or self.mutates_workspace):
            raise ValueError("sideEffectClass=none cannot be dangerous or mutate workspace")

        if self.mutates_workspace and self.side_effect_class not in {
            "local_workspace",
            "local_and_external",
        }:
            raise ValueError(
                "mutatesWorkspace=True must use sideEffectClass=local_workspace "
                "or local_and_external"
            )

        if (
            self.side_effect_class in {"local_workspace", "local_and_external"}
            and not self.mutates_workspace
        ):
            raise ValueError(
                "sideEffectClass=local_workspace or local_and_external requires "
                "mutatesWorkspace=True"
            )

        if self.parallel_safety == "readonly" and (self.dangerous or self.mutates_workspace):
            raise ValueError("readonly parallel-safety cannot be dangerous or mutate workspace")

        if self.can_satisfy_deterministic_requirement:
            if not self.deterministic_requirement_types or not self.emits_evidence_types:
                raise ValueError(
                    "deterministic-capable tools must declare deterministic requirement "
                    "types and emitted evidence types"
                )
        elif self.deterministic_requirement_types:
            raise ValueError("non-deterministic tools cannot declare deterministic requirement types")

        if (
            self.adk_tool_type == "LongRunningFunctionTool"
            and not self.should_defer
            and self.latency_class not in _LONG_RUNNING_LATENCY_CLASSES
        ):
            raise ValueError(
                "LongRunningFunctionTool metadata requires shouldDefer=True or "
                "background/long_running latency"
            )

        if (
            self.adk_tool_type == "FunctionTool"
            and self.latency_class == "long_running"
        ):
            raise ValueError(
                "latencyClass=long_running requires adkToolType=LongRunningFunctionTool"
            )

        return self


def _truthy(value: object) -> bool:
    return value is True
