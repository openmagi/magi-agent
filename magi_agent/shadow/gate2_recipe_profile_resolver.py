from __future__ import annotations

import re
import hashlib
import json
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from magi_agent.ops.safety import reject_private_text
from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_APPROVED_GATE2_PROFILE_REFS = frozenset({"openmagi.gate2.workspace-canary.v1"})
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_UNSAFE_PUBLIC_TEXT_RE = re.compile(
    r"auth|cookie|credential|key|password|private|secret|session|token|"
    r"sk-[A-Za-z0-9._:-]{4,}",
    re.IGNORECASE,
)


class _Gate2ProfileModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=True, mode="json")
        if update:
            data.update(update)
        return type(self).model_validate(data)


class Gate2RecipeProfile(_Gate2ProfileModel):
    status: Literal["ready", "blocked"]
    reason: str
    profile_ref: str = Field(alias="profileRef")
    profile_digest: str = Field(alias="profileDigest")
    selected_pack_ids: tuple[str, ...] = Field(alias="selectedPackIds")
    tools_policy: Literal["sandbox_readwrite_diagnostic", "disabled"] = Field(
        alias="toolsPolicy",
    )
    core_runtime_owns_workflow_policy: Literal[False] = Field(
        default=False,
        alias="coreRuntimeOwnsWorkflowPolicy",
    )
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )
    live_tool_refs: tuple[str, ...] = Field(default=(), alias="liveToolRefs")
    runner_route_refs: tuple[str, ...] = Field(default=(), alias="runnerRouteRefs")
    policy_owner_layer: Literal["recipe_harness"] = Field(
        default="recipe_harness",
        alias="policyOwnerLayer",
    )
    resolution_source: Literal["recipe_compiler", "blocked"] = Field(
        default="recipe_compiler",
        alias="resolutionSource",
    )

    @model_validator(mode="after")
    def _validate_public_refs(self) -> Self:
        if not _is_safe_ref(self.profile_ref):
            raise ValueError("Gate 2 profile ref must be public-safe")
        if not re.fullmatch(r"^sha256:[a-f0-9]{64}$", self.profile_digest):
            raise ValueError("Gate 2 profile digest must be digest-only")
        for pack_id in self.selected_pack_ids:
            if not _is_safe_ref(pack_id):
                raise ValueError("Gate 2 pack ids must be public-safe")
        if self.live_tool_refs:
            raise ValueError("Gate 2 profile must not expose live tool refs")
        if self.runner_route_refs:
            raise ValueError("Gate 2 profile must not expose runner route refs")
        return self


def resolve_gate2_recipe_profile(profile_ref: str) -> Gate2RecipeProfile:
    safe_ref = str(profile_ref or "").strip()
    if not _is_safe_ref(safe_ref):
        safe_ref = "invalid_profile_ref"
    if safe_ref not in _APPROVED_GATE2_PROFILE_REFS:
        return Gate2RecipeProfile(
            status="blocked",
            reason="profile_not_approved",
            profileRef=safe_ref or "missing",
            profileDigest=_profile_digest(
                {
                    "profileRef": safe_ref or "missing",
                    "status": "blocked",
                    "toolsPolicy": "disabled",
                }
            ),
            selectedPackIds=(),
            toolsPolicy="disabled",
            resolutionSource="blocked",
        )
    from magi_agent.recipes.kernel_recipe_packs import build_runtime_pack_registry

    snapshot = AgentRecipeCompiler(build_runtime_pack_registry()).compile(
        ProfileResolutionRequest(
            taskProfile={
                "taskTypes": ("coding",),
                "taskIntents": ("development", "dev-coding"),
            },
        )
    )
    return Gate2RecipeProfile(
        status="ready",
        reason="approved_gate2_workspace_canary_profile",
        profileRef=safe_ref,
        profileDigest=_profile_digest(
            {
                "profileRef": safe_ref,
                "status": "ready",
                "recipeSnapshot": snapshot.model_dump(
                    by_alias=True,
                    mode="json",
                    warnings=False,
                ),
                "toolsPolicy": "sandbox_readwrite_diagnostic",
                "resolutionSource": "recipe_compiler",
            }
        ),
        selectedPackIds=snapshot.selected_pack_ids,
        toolsPolicy="sandbox_readwrite_diagnostic",
        liveToolRefs=(),
        runnerRouteRefs=(),
        resolutionSource="recipe_compiler",
    )


def _is_safe_ref(value: str) -> bool:
    if _SAFE_REF_RE.fullmatch(value) is None:
        return False
    if _UNSAFE_PUBLIC_TEXT_RE.search(value) is not None:
        return False
    try:
        reject_private_text(value, field_name="gate2ProfileRef")
    except ValueError:
        return False
    return True


def _profile_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
