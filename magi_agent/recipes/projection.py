from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import re
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_serializer,
    model_validator,
)

from magi_agent.recipes.effective_contract import (
    EffectiveRecipeConflict,
    EffectiveRecipeContract,
    EffectiveRecipeExclusion,
    _canonical_digest,
    _public_id as _effective_public_id,
    _recipe_ref_tuple,
    _safe_digest,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    hide_input_in_errors=True,
    revalidate_instances="always",
    validate_default=True,
)
_PROJECTION_UNSAFE_TEXT_FRAGMENTS = frozenset((
    "hiddenconfig",
    "pluginconfig",
    "rawpluginconfig",
))
_MINTED_AUDIT_DIGESTS: set[tuple[str, str]] = set()
_ALLOW_UNMINTED_PROJECTION_VALIDATION = 0


def _projection_public_id(value: object, field_name: str) -> str:
    public_value = _effective_public_id(value, field_name)
    normalized = re.sub(r"[^a-z0-9]", "", public_value.strip().lower())
    if any(fragment in normalized for fragment in _PROJECTION_UNSAFE_TEXT_FRAGMENTS):
        raise ValueError(f"{field_name} must be a public projection identifier")
    return public_value


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
        refs.add(_projection_public_id(raw_value, field_name))
    return tuple(sorted(refs))


def _subject_digest(subject_ref: str) -> str:
    return _canonical_digest({"subjectRef": _projection_public_id(subject_ref, "subject_ref")})


def _strict_non_negative_count(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


class RecipeCompositionPublicCounts(BaseModel):
    model_config = _MODEL_CONFIG

    tool_grant_count: StrictInt = Field(alias="toolGrantCount")
    tool_denial_count: StrictInt = Field(alias="toolDenialCount")
    hook_count: StrictInt = Field(alias="hookCount")
    evidence_requirement_count: StrictInt = Field(alias="evidenceRequirementCount")
    approval_requirement_count: StrictInt = Field(alias="approvalRequirementCount")
    context_policy_count: StrictInt = Field(alias="contextPolicyCount")

    @field_validator(
        "tool_grant_count",
        "tool_denial_count",
        "hook_count",
        "evidence_requirement_count",
        "approval_requirement_count",
        "context_policy_count",
        mode="before",
    )
    @classmethod
    def _sanitize_count(cls, value: object, info: Any) -> int:
        return _strict_non_negative_count(value, info.field_name)

    def public_projection(self) -> dict[str, int]:
        return {
            "toolGrantCount": self.tool_grant_count,
            "toolDenialCount": self.tool_denial_count,
            "hookCount": self.hook_count,
            "evidenceRequirementCount": self.evidence_requirement_count,
            "approvalRequirementCount": self.approval_requirement_count,
            "contextPolicyCount": self.context_policy_count,
        }

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, int]:
        return RecipeCompositionPublicCounts.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            RecipeCompositionPublicCounts.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_model(self) -> dict[str, int]:
        return RecipeCompositionPublicCounts.public_projection(self)


class RecipeCompositionConflictSummary(BaseModel):
    model_config = _MODEL_CONFIG

    code: StrictStr
    subject_digest: StrictStr = Field(alias="subjectDigest")
    recipe_ref_count: StrictInt = Field(alias="recipeRefCount")
    blocking: StrictBool

    @classmethod
    def from_conflict(
        cls,
        conflict: EffectiveRecipeConflict,
    ) -> "RecipeCompositionConflictSummary":
        if type(conflict) is not EffectiveRecipeConflict:
            raise ValueError("conflict summary requires effective conflict")
        return cls(
            code=conflict.code,
            subjectDigest=_subject_digest(conflict.subject_ref),
            recipeRefCount=len(_recipe_ref_tuple(conflict.recipe_refs, "recipe_refs")),
            blocking=conflict.blocking,
        )

    @field_validator("code")
    @classmethod
    def _sanitize_code(cls, value: object) -> str:
        return _projection_public_id(value, "code")

    @field_validator("subject_digest", mode="before")
    @classmethod
    def _sanitize_subject_digest(cls, value: object) -> str:
        return _safe_digest(value, "subject_digest")

    @field_validator("recipe_ref_count", mode="before")
    @classmethod
    def _sanitize_recipe_ref_count(cls, value: object) -> int:
        return _strict_non_negative_count(value, "recipe_ref_count")

    def public_projection(self) -> dict[str, object]:
        return {
            "code": _projection_public_id(self.code, "code"),
            "subjectDigest": _safe_digest(self.subject_digest, "subject_digest"),
            "recipeRefCount": self.recipe_ref_count,
            "blocking": bool(self.blocking),
        }

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        return RecipeCompositionConflictSummary.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            RecipeCompositionConflictSummary.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_model(self) -> dict[str, object]:
        return RecipeCompositionConflictSummary.public_projection(self)


class RecipeCompositionMergeDecision(BaseModel):
    model_config = _MODEL_CONFIG

    code: StrictStr
    subject_digest: StrictStr = Field(alias="subjectDigest")
    recipe_ref_count: StrictInt = Field(alias="recipeRefCount")
    blocking: StrictBool = False

    @classmethod
    def from_subject(
        cls,
        *,
        code: str,
        subject_ref: str,
        recipe_ref_count: int,
        blocking: bool,
    ) -> "RecipeCompositionMergeDecision":
        return cls(
            code=code,
            subjectDigest=_subject_digest(subject_ref),
            recipeRefCount=recipe_ref_count,
            blocking=blocking,
        )

    @field_validator("code")
    @classmethod
    def _sanitize_code(cls, value: object) -> str:
        return _projection_public_id(value, "code")

    @field_validator("subject_digest", mode="before")
    @classmethod
    def _sanitize_subject_digest(cls, value: object) -> str:
        return _safe_digest(value, "subject_digest")

    @field_validator("recipe_ref_count", mode="before")
    @classmethod
    def _sanitize_recipe_ref_count(cls, value: object) -> int:
        return _strict_non_negative_count(value, "recipe_ref_count")

    def public_projection(self) -> dict[str, object]:
        return {
            "code": _projection_public_id(self.code, "code"),
            "subjectDigest": _safe_digest(self.subject_digest, "subject_digest"),
            "recipeRefCount": self.recipe_ref_count,
            "blocking": bool(self.blocking),
        }

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        return RecipeCompositionMergeDecision.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            RecipeCompositionMergeDecision.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_model(self) -> dict[str, object]:
        return RecipeCompositionMergeDecision.public_projection(self)


class RecipeCompositionProjection(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["recipeCompositionProjection.v1"] = Field(
        default="recipeCompositionProjection.v1",
        alias="schemaVersion",
    )
    effective_digest: StrictStr = Field(alias="effectiveDigest")
    audit_digest: StrictStr = Field(alias="auditDigest")
    effective_recipe_refs: tuple[str, ...] = Field(alias="effectiveRecipeRefs")
    included_explicit_refs: tuple[str, ...] = Field(alias="includedExplicitRefs")
    included_auto_refs: tuple[str, ...] = Field(alias="includedAutoRefs")
    excluded_refs: tuple[EffectiveRecipeExclusion, ...] = Field(alias="excludedRefs")
    excluded_reason_codes: tuple[str, ...] = Field(alias="excludedReasonCodes")
    merge_decisions: tuple[RecipeCompositionMergeDecision, ...] = Field(alias="mergeDecisions")
    conflict_status: Literal["clear", "conflicted", "blocked"] = Field(alias="conflictStatus")
    conflict_count: StrictInt = Field(alias="conflictCount")
    conflicts: tuple[RecipeCompositionConflictSummary, ...]
    blocked: StrictBool
    hard_safety_status: Literal["not_required", "enforced", "missing", "blocked"] = Field(
        alias="hardSafetyStatus"
    )
    hard_safety_ref_count: StrictInt = Field(alias="hardSafetyRefCount")
    hard_safety_included_count: StrictInt = Field(alias="hardSafetyIncludedCount")
    public_safe_counts: RecipeCompositionPublicCounts = Field(alias="publicSafeCounts")
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    live_activation: Literal[False] = Field(default=False, alias="liveActivation")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls.model_validate(values)

    @field_validator("effective_digest", "audit_digest", mode="before")
    @classmethod
    def _sanitize_digest(cls, value: object, info: Any) -> str:
        return _safe_digest(value, info.field_name)

    @field_validator(
        "effective_recipe_refs",
        "included_explicit_refs",
        "included_auto_refs",
        mode="before",
    )
    @classmethod
    def _sanitize_recipe_refs(cls, value: object) -> tuple[str, ...]:
        return _recipe_ref_tuple(value, "recipe_refs")

    @field_validator("excluded_reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return _public_id_tuple(value, "excluded_reason_codes")

    @field_validator(
        "conflict_count",
        "hard_safety_ref_count",
        "hard_safety_included_count",
        mode="before",
    )
    @classmethod
    def _sanitize_count(cls, value: object, info: Any) -> int:
        return _strict_non_negative_count(value, info.field_name)

    @model_validator(mode="after")
    def _validate_audit_digest(self) -> Self:
        if self.conflict_count != len(self.conflicts):
            raise ValueError("conflict count must match conflicts")
        expected_conflict_status = "clear"
        if self.blocked:
            expected_conflict_status = "blocked"
        elif self.conflicts:
            expected_conflict_status = "conflicted"
        if self.conflict_status != expected_conflict_status:
            raise ValueError("conflict status must match blocked/conflicts")
        if self.hard_safety_included_count > self.hard_safety_ref_count:
            raise ValueError("hard safety included count cannot exceed ref count")
        if self.hard_safety_ref_count == 0:
            allowed_statuses = {"not_required"}
            if self.blocked:
                allowed_statuses.add("blocked")
            if (
                self.hard_safety_status not in allowed_statuses
                or self.hard_safety_included_count != 0
            ):
                raise ValueError("hard safety status/count mismatch")
        elif self.blocked:
            if self.hard_safety_status != "blocked":
                raise ValueError("hard safety status must be blocked when projection is blocked")
        elif self.hard_safety_included_count == self.hard_safety_ref_count:
            if self.hard_safety_status != "enforced":
                raise ValueError("hard safety status must be enforced when all refs are included")
        elif self.hard_safety_status != "missing":
            raise ValueError("hard safety status must be missing when refs are omitted")
        if self.audit_digest != RecipeCompositionProjection._compute_audit_digest(self):
            raise ValueError("recipe composition projection audit digest mismatch")
        if (
            _ALLOW_UNMINTED_PROJECTION_VALIDATION == 0
            and (self.effective_digest, self.audit_digest) not in _MINTED_AUDIT_DIGESTS
        ):
            raise ValueError("recipe composition projection requires builder-minted audit digest")
        return self

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> "RecipeCompositionProjection":
        RecipeCompositionProjection._assert_audit_digest_matches(self)
        if update:
            raise ValueError("recipe composition projection authority fields are immutable")
        return RecipeCompositionProjection.model_validate(
            RecipeCompositionProjection.public_projection(self)
        )

    @staticmethod
    def _compute_audit_digest(projection: "RecipeCompositionProjection") -> str:
        return _canonical_digest(RecipeCompositionProjection._digest_payload(projection))

    @staticmethod
    def compute_audit_digest(values: Mapping[str, object]) -> str:
        return _canonical_digest(
            RecipeCompositionProjection._digest_payload_from_values(values)
        )

    @classmethod
    def _from_builder_projection(
        cls,
        values: Mapping[str, object],
    ) -> "RecipeCompositionProjection":
        global _ALLOW_UNMINTED_PROJECTION_VALIDATION

        _ALLOW_UNMINTED_PROJECTION_VALIDATION += 1
        try:
            projection = cls.model_validate(values)
        finally:
            _ALLOW_UNMINTED_PROJECTION_VALIDATION -= 1
        _MINTED_AUDIT_DIGESTS.add(
            (
                _safe_digest(projection.effective_digest, "effective_digest"),
                _safe_digest(projection.audit_digest, "audit_digest"),
            )
        )
        return projection

    @staticmethod
    def _digest_payload(projection: "RecipeCompositionProjection") -> dict[str, object]:
        return RecipeCompositionProjection._digest_payload_from_values(
            {
                "schemaVersion": projection.schema_version,
                "effectiveDigest": projection.effective_digest,
                "effectiveRecipeRefs": projection.effective_recipe_refs,
                "includedExplicitRefs": projection.included_explicit_refs,
                "includedAutoRefs": projection.included_auto_refs,
                "excludedRefs": projection.excluded_refs,
                "excludedReasonCodes": projection.excluded_reason_codes,
                "mergeDecisions": projection.merge_decisions,
                "conflictStatus": projection.conflict_status,
                "conflictCount": projection.conflict_count,
                "conflicts": projection.conflicts,
                "blocked": projection.blocked,
                "hardSafetyStatus": projection.hard_safety_status,
                "hardSafetyRefCount": projection.hard_safety_ref_count,
                "hardSafetyIncludedCount": projection.hard_safety_included_count,
                "publicSafeCounts": projection.public_safe_counts,
                "defaultOff": True,
                "trafficAttached": False,
                "executionAttached": False,
                "liveActivation": False,
            }
        )

    @staticmethod
    def _digest_payload_from_values(values: Mapping[str, object]) -> dict[str, object]:
        excluded_refs = tuple(
            exclusion.public_projection()
            if type(exclusion) is EffectiveRecipeExclusion
            else EffectiveRecipeExclusion.model_validate(exclusion).public_projection()
            for exclusion in _tuple_values(values.get("excludedRefs"), "excluded_refs")
        )
        merge_decisions = tuple(
            decision.public_projection()
            if type(decision) is RecipeCompositionMergeDecision
            else RecipeCompositionMergeDecision.model_validate(decision).public_projection()
            for decision in _tuple_values(values.get("mergeDecisions"), "merge_decisions")
        )
        conflicts = tuple(
            conflict.public_projection()
            if type(conflict) is RecipeCompositionConflictSummary
            else RecipeCompositionConflictSummary.model_validate(conflict).public_projection()
            for conflict in _tuple_values(values.get("conflicts"), "conflicts")
        )
        raw_counts = values.get("publicSafeCounts")
        counts = (
            raw_counts.public_projection()
            if type(raw_counts) is RecipeCompositionPublicCounts
            else RecipeCompositionPublicCounts.model_validate(raw_counts).public_projection()
        )
        return {
            "schemaVersion": _projection_public_id(values.get("schemaVersion"), "schema_version"),
            "effectiveDigest": _safe_digest(values.get("effectiveDigest"), "effective_digest"),
            "effectiveRecipeRefs": _recipe_ref_tuple(
                values.get("effectiveRecipeRefs"),
                "effective_recipe_refs",
            ),
            "includedExplicitRefs": _recipe_ref_tuple(
                values.get("includedExplicitRefs"),
                "included_explicit_refs",
            ),
            "includedAutoRefs": _recipe_ref_tuple(
                values.get("includedAutoRefs"),
                "included_auto_refs",
            ),
            "excludedRefs": excluded_refs,
            "excludedReasonCodes": _public_id_tuple(
                values.get("excludedReasonCodes"),
                "excluded_reason_codes",
            ),
            "mergeDecisions": merge_decisions,
            "conflictStatus": _projection_public_id(
                values.get("conflictStatus"),
                "conflict_status",
            ),
            "conflictCount": _strict_non_negative_count(
                values.get("conflictCount"),
                "conflict_count",
            ),
            "conflicts": conflicts,
            "blocked": bool(values.get("blocked")),
            "hardSafetyStatus": _projection_public_id(
                values.get("hardSafetyStatus"),
                "hard_safety_status",
            ),
            "hardSafetyRefCount": _strict_non_negative_count(
                values.get("hardSafetyRefCount"),
                "hard_safety_ref_count",
            ),
            "hardSafetyIncludedCount": _strict_non_negative_count(
                values.get("hardSafetyIncludedCount"),
                "hard_safety_included_count",
            ),
            "publicSafeCounts": counts,
            "defaultOff": True,
            "trafficAttached": False,
            "executionAttached": False,
            "liveActivation": False,
        }

    @staticmethod
    def _assert_audit_digest_matches(projection: "RecipeCompositionProjection") -> None:
        if projection.audit_digest != RecipeCompositionProjection._compute_audit_digest(projection):
            raise ValueError("recipe composition projection audit digest mismatch")
        if (projection.effective_digest, projection.audit_digest) not in _MINTED_AUDIT_DIGESTS:
            raise ValueError("recipe composition projection requires builder-minted audit digest")

    def public_projection(self) -> dict[str, object]:
        RecipeCompositionProjection._assert_audit_digest_matches(self)
        payload = RecipeCompositionProjection._digest_payload(self)
        return {
            **payload,
            "auditDigest": _safe_digest(self.audit_digest, "audit_digest"),
        }

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        return RecipeCompositionProjection.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            RecipeCompositionProjection.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_model(self) -> dict[str, object]:
        return RecipeCompositionProjection.public_projection(self)


def _conflict_status(contract: EffectiveRecipeContract) -> Literal["clear", "conflicted", "blocked"]:
    if contract.blocked:
        return "blocked"
    if contract.conflicts:
        return "conflicted"
    return "clear"


def _hard_safety_status(
    contract: EffectiveRecipeContract,
    hard_safety_refs: tuple[str, ...],
) -> tuple[Literal["not_required", "enforced", "missing", "blocked"], int]:
    if not hard_safety_refs:
        if contract.blocked and any(
            conflict.code.startswith("hard_safety")
            or conflict.subject_ref.startswith("hardSafety")
            for conflict in contract.conflicts
        ):
            return "blocked", 0
        return "not_required", 0
    effective_refs = set(contract.effective_recipe_refs)
    included_count = sum(1 for recipe_ref in hard_safety_refs if recipe_ref in effective_refs)
    if contract.blocked:
        return "blocked", included_count
    if included_count == len(hard_safety_refs):
        return "enforced", included_count
    return "missing", included_count


def _hook_count(contract_projection: Mapping[str, object]) -> int:
    hooks = contract_projection.get("effectiveHooks")
    if not isinstance(hooks, Mapping):
        return 0
    hook_count = hooks.get("hookCount", 0)
    return _strict_non_negative_count(hook_count, "hook_count")


def _public_counts(
    contract: EffectiveRecipeContract,
    contract_projection: Mapping[str, object],
) -> RecipeCompositionPublicCounts:
    return RecipeCompositionPublicCounts(
        toolGrantCount=len(contract.effective_tool_grants),
        toolDenialCount=len(contract.effective_tool_denials),
        hookCount=_hook_count(contract_projection),
        evidenceRequirementCount=len(contract.effective_evidence_requirements),
        approvalRequirementCount=len(contract.effective_approval_requirements),
        contextPolicyCount=len(contract.effective_context_policy),
    )


def _merge_decisions(
    contract: EffectiveRecipeContract,
    hard_safety_refs: tuple[str, ...],
) -> tuple[RecipeCompositionMergeDecision, ...]:
    decisions: list[RecipeCompositionMergeDecision] = []
    hard_safety_ref_set = set(hard_safety_refs)
    for recipe_ref in contract.included_explicit_refs:
        decisions.append(
            RecipeCompositionMergeDecision.from_subject(
                code="explicit.included",
                subject_ref=recipe_ref,
                recipe_ref_count=1,
                blocking=False,
            )
        )
    for recipe_ref in contract.included_auto_refs:
        decisions.append(
            RecipeCompositionMergeDecision.from_subject(
                code="auto.included",
                subject_ref=recipe_ref,
                recipe_ref_count=1,
                blocking=False,
            )
        )
    for recipe_ref in hard_safety_refs:
        if recipe_ref in contract.effective_recipe_refs:
            decisions.append(
                RecipeCompositionMergeDecision.from_subject(
                    code="hard_safety.enforced",
                    subject_ref=recipe_ref,
                    recipe_ref_count=1,
                    blocking=False,
                )
            )
    for exclusion in contract.excluded_refs:
        blocking = bool(exclusion.blocking)
        decisions.append(
            RecipeCompositionMergeDecision.from_subject(
                code=f"excluded.{exclusion.reason}",
                subject_ref=exclusion.recipe_ref,
                recipe_ref_count=1,
                blocking=blocking,
            )
        )
    for conflict in contract.conflicts:
        recipe_ref_count = len(_recipe_ref_tuple(conflict.recipe_refs, "recipe_refs"))
        blocking = bool(conflict.blocking)
        code = f"conflict.{conflict.code}"
        if hard_safety_ref_set.intersection(conflict.recipe_refs) and blocking:
            code = "hard_safety.blocked"
        decisions.append(
            RecipeCompositionMergeDecision.from_subject(
                code=code,
                subject_ref=conflict.subject_ref,
                recipe_ref_count=recipe_ref_count,
                blocking=blocking,
            )
        )
    return tuple(decisions)


def _conflict_summaries(
    contract: EffectiveRecipeContract,
) -> tuple[RecipeCompositionConflictSummary, ...]:
    return tuple(
        RecipeCompositionConflictSummary.from_conflict(conflict)
        for conflict in contract.conflicts
    )


def project_effective_recipe_contract(
    contract: EffectiveRecipeContract,
) -> RecipeCompositionProjection:
    if type(contract) is not EffectiveRecipeContract:
        raise ValueError("recipe composition projection requires effective recipe contract")
    if (
        contract.default_off is not True
        or contract.traffic_attached is not False
        or contract.execution_attached is not False
        or contract.live_activation is not False
    ):
        raise ValueError("effective recipe contract authority flags must be default-off")

    contract_projection = contract.public_projection()
    sanitized_hard_safety_refs = _recipe_ref_tuple(
        contract.effective_hard_safety_refs,
        "hard_safety_refs",
    )
    hard_safety_status, hard_safety_included_count = _hard_safety_status(
        contract,
        sanitized_hard_safety_refs,
    )
    excluded_reason_codes = tuple(
        sorted(
            {
                _projection_public_id(exclusion.reason, "excluded_reason_code")
                for exclusion in contract.excluded_refs
            }
        )
    )
    payload: dict[str, object] = {
        "schemaVersion": "recipeCompositionProjection.v1",
        "effectiveDigest": _safe_digest(contract.effective_digest, "effective_digest"),
        "auditDigest": "sha256:" + "0" * 64,
        "effectiveRecipeRefs": contract.effective_recipe_refs,
        "includedExplicitRefs": contract.included_explicit_refs,
        "includedAutoRefs": contract.included_auto_refs,
        "excludedRefs": contract.excluded_refs,
        "excludedReasonCodes": excluded_reason_codes,
        "mergeDecisions": _merge_decisions(contract, sanitized_hard_safety_refs),
        "conflictStatus": _conflict_status(contract),
        "conflictCount": len(contract.conflicts),
        "conflicts": _conflict_summaries(contract),
        "blocked": bool(contract.blocked),
        "hardSafetyStatus": hard_safety_status,
        "hardSafetyRefCount": len(sanitized_hard_safety_refs),
        "hardSafetyIncludedCount": hard_safety_included_count,
        "publicSafeCounts": _public_counts(contract, contract_projection),
        "defaultOff": True,
        "trafficAttached": False,
        "executionAttached": False,
        "liveActivation": False,
    }
    payload["auditDigest"] = RecipeCompositionProjection.compute_audit_digest(payload)
    return RecipeCompositionProjection._from_builder_projection(payload)


__all__ = (
    "RecipeCompositionConflictSummary",
    "RecipeCompositionMergeDecision",
    "RecipeCompositionProjection",
    "RecipeCompositionPublicCounts",
    "project_effective_recipe_contract",
)
