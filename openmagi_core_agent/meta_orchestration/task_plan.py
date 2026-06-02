from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


MetaChildDeliveryMode: TypeAlias = Literal["return", "background"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_AUTHORITY_FLAG_NAMES = (
    "tool_execution_allowed",
    "child_execution_allowed",
    "model_call_allowed",
    "workspace_mutation_allowed",
    "memory_write_allowed",
    "route_attached",
    "production_authority",
    "adk_runner_attached",
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z0-9._:@-]+$")
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users(?:/[^,\s\"']*)?|/home(?:/[^,\s\"']*)?|"
    r"/workspace(?:/[^,\s\"']*)?|/data/bots(?:/[^,\s\"']*)?|"
    r"/var/lib/kubelet(?:/[^,\s\"']*)?|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[a-z]-[A-Za-z0-9._-]{8,}|"
    r"AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|ACCESS[_-]?KEY)[A-Z0-9_]*"
    r"\s*[:=]\s*[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_RAW_PRIVATE_TEXT_RE = re.compile(
    r"raw[\s_-]*(?:prompt|model|output|transcript|tool|result|log|args)|"
    r"(?:hidden|private)[\s_-]*(?:reasoning|instructions?|transcript)|"
    r"chain[\s_-]*of[\s_-]*thought|tool[\s_-]*result|"
    r"authorization|auth[\s_-]*header|cookie|set-cookie|secret|credential|token",
    re.IGNORECASE,
)


class _MetaTaskPlanModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        raise TypeError("model_construct is disabled for meta task plan contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        return type(self).model_validate(data)


class MetaPlanAuthorityFlags(_MetaTaskPlanModel):
    tool_execution_allowed: Literal[False] = Field(
        default=False,
        alias="toolExecutionAllowed",
    )
    child_execution_allowed: Literal[False] = Field(
        default=False,
        alias="childExecutionAllowed",
    )
    model_call_allowed: Literal[False] = Field(default=False, alias="modelCallAllowed")
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")

    @field_validator(*_AUTHORITY_FLAG_NAMES, mode="before")
    @classmethod
    def _validate_false_only_flags(cls, value: object, info: Any) -> object:
        if value is not False:
            raise ValueError(f"{info.field_name} must remain false")
        return value

    @field_serializer(*_AUTHORITY_FLAG_NAMES)
    def _serialize_false(self, _value: object) -> bool:
        return False


class MetaChildContextBudget(_MetaTaskPlanModel):
    max_input_tokens: int = Field(alias="maxInputTokens", ge=0, strict=True)
    max_output_tokens: int = Field(alias="maxOutputTokens", ge=0, strict=True)
    reserved_evidence_tokens: int = Field(
        default=0,
        alias="reservedEvidenceTokens",
        ge=0,
        strict=True,
    )

    @model_validator(mode="after")
    def _require_context_budget(self) -> Self:
        if self.max_input_tokens + self.max_output_tokens <= 0:
            raise ValueError("contextBudget must reserve input or output tokens")
        return self


class MetaChildTaskSpec(_MetaTaskPlanModel):
    task_id: str = Field(alias="taskId")
    role_ref: str = Field(alias="roleRef")
    scope_ref: str = Field(alias="scopeRef")
    allowed_tool_refs: tuple[str, ...] = Field(default=(), alias="allowedToolRefs")
    context_budget: MetaChildContextBudget = Field(alias="contextBudget")
    completion_contract_ref: str = Field(alias="completionContractRef")
    delivery_mode: MetaChildDeliveryMode = Field(alias="deliveryMode")
    requires_evidence_envelope: Literal[True] = Field(
        default=True,
        alias="requiresEvidenceEnvelope",
    )

    @field_validator(
        "task_id",
        "role_ref",
        "scope_ref",
        "completion_contract_ref",
    )
    @classmethod
    def _validate_public_ref(cls, value: str) -> str:
        return _validate_public_ref(value, "child task spec")

    @field_validator("allowed_tool_refs")
    @classmethod
    def _validate_allowed_tool_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_ref_tuple(value, "allowedToolRefs")

    @field_validator("requires_evidence_envelope", mode="before")
    @classmethod
    def _validate_evidence_envelope_required(cls, value: object) -> object:
        if value is not True:
            raise ValueError("requiresEvidenceEnvelope must remain true")
        return value

    @field_validator("context_budget")
    @classmethod
    def _revalidate_context_budget(cls, value: MetaChildContextBudget) -> MetaChildContextBudget:
        return MetaChildContextBudget.model_validate(value.model_dump(by_alias=True))


class MetaTaskPlan(_MetaTaskPlanModel):
    plan_id: str = Field(alias="planId")
    parent_execution_id: str = Field(alias="parentExecutionId")
    objective_digest: str = Field(alias="objectiveDigest")
    objective_preview: str = Field(alias="objectivePreview", max_length=512)
    acceptance_criteria_refs: tuple[str, ...] = Field(alias="acceptanceCriteriaRefs")
    child_task_specs: tuple[MetaChildTaskSpec, ...] = Field(alias="childTaskSpecs")
    verifier_chain_refs: tuple[str, ...] = Field(default=(), alias="verifierChainRefs")
    max_retry_budget: int = Field(alias="maxRetryBudget", ge=0, le=10, strict=True)
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    authority_flags: MetaPlanAuthorityFlags = Field(
        default_factory=MetaPlanAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("plan_id", "parent_execution_id")
    @classmethod
    def _validate_ids(cls, value: str) -> str:
        return _validate_public_ref(value, "meta task plan identifiers")

    @field_validator("objective_digest")
    @classmethod
    def _validate_objective_digest(cls, value: str) -> str:
        if _DIGEST_RE.fullmatch(value) is None:
            raise ValueError("objectiveDigest must be a sha256 digest ref")
        return value

    @field_validator("objective_preview")
    @classmethod
    def _validate_objective_preview(cls, value: str) -> str:
        return _validate_public_text(value, "objectivePreview")

    @field_validator("acceptance_criteria_refs", "verifier_chain_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...], info: Any) -> tuple[str, ...]:
        return _validate_ref_tuple(value, info.field_name)

    @field_validator("child_task_specs")
    @classmethod
    def _revalidate_child_specs(
        cls,
        value: tuple[MetaChildTaskSpec, ...],
    ) -> tuple[MetaChildTaskSpec, ...]:
        if not value:
            raise ValueError("childTaskSpecs must include at least one child task spec")
        return tuple(
            MetaChildTaskSpec.model_validate(item.model_dump(by_alias=True)) for item in value
        )

    @field_validator("default_off", mode="before")
    @classmethod
    def _validate_default_off(cls, value: object) -> object:
        if value is not True:
            raise ValueError("defaultOff must remain true")
        return value

    @field_validator("authority_flags")
    @classmethod
    def _revalidate_authority_flags(
        cls,
        value: MetaPlanAuthorityFlags,
    ) -> MetaPlanAuthorityFlags:
        return MetaPlanAuthorityFlags.model_validate(value.model_dump(by_alias=True))

    @model_validator(mode="after")
    def _validate_plan_contract(self) -> Self:
        task_ids = tuple(child.task_id for child in self.child_task_specs)
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("childTaskSpecs taskId values must be unique")
        return self


def _copy_update_alias(model_cls: type[BaseModel], key: str) -> str:
    for field_name, field_info in model_cls.model_fields.items():
        if key == field_name or key == field_info.alias:
            return str(field_info.alias or field_name)
    return key


def _validate_ref_tuple(value: Sequence[str], field_name: str) -> tuple[str, ...]:
    refs = tuple(value)
    for item in refs:
        _validate_public_ref(item, field_name)
    if len(set(refs)) != len(refs):
        raise ValueError(f"{field_name} must not contain duplicates")
    return refs


def _validate_public_ref(value: str, field_name: str) -> str:
    clean = _validate_public_text(value, field_name)
    if _PUBLIC_REF_RE.fullmatch(clean) is None:
        raise ValueError(f"{field_name} must contain opaque public refs only")
    return clean


def _validate_public_text(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if _PRIVATE_PATH_RE.search(value) is not None:
        raise ValueError(f"{field_name} must not expose private path values")
    if _SECRET_TEXT_RE.search(value) is not None:
        raise ValueError(f"{field_name} must not expose auth or secret values")
    if _RAW_PRIVATE_TEXT_RE.search(value) is not None:
        raise ValueError(f"{field_name} must not expose raw private values")
    return value
