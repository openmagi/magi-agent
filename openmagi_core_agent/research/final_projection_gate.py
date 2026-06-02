from __future__ import annotations

import json
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Literal, Self
from weakref import finalize

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from openmagi_core_agent.research.action_claims import detect_research_action_claims
from openmagi_core_agent.research.boundary_enforcement import (
    ResearchBoundaryDecision,
    ResearchBoundarySequenceRef,
    issued_research_boundary_decisions_for_sequence,
    research_task_scope_digest_for_graph,
    research_task_scope_has_failed_boundary,
)
from openmagi_core_agent.research.evidence_graph import (
    ResearchEvidenceGraph,
    project_research_evidence_graph,
)


ResearchFinalProjectionGateMode = Literal["off", "audit", "local_only"]
ResearchFinalProjectionStatus = Literal["skipped", "passed", "partial", "repair_required"]
ResearchFinalProjectionReasonCode = Literal[
    "research_final_projection_gate_off",
    "research_final_projection_passed",
    "url_only_citation",
    "missing_source_proof",
    "unopened_source",
    "stale_source",
    "source_ref_without_verified_source",
    "claim_source_not_cited",
    "unsupported_claim",
    "contradicted_claim",
    "stale_claim",
    "not_evaluated_claim",
    "action_claim_without_receipt",
    "missing_task_proof",
    "incomplete_acceptance_criteria",
    "blocked_acceptance_criteria",
    "unsafe_candidate_projection",
    "missing_boundary_history",
    "prior_boundary_failed",
]

ResearchFinalProjectionRepairAction = Literal[
    "inspect_missing_source",
    "refresh_stale_source",
    "omit_unsupported_claim",
    "qualify_weak_claim",
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
_SOURCE_REF_RE = re.compile(r"^src_[1-9][0-9]*$")
_SOURCE_REF_IN_TEXT_RE = re.compile(r"\bsrc_[1-9][0-9]*\b")
_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+", re.IGNORECASE)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{4,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{12,}|"
    r"AIza[0-9A-Za-z_-]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,}|"
    r"\bcallback\s+(?:code|state)\s+(?:is\s+)?[A-Za-z0-9._~+/=-]{4,}|"
    r"\bcallback\s+(?:query|url)[^.!?\n]{0,120}\b(?:code|state)\s*=\s*"
    r"[A-Za-z0-9._~+/=-]{4,}|"
    r"\b(?:password|secret|cookie|token|api[\s_-]*key|access[\s_-]*token|"
    r"refresh[\s_-]*token|id[\s_-]*token|auth[\s_-]*token|session[\s_-]*key)"
    r"\s+(?:is\s+)?(?=[A-Za-z0-9._~+/=-]{4,}\b)"
    r"(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{4,}|"
    r"\b(?:password|secret|cookie|token|api[\s_-]*key|access[\s_-]*token|"
    r"refresh[\s_-]*token|id[\s_-]*token|auth[\s_-]*token|session[\s_-]*key)"
    r"\s+(?:is\s+)?[A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/home/[^,\s\"']+|/root/[^,\s\"']+|"
    r"/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+|"
    r"[A-Za-z]:[\\/][^,\s\"']+|\\\\[^\\\s\"']+\\[^,\s\"']+)",
    re.IGNORECASE,
)
_UNSAFE_CANDIDATE_RE = re.compile(
    r"raw[_ -]?(?:source|transcript|tool|prompt|output|result|log|child)|"
    r"raw[_ -]?child[_ -]?(?:evidence|output|result|transcript|payload)?|"
    r"child[_ -]?(?:evidence|output|result|transcript|payload)|"
    r"callback[_ -]?(?:code|state|query|url)|"
    r"source[_ -]?(?:body|content|html|text)|hidden[_ -]?reasoning|"
    r"chain[_ -]?of[_ -]?thought|authorization|cookie|set-cookie|"
    r"api[_ -]?key|secret|token|"
    r"model[_ -]?summary|model[_ -]?generated[_ -]?summary",
    re.IGNORECASE,
)
_UNSAFE_PUBLIC_TEXT_RE = re.compile(
    r"https?://|file://|raw[_ -]?(?:source|transcript|tool|prompt|output|result|log|child)|"
    r"raw[_ -]?child[_ -]?(?:evidence|output|result|transcript|payload)?|"
    r"child[_ -]?(?:evidence|output|result|transcript|payload)|"
    r"callback[_ -]?(?:code|state|query|url)|"
    r"source[_ -]?(?:body|content|html|text)|hidden[_ -]?reasoning|"
    r"chain[_ -]?of[_ -]?thought|authorization|cookie|set-cookie|"
    r"api[_ -]?key|secret|token|"
    r"model[_ -]?summary|model[_ -]?generated[_ -]?summary",
    re.IGNORECASE,
)
_ADK_USAGE_NOTES = (
    "Research projection metadata only; no ADK Runner, FunctionTool, live provider, "
    "browser, memory write, channel delivery, or ToolHost execution is attached."
)
_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]|$)")
_FACT_CUE_RE = re.compile(
    r"\b(?:\d+(?:\.\d+)?|20\d{2}|earned|acquired|launched|revenue|costs?|"
    r"changed|increased?|decreased?|more|less|before|after|current|currently|"
    r"certified|compliant|encrypted|deprecated|available|unavailable|approved|"
    r"verified|unverified|supports?|supported|unsupported|according|has|have|"
    r"is|are|was|were)\b",
    re.IGNORECASE,
)
_SOURCE_CITATION_RE = re.compile(r"\[[^\]]*\bsrc_[1-9][0-9]*\b[^\]]*\]")
_FINAL_PROJECTION_REQUIRED_BOUNDARY_STAGES = frozenset(
    {"after_source_summary", "before_intermediate_synthesis"}
)
_LOW_INFORMATION_SENTENCES = frozenset((
    "here is the answer",
    "thanks",
    "thank you",
))
_LOW_INFORMATION_SENTENCE_RE = re.compile(
    r"(?:here|below)\s+is\s+(?:a\s+|the\s+)?"
    r"(?:(?:brief|concise|short)\s+)?(?:answer|summary|response)"
)
_LOW_INFORMATION_PREFIX_RE = re.compile(
    r"^\s*(?:here|below)\s+is\s+(?:a\s+|the\s+)?"
    r"(?:(?:brief|concise|short)\s+)?(?:answer|summary|response)\s*:\s*(?P<rest>.+)$",
    re.IGNORECASE,
)
_GATE_RESULT_OBJECT_IDS: set[int] = set()
_GATE_RESULT_FINGERPRINTS: dict[int, object] = {}
_GATE_RESULT_FINALIZERS: dict[int, object] = {}


class _ResearchFinalProjectionModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for research final projection contracts")

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


class ResearchFinalProjectionExecutionPosture(_ResearchFinalProjectionModel):
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


class ResearchFinalProjectionAuthorityFlags(_ResearchFinalProjectionModel):
    final_answer_blocked: Literal[False] = Field(default=False, alias="finalAnswerBlocked")
    final_answer_blocking_enabled: Literal[False] = Field(
        default=False,
        alias="finalAnswerBlockingEnabled",
    )
    user_visible_output_blocked: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputBlocked",
    )
    channel_delivery_performed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryPerformed",
    )
    memory_written: Literal[False] = Field(default=False, alias="memoryWritten")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    provider_called: Literal[False] = Field(default=False, alias="providerCalled")
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")


class ResearchRenderedClaim(_ResearchFinalProjectionModel):
    claim_id: str = Field(alias="claimId")
    text: str
    source_refs: tuple[str, ...] = Field(alias="sourceRefs")
    span_refs: tuple[str, ...] = Field(alias="spanRefs")
    render_as_fact: bool = Field(alias="renderAsFact")

    @field_validator("claim_id")
    @classmethod
    def _validate_claim_id(cls, value: str) -> str:
        return _public_ref(value, "claimId")

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        clean = value.strip()
        _reject_unsafe_public_text(clean, "rendered claim text")
        if len(clean) > 300:
            raise ValueError("rendered claim text must be at most 300 characters")
        return clean

    @field_validator("source_refs")
    @classmethod
    def _validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("rendered claims require sourceRefs")
        if len(set(value)) != len(value):
            raise ValueError("sourceRefs must not contain duplicates")
        return tuple(_source_ref(item, "sourceRef") for item in value)

    @field_validator("span_refs")
    @classmethod
    def _validate_span_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("rendered claims require spanRefs")
        if len(set(value)) != len(value):
            raise ValueError("spanRefs must not contain duplicates")
        return tuple(_public_ref(item, "spanRef") for item in value)

    def public_projection(self) -> dict[str, object]:
        return {
            "claimId": self.claim_id,
            "text": self.text,
            "sourceRefs": self.source_refs,
            "spanRefs": self.span_refs,
            "renderAsFact": self.render_as_fact,
        }


class ResearchOmittedClaim(_ResearchFinalProjectionModel):
    claim_id: str = Field(alias="claimId")
    reason_code: ResearchFinalProjectionReasonCode = Field(alias="reasonCode")
    repair_action: ResearchFinalProjectionRepairAction = Field(alias="repairAction")

    @field_validator("claim_id")
    @classmethod
    def _validate_claim_id(cls, value: str) -> str:
        return _public_ref(value, "claimId")

    def public_projection(self) -> dict[str, object]:
        return {
            "claimId": self.claim_id,
            "reasonCode": self.reason_code,
            "repairAction": self.repair_action,
        }


class ResearchActionProjection(_ResearchFinalProjectionModel):
    claim_id: str = Field(alias="claimId")
    action_verb: str = Field(alias="actionVerb")
    text: str
    verified: bool

    @field_validator("claim_id")
    @classmethod
    def _validate_claim_id(cls, value: str) -> str:
        return _public_ref(value, "claimId")

    @field_validator("action_verb")
    @classmethod
    def _validate_action_verb(cls, value: str) -> str:
        clean = value.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_:-]{1,80}", clean):
            raise ValueError("actionVerb must be digest-safe lower-case text")
        return clean

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        clean = value.strip()
        _reject_unsafe_public_text(clean, "action projection text")
        return clean

    def public_projection(self) -> dict[str, object]:
        return {
            "claimId": self.claim_id,
            "actionVerb": self.action_verb,
            "text": self.text,
            "verified": self.verified,
        }


class ResearchMissingWorkItem(_ResearchFinalProjectionModel):
    criteria_id: str = Field(alias="criteriaId")
    status: str
    description: str

    @field_validator("criteria_id")
    @classmethod
    def _validate_criteria_id(cls, value: str) -> str:
        return _public_ref(value, "criteriaId")

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        if value not in {"missing", "partial", "blocked"}:
            raise ValueError("missing work status must be missing, partial, or blocked")
        return value

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        clean = value.strip()
        _reject_unsafe_public_text(clean, "missing work description")
        if len(clean) > 500:
            raise ValueError("missing work description must be at most 500 characters")
        return clean

    def public_projection(self) -> dict[str, object]:
        return {
            "criteriaId": self.criteria_id,
            "status": self.status,
            "description": self.description,
        }


class ResearchFinalProjectionGateRequest(_ResearchFinalProjectionModel):
    gate_id: str = Field(alias="gateId")
    mode: ResearchFinalProjectionGateMode = "off"
    candidate_final_answer: str = Field(alias="candidateFinalAnswer")
    evidence_graph: ResearchEvidenceGraph = Field(alias="evidenceGraph")
    boundary_sequence_ref: ResearchBoundarySequenceRef | None = Field(
        default=None,
        alias="boundarySequenceRef",
    )
    boundary_decisions: tuple[ResearchBoundaryDecision, ...] = Field(
        default=(),
        alias="boundaryDecisions",
    )

    @field_validator("gate_id")
    @classmethod
    def _validate_gate_id(cls, value: str) -> str:
        return _public_ref(value, "gateId")

    @field_validator("candidate_final_answer")
    @classmethod
    def _validate_candidate_text_shape(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("candidateFinalAnswer must be non-empty")
        if len(clean) > 10_000:
            raise ValueError("candidateFinalAnswer must be at most 10000 characters")
        return clean

    @field_validator("boundary_decisions")
    @classmethod
    def _validate_boundary_decisions(
        cls,
        value: tuple[ResearchBoundaryDecision, ...],
    ) -> tuple[ResearchBoundaryDecision, ...]:
        ids = [decision.boundary_id for decision in value]
        if len(set(ids)) != len(ids):
            raise ValueError("boundaryDecisions must not contain duplicate boundaryId values")
        return value


class ResearchFinalProjectionGateResult(_ResearchFinalProjectionModel):
    _issued_by_final_projection_gate: bool = PrivateAttr(default=False)

    gate_id: str = Field(alias="gateId")
    mode: ResearchFinalProjectionGateMode
    status: ResearchFinalProjectionStatus
    ok: bool
    reason_codes: tuple[ResearchFinalProjectionReasonCode, ...] = Field(alias="reasonCodes")
    repair_actions: tuple[ResearchFinalProjectionRepairAction, ...] = Field(
        default=(),
        alias="repairActions",
    )
    rendered_facts: tuple[ResearchRenderedClaim, ...] = Field(
        default=(),
        alias="renderedFacts",
    )
    qualified_claims: tuple[ResearchRenderedClaim, ...] = Field(
        default=(),
        alias="qualifiedClaims",
    )
    omitted_claims: tuple[ResearchOmittedClaim, ...] = Field(default=(), alias="omittedClaims")
    action_projections: tuple[ResearchActionProjection, ...] = Field(
        default=(),
        alias="actionProjections",
    )
    missing_work_report: tuple[ResearchMissingWorkItem, ...] = Field(
        default=(),
        alias="missingWorkReport",
    )
    output_link_digests: tuple[str, ...] = Field(default=(), alias="outputLinkDigests")
    final_answer_digest: str = Field(alias="finalAnswerDigest")
    evidence_graph_digest: str = Field(alias="evidenceGraphDigest")
    execution_posture: ResearchFinalProjectionExecutionPosture = Field(
        default_factory=ResearchFinalProjectionExecutionPosture,
        alias="executionPosture",
    )
    authority_flags: ResearchFinalProjectionAuthorityFlags = Field(
        default_factory=ResearchFinalProjectionAuthorityFlags,
        alias="authorityFlags",
    )
    adk_usage_notes: str = Field(default=_ADK_USAGE_NOTES, alias="adkUsageNotes")

    @field_validator("gate_id")
    @classmethod
    def _validate_gate_id(cls, value: str) -> str:
        return _public_ref(value, "gateId")

    @field_validator("reason_codes", "repair_actions")
    @classmethod
    def _validate_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("final projection tuples must not contain duplicates")
        return value

    @field_validator("output_link_digests")
    @classmethod
    def _validate_output_link_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("outputLinkDigests must not contain duplicates")
        return tuple(_digest(item, "outputLinkDigest") for item in value)

    @field_validator("final_answer_digest", "evidence_graph_digest")
    @classmethod
    def _validate_digest_fields(cls, value: str) -> str:
        return _digest(value, "digest")

    @field_validator("adk_usage_notes")
    @classmethod
    def _validate_adk_usage_notes(cls, value: str) -> str:
        clean = value.strip()
        _reject_unsafe_public_text(clean, "adkUsageNotes")
        if len(clean) > 300:
            raise ValueError("adkUsageNotes must be at most 300 characters")
        return clean

    @model_validator(mode="after")
    def _validate_result_shape(self) -> Self:
        if self.status in {"skipped", "passed"} and not self.ok:
            raise ValueError("skipped and passed projections must have ok=true")
        if self.status in {"partial", "repair_required"} and self.ok:
            raise ValueError("partial and repair projections must have ok=false")
        if self.status == "repair_required" and not self.repair_actions:
            raise ValueError("repair_required projections require repairActions")
        if self.status == "partial" and not self.missing_work_report:
            raise ValueError("partial projections require missingWorkReport")
        return self

    def public_projection(self) -> dict[str, object]:
        _validate_gate_result_object(self)
        return {
            "gateId": self.gate_id,
            "mode": self.mode,
            "status": self.status,
            "ok": self.ok,
            "reasonCodes": self.reason_codes,
            "repairActions": self.repair_actions,
            "renderedFacts": tuple(item.public_projection() for item in self.rendered_facts),
            "qualifiedClaims": tuple(
                item.public_projection() for item in self.qualified_claims
            ),
            "omittedClaims": tuple(item.public_projection() for item in self.omitted_claims),
            "actionProjections": tuple(
                item.public_projection() for item in self.action_projections
            ),
            "missingWorkReport": tuple(
                item.public_projection() for item in self.missing_work_report
            ),
            "outputLinkDigests": self.output_link_digests,
            "finalAnswerDigest": self.final_answer_digest,
            "evidenceGraphDigest": self.evidence_graph_digest,
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


def evaluate_research_final_projection_gate(
    request: ResearchFinalProjectionGateRequest,
) -> ResearchFinalProjectionGateResult:
    parsed = ResearchFinalProjectionGateRequest.model_validate(request)
    graph_projection = project_research_evidence_graph(parsed.evidence_graph)
    evidence_graph_digest = str(graph_projection["digest"])
    final_answer_digest = _digest_text(parsed.candidate_final_answer)

    if parsed.mode == "off":
        return _result(
            parsed,
            status="skipped",
            ok=True,
            reason_codes=("research_final_projection_gate_off",),
            repair_actions=(),
            rendered_facts=(),
            qualified_claims=(),
            omitted_claims=(),
            action_projections=(),
            missing_work_report=(),
            output_link_digests=_output_link_digests(parsed.candidate_final_answer),
            final_answer_digest=final_answer_digest,
            evidence_graph_digest=evidence_graph_digest,
        )

    boundary_reasons = _boundary_history_reason_codes(parsed)
    rendered_facts, qualified_claims, omitted_claims, claim_reasons = _project_claims(
        parsed.candidate_final_answer,
        parsed.evidence_graph,
    )
    action_projections, action_reasons = _project_actions(
        parsed.candidate_final_answer,
        parsed.evidence_graph,
    )
    missing_work_report, task_reasons = _project_missing_work(parsed.evidence_graph)
    source_reasons = _source_reason_codes(parsed.candidate_final_answer, parsed.evidence_graph)
    candidate_reasons = _candidate_reason_codes(parsed.candidate_final_answer)
    coverage_reasons = _candidate_claim_coverage_reason_codes(
        parsed.candidate_final_answer,
        parsed.evidence_graph,
    )

    reason_codes = _dedupe_reason_codes(
        (
            *boundary_reasons,
            *source_reasons,
            *claim_reasons,
            *action_reasons,
            *task_reasons,
            *candidate_reasons,
            *coverage_reasons,
        )
    )
    repair_actions = _repair_actions_for(reason_codes)
    output_link_digests = _output_link_digests(parsed.candidate_final_answer)

    fatal_reasons = tuple(
        reason
        for reason in reason_codes
        if reason
        not in {
            "missing_task_proof",
            "incomplete_acceptance_criteria",
        }
    )
    if fatal_reasons:
        status: ResearchFinalProjectionStatus = "repair_required"
        ok = False
        rendered_facts = ()
        qualified_claims = ()
    elif missing_work_report:
        status = "partial"
        ok = False
    else:
        status = "passed"
        ok = True
        reason_codes = ("research_final_projection_passed",)
        repair_actions = ()

    return _result(
        parsed,
        status=status,
        ok=ok,
        reason_codes=reason_codes,
        repair_actions=repair_actions,
        rendered_facts=rendered_facts,
        qualified_claims=qualified_claims,
        omitted_claims=omitted_claims,
        action_projections=action_projections,
        missing_work_report=missing_work_report,
        output_link_digests=output_link_digests,
        final_answer_digest=final_answer_digest,
        evidence_graph_digest=evidence_graph_digest,
    )


def _project_claims(
    candidate_text: str,
    evidence_graph: ResearchEvidenceGraph,
) -> tuple[
    tuple[ResearchRenderedClaim, ...],
    tuple[ResearchRenderedClaim, ...],
    tuple[ResearchOmittedClaim, ...],
    tuple[ResearchFinalProjectionReasonCode, ...],
]:
    cited_refs = set(_cited_source_refs(candidate_text))
    candidate_claims = set(_normalized_candidate_claims(candidate_text))
    candidate_fact_digests = set(_candidate_fact_claim_digests(candidate_text, evidence_graph))
    rendered_facts: list[ResearchRenderedClaim] = []
    qualified_claims: list[ResearchRenderedClaim] = []
    omitted_claims: list[ResearchOmittedClaim] = []
    reasons: list[ResearchFinalProjectionReasonCode] = []

    for claim in evidence_graph.claim_graph.claims:
        preview = claim.claim_preview
        if preview is None:
            continue
        normalized_preview = _normalize_candidate_claim_text(preview)
        if not normalized_preview or normalized_preview not in candidate_claims:
            continue
        if _is_unsafe_public_text(preview):
            reasons.append("unsafe_candidate_projection")
            omitted_claims.append(
                ResearchOmittedClaim(
                    claimId=claim.claim_id,
                    reasonCode="unsafe_candidate_projection",
                    repairAction="omit_unsupported_claim",
                )
            )
            continue
        if claim.projection_mode == "fact" and claim.support_verdict == "supported":
            support_refs = _candidate_bound_support_refs(
                claim,
                candidate_fact_digests,
                allowed_support_verdicts=("supported",),
            )
            if not support_refs:
                reasons.append("not_evaluated_claim")
                omitted_claims.append(
                    ResearchOmittedClaim(
                        claimId=claim.claim_id,
                        reasonCode="not_evaluated_claim",
                        repairAction="omit_unsupported_claim",
                    )
                )
                continue
            source_refs = _claim_source_refs(support_refs)
            span_refs = _claim_span_refs(support_refs)
            if source_refs and not set(source_refs).issubset(cited_refs):
                reasons.append("claim_source_not_cited")
            if source_refs and set(source_refs).issubset(cited_refs):
                rendered_facts.append(
                    ResearchRenderedClaim(
                        claimId=claim.claim_id,
                        text=preview,
                        sourceRefs=source_refs,
                        spanRefs=span_refs,
                        renderAsFact=True,
                    )
            )
            continue
        if claim.projection_mode == "qualified":
            support_refs = _candidate_bound_support_refs(
                claim,
                candidate_fact_digests,
                allowed_support_verdicts=("supported", "weak"),
            )
            if not support_refs:
                reasons.append("not_evaluated_claim")
                omitted_claims.append(
                    ResearchOmittedClaim(
                        claimId=claim.claim_id,
                        reasonCode="not_evaluated_claim",
                        repairAction="omit_unsupported_claim",
                    )
                )
                continue
            source_refs = _claim_source_refs(support_refs)
            span_refs = _claim_span_refs(support_refs)
            if source_refs and not set(source_refs).issubset(cited_refs):
                reasons.append("claim_source_not_cited")
            if source_refs and set(source_refs).issubset(cited_refs):
                qualified_claims.append(
                    ResearchRenderedClaim(
                        claimId=claim.claim_id,
                        text=_qualified_text(preview),
                        sourceRefs=source_refs,
                        spanRefs=span_refs,
                        renderAsFact=False,
                    )
                )
            continue
        reason = _claim_failure_reason(claim.support_verdict)
        if reason is not None:
            reasons.append(reason)
            omitted_claims.append(
                ResearchOmittedClaim(
                    claimId=claim.claim_id,
                    reasonCode=reason,
                    repairAction="omit_unsupported_claim",
                )
            )

    return (
        tuple(rendered_facts),
        tuple(qualified_claims),
        tuple(omitted_claims),
        _dedupe_reason_codes(tuple(reasons)),
    )


def _project_actions(
    candidate_text: str,
    evidence_graph: ResearchEvidenceGraph,
) -> tuple[tuple[ResearchActionProjection, ...], tuple[ResearchFinalProjectionReasonCode, ...]]:
    try:
        claims = detect_research_action_claims(candidate_text)
    except ValueError:
        return (), ("unsafe_candidate_projection",)
    allowed_verdicts = {
        (verdict.claim_id, verdict.claim_text_digest, verdict.action_verb): verdict
        for verdict in evidence_graph.action_proof_verdicts
        if getattr(verdict, "is_action_verifier_issued", False)
        and verdict.verdict == "allowed"
        and verdict.reason_code == "receipt_match"
        and verdict.matched_receipt_refs
    }
    projections: list[ResearchActionProjection] = []
    reasons: list[ResearchFinalProjectionReasonCode] = []
    for claim in claims:
        verdict = allowed_verdicts.get(
            (claim.claim_id, claim.claim_text_digest, claim.action_verb)
        )
        verified = verdict is not None
        if not verified:
            reasons.append("action_claim_without_receipt")
        projections.append(
            ResearchActionProjection(
                claimId=claim.claim_id,
                actionVerb=claim.action_verb,
                text=(
                    f"verified: {claim.action_verb}"
                    if verified
                    else f"not verified: {claim.action_verb}"
                ),
                verified=verified,
            )
        )
    return tuple(projections), _dedupe_reason_codes(tuple(reasons))


def _boundary_history_reason_codes(
    request: ResearchFinalProjectionGateRequest,
) -> tuple[ResearchFinalProjectionReasonCode, ...]:
    sequence_ref = request.boundary_sequence_ref
    if sequence_ref is None or not sequence_ref.is_boundary_sequence_issued:
        return ("missing_boundary_history",)
    current_task_scope_digest = research_task_scope_digest_for_graph(request.evidence_graph)
    if research_task_scope_has_failed_boundary(current_task_scope_digest):
        return ("prior_boundary_failed",)
    supplied_decisions = request.boundary_decisions
    if not supplied_decisions:
        return ("missing_boundary_history",)
    sequence_id = sequence_ref.sequence_id
    supplied_boundary_ids: set[str] = set()
    for decision in supplied_decisions:
        if not decision.is_boundary_enforcer_issued:
            return ("missing_boundary_history",)
        if decision.boundary_sequence_id != sequence_id:
            return ("missing_boundary_history",)
        supplied_boundary_ids.add(decision.boundary_id)
    research_decisions = issued_research_boundary_decisions_for_sequence(sequence_ref)
    if not research_decisions:
        return ("missing_boundary_history",)
    issued_boundary_ids = {decision.boundary_id for decision in research_decisions}
    if not supplied_boundary_ids.issubset(issued_boundary_ids):
        return ("missing_boundary_history",)
    task_scope_digests = {
        decision.task_scope_digest
        for decision in research_decisions
        if decision.task_scope_digest is not None
    }
    if len(task_scope_digests) != 1:
        return ("missing_boundary_history",)
    if task_scope_digests != {current_task_scope_digest}:
        return ("missing_boundary_history",)
    supplied_task_scope_digests = {
        decision.task_scope_digest
        for decision in supplied_decisions
        if decision.harness_kind == "research" and decision.task_scope_digest is not None
    }
    if supplied_task_scope_digests != {current_task_scope_digest}:
        return ("missing_boundary_history",)
    if any(
        decision.status != "pass" or not decision.final_projection_allowed
        for decision in research_decisions
    ):
        return ("prior_boundary_failed",)
    passed_stages = {decision.stage for decision in research_decisions}
    if not _FINAL_PROJECTION_REQUIRED_BOUNDARY_STAGES.issubset(passed_stages):
        return ("missing_boundary_history",)
    return ()


def _project_missing_work(
    evidence_graph: ResearchEvidenceGraph,
) -> tuple[tuple[ResearchMissingWorkItem, ...], tuple[ResearchFinalProjectionReasonCode, ...]]:
    items: list[ResearchMissingWorkItem] = []
    reasons: list[ResearchFinalProjectionReasonCode] = []
    if all(
        criterion.completion_mode == "not_applicable"
        for criterion in evidence_graph.acceptance_criteria.criteria
    ):
        return (
            (
                ResearchMissingWorkItem(
                    criteriaId="task-proof",
                    status="missing",
                    description="User-requested work requires acceptance criteria evidence.",
                ),
            ),
            ("missing_task_proof",),
        )
    for criterion in evidence_graph.acceptance_criteria.criteria:
        if criterion.status == "satisfied":
            continue
        if criterion.status == "blocked":
            reasons.append("blocked_acceptance_criteria")
        else:
            reasons.append("incomplete_acceptance_criteria")
        items.append(
            ResearchMissingWorkItem(
                criteriaId=criterion.criteria_id,
                status=criterion.status or "missing",
                description=_safe_missing_work_description(criterion.description),
            )
        )
    return tuple(items), _dedupe_reason_codes(tuple(reasons))


def _source_reason_codes(
    candidate_text: str,
    evidence_graph: ResearchEvidenceGraph,
) -> tuple[ResearchFinalProjectionReasonCode, ...]:
    reasons: list[ResearchFinalProjectionReasonCode] = []
    if _URL_RE.search(candidate_text) and not _cited_source_refs(candidate_text):
        reasons.append("url_only_citation")

    allowed_sources = {
        verdict.source_ref_id
        for verdict in evidence_graph.source_proof_verdicts
        if getattr(verdict, "verdict", None) == "allowed"
        and getattr(verdict, "freshness_verdict", None) == "current"
        and getattr(verdict, "content_digest", None)
        and getattr(verdict, "span_refs", ())
    }
    cited_sources = set(_cited_source_refs(candidate_text))
    for source_ref in cited_sources:
        if source_ref not in allowed_sources:
            reasons.append("source_ref_without_verified_source")

    if not evidence_graph.source_proof_verdicts:
        reasons.append("missing_source_proof")
    for verdict in evidence_graph.source_proof_verdicts:
        if getattr(verdict, "verdict", None) == "denied":
            reason_code = getattr(verdict, "reason_code", None)
            if reason_code == "unopened_source":
                reasons.append("unopened_source")
            elif reason_code == "stale_source":
                reasons.append("stale_source")
            else:
                reasons.append("missing_source_proof")
        elif getattr(verdict, "freshness_verdict", None) == "stale":
            reasons.append("stale_source")
        elif getattr(verdict, "freshness_verdict", None) == "not_checked":
            reasons.append("missing_source_proof")
    return _dedupe_reason_codes(tuple(reasons))


def _candidate_reason_codes(candidate_text: str) -> tuple[ResearchFinalProjectionReasonCode, ...]:
    if _is_unsafe_candidate_text(candidate_text):
        return ("unsafe_candidate_projection",)
    return ()


def _candidate_claim_coverage_reason_codes(
    candidate_text: str,
    evidence_graph: ResearchEvidenceGraph,
) -> tuple[ResearchFinalProjectionReasonCode, ...]:
    mapped_claims = {
        _normalize_candidate_claim_text(claim.claim_preview)
        for claim in evidence_graph.claim_graph.claims
        if claim.claim_preview is not None
    }
    mapped_claims.discard("")
    for sentence in _candidate_fact_sentences(
        candidate_text,
        require_all_candidate_sentences=_has_applicable_acceptance_criteria(evidence_graph),
    ):
        normalized = _normalize_candidate_claim_text(sentence)
        if not normalized:
            continue
        if normalized in mapped_claims:
            continue
        return ("not_evaluated_claim",)
    if _has_candidate_text_without_projectable_content(candidate_text, evidence_graph):
        return ("not_evaluated_claim",)
    return ()


def _normalized_candidate_fact_claims(candidate_text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            normalized
            for sentence in _candidate_fact_sentences(candidate_text)
            if (normalized := _normalize_candidate_claim_text(sentence))
        )
    )


def _normalized_candidate_claims(candidate_text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            normalized
            for sentence in _sentences(candidate_text)
            if (candidate_sentence := _candidate_sentence_for_claim(sentence))
            if not _is_pure_action_sentence(candidate_sentence)
            if (normalized := _normalize_candidate_claim_text(candidate_sentence))
        )
    )


def _claim_failure_reason(support_verdict: str) -> ResearchFinalProjectionReasonCode | None:
    if support_verdict == "unsupported":
        return "unsupported_claim"
    if support_verdict == "contradicted":
        return "contradicted_claim"
    if support_verdict == "stale":
        return "stale_claim"
    if support_verdict == "not_evaluated":
        return "not_evaluated_claim"
    return None


def _repair_actions_for(
    reason_codes: tuple[ResearchFinalProjectionReasonCode, ...],
) -> tuple[ResearchFinalProjectionRepairAction, ...]:
    actions: list[ResearchFinalProjectionRepairAction] = []
    if any(reason in reason_codes for reason in {"missing_source_proof", "unopened_source"}):
        actions.append("inspect_missing_source")
    if any(reason in reason_codes for reason in {"stale_source", "stale_claim"}):
        actions.append("refresh_stale_source")
    if any(
        reason in reason_codes
        for reason in {
            "unsupported_claim",
            "contradicted_claim",
            "not_evaluated_claim",
            "action_claim_without_receipt",
            "claim_source_not_cited",
            "url_only_citation",
            "source_ref_without_verified_source",
            "unsafe_candidate_projection",
            "blocked_acceptance_criteria",
            "missing_boundary_history",
            "prior_boundary_failed",
        }
    ):
        actions.append("omit_unsupported_claim")
    if any(
        reason in reason_codes
        for reason in {"missing_task_proof", "incomplete_acceptance_criteria"}
    ):
        actions.append("return_partial_with_missing_work_report")
    return tuple(dict.fromkeys(actions))


def _result(
    request: ResearchFinalProjectionGateRequest,
    *,
    status: ResearchFinalProjectionStatus,
    ok: bool,
    reason_codes: tuple[ResearchFinalProjectionReasonCode, ...],
    repair_actions: tuple[ResearchFinalProjectionRepairAction, ...],
    rendered_facts: tuple[ResearchRenderedClaim, ...],
    qualified_claims: tuple[ResearchRenderedClaim, ...],
    omitted_claims: tuple[ResearchOmittedClaim, ...],
    action_projections: tuple[ResearchActionProjection, ...],
    missing_work_report: tuple[ResearchMissingWorkItem, ...],
    output_link_digests: tuple[str, ...],
    final_answer_digest: str,
    evidence_graph_digest: str,
) -> ResearchFinalProjectionGateResult:
    result = ResearchFinalProjectionGateResult(
        gateId=request.gate_id,
        mode=request.mode,
        status=status,
        ok=ok,
        reasonCodes=reason_codes,
        repairActions=repair_actions,
        renderedFacts=rendered_facts,
        qualifiedClaims=qualified_claims,
        omittedClaims=omitted_claims,
        actionProjections=action_projections,
        missingWorkReport=missing_work_report,
        outputLinkDigests=output_link_digests,
        finalAnswerDigest=final_answer_digest,
        evidenceGraphDigest=evidence_graph_digest,
    )
    return _mark_gate_result_issued(result)


def _claim_source_refs(support_refs: object) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            getattr(support_ref, "source_ref_id")
            for support_ref in support_refs
        )
    )


def _claim_span_refs(support_refs: object) -> tuple[str, ...]:
    refs: list[str] = []
    for support_ref in support_refs:
        refs.extend(getattr(support_ref, "span_refs", ()))
    return tuple(dict.fromkeys(refs))


def _candidate_bound_support_refs(
    claim: object,
    candidate_fact_digests: set[str],
    *,
    allowed_support_verdicts: tuple[str, ...],
) -> tuple[object, ...]:
    claim_text_digest = getattr(claim, "claim_text_digest", None)
    if not claim_text_digest or claim_text_digest not in candidate_fact_digests:
        return ()
    return tuple(
        support_ref
        for support_ref in getattr(claim, "support_refs", ())
        if (
            getattr(support_ref, "claim_text_digest", None) == claim_text_digest
            and getattr(support_ref, "support_verdict", None) in allowed_support_verdicts
            and getattr(support_ref, "freshness_verdict", None) == "current"
            and getattr(support_ref, "relevance_verdict", None) == "relevant"
            and getattr(support_ref, "is_claim_support_verifier_issued", False)
            and getattr(support_ref, "source_ref_id", None)
            and getattr(support_ref, "span_refs", ())
        )
    )


def _qualified_text(preview: str) -> str:
    if preview.casefold().startswith("evidence suggests:"):
        return preview
    return f"Evidence suggests: {preview}"


def _candidate_fact_sentences(
    candidate_text: str,
    *,
    require_all_candidate_sentences: bool = False,
) -> tuple[str, ...]:
    sentences: list[str] = []
    has_source_citation = bool(_cited_source_refs(candidate_text))
    for sentence in _sentences(candidate_text):
        candidate_sentence = _candidate_sentence_for_claim(sentence)
        normalized = _normalize_candidate_claim_text(candidate_sentence)
        if not normalized:
            continue
        if _is_citation_only_sentence(candidate_sentence):
            continue
        if _is_pure_action_sentence(candidate_sentence):
            continue
        if (
            require_all_candidate_sentences
            or has_source_citation
            or _looks_like_factual_sentence(candidate_sentence)
            or _has_candidate_claim_words(candidate_sentence)
        ):
            sentences.append(candidate_sentence)
    return tuple(sentences)


def _sentences(text: str) -> tuple[str, ...]:
    return tuple(
        match.group(0).strip()
        for match in _SENTENCE_RE.finditer(text)
        if match.group(0).strip()
    )


def _looks_like_factual_sentence(sentence: str) -> bool:
    return (
        bool(_FACT_CUE_RE.search(sentence))
        or bool(_SOURCE_REF_IN_TEXT_RE.search(sentence))
        or bool(_URL_RE.search(sentence))
        or _has_non_ascii_letter(sentence)
    )


def _has_candidate_claim_words(sentence: str) -> bool:
    clean = _candidate_claim_text_for_digest(sentence)
    words = re.findall(r"[^\W_]+", clean, flags=re.UNICODE)
    return len(words) >= 3 or _has_non_ascii_letter(clean)


def _is_citation_only_sentence(sentence: str) -> bool:
    return not _normalize_candidate_claim_text(sentence)


def _is_pure_action_sentence(sentence: str) -> bool:
    try:
        claims = detect_research_action_claims(sentence)
    except ValueError:
        return False
    return bool(claims) and not _FACT_CUE_RE.search(sentence)


def _is_low_information_sentence(sentence: str) -> bool:
    normalized = _normalize_candidate_claim_text(sentence)
    return normalized in _LOW_INFORMATION_SENTENCES or _LOW_INFORMATION_SENTENCE_RE.fullmatch(normalized) is not None


def _candidate_sentence_for_claim(sentence: str) -> str:
    prefix_match = _LOW_INFORMATION_PREFIX_RE.match(sentence)
    if prefix_match is not None:
        return prefix_match.group("rest").strip()
    if _is_low_information_sentence(sentence):
        return ""
    return sentence


def _has_candidate_text_without_projectable_content(
    candidate_text: str,
    evidence_graph: ResearchEvidenceGraph,
) -> bool:
    if not _has_applicable_acceptance_criteria(evidence_graph):
        return False
    if not _normalize_candidate_claim_text(candidate_text):
        return False
    if _candidate_fact_sentences(
        candidate_text,
        require_all_candidate_sentences=_has_applicable_acceptance_criteria(evidence_graph),
    ):
        return False
    try:
        return not bool(detect_research_action_claims(candidate_text))
    except ValueError:
        return False


def _normalize_candidate_claim_text(value: str | None) -> str:
    if value is None:
        return ""
    without_citations = _SOURCE_CITATION_RE.sub(" ", value)
    without_source_refs = _SOURCE_REF_IN_TEXT_RE.sub(" ", without_citations)
    without_urls = _URL_RE.sub(" ", without_source_refs)
    normalized = re.sub(r"[^\w]+", " ", without_urls.casefold(), flags=re.UNICODE)
    normalized = re.sub(r"_+", " ", normalized).strip()
    return normalized


def _candidate_fact_claim_digests(
    candidate_text: str,
    evidence_graph: ResearchEvidenceGraph,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            digest
            for sentence in _candidate_fact_sentences(
                candidate_text,
                require_all_candidate_sentences=_has_applicable_acceptance_criteria(evidence_graph),
            )
            for digest in _candidate_claim_text_digest_variants(
                _candidate_claim_text_for_digest(sentence)
            )
        )
    )


def _candidate_claim_text_digest_variants(claim_text: str) -> tuple[str, ...]:
    clean = claim_text.strip()
    if not clean:
        return ()
    variants = [clean]
    if clean[-1] not in ".!?":
        variants.append(f"{clean}.")
    return tuple(dict.fromkeys(_candidate_claim_text_digest(variant) for variant in variants))


def _candidate_claim_text_for_digest(value: str) -> str:
    without_citations = _SOURCE_CITATION_RE.sub(" ", value)
    without_source_refs = _SOURCE_REF_IN_TEXT_RE.sub(" ", without_citations)
    without_urls = _URL_RE.sub(" ", without_source_refs)
    normalized_spacing = re.sub(r"\s+", " ", without_urls).strip()
    return re.sub(r"\s+([.!?,;:])", r"\1", normalized_spacing).strip()


def _candidate_claim_text_digest(claim_text: str) -> str:
    material = "\n".join(
        (
            "openmagi-research-boundary-candidate-claim-v1",
            claim_text.strip(),
        )
    )
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _has_non_ascii_letter(value: str) -> bool:
    return any(ord(char) > 127 and char.isalpha() for char in value)


def _cited_source_refs(candidate_text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_SOURCE_REF_IN_TEXT_RE.findall(candidate_text)))


def _output_link_digests(candidate_text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            _digest_text(match.group(0).strip().rstrip(".,;:!?"))
            for match in _URL_RE.finditer(candidate_text)
        )
    )


def _dedupe_reason_codes(
    values: tuple[ResearchFinalProjectionReasonCode, ...],
) -> tuple[ResearchFinalProjectionReasonCode, ...]:
    return tuple(dict.fromkeys(values))


def _has_applicable_acceptance_criteria(evidence_graph: ResearchEvidenceGraph) -> bool:
    return any(
        getattr(criterion, "completion_mode", None) != "not_applicable"
        for criterion in evidence_graph.acceptance_criteria.criteria
    )


def _safe_missing_work_description(value: str) -> str:
    clean = value.strip()
    if not clean or _is_unsafe_public_text(clean):
        return "Missing required research criterion."
    return clean


def _is_unsafe_candidate_text(value: str) -> bool:
    return (
        _SECRET_TEXT_RE.search(value) is not None
        or _PRIVATE_PATH_RE.search(value) is not None
        or _UNSAFE_CANDIDATE_RE.search(value) is not None
    )


def _is_unsafe_public_text(value: str) -> bool:
    return (
        _SECRET_TEXT_RE.search(value) is not None
        or _PRIVATE_PATH_RE.search(value) is not None
        or _UNSAFE_PUBLIC_TEXT_RE.search(value) is not None
    )


def _mark_gate_result_issued(
    result: ResearchFinalProjectionGateResult,
) -> ResearchFinalProjectionGateResult:
    object_id = id(result)
    result.__pydantic_private__["_issued_by_final_projection_gate"] = True
    _GATE_RESULT_OBJECT_IDS.add(object_id)
    _GATE_RESULT_FINGERPRINTS[object_id] = _model_fingerprint(result)
    _GATE_RESULT_FINALIZERS[object_id] = finalize(
        result,
        _discard_gate_result_object_id,
        object_id,
    )
    return result


def _discard_gate_result_object_id(object_id: int) -> None:
    _GATE_RESULT_OBJECT_IDS.discard(object_id)
    _GATE_RESULT_FINGERPRINTS.pop(object_id, None)
    _GATE_RESULT_FINALIZERS.pop(object_id, None)


def _validate_gate_result_object(result: ResearchFinalProjectionGateResult) -> None:
    object_id = id(result)
    if (
        not result.__pydantic_private__.get("_issued_by_final_projection_gate")
        or object_id not in _GATE_RESULT_OBJECT_IDS
    ):
        raise ValueError("projection result must be issued by the final projection gate")
    expected = _GATE_RESULT_FINGERPRINTS.get(object_id)
    if expected != _model_fingerprint(result):
        raise ValueError("projection result was modified after final projection gate issuance")
    ResearchFinalProjectionGateResult.model_validate(
        result.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _model_fingerprint(model: BaseModel) -> object:
    return _jsonable(model.model_dump(by_alias=True, mode="python", warnings=False))


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(by_alias=True, mode="python", warnings=False))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value


def _public_ref(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    _reject_unsafe_public_text(clean, field_name)
    if not _PUBLIC_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe public id")
    return clean


def _source_ref(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    _reject_unsafe_public_text(clean, field_name)
    if not _SOURCE_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must use stable src_N metadata refs")
    return clean


def _digest(value: str, field_name: str) -> str:
    clean = value.strip()
    if not _DIGEST_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a sha256 hex digest")
    return clean


def _digest_text(value: str) -> str:
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


def _reject_unsafe_public_text(value: str, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must be non-empty")
    if _SECRET_TEXT_RE.search(value) or _PRIVATE_PATH_RE.search(value):
        raise ValueError(f"{field_name} must not contain private paths or secrets")
    if _UNSAFE_PUBLIC_TEXT_RE.search(value):
        raise ValueError(f"{field_name} must not contain raw/private/source/tool data")


def public_digest_for_research_final_projection(
    result: ResearchFinalProjectionGateResult,
) -> str:
    projection = result.public_projection()
    encoded = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


__all__ = [
    "ResearchActionProjection",
    "ResearchFinalProjectionAuthorityFlags",
    "ResearchFinalProjectionExecutionPosture",
    "ResearchFinalProjectionGateMode",
    "ResearchFinalProjectionGateRequest",
    "ResearchFinalProjectionGateResult",
    "ResearchFinalProjectionReasonCode",
    "ResearchFinalProjectionRepairAction",
    "ResearchFinalProjectionStatus",
    "ResearchMissingWorkItem",
    "ResearchOmittedClaim",
    "ResearchRenderedClaim",
    "evaluate_research_final_projection_gate",
    "public_digest_for_research_final_projection",
]
