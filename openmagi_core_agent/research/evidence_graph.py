from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal, Self
from weakref import finalize

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from openmagi_core_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    project_child_runtime_envelope,
)
from openmagi_core_agent.research.acceptance_criteria import (
    ResearchAcceptanceCriteriaSet,
    project_research_acceptance_criteria_set,
)
from openmagi_core_agent.research.action_claims import (
    ResearchActionProofVerdict,
    project_research_action_proof_verdicts,
)
from openmagi_core_agent.research.claim_graph import (
    ResearchClaimGraph,
    project_research_claim_graph,
)
from openmagi_core_agent.research.source_proof import (
    ResearchSourceProofVerdict,
    project_research_source_proof_verdicts,
)


ResearchMissingEvidenceReasonCode = Literal[
    "missing_source_proof",
    "missing_action_proof",
    "unsupported_claim",
    "stale_source",
    "child_evidence_missing",
    "not_required_for_criterion",
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
_SAFE_EVIDENCE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,80}$")
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
_FORBIDDEN_EVIDENCE_TYPE_PARTS = frozenset(
    {
        "api",
        "auth",
        "cookie",
        "key",
        "log",
        "model",
        "output",
        "path",
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
_ADK_USAGE_NOTES = (
    "Research harness metadata only; no ADK Runner, FunctionTool, live provider, "
    "browser, memory write, or channel delivery is attached."
)
_CHILD_REF_OBJECT_IDS: set[int] = set()
_CHILD_REF_FINGERPRINTS: dict[int, object] = {}
_CHILD_REF_FINALIZERS: dict[int, object] = {}
_MISSING_REASON_OBJECT_IDS: set[int] = set()
_MISSING_REASON_FINGERPRINTS: dict[int, object] = {}
_MISSING_REASON_FINALIZERS: dict[int, object] = {}
_EVIDENCE_GRAPH_OBJECT_IDS: set[int] = set()
_EVIDENCE_GRAPH_FINGERPRINTS: dict[int, object] = {}
_EVIDENCE_GRAPH_FINALIZERS: dict[int, object] = {}


@dataclass(frozen=True)
class _KnownEvidenceBinding:
    evidence_type: str
    digest: str
    support_verdict: str
    freshness_verdict: str
    span_refs: tuple[str, ...] = ()


class _ResearchEvidenceModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for research evidence graph contracts")

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


class ResearchEvidenceExecutionPosture(_ResearchEvidenceModel):
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


class ResearchChildEvidenceRef(_ResearchEvidenceModel):
    _issued_from_runtime_child_envelope: bool = PrivateAttr(default=False)

    child_evidence_ref_id: str = Field(alias="childEvidenceRefId")
    issuer: Literal["openmagi_runtime_boundary"]
    mode: str
    status: str
    parent_execution_id: str = Field(alias="parentExecutionId")
    child_execution_id: str = Field(alias="childExecutionId")
    task_id: str = Field(alias="taskId")
    role: str
    spawn_depth: int = Field(alias="spawnDepth", ge=1)
    ledger_digest: str = Field(alias="ledgerDigest")
    completion_summary_is_evidence: Literal[False] = Field(
        alias="completionSummaryIsEvidence",
    )
    accepted_evidence_metadata_only: Literal[True] = Field(
        alias="acceptedEvidenceMetadataOnly",
    )
    authority_digest: str = Field(alias="authorityDigest")
    audit_event_refs: tuple[str, ...] = Field(alias="auditEventRefs")
    digest: str

    @property
    def is_runtime_child_envelope_issued(self) -> bool:
        return (
            bool(self.__pydantic_private__.get("_issued_from_runtime_child_envelope"))
            and id(self) in _CHILD_REF_OBJECT_IDS
        )

    @field_validator(
        "child_evidence_ref_id",
        "parent_execution_id",
        "child_execution_id",
        "task_id",
        "role",
        "mode",
        "status",
    )
    @classmethod
    def _validate_public_ref_fields(cls, value: str) -> str:
        return _public_ref(value, "child evidence ref")

    @field_validator("ledger_digest", "authority_digest", "digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest fields must be sha256 hex digests")
        return value

    @field_validator("audit_event_refs")
    @classmethod
    def _validate_audit_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("auditEventRefs must not contain duplicates")
        return tuple(_public_ref(item, "auditEventRefs") for item in value)

    @model_validator(mode="after")
    def _validate_digest_binding(self) -> Self:
        expected = _digest_for(
            {
                "childEvidenceRefId": self.child_evidence_ref_id,
                "issuer": self.issuer,
                "mode": self.mode,
                "status": self.status,
                "parentExecutionId": self.parent_execution_id,
                "childExecutionId": self.child_execution_id,
                "taskId": self.task_id,
                "role": self.role,
                "spawnDepth": self.spawn_depth,
                "ledgerDigest": self.ledger_digest,
                "completionSummaryIsEvidence": self.completion_summary_is_evidence,
                "acceptedEvidenceMetadataOnly": self.accepted_evidence_metadata_only,
                "authorityDigest": self.authority_digest,
                "auditEventRefs": self.audit_event_refs,
            }
        )
        if self.digest != expected:
            raise ValueError("digest must be bound to child evidence metadata")
        return self

    def public_projection(self) -> dict[str, object]:
        validated = _validate_child_ref_object(self)
        return {
            "childEvidenceRefId": validated.child_evidence_ref_id,
            "issuer": validated.issuer,
            "mode": validated.mode,
            "status": validated.status,
            "parentExecutionId": validated.parent_execution_id,
            "childExecutionId": validated.child_execution_id,
            "taskId": validated.task_id,
            "role": validated.role,
            "spawnDepth": validated.spawn_depth,
            "ledgerDigest": validated.ledger_digest,
            "completionSummaryIsEvidence": validated.completion_summary_is_evidence,
            "acceptedEvidenceMetadataOnly": validated.accepted_evidence_metadata_only,
            "authorityDigest": validated.authority_digest,
            "auditEventRefs": validated.audit_event_refs,
            "digest": validated.digest,
        }


class ResearchMissingEvidenceReason(_ResearchEvidenceModel):
    reason_id: str = Field(alias="reasonId")
    subject_ref_id: str = Field(alias="subjectRefId")
    evidence_type: str = Field(alias="evidenceType")
    reason_code: ResearchMissingEvidenceReasonCode = Field(alias="reasonCode")

    @field_validator("reason_id", "subject_ref_id")
    @classmethod
    def _validate_public_ref_fields(cls, value: str) -> str:
        return _public_ref(value, "missing evidence ref")

    @field_validator("evidence_type")
    @classmethod
    def _validate_evidence_type(cls, value: str) -> str:
        return _safe_evidence_type(value, "evidenceType")

    @model_validator(mode="after")
    def _bind_creation_fingerprint(self) -> Self:
        _mark_missing_reason_created(self)
        return self

    def public_projection(self) -> dict[str, object]:
        _validate_missing_reason_object(self)
        return {
            "reasonId": self.reason_id,
            "subjectRefId": self.subject_ref_id,
            "evidenceType": self.evidence_type,
            "reasonCode": self.reason_code,
        }


class ResearchEvidenceGraph(_ResearchEvidenceModel):
    evidence_graph_id: str = Field(alias="evidenceGraphId")
    action_proof_verdicts: tuple[object, ...] = Field(
        default=(),
        alias="actionProofVerdicts",
    )
    source_proof_verdicts: tuple[object, ...] = Field(
        default=(),
        alias="sourceProofVerdicts",
    )
    claim_graph: ResearchClaimGraph = Field(alias="claimGraph")
    acceptance_criteria: ResearchAcceptanceCriteriaSet = Field(alias="acceptanceCriteria")
    child_evidence_refs: tuple[ResearchChildEvidenceRef, ...] = Field(
        default=(),
        alias="childEvidenceRefs",
    )
    missing_evidence_reasons: tuple[ResearchMissingEvidenceReason, ...] = Field(
        default=(),
        alias="missingEvidenceReasons",
    )
    execution_posture: ResearchEvidenceExecutionPosture = Field(
        default_factory=ResearchEvidenceExecutionPosture,
        alias="executionPosture",
    )
    adk_usage_notes: str = Field(default=_ADK_USAGE_NOTES, alias="adkUsageNotes")

    @classmethod
    def from_runtime_evidence(
        cls,
        *,
        evidence_graph_id: str,
        action_proof_verdicts: Iterable[ResearchActionProofVerdict] = (),
        source_proof_verdicts: Iterable[ResearchSourceProofVerdict] = (),
        claim_graph: ResearchClaimGraph,
        acceptance_criteria: ResearchAcceptanceCriteriaSet,
        child_evidence_envelopes: Iterable[ChildRuntimeEnvelope] = (),
        missing_evidence_reasons: Iterable[ResearchMissingEvidenceReason] = (),
    ) -> Self:
        child_refs = tuple(
            _child_ref_from_runtime_envelope(envelope)
            for envelope in _validate_child_envelopes(child_evidence_envelopes)
        )
        return cls(
            evidenceGraphId=evidence_graph_id,
            actionProofVerdicts=tuple(action_proof_verdicts),
            sourceProofVerdicts=tuple(source_proof_verdicts),
            claimGraph=claim_graph,
            acceptanceCriteria=acceptance_criteria,
            childEvidenceRefs=child_refs,
            missingEvidenceReasons=tuple(missing_evidence_reasons),
        )

    @field_validator("evidence_graph_id")
    @classmethod
    def _validate_evidence_graph_id(cls, value: str) -> str:
        return _public_ref(value, "evidenceGraphId")

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

    @model_validator(mode="after")
    def _validate_graph(self) -> Self:
        project_research_action_proof_verdicts(self.action_proof_verdicts)
        project_research_source_proof_verdicts(self.source_proof_verdicts)
        project_research_claim_graph(self.claim_graph)
        project_research_acceptance_criteria_set(self.acceptance_criteria)
        for child_ref in self.child_evidence_refs:
            _validate_child_ref_object(child_ref)
        self._validate_unique_ids()
        self._validate_claim_sources()
        self._validate_acceptance_refs()
        self._validate_missing_reason_subjects()
        _mark_evidence_graph_created(self)
        return self

    def public_projection(self) -> dict[str, object]:
        self._validate_not_modified_after_creation()
        projection = {
            "evidenceGraphId": self.evidence_graph_id,
            "executionPosture": self.execution_posture.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            ),
            "adkUsageNotes": self.adk_usage_notes,
            "actionProofVerdicts": project_research_action_proof_verdicts(
                self.action_proof_verdicts
            ),
            "sourceProofVerdicts": project_research_source_proof_verdicts(
                self.source_proof_verdicts
            ),
            "claimGraph": project_research_claim_graph(self.claim_graph),
            "acceptanceCriteria": project_research_acceptance_criteria_set(
                self.acceptance_criteria
            ),
            "childEvidenceRefs": tuple(
                child_ref.public_projection() for child_ref in self.child_evidence_refs
            ),
            "missingEvidenceReasons": tuple(
                reason.public_projection() for reason in self.missing_evidence_reasons
            ),
        }
        return {**projection, "digest": self.public_digest()}

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data: dict[str, object] = {
            "evidenceGraphId": self.evidence_graph_id,
            "actionProofVerdicts": self.action_proof_verdicts,
            "sourceProofVerdicts": self.source_proof_verdicts,
            "claimGraph": self.claim_graph,
            "acceptanceCriteria": self.acceptance_criteria,
            "childEvidenceRefs": self.child_evidence_refs,
            "missingEvidenceReasons": self.missing_evidence_reasons,
            "executionPosture": self.execution_posture,
            "adkUsageNotes": self.adk_usage_notes,
        }
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    def public_digest_projection(self) -> dict[str, object]:
        self._validate_not_modified_after_creation()
        return {
            "evidenceGraphId": self.evidence_graph_id,
            "digest": self.public_digest(),
            "defaultOff": self.execution_posture.default_off,
            "localOnly": self.execution_posture.local_only,
            "fakeProviderOnly": self.execution_posture.fake_provider_only,
        }

    def public_digest(self) -> str:
        self._validate_not_modified_after_creation()
        return _digest_for(self._canonical_public_digest_payload())

    def _validate_not_modified_after_creation(self) -> None:
        _validate_evidence_graph_object(self)

    def _validate_unique_ids(self) -> None:
        _reject_duplicates(
            (verdict.claim_id for verdict in self._action_verdicts()),
            "action proof claimId",
        )
        _reject_duplicates(
            (verdict.source_ref_id for verdict in self._source_verdicts()),
            "source proof sourceRefId",
        )
        _reject_duplicates(
            (child_ref.child_evidence_ref_id for child_ref in self.child_evidence_refs),
            "childEvidenceRefId",
        )
        _reject_duplicates(
            (reason.reason_id for reason in self.missing_evidence_reasons),
            "missing reasonId",
        )

    def _validate_claim_sources(self) -> None:
        allowed_sources = {
            verdict.source_ref_id: verdict
            for verdict in self._source_verdicts()
            if verdict.verdict == "allowed"
        }
        dangling = sorted(
            {
                support_ref.source_ref_id
                for claim in self.claim_graph.claims
                for support_ref in claim.support_refs
                if support_ref.source_ref_id not in allowed_sources
            }
        )
        if dangling:
            raise ValueError(f"dangling sourceRefId values: {', '.join(dangling)}")
        for claim in self.claim_graph.claims:
            for support_ref in claim.support_refs:
                source_verdict = allowed_sources[support_ref.source_ref_id]
                if support_ref.source_digest != source_verdict.content_digest:
                    raise ValueError(
                        "claim support sourceDigest must match verified source contentDigest"
                    )
                if not set(support_ref.span_refs).issubset(set(source_verdict.span_refs)):
                    raise ValueError("claim support spanRefs must be verified source spans")
                expected_freshness = _source_freshness_to_claim(
                    source_verdict.freshness_verdict
                )
                if support_ref.freshness_verdict != expected_freshness:
                    raise ValueError(
                        "claim support freshnessVerdict must match source proof freshness"
                    )

    def _validate_acceptance_refs(self) -> None:
        known = self._known_evidence_ref_bindings()
        for criterion in self.acceptance_criteria.criteria:
            for evidence_ref in criterion.evidence_refs:
                binding = known.get(evidence_ref.evidence_ref_id)
                if binding is None:
                    raise ValueError(
                        f"dangling evidenceRefId: {evidence_ref.evidence_ref_id}"
                    )
                if evidence_ref.digest != binding.digest:
                    raise ValueError(
                        "acceptance evidenceRef digest must match verifier evidence digest"
                    )
                if evidence_ref.evidence_type != binding.evidence_type:
                    raise ValueError(
                        "acceptance evidenceRef evidenceType must match verifier evidence"
                    )
                if evidence_ref.support_verdict != binding.support_verdict:
                    raise ValueError(
                        "acceptance evidenceRef supportVerdict must match verifier evidence"
                    )
                if evidence_ref.freshness_verdict != binding.freshness_verdict:
                    raise ValueError(
                        "acceptance evidenceRef freshnessVerdict must match verifier evidence"
                    )
                if binding.span_refs and not evidence_ref.span_refs:
                    raise ValueError(
                        "acceptance evidenceRef spanRefs must include verifier evidence spans"
                    )
                if not set(evidence_ref.span_refs).issubset(set(binding.span_refs)):
                    raise ValueError(
                        "acceptance evidenceRef spanRefs must match verifier evidence spans"
                    )

    def _validate_missing_reason_subjects(self) -> None:
        known = self._known_subject_ref_ids()
        dangling = sorted(
            reason.subject_ref_id
            for reason in self.missing_evidence_reasons
            if reason.subject_ref_id not in known
        )
        if dangling:
            raise ValueError(f"dangling missing evidence subjectRefId: {', '.join(dangling)}")

    def _known_evidence_ref_bindings(self) -> dict[str, _KnownEvidenceBinding]:
        known: dict[str, _KnownEvidenceBinding] = {}
        for verdict in self._source_verdicts():
            if verdict.verdict == "allowed" and verdict.content_digest is not None:
                binding = _KnownEvidenceBinding(
                    evidence_type="source_inspection",
                    digest=verdict.content_digest,
                    support_verdict="supports",
                    freshness_verdict=_source_freshness_to_acceptance(
                        verdict.freshness_verdict
                    ),
                    span_refs=verdict.span_refs,
                )
                _add_known_evidence_binding(known, verdict.source_ref_id, binding)
                for source_ref_id in verdict.matched_source_refs:
                    _add_known_evidence_binding(known, source_ref_id, binding)
        for claim in self.claim_graph.claims:
            for support_ref in claim.support_refs:
                _add_known_evidence_binding(
                    known,
                    support_ref.support_ref_id,
                    _KnownEvidenceBinding(
                        evidence_type="claim_support",
                        digest=support_ref.evidence_digest,
                        support_verdict=_claim_support_to_acceptance(
                            support_ref.support_verdict
                        ),
                        freshness_verdict=_claim_freshness_to_acceptance(
                            support_ref.freshness_verdict
                        ),
                        span_refs=support_ref.span_refs,
                    ),
                )
        for verdict in self._action_verdicts():
            if verdict.verdict == "allowed":
                binding = _KnownEvidenceBinding(
                    evidence_type="action_proof",
                    digest=_digest_for(project_research_action_proof_verdicts((verdict,))[0]),
                    support_verdict="supports",
                    freshness_verdict="current",
                )
                _add_known_evidence_binding(known, verdict.claim_id, binding)
                for receipt_ref in verdict.matched_receipt_refs:
                    _add_known_evidence_binding(known, receipt_ref, binding)
        for child_ref in self.child_evidence_refs:
            _add_known_evidence_binding(
                known,
                child_ref.child_evidence_ref_id,
                _KnownEvidenceBinding(
                    evidence_type="child_evidence",
                    digest=child_ref.digest,
                    support_verdict="supports",
                    freshness_verdict="current",
                ),
            )
        return known

    def _known_subject_ref_ids(self) -> set[str]:
        subject_ids = {
            self.evidence_graph_id,
            self.acceptance_criteria.criteria_set_id,
            self.claim_graph.claim_graph_id,
        }
        subject_ids.update(verdict.claim_id for verdict in self._action_verdicts())
        subject_ids.update(verdict.source_ref_id for verdict in self._source_verdicts())
        subject_ids.update(claim.claim_id for claim in self.claim_graph.claims)
        subject_ids.update(
            support_ref.support_ref_id
            for claim in self.claim_graph.claims
            for support_ref in claim.support_refs
        )
        subject_ids.update(criterion.criteria_id for criterion in self.acceptance_criteria.criteria)
        subject_ids.update(child_ref.child_evidence_ref_id for child_ref in self.child_evidence_refs)
        return subject_ids

    def _canonical_public_digest_payload(self) -> dict[str, object]:
        return {
            "evidenceGraphId": self.evidence_graph_id,
            "adkUsageNotes": self.adk_usage_notes,
            "executionPosture": self.execution_posture.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            ),
            "actionProofVerdicts": project_research_action_proof_verdicts(
                self.action_proof_verdicts
            ),
            "sourceProofVerdicts": project_research_source_proof_verdicts(
                self.source_proof_verdicts
            ),
            "claimGraph": project_research_claim_graph(self.claim_graph),
            "acceptanceCriteria": project_research_acceptance_criteria_set(
                self.acceptance_criteria
            ),
            "childEvidenceRefs": tuple(
                child_ref.public_projection() for child_ref in self.child_evidence_refs
            ),
            "missingEvidenceReasons": tuple(
                reason.public_projection() for reason in self.missing_evidence_reasons
            ),
        }

    def _action_verdicts(self) -> tuple[ResearchActionProofVerdict, ...]:
        verdicts = self.action_proof_verdicts
        for verdict in verdicts:
            if not isinstance(verdict, ResearchActionProofVerdict):
                raise TypeError("action proof verdicts must be verifier-issued verdict objects")
        return verdicts

    def _source_verdicts(self) -> tuple[ResearchSourceProofVerdict, ...]:
        verdicts = self.source_proof_verdicts
        for verdict in verdicts:
            if not isinstance(verdict, ResearchSourceProofVerdict):
                raise TypeError("source proof verdicts must be verifier-issued verdict objects")
        return verdicts


def _source_freshness_to_claim(value: str) -> str:
    if value == "not_checked":
        return "not_checked"
    return value


def _source_freshness_to_acceptance(value: str) -> str:
    if value == "not_checked":
        return "unknown"
    return value


def _claim_freshness_to_acceptance(value: str) -> str:
    if value == "not_checked":
        return "unknown"
    return value


def _claim_support_to_acceptance(value: str) -> str:
    if value == "supported":
        return "supports"
    if value == "weak":
        return "weak"
    if value == "contradicted":
        return "contradicts"
    return "unknown"


def _add_known_evidence_binding(
    known: dict[str, _KnownEvidenceBinding],
    ref_id: str,
    binding: _KnownEvidenceBinding,
) -> None:
    existing = known.get(ref_id)
    if existing is not None and existing != binding:
        raise ValueError(f"evidence ref namespace collision: {ref_id}")
    known[ref_id] = binding


def _validate_child_envelopes(
    child_evidence_envelopes: Iterable[ChildRuntimeEnvelope],
) -> tuple[ChildRuntimeEnvelope, ...]:
    envelopes = tuple(child_evidence_envelopes)
    for envelope in envelopes:
        if (
            not isinstance(envelope, ChildRuntimeEnvelope)
            or not envelope.is_runtime_boundary_issued
        ):
            raise TypeError("child evidence must arrive as runtime-issued child evidence envelopes")
    return envelopes


def _child_ref_from_runtime_envelope(envelope: ChildRuntimeEnvelope) -> ResearchChildEvidenceRef:
    projection = project_child_runtime_envelope(envelope)
    projected = projection.model_dump(by_alias=True, mode="python", warnings=False)
    if projected["status"] != "accepted":
        raise ValueError("accepted child evidence envelopes are required for research evidence")
    completion_contract = dict(projected["completionContract"])
    if completion_contract.get("summaryIsEvidence") is not False:
        raise ValueError("child model summaries cannot be accepted as research evidence")
    if completion_contract.get("acceptedEvidenceMetadataOnly") is not True:
        raise ValueError("child evidence must remain metadata-only")
    authority_flags = dict(projected["authorityFlags"])
    if any(bool(value) for value in authority_flags.values()):
        raise ValueError("child evidence envelope must not attach live runtime authority")
    ledger_digest = _digest_for(projected["ledgerRef"])
    authority_digest = _digest_for(authority_flags)
    child_ref_id = f"child:{_digest_for(projected)[7:23]}"
    payload = {
        "childEvidenceRefId": child_ref_id,
        "issuer": projected["issuer"],
        "mode": projected["mode"],
        "status": projected["status"],
        "parentExecutionId": projected["parentExecutionId"],
        "childExecutionId": projected["childExecutionId"],
        "taskId": projected["taskId"],
        "role": projected["role"],
        "spawnDepth": projected["spawnDepth"],
        "ledgerDigest": ledger_digest,
        "completionSummaryIsEvidence": False,
        "acceptedEvidenceMetadataOnly": True,
        "authorityDigest": authority_digest,
        "auditEventRefs": tuple(projected["auditEventRefs"]),
    }
    return _mark_child_ref_issued(ResearchChildEvidenceRef(**payload, digest=_digest_for(payload)))


def _mark_child_ref_issued(ref: ResearchChildEvidenceRef) -> ResearchChildEvidenceRef:
    object_id = id(ref)
    ref.__pydantic_private__["_issued_from_runtime_child_envelope"] = True
    _CHILD_REF_OBJECT_IDS.add(object_id)
    _CHILD_REF_FINGERPRINTS[object_id] = _model_fingerprint(ref)
    _CHILD_REF_FINALIZERS[object_id] = finalize(
        ref,
        _discard_child_ref_object_id,
        object_id,
    )
    return ref


def _discard_child_ref_object_id(object_id: int) -> None:
    _CHILD_REF_OBJECT_IDS.discard(object_id)
    _CHILD_REF_FINGERPRINTS.pop(object_id, None)
    _CHILD_REF_FINALIZERS.pop(object_id, None)


def _mark_missing_reason_created(reason: ResearchMissingEvidenceReason) -> None:
    object_id = id(reason)
    if object_id in _MISSING_REASON_FINGERPRINTS:
        return
    _MISSING_REASON_OBJECT_IDS.add(object_id)
    _MISSING_REASON_FINGERPRINTS[object_id] = _model_fingerprint(reason)
    _MISSING_REASON_FINALIZERS[object_id] = finalize(
        reason,
        _discard_missing_reason_object_id,
        object_id,
    )


def _discard_missing_reason_object_id(object_id: int) -> None:
    _MISSING_REASON_OBJECT_IDS.discard(object_id)
    _MISSING_REASON_FINGERPRINTS.pop(object_id, None)
    _MISSING_REASON_FINALIZERS.pop(object_id, None)


def _validate_missing_reason_object(value: ResearchMissingEvidenceReason) -> None:
    object_id = id(value)
    if object_id not in _MISSING_REASON_OBJECT_IDS:
        raise ValueError("missing evidence reason was not created by the evidence graph contract")
    expected = _MISSING_REASON_FINGERPRINTS.get(object_id)
    if expected != _model_fingerprint(value):
        raise ValueError("missing evidence reason was modified after creation")
    ResearchMissingEvidenceReason.model_validate(
        value.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _mark_evidence_graph_created(graph: ResearchEvidenceGraph) -> None:
    object_id = id(graph)
    if object_id in _EVIDENCE_GRAPH_FINGERPRINTS:
        return
    _EVIDENCE_GRAPH_OBJECT_IDS.add(object_id)
    _EVIDENCE_GRAPH_FINGERPRINTS[object_id] = _model_fingerprint(graph)
    _EVIDENCE_GRAPH_FINALIZERS[object_id] = finalize(
        graph,
        _discard_evidence_graph_object_id,
        object_id,
    )


def _discard_evidence_graph_object_id(object_id: int) -> None:
    _EVIDENCE_GRAPH_OBJECT_IDS.discard(object_id)
    _EVIDENCE_GRAPH_FINGERPRINTS.pop(object_id, None)
    _EVIDENCE_GRAPH_FINALIZERS.pop(object_id, None)


def _validate_evidence_graph_object(value: ResearchEvidenceGraph) -> None:
    object_id = id(value)
    if object_id not in _EVIDENCE_GRAPH_OBJECT_IDS:
        raise ValueError("evidence graph was not created by the evidence graph contract")
    expected = _EVIDENCE_GRAPH_FINGERPRINTS.get(object_id)
    if expected != _model_fingerprint(value):
        raise ValueError("evidence graph was modified after creation")


def _validate_child_ref_object(value: object) -> ResearchChildEvidenceRef:
    if not isinstance(value, ResearchChildEvidenceRef):
        raise TypeError("child evidence refs must be issued from runtime child envelopes")
    if not value.is_runtime_child_envelope_issued:
        raise ValueError("child evidence refs must be issued from runtime child envelopes")
    expected = _CHILD_REF_FINGERPRINTS.get(id(value))
    if expected != _model_fingerprint(value):
        raise ValueError("child evidence ref was modified after runtime child envelope issuance")
    ResearchChildEvidenceRef.model_validate(
        value.model_dump(by_alias=True, mode="python", warnings=False)
    )
    return value


def _reject_duplicates(values: Iterable[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ValueError(f"{label} values must not contain duplicates")


def _public_ref(value: str, field: str) -> str:
    clean = value.strip()
    _reject_unsafe_public_text(clean, field)
    if not _PUBLIC_REF_RE.fullmatch(clean):
        raise ValueError(f"{field} must be a digest-safe public ref")
    return clean


def _safe_evidence_type(value: str, field: str) -> str:
    clean = value.strip()
    _reject_unsafe_public_text(clean, field)
    if not _SAFE_EVIDENCE_TYPE_RE.fullmatch(clean):
        raise ValueError(f"{field} must be a digest-safe evidence type")
    parts = frozenset(re.split(r"[_.:-]+", clean.casefold()))
    if parts & _FORBIDDEN_EVIDENCE_TYPE_PARTS:
        raise ValueError(f"{field} cannot name raw, private, tool, or model-summary data")
    return clean


def _reject_unsafe_public_text(value: str, field: str) -> None:
    if not value:
        raise ValueError(f"{field} must be non-empty")
    if _SECRET_TEXT_RE.search(value) or _PRIVATE_PATH_RE.search(value):
        raise ValueError(f"{field} must not contain private paths or secrets")
    if _UNSAFE_TEXT_RE.search(value):
        raise ValueError(f"{field} must not contain raw/private/source/tool data")


def _digest_for(value: object) -> str:
    encoded = json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


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


def project_research_evidence_graph(graph: ResearchEvidenceGraph) -> dict[str, object]:
    if not isinstance(graph, ResearchEvidenceGraph):
        raise TypeError("research evidence graph projection requires a graph object")
    return graph.public_projection()


__all__ = [
    "ResearchChildEvidenceRef",
    "ResearchEvidenceExecutionPosture",
    "ResearchEvidenceGraph",
    "ResearchMissingEvidenceReason",
    "ResearchMissingEvidenceReasonCode",
    "project_research_evidence_graph",
]
