from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
import re
from typing import Any, Literal, Self
import weakref

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictBool,
    StrictStr,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

from openmagi_core_agent.recipes.composition import (
    AdmittedRecipeSnapshot,
    RecipeAdmissionConflict,
    RecipeAdmissionRequest,
    RecipeStackInput,
    admit_recipe_stack,
)
from openmagi_core_agent.recipes.hook_composition import (
    EffectiveRecipeHookContract,
    HookCompositionConflict,
    HookContribution,
    compose_hook_contributions,
)
from openmagi_core_agent.recipes.merge_algebra import (
    EffectiveRecipeMergeContract,
    RecipeMergeConflict,
    RetryMergePolicy,
    merge_admitted_recipe_snapshots,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    hide_input_in_errors=True,
    revalidate_instances="always",
    validate_default=True,
)
_SAFE_RECIPE_REF_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}(?:\.[a-z0-9][a-z0-9_-]{0,63})+$")
_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users|/home|/private|/var/lib/kubelet|/var/run/secrets|/workspace|/data/bots)"
)
_UNSAFE_TEXT_FRAGMENTS = frozenset((
    "accesskey",
    "apikey",
    "auth",
    "authheader",
    "secret",
    "sessionkey",
    "token",
    "credential",
    "password",
    "bearer",
    "authorization",
    "cookie",
    "privateconfig",
    "privatekey",
    "rawconfig",
    "rawprompt",
    "rawtool",
    "skproj",
    "toolargs",
    "toolarguments",
    "toolresult",
    "toolresults",
))
_UNSAFE_SECRET_SHAPED_RE = re.compile(
    r"(?:"
    r"sk[-_](?:proj[-_])?[a-z0-9_-]{8,}"
    r"|rk[-_][a-z0-9_-]{8,}"
    r"|glpat-[a-z0-9_-]{8,}"
    r"|github_pat_[a-z0-9_]{8,}"
    r"|gh[pousr]_[a-z0-9]{8,}"
    r"|xox[a-z0-9]*-[a-z0-9-]{8,}"
    r"|(?:A3T[A-Z0-9]|AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}"
    r"|AIza[0-9A-Za-z_-]{20,}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r")",
    re.IGNORECASE,
)


class _IdentityDigestRegistry:
    def __init__(self) -> None:
        self._entries: dict[int, tuple[weakref.ReferenceType[object], str]] = {}

    def mark(self, value: object, digest: object, field_name: str) -> None:
        object_id = id(value)

        def _remove_stale(ref: weakref.ReferenceType[object]) -> None:
            entry = self._entries.get(object_id)
            if entry is not None and entry[0] is ref:
                self._entries.pop(object_id, None)

        ref = weakref.ref(value, _remove_stale)
        self._entries[object_id] = (ref, _safe_digest(digest, field_name))

    def digest_for(self, value: object) -> str | None:
        object_id = id(value)
        entry = self._entries.get(object_id)
        if entry is None:
            return None
        ref, digest = entry
        if ref() is not value:
            self._entries.pop(object_id, None)
            return None
        return digest


_EFFECTIVE_CONTRACT_DIGESTS = _IdentityDigestRegistry()


def _contains_unsafe_text(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.strip().lower())
    return (
        _PRIVATE_PATH_RE.search(value) is not None
        or _UNSAFE_SECRET_SHAPED_RE.search(value) is not None
        or any(fragment in normalized for fragment in _UNSAFE_TEXT_FRAGMENTS)
    )


def _safe_digest(value: object, field_name: str = "digest") -> str:
    if not isinstance(value, str) or _SHA256_DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value.lower()


def _public_id(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a public identifier")
    public_value = value.strip()
    if not public_value:
        raise ValueError(f"{field_name} must be a public identifier")
    if _contains_unsafe_text(public_value) or _PUBLIC_ID_RE.fullmatch(public_value) is None:
        raise ValueError(f"{field_name} must be a public identifier")
    return public_value


def _recipe_ref(value: object, field_name: str = "recipe_ref") -> str:
    ref = _public_id(value, field_name)
    if _SAFE_RECIPE_REF_RE.fullmatch(ref) is None:
        raise ValueError(f"{field_name} must be a recipe ref")
    return ref


def _tuple_values(value: object, field_name: str) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
        return tuple(value)
    raise ValueError(f"{field_name} must be iterable")


def _public_id_tuple(value: object, field_name: str) -> tuple[str, ...]:
    refs: set[str] = set()
    for raw_value in _tuple_values(value, field_name):
        refs.add(_public_id(raw_value, field_name))
    return tuple(sorted(refs))


def _recipe_ref_tuple(value: object, field_name: str) -> tuple[str, ...]:
    refs: set[str] = set()
    for raw_value in _tuple_values(value, field_name):
        refs.add(_recipe_ref(raw_value, field_name))
    return tuple(sorted(refs))


def _dedupe_preserving_order(values: Iterable[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        ref = _recipe_ref(value, "recipe_ref")
        if ref not in seen:
            seen.add(ref)
            deduped.append(ref)
    return tuple(deduped)


def _canonical_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _subject_digest(subject_ref: str) -> str:
    return _canonical_digest({"subjectRef": _public_id(subject_ref, "subject_ref")})


class EffectiveRecipeExclusion(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_ref: StrictStr = Field(alias="recipeRef")
    reason: StrictStr
    blocking: StrictBool = False

    @field_validator("recipe_ref", mode="before")
    @classmethod
    def _sanitize_recipe_ref(cls, value: object) -> str:
        return _recipe_ref(value, "recipe_ref")

    @field_validator("reason")
    @classmethod
    def _sanitize_reason(cls, value: object) -> str:
        return _public_id(value, "reason")

    def public_projection(self) -> dict[str, object]:
        return {
            "recipeRef": _recipe_ref(self.recipe_ref, "recipe_ref"),
            "reason": _public_id(self.reason, "reason"),
            "blocking": bool(self.blocking),
        }


class EffectiveRecipeConflict(BaseModel):
    model_config = _MODEL_CONFIG

    code: StrictStr
    subject_ref: StrictStr = Field(alias="subjectRef")
    recipe_refs: tuple[str, ...] = Field(default=(), alias="recipeRefs")
    blocking: StrictBool

    @field_validator("code", "subject_ref")
    @classmethod
    def _sanitize_public_scalar(cls, value: object, info: Any) -> str:
        return _public_id(value, info.field_name)

    @field_validator("recipe_refs", mode="before")
    @classmethod
    def _sanitize_recipe_refs(cls, value: object) -> tuple[str, ...]:
        return _recipe_ref_tuple(value, "recipe_refs")

    def public_projection(self) -> dict[str, object]:
        return {
            "code": _public_id(self.code, "code"),
            "blocking": bool(self.blocking),
            "subjectDigest": _subject_digest(self.subject_ref),
            "recipeRefCount": len(_recipe_ref_tuple(self.recipe_refs, "recipe_refs")),
        }

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        return EffectiveRecipeConflict.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            EffectiveRecipeConflict.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_model(self) -> dict[str, object]:
        return EffectiveRecipeConflict.public_projection(self)


class EffectiveRecipeContract(BaseModel):
    model_config = _MODEL_CONFIG

    _validated_effective_digest: str = PrivateAttr(default="")

    schema_version: Literal["effectiveRecipeContract.v1"] = Field(
        default="effectiveRecipeContract.v1",
        alias="schemaVersion",
    )
    effective_recipe_refs: tuple[str, ...] = Field(alias="effectiveRecipeRefs")
    effective_hard_safety_refs: tuple[str, ...] = Field(
        default=(),
        alias="effectiveHardSafetyRefs",
    )
    included_explicit_refs: tuple[str, ...] = Field(alias="includedExplicitRefs")
    included_auto_refs: tuple[str, ...] = Field(alias="includedAutoRefs")
    excluded_refs: tuple[EffectiveRecipeExclusion, ...] = Field(default=(), alias="excludedRefs")
    effective_tool_grants: tuple[str, ...] = Field(alias="effectiveToolGrants")
    effective_tool_denials: tuple[str, ...] = Field(alias="effectiveToolDenials")
    effective_evidence_requirements: tuple[str, ...] = Field(alias="effectiveEvidenceRequirements")
    effective_approval_requirements: tuple[str, ...] = Field(alias="effectiveApprovalRequirements")
    effective_context_policy: tuple[str, ...] = Field(alias="effectiveContextPolicy")
    effective_hooks: EffectiveRecipeHookContract | None = Field(default=None, alias="effectiveHooks")
    effective_retry_policy: RetryMergePolicy = Field(alias="effectiveRetryPolicy")
    conflicts: tuple[EffectiveRecipeConflict, ...] = Field(default=())
    blocked: StrictBool
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    live_activation: Literal[False] = Field(default=False, alias="liveActivation")
    effective_digest: StrictStr = Field(alias="effectiveDigest")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls.model_validate(cls._canonical_authority_payload(values))

    @classmethod
    def _construct_for_digest(cls, **values: Any) -> Self:
        values = cls._canonical_authority_payload(values)
        instance = super().model_construct(**values)
        cls._set_canonical_authority_fields(instance)
        return instance

    @staticmethod
    def _canonical_authority_payload(values: Mapping[str, Any]) -> dict[str, Any]:
        values = dict(values)
        for field_name, alias in (
            ("default_off", "defaultOff"),
            ("traffic_attached", "trafficAttached"),
            ("execution_attached", "executionAttached"),
            ("live_activation", "liveActivation"),
        ):
            values.pop(field_name, None)
            values.pop(alias, None)
        values.update({
            "defaultOff": True,
            "trafficAttached": False,
            "executionAttached": False,
            "liveActivation": False,
        })
        return values

    @staticmethod
    def _set_canonical_authority_fields(instance: "EffectiveRecipeContract") -> None:
        object.__setattr__(instance, "default_off", True)
        object.__setattr__(instance, "traffic_attached", False)
        object.__setattr__(instance, "execution_attached", False)
        object.__setattr__(instance, "live_activation", False)

    @field_validator(
        "effective_recipe_refs",
        "effective_hard_safety_refs",
        "included_explicit_refs",
        "included_auto_refs",
        mode="before",
    )
    @classmethod
    def _sanitize_recipe_ref_section(cls, value: object) -> tuple[str, ...]:
        return _recipe_ref_tuple(value, "recipe_refs")

    @field_validator(
        "effective_tool_grants",
        "effective_tool_denials",
        "effective_evidence_requirements",
        "effective_approval_requirements",
        "effective_context_policy",
        mode="before",
    )
    @classmethod
    def _sanitize_public_ref_section(cls, value: object, info: Any) -> tuple[str, ...]:
        return _public_id_tuple(value, info.field_name)

    @field_validator("excluded_refs", mode="before")
    @classmethod
    def _sanitize_exclusions(cls, value: object) -> tuple[EffectiveRecipeExclusion, ...]:
        exclusions: list[EffectiveRecipeExclusion] = []
        for exclusion in _tuple_values(value, "excluded_refs"):
            if type(exclusion) is EffectiveRecipeExclusion:
                exclusions.append(exclusion)
                continue
            exclusions.append(EffectiveRecipeExclusion.model_validate(exclusion))
        return tuple(exclusions)

    @field_validator("conflicts", mode="before")
    @classmethod
    def _sanitize_conflicts(cls, value: object) -> tuple[EffectiveRecipeConflict, ...]:
        conflicts: list[EffectiveRecipeConflict] = []
        for conflict in _tuple_values(value, "conflicts"):
            if type(conflict) is EffectiveRecipeConflict:
                conflicts.append(conflict)
                continue
            raise ValueError("effective conflicts require structured conflict instances")
        return tuple(conflicts)

    @field_validator("effective_hooks", mode="before")
    @classmethod
    def _sanitize_effective_hooks(cls, value: object) -> EffectiveRecipeHookContract | None:
        if value is None:
            return None
        if type(value) is not EffectiveRecipeHookContract:
            raise ValueError("effective hooks require composed hook contract")
        value.public_projection()
        return value

    @field_validator("effective_digest", mode="before")
    @classmethod
    def _sanitize_effective_digest(cls, value: object) -> str:
        return _safe_digest(value, "effective_digest")

    @model_validator(mode="after")
    def _validate_effective_contract(self) -> Self:
        if self.blocked:
            if (
                self.effective_recipe_refs
                or self.effective_hard_safety_refs
                or self.included_explicit_refs
                or self.included_auto_refs
                or self.effective_tool_grants
                or self.effective_tool_denials
                or self.effective_evidence_requirements
                or self.effective_approval_requirements
                or self.effective_context_policy
                or self.effective_hooks is not None
                or self.effective_retry_policy.max_attempts > 0
                or self.effective_retry_policy.repair_attempts > 0
            ):
                raise ValueError("blocked effective contract cannot carry activation material")
        if self.effective_digest != EffectiveRecipeContract._compute_effective_digest(self):
            raise ValueError("effective digest mismatch")
        return self

    def model_post_init(self, __context: Any) -> None:
        self._validated_effective_digest = EffectiveRecipeContract._compute_effective_digest(self)
        _EFFECTIVE_CONTRACT_DIGESTS.mark(
            self,
            self._validated_effective_digest,
            "effective_digest",
        )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> "EffectiveRecipeContract":
        EffectiveRecipeContract._assert_effective_digest_matches(self)
        if update:
            raise ValueError("effective recipe contract authority fields are immutable")
        return EffectiveRecipeContract.model_validate(EffectiveRecipeContract._as_model_payload(self))

    @staticmethod
    def _compute_effective_digest(contract: "EffectiveRecipeContract") -> str:
        return _canonical_digest(EffectiveRecipeContract._digest_payload(contract))

    @staticmethod
    def _as_model_payload(contract: "EffectiveRecipeContract") -> dict[str, object]:
        return {
            "schemaVersion": contract.schema_version,
            "effectiveRecipeRefs": contract.effective_recipe_refs,
            "effectiveHardSafetyRefs": contract.effective_hard_safety_refs,
            "includedExplicitRefs": contract.included_explicit_refs,
            "includedAutoRefs": contract.included_auto_refs,
            "excludedRefs": contract.excluded_refs,
            "effectiveToolGrants": contract.effective_tool_grants,
            "effectiveToolDenials": contract.effective_tool_denials,
            "effectiveEvidenceRequirements": contract.effective_evidence_requirements,
            "effectiveApprovalRequirements": contract.effective_approval_requirements,
            "effectiveContextPolicy": contract.effective_context_policy,
            "effectiveHooks": contract.effective_hooks,
            "effectiveRetryPolicy": contract.effective_retry_policy,
            "conflicts": contract.conflicts,
            "blocked": contract.blocked,
            "defaultOff": True,
            "trafficAttached": False,
            "executionAttached": False,
            "liveActivation": False,
            "effectiveDigest": contract.effective_digest,
        }

    @staticmethod
    def _digest_payload(contract: "EffectiveRecipeContract") -> dict[str, object]:
        hook_digest = None
        hook_blocked = None
        if contract.effective_hooks is not None:
            hook_projection = contract.effective_hooks.public_projection()
            hook_digest = _safe_digest(
                hook_projection["compositionDigest"],
                "composition_digest",
            )
            hook_blocked = bool(hook_projection["blocked"])
        return {
            "schemaVersion": _public_id(contract.schema_version, "schema_version"),
            "effectiveRecipeRefs": _recipe_ref_tuple(
                contract.effective_recipe_refs,
                "effective_recipe_refs",
            ),
            "effectiveHardSafetyRefs": _recipe_ref_tuple(
                contract.effective_hard_safety_refs,
                "effective_hard_safety_refs",
            ),
            "includedExplicitRefs": _recipe_ref_tuple(
                contract.included_explicit_refs,
                "included_explicit_refs",
            ),
            "includedAutoRefs": _recipe_ref_tuple(
                contract.included_auto_refs,
                "included_auto_refs",
            ),
            "excludedRefs": tuple(
                exclusion.public_projection() for exclusion in contract.excluded_refs
            ),
            "effectiveToolGrants": _public_id_tuple(
                contract.effective_tool_grants,
                "effective_tool_grants",
            ),
            "effectiveToolDenials": _public_id_tuple(
                contract.effective_tool_denials,
                "effective_tool_denials",
            ),
            "effectiveEvidenceRequirements": _public_id_tuple(
                contract.effective_evidence_requirements,
                "effective_evidence_requirements",
            ),
            "effectiveApprovalRequirements": _public_id_tuple(
                contract.effective_approval_requirements,
                "effective_approval_requirements",
            ),
            "effectiveContextPolicy": _public_id_tuple(
                contract.effective_context_policy,
                "effective_context_policy",
            ),
            "effectiveHooks": {
                "compositionDigest": hook_digest,
                "blocked": hook_blocked,
            },
            "effectiveRetryPolicy": contract.effective_retry_policy.public_projection(),
            "conflicts": tuple(
                {
                    "code": _public_id(conflict.code, "code"),
                    "subjectRef": _public_id(conflict.subject_ref, "subject_ref"),
                    "recipeRefs": _recipe_ref_tuple(conflict.recipe_refs, "recipe_refs"),
                    "blocking": bool(conflict.blocking),
                }
                for conflict in contract.conflicts
            ),
            "blocked": bool(contract.blocked),
            "defaultOff": True,
            "trafficAttached": False,
            "executionAttached": False,
            "liveActivation": False,
        }

    @staticmethod
    def _assert_effective_digest_matches(contract: "EffectiveRecipeContract") -> None:
        registered_digest = _EFFECTIVE_CONTRACT_DIGESTS.digest_for(contract)
        if registered_digest != EffectiveRecipeContract._compute_effective_digest(contract):
            raise ValueError("effective digest mismatch")

    def public_projection(self) -> dict[str, object]:
        EffectiveRecipeContract._assert_effective_digest_matches(self)
        hook_projection: dict[str, object] | None = None
        if self.effective_hooks is not None:
            hook_projection = self.effective_hooks.public_projection()
        return {
            "schemaVersion": self.schema_version,
            "effectiveDigest": _safe_digest(self.effective_digest, "effective_digest"),
            "effectiveRecipeRefs": _recipe_ref_tuple(
                self.effective_recipe_refs,
                "effective_recipe_refs",
            ),
            "effectiveHardSafetyRefs": _recipe_ref_tuple(
                self.effective_hard_safety_refs,
                "effective_hard_safety_refs",
            ),
            "includedExplicitRefs": _recipe_ref_tuple(
                self.included_explicit_refs,
                "included_explicit_refs",
            ),
            "includedAutoRefs": _recipe_ref_tuple(
                self.included_auto_refs,
                "included_auto_refs",
            ),
            "excludedRefs": tuple(
                exclusion.public_projection() for exclusion in self.excluded_refs
            ),
            "blocked": bool(self.blocked),
            "conflictCount": len(self.conflicts),
            "conflicts": tuple(conflict.public_projection() for conflict in self.conflicts),
            "toolGrantCount": len(self.effective_tool_grants),
            "toolDenialCount": len(self.effective_tool_denials),
            "evidenceRequirementCount": len(self.effective_evidence_requirements),
            "approvalRequirementCount": len(self.effective_approval_requirements),
            "contextPolicyCount": len(self.effective_context_policy),
            "effectiveHooks": hook_projection,
            "effectiveRetryPolicy": self.effective_retry_policy.public_projection(),
            "defaultOff": True,
            "trafficAttached": False,
            "executionAttached": False,
            "liveActivation": False,
        }

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        return EffectiveRecipeContract.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            EffectiveRecipeContract.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_model(self) -> dict[str, object]:
        return EffectiveRecipeContract.public_projection(self)

    @field_serializer("effective_digest")
    def _serialize_effective_digest(self, value: object) -> str:
        EffectiveRecipeContract._assert_effective_digest_matches(self)
        return _safe_digest(value, "effective_digest")


def _conflict_from_admission(conflict: RecipeAdmissionConflict) -> EffectiveRecipeConflict:
    return EffectiveRecipeConflict(
        code=conflict.code,
        subjectRef=conflict.recipe_ref,
        recipeRefs=(conflict.recipe_ref,),
        blocking=True,
    )


def _conflict_from_merge(conflict: RecipeMergeConflict) -> EffectiveRecipeConflict:
    return EffectiveRecipeConflict(
        code=conflict.code,
        subjectRef=conflict.subject_ref,
        recipeRefs=conflict.recipe_refs,
        blocking=conflict.blocking,
    )


def _conflicts_from_merge_contract(
    merge_contract: EffectiveRecipeMergeContract,
) -> tuple[EffectiveRecipeConflict, ...]:
    return tuple(_conflict_from_merge(conflict) for conflict in merge_contract.conflicts)


def _conflict_from_hook(
    conflict: HookCompositionConflict,
    *,
    blocking: bool | None = None,
) -> EffectiveRecipeConflict:
    return EffectiveRecipeConflict(
        code=conflict.code,
        subjectRef=conflict.subject_ref,
        recipeRefs=conflict.recipe_refs,
        blocking=conflict.blocking if blocking is None else blocking,
    )


def _empty_retry_policy(global_retry_cap: int) -> RetryMergePolicy:
    return RetryMergePolicy(
        maxAttempts=0,
        repairAttempts=0,
        globalCap=max(global_retry_cap, 0),
    )


def _sorted_conflicts(
    conflicts: Iterable[EffectiveRecipeConflict],
) -> tuple[EffectiveRecipeConflict, ...]:
    unique = {
        (
            conflict.code,
            conflict.subject_ref,
            conflict.recipe_refs,
            conflict.blocking,
        ): conflict
        for conflict in conflicts
    }
    return tuple(
        sorted(
            unique.values(),
            key=lambda conflict: (
                not conflict.blocking,
                conflict.code,
                conflict.subject_ref,
                conflict.recipe_refs,
            ),
        )
    )


def _sorted_exclusions(
    exclusions: Iterable[EffectiveRecipeExclusion],
) -> tuple[EffectiveRecipeExclusion, ...]:
    return tuple(
        sorted(
            exclusions,
            key=lambda exclusion: (
                not exclusion.blocking,
                exclusion.reason,
                exclusion.recipe_ref,
            ),
        )
    )


def _build_contract(
    *,
    effective_recipe_refs: tuple[str, ...],
    included_explicit_refs: tuple[str, ...],
    included_auto_refs: tuple[str, ...],
    excluded_refs: tuple[EffectiveRecipeExclusion, ...],
    merge_contract: EffectiveRecipeMergeContract | None,
    hook_contract: EffectiveRecipeHookContract | None,
    conflicts: tuple[EffectiveRecipeConflict, ...],
    blocked: bool,
    global_retry_cap: int,
) -> EffectiveRecipeContract:
    if blocked:
        effective_recipe_refs = ()
        hard_safety_refs: tuple[str, ...] = ()
        included_explicit_refs = ()
        included_auto_refs = ()
        hook_contract = None
        tool_grants: tuple[str, ...] = ()
        tool_denials: tuple[str, ...] = ()
        evidence_requirements: tuple[str, ...] = ()
        approval_requirements: tuple[str, ...] = ()
        context_policy: tuple[str, ...] = ()
        retry_policy = _empty_retry_policy(global_retry_cap)
    elif merge_contract is None:
        hard_safety_refs = ()
        tool_grants = ()
        tool_denials = ()
        evidence_requirements = ()
        approval_requirements = ()
        context_policy = ()
        retry_policy = _empty_retry_policy(global_retry_cap)
    else:
        hard_safety_refs = merge_contract.hard_safety_refs
        tool_grants = merge_contract.tool_grants
        tool_denials = merge_contract.tool_denials
        evidence_requirements = merge_contract.evidence_requirements
        approval_requirements = merge_contract.approval_requirements
        context_policy = merge_contract.context_requirements
        retry_policy = merge_contract.retry_policy

    payload: dict[str, object] = {
        "schemaVersion": "effectiveRecipeContract.v1",
        "effectiveRecipeRefs": effective_recipe_refs,
        "effectiveHardSafetyRefs": hard_safety_refs,
        "includedExplicitRefs": included_explicit_refs,
        "includedAutoRefs": included_auto_refs,
        "excludedRefs": excluded_refs,
        "effectiveToolGrants": tool_grants,
        "effectiveToolDenials": tool_denials,
        "effectiveEvidenceRequirements": evidence_requirements,
        "effectiveApprovalRequirements": approval_requirements,
        "effectiveContextPolicy": context_policy,
        "effectiveHooks": hook_contract,
        "effectiveRetryPolicy": retry_policy,
        "conflicts": conflicts,
        "blocked": blocked,
        "defaultOff": True,
        "trafficAttached": False,
        "executionAttached": False,
        "liveActivation": False,
        "effectiveDigest": "sha256:" + "0" * 64,
    }
    draft = EffectiveRecipeContract._construct_for_digest(**payload)
    payload["effectiveDigest"] = EffectiveRecipeContract._compute_effective_digest(draft)
    return EffectiveRecipeContract.model_validate(payload)


def _snapshot_by_ref(
    snapshots: Iterable[AdmittedRecipeSnapshot],
) -> dict[str, AdmittedRecipeSnapshot]:
    result: dict[str, AdmittedRecipeSnapshot] = {}
    for snapshot in snapshots:
        if type(snapshot) is not AdmittedRecipeSnapshot:
            raise ValueError("admitted snapshots require registry-resolved instances")
        snapshot.public_projection()
        result[_recipe_ref(snapshot.recipe_ref, "recipe_ref")] = snapshot
    return result


def _non_auto_refs(stack: RecipeStackInput) -> tuple[str, ...]:
    return _dedupe_preserving_order(
        (
            *stack.explicit_recipe_refs,
            *stack.default_recipe_refs,
            *stack.plugin_recipe_refs,
            *stack.hard_safety_refs,
        )
    )


def _hook_recipe_refs(hook_contract: EffectiveRecipeHookContract | None) -> tuple[str, ...]:
    if hook_contract is None:
        return ()
    hook_contract.public_projection()
    refs: set[str] = set()
    for hook in hook_contract.hooks:
        refs.update(hook.recipe_refs)
    return _recipe_ref_tuple(refs, "hook_recipe_refs")


def _hook_ids_by_recipe(
    hook_contract: EffectiveRecipeHookContract | None,
) -> dict[str, set[str]]:
    if hook_contract is None:
        return {}
    hook_contract.public_projection()
    hook_ids_by_recipe: dict[str, set[str]] = {}
    for hook in hook_contract.hooks:
        hook_id = _public_id(hook.hook_id, "hook_id")
        for recipe_ref in hook.recipe_refs:
            hook_ids_by_recipe.setdefault(_recipe_ref(recipe_ref), set()).add(hook_id)
    return hook_ids_by_recipe


def _declared_hook_ids_by_recipe(
    snapshots: Iterable[AdmittedRecipeSnapshot],
) -> dict[str, set[str]]:
    declared: dict[str, set[str]] = {}
    for snapshot in snapshots:
        declared[_recipe_ref(snapshot.recipe_ref)] = {
            _public_id(hook_id, "hook_contributions")
            for hook_id in snapshot.hook_contributions
        }
    return declared


def _hook_contributions_by_recipe(
    contributions: Iterable[HookContribution],
) -> dict[str, tuple[HookContribution, ...]]:
    grouped: dict[str, list[HookContribution]] = {}
    for contribution in contributions:
        if type(contribution) is not HookContribution:
            raise ValueError("hook contributions require registry-resolved instances")
        contribution.public_projection()
        recipe_ref = _recipe_ref(contribution.recipe_ref, "recipe_ref")
        grouped.setdefault(recipe_ref, []).append(contribution)
    return {
        recipe_ref: tuple(items)
        for recipe_ref, items in sorted(grouped.items())
    }


def _compose_hook_contract_for_snapshots(
    snapshots: Iterable[AdmittedRecipeSnapshot],
    contributions_by_recipe: Mapping[str, tuple[HookContribution, ...]],
) -> EffectiveRecipeHookContract | None:
    recipe_refs = tuple(
        sorted({_recipe_ref(snapshot.recipe_ref, "recipe_ref") for snapshot in snapshots})
    )
    contributions = tuple(
        contribution
        for recipe_ref in recipe_refs
        for contribution in contributions_by_recipe.get(recipe_ref, ())
    )
    if not contributions:
        return None
    return compose_hook_contributions(contributions)


def _precomposed_auto_hook_refs(
    hook_contract: EffectiveRecipeHookContract | None,
    auto_recipe_refs: Iterable[str],
    snapshot_lookup: Mapping[str, AdmittedRecipeSnapshot],
) -> tuple[str, ...]:
    auto_ref_set = set(_recipe_ref_tuple(auto_recipe_refs, "auto_recipe_refs"))
    if not auto_ref_set:
        return ()
    contract_refs = set(_hook_recipe_refs(hook_contract)) if hook_contract is not None else set()
    if hook_contract is not None:
        for conflict in hook_contract.conflicts:
            contract_refs.update(_recipe_ref_tuple(conflict.recipe_refs, "recipe_refs"))
    for auto_ref in auto_ref_set:
        snapshot = snapshot_lookup.get(auto_ref)
        if snapshot is not None and snapshot.hook_contributions:
            contract_refs.add(auto_ref)
    return tuple(sorted(contract_refs & auto_ref_set))


def _evaluate_hook_contract(
    hook_contract: EffectiveRecipeHookContract | None,
    *,
    effective_refs: tuple[str, ...],
    included_snapshots: Iterable[AdmittedRecipeSnapshot],
    blocking_override: bool | None = None,
) -> tuple[bool, tuple[EffectiveRecipeConflict, ...]]:
    if hook_contract is None:
        return False, ()

    def _blocking(value: bool) -> bool:
        return value if blocking_override is None else blocking_override

    hook_projection = hook_contract.public_projection()
    hook_blocked = bool(hook_projection["blocked"])
    conflicts: list[EffectiveRecipeConflict] = []
    if hook_blocked:
        conflicts.append(
            EffectiveRecipeConflict(
                code="hook_contract_blocked",
                subjectRef="effective.hooks",
                recipeRefs=effective_refs,
                blocking=_blocking(True),
            )
        )
        conflicts.extend(
            _conflict_from_hook(conflict, blocking=_blocking(conflict.blocking))
            for conflict in hook_contract.conflicts
        )

    hook_refs = set(_hook_recipe_refs(hook_contract))
    effective_ref_set = set(effective_refs)
    out_of_scope_hook_refs = tuple(sorted(hook_refs - effective_ref_set))
    if out_of_scope_hook_refs:
        hook_blocked = True
        conflicts.append(
            EffectiveRecipeConflict(
                code="hook_recipe_scope_violation",
                subjectRef="effective.hooks",
                recipeRefs=out_of_scope_hook_refs,
                blocking=_blocking(True),
            )
        )

    declared_hook_ids = _declared_hook_ids_by_recipe(included_snapshots)
    for recipe_ref, hook_ids in sorted(_hook_ids_by_recipe(hook_contract).items()):
        if recipe_ref not in effective_ref_set:
            continue
        undeclared_hook_ids = tuple(
            sorted(hook_ids - declared_hook_ids.get(recipe_ref, set()))
        )
        for hook_id in undeclared_hook_ids:
            hook_blocked = True
            conflicts.append(
                EffectiveRecipeConflict(
                    code="hook_contribution_not_declared",
                    subjectRef=hook_id,
                    recipeRefs=(recipe_ref,),
                    blocking=_blocking(True),
                )
            )
    return hook_blocked, tuple(conflicts)


def _missing_declared_hook_conflicts(
    hook_contract: EffectiveRecipeHookContract | None,
    *,
    included_snapshots: Iterable[AdmittedRecipeSnapshot],
    blocking_override: bool | None = None,
) -> tuple[EffectiveRecipeConflict, ...]:
    def _blocking() -> bool:
        return True if blocking_override is None else blocking_override

    contributed_by_recipe = _hook_ids_by_recipe(hook_contract)
    if hook_contract is not None:
        for conflict in hook_contract.conflicts:
            hook_ids = _public_id_tuple(conflict.hook_ids, "hook_ids")
            for recipe_ref in conflict.recipe_refs:
                contributed_by_recipe.setdefault(_recipe_ref(recipe_ref), set()).update(hook_ids)
    conflicts: list[EffectiveRecipeConflict] = []
    for recipe_ref, declared_hook_ids in sorted(
        _declared_hook_ids_by_recipe(included_snapshots).items()
    ):
        contributed_hook_ids = contributed_by_recipe.get(recipe_ref, set())
        for hook_id in sorted(declared_hook_ids - contributed_hook_ids):
            conflicts.append(
                EffectiveRecipeConflict(
                    code="declared_hook_contribution_missing",
                    subjectRef=hook_id,
                    recipeRefs=(recipe_ref,),
                    blocking=_blocking(),
                )
            )
    return tuple(conflicts)


def build_effective_recipe_contract(
    *,
    stack: RecipeStackInput,
    admitted_snapshots: Iterable[AdmittedRecipeSnapshot],
    hook_contract: EffectiveRecipeHookContract | None = None,
    hook_contributions: Iterable[HookContribution] | None = None,
    auto_conflict_policy: Literal["exclude", "block"] = "exclude",
    global_retry_cap: int = 3,
    required_governed_recipe_refs: Iterable[str] = (),
) -> EffectiveRecipeContract:
    if type(stack) is not RecipeStackInput:
        raise ValueError("effective contract stack requires RecipeStackInput")
    stack.public_projection()
    if auto_conflict_policy not in {"exclude", "block"}:
        raise ValueError("auto_conflict_policy must be exclude or block")
    if global_retry_cap < 0:
        raise ValueError("global_retry_cap must be non-negative")
    if hook_contract is not None and hook_contributions is not None:
        raise ValueError("provide hook_contract or hook_contributions, not both")

    snapshots = tuple(admitted_snapshots)
    hook_contributions_by_ref: dict[str, tuple[HookContribution, ...]] | None = None
    if hook_contributions is not None:
        raw_hook_contributions = tuple(hook_contributions)
        compose_hook_contributions(raw_hook_contributions)
        hook_contributions_by_ref = _hook_contributions_by_recipe(raw_hook_contributions)
    required_governed_refs = _recipe_ref_tuple(
        required_governed_recipe_refs,
        "required_governed_recipe_refs",
    )
    admission = admit_recipe_stack(
        RecipeAdmissionRequest(
            stack=stack,
            admittedSnapshots=snapshots,
            requiredGovernedRecipeRefs=required_governed_refs,
        )
    )
    snapshot_lookup = _snapshot_by_ref(snapshots)
    exclusions: list[EffectiveRecipeExclusion] = [
        EffectiveRecipeExclusion(
            recipeRef=recipe_ref,
            reason="explicit_recipe_missing",
            blocking=True,
        )
        for recipe_ref in admission.missing_explicit_refs
    ]
    exclusions.extend(
        EffectiveRecipeExclusion(
            recipeRef=recipe_ref,
            reason="required_recipe_missing",
            blocking=True,
        )
        for recipe_ref in admission.missing_required_refs
    )
    exclusions.extend(
        EffectiveRecipeExclusion(
            recipeRef=recipe_ref,
            reason="auto_recipe_missing",
            blocking=False,
        )
        for recipe_ref in admission.rejected_auto_refs
    )
    conflicts: list[EffectiveRecipeConflict] = [
        _conflict_from_admission(conflict) for conflict in admission.conflicts
    ]

    if admission.blocked:
        return _build_contract(
            effective_recipe_refs=(),
            included_explicit_refs=(),
            included_auto_refs=(),
            excluded_refs=_sorted_exclusions(exclusions),
            merge_contract=None,
            hook_contract=hook_contract,
            conflicts=_sorted_conflicts(conflicts),
            blocked=True,
            global_retry_cap=global_retry_cap,
        )

    mandatory_refs = _non_auto_refs(stack)
    included_snapshots: list[AdmittedRecipeSnapshot] = [
        snapshot_lookup[recipe_ref]
        for recipe_ref in mandatory_refs
        if recipe_ref in snapshot_lookup and recipe_ref in admission.admitted_recipe_refs
    ]
    merge_contract = merge_admitted_recipe_snapshots(
        included_snapshots,
        global_retry_cap=global_retry_cap,
    )
    conflicts.extend(_conflicts_from_merge_contract(merge_contract))
    if merge_contract.blocked:
        return _build_contract(
            effective_recipe_refs=(),
            included_explicit_refs=(),
            included_auto_refs=(),
            excluded_refs=_sorted_exclusions(exclusions),
            merge_contract=merge_contract,
            hook_contract=hook_contract,
            conflicts=_sorted_conflicts(conflicts),
            blocked=True,
            global_retry_cap=global_retry_cap,
        )

    precomposed_auto_hook_refs = ()
    if hook_contributions_by_ref is None:
        missing_mandatory_hook_conflicts = _missing_declared_hook_conflicts(
            hook_contract,
            included_snapshots=included_snapshots,
        )
        if missing_mandatory_hook_conflicts:
            conflicts.extend(missing_mandatory_hook_conflicts)
            return _build_contract(
                effective_recipe_refs=(),
                included_explicit_refs=(),
                included_auto_refs=(),
                excluded_refs=_sorted_exclusions(exclusions),
                merge_contract=merge_contract,
                hook_contract=hook_contract,
                conflicts=_sorted_conflicts(conflicts),
                blocked=True,
                global_retry_cap=global_retry_cap,
            )
        precomposed_auto_hook_refs = _precomposed_auto_hook_refs(
            hook_contract,
            (
                auto_ref
                for auto_ref in stack.auto_recipe_refs
                if auto_ref in admission.admitted_recipe_refs
            ),
            snapshot_lookup,
        )
    if precomposed_auto_hook_refs:
        conflicts.append(
            EffectiveRecipeConflict(
                code="recipe_scoped_hook_contributions_required",
                subjectRef="effective.hooks",
                recipeRefs=precomposed_auto_hook_refs,
                blocking=True,
            )
        )
        if hook_contract is not None:
            conflicts.extend(
                _conflict_from_hook(conflict)
                for conflict in hook_contract.conflicts
            )
        return _build_contract(
            effective_recipe_refs=(),
            included_explicit_refs=(),
            included_auto_refs=(),
            excluded_refs=_sorted_exclusions(exclusions),
            merge_contract=merge_contract,
            hook_contract=hook_contract,
            conflicts=_sorted_conflicts(conflicts),
            blocked=True,
            global_retry_cap=global_retry_cap,
        )

    active_hook_contract = hook_contract
    if hook_contributions_by_ref is not None:
        active_hook_contract = _compose_hook_contract_for_snapshots(
            included_snapshots,
            hook_contributions_by_ref,
        )
        hook_blocked, hook_conflicts = _evaluate_hook_contract(
            active_hook_contract,
            effective_refs=merge_contract.recipe_refs,
            included_snapshots=included_snapshots,
        )
        conflicts.extend(hook_conflicts)
        missing_hook_conflicts = _missing_declared_hook_conflicts(
            active_hook_contract,
            included_snapshots=included_snapshots,
        )
        conflicts.extend(missing_hook_conflicts)
        if missing_hook_conflicts:
            hook_blocked = True
        if hook_blocked:
            return _build_contract(
                effective_recipe_refs=(),
                included_explicit_refs=(),
                included_auto_refs=(),
                excluded_refs=_sorted_exclusions(exclusions),
                merge_contract=merge_contract,
                hook_contract=active_hook_contract,
                conflicts=_sorted_conflicts(conflicts),
                blocked=True,
                global_retry_cap=global_retry_cap,
            )

    included_auto_refs: list[str] = []
    for auto_ref in stack.auto_recipe_refs:
        if auto_ref not in admission.admitted_recipe_refs or auto_ref in mandatory_refs:
            continue
        auto_snapshot = snapshot_lookup[auto_ref]
        if required_governed_refs and not auto_snapshot.governed:
            exclusions.append(
                EffectiveRecipeExclusion(
                    recipeRef=auto_ref,
                    reason="auto_recipe_ungoverned_for_required_governance",
                    blocking=True,
                )
            )
            conflicts.append(
                EffectiveRecipeConflict(
                    code="auto_recipe_ungoverned_for_required_governance",
                    subjectRef=auto_ref,
                    recipeRefs=(auto_ref,),
                    blocking=True,
                )
            )
            return _build_contract(
                effective_recipe_refs=(),
                included_explicit_refs=(),
                included_auto_refs=(),
                excluded_refs=_sorted_exclusions(exclusions),
                merge_contract=merge_contract,
                hook_contract=active_hook_contract,
                conflicts=_sorted_conflicts(conflicts),
                blocked=True,
                global_retry_cap=global_retry_cap,
            )
        trial_snapshots = (*included_snapshots, auto_snapshot)
        trial_merge = merge_admitted_recipe_snapshots(
            trial_snapshots,
            global_retry_cap=global_retry_cap,
        )
        if trial_merge.blocked:
            blocking = auto_conflict_policy == "block"
            exclusions.append(
                EffectiveRecipeExclusion(
                    recipeRef=auto_ref,
                    reason="auto_recipe_incompatible",
                    blocking=blocking,
                )
            )
            conflicts.append(
                EffectiveRecipeConflict(
                    code="auto_recipe_incompatible",
                    subjectRef=auto_ref,
                    recipeRefs=(auto_ref,),
                    blocking=blocking,
                )
            )
            if blocking:
                conflicts.extend(_conflicts_from_merge_contract(trial_merge))
                return _build_contract(
                    effective_recipe_refs=(),
                    included_explicit_refs=(),
                    included_auto_refs=(),
                    excluded_refs=_sorted_exclusions(exclusions),
                    merge_contract=trial_merge,
                    hook_contract=hook_contract,
                    conflicts=_sorted_conflicts(conflicts),
                    blocked=True,
                    global_retry_cap=global_retry_cap,
                )
            continue
        if hook_contributions_by_ref is not None:
            trial_hook_contract = _compose_hook_contract_for_snapshots(
                trial_snapshots,
                hook_contributions_by_ref,
            )
            blocking = auto_conflict_policy == "block"
            trial_hook_blocked, trial_hook_conflicts = _evaluate_hook_contract(
                trial_hook_contract,
                effective_refs=trial_merge.recipe_refs,
                included_snapshots=trial_snapshots,
                blocking_override=blocking,
            )
            missing_hook_conflicts = _missing_declared_hook_conflicts(
                trial_hook_contract,
                included_snapshots=trial_snapshots,
                blocking_override=blocking,
            )
            trial_hook_conflicts = (*trial_hook_conflicts, *missing_hook_conflicts)
            if missing_hook_conflicts:
                trial_hook_blocked = True
            if trial_hook_blocked:
                exclusions.append(
                    EffectiveRecipeExclusion(
                        recipeRef=auto_ref,
                        reason="auto_recipe_incompatible",
                        blocking=blocking,
                    )
                )
                conflicts.append(
                    EffectiveRecipeConflict(
                        code="auto_recipe_incompatible",
                        subjectRef=auto_ref,
                        recipeRefs=(auto_ref,),
                        blocking=blocking,
                    )
                )
                conflicts.extend(trial_hook_conflicts)
                if blocking:
                    return _build_contract(
                        effective_recipe_refs=(),
                        included_explicit_refs=(),
                        included_auto_refs=(),
                        excluded_refs=_sorted_exclusions(exclusions),
                        merge_contract=trial_merge,
                        hook_contract=trial_hook_contract,
                        conflicts=_sorted_conflicts(conflicts),
                        blocked=True,
                        global_retry_cap=global_retry_cap,
                    )
                continue
            active_hook_contract = trial_hook_contract
        included_snapshots.append(auto_snapshot)
        included_auto_refs.append(auto_ref)
        merge_contract = trial_merge
        conflicts.extend(_conflicts_from_merge_contract(trial_merge))

    effective_refs = merge_contract.recipe_refs
    hook_blocked, hook_conflicts = _evaluate_hook_contract(
        active_hook_contract,
        effective_refs=effective_refs,
        included_snapshots=included_snapshots,
    )
    conflicts.extend(hook_conflicts)
    if hook_contributions_by_ref is not None:
        missing_hook_conflicts = _missing_declared_hook_conflicts(
            active_hook_contract,
            included_snapshots=included_snapshots,
        )
        conflicts.extend(missing_hook_conflicts)
        if missing_hook_conflicts:
            hook_blocked = True
    effective_hook_contract = None if hook_blocked else active_hook_contract

    included_explicit_refs = tuple(
        recipe_ref for recipe_ref in stack.explicit_recipe_refs if recipe_ref in effective_refs
    )
    return _build_contract(
        effective_recipe_refs=effective_refs,
        included_explicit_refs=included_explicit_refs,
        included_auto_refs=tuple(included_auto_refs),
        excluded_refs=_sorted_exclusions(exclusions),
        merge_contract=merge_contract,
        hook_contract=effective_hook_contract,
        conflicts=_sorted_conflicts(conflicts),
        blocked=merge_contract.blocked or hook_blocked,
        global_retry_cap=global_retry_cap,
    )


__all__ = (
    "EffectiveRecipeConflict",
    "EffectiveRecipeContract",
    "EffectiveRecipeExclusion",
    "build_effective_recipe_contract",
)
