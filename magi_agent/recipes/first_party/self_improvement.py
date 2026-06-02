from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


_REQUIRED_POLICY_REFS = (
    "policy:self-improvement.eval-observation-required@1",
    "policy:self-improvement.no-direct-mutation@1",
)
_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class SelfImprovementRecipeAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    model_call_enabled: Literal[False] = Field(default=False, alias="modelCallEnabled")
    live_tool_attached: Literal[False] = Field(default=False, alias="liveToolAttached")
    live_callback_attached: Literal[False] = Field(
        default=False,
        alias="liveCallbackAttached",
    )
    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )
    user_visible_output_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEnabled",
    )
    mutation_enabled: Literal[False] = Field(default=False, alias="mutationEnabled")

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field_name, field in cls.model_fields.items():
            payload[field.alias or field_name] = False
            payload.pop(field_name, None)
        return payload

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

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        return type(self)()

    @field_serializer(
        "traffic_attached",
        "runner_attached",
        "model_call_enabled",
        "live_tool_attached",
        "live_callback_attached",
        "production_write_enabled",
        "user_visible_output_enabled",
        "mutation_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class SelfImprovementProposalRecipeManifest(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_id: Literal["recipe:self-improvement.proposal@1"] = Field(
        default="recipe:self-improvement.proposal@1",
        alias="recipeId",
    )
    status: Literal["disabled"] = "disabled"
    governed: Literal[True] = True
    proposal_only: Literal[True] = Field(default=True, alias="proposalOnly")
    required_policy_refs: tuple[str, ...] = Field(
        default=_REQUIRED_POLICY_REFS,
        alias="requiredPolicyRefs",
    )
    live_tool_refs: tuple[()] = Field(default=(), alias="liveToolRefs")
    live_callback_refs: tuple[()] = Field(default=(), alias="liveCallbackRefs")
    live_runner_route_refs: tuple[()] = Field(default=(), alias="liveRunnerRouteRefs")
    attachment_flags: SelfImprovementRecipeAttachmentFlags = Field(
        default_factory=SelfImprovementRecipeAttachmentFlags,
        alias="attachmentFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_default_off(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["recipeId"] = "recipe:self-improvement.proposal@1"
        payload.pop("recipe_id", None)
        payload["status"] = "disabled"
        payload["governed"] = True
        payload["proposalOnly"] = True
        payload.pop("proposal_only", None)
        payload["requiredPolicyRefs"] = _REQUIRED_POLICY_REFS
        payload.pop("required_policy_refs", None)
        payload["liveToolRefs"] = ()
        payload.pop("live_tool_refs", None)
        payload["liveCallbackRefs"] = ()
        payload.pop("live_callback_refs", None)
        payload["liveRunnerRouteRefs"] = ()
        payload.pop("live_runner_route_refs", None)
        payload["attachmentFlags"] = SelfImprovementRecipeAttachmentFlags().model_dump(
            by_alias=True,
        )
        payload.pop("attachment_flags", None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        return self.model_copy(update=update)


def build_self_improvement_proposal_recipe_manifest() -> SelfImprovementProposalRecipeManifest:
    return SelfImprovementProposalRecipeManifest()


__all__ = [
    "SelfImprovementProposalRecipeManifest",
    "SelfImprovementRecipeAttachmentFlags",
    "build_self_improvement_proposal_recipe_manifest",
]
