from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


CodingMechanicId = Literal[
    "read-before-edit",
    "stale-read-rejection",
    "patch-test-verification",
    "bash-policy",
    "subagents",
    "compaction",
    "repair",
    "completion-criteria",
]
CodingMechanicOwner = Literal["coding_recipe"]

REQUIRED_CODING_MECHANICS: tuple[CodingMechanicId, ...] = (
    "read-before-edit",
    "stale-read-rejection",
    "patch-test-verification",
    "bash-policy",
    "subagents",
    "compaction",
    "repair",
    "completion-criteria",
)
ADK_PRIMITIVE_NAMES: tuple[str, ...] = ("Agent.metadata", "FunctionTool.name")
CORE_SUBSTRATE_REFS: tuple[str, ...] = (
    "agent-metadata",
    "tool-name-metadata",
    "callback-metadata",
    "session-metadata",
    "artifact-ref-metadata",
    "evaluation-result-metadata",
)
_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_POLICY_REF_RE = re.compile(r"^policy-ref:sha256:[a-f0-9]{64}$")
_PRIVATE_TEXT_RE = re.compile(
    r"(?:/Users/|/home/|/workspace/|/data/bots/|/var/lib/|authorization|cookie|"
    r"bearer|token|secret|password|credential|private[_-]?key|raw|prompt|output|"
    r"transcript|hidden[_-]?reasoning|toolhost|dispatcher|registry|"
    r"openmagi_core_agent\.runtime)",
    re.IGNORECASE,
)
_FORCED_EMPTY_SEQUENCE_FIELDS = (
    "liveAttachmentRefs",
    "liveRunnerRouteRefs",
    "liveToolRefs",
    "toolHostDispatchRefs",
)
_FORCED_FALSE_FIELDS = (
    "defaultOff",
    "liveAuthorityAllowed",
    "coreTouchAllowed",
    "toolHostDispatchAttached",
    "adkRunnerAttached",
    "modelProviderAttached",
    "workspaceMutationAttached",
)


class CodingMechanicOwnership(BaseModel):
    model_config = _MODEL_CONFIG

    mechanic_id: CodingMechanicId = Field(alias="mechanicId")
    owner: CodingMechanicOwner = "coding_recipe"
    policy_ref: str = Field(alias="policyRef")
    substrate_refs: tuple[str, ...] = Field(alias="substrateRefs")

    @field_validator("policy_ref")
    @classmethod
    def _validate_policy_ref(cls, value: str) -> str:
        if _POLICY_REF_RE.fullmatch(value) is None:
            raise ValueError("policyRef must be a digest-only policy ref")
        return value

    @field_validator("substrate_refs")
    @classmethod
    def _validate_substrate_refs(cls, value: Sequence[str]) -> tuple[str, ...]:
        refs = tuple(value)
        if not refs:
            raise ValueError("substrateRefs must not be empty")
        for ref in refs:
            _validate_public_ref(ref, "substrateRefs")
        return refs

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


class CodingRecipeOwnershipManifest(BaseModel):
    model_config = _MODEL_CONFIG

    manifest_id: Literal["coding-recipe-ownership-boundaries@1"] = Field(
        default="coding-recipe-ownership-boundaries@1",
        alias="manifestId",
    )
    owning_layer: Literal["Coding recipe/harness"] = Field(
        default="Coding recipe/harness",
        alias="owningLayer",
    )
    activation_gate: Literal["PR1-coding-ownership-fixture-only"] = Field(
        default="PR1-coding-ownership-fixture-only",
        alias="activationGate",
    )
    adk_primitive_names: tuple[Literal["Agent.metadata", "FunctionTool.name"], ...] = Field(
        default=ADK_PRIMITIVE_NAMES,
        alias="adkPrimitiveNames",
    )
    mechanics: tuple[CodingMechanicOwnership, ...]
    policy_refs: tuple[str, ...] = Field(alias="policyRefs")
    core_substrate_refs: tuple[str, ...] = Field(
        default=CORE_SUBSTRATE_REFS,
        alias="coreSubstrateRefs",
    )
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
    def _force_inert_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["defaultOff"] = True
        payload.pop("default_off", None)
        for field in _FORCED_FALSE_FIELDS[1:]:
            payload[field] = False
        for field in _FORCED_EMPTY_SEQUENCE_FIELDS:
            if payload.get(field) or payload.get(_snake_case(field)):
                raise ValueError(f"{field} must remain empty")
            payload[field] = ()
            payload.pop(_snake_case(field), None)
        return payload

    @field_validator("mechanics")
    @classmethod
    def _validate_required_mechanics(
        cls,
        value: Sequence[CodingMechanicOwnership],
    ) -> tuple[CodingMechanicOwnership, ...]:
        mechanics = tuple(value)
        if tuple(mechanic.mechanic_id for mechanic in mechanics) != REQUIRED_CODING_MECHANICS:
            raise ValueError("mechanics must match the required coding ownership set")
        return mechanics

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
                raise ValueError("policyRefs must not contain raw or private terms")
        return refs

    @field_validator("core_substrate_refs")
    @classmethod
    def _validate_core_substrate_refs(cls, value: Sequence[str]) -> tuple[str, ...]:
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

    def public_projection(self) -> dict[str, object]:
        return {
            "manifestId": self.manifest_id,
            "owningLayer": self.owning_layer,
            "activationGate": self.activation_gate,
            "adkPrimitiveNames": list(self.adk_primitive_names),
            "mechanics": [
                {
                    "mechanicId": mechanic.mechanic_id,
                    "owner": mechanic.owner,
                    "policyRef": mechanic.policy_ref,
                    "substrateRefs": list(mechanic.substrate_refs),
                }
                for mechanic in self.mechanics
            ],
            "policyRefs": list(self.policy_refs),
            "coreSubstrateRefs": list(self.core_substrate_refs),
            "defaultOff": True,
            "liveAuthorityAllowed": False,
            "coreTouchAllowed": False,
            "liveAttachmentRefs": [],
            "liveRunnerRouteRefs": [],
            "liveToolRefs": [],
            "toolHostDispatchRefs": [],
            "toolHostDispatchAttached": False,
            "adkRunnerAttached": False,
            "modelProviderAttached": False,
            "workspaceMutationAttached": False,
        }


def build_coding_recipe_ownership_manifest(**overrides: object) -> CodingRecipeOwnershipManifest:
    policy_refs = tuple(_policy_ref(mechanic_id) for mechanic_id in REQUIRED_CODING_MECHANICS)
    payload: dict[str, object] = {
        "mechanics": tuple(
            CodingMechanicOwnership(
                mechanicId=mechanic_id,
                policyRef=policy_ref,
                substrateRefs=_substrate_refs_for(mechanic_id),
            )
            for mechanic_id, policy_ref in zip(REQUIRED_CODING_MECHANICS, policy_refs, strict=True)
        ),
        "policyRefs": policy_refs,
    }
    payload.update(overrides)
    return CodingRecipeOwnershipManifest.model_validate(payload)


def _policy_ref(mechanic_id: CodingMechanicId) -> str:
    digest = hashlib.sha256(f"coding-recipe-ownership:{mechanic_id}:v1".encode()).hexdigest()
    return f"policy-ref:sha256:{digest}"


def _substrate_refs_for(mechanic_id: CodingMechanicId) -> tuple[str, ...]:
    if mechanic_id in {"patch-test-verification", "completion-criteria"}:
        return ("artifact-ref-metadata", "evaluation-result-metadata")
    if mechanic_id in {"subagents", "repair"}:
        return ("agent-metadata", "callback-metadata")
    if mechanic_id == "compaction":
        return ("session-metadata", "artifact-ref-metadata")
    return ("tool-name-metadata", "callback-metadata")


def _validate_public_ref(value: str, field_name: str) -> str:
    if _PRIVATE_TEXT_RE.search(value):
        raise ValueError(f"{field_name} must not contain private or concrete runtime terms")
    if _SAFE_REF_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must contain public metadata refs only")
    return value


def _snake_case(value: str) -> str:
    chars: list[str] = []
    for index, char in enumerate(value):
        if char.isupper() and index:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)


__all__ = [
    "ADK_PRIMITIVE_NAMES",
    "CORE_SUBSTRATE_REFS",
    "REQUIRED_CODING_MECHANICS",
    "CodingMechanicOwnership",
    "CodingRecipeOwnershipManifest",
    "build_coding_recipe_ownership_manifest",
]
