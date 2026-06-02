from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from hashlib import sha256
from typing import Literal, Self
from weakref import finalize

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openmagi_core_agent.research.acceptance_criteria import (
    ResearchAcceptanceCriterion,
    ResearchAcceptanceCriteriaSet,
)
from openmagi_core_agent.research.claim_graph import ResearchClaimGraph
from openmagi_core_agent.research.claim_graph import ResearchClaimNode
from openmagi_core_agent.research.evidence_graph import (
    ResearchEvidenceGraph,
    ResearchMissingEvidenceReason,
)
from openmagi_core_agent.research.source_proof import ResearchSourceProofVerdict
from openmagi_core_agent.research.source_proof import project_research_source_proof_verdicts


ResearchRepairAction = Literal[
    "inspect_missing_source",
    "refresh_stale_source",
    "extract_missing_span",
    "downgrade_weak_claim",
    "omit_unsupported_claim",
    "request_user_clarification",
    "return_partial_with_missing_work_report",
]
ResearchRepairReasonCode = Literal[
    "missing_source",
    "stale_source",
    "missing_span",
    "weak_claim",
    "unsupported_claim",
    "not_evaluated_claim",
    "task_incomplete",
    "bounded_retries_exhausted",
]
ResearchRepairStatus = Literal[
    "no_repair_needed",
    "repair_planned",
    "clarification_required",
    "partial_report",
]
ResearchRepairActionStatus = Literal["planned", "terminal"]
UnsupportedClaimStrategy = Literal["omit", "repair"]
WeakClaimStrategy = Literal["downgrade", "repair"]

RESEARCH_REPAIR_ACTIONS: tuple[ResearchRepairAction, ...] = (
    "inspect_missing_source",
    "refresh_stale_source",
    "extract_missing_span",
    "downgrade_weak_claim",
    "omit_unsupported_claim",
    "request_user_clarification",
    "return_partial_with_missing_work_report",
)

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="never",
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{12,}|"
    r"AIza[0-9A-Za-z_-]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/home/[^,\s\"']+|/root/[^,\s\"']+|"
    r"/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_UNSAFE_TEXT_RE = re.compile(
    r"https?://|file://|raw[_ -]?(?:source|transcript|tool|prompt|output|result|log)|"
    r"source[_ -]?(?:body|content|html|text)|hidden[_ -]?reasoning|"
    r"chain[_ -]?of[_ -]?thought|authorization|cookie|set-cookie|"
    r"api[_ -]?key|secret|token|model[_ -]?summary|model[_ -]?generated[_ -]?summary",
    re.IGNORECASE,
)
_ADK_USAGE_NOTES = (
    "Metadata only; no ADK Runner, FunctionTool, live provider, browser, model, "
    "memory write, channel delivery, or ToolHost execution is attached."
)
_FAKE_PROVIDER_METADATA: dict[str, object] = {
    "provider": "fake",
    "metadataOnly": True,
    "liveProvider": False,
}
_POLICY_OBJECT_IDS: set[int] = set()
_POLICY_FINGERPRINTS: dict[int, str] = {}
_POLICY_FINALIZERS: dict[int, object] = {}
_ACTION_RECORD_OBJECT_IDS: set[int] = set()
_ACTION_RECORD_FINGERPRINTS: dict[int, str] = {}
_ACTION_RECORD_FINALIZERS: dict[int, object] = {}
_RESULT_OBJECT_IDS: set[int] = set()
_RESULT_FINGERPRINTS: dict[int, str] = {}
_RESULT_FINALIZERS: dict[int, object] = {}


class _ResearchRepairModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for research repair contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)


class ResearchRepairExecutionPosture(_ResearchRepairModel):
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")
    live_execution_allowed: Literal[False] = Field(default=False, alias="liveExecutionAllowed")
    provider_calls_allowed: Literal[False] = Field(default=False, alias="providerCallsAllowed")
    browser_execution_allowed: Literal[False] = Field(
        default=False,
        alias="browserExecutionAllowed",
    )
    tool_execution_allowed: Literal[False] = Field(default=False, alias="toolExecutionAllowed")
    model_calls_allowed: Literal[False] = Field(default=False, alias="modelCallsAllowed")
    memory_writes_allowed: Literal[False] = Field(default=False, alias="memoryWritesAllowed")
    channel_delivery_allowed: Literal[False] = Field(default=False, alias="channelDeliveryAllowed")
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")
    function_tool_attached: Literal[False] = Field(default=False, alias="functionToolAttached")


class ResearchRepairAuthorityFlags(_ResearchRepairModel):
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    provider_called: Literal[False] = Field(default=False, alias="providerCalled")
    browser_opened: Literal[False] = Field(default=False, alias="browserOpened")
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    memory_written: Literal[False] = Field(default=False, alias="memoryWritten")
    channel_delivery_performed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryPerformed",
    )
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")
    function_tool_attached: Literal[False] = Field(default=False, alias="functionToolAttached")


class ResearchRepairFakeProviderMetadata(_ResearchRepairModel):
    provider: Literal["fake"] = "fake"
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    live_provider: Literal[False] = Field(default=False, alias="liveProvider")

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="python", warnings=False)


class ResearchRepairPolicy(_ResearchRepairModel):
    unsupported_claim_strategy: UnsupportedClaimStrategy = Field(
        default="omit",
        alias="unsupportedClaimStrategy",
    )
    weak_claim_strategy: WeakClaimStrategy = Field(default="downgrade", alias="weakClaimStrategy")
    max_repair_attempts: int = Field(default=1, alias="maxRepairAttempts", ge=0)
    repair_attempt: int = Field(default=0, alias="repairAttempt", ge=0)

    @property
    def retries_exhausted(self) -> bool:
        return self.repair_attempt >= self.max_repair_attempts

    @model_validator(mode="after")
    def _bind_creation_fingerprint(self) -> Self:
        _mark_repair_policy_created(self)
        return self

    def validate_not_modified_after_creation(self) -> None:
        _validate_repair_policy_object(self)


class ResearchRepairActionRecord(_ResearchRepairModel):
    action_id: str = Field(alias="actionId")
    action: ResearchRepairAction
    subject_ref_id: str = Field(alias="subjectRefId")
    reason_code: ResearchRepairReasonCode = Field(alias="reasonCode")
    result_status: ResearchRepairActionStatus = Field(alias="resultStatus")
    public_label: str = Field(alias="publicLabel")
    fake_provider_metadata: ResearchRepairFakeProviderMetadata = Field(
        default_factory=ResearchRepairFakeProviderMetadata,
        alias="fakeProviderMetadata",
    )
    digest: str

    @field_validator("action_id", "subject_ref_id")
    @classmethod
    def _validate_public_ref_fields(cls, value: str) -> str:
        return _public_ref(value, "repair ref")

    @field_validator("public_label")
    @classmethod
    def _validate_public_label(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("publicLabel must be non-empty")
        _reject_unsafe_public_text(clean, "publicLabel")
        if len(clean) > 180:
            raise ValueError("publicLabel must be at most 180 characters")
        return clean

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be a sha256 hex digest")
        return value

    @model_validator(mode="after")
    def _validate_digest_binding(self) -> Self:
        if self.digest != _digest_for(self._digest_payload()):
            raise ValueError("digest must be bound to repair action metadata")
        _mark_repair_action_record_created(self)
        return self

    def public_projection(self) -> dict[str, object]:
        _validate_repair_action_record_object(self)
        if self.digest != _digest_for(self._digest_payload()):
            raise ValueError("repair action was modified after creation")
        return {
            "actionId": self.action_id,
            "action": self.action,
            "subjectRefId": self.subject_ref_id,
            "reasonCode": self.reason_code,
            "resultStatus": self.result_status,
            "publicLabel": self.public_label,
            "fakeProviderMetadata": self.fake_provider_metadata.public_projection(),
            "digest": self.digest,
        }

    def _digest_payload(self) -> dict[str, object]:
        return {
            "actionId": self.action_id,
            "action": self.action,
            "subjectRefId": self.subject_ref_id,
            "reasonCode": self.reason_code,
            "resultStatus": self.result_status,
            "publicLabel": self.public_label,
            "fakeProviderMetadata": self.fake_provider_metadata.public_projection(),
        }


class ResearchRepairResult(_ResearchRepairModel):
    repair_result_id: str = Field(alias="repairResultId")
    evidence_graph_id: str = Field(alias="evidenceGraphId")
    status: ResearchRepairStatus
    repair_attempt: int = Field(alias="repairAttempt", ge=0)
    max_repair_attempts: int = Field(alias="maxRepairAttempts", ge=0)
    actions: tuple[ResearchRepairActionRecord, ...]
    missing_work_report: tuple[str, ...] = Field(default=(), alias="missingWorkReport")
    execution_posture: ResearchRepairExecutionPosture = Field(
        default_factory=ResearchRepairExecutionPosture,
        alias="executionPosture",
    )
    authority_flags: ResearchRepairAuthorityFlags = Field(
        default_factory=ResearchRepairAuthorityFlags,
        alias="authorityFlags",
    )
    fake_provider_metadata: ResearchRepairFakeProviderMetadata = Field(
        default_factory=ResearchRepairFakeProviderMetadata,
        alias="fakeProviderMetadata",
    )
    adk_usage_notes: str = Field(default=_ADK_USAGE_NOTES, alias="adkUsageNotes")
    digest: str

    @field_validator("repair_result_id", "evidence_graph_id")
    @classmethod
    def _validate_public_ref_fields(cls, value: str) -> str:
        return _public_ref(value, "repair result ref")

    @field_validator("missing_work_report")
    @classmethod
    def _validate_missing_work_report(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("missingWorkReport must not contain duplicates")
        return tuple(_public_ref(item, "missing work item") for item in value)

    @field_validator("adk_usage_notes")
    @classmethod
    def _validate_adk_usage_notes(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("adkUsageNotes must be non-empty")
        _reject_unsafe_public_text(clean, "adkUsageNotes")
        if len(clean) > 300:
            raise ValueError("adkUsageNotes must be at most 300 characters")
        return clean

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be a sha256 hex digest")
        return value

    @model_validator(mode="after")
    def _validate_result_shape(self) -> Self:
        if self.repair_attempt > self.max_repair_attempts:
            raise ValueError("repairAttempt must not exceed maxRepairAttempts")
        if self.status == "no_repair_needed" and self.actions:
            raise ValueError("no_repair_needed results must not contain actions")
        if self.status != "no_repair_needed" and not self.actions:
            raise ValueError("repair results require at least one action")
        if self.status == "partial_report":
            if self.actions != (
                self.actions[0],
            ) or self.actions[0].action != "return_partial_with_missing_work_report":
                raise ValueError("partial reports require the partial report repair action")
            if not self.missing_work_report:
                raise ValueError("partial reports require missingWorkReport")
        if self.digest != _digest_for(self._digest_payload()):
            raise ValueError("digest must be bound to repair result metadata")
        _mark_repair_result_created(self)
        return self

    def public_projection(self) -> dict[str, object]:
        _validate_repair_result_object(self)
        if self.digest != _digest_for(self._digest_payload()):
            raise ValueError("repair result was modified after creation")
        return {
            "repairResultId": self.repair_result_id,
            "evidenceGraphId": self.evidence_graph_id,
            "status": self.status,
            "repairAttempt": self.repair_attempt,
            "maxRepairAttempts": self.max_repair_attempts,
            "actions": tuple(action.public_projection() for action in self.actions),
            "missingWorkReport": self.missing_work_report,
            "executionPosture": self.execution_posture.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            ),
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            ),
            "fakeProviderMetadata": self.fake_provider_metadata.public_projection(),
            "adkUsageNotes": self.adk_usage_notes,
            "digest": self.digest,
        }

    def _digest_payload(self) -> dict[str, object]:
        return {
            "repairResultId": self.repair_result_id,
            "evidenceGraphId": self.evidence_graph_id,
            "status": self.status,
            "repairAttempt": self.repair_attempt,
            "maxRepairAttempts": self.max_repair_attempts,
            "actions": tuple(action.public_projection() for action in self.actions),
            "missingWorkReport": self.missing_work_report,
            "executionPosture": self.execution_posture.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            ),
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            ),
            "fakeProviderMetadata": self.fake_provider_metadata.public_projection(),
            "adkUsageNotes": self.adk_usage_notes,
        }


def plan_research_repairs(
    evidence_graph: ResearchEvidenceGraph,
    *,
    policy: ResearchRepairPolicy | Mapping[str, object] | None = None,
) -> ResearchRepairResult:
    graph = _validate_repair_graph(evidence_graph)
    repair_policy = (
        ResearchRepairPolicy()
        if policy is None
        else ResearchRepairPolicy.model_validate(policy)
    )
    repair_policy.validate_not_modified_after_creation()
    missing_work_report = _missing_work_report(graph)
    if missing_work_report and repair_policy.retries_exhausted:
        return _repair_result(
            graph=graph,
            policy=repair_policy,
            status="partial_report",
            actions=(
                _action_record(
                    index=1,
                    action="return_partial_with_missing_work_report",
                    subject_ref_id=graph.evidence_graph_id,
                    reason_code="bounded_retries_exhausted",
                    result_status="terminal",
                    public_label="Return partial report with missing work.",
                ),
            ),
            missing_work_report=missing_work_report,
        )

    actions = _dedupe_actions(
        (
            *_missing_reason_repair_actions(graph, repair_policy),
            *_source_repair_actions(graph.source_proof_verdicts),
            *_claim_repair_actions(graph, repair_policy),
            *_criterion_repair_actions(graph.acceptance_criteria.criteria),
        )
    )
    if not actions and missing_work_report:
        actions = (
            _planned(
                "request_user_clarification",
                graph.evidence_graph_id,
                "task_incomplete",
                "Request clarification from user.",
                result_status="terminal",
            ),
        )
        status: ResearchRepairStatus = "clarification_required"
    elif not actions:
        status = "no_repair_needed"
    elif any(action.action == "request_user_clarification" for action in actions):
        status = "clarification_required"
    else:
        status = "repair_planned"
    return _repair_result(
        graph=graph,
        policy=repair_policy,
        status=status,
        actions=tuple(
            _action_record(
                index=index,
                action=action.action,
                subject_ref_id=action.subject_ref_id,
                reason_code=action.reason_code,
                result_status=action.result_status,
                public_label=action.public_label,
            )
            for index, action in enumerate(actions, start=1)
        ),
        missing_work_report=missing_work_report if status == "clarification_required" else (),
    )


class _PlannedAction(BaseModel):
    model_config = _MODEL_CONFIG

    action: ResearchRepairAction
    subject_ref_id: str
    reason_code: ResearchRepairReasonCode
    result_status: ResearchRepairActionStatus
    public_label: str


def _validate_repair_graph(evidence_graph: object) -> ResearchEvidenceGraph:
    if not isinstance(evidence_graph, ResearchEvidenceGraph):
        raise TypeError("repair planning requires a ResearchEvidenceGraph contract")
    for verdict in evidence_graph.source_proof_verdicts:
        _validate_source_verdict(verdict)
    if not isinstance(evidence_graph.claim_graph, ResearchClaimGraph):
        raise TypeError("repair planning requires a validated claimGraph")
    for claim in evidence_graph.claim_graph.claims:
        if not isinstance(claim, ResearchClaimNode):
            raise TypeError("repair planning requires validated claim node entries")
    try:
        evidence_graph.claim_graph.public_projection()
    except ValueError as exc:
        raise ValueError("claimGraph was modified after validation") from exc
    if not isinstance(evidence_graph.acceptance_criteria, ResearchAcceptanceCriteriaSet):
        raise TypeError("repair planning requires validated acceptanceCriteria")
    for criterion in evidence_graph.acceptance_criteria.criteria:
        if not isinstance(criterion, ResearchAcceptanceCriterion):
            raise TypeError("repair planning requires validated acceptance criterion entries")
        try:
            ResearchAcceptanceCriterion.model_validate(
                criterion.model_dump(by_alias=True, mode="python", warnings=False)
            )
        except ValueError as exc:
            raise ValueError("acceptance criterion was modified after validation") from exc
    try:
        evidence_graph.acceptance_criteria.public_projection()
    except ValueError as exc:
        raise ValueError("acceptanceCriteria was modified after validation") from exc
    for reason in evidence_graph.missing_evidence_reasons:
        if not isinstance(reason, ResearchMissingEvidenceReason):
            raise TypeError("repair planning requires validated missing evidence reasons")
        try:
            reason.public_projection()
        except ValueError as exc:
            raise ValueError("missing evidence reason was modified after validation") from exc
    try:
        evidence_graph.public_projection()
    except ValueError as exc:
        raise ValueError("evidenceGraph was modified after validation") from exc
    return evidence_graph


def _source_repair_actions(source_verdicts: Iterable[object]) -> tuple[_PlannedAction, ...]:
    actions: list[_PlannedAction] = []
    for verdict in source_verdicts:
        verdict = _validate_source_verdict(verdict)
        reason_code = verdict.reason_code
        source_ref_id = verdict.source_ref_id
        if reason_code in {"missing_source", "unopened_source"}:
            actions.append(
                _planned(
                    "inspect_missing_source",
                    source_ref_id,
                    "missing_source",
                    "Inspect missing source metadata.",
                )
            )
        elif reason_code == "stale_source":
            actions.append(
                _planned(
                    "refresh_stale_source",
                    source_ref_id,
                    "stale_source",
                    "Refresh stale source metadata.",
                )
            )
        elif reason_code == "source_mismatch":
            actions.append(
                _planned(
                    "extract_missing_span",
                    source_ref_id,
                    "missing_span",
                    "Extract required source span metadata.",
                )
            )
    return tuple(actions)


def _validate_source_verdict(verdict: object) -> ResearchSourceProofVerdict:
    if (
        not isinstance(verdict, ResearchSourceProofVerdict)
        or not verdict.is_source_verifier_issued
    ):
        raise TypeError("source proof verdicts must be verifier-issued contracts")
    try:
        project_research_source_proof_verdicts((verdict,))
    except ValueError as exc:
        raise ValueError("source proof verdict was modified after validation") from exc
    return verdict


def _missing_reason_repair_actions(
    graph: ResearchEvidenceGraph,
    policy: ResearchRepairPolicy,
) -> tuple[_PlannedAction, ...]:
    actions: list[_PlannedAction] = []
    for reason in graph.missing_evidence_reasons:
        if reason.reason_code == "missing_source_proof":
            actions.append(
                _planned(
                    "inspect_missing_source",
                    reason.subject_ref_id,
                    "missing_source",
                    "Inspect missing source metadata.",
                )
            )
        elif reason.reason_code == "stale_source":
            actions.append(
                _planned(
                    "refresh_stale_source",
                    reason.subject_ref_id,
                    "stale_source",
                    "Refresh stale source metadata.",
                )
            )
        elif reason.reason_code == "unsupported_claim":
            if policy.unsupported_claim_strategy == "repair":
                actions.append(
                    _planned(
                        "request_user_clarification",
                        reason.subject_ref_id,
                        "unsupported_claim",
                        "Request clarification from user.",
                        result_status="terminal",
                    )
                )
            else:
                actions.append(
                    _planned(
                        "omit_unsupported_claim",
                        reason.subject_ref_id,
                        "unsupported_claim",
                        "Omit unsupported claim.",
                    )
                )
    return tuple(actions)


def _claim_repair_actions(
    graph: ResearchEvidenceGraph,
    policy: ResearchRepairPolicy,
) -> tuple[_PlannedAction, ...]:
    actions: list[_PlannedAction] = []
    for claim in graph.claim_graph.claims:
        if claim.support_verdict == "weak":
            if policy.weak_claim_strategy == "repair":
                actions.append(
                    _planned(
                        "request_user_clarification",
                        claim.claim_id,
                        "weak_claim",
                        "Request clarification from user.",
                        result_status="terminal",
                    )
                )
            else:
                actions.append(
                    _planned(
                        "downgrade_weak_claim",
                        claim.claim_id,
                        "weak_claim",
                        "Downgrade weak claim.",
                    )
                )
        elif claim.support_verdict == "unsupported":
            if policy.unsupported_claim_strategy == "repair":
                actions.append(
                    _planned(
                        "request_user_clarification",
                        claim.claim_id,
                        "unsupported_claim",
                        "Request clarification from user.",
                        result_status="terminal",
                    )
                )
            else:
                actions.append(
                    _planned(
                        "omit_unsupported_claim",
                        claim.claim_id,
                        "unsupported_claim",
                        "Omit unsupported claim.",
                    )
                )
        elif claim.support_verdict == "not_evaluated":
            actions.append(
                _planned(
                    "request_user_clarification",
                    claim.claim_id,
                    "not_evaluated_claim",
                    "Request clarification from user.",
                    result_status="terminal",
                )
            )
    return tuple(actions)


def _criterion_repair_actions(
    criteria: Iterable[ResearchAcceptanceCriterion],
) -> tuple[_PlannedAction, ...]:
    actions: list[_PlannedAction] = []
    for criterion in criteria:
        if criterion.status in {"missing", "partial"}:
            actions.append(
                _planned(
                    "inspect_missing_source",
                    criterion.criteria_id,
                    "task_incomplete",
                    "Inspect missing source metadata.",
                )
            )
        elif criterion.status == "blocked":
            actions.append(
                _planned(
                    "request_user_clarification",
                    criterion.criteria_id,
                    "task_incomplete",
                    "Request clarification from user.",
                    result_status="terminal",
                )
            )
    return tuple(actions)


def _planned(
    action: ResearchRepairAction,
    subject_ref_id: str,
    reason_code: ResearchRepairReasonCode,
    public_label: str,
    *,
    result_status: ResearchRepairActionStatus = "planned",
) -> _PlannedAction:
    return _PlannedAction(
        action=action,
        subject_ref_id=subject_ref_id,
        reason_code=reason_code,
        result_status=result_status,
        public_label=public_label,
    )


def _dedupe_actions(actions: Iterable[_PlannedAction]) -> tuple[_PlannedAction, ...]:
    deduped: list[_PlannedAction] = []
    seen: set[tuple[str, str, str]] = set()
    for action in actions:
        key = (action.action, action.subject_ref_id, action.reason_code)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(action)
    return tuple(deduped)


def _missing_work_report(graph: ResearchEvidenceGraph) -> tuple[str, ...]:
    items: list[str] = []
    for criterion in graph.acceptance_criteria.criteria:
        if criterion.status in {"missing", "partial", "blocked"}:
            items.append(f"{criterion.criteria_id}:{criterion.status}")
    for reason in graph.missing_evidence_reasons:
        if reason.reason_code == "not_required_for_criterion":
            continue
        items.append(f"{reason.subject_ref_id}:{reason.reason_code}")
    return tuple(dict.fromkeys(items))


def _action_record(
    *,
    index: int,
    action: ResearchRepairAction,
    subject_ref_id: str,
    reason_code: ResearchRepairReasonCode,
    result_status: ResearchRepairActionStatus,
    public_label: str,
) -> ResearchRepairActionRecord:
    action_id = f"repair-action:{index:04d}"
    payload = {
        "actionId": action_id,
        "action": action,
        "subjectRefId": subject_ref_id,
        "reasonCode": reason_code,
        "resultStatus": result_status,
        "publicLabel": public_label,
        "fakeProviderMetadata": ResearchRepairFakeProviderMetadata().public_projection(),
    }
    return ResearchRepairActionRecord(**payload, digest=_digest_for(payload))


def _repair_result(
    *,
    graph: ResearchEvidenceGraph,
    policy: ResearchRepairPolicy,
    status: ResearchRepairStatus,
    actions: tuple[ResearchRepairActionRecord, ...],
    missing_work_report: tuple[str, ...],
) -> ResearchRepairResult:
    result_id = f"repair-result:{_short_digest(graph.evidence_graph_id)}"
    payload = {
        "repairResultId": result_id,
        "evidenceGraphId": graph.evidence_graph_id,
        "status": status,
        "repairAttempt": policy.repair_attempt,
        "maxRepairAttempts": policy.max_repair_attempts,
        "actions": tuple(action.public_projection() for action in actions),
        "missingWorkReport": missing_work_report,
        "executionPosture": ResearchRepairExecutionPosture().model_dump(
            by_alias=True,
            mode="python",
            warnings=False,
        ),
        "authorityFlags": ResearchRepairAuthorityFlags().model_dump(
            by_alias=True,
            mode="python",
            warnings=False,
        ),
        "fakeProviderMetadata": ResearchRepairFakeProviderMetadata().public_projection(),
        "adkUsageNotes": _ADK_USAGE_NOTES,
    }
    return ResearchRepairResult(**payload, digest=_digest_for(payload))


def _mark_repair_policy_created(policy: ResearchRepairPolicy) -> None:
    object_id = id(policy)
    if object_id in _POLICY_FINGERPRINTS:
        return
    _POLICY_OBJECT_IDS.add(object_id)
    _POLICY_FINGERPRINTS[object_id] = _model_fingerprint(policy)
    _POLICY_FINALIZERS[object_id] = finalize(
        policy,
        _discard_repair_policy_object_id,
        object_id,
    )


def _discard_repair_policy_object_id(object_id: int) -> None:
    _POLICY_OBJECT_IDS.discard(object_id)
    _POLICY_FINGERPRINTS.pop(object_id, None)
    _POLICY_FINALIZERS.pop(object_id, None)


def _validate_repair_policy_object(policy: ResearchRepairPolicy) -> None:
    object_id = id(policy)
    if object_id not in _POLICY_OBJECT_IDS:
        raise ValueError("repair policy was not created by the repair contract")
    expected = _POLICY_FINGERPRINTS.get(object_id)
    if expected != _model_fingerprint(policy):
        raise ValueError("repair policy was modified after creation")
    ResearchRepairPolicy.model_validate(
        policy.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _mark_repair_action_record_created(action: ResearchRepairActionRecord) -> None:
    object_id = id(action)
    if object_id in _ACTION_RECORD_FINGERPRINTS:
        return
    _ACTION_RECORD_OBJECT_IDS.add(object_id)
    _ACTION_RECORD_FINGERPRINTS[object_id] = _model_fingerprint(action)
    _ACTION_RECORD_FINALIZERS[object_id] = finalize(
        action,
        _discard_repair_action_record_object_id,
        object_id,
    )


def _discard_repair_action_record_object_id(object_id: int) -> None:
    _ACTION_RECORD_OBJECT_IDS.discard(object_id)
    _ACTION_RECORD_FINGERPRINTS.pop(object_id, None)
    _ACTION_RECORD_FINALIZERS.pop(object_id, None)


def _validate_repair_action_record_object(action: ResearchRepairActionRecord) -> None:
    object_id = id(action)
    if object_id not in _ACTION_RECORD_OBJECT_IDS:
        raise ValueError("repair action was not created by the repair contract")
    expected = _ACTION_RECORD_FINGERPRINTS.get(object_id)
    if expected != _model_fingerprint(action):
        raise ValueError("repair action was modified after creation")
    ResearchRepairActionRecord.model_validate(
        action.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _mark_repair_result_created(result: ResearchRepairResult) -> None:
    object_id = id(result)
    if object_id in _RESULT_FINGERPRINTS:
        return
    _RESULT_OBJECT_IDS.add(object_id)
    _RESULT_FINGERPRINTS[object_id] = _model_fingerprint(result)
    _RESULT_FINALIZERS[object_id] = finalize(
        result,
        _discard_repair_result_object_id,
        object_id,
    )


def _discard_repair_result_object_id(object_id: int) -> None:
    _RESULT_OBJECT_IDS.discard(object_id)
    _RESULT_FINGERPRINTS.pop(object_id, None)
    _RESULT_FINALIZERS.pop(object_id, None)


def _validate_repair_result_object(result: ResearchRepairResult) -> None:
    object_id = id(result)
    if object_id not in _RESULT_OBJECT_IDS:
        raise ValueError("repair result was not created by the repair contract")
    expected = _RESULT_FINGERPRINTS.get(object_id)
    if expected != _model_fingerprint(result):
        raise ValueError("repair result was modified after creation")
    ResearchRepairResult.model_validate(
        result.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _public_ref(value: str, field_name: str) -> str:
    clean = value.strip()
    _reject_unsafe_public_text(clean, field_name)
    if not _PUBLIC_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe public ref")
    return clean


def _reject_unsafe_public_text(value: str, field_name: str) -> None:
    if _SECRET_TEXT_RE.search(value):
        raise ValueError(f"{field_name} must not contain credential material")
    if _PRIVATE_PATH_RE.search(value):
        raise ValueError(f"{field_name} must not contain private paths")
    if _UNSAFE_TEXT_RE.search(value):
        raise ValueError(f"{field_name} must not contain raw or unsafe text")


def _digest_for(payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _model_fingerprint(model: BaseModel) -> str:
    return _digest_for(model.model_dump(by_alias=True, mode="python", warnings=False))


def _short_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "RESEARCH_REPAIR_ACTIONS",
    "ResearchRepairAction",
    "ResearchRepairActionRecord",
    "ResearchRepairAuthorityFlags",
    "ResearchRepairExecutionPosture",
    "ResearchRepairPolicy",
    "ResearchRepairResult",
    "plan_research_repairs",
]
