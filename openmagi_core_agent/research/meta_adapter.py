from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openmagi_core_agent.meta_orchestration.child_acceptance import (
    ChildAcceptancePolicy,
    ChildAcceptanceVerdict,
    RetryExhaustedStatus,
    RuntimeIssuedChildResult,
    accept_child_result,
)
from openmagi_core_agent.meta_orchestration.child_roles import (
    MetaChildRoleDefinition,
    MetaChildRoleRegistry,
)
from openmagi_core_agent.meta_orchestration.task_plan import (
    MetaChildContextBudget,
    MetaChildTaskSpec,
    MetaTaskPlan,
    _copy_update_alias,
    _validate_public_ref,
    _validate_public_text,
    _validate_ref_tuple,
)


RESEARCH_META_ROLE_NAMES: tuple[str, ...] = (
    "research_searcher",
    "source_inspector",
    "claim_mapper",
    "research_verifier",
    "synthesis_reviewer",
)

_ADK_USAGE_NOTES = (
    "Research adapter metadata only; ADK Agent, Runner, FunctionTool, callbacks, "
    "SessionService, MemoryService, ArtifactService, and Evaluation are not attached."
)
_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_URL_ONLY_PREFIXES = ("http://", "https://", "url:", "citation:")
_EVIDENCE_REF_PREFIXES_BY_FIELD = {
    "source_evidence_refs": ("audit:source-", "source:"),
    "claim_evidence_refs": ("audit:claim-", "claim:", "support:"),
    "task_evidence_refs": ("ledger:", "receipt:", "audit:task-", "audit:child-"),
}
_FORBIDDEN_TYPED_REF_TERMS = frozenset(
    {
        "auth",
        "cookie",
        "key",
        "log",
        "output",
        "prompt",
        "raw",
        "result",
        "secret",
        "summary",
        "token",
        "tool",
        "transcript",
    }
)


class _ResearchMetaModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        raise TypeError("model_construct is disabled for research meta adapter contracts")

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


class ResearchMetaEvidencePolicy(_ResearchMetaModel):
    parent_execution_id: str = Field(alias="parentExecutionId")
    child_execution_id: str = Field(alias="childExecutionId")
    task_id: str = Field(alias="taskId")
    parent_policy_snapshot_id: str = Field(alias="parentPolicySnapshotId")
    child_policy_snapshot_id: str = Field(alias="childPolicySnapshotId")
    runtime_receipt_ref: str = Field(alias="runtimeReceiptRef")
    source_evidence_refs: tuple[str, ...] = Field(alias="sourceEvidenceRefs")
    claim_evidence_refs: tuple[str, ...] = Field(alias="claimEvidenceRefs")
    task_evidence_refs: tuple[str, ...] = Field(alias="taskEvidenceRefs")
    max_retry_budget: int = Field(alias="maxRetryBudget", ge=0, le=10, strict=True)
    current_attempt: int = Field(alias="currentAttempt", ge=0, le=10, strict=True)
    exhausted_status: RetryExhaustedStatus = Field(default="rejected", alias="exhaustedStatus")
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")
    live_execution_allowed: Literal[False] = Field(default=False, alias="liveExecutionAllowed")
    child_execution_allowed: Literal[False] = Field(default=False, alias="childExecutionAllowed")
    tool_execution_allowed: Literal[False] = Field(default=False, alias="toolExecutionAllowed")
    model_call_allowed: Literal[False] = Field(default=False, alias="modelCallAllowed")
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")

    @field_validator(
        "parent_execution_id",
        "child_execution_id",
        "task_id",
        "parent_policy_snapshot_id",
        "child_policy_snapshot_id",
        "runtime_receipt_ref",
    )
    @classmethod
    def _validate_public_ids(cls, value: str, info: Any) -> str:
        return _validate_public_ref(value, info.field_name)

    @field_validator("source_evidence_refs", "claim_evidence_refs", "task_evidence_refs")
    @classmethod
    def _validate_research_evidence_refs(
        cls,
        value: Sequence[str],
        info: Any,
    ) -> tuple[str, ...]:
        refs = _validate_ref_tuple(value, info.field_name)
        if not refs:
            raise ValueError(f"{info.field_name} must include at least one evidence ref")
        for ref in refs:
            lowered = ref.lower()
            if lowered.startswith(_URL_ONLY_PREFIXES):
                raise ValueError(f"{info.field_name} cannot use URL-only citation refs")
            allowed_prefixes = _EVIDENCE_REF_PREFIXES_BY_FIELD[str(info.field_name)]
            if not lowered.startswith(allowed_prefixes):
                raise ValueError(f"{info.field_name} must use typed research evidence refs")
            _validate_typed_evidence_ref_payload(lowered, info.field_name)
        return refs

    @field_validator("default_off", "local_only", "fake_provider_only", mode="before")
    @classmethod
    def _validate_true_only_flags(cls, value: object, info: Any) -> object:
        if value is not True:
            raise ValueError(f"{info.field_name} must remain true")
        return value

    @field_validator(
        "live_execution_allowed",
        "child_execution_allowed",
        "tool_execution_allowed",
        "model_call_allowed",
        "adk_runner_attached",
        mode="before",
    )
    @classmethod
    def _validate_false_only_flags(cls, value: object, info: Any) -> object:
        if value is not False:
            raise ValueError(f"{info.field_name} must remain false")
        return value

    @model_validator(mode="after")
    def _validate_policy_shape(self) -> Self:
        if self.current_attempt > self.max_retry_budget:
            raise ValueError("currentAttempt must not exceed maxRetryBudget")
        refs = self.required_evidence_refs()
        if len(set(refs)) != len(refs):
            raise ValueError("research evidence refs must not overlap")
        return self

    def required_evidence_refs(self) -> tuple[str, ...]:
        return (
            *self.source_evidence_refs,
            *self.claim_evidence_refs,
            *self.task_evidence_refs,
        )

    def to_child_acceptance_policy(self) -> ChildAcceptancePolicy:
        return ChildAcceptancePolicy.model_validate(
            {
                "parentExecutionId": self.parent_execution_id,
                "childExecutionId": self.child_execution_id,
                "taskId": self.task_id,
                "parentPolicySnapshotId": self.parent_policy_snapshot_id,
                "childPolicySnapshotId": self.child_policy_snapshot_id,
                "runtimeReceiptRef": self.runtime_receipt_ref,
                "requiredEvidenceRefs": self.required_evidence_refs(),
                "maxRetryBudget": self.max_retry_budget,
                "currentAttempt": self.current_attempt,
                "exhaustedStatus": self.exhausted_status,
                "defaultOff": True,
            }
        )


class ResearchMetaHarnessPlan(_ResearchMetaModel):
    role_names: tuple[str, ...] = Field(alias="roleNames")
    role_definitions: tuple[MetaChildRoleDefinition, ...] = Field(alias="roleDefinitions")
    task_plan: MetaTaskPlan = Field(alias="taskPlan")
    evidence_policy: ResearchMetaEvidencePolicy = Field(alias="evidencePolicy")
    adk_usage_notes: str = Field(default=_ADK_USAGE_NOTES, alias="adkUsageNotes")
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")

    @field_validator("role_names")
    @classmethod
    def _validate_role_names(cls, value: Sequence[str]) -> tuple[str, ...]:
        names = tuple(value)
        if names != RESEARCH_META_ROLE_NAMES:
            raise ValueError("roleNames must match the first-party research meta role set")
        return names

    @field_validator("role_definitions")
    @classmethod
    def _validate_role_definitions(
        cls,
        value: Sequence[MetaChildRoleDefinition],
    ) -> tuple[MetaChildRoleDefinition, ...]:
        roles = tuple(
            MetaChildRoleDefinition.model_validate(item.model_dump(by_alias=True))
            for item in value
        )
        if len(roles) != len(RESEARCH_META_ROLE_NAMES):
            raise ValueError("roleDefinitions must include all research meta roles")
        MetaChildRoleRegistry(roles)
        expected_by_ref = {
            role.role_ref: role.model_dump(by_alias=True, mode="python", warnings=False)
            for role in build_research_meta_role_definitions()
        }
        if {role.role_ref for role in roles} != set(expected_by_ref):
            raise ValueError("roleDefinitions must use the first-party research role refs")
        for role in roles:
            actual_role = role.model_dump(by_alias=True, mode="python", warnings=False)
            if actual_role != expected_by_ref[role.role_ref]:
                raise ValueError("roleDefinitions must match first-party research role specs")
        return roles

    @field_validator("task_plan")
    @classmethod
    def _revalidate_task_plan(cls, value: MetaTaskPlan) -> MetaTaskPlan:
        return MetaTaskPlan.model_validate(value.model_dump(by_alias=True))

    @field_validator("evidence_policy")
    @classmethod
    def _revalidate_evidence_policy(
        cls,
        value: ResearchMetaEvidencePolicy,
    ) -> ResearchMetaEvidencePolicy:
        return ResearchMetaEvidencePolicy.model_validate(value.model_dump(by_alias=True))

    @field_validator("adk_usage_notes")
    @classmethod
    def _validate_adk_usage_notes(cls, value: str) -> str:
        clean = _validate_public_text(value, "adkUsageNotes")
        if len(clean) > 300:
            raise ValueError("adkUsageNotes must be at most 300 characters")
        return clean

    @field_validator("default_off", "local_only", "fake_provider_only", mode="before")
    @classmethod
    def _validate_true_only_flags(cls, value: object, info: Any) -> object:
        if value is not True:
            raise ValueError(f"{info.field_name} must remain true")
        return value

    @model_validator(mode="after")
    def _validate_role_task_alignment(self) -> Self:
        roles_by_ref = {role.role_ref: role for role in self.role_definitions}
        task_role_refs = tuple(child.role_ref for child in self.task_plan.child_task_specs)
        task_ids = tuple(child.task_id for child in self.task_plan.child_task_specs)
        expected_task_id_by_role_ref = {
            spec.role_ref: spec.task_id
            for spec in _ROLE_SPECS
        }
        expected_task_ids = set(expected_task_id_by_role_ref.values())
        expected_child_specs_by_task_id = {
            expected_child.task_id: expected_child.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            )
            for spec in _ROLE_SPECS
            for expected_child in (_child_spec(spec.name, roles_by_ref[spec.role_ref]),)
        }
        if set(task_role_refs) != set(roles_by_ref):
            raise ValueError("research child task specs must map one-to-one to role definitions")
        if len(task_ids) != len(expected_task_ids) or set(task_ids) != expected_task_ids:
            raise ValueError("research child task specs must match first-party task ids")
        if self.evidence_policy.parent_execution_id != self.task_plan.parent_execution_id:
            raise ValueError("research evidence policy parent must match task plan parent")
        if self.evidence_policy.task_id not in task_ids:
            raise ValueError("research evidence policy task must be one composed child task")
        if self.evidence_policy.max_retry_budget != self.task_plan.max_retry_budget:
            raise ValueError("research evidence policy retry budget must match task plan")
        for child in self.task_plan.child_task_specs:
            role = roles_by_ref[child.role_ref]
            if child.task_id != expected_task_id_by_role_ref[child.role_ref]:
                raise ValueError("research child task id must match the first-party role")
            actual_child = child.model_dump(by_alias=True, mode="python", warnings=False)
            if actual_child != expected_child_specs_by_task_id[child.task_id]:
                raise ValueError("research child task specs must match first-party specs")
            if child.allowed_tool_refs != role.allowed_tool_refs:
                raise ValueError("child task allowed tool refs must match role grants")
            if child.completion_contract_ref != role.completion_contract_ref:
                raise ValueError("child task completion contract must match role contract")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "roleNames": self.role_names,
            "roleCount": len(self.role_definitions),
            "childTaskCount": len(self.task_plan.child_task_specs),
            "verifierChainRefs": self.task_plan.verifier_chain_refs,
            "sourceEvidenceRefCount": len(self.evidence_policy.source_evidence_refs),
            "claimEvidenceRefCount": len(self.evidence_policy.claim_evidence_refs),
            "taskEvidenceRefCount": len(self.evidence_policy.task_evidence_refs),
            "adkUsageNotes": self.adk_usage_notes,
            "defaultOff": self.default_off,
            "localOnly": self.local_only,
            "fakeProviderOnly": self.fake_provider_only,
        }


def build_research_meta_role_definitions() -> tuple[MetaChildRoleDefinition, ...]:
    return tuple(_role_definition(spec) for spec in _ROLE_SPECS)


def build_research_meta_harness_plan(
    *,
    plan_id: str,
    parent_execution_id: str,
    objective_digest: str,
    objective_preview: str,
    evidence_policy: ResearchMetaEvidencePolicy,
) -> ResearchMetaHarnessPlan:
    role_definitions = build_research_meta_role_definitions()
    role_by_name = {spec.name: role for spec, role in zip(_ROLE_SPECS, role_definitions)}
    child_specs = tuple(_child_spec(spec.name, role_by_name[spec.name]) for spec in _ROLE_SPECS)
    task_plan = MetaTaskPlan.model_validate(
        {
            "planId": plan_id,
            "parentExecutionId": parent_execution_id,
            "objectiveDigest": objective_digest,
            "objectivePreview": objective_preview,
            "acceptanceCriteriaRefs": (
                "criteria:research-source-evidence",
                "criteria:research-claim-evidence",
                "criteria:research-task-proof",
            ),
            "childTaskSpecs": child_specs,
            "verifierChainRefs": (
                "verifier:research-evidence-policy",
                "verifier:meta-before-commit",
            ),
            "maxRetryBudget": evidence_policy.max_retry_budget,
            "defaultOff": True,
        }
    )
    return ResearchMetaHarnessPlan.model_validate(
        {
            "roleNames": RESEARCH_META_ROLE_NAMES,
            "roleDefinitions": role_definitions,
            "taskPlan": task_plan,
            "evidencePolicy": evidence_policy,
            "adkUsageNotes": _ADK_USAGE_NOTES,
            "defaultOff": True,
            "localOnly": True,
            "fakeProviderOnly": True,
        }
    )


def accept_research_child_result(
    child_result: RuntimeIssuedChildResult | object,
    policy: ResearchMetaEvidencePolicy | Mapping[str, object],
) -> ChildAcceptanceVerdict:
    parsed_policy = (
        policy
        if isinstance(policy, ResearchMetaEvidencePolicy)
        else ResearchMetaEvidencePolicy.model_validate(policy)
    )
    return accept_child_result(child_result, parsed_policy.to_child_acceptance_policy())


def _validate_typed_evidence_ref_payload(ref: str, field_name: str) -> None:
    payload = ref.split(":", 1)[1] if ":" in ref else ref
    if "." in payload or "/" in payload:
        raise ValueError(f"{field_name} cannot use domain or path-like evidence refs")
    terms = frozenset(term for term in re.split(r"[^a-z0-9]+", payload) if term)
    if terms & _FORBIDDEN_TYPED_REF_TERMS:
        raise ValueError(f"{field_name} cannot use raw, summary, or private evidence refs")


class _RoleSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    role_ref: str
    display_name: str
    allowed_tool_refs: tuple[str, ...]
    context_policy_ref: str
    completion_contract_ref: str
    scope_ref: str
    task_id: str


_ROLE_SPECS: tuple[_RoleSpec, ...] = (
    _RoleSpec(
        name="research_searcher",
        role_ref="role:research-searcher",
        display_name="research_searcher",
        allowed_tool_refs=("tool:fake-provider-search-index",),
        context_policy_ref="context:research-search-metadata-only",
        completion_contract_ref="contract:research-source-candidates",
        scope_ref="scope:research-searcher",
        task_id="task:research-searcher",
    ),
    _RoleSpec(
        name="source_inspector",
        role_ref="role:source-inspector",
        display_name="source_inspector",
        allowed_tool_refs=("tool:fake-source-metadata",),
        context_policy_ref="context:research-source-digest-only",
        completion_contract_ref="contract:research-source-proof",
        scope_ref="scope:source-inspector",
        task_id="task:source-inspector",
    ),
    _RoleSpec(
        name="claim_mapper",
        role_ref="role:claim-mapper",
        display_name="claim_mapper",
        allowed_tool_refs=("tool:local-claim-index",),
        context_policy_ref="context:research-claims-only",
        completion_contract_ref="contract:research-claim-graph",
        scope_ref="scope:claim-mapper",
        task_id="task:claim-mapper",
    ),
    _RoleSpec(
        name="research_verifier",
        role_ref="role:research-verifier",
        display_name="research_verifier",
        allowed_tool_refs=("tool:local-evidence-ledger",),
        context_policy_ref="context:research-verifier-digests-only",
        completion_contract_ref="contract:research-evidence-policy",
        scope_ref="scope:research-verifier",
        task_id="task:research-verifier",
    ),
    _RoleSpec(
        name="synthesis_reviewer",
        role_ref="role:synthesis-reviewer",
        display_name="synthesis_reviewer",
        allowed_tool_refs=("tool:local-digest-projection",),
        context_policy_ref="context:research-synthesis-digests-only",
        completion_contract_ref="contract:research-synthesis-review",
        scope_ref="scope:synthesis-reviewer",
        task_id="task:synthesis-reviewer",
    ),
)


def _role_definition(spec: _RoleSpec) -> MetaChildRoleDefinition:
    return MetaChildRoleDefinition.model_validate(
        {
            "roleRef": spec.role_ref,
            "displayName": spec.display_name,
            "domain": "research",
            "allowedToolRefs": spec.allowed_tool_refs,
            "deniedToolRefs": (
                "tool:browser-live-web",
                "tool:channel-send",
                "tool:memory-write",
                "tool:workspace-write",
            ),
            "contextPolicyRef": spec.context_policy_ref,
            "completionContractRef": spec.completion_contract_ref,
            "maxSpawnDepth": 1,
            "defaultOff": True,
        }
    )


def _child_spec(name: str, role: MetaChildRoleDefinition) -> MetaChildTaskSpec:
    spec = next(item for item in _ROLE_SPECS if item.name == name)
    return MetaChildTaskSpec.model_validate(
        {
            "taskId": spec.task_id,
            "roleRef": role.role_ref,
            "scopeRef": spec.scope_ref,
            "allowedToolRefs": role.allowed_tool_refs,
            "contextBudget": MetaChildContextBudget(
                maxInputTokens=4000,
                maxOutputTokens=1200,
                reservedEvidenceTokens=400,
            ),
            "completionContractRef": role.completion_contract_ref,
            "deliveryMode": "return",
            "requiresEvidenceEnvelope": True,
        }
    )


__all__ = [
    "RESEARCH_META_ROLE_NAMES",
    "ResearchMetaEvidencePolicy",
    "ResearchMetaHarnessPlan",
    "accept_research_child_result",
    "build_research_meta_harness_plan",
    "build_research_meta_role_definitions",
]
