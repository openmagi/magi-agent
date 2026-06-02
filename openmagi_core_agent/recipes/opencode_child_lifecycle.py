from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from types import MappingProxyType
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from openmagi_core_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    project_child_runtime_envelope,
)
from openmagi_core_agent.evidence.subagent import (
    ChildEvidenceEnvelope,
    ParentEvidenceAggregation,
    PublicChildAggregateReport,
    aggregate_child_evidence,
    match_delegated_requirement,
    public_child_aggregate_report,
)
from openmagi_core_agent.research.child_roles import (
    ResearchChildEvidenceAdmissionDecision,
    ResearchChildProofRef,
    ResearchChildRoleName,
    admit_research_child_evidence,
)


OpenCodeScoutChildLifecycleProfileKey = Literal[
    "scout_repo_fixture",
    "scout_external_repo",
    "scout_web_docs",
]
OpenCodeScoutChildLifecycleStatus = Literal[
    "disabled",
    "ready",
    "blocked",
    "retry",
    "accepted",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="never",
    hide_input_in_errors=True,
)
_ACTIVATION_GATE = "local-child-envelope-fixtures-only"
_ADK_USAGE_NOTES = (
    "ADK Agent/Runner child role metadata and callback vocabulary only; no live "
    "runner, model, tool, browser, channel, memory, or workspace authority is attached."
)
_ATTACHMENT_FLAGS: Mapping[str, bool] = MappingProxyType(
    {
        "adkRunnerInvoked": False,
        "childExecutionAttached": False,
        "liveToolDispatched": False,
        "modelCalled": False,
        "browserExecuted": False,
        "channelDelivered": False,
        "memoryWritten": False,
        "workspaceMutated": False,
        "rawChildOutputProjected": False,
        "productionAuthority": False,
    }
)
_UNSAFE_PUBLIC_KEY_RE = re.compile(
    r"(?:raw|private|path|authorization|cookie|secret|token|credential|"
    r"transcript|prompt|reasoning|toollog|tool_log|callback|session|query|"
    r"url|uri)",
    re.IGNORECASE,
)
_ROOT_SAFE_PUBLIC_CONTROL_KEYS = frozenset(
    {
        "adkUsageNotes",
        "browserExecutionAllowed",
        "channelDeliveryAllowed",
        "childExecutionAttached",
        "defaultOff",
        "fixtureOnly",
        "liveAuthorityAllowed",
        "localOnly",
        "memoryWritesAllowed",
        "modelCallsAllowed",
        "rawChildOutputProjectionAllowed",
        "sessionRuntimeAttached",
        "toolExecutionAllowed",
        "workspaceMutationAllowed",
    }
)
_AGGREGATE_REPORT_SAFE_PUBLIC_CONTROL_KEYS = frozenset(
    {
        "apiAttached",
        "artifactRuntimeAttached",
        "canaryAttached",
        "childExecutionAttached",
        "dashboardAttached",
        "enforcementAttached",
        "executionAttached",
        "routeAttached",
        "runnerAttached",
        "sessionRuntimeAttached",
        "trafficAttached",
    }
)
_RESERVED_PUBLIC_CONTROL_KEYS = (
    _ROOT_SAFE_PUBLIC_CONTROL_KEYS
    | frozenset(_ATTACHMENT_FLAGS)
    | _AGGREGATE_REPORT_SAFE_PUBLIC_CONTROL_KEYS
)
_CHILD_AGGREGATE_ROOT_KEYS = frozenset(
    {
        "parentExecutionId",
        "state",
        "children",
        *_AGGREGATE_REPORT_SAFE_PUBLIC_CONTROL_KEYS,
    }
)
_CHILD_AGGREGATE_CHILD_KEYS = frozenset(
    {
        "executionId",
        "agentId",
        "taskId",
        "parentExecutionId",
        "policySnapshotId",
        "status",
        "matchedTypes",
        "missingTypes",
        "blockingFailures",
        "auditFailures",
    }
)
_CHILD_AGGREGATE_FAILURE_KEYS = frozenset(
    {
        "code",
        "contractId",
        "requirementType",
    }
)
_NORMALIZED_RESERVED_PUBLIC_CONTROL_KEYS = frozenset(
    re.sub(r"[^a-z0-9]", "", key.casefold()) for key in _RESERVED_PUBLIC_CONTROL_KEYS
)
_UNSAFE_PUBLIC_TEXT_RE = re.compile(
    r"(?:/Users/|/home/|/workspace/|/data/bots/|/var/lib/|raw[_ -]?(?:child|"
    r"summary|transcript|tool|output|result|log)|hidden[_ -]?reasoning|"
    r"authorization|cookie|secret|token|callback|session|code=|https?://|"
    r"model\s*calls?\s*allowed|modelcallsallowed|live\s+model|"
    r"tool\s*execution\s*allowed|toolexecutionallowed|runner\s*attached|"
    r"runnerattached|production\s*authority|productionauthority|"
    r"browser|workspace\s+mutation|workspacemutated|live\s+authority|"
    r"bearer\s+[A-Za-z0-9._~+/=-]{6,})",
    re.IGNORECASE,
)


class OpenCodeScoutChildLifecycleDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: OpenCodeScoutChildLifecycleStatus
    profile_key: OpenCodeScoutChildLifecycleProfileKey = Field(alias="profileKey")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    expected_role: ResearchChildRoleName = Field(
        default="source_inspector",
        alias="expectedRole",
    )
    child_admission_decision: ResearchChildEvidenceAdmissionDecision | None = Field(
        default=None,
        alias="childAdmissionDecision",
    )
    child_aggregate_report: PublicChildAggregateReport | None = Field(
        default=None,
        alias="childAggregateReport",
    )
    activation_gate: Literal["local-child-envelope-fixtures-only"] = Field(
        default=_ACTIVATION_GATE,
        alias="activationGate",
    )
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fixture_only: Literal[True] = Field(default=True, alias="fixtureOnly")
    live_authority_allowed: Literal[False] = Field(
        default=False,
        alias="liveAuthorityAllowed",
    )
    model_calls_allowed: Literal[False] = Field(default=False, alias="modelCallsAllowed")
    tool_execution_allowed: Literal[False] = Field(default=False, alias="toolExecutionAllowed")
    browser_execution_allowed: Literal[False] = Field(
        default=False,
        alias="browserExecutionAllowed",
    )
    channel_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAllowed",
    )
    memory_writes_allowed: Literal[False] = Field(default=False, alias="memoryWritesAllowed")
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    child_execution_attached: Literal[False] = Field(
        default=False,
        alias="childExecutionAttached",
    )
    child_summary_is_evidence: Literal[False] = Field(
        default=False,
        alias="childSummaryIsEvidence",
    )
    raw_child_output_projection_allowed: Literal[False] = Field(
        default=False,
        alias="rawChildOutputProjectionAllowed",
    )
    adk_usage_notes: Literal[_ADK_USAGE_NOTES] = Field(
        default=_ADK_USAGE_NOTES,
        alias="adkUsageNotes",
    )
    attachment_flags: Mapping[str, bool] = Field(
        default_factory=lambda: dict(_ATTACHMENT_FLAGS),
        alias="attachmentFlags",
    )

    @model_validator(mode="after")
    def _validate_decision_shape(self) -> Self:
        if not self.reason_codes:
            raise ValueError("OpenCode child lifecycle reasonCodes must be non-empty")
        if self.profile_key != "scout_repo_fixture" and self.status in {
            "ready",
            "retry",
            "accepted",
        }:
            raise ValueError("OpenCode child lifecycle is fixture-only for scout_repo_fixture")
        if self.status == "disabled" and self.reason_codes != ("rollout_gate_disabled",):
            raise ValueError("disabled child lifecycle decisions require rollout_gate_disabled")
        if self.status == "ready" and self.reason_codes != (
            "local_child_envelope_fixtures_only",
        ):
            raise ValueError("ready child lifecycle decisions require the local fixture gate")
        if self.status == "accepted":
            if self.reason_codes != ("child_evidence_accepted",):
                raise ValueError("accepted child lifecycle decisions require accepted evidence")
            if self.child_admission_decision is None or (
                self.child_admission_decision.decision != "accept"
            ):
                raise ValueError("accepted child lifecycle decisions require accepted admission")
            if self.child_aggregate_report is None or self.child_aggregate_report.state != "pass":
                raise ValueError("accepted child lifecycle decisions require passing child evidence")
        if self.status == "retry":
            if self.child_admission_decision is None or (
                self.child_admission_decision.decision != "retry"
            ):
                raise ValueError("retry child lifecycle decisions require retry admission")
        if self.status == "blocked" and self.reason_codes == ("child_evidence_accepted",):
            raise ValueError("blocked child lifecycle decisions cannot use accepted evidence")
        if dict(self.attachment_flags) != dict(_ATTACHMENT_FLAGS):
            raise ValueError("OpenCode child lifecycle attachmentFlags must remain false")
        object.__setattr__(
            self,
            "attachment_flags",
            MappingProxyType(dict(_ATTACHMENT_FLAGS)),
        )
        return self

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(self, value: Mapping[str, bool]) -> dict[str, bool]:
        return dict(value)

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls(**values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        projection: dict[str, object] = {
            "status": self.status,
            "profileKey": self.profile_key,
            "reasonCodes": list(self.reason_codes),
            "expectedRole": self.expected_role,
            "activationGate": self.activation_gate,
            "defaultOff": self.default_off,
            "localOnly": self.local_only,
            "fixtureOnly": self.fixture_only,
            "liveAuthorityAllowed": self.live_authority_allowed,
            "modelCallsAllowed": self.model_calls_allowed,
            "toolExecutionAllowed": self.tool_execution_allowed,
            "browserExecutionAllowed": self.browser_execution_allowed,
            "channelDeliveryAllowed": self.channel_delivery_allowed,
            "memoryWritesAllowed": self.memory_writes_allowed,
            "workspaceMutationAllowed": self.workspace_mutation_allowed,
            "childExecutionAttached": self.child_execution_attached,
            "childSummaryIsEvidence": self.child_summary_is_evidence,
            "rawChildOutputProjectionAllowed": self.raw_child_output_projection_allowed,
            "adkUsageNotes": self.adk_usage_notes,
            "attachmentFlags": dict(self.attachment_flags),
        }
        if self.child_admission_decision is not None:
            projection["childAdmissionDecision"] = (
                self.child_admission_decision.public_projection()
            )
        if self.child_aggregate_report is not None:
            projection["childAggregateReport"] = self.child_aggregate_report.model_dump(
                by_alias=True,
                mode="json",
            )
        return _strip_unsafe_projection(projection)


def materialize_opencode_scout_child_lifecycle(
    *,
    profile_key: OpenCodeScoutChildLifecycleProfileKey = "scout_repo_fixture",
    rollout_enabled: bool = False,
    expected_role: ResearchChildRoleName = "source_inspector",
) -> OpenCodeScoutChildLifecycleDecision:
    if not rollout_enabled:
        return _decision(
            status="disabled",
            profile_key=profile_key,
            reason_codes=("rollout_gate_disabled",),
            expected_role=expected_role,
        )
    if profile_key != "scout_repo_fixture":
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("profile_not_enabled_for_child_lifecycle",),
            expected_role=expected_role,
        )
    return _decision(
        status="ready",
        profile_key=profile_key,
        reason_codes=("local_child_envelope_fixtures_only",),
        expected_role=expected_role,
    )


def admit_opencode_scout_child_lifecycle(
    child_output: object,
    *,
    child_evidence_envelope: object | None,
    expected_role: ResearchChildRoleName = "source_inspector",
    child_proof_refs: Iterable[ResearchChildProofRef] = (),
    profile_key: OpenCodeScoutChildLifecycleProfileKey = "scout_repo_fixture",
    rollout_enabled: bool = False,
) -> OpenCodeScoutChildLifecycleDecision:
    gate = materialize_opencode_scout_child_lifecycle(
        profile_key=profile_key,
        rollout_enabled=rollout_enabled,
        expected_role=expected_role,
    )
    if gate.status != "ready":
        return gate

    if type(child_output) is not ChildRuntimeEnvelope:
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("runtime_child_envelope_required",),
            expected_role=expected_role,
        )
    try:
        project_child_runtime_envelope(child_output)
    except ValueError:
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("runtime_child_envelope_required",),
            expected_role=expected_role,
        )

    if not (
        type(child_evidence_envelope) is ChildEvidenceEnvelope
        and child_evidence_envelope.is_runtime_boundary_issued
    ):
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("runtime_child_evidence_envelope_required",),
            expected_role=expected_role,
        )
    if not _child_boundaries_match(child_output, child_evidence_envelope):
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("child_boundary_mismatch",),
            expected_role=expected_role,
        )

    try:
        aggregation = aggregate_child_evidence(
            child_output.parent_boundary,
            (child_evidence_envelope,),
        )
        aggregate_report = public_child_aggregate_report(aggregation)
    except ValueError:
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("child_evidence_aggregation_failed",),
            expected_role=expected_role,
        )
    if aggregation.state != "pass":
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("child_evidence_aggregation_failed",),
            expected_role=expected_role,
            child_aggregate_report=aggregate_report,
        )
    if not aggregation.propagated_evidence:
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("child_evidence_envelope_missing_records",),
            expected_role=expected_role,
            child_aggregate_report=aggregate_report,
        )
    if not _delegated_child_requirements_satisfied(child_output, aggregation):
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("delegated_child_evidence_type_missing",),
            expected_role=expected_role,
            child_aggregate_report=aggregate_report,
        )

    try:
        admission = admit_research_child_evidence(
            child_output,
            expected_role=expected_role,
            child_proof_refs=tuple(child_proof_refs),
        )
    except (TypeError, ValueError):
        return _decision(
            status="blocked",
            profile_key=profile_key,
            reason_codes=("research_child_admission_failed",),
            expected_role=expected_role,
            child_aggregate_report=aggregate_report,
        )
    if admission.decision == "accept":
        return _decision(
            status="accepted",
            profile_key=profile_key,
            reason_codes=("child_evidence_accepted",),
            expected_role=expected_role,
            child_admission_decision=admission,
            child_aggregate_report=aggregate_report,
        )
    if admission.decision == "retry":
        return _decision(
            status="retry",
            profile_key=profile_key,
            reason_codes=admission.reason_codes,
            expected_role=expected_role,
            child_admission_decision=admission,
            child_aggregate_report=aggregate_report,
        )
    return _decision(
        status="blocked",
        profile_key=profile_key,
        reason_codes=admission.reason_codes,
        expected_role=expected_role,
        child_admission_decision=admission,
        child_aggregate_report=aggregate_report,
    )


def _decision(
    *,
    status: OpenCodeScoutChildLifecycleStatus,
    profile_key: OpenCodeScoutChildLifecycleProfileKey,
    reason_codes: tuple[str, ...],
    expected_role: ResearchChildRoleName,
    child_admission_decision: ResearchChildEvidenceAdmissionDecision | None = None,
    child_aggregate_report: PublicChildAggregateReport | None = None,
) -> OpenCodeScoutChildLifecycleDecision:
    return OpenCodeScoutChildLifecycleDecision(
        status=status,
        profileKey=profile_key,
        reasonCodes=reason_codes,
        expectedRole=expected_role,
        childAdmissionDecision=child_admission_decision,
        childAggregateReport=child_aggregate_report,
        attachmentFlags=dict(_ATTACHMENT_FLAGS),
    )


def _child_boundaries_match(
    runtime_envelope: ChildRuntimeEnvelope,
    evidence_envelope: ChildEvidenceEnvelope,
) -> bool:
    boundary = evidence_envelope.boundary
    expected = runtime_envelope.child_boundary
    return (
        boundary.execution_id == expected.execution_id
        and boundary.agent_id == expected.agent_id
        and boundary.parent_execution_id == expected.parent_execution_id
        and boundary.task_id == expected.task_id
        and boundary.policy_snapshot_id == expected.policy_snapshot_id
        and boundary.run_on == "child"
        and boundary.agent_role == "research"
    )


def _delegated_child_requirements_satisfied(
    runtime_envelope: ChildRuntimeEnvelope,
    aggregation: ParentEvidenceAggregation,
) -> bool:
    required = tuple(
        requirement
        for requirement in runtime_envelope.delegated_evidence_requirements
        if requirement.delegation == "delegated_required"
    )
    if not required:
        return False
    return all(
        match_delegated_requirement(
            runtime_envelope.parent_boundary,
            requirement,
            local_records=(),
            child_aggregation=aggregation,
        ).satisfied
        for requirement in required
    )


def _strip_unsafe_projection(
    value: object,
    *,
    field_name: str | None = None,
    path: tuple[str, ...] = (),
) -> object:
    if isinstance(value, Mapping):
        clean: dict[str, object] = {}
        allowed_keys = _child_aggregate_allowed_keys(path)
        for key, nested in value.items():
            key_string = str(key)
            child_path = (*path, key_string)
            if allowed_keys is not None and key_string not in allowed_keys:
                continue
            if (
                not _is_trusted_control_field(key_string, path)
                and (
                    key_string in _RESERVED_PUBLIC_CONTROL_KEYS
                    or _normalize_public_key(key_string)
                    in _NORMALIZED_RESERVED_PUBLIC_CONTROL_KEYS
                    or _UNSAFE_PUBLIC_KEY_RE.search(key_string) is not None
                )
            ):
                continue
            stripped = (
                nested
                if _is_trusted_control_field(key_string, path)
                else _strip_unsafe_projection(
                    nested,
                    field_name=key_string,
                    path=child_path,
                )
            )
            if stripped is not None:
                clean[key_string] = stripped
        return clean
    if isinstance(value, tuple | list):
        return [
            stripped
            for item in value
            if (
                stripped := _strip_unsafe_projection(
                    item,
                    field_name=field_name,
                    path=path,
                )
            )
            is not None
        ]
    if isinstance(value, str):
        if field_name is not None and _is_trusted_control_field(field_name, path[:-1]):
            return value
        normalized_value = _normalize_public_key(value)
        if any(
            reserved_key in normalized_value
            for reserved_key in _NORMALIZED_RESERVED_PUBLIC_CONTROL_KEYS
        ):
            return None
        if _UNSAFE_PUBLIC_TEXT_RE.search(value) is not None:
            return None
        return value
    return value


def _child_aggregate_allowed_keys(path: tuple[str, ...]) -> frozenset[str] | None:
    if path == ("childAggregateReport",):
        return _CHILD_AGGREGATE_ROOT_KEYS
    if path == ("childAggregateReport", "children"):
        return _CHILD_AGGREGATE_CHILD_KEYS
    if path in {
        ("childAggregateReport", "children", "blockingFailures"),
        ("childAggregateReport", "children", "auditFailures"),
    }:
        return _CHILD_AGGREGATE_FAILURE_KEYS
    if path[:1] == ("childAggregateReport",):
        return frozenset()
    return None


def _is_trusted_control_field(key: str, parent_path: tuple[str, ...]) -> bool:
    if not parent_path:
        return key in _ROOT_SAFE_PUBLIC_CONTROL_KEYS
    if parent_path == ("attachmentFlags",):
        return key in _ATTACHMENT_FLAGS
    if parent_path == ("childAggregateReport",):
        return key in _AGGREGATE_REPORT_SAFE_PUBLIC_CONTROL_KEYS
    return False


def _normalize_public_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


__all__ = [
    "OpenCodeScoutChildLifecycleDecision",
    "OpenCodeScoutChildLifecycleProfileKey",
    "OpenCodeScoutChildLifecycleStatus",
    "admit_opencode_scout_child_lifecycle",
    "materialize_opencode_scout_child_lifecycle",
]
