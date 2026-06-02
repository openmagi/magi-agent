from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
import re
from typing import Any, Literal, NamedTuple, Self
import weakref

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictBool,
    StrictInt,
    StrictStr,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

from magi_agent.recipes.composition import AdmittedRecipeSnapshot


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
_EVIDENCE_STRICTNESS = {
    "optional": (0, "optional"),
    "warn": (0, "optional"),
    "required": (1, "required"),
    "require": (1, "required"),
    "verified": (2, "verified"),
    "blocking": (3, "blocking"),
    "block": (3, "blocking"),
}
_CONTEXT_PRIVILEGE = {
    "full": (0, "full"),
    "summary": (1, "summary"),
    "refs_only": (2, "refs_only"),
    "metadata_only": (2, "refs_only"),
    "none": (3, "none"),
}
_HARD_SAFETY_WEAK_MODES = {"log_only", "disabled"}


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


_MERGE_RESULT_DIGESTS = _IdentityDigestRegistry()


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


def _sha256_public(value: str) -> str:
    encoded = value.encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _canonical_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class RecipeMergeConflict(BaseModel):
    model_config = _MODEL_CONFIG

    code: StrictStr
    subject_ref: StrictStr = Field(alias="subjectRef")
    recipe_refs: tuple[str, ...] = Field(default=(), alias="recipeRefs")
    blocking: StrictBool

    @field_validator("code")
    @classmethod
    def _sanitize_code(cls, value: object) -> str:
        return _public_id(value, "code")

    @field_validator("subject_ref")
    @classmethod
    def _sanitize_subject_ref(cls, value: object) -> str:
        return _public_id(value, "subject_ref")

    @field_validator("recipe_refs", mode="before")
    @classmethod
    def _sanitize_recipe_refs(cls, value: object) -> tuple[str, ...]:
        return _recipe_ref_tuple(value, "recipe_refs")

    def public_projection(self) -> dict[str, object]:
        return {
            "code": _public_id(self.code, "code"),
            "blocking": bool(self.blocking),
            "subjectDigest": _sha256_public(_public_id(self.subject_ref, "subject_ref")),
            "recipeRefCount": len(_recipe_ref_tuple(self.recipe_refs, "recipe_refs")),
        }

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        return RecipeMergeConflict.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            RecipeMergeConflict.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_model(self) -> dict[str, object]:
        return RecipeMergeConflict.public_projection(self)


class RetryMergePolicy(BaseModel):
    model_config = _MODEL_CONFIG

    max_attempts: StrictInt = Field(alias="maxAttempts")
    repair_attempts: StrictInt = Field(alias="repairAttempts")
    global_cap: StrictInt = Field(alias="globalCap")

    @field_validator("max_attempts", "repair_attempts", "global_cap")
    @classmethod
    def _sanitize_non_negative_int(cls, value: int, info: Any) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{info.field_name} must be a non-negative integer")
        return value

    @model_validator(mode="after")
    def _validate_bounds(self) -> Self:
        if self.global_cap < self.max_attempts:
            raise ValueError("retry max attempts exceed global cap")
        if self.repair_attempts > self.global_cap:
            raise ValueError("repair attempts exceed global cap")
        if self.repair_attempts > self.max_attempts:
            raise ValueError("repair attempts exceed retry max attempts")
        return self

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> "RetryMergePolicy":
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(update)
        return RetryMergePolicy.model_validate(data)

    def public_projection(self) -> dict[str, int]:
        RetryMergePolicy.model_validate(
            self.model_dump(by_alias=True, mode="python", warnings=False)
        )
        return {
            "maxAttempts": self.max_attempts,
            "repairAttempts": self.repair_attempts,
            "globalCap": self.global_cap,
        }


class EffectiveRecipeMergeContract(BaseModel):
    model_config = _MODEL_CONFIG

    _validated_merge_digest: str = PrivateAttr(default="")

    schema_version: Literal["recipeMergeAlgebra.v1"] = Field(
        default="recipeMergeAlgebra.v1",
        alias="schemaVersion",
    )
    recipe_refs: tuple[str, ...] = Field(alias="recipeRefs")
    hard_safety_refs: tuple[str, ...] = Field(alias="hardSafetyRefs")
    hard_safety_mode: Literal["none", "enforce"] = Field(alias="hardSafetyMode")
    tool_grants: tuple[str, ...] = Field(alias="toolGrants")
    tool_denials: tuple[str, ...] = Field(alias="toolDenials")
    approval_requirements: tuple[str, ...] = Field(alias="approvalRequirements")
    evidence_requirements: tuple[str, ...] = Field(alias="evidenceRequirements")
    context_requirements: tuple[str, ...] = Field(alias="contextRequirements")
    retry_policy: RetryMergePolicy = Field(alias="retryPolicy")
    conflicts: tuple[RecipeMergeConflict, ...] = Field(default=())
    blocked: StrictBool
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    live_activation: Literal[False] = Field(default=False, alias="liveActivation")
    merge_digest: StrictStr = Field(alias="mergeDigest")

    @field_validator("recipe_refs", "hard_safety_refs", mode="before")
    @classmethod
    def _sanitize_recipe_ref_section(cls, value: object) -> tuple[str, ...]:
        return _recipe_ref_tuple(value, "recipe_refs")

    @field_validator(
        "tool_grants",
        "tool_denials",
        "approval_requirements",
        "evidence_requirements",
        "context_requirements",
        mode="before",
    )
    @classmethod
    def _sanitize_public_ref_section(cls, value: object, info: Any) -> tuple[str, ...]:
        return _public_id_tuple(value, info.field_name)

    @field_validator("merge_digest", mode="before")
    @classmethod
    def _sanitize_merge_digest(cls, value: object) -> str:
        return _safe_digest(value, "merge_digest")

    @field_validator("conflicts", mode="before")
    @classmethod
    def _sanitize_conflicts(cls, value: object) -> tuple[RecipeMergeConflict, ...]:
        if value is None:
            return ()
        conflicts: list[RecipeMergeConflict] = []
        for conflict in _tuple_values(value, "conflicts"):
            if type(conflict) is not RecipeMergeConflict:
                raise ValueError("merge conflicts require structured conflict instances")
            conflicts.append(conflict)
        return tuple(conflicts)

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls.model_validate(cls._canonical_authority_payload(values))

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

    @model_validator(mode="after")
    def _validate_merge_contract(self) -> Self:
        if self.blocked and self.tool_grants:
            raise ValueError("blocked merge contract cannot carry tool grants")
        if bool(self.hard_safety_refs) != (self.hard_safety_mode == "enforce"):
            raise ValueError("hard safety refs require enforce mode")
        if self.merge_digest != EffectiveRecipeMergeContract._compute_merge_digest(self):
            raise ValueError("merge digest mismatch")
        return self

    def model_post_init(self, __context: Any) -> None:
        self._validated_merge_digest = EffectiveRecipeMergeContract._compute_merge_digest(self)
        _MERGE_RESULT_DIGESTS.mark(
            self,
            self._validated_merge_digest,
            "merge_digest",
        )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> "EffectiveRecipeMergeContract":
        EffectiveRecipeMergeContract._assert_merge_digest_matches(self)
        if update:
            raise ValueError("merge contract authority fields are immutable")
        return EffectiveRecipeMergeContract.model_validate(
            {
                "schemaVersion": self.schema_version,
                "recipeRefs": self.recipe_refs,
                "hardSafetyRefs": self.hard_safety_refs,
                "hardSafetyMode": self.hard_safety_mode,
                "toolGrants": self.tool_grants,
                "toolDenials": self.tool_denials,
                "approvalRequirements": self.approval_requirements,
                "evidenceRequirements": self.evidence_requirements,
                "contextRequirements": self.context_requirements,
                "retryPolicy": self.retry_policy,
                "conflicts": self.conflicts,
                "blocked": self.blocked,
                "defaultOff": True,
                "trafficAttached": False,
                "executionAttached": False,
                "liveActivation": False,
                "mergeDigest": self.merge_digest,
            }
        )

    @staticmethod
    def _compute_merge_digest(contract: "EffectiveRecipeMergeContract") -> str:
        return _canonical_digest(EffectiveRecipeMergeContract._digest_payload(contract))

    @staticmethod
    def _digest_payload(contract: "EffectiveRecipeMergeContract") -> dict[str, object]:
        return EffectiveRecipeMergeContract._digest_payload_from_values(
            schema_version=contract.schema_version,
            recipe_refs=contract.recipe_refs,
            hard_safety_refs=contract.hard_safety_refs,
            hard_safety_mode=contract.hard_safety_mode,
            tool_grants=contract.tool_grants,
            tool_denials=contract.tool_denials,
            approval_requirements=contract.approval_requirements,
            evidence_requirements=contract.evidence_requirements,
            context_requirements=contract.context_requirements,
            retry_policy=contract.retry_policy,
            conflicts=contract.conflicts,
            blocked=contract.blocked,
        )

    @staticmethod
    def _digest_payload_from_values(
        *,
        schema_version: str,
        recipe_refs: Iterable[str],
        hard_safety_refs: Iterable[str],
        hard_safety_mode: str,
        tool_grants: Iterable[str],
        tool_denials: Iterable[str],
        approval_requirements: Iterable[str],
        evidence_requirements: Iterable[str],
        context_requirements: Iterable[str],
        retry_policy: RetryMergePolicy,
        conflicts: Iterable[RecipeMergeConflict],
        blocked: bool,
    ) -> dict[str, object]:
        return {
            "schemaVersion": _public_id(schema_version, "schema_version"),
            "recipeRefs": _recipe_ref_tuple(recipe_refs, "recipe_refs"),
            "hardSafetyRefs": _recipe_ref_tuple(
                hard_safety_refs,
                "hard_safety_refs",
            ),
            "hardSafetyMode": _public_id(hard_safety_mode, "hard_safety_mode"),
            "toolGrants": _public_id_tuple(tool_grants, "tool_grants"),
            "toolDenials": _public_id_tuple(tool_denials, "tool_denials"),
            "approvalRequirements": _public_id_tuple(
                approval_requirements,
                "approval_requirements",
            ),
            "evidenceRequirements": _public_id_tuple(
                evidence_requirements,
                "evidence_requirements",
            ),
            "contextRequirements": _public_id_tuple(
                context_requirements,
                "context_requirements",
            ),
            "retryPolicy": retry_policy.public_projection(),
            "conflicts": tuple(
                {
                    "code": _public_id(conflict.code, "code"),
                    "subjectRef": _public_id(conflict.subject_ref, "subject_ref"),
                    "recipeRefs": _recipe_ref_tuple(conflict.recipe_refs, "recipe_refs"),
                    "blocking": bool(conflict.blocking),
                }
                for conflict in conflicts
            ),
            "blocked": bool(blocked),
            "defaultOff": True,
            "trafficAttached": False,
            "executionAttached": False,
            "liveActivation": False,
        }

    @staticmethod
    def _assert_merge_digest_matches(contract: "EffectiveRecipeMergeContract") -> None:
        registered_digest = _MERGE_RESULT_DIGESTS.digest_for(contract)
        computed_digest = EffectiveRecipeMergeContract._compute_merge_digest(contract)
        if registered_digest != computed_digest or contract.merge_digest != computed_digest:
            raise ValueError("merge digest mismatch")

    def public_projection(self) -> dict[str, object]:
        EffectiveRecipeMergeContract._assert_merge_digest_matches(self)
        return {
            "schemaVersion": self.schema_version,
            "mergeDigest": _safe_digest(self.merge_digest, "merge_digest"),
            "recipeRefs": _recipe_ref_tuple(self.recipe_refs, "recipe_refs"),
            "hardSafetyRecipeRefs": _recipe_ref_tuple(
                self.hard_safety_refs,
                "hard_safety_refs",
            ),
            "hardSafetyMode": self.hard_safety_mode,
            "blocked": bool(self.blocked),
            "conflictCount": len(self.conflicts),
            "conflicts": tuple(conflict.public_projection() for conflict in self.conflicts),
            "toolGrantCount": len(self.tool_grants),
            "toolDenialCount": len(self.tool_denials),
            "approvalRequirementCount": len(self.approval_requirements),
            "evidenceRequirementCount": len(self.evidence_requirements),
            "contextRequirementCount": len(self.context_requirements),
            "retryPolicy": self.retry_policy.public_projection(),
            "defaultOff": True,
            "trafficAttached": False,
            "executionAttached": False,
            "liveActivation": False,
        }

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        return EffectiveRecipeMergeContract.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            EffectiveRecipeMergeContract.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_model(self) -> dict[str, object]:
        return EffectiveRecipeMergeContract.public_projection(self)

    @field_serializer("merge_digest")
    def _serialize_merge_digest(self, value: object) -> str:
        EffectiveRecipeMergeContract._assert_merge_digest_matches(self)
        return _safe_digest(value, "merge_digest")


class _ParsedRetry(NamedTuple):
    max_attempts: int
    repair_attempts: int | None
    conflict: RecipeMergeConflict | None


def _require_snapshot(snapshot: object) -> AdmittedRecipeSnapshot:
    if type(snapshot) is not AdmittedRecipeSnapshot:
        raise ValueError("admitted snapshots require registry-resolved instances")
    AdmittedRecipeSnapshot._assert_registry_admitted(snapshot)
    return snapshot


def _snapshot_recipe_refs_by_subject(
    snapshots: tuple[AdmittedRecipeSnapshot, ...],
    attribute: str,
) -> dict[str, tuple[str, ...]]:
    refs: dict[str, set[str]] = {}
    for snapshot in snapshots:
        for subject_ref in getattr(snapshot, attribute):
            refs.setdefault(_public_id(subject_ref, attribute), set()).add(snapshot.recipe_ref)
    return {subject_ref: tuple(sorted(recipe_refs)) for subject_ref, recipe_refs in refs.items()}


def _policy_requirement(
    value: str,
    levels: Mapping[str, tuple[int, str]],
    default_level: str,
    field_name: str,
) -> tuple[str, int, str, bool]:
    public_value = _public_id(value, field_name)
    if ":" not in public_value:
        level_value, canonical = levels[default_level]
        return public_value, level_value, canonical, True
    policy_class, raw_level = public_value.rsplit(":", 1)
    level_entry = levels.get(raw_level)
    if level_entry is None:
        level_value, canonical = max(levels.values(), key=lambda level: level[0])
        return public_value, level_value, canonical, False
    _public_id(policy_class, field_name)
    return policy_class, level_entry[0], level_entry[1], True


def _merge_snapshot_policy_requirements(
    snapshots: tuple[AdmittedRecipeSnapshot, ...],
    *,
    attribute: str,
    levels: Mapping[str, tuple[int, str]],
    default_level: str,
    field_name: str,
    conflict_code: str,
) -> tuple[tuple[str, ...], tuple[RecipeMergeConflict, ...]]:
    merged: dict[str, tuple[int, str]] = {}
    conflicts: list[RecipeMergeConflict] = []
    for snapshot in snapshots:
        for value in getattr(snapshot, attribute):
            policy_class, level_value, canonical_level, recognized = _policy_requirement(
                value,
                levels,
                default_level,
                field_name,
            )
            if not recognized:
                conflicts.append(
                    RecipeMergeConflict(
                        code=conflict_code,
                        subjectRef=policy_class,
                        recipeRefs=(snapshot.recipe_ref,),
                        blocking=True,
                    )
                )
            existing = merged.get(policy_class)
            if existing is None or level_value > existing[0]:
                merged[policy_class] = (level_value, canonical_level)
    requirements = tuple(
        f"{policy_class}:{level}"
        for policy_class, (_level_value, level) in sorted(merged.items())
    )
    return requirements, tuple(conflicts)


def _hard_safety_mode_rule(rule: str) -> str | None:
    public_rule = _public_id(rule, "projection_rules")
    if ":" not in public_rule:
        return None
    subject, mode = public_rule.rsplit(":", 1)
    if subject != "hardSafety.mode":
        return None
    return mode


def _parse_retry_policy(
    snapshot: AdmittedRecipeSnapshot,
    global_retry_cap: int,
) -> _ParsedRetry:
    retry_policy = _public_id(snapshot.retry_policy, "retry_policy")
    if retry_policy == "none":
        return _ParsedRetry(max_attempts=0, repair_attempts=0, conflict=None)
    tokens = retry_policy.split(":")
    if tokens == ["retry", "unbounded"]:
        return _ParsedRetry(
            max_attempts=0,
            repair_attempts=0,
            conflict=RecipeMergeConflict(
                code="retry_policy_unbounded",
                subjectRef="retry.policy",
                recipeRefs=(snapshot.recipe_ref,),
                blocking=True,
            ),
        )
    if len(tokens) not in {3, 5} or tokens[:2] != ["retry", "max"] or not tokens[2].isdigit():
        return _ParsedRetry(
            max_attempts=0,
            repair_attempts=0,
            conflict=RecipeMergeConflict(
                code="retry_policy_unrecognized",
                subjectRef="retry.policy",
                recipeRefs=(snapshot.recipe_ref,),
                blocking=True,
            ),
        )
    max_attempts = min(int(tokens[2]), global_retry_cap)
    repair_attempts: int | None = None
    if len(tokens) == 5:
        if tokens[3] != "repair" or not tokens[4].isdigit():
            return _ParsedRetry(
                max_attempts=0,
                repair_attempts=0,
                conflict=RecipeMergeConflict(
                    code="retry_policy_unrecognized",
                    subjectRef="retry.policy",
                    recipeRefs=(snapshot.recipe_ref,),
                    blocking=True,
                ),
            )
        repair_attempts = min(int(tokens[4]), global_retry_cap, max_attempts)
    return _ParsedRetry(
        max_attempts=max_attempts,
        repair_attempts=repair_attempts,
        conflict=None,
    )


def _merged_retry_policy(
    snapshots: tuple[AdmittedRecipeSnapshot, ...],
    global_retry_cap: int,
) -> tuple[RetryMergePolicy, tuple[RecipeMergeConflict, ...]]:
    if global_retry_cap < 0:
        raise ValueError("global_retry_cap must be non-negative")
    max_caps: list[int] = []
    repair_caps: list[int] = []
    conflicts: list[RecipeMergeConflict] = []
    for snapshot in snapshots:
        parsed = _parse_retry_policy(snapshot, global_retry_cap)
        max_caps.append(parsed.max_attempts)
        if parsed.repair_attempts is not None:
            repair_caps.append(parsed.repair_attempts)
        if parsed.conflict is not None:
            conflicts.append(parsed.conflict)
    max_attempts = min(max_caps) if max_caps else 0
    repair_attempts = min(repair_caps) if repair_caps else 0
    repair_attempts = min(repair_attempts, global_retry_cap, max_attempts)
    return (
        RetryMergePolicy(
            maxAttempts=max_attempts,
            repairAttempts=repair_attempts,
            globalCap=global_retry_cap,
        ),
        tuple(conflicts),
    )


def _sorted_conflicts(conflicts: Iterable[RecipeMergeConflict]) -> tuple[RecipeMergeConflict, ...]:
    return tuple(
        sorted(
            conflicts,
            key=lambda conflict: (
                not conflict.blocking,
                conflict.code,
                conflict.subject_ref,
                conflict.recipe_refs,
            ),
        )
    )


def _build_merge_contract(
    *,
    recipe_refs: tuple[str, ...],
    hard_safety_refs: tuple[str, ...],
    hard_safety_mode: Literal["none", "enforce"],
    tool_grants: tuple[str, ...],
    tool_denials: tuple[str, ...],
    approval_requirements: tuple[str, ...],
    evidence_requirements: tuple[str, ...],
    context_requirements: tuple[str, ...],
    retry_policy: RetryMergePolicy,
    conflicts: tuple[RecipeMergeConflict, ...],
    blocked: bool,
) -> EffectiveRecipeMergeContract:
    payload = {
        "schemaVersion": "recipeMergeAlgebra.v1",
        "recipeRefs": recipe_refs,
        "hardSafetyRefs": hard_safety_refs,
        "hardSafetyMode": hard_safety_mode,
        "toolGrants": () if blocked else tool_grants,
        "toolDenials": tool_denials,
        "approvalRequirements": approval_requirements,
        "evidenceRequirements": evidence_requirements,
        "contextRequirements": context_requirements,
        "retryPolicy": retry_policy,
        "conflicts": conflicts,
        "blocked": blocked,
        "defaultOff": True,
        "trafficAttached": False,
        "executionAttached": False,
        "liveActivation": False,
    }
    payload["mergeDigest"] = _canonical_digest(
        EffectiveRecipeMergeContract._digest_payload_from_values(
            schema_version="recipeMergeAlgebra.v1",
            recipe_refs=recipe_refs,
            hard_safety_refs=hard_safety_refs,
            hard_safety_mode=hard_safety_mode,
            tool_grants=() if blocked else tool_grants,
            tool_denials=tool_denials,
            approval_requirements=approval_requirements,
            evidence_requirements=evidence_requirements,
            context_requirements=context_requirements,
            retry_policy=retry_policy,
            conflicts=conflicts,
            blocked=blocked,
        )
    )
    return EffectiveRecipeMergeContract.model_validate(payload)


def merge_admitted_recipe_snapshots(
    snapshots: Iterable[AdmittedRecipeSnapshot],
    *,
    global_retry_cap: int = 3,
) -> EffectiveRecipeMergeContract:
    admitted_snapshots = tuple(_require_snapshot(snapshot) for snapshot in snapshots)
    ordered_snapshots = tuple(
        sorted(
            admitted_snapshots,
            key=lambda snapshot: (snapshot.recipe_ref, snapshot.snapshot_digest),
        )
    )

    recipe_refs = _recipe_ref_tuple(
        (snapshot.recipe_ref for snapshot in ordered_snapshots),
        "recipe_refs",
    )
    hard_safety_refs = _recipe_ref_tuple(
        (snapshot.recipe_ref for snapshot in ordered_snapshots if snapshot.hard_safety),
        "hard_safety_refs",
    )
    hard_safety_mode: Literal["none", "enforce"] = "enforce" if hard_safety_refs else "none"

    grant_refs = {
        _public_id(tool_ref, "tool_grants")
        for snapshot in ordered_snapshots
        for tool_ref in snapshot.tool_grants
    }
    denial_refs = {
        _public_id(tool_ref, "tool_denials")
        for snapshot in ordered_snapshots
        for tool_ref in snapshot.tool_denials
    }
    grant_recipes_by_tool = _snapshot_recipe_refs_by_subject(ordered_snapshots, "tool_grants")
    denial_recipes_by_tool = _snapshot_recipe_refs_by_subject(ordered_snapshots, "tool_denials")

    conflicts: list[RecipeMergeConflict] = []
    for denied_grant in sorted(grant_refs & denial_refs):
        conflicts.append(
            RecipeMergeConflict(
                code="tool_denied_grant",
                subjectRef=denied_grant,
                recipeRefs=tuple(
                    sorted(
                        {
                            *grant_recipes_by_tool.get(denied_grant, ()),
                            *denial_recipes_by_tool.get(denied_grant, ()),
                        }
                    )
                ),
                blocking=True,
            )
        )

    for snapshot in ordered_snapshots:
        for rule in snapshot.projection_rules:
            mode = _hard_safety_mode_rule(rule)
            if mode is None:
                continue
            if snapshot.hard_safety and mode in _HARD_SAFETY_WEAK_MODES:
                conflicts.append(
                    RecipeMergeConflict(
                        code="hard_safety_invalid_mode",
                        subjectRef="hardSafety.mode",
                        recipeRefs=(snapshot.recipe_ref,),
                        blocking=True,
                    )
                )
            elif hard_safety_refs and mode in _HARD_SAFETY_WEAK_MODES:
                conflicts.append(
                    RecipeMergeConflict(
                        code="hard_safety_downgrade_rejected",
                        subjectRef="hardSafety.mode",
                        recipeRefs=tuple(sorted({snapshot.recipe_ref, *hard_safety_refs})),
                        blocking=False,
                    )
                )

    retry_policy, retry_conflicts = _merged_retry_policy(ordered_snapshots, global_retry_cap)
    conflicts.extend(retry_conflicts)
    evidence_requirements, evidence_conflicts = _merge_snapshot_policy_requirements(
        ordered_snapshots,
        attribute="evidence_requirements",
        levels=_EVIDENCE_STRICTNESS,
        default_level="required",
        field_name="evidence_requirements",
        conflict_code="evidence_requirement_unrecognized",
    )
    conflicts.extend(evidence_conflicts)
    context_requirements, context_conflicts = _merge_snapshot_policy_requirements(
        ordered_snapshots,
        attribute="context_requirements",
        levels=_CONTEXT_PRIVILEGE,
        default_level="full",
        field_name="context_requirements",
        conflict_code="context_requirement_unrecognized",
    )
    conflicts.extend(context_conflicts)

    sorted_conflicts = _sorted_conflicts(conflicts)
    blocked = any(conflict.blocking for conflict in sorted_conflicts)

    return _build_merge_contract(
        recipe_refs=recipe_refs,
        hard_safety_refs=hard_safety_refs,
        hard_safety_mode=hard_safety_mode,
        tool_grants=_public_id_tuple(grant_refs - denial_refs, "tool_grants"),
        tool_denials=_public_id_tuple(denial_refs, "tool_denials"),
        approval_requirements=_public_id_tuple(
            (
                approval_ref
                for snapshot in ordered_snapshots
                for approval_ref in snapshot.approval_requirements
            ),
            "approval_requirements",
        ),
        evidence_requirements=evidence_requirements,
        context_requirements=context_requirements,
        retry_policy=retry_policy,
        conflicts=sorted_conflicts,
        blocked=blocked,
    )
