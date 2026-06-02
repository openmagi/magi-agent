from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.meta_orchestration.child_acceptance import (
    ChildAcceptancePolicy,
    ChildAcceptanceVerdict,
    RetryExhaustedStatus,
    RuntimeIssuedChildResult,
    accept_child_result,
)
from magi_agent.meta_orchestration.child_roles import (
    MetaChildRoleDefinition,
    MetaChildRoleRegistry,
)
from magi_agent.meta_orchestration.task_plan import (
    MetaChildContextBudget,
    MetaChildTaskSpec,
    MetaTaskPlan,
    _copy_update_alias,
    _validate_public_ref,
    _validate_public_text,
    _validate_ref_tuple,
)


CODING_META_ROLE_NAMES: tuple[str, ...] = (
    "code_reader",
    "code_editor",
    "test_runner",
    "code_reviewer",
)

_ADK_USAGE_NOTES = (
    "Coding adapter metadata only; ADK Agent, Runner, FunctionTool, callbacks, "
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
_EVIDENCE_REF_PREFIXES_BY_FIELD = {
    "read_evidence_refs": ("read:",),
    "diff_evidence_refs": ("diff:",),
    "test_evidence_refs": ("test:",),
    "checkpoint_evidence_refs": ("checkpoint:",),
}
_FORBIDDEN_REF_TERMS = frozenset(
    {
        "auth",
        "cookie",
        "failed",
        "key",
        "log",
        "output",
        "private",
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
_REVISION_RE = re.compile(r"^rev:[1-9][0-9]*$")


class _CodingMetaModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        raise TypeError("model_construct is disabled for coding meta adapter contracts")

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


CodingMetaTestStatus = Literal["pass", "failed"]


class CodingMetaEvidencePolicy(_CodingMetaModel):
    parent_execution_id: str = Field(alias="parentExecutionId")
    child_execution_id: str = Field(alias="childExecutionId")
    task_id: str = Field(alias="taskId")
    parent_policy_snapshot_id: str = Field(alias="parentPolicySnapshotId")
    child_policy_snapshot_id: str = Field(alias="childPolicySnapshotId")
    runtime_receipt_ref: str = Field(alias="runtimeReceiptRef")
    read_evidence_refs: tuple[str, ...] = Field(alias="readEvidenceRefs")
    diff_evidence_refs: tuple[str, ...] = Field(alias="diffEvidenceRefs")
    test_evidence_refs: tuple[str, ...] = Field(alias="testEvidenceRefs")
    checkpoint_evidence_refs: tuple[str, ...] = Field(alias="checkpointEvidenceRefs")
    last_read_revision_ref: str = Field(alias="lastReadRevisionRef")
    latest_mutation_revision_ref: str = Field(alias="latestMutationRevisionRef")
    test_status: CodingMetaTestStatus = Field(alias="testStatus")
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
    workspace_write_allowed: Literal[False] = Field(default=False, alias="workspaceWriteAllowed")
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

    @field_validator(
        "read_evidence_refs",
        "diff_evidence_refs",
        "test_evidence_refs",
        "checkpoint_evidence_refs",
    )
    @classmethod
    def _validate_evidence_refs(
        cls,
        value: Sequence[str],
        info: Any,
    ) -> tuple[str, ...]:
        refs = _validate_ref_tuple(value, info.field_name)
        if not refs:
            raise ValueError(f"{info.field_name} must include at least one evidence ref")
        allowed_prefixes = _EVIDENCE_REF_PREFIXES_BY_FIELD[str(info.field_name)]
        for ref in refs:
            lowered = ref.lower()
            if not lowered.startswith(allowed_prefixes):
                raise ValueError(f"{info.field_name} must use typed coding evidence refs")
            _validate_typed_evidence_ref_payload(lowered, info.field_name)
        return refs

    @field_validator("last_read_revision_ref", "latest_mutation_revision_ref")
    @classmethod
    def _validate_revision_ref(cls, value: str, info: Any) -> str:
        clean = _validate_public_ref(value, info.field_name)
        if _REVISION_RE.fullmatch(clean) is None:
            raise ValueError(f"{info.field_name} must be a rev:N ref")
        return clean

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
        "workspace_write_allowed",
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
            raise ValueError("coding evidence refs must not overlap")
        if self.test_status == "failed" and any(ref.endswith(":failed") for ref in self.test_evidence_refs):
            raise ValueError("failed test refs cannot satisfy coding completion")
        return self

    def required_evidence_refs(self) -> tuple[str, ...]:
        return (
            *self.read_evidence_refs,
            *self.diff_evidence_refs,
            *self.test_evidence_refs,
            *self.checkpoint_evidence_refs,
        )

    def stale_read_missing_refs(self) -> tuple[str, ...]:
        if _revision_number(self.last_read_revision_ref) >= _revision_number(
            self.latest_mutation_revision_ref,
        ):
            return ()
        return (f"read:fresh-after-rev-{_revision_number(self.latest_mutation_revision_ref)}",)

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


class CodingMetaHarnessPlan(_CodingMetaModel):
    role_names: tuple[str, ...] = Field(alias="roleNames")
    role_definitions: tuple[MetaChildRoleDefinition, ...] = Field(alias="roleDefinitions")
    task_plan: MetaTaskPlan = Field(alias="taskPlan")
    evidence_policy: CodingMetaEvidencePolicy = Field(alias="evidencePolicy")
    adk_usage_notes: str = Field(default=_ADK_USAGE_NOTES, alias="adkUsageNotes")
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")

    @field_validator("role_names")
    @classmethod
    def _validate_role_names(cls, value: Sequence[str]) -> tuple[str, ...]:
        names = tuple(value)
        if names != CODING_META_ROLE_NAMES:
            raise ValueError("roleNames must match the first-party coding meta role set")
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
        if len(roles) != len(CODING_META_ROLE_NAMES):
            raise ValueError("roleDefinitions must include all coding meta roles")
        MetaChildRoleRegistry(roles)
        expected_by_ref = {
            role.role_ref: role.model_dump(by_alias=True, mode="python", warnings=False)
            for role in build_coding_meta_role_definitions()
        }
        if {role.role_ref for role in roles} != set(expected_by_ref):
            raise ValueError("roleDefinitions must use the first-party coding role refs")
        for role in roles:
            actual_role = role.model_dump(by_alias=True, mode="python", warnings=False)
            if actual_role != expected_by_ref[role.role_ref]:
                raise ValueError("roleDefinitions must match first-party coding role specs")
        return roles

    @field_validator("task_plan")
    @classmethod
    def _revalidate_task_plan(cls, value: MetaTaskPlan) -> MetaTaskPlan:
        return MetaTaskPlan.model_validate(value.model_dump(by_alias=True))

    @field_validator("evidence_policy")
    @classmethod
    def _revalidate_evidence_policy(
        cls,
        value: CodingMetaEvidencePolicy,
    ) -> CodingMetaEvidencePolicy:
        return CodingMetaEvidencePolicy.model_validate(value.model_dump(by_alias=True))

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
        expected_task_id_by_role_ref = {spec.role_ref: spec.task_id for spec in _ROLE_SPECS}
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
            raise ValueError("coding child task specs must map one-to-one to role definitions")
        if len(task_ids) != len(expected_task_ids) or set(task_ids) != expected_task_ids:
            raise ValueError("coding child task specs must match first-party task ids")
        if self.evidence_policy.parent_execution_id != self.task_plan.parent_execution_id:
            raise ValueError("coding evidence policy parent must match task plan parent")
        if self.evidence_policy.task_id not in task_ids:
            raise ValueError("coding evidence policy task must be one composed child task")
        if self.evidence_policy.max_retry_budget != self.task_plan.max_retry_budget:
            raise ValueError("coding evidence policy retry budget must match task plan")
        for child in self.task_plan.child_task_specs:
            role = roles_by_ref[child.role_ref]
            if child.task_id != expected_task_id_by_role_ref[child.role_ref]:
                raise ValueError("coding child task id must match the first-party role")
            actual_child = child.model_dump(by_alias=True, mode="python", warnings=False)
            if actual_child != expected_child_specs_by_task_id[child.task_id]:
                raise ValueError("coding child task specs must match first-party specs")
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
            "readEvidenceRefCount": len(self.evidence_policy.read_evidence_refs),
            "diffEvidenceRefCount": len(self.evidence_policy.diff_evidence_refs),
            "testEvidenceRefCount": len(self.evidence_policy.test_evidence_refs),
            "checkpointEvidenceRefCount": len(self.evidence_policy.checkpoint_evidence_refs),
            "adkUsageNotes": self.adk_usage_notes,
            "defaultOff": self.default_off,
            "localOnly": self.local_only,
            "fakeProviderOnly": self.fake_provider_only,
        }


def build_coding_meta_role_definitions() -> tuple[MetaChildRoleDefinition, ...]:
    return tuple(_role_definition(spec) for spec in _ROLE_SPECS)


def build_coding_meta_harness_plan(
    *,
    plan_id: str,
    parent_execution_id: str,
    objective_digest: str,
    objective_preview: str,
    evidence_policy: CodingMetaEvidencePolicy,
) -> CodingMetaHarnessPlan:
    role_definitions = build_coding_meta_role_definitions()
    role_by_name = {spec.name: role for spec, role in zip(_ROLE_SPECS, role_definitions)}
    child_specs = tuple(_child_spec(spec.name, role_by_name[spec.name]) for spec in _ROLE_SPECS)
    task_plan = MetaTaskPlan.model_validate(
        {
            "planId": plan_id,
            "parentExecutionId": parent_execution_id,
            "objectiveDigest": objective_digest,
            "objectivePreview": objective_preview,
            "acceptanceCriteriaRefs": (
                "criteria:coding-read-evidence",
                "criteria:coding-diff-evidence",
                "criteria:coding-test-evidence",
                "criteria:coding-checkpoint-evidence",
            ),
            "childTaskSpecs": child_specs,
            "verifierChainRefs": (
                "verifier:coding-evidence-policy",
                "verifier:meta-before-commit",
            ),
            "maxRetryBudget": evidence_policy.max_retry_budget,
            "defaultOff": True,
        }
    )
    return CodingMetaHarnessPlan.model_validate(
        {
            "roleNames": CODING_META_ROLE_NAMES,
            "roleDefinitions": role_definitions,
            "taskPlan": task_plan,
            "evidencePolicy": evidence_policy,
            "adkUsageNotes": _ADK_USAGE_NOTES,
            "defaultOff": True,
            "localOnly": True,
            "fakeProviderOnly": True,
        }
    )


def accept_coding_child_result(
    child_result: RuntimeIssuedChildResult | object,
    policy: CodingMetaEvidencePolicy | Mapping[str, object],
) -> ChildAcceptanceVerdict:
    parsed_policy = (
        policy
        if isinstance(policy, CodingMetaEvidencePolicy)
        else CodingMetaEvidencePolicy.model_validate(policy)
    )
    generic_verdict = accept_child_result(child_result, parsed_policy.to_child_acceptance_policy())
    if generic_verdict.status != "accepted" and generic_verdict.status != "retry":
        return generic_verdict
    stale_refs = parsed_policy.stale_read_missing_refs()
    if stale_refs:
        retry_remaining = max(parsed_policy.max_retry_budget - parsed_policy.current_attempt, 0)
        if retry_remaining > 0:
            return ChildAcceptanceVerdict._from_evaluation(
                status="retry",
                reason_codes=("missing_required_evidence",),
                accepted_evidence_refs=(),
                missing_evidence_refs=stale_refs,
                retryable=True,
                retry_budget_remaining=retry_remaining,
            )
        return ChildAcceptanceVerdict._from_evaluation(
            status=parsed_policy.exhausted_status,
            reason_codes=("missing_required_evidence", "retry_budget_exhausted"),
            accepted_evidence_refs=(),
            missing_evidence_refs=stale_refs,
            retryable=False,
            retry_budget_remaining=0,
        )
    if parsed_policy.test_status == "failed":
        return ChildAcceptanceVerdict._from_evaluation(
            status="blocked",
            reason_codes=("child_blocked",),
            accepted_evidence_refs=(),
            missing_evidence_refs=("test:passing-required",),
            retryable=False,
            retry_budget_remaining=0,
        )
    return generic_verdict


def _validate_typed_evidence_ref_payload(ref: str, field_name: str) -> None:
    payload = ref.split(":", 1)[1] if ":" in ref else ref
    if "." in payload or "/" in payload:
        raise ValueError(f"{field_name} cannot use path-like evidence refs")
    terms = frozenset(term for term in re.split(r"[^a-z0-9]+", payload) if term)
    if terms & _FORBIDDEN_REF_TERMS:
        raise ValueError(f"{field_name} cannot use raw, failed, summary, or private refs")


def _revision_number(ref: str) -> int:
    return int(ref.split(":", 1)[1])


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
        name="code_reader",
        role_ref="role:code-reader",
        display_name="code_reader",
        allowed_tool_refs=("tool:readonly-repo-files",),
        context_policy_ref="context:coding-read-digests-only",
        completion_contract_ref="contract:coding-read-evidence",
        scope_ref="scope:code-reader",
        task_id="task:code-reader",
    ),
    _RoleSpec(
        name="code_editor",
        role_ref="role:code-editor",
        display_name="code_editor",
        allowed_tool_refs=("tool:local-diff-metadata",),
        context_policy_ref="context:coding-edit-intent-only",
        completion_contract_ref="contract:coding-diff-evidence",
        scope_ref="scope:code-editor",
        task_id="task:code-editor",
    ),
    _RoleSpec(
        name="test_runner",
        role_ref="role:coding-test-runner",
        display_name="test_runner",
        allowed_tool_refs=("tool:local-test-receipt",),
        context_policy_ref="context:coding-test-receipts-only",
        completion_contract_ref="contract:coding-test-evidence",
        scope_ref="scope:test-runner",
        task_id="task:test-runner",
    ),
    _RoleSpec(
        name="code_reviewer",
        role_ref="role:code-reviewer",
        display_name="code_reviewer",
        allowed_tool_refs=("tool:local-review-digest",),
        context_policy_ref="context:coding-review-digests-only",
        completion_contract_ref="contract:coding-review-checkpoint",
        scope_ref="scope:code-reviewer",
        task_id="task:code-reviewer",
    ),
)


def _role_definition(spec: _RoleSpec) -> MetaChildRoleDefinition:
    return MetaChildRoleDefinition.model_validate(
        {
            "roleRef": spec.role_ref,
            "displayName": spec.display_name,
            "domain": "coding",
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
    "CODING_META_ROLE_NAMES",
    "CodingMetaEvidencePolicy",
    "CodingMetaHarnessPlan",
    "accept_coding_child_result",
    "build_coding_meta_harness_plan",
    "build_coding_meta_role_definitions",
]
