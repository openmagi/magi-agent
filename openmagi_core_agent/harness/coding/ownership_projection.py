from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from openmagi_core_agent.recipes.first_party.coding.ownership import (
    ADK_PRIMITIVE_NAMES,
    CORE_SUBSTRATE_REFS,
    REQUIRED_CODING_MECHANICS,
    CodingRecipeOwnershipManifest,
    build_coding_recipe_ownership_manifest,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SAFE_MANIFEST_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:@-]{1,180}$")
_POLICY_REF_RE = re.compile(r"^policy-ref:sha256:[a-f0-9]{64}$")
_PRIVATE_TEXT_RE = re.compile(
    r"(?:/Users/|/home/|/workspace/|/data/bots/|/var/lib/|authorization|cookie|"
    r"bearer|token|secret|password|credential|private[_-]?key|raw|prompt|output|"
    r"transcript|hidden[_-]?reasoning|toolhost|dispatcher|registry|"
    r"openmagi_core_agent\.runtime)",
    re.IGNORECASE,
)


class CodingRecipeOwnershipProjection(BaseModel):
    model_config = _MODEL_CONFIG

    manifest_id: str = Field(alias="manifestId")
    owning_layer: Literal["Coding recipe/harness"] = Field(alias="owningLayer")
    activation_gate: Literal["PR1-coding-ownership-fixture-only"] = Field(alias="activationGate")
    mechanic_ids: tuple[str, ...] = Field(alias="mechanicIds")
    adk_primitive_names: tuple[Literal["Agent.metadata", "FunctionTool.name"], ...] = Field(
        alias="adkPrimitiveNames",
    )
    policy_refs: tuple[str, ...] = Field(alias="policyRefs")
    core_substrate_refs: tuple[str, ...] = Field(alias="coreSubstrateRefs")
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    live_authority_allowed: Literal[False] = Field(default=False, alias="liveAuthorityAllowed")
    core_touch_allowed: Literal[False] = Field(default=False, alias="coreTouchAllowed")
    live_attachment_refs: tuple[()] = Field(default=(), alias="liveAttachmentRefs")
    live_runner_route_refs: tuple[()] = Field(default=(), alias="liveRunnerRouteRefs")
    live_tool_refs: tuple[()] = Field(default=(), alias="liveToolRefs")
    tool_host_dispatch_refs: tuple[()] = Field(default=(), alias="toolHostDispatchRefs")
    tool_host_dispatch_attached: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchAttached",
    )
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")
    model_provider_attached: Literal[False] = Field(default=False, alias="modelProviderAttached")
    workspace_mutation_attached: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAttached",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_inert_projection(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["defaultOff"] = True
        payload["liveAuthorityAllowed"] = False
        payload["coreTouchAllowed"] = False
        payload["liveAttachmentRefs"] = ()
        payload["liveRunnerRouteRefs"] = ()
        payload["liveToolRefs"] = ()
        payload["toolHostDispatchRefs"] = ()
        payload["toolHostDispatchAttached"] = False
        payload["adkRunnerAttached"] = False
        payload["modelProviderAttached"] = False
        payload["workspaceMutationAttached"] = False
        return payload

    @field_validator("mechanic_ids")
    @classmethod
    def _validate_mechanics(cls, value: Sequence[str]) -> tuple[str, ...]:
        ids = tuple(value)
        if ids != REQUIRED_CODING_MECHANICS:
            raise ValueError("mechanicIds must match the required coding ownership set")
        return ids

    @field_validator("manifest_id")
    @classmethod
    def _validate_manifest_id(cls, value: str) -> str:
        if _PRIVATE_TEXT_RE.search(value):
            raise ValueError("manifestId must not contain private or raw terms")
        if _SAFE_MANIFEST_ID_RE.fullmatch(value) is None:
            raise ValueError("manifestId must be a public metadata id")
        return value

    @field_validator("policy_refs")
    @classmethod
    def _validate_policy_refs(cls, value: Sequence[str]) -> tuple[str, ...]:
        refs = tuple(value)
        if len(refs) != len(REQUIRED_CODING_MECHANICS):
            raise ValueError("policyRefs must contain one digest ref per coding mechanic")
        for ref in refs:
            if _POLICY_REF_RE.fullmatch(ref) is None:
                raise ValueError("policyRefs must be digest-only policy refs")
            if _PRIVATE_TEXT_RE.search(ref):
                raise ValueError("policyRefs must not contain private or raw terms")
        return refs

    @field_validator("adk_primitive_names")
    @classmethod
    def _validate_adk_names(cls, value: Sequence[str]) -> tuple[str, ...]:
        names = tuple(value)
        if names != ADK_PRIMITIVE_NAMES:
            raise ValueError("adkPrimitiveNames must remain metadata names only")
        return names

    @field_validator("core_substrate_refs")
    @classmethod
    def _validate_core_refs(cls, value: Sequence[str]) -> tuple[str, ...]:
        refs = tuple(value)
        if refs != CORE_SUBSTRATE_REFS:
            raise ValueError("coreSubstrateRefs must stay generic metadata substrate names")
        return refs

    @field_serializer(
        "live_authority_allowed",
        "core_touch_allowed",
        "tool_host_dispatch_attached",
        "adk_runner_attached",
        "model_provider_attached",
        "workspace_mutation_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    @field_serializer(
        "live_attachment_refs",
        "live_runner_route_refs",
        "live_tool_refs",
        "tool_host_dispatch_refs",
    )
    def _serialize_empty_tuple(self, _value: object) -> tuple[()]:
        return ()

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
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        return type(self).model_validate(payload)


def project_coding_recipe_ownership(
    manifest: CodingRecipeOwnershipManifest | None = None,
) -> CodingRecipeOwnershipProjection:
    source = manifest or build_coding_recipe_ownership_manifest()
    return CodingRecipeOwnershipProjection.model_validate(
        {
            "manifestId": source.manifest_id,
            "owningLayer": source.owning_layer,
            "activationGate": source.activation_gate,
            "mechanicIds": tuple(mechanic.mechanic_id for mechanic in source.mechanics),
            "adkPrimitiveNames": source.adk_primitive_names,
            "policyRefs": source.policy_refs,
            "coreSubstrateRefs": source.core_substrate_refs,
        }
    )


__all__ = [
    "CodingRecipeOwnershipProjection",
    "project_coding_recipe_ownership",
]
