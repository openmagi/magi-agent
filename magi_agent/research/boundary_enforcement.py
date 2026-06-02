from __future__ import annotations

import json
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any, Literal, Self
from weakref import finalize

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from magi_agent.evidence.runtime_issuance import (
    RuntimeIssueAuthority,
    require_runtime_issue_authority,
)
from magi_agent.research.action_claims import (
    detect_research_action_claims,
)
from magi_agent.research.evidence_graph import ResearchEvidenceGraph


ResearchBoundaryStage = Literal[
    "after_source_summary",
    "before_child_result_acceptance",
    "before_intermediate_synthesis",
    "before_final_projection",
    "before_commit",
]
ResearchHarnessKind = Literal["research", "coding", "general"]
ResearchBoundaryStatus = Literal["pass", "skipped", "repair_required", "blocked"]
ResearchBoundaryAction = Literal["pass", "repair", "block"]
ResearchBoundaryReasonCode = Literal[
    "passed",
    "non_research_harness",
    "action_claim_without_receipt",
    "unsupported_claim",
    "missing_child_evidence_envelope",
    "prior_boundary_failed",
    "missing_boundary_history",
    "missing_evidence_graph",
    "missing_source_proof",
    "stale_source",
    "missing_projection_text",
    "missing_execution_lifecycle",
]
ResearchBoundaryRepairAction = Literal[
    "omit_unsupported_claim",
    "downgrade_weak_claim",
    "return_partial_with_missing_work_report",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="never",
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
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
_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]|$)")
_FACT_CUE_RE = re.compile(
    r"\b(?:\d+(?:\.\d+)?|20\d{2}|earned|acquired|launched|revenue|costs?|"
    r"changed|increased?|decreased?|more|less|before|after|current|currently|"
    r"has|have|is|are|was|were)\b",
    re.IGNORECASE,
)
_FACTUAL_CLAIM_KINDS = frozenset({"factual", "numeric", "comparative", "temporal"})
_ADK_USAGE_NOTES = (
    "Research boundary metadata only; no ADK Runner, FunctionTool, live provider, "
    "browser, memory write, channel delivery, or ToolHost execution is attached."
)
_REQUIRED_PRIOR_STAGES: dict[ResearchBoundaryStage, tuple[ResearchBoundaryStage, ...]] = {
    "before_final_projection": ("after_source_summary", "before_intermediate_synthesis"),
    "before_commit": (
        "after_source_summary",
        "before_intermediate_synthesis",
        "before_final_projection",
    ),
}
_GRAPH_REQUIRED_STAGES: frozenset[ResearchBoundaryStage] = frozenset(
    {"before_intermediate_synthesis", "before_final_projection", "before_commit"}
)
_BOUNDARY_DECISION_OBJECT_IDS: set[int] = set()
_BOUNDARY_DECISION_FINGERPRINTS: dict[int, str] = {}
_BOUNDARY_DECISION_FINALIZERS: dict[int, object] = {}
_BOUNDARY_SEQUENCE_DECISION_IDS: dict[str, list[int]] = {}
_BOUNDARY_TASK_DECISION_IDS: dict[str, list[int]] = {}
_BOUNDARY_DECISION_REGISTRY: dict[int, ResearchBoundaryDecision] = {}
_BOUNDARY_SEQUENCE_REF_OBJECT_IDS: set[int] = set()
_BOUNDARY_SEQUENCE_REF_FINGERPRINTS: dict[int, str] = {}
_BOUNDARY_SEQUENCE_REF_FINALIZERS: dict[int, object] = {}
_ACTIVE_RESEARCH_EXECUTION_ID: str | None = None
_ACTIVE_RESEARCH_TASK_SCOPE_DIGEST: str | None = None


class _ResearchBoundaryModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for research boundary contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)


class ResearchBoundaryExecutionPosture(_ResearchBoundaryModel):
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
    memory_writes_allowed: Literal[False] = Field(default=False, alias="memoryWritesAllowed")
    channel_delivery_allowed: Literal[False] = Field(default=False, alias="channelDeliveryAllowed")
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")
    function_tool_attached: Literal[False] = Field(default=False, alias="functionToolAttached")


class ResearchBoundaryAuthorityFlags(_ResearchBoundaryModel):
    final_answer_blocked: Literal[False] = Field(default=False, alias="finalAnswerBlocked")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    channel_delivery_performed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryPerformed",
    )
    memory_written: Literal[False] = Field(default=False, alias="memoryWritten")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class ResearchBoundarySequenceRef(_ResearchBoundaryModel):
    _issued_by_boundary_enforcer: bool = PrivateAttr(default=False)

    sequence_id: str = Field(alias="sequenceId")
    task_scope_digest: str = Field(alias="taskScopeDigest")
    issuer: Literal["openmagi_research_boundary_enforcer"] = (
        "openmagi_research_boundary_enforcer"
    )
    local_only: Literal[True] = Field(default=True, alias="localOnly")

    @classmethod
    def issue_runtime_sequence_ref(
        cls,
        *,
        runtime_authority: RuntimeIssueAuthority | None = None,
        sequence_id: str,
    ) -> Self:
        require_runtime_issue_authority(
            runtime_authority,
            scope="research_boundary",
        )
        if _ACTIVE_RESEARCH_EXECUTION_ID is None:
            raise RuntimeError("research boundary execution lifecycle is not active")
        sequence_ref = cls(
            sequenceId=sequence_id,
            taskScopeDigest=_sequence_label_digest(sequence_id),
        )
        _mark_boundary_sequence_ref_issued(sequence_ref)
        return sequence_ref

    @property
    def is_boundary_sequence_issued(self) -> bool:
        object_id = id(self)
        return (
            bool(self.__pydantic_private__.get("_issued_by_boundary_enforcer"))
            and object_id in _BOUNDARY_SEQUENCE_REF_OBJECT_IDS
            and _BOUNDARY_SEQUENCE_REF_FINGERPRINTS.get(object_id)
            == _sequence_ref_fingerprint(self)
        )

    @field_validator("sequence_id")
    @classmethod
    def _validate_sequence_id(cls, value: str) -> str:
        return _public_ref(value, "sequenceId")

    @field_validator("task_scope_digest")
    @classmethod
    def _validate_task_scope_digest(cls, value: str) -> str:
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("taskScopeDigest must be a sha256 hex digest")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "sequenceId": self.sequence_id,
            "taskScopeDigest": self.task_scope_digest,
            "issuer": self.issuer,
            "localOnly": self.local_only,
        }


class ResearchBoundaryDecision(_ResearchBoundaryModel):
    _issued_by_boundary_enforcer: bool = PrivateAttr(default=False)

    boundary_id: str = Field(alias="boundaryId")
    boundary_sequence_id: str = Field(alias="boundarySequenceId")
    task_scope_digest: str | None = Field(default=None, alias="taskScopeDigest")
    stage: ResearchBoundaryStage
    harness_kind: ResearchHarnessKind = Field(alias="harnessKind")
    status: ResearchBoundaryStatus
    action: ResearchBoundaryAction
    reason_codes: tuple[ResearchBoundaryReasonCode, ...] = Field(alias="reasonCodes")
    repair_actions: tuple[ResearchBoundaryRepairAction, ...] = Field(
        default=(),
        alias="repairActions",
    )
    final_projection_allowed: bool = Field(alias="finalProjectionAllowed")
    execution_posture: ResearchBoundaryExecutionPosture = Field(
        default_factory=ResearchBoundaryExecutionPosture,
        alias="executionPosture",
    )
    authority_flags: ResearchBoundaryAuthorityFlags = Field(
        default_factory=ResearchBoundaryAuthorityFlags,
        alias="authorityFlags",
    )
    adk_usage_notes: str = Field(default=_ADK_USAGE_NOTES, alias="adkUsageNotes")

    @property
    def is_boundary_enforcer_issued(self) -> bool:
        object_id = id(self)
        return (
            bool(self.__pydantic_private__.get("_issued_by_boundary_enforcer"))
            and object_id in _BOUNDARY_DECISION_OBJECT_IDS
            and _BOUNDARY_DECISION_FINGERPRINTS.get(object_id) == _decision_fingerprint(self)
        )

    @field_validator("boundary_id")
    @classmethod
    def _validate_boundary_id(cls, value: str) -> str:
        return _public_ref(value, "boundaryId")

    @field_validator("boundary_sequence_id")
    @classmethod
    def _validate_boundary_sequence_id(cls, value: str) -> str:
        return _public_ref(value, "boundarySequenceId")

    @field_validator("task_scope_digest")
    @classmethod
    def _validate_task_scope_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("taskScopeDigest must be a sha256 hex digest")
        return value

    @field_validator("reason_codes", "repair_actions")
    @classmethod
    def _validate_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("boundary decision tuples must not contain duplicates")
        return value

    @field_validator("adk_usage_notes")
    @classmethod
    def _validate_adk_usage_notes(cls, value: str) -> str:
        clean = value.strip()
        _reject_unsafe_public_text(clean, "adkUsageNotes")
        if len(clean) > 300:
            raise ValueError("adkUsageNotes must be at most 300 characters")
        return clean

    @model_validator(mode="after")
    def _validate_decision_shape(self) -> Self:
        if self.status in {"pass", "skipped"} and self.action != "pass":
            raise ValueError("passing boundary decisions must use action=pass")
        if self.status == "repair_required" and self.action != "repair":
            raise ValueError("repair boundary decisions must use action=repair")
        if self.status == "blocked" and self.action != "block":
            raise ValueError("blocked boundary decisions must use action=block")
        if self.status == "pass" and self.reason_codes != ("passed",):
            raise ValueError("pass decisions must use the passed reason code")
        if self.status == "skipped" and self.reason_codes != ("non_research_harness",):
            raise ValueError("skipped decisions must use the non_research_harness reason code")
        if self.status == "repair_required" and not self.repair_actions:
            raise ValueError("repair decisions require repairActions")
        if self.status in {"blocked", "repair_required"} and self.final_projection_allowed:
            raise ValueError("failed boundary decisions cannot allow final projection")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "boundaryId": self.boundary_id,
            "boundarySequenceId": self.boundary_sequence_id,
            "taskScopeDigest": self.task_scope_digest,
            "stage": self.stage,
            "harnessKind": self.harness_kind,
            "status": self.status,
            "action": self.action,
            "reasonCodes": self.reason_codes,
            "repairActions": self.repair_actions,
            "finalProjectionAllowed": self.final_projection_allowed,
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
            "adkUsageNotes": self.adk_usage_notes,
        }


class ResearchBoundaryRequest(_ResearchBoundaryModel):
    boundary_id: str = Field(alias="boundaryId")
    boundary_sequence_id: str | None = Field(default=None, alias="boundarySequenceId")
    boundary_sequence_ref: ResearchBoundarySequenceRef | None = Field(
        default=None,
        alias="boundarySequenceRef",
    )
    stage: ResearchBoundaryStage
    harness_kind: ResearchHarnessKind = Field(alias="harnessKind")
    candidate_text: str | None = Field(default=None, alias="candidateText")
    evidence_graph: ResearchEvidenceGraph | None = Field(default=None, alias="evidenceGraph")
    child_result_received: bool = Field(default=False, alias="childResultReceived")
    prior_decisions: tuple[ResearchBoundaryDecision, ...] = Field(
        default=(),
        alias="priorDecisions",
    )

    @field_validator("boundary_id")
    @classmethod
    def _validate_boundary_id(cls, value: str) -> str:
        return _public_ref(value, "boundaryId")

    @field_validator("boundary_sequence_id")
    @classmethod
    def _validate_boundary_sequence_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _public_ref(value, "boundarySequenceId")

    @field_validator("candidate_text")
    @classmethod
    def _validate_candidate_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        _reject_unsafe_public_text(clean, "candidateText")
        if len(clean) > 2_000:
            raise ValueError("candidateText must be at most 2000 characters")
        return clean

    @field_validator("prior_decisions")
    @classmethod
    def _validate_prior_decisions(
        cls,
        value: tuple[ResearchBoundaryDecision, ...],
    ) -> tuple[ResearchBoundaryDecision, ...]:
        ids = [decision.boundary_id for decision in value]
        if len(set(ids)) != len(ids):
            raise ValueError("priorDecisions must not contain duplicate boundaryId values")
        return value


def begin_research_boundary_execution(execution_id: str) -> None:
    global _ACTIVE_RESEARCH_EXECUTION_ID
    _clear_boundary_execution_state()
    _ACTIVE_RESEARCH_EXECUTION_ID = _public_ref(execution_id, "executionId")


def end_research_boundary_execution() -> None:
    _clear_boundary_execution_state()


def enforce_research_boundary(request: ResearchBoundaryRequest) -> ResearchBoundaryDecision:
    parsed = ResearchBoundaryRequest.model_validate(request)
    if parsed.harness_kind != "research":
        return _decision(parsed, "skipped", "pass", ("non_research_harness",))

    if _ACTIVE_RESEARCH_EXECUTION_ID is None:
        return _decision(parsed, "blocked", "block", ("missing_execution_lifecycle",))

    if parsed.stage in _GRAPH_REQUIRED_STAGES and parsed.evidence_graph is None:
        return _decision(parsed, "blocked", "block", ("missing_evidence_graph",))

    history_failure = _boundary_history_failure(parsed)
    if history_failure is not None:
        return _decision(parsed, "blocked", "block", (history_failure,))

    graph_failure = _evidence_graph_failure(parsed)
    if graph_failure is not None:
        return _decision(parsed, "blocked", "block", (graph_failure,))

    if parsed.stage in {"before_final_projection", "before_commit"} and not parsed.candidate_text:
        return _decision(parsed, "blocked", "block", ("missing_projection_text",))

    if parsed.stage in {
        "after_source_summary",
        "before_intermediate_synthesis",
        "before_final_projection",
        "before_commit",
    } and _contains_unverified_action_claim(parsed.candidate_text, parsed.evidence_graph):
        return _decision(parsed, "blocked", "block", ("action_claim_without_receipt",))

    if parsed.stage in _GRAPH_REQUIRED_STAGES and _has_unmapped_candidate_fact_claim(
        parsed.candidate_text,
        parsed.evidence_graph,
    ):
        return _decision(parsed, "blocked", "block", ("unsupported_claim",))

    if (
        parsed.stage == "before_child_result_acceptance"
        and parsed.child_result_received
        and not _has_child_evidence_ref(parsed.evidence_graph)
    ):
        return _decision(parsed, "blocked", "block", ("missing_child_evidence_envelope",))

    if parsed.stage in {
        "before_intermediate_synthesis",
        "before_final_projection",
        "before_commit",
    } and _has_unsupported_fact_claim(parsed.evidence_graph):
        return _decision(
            parsed,
            "repair_required",
            "repair",
            ("unsupported_claim",),
            repair_actions=("omit_unsupported_claim",),
        )

    return _decision(parsed, "pass", "pass", ("passed",))


def _contains_unverified_action_claim(
    candidate_text: str | None,
    evidence_graph: ResearchEvidenceGraph | None,
) -> bool:
    if not candidate_text:
        return False
    claims = detect_research_action_claims(candidate_text)
    if not claims:
        return False
    if evidence_graph is None:
        return True
    allowed_by_claim_id = {
        verdict.claim_id: verdict
        for verdict in evidence_graph.action_proof_verdicts
        if getattr(verdict, "is_action_verifier_issued", False)
        and verdict.verdict == "allowed"
        and verdict.reason_code == "receipt_match"
        and verdict.matched_receipt_refs
        and getattr(verdict, "claim_text_digest", None) is not None
    }
    for claim in claims:
        verdict = allowed_by_claim_id.get(claim.claim_id)
        if (
            verdict is None
            or verdict.action_verb != claim.action_verb
            or verdict.claim_text_digest != claim.claim_text_digest
        ):
            return True
    return False


def _boundary_history_failure(
    request: ResearchBoundaryRequest,
) -> ResearchBoundaryReasonCode | None:
    required_stages = _REQUIRED_PRIOR_STAGES.get(request.stage, ())
    if not required_stages:
        return None
    sequence_ref = request.boundary_sequence_ref
    if sequence_ref is None or not sequence_ref.is_boundary_sequence_issued:
        return "missing_boundary_history"
    task_scope_digest = _request_task_scope_digest(request)
    if task_scope_digest is None:
        return "missing_boundary_history"
    if _task_scope_has_failed_decision(task_scope_digest):
        return "prior_boundary_failed"
    issued_decisions = tuple(
        decision
        for decision in _sequence_decisions(_request_sequence_id(request))
        if decision.is_boundary_enforcer_issued
        and decision.harness_kind == "research"
        and decision.boundary_sequence_id == _request_sequence_id(request)
        and decision.task_scope_digest == task_scope_digest
    )
    if any(decision.status != "pass" for decision in issued_decisions):
        return "prior_boundary_failed"
    passing_stage_names = {
        decision.stage
        for decision in issued_decisions
        if decision.status == "pass"
    }
    if any(stage not in passing_stage_names for stage in required_stages):
        return "missing_boundary_history"
    return None


def _has_unmapped_candidate_fact_claim(
    candidate_text: str | None,
    evidence_graph: ResearchEvidenceGraph | None,
) -> bool:
    candidate_digests = _candidate_fact_claim_digests(candidate_text)
    if not candidate_digests:
        return False
    if evidence_graph is None:
        return True
    supported_claim_digests = {
        claim.claim_text_digest
        for claim in evidence_graph.claim_graph.claims
        if _claim_node_supports_candidate_projection(claim)
    }
    return any(digest not in supported_claim_digests for digest in candidate_digests)


def _claim_node_supports_candidate_projection(claim: object) -> bool:
    claim_text_digest = getattr(claim, "claim_text_digest", None)
    if (
        getattr(claim, "claim_kind", None) not in _FACTUAL_CLAIM_KINDS
        or getattr(claim, "support_verdict", None) != "supported"
        or getattr(claim, "projection_mode", None) != "fact"
        or claim_text_digest is None
    ):
        return False
    for support_ref in getattr(claim, "support_refs", ()):
        if (
            getattr(support_ref, "claim_text_digest", None) == claim_text_digest
            and getattr(support_ref, "support_verdict", None) == "supported"
            and getattr(support_ref, "freshness_verdict", None) == "current"
            and getattr(support_ref, "relevance_verdict", None) == "relevant"
            and getattr(support_ref, "is_claim_support_verifier_issued", False)
        ):
            return True
    return False


def _candidate_fact_claim_digests(candidate_text: str | None) -> tuple[str, ...]:
    if not candidate_text:
        return ()
    digests: list[str] = []
    for sentence in _sentences(candidate_text):
        if _is_pure_action_sentence(sentence):
            continue
        digests.append(_candidate_claim_text_digest(sentence))
    return tuple(digests)


def _sentences(text: str) -> tuple[str, ...]:
    return tuple(
        match.group(0).strip()
        for match in _SENTENCE_RE.finditer(text)
        if match.group(0).strip()
    )


def _looks_like_factual_claim(sentence: str) -> bool:
    return bool(_FACT_CUE_RE.search(sentence))


def _is_pure_action_sentence(sentence: str) -> bool:
    return bool(detect_research_action_claims(sentence)) and not _looks_like_factual_claim(
        sentence
    )


def _candidate_claim_text_digest(claim_text: str) -> str:
    material = "\n".join(
        (
            "openmagi-research-boundary-candidate-claim-v1",
            claim_text.strip(),
        )
    )
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _evidence_graph_failure(
    request: ResearchBoundaryRequest,
) -> ResearchBoundaryReasonCode | None:
    if request.stage not in _GRAPH_REQUIRED_STAGES:
        return None
    graph = request.evidence_graph
    if graph is None:
        return "missing_evidence_graph"
    for reason in graph.missing_evidence_reasons:
        if reason.reason_code == "stale_source":
            return "stale_source"
        if reason.reason_code == "missing_source_proof":
            return "missing_source_proof"
    if not graph.source_proof_verdicts:
        return "missing_source_proof"
    for verdict in graph.source_proof_verdicts:
        if getattr(verdict, "verdict", None) == "denied":
            if getattr(verdict, "reason_code", None) == "stale_source":
                return "stale_source"
            return "missing_source_proof"
        if getattr(verdict, "freshness_verdict", None) == "stale":
            return "stale_source"
        if getattr(verdict, "freshness_verdict", None) == "not_checked":
            return "missing_source_proof"
    return None


def _has_child_evidence_ref(evidence_graph: ResearchEvidenceGraph | None) -> bool:
    if evidence_graph is None:
        return False
    return bool(evidence_graph.child_evidence_refs)


def _has_unsupported_fact_claim(evidence_graph: ResearchEvidenceGraph | None) -> bool:
    if evidence_graph is None:
        return False
    factual_kinds = {"factual", "numeric", "comparative", "temporal"}
    for claim in evidence_graph.claim_graph.claims:
        if claim.claim_kind not in factual_kinds:
            continue
        if claim.support_verdict in {"unsupported", "not_evaluated", "contradicted", "stale"}:
            return True
        if claim.projection_mode in {"needs_repair", "omitted"}:
            return True
    return False


def _decision(
    request: ResearchBoundaryRequest,
    status: ResearchBoundaryStatus,
    action: ResearchBoundaryAction,
    reason_codes: tuple[ResearchBoundaryReasonCode, ...],
    *,
    repair_actions: tuple[ResearchBoundaryRepairAction, ...] = (),
) -> ResearchBoundaryDecision:
    decision = ResearchBoundaryDecision(
        boundaryId=request.boundary_id,
        boundarySequenceId=_request_sequence_id(request),
        taskScopeDigest=(
            _request_task_scope_digest(request)
            if request.harness_kind == "research"
            else None
        ),
        stage=request.stage,
        harnessKind=request.harness_kind,
        status=status,
        action=action,
        reasonCodes=reason_codes,
        repairActions=repair_actions,
        finalProjectionAllowed=status in {"pass", "skipped"},
    )
    _mark_boundary_decision_issued(decision)
    return decision


def _request_sequence_id(request: ResearchBoundaryRequest) -> str:
    if request.boundary_sequence_ref is not None:
        return request.boundary_sequence_ref.sequence_id
    return request.boundary_sequence_id or request.boundary_id


def _request_task_scope_digest(request: ResearchBoundaryRequest) -> str | None:
    global _ACTIVE_RESEARCH_TASK_SCOPE_DIGEST
    if _ACTIVE_RESEARCH_TASK_SCOPE_DIGEST is None:
        _ACTIVE_RESEARCH_TASK_SCOPE_DIGEST = _derive_initial_task_scope_digest(request)
    return _ACTIVE_RESEARCH_TASK_SCOPE_DIGEST


def _derive_initial_task_scope_digest(request: ResearchBoundaryRequest) -> str:
    if request.evidence_graph is not None:
        return _research_task_scope_digest(request.evidence_graph)
    if request.boundary_sequence_ref is not None:
        return request.boundary_sequence_ref.task_scope_digest
    return _boundary_label_digest(request.boundary_id)


def _mark_boundary_decision_issued(decision: ResearchBoundaryDecision) -> None:
    object_id = id(decision)
    decision.__pydantic_private__["_issued_by_boundary_enforcer"] = True
    _BOUNDARY_DECISION_OBJECT_IDS.add(object_id)
    _BOUNDARY_DECISION_FINGERPRINTS[object_id] = _decision_fingerprint(decision)
    _BOUNDARY_DECISION_REGISTRY[object_id] = decision
    _BOUNDARY_SEQUENCE_DECISION_IDS.setdefault(decision.boundary_sequence_id, []).append(
        object_id
    )
    if decision.task_scope_digest is not None:
        _BOUNDARY_TASK_DECISION_IDS.setdefault(decision.task_scope_digest, []).append(
            object_id
        )
    _BOUNDARY_DECISION_FINALIZERS[object_id] = finalize(
        decision,
        _discard_boundary_decision_object_id,
        object_id,
    )


def _discard_boundary_decision_object_id(object_id: int) -> None:
    decision = _BOUNDARY_DECISION_REGISTRY.pop(object_id, None)
    _BOUNDARY_DECISION_OBJECT_IDS.discard(object_id)
    _BOUNDARY_DECISION_FINGERPRINTS.pop(object_id, None)
    _BOUNDARY_DECISION_FINALIZERS.pop(object_id, None)
    if decision is not None:
        sequence_ids = _BOUNDARY_SEQUENCE_DECISION_IDS.get(decision.boundary_sequence_id)
        if sequence_ids is not None:
            _BOUNDARY_SEQUENCE_DECISION_IDS[decision.boundary_sequence_id] = [
                item for item in sequence_ids if item != object_id
            ]
        if decision.task_scope_digest is not None:
            task_ids = _BOUNDARY_TASK_DECISION_IDS.get(decision.task_scope_digest)
            if task_ids is not None:
                _BOUNDARY_TASK_DECISION_IDS[decision.task_scope_digest] = [
                    item for item in task_ids if item != object_id
                ]


def _sequence_decisions(boundary_sequence_id: str) -> tuple[ResearchBoundaryDecision, ...]:
    decisions: list[ResearchBoundaryDecision] = []
    for object_id in _BOUNDARY_SEQUENCE_DECISION_IDS.get(boundary_sequence_id, ()):
        decision = _BOUNDARY_DECISION_REGISTRY.get(object_id)
        if decision is not None and decision.is_boundary_enforcer_issued:
            decisions.append(decision)
    return tuple(decisions)


def issued_research_boundary_decisions_for_sequence(
    sequence_ref: ResearchBoundarySequenceRef,
) -> tuple[ResearchBoundaryDecision, ...]:
    if not sequence_ref.is_boundary_sequence_issued:
        return ()
    return tuple(
        decision
        for decision in _sequence_decisions(sequence_ref.sequence_id)
        if decision.is_boundary_enforcer_issued
        and decision.harness_kind == "research"
        and decision.boundary_sequence_id == sequence_ref.sequence_id
    )


def research_task_scope_digest_for_graph(evidence_graph: ResearchEvidenceGraph) -> str:
    return _research_task_scope_digest(evidence_graph)


def research_task_scope_has_failed_boundary(task_scope_digest: str) -> bool:
    return _task_scope_has_failed_decision(task_scope_digest)


def _task_scope_has_failed_decision(task_scope_digest: str) -> bool:
    for object_id in _BOUNDARY_TASK_DECISION_IDS.get(task_scope_digest, ()):
        decision = _BOUNDARY_DECISION_REGISTRY.get(object_id)
        if (
            decision is not None
            and decision.is_boundary_enforcer_issued
            and decision.harness_kind == "research"
            and decision.status != "pass"
        ):
            return True
    return False


def _mark_boundary_sequence_ref_issued(sequence_ref: ResearchBoundarySequenceRef) -> None:
    object_id = id(sequence_ref)
    sequence_ref.__pydantic_private__["_issued_by_boundary_enforcer"] = True
    _BOUNDARY_SEQUENCE_REF_OBJECT_IDS.add(object_id)
    _BOUNDARY_SEQUENCE_REF_FINGERPRINTS[object_id] = _sequence_ref_fingerprint(sequence_ref)
    _BOUNDARY_SEQUENCE_REF_FINALIZERS[object_id] = finalize(
        sequence_ref,
        _discard_boundary_sequence_ref_object_id,
        object_id,
    )


def _discard_boundary_sequence_ref_object_id(object_id: int) -> None:
    _BOUNDARY_SEQUENCE_REF_OBJECT_IDS.discard(object_id)
    _BOUNDARY_SEQUENCE_REF_FINGERPRINTS.pop(object_id, None)
    _BOUNDARY_SEQUENCE_REF_FINALIZERS.pop(object_id, None)


def _sequence_ref_fingerprint(sequence_ref: ResearchBoundarySequenceRef) -> str:
    payload = sequence_ref.public_projection()
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _sequence_label_digest(sequence_id: str) -> str:
    material = "\n".join(("openmagi-research-boundary-sequence-label-v1", sequence_id))
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _boundary_label_digest(boundary_id: str) -> str:
    material = "\n".join(("openmagi-research-boundary-task-label-v1", boundary_id))
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _research_task_scope_digest(evidence_graph: ResearchEvidenceGraph) -> str:
    payload = evidence_graph.acceptance_criteria.public_projection()
    material = json.dumps(
        {
            "version": "openmagi-research-boundary-task-scope-v1",
            "executionId": _ACTIVE_RESEARCH_EXECUTION_ID or "execution:implicit",
            "acceptanceCriteria": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _decision_fingerprint(decision: ResearchBoundaryDecision) -> str:
    payload = decision.public_projection()
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _clear_boundary_execution_state() -> None:
    global _ACTIVE_RESEARCH_EXECUTION_ID, _ACTIVE_RESEARCH_TASK_SCOPE_DIGEST
    _BOUNDARY_DECISION_OBJECT_IDS.clear()
    _BOUNDARY_DECISION_FINGERPRINTS.clear()
    _BOUNDARY_DECISION_FINALIZERS.clear()
    _BOUNDARY_SEQUENCE_DECISION_IDS.clear()
    _BOUNDARY_TASK_DECISION_IDS.clear()
    _BOUNDARY_DECISION_REGISTRY.clear()
    _BOUNDARY_SEQUENCE_REF_OBJECT_IDS.clear()
    _BOUNDARY_SEQUENCE_REF_FINGERPRINTS.clear()
    _BOUNDARY_SEQUENCE_REF_FINALIZERS.clear()
    _ACTIVE_RESEARCH_EXECUTION_ID = None
    _ACTIVE_RESEARCH_TASK_SCOPE_DIGEST = None


def _public_ref(value: str, field_name: str) -> str:
    clean = value.strip()
    _reject_unsafe_public_text(clean, field_name)
    if not _PUBLIC_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe public id")
    return clean


def _reject_unsafe_public_text(value: str, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must be non-empty")
    if _SECRET_TEXT_RE.search(value) or _PRIVATE_PATH_RE.search(value):
        raise ValueError(f"{field_name} must not contain private paths or secrets")
    if _UNSAFE_TEXT_RE.search(value):
        raise ValueError(f"{field_name} must not contain raw/private/source/tool data")


__all__ = [
    "ResearchBoundaryAction",
    "ResearchBoundaryAuthorityFlags",
    "ResearchBoundaryDecision",
    "ResearchBoundaryExecutionPosture",
    "ResearchBoundaryReasonCode",
    "ResearchBoundaryRepairAction",
    "ResearchBoundaryRequest",
    "ResearchBoundarySequenceRef",
    "ResearchBoundaryStage",
    "ResearchBoundaryStatus",
    "ResearchHarnessKind",
    "begin_research_boundary_execution",
    "end_research_boundary_execution",
    "enforce_research_boundary",
    "issued_research_boundary_decisions_for_sequence",
    "research_task_scope_digest_for_graph",
    "research_task_scope_has_failed_boundary",
]
