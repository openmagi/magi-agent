from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.evidence.calculation_policy import (
    CalculationEvidencePolicy,
    NumericClaimRequest,
)
from magi_agent.runtime.evidence_first_projection import (
    EvidenceFirstProjection,
    EvidenceFirstProjectionRequest,
)
from magi_agent.runtime.model_tiers import ModelTier
from magi_agent.runtime.uncertainty_policy import (
    UncertaintyDecisionEngine,
    UncertaintyDecisionRequest,
    UncertaintyLevel,
)


FinalGateStatus = Literal["passed", "repair_required", "insufficient_evidence", "blocked", "skipped"]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_PRIVATE_REF_RE = re.compile(
    r"(?:"
    r"/Users/|/home/|/workspace/|/data/bots/|/var/lib/kubelet/|"
    r"Bearer\s+|github_pat_|gh[opusr]_|xox[a-z]-|AKIA|AIza|sk-|"
    r"authorization|cookie|secret|token|password|credential|api[_-]?key|private[_-]?key"
    r")",
    re.IGNORECASE,
)
_SOURCE_EVIDENCE_TYPES = frozenset({"SourceInspection", "WebSearch", "KnowledgeSearch"})


class FinalOutputGateConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_evaluation_enabled: bool = Field(default=False, alias="localEvaluationEnabled")


class FinalOutputGateAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    final_answer_allowed: bool = Field(default=False, alias="finalAnswerAllowed")
    user_visible_output_allowed: bool = Field(default=False, alias="userVisibleOutputAllowed")
    production_write_allowed: bool = Field(default=False, alias="productionWriteAllowed")


class FinalOutputGateRequest(BaseModel):
    model_config = _MODEL_CONFIG

    domain: str
    output_text: str = Field(alias="outputText")
    citations: tuple[str, ...] = ()
    evidence_records: tuple[Mapping[str, object], ...] = Field(
        default=(),
        alias="evidenceRecords",
    )
    required_evidence: tuple[str, ...] = Field(default=(), alias="requiredEvidence")
    model_tier: ModelTier = Field(alias="modelTier")
    uncertainty: UncertaintyLevel = "unknown"
    hidden_reasoning: str | None = Field(default=None, alias="hiddenReasoning")


class FinalOutputGateDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: FinalGateStatus
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    authority_flags: FinalOutputGateAuthorityFlags = Field(alias="authorityFlags")
    evidence_first_progress: Mapping[str, object] = Field(
        default_factory=dict,
        alias="evidenceFirstProgress",
    )

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasonCodes": list(self.reason_codes),
            "evidenceRefs": list(self.evidence_refs),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
            "evidenceFirstProgress": dict(self.evidence_first_progress),
        }


class FinalOutputGate:
    def __init__(self, config: FinalOutputGateConfig | None = None) -> None:
        self.config = config or FinalOutputGateConfig()

    def evaluate(self, request: FinalOutputGateRequest) -> FinalOutputGateDecision:
        # F-11: single activation predicate so both gate configs cannot
        # drift on the ``enabled`` + ``local_evaluation_enabled`` pair.
        from magi_agent.evidence.gate_activation import gate_is_live  # noqa: PLC0415

        if not gate_is_live(self.config):
            return _decision("skipped", ("final_output_gate_disabled",), (), {})

        source_refs = _source_refs(request.evidence_records)
        evidence_refs = _evidence_refs(request.evidence_records)
        calculation_refs = _evidence_refs(
            record
            for record in request.evidence_records
            if str(record.get("type")) in {"Calculation", "SpreadsheetValidation", "SpreadsheetDiff", "SQLQueryResult", "ToolResult"}
        )
        reasons: list[str] = []
        if request.required_evidence:
            present = _required_present(request.required_evidence, request.evidence_records)
            missing = tuple(item for item in request.required_evidence if item not in present)
            if missing:
                reasons.extend(f"missing_required_evidence:{item}" for item in missing)
        unsupported = tuple(citation for citation in request.citations if citation not in source_refs)
        if unsupported:
            reasons.append("unsupported_citation")

        calc_decision = CalculationEvidencePolicy(enabled=True).evaluate(
            NumericClaimRequest(
                domain=request.domain,
                outputText=request.output_text,
                evidenceRecords=request.evidence_records,
            )
        )
        if calc_decision.status != "passed":
            reasons.extend(calc_decision.reason_codes)

        uncertainty_decision = UncertaintyDecisionEngine.with_defaults().decide(
            UncertaintyDecisionRequest(
                domain=request.domain,
                uncertainty=request.uncertainty,
                missingEvidence=tuple(
                    reason.removeprefix("missing_required_evidence:")
                    for reason in reasons
                    if reason.startswith("missing_required_evidence:")
                ),
                repairAllowed=True,
                escalationAllowed=request.model_tier == "cheap",
                budgetRemainingUsd=0.05 if request.model_tier == "cheap" else 0.0,
            )
        )
        evidence_first = EvidenceFirstProjection().project(
            EvidenceFirstProjectionRequest(
                openedSourceRefs=source_refs,
                toolEvidenceRefs=evidence_refs,
                calculationEvidenceRefs=calculation_refs,
                validatorRefs=("validator:final-output-gate",),
                validatorStatuses={"validator:final-output-gate": "passed" if not reasons else "failed"},
                hiddenReasoning=request.hidden_reasoning,
            )
        ).public_projection()

        if calc_decision.status == "blocked":
            return _decision("blocked", reasons or calc_decision.reason_codes, evidence_refs, evidence_first)
        if reasons:
            status: FinalGateStatus = (
                "insufficient_evidence"
                if request.model_tier == "cheap"
                and any(reason.startswith("missing_required_evidence") for reason in reasons)
                else "repair_required"
            )
            return _decision(status, tuple(sorted(dict.fromkeys(reasons))), evidence_refs, evidence_first)
        if uncertainty_decision.action in {
            "block",
            "insufficient_evidence",
            "repair",
            "gather_evidence",
            "ask_user",
            "escalate_model",
            "fallback_to_typescript",
        }:
            status = _status_for_uncertainty_action(uncertainty_decision.action)
            return _decision(
                status,
                uncertainty_decision.reason_codes,
                evidence_refs,
                evidence_first,
            )
        return FinalOutputGateDecision(
            status="passed",
            reasonCodes=("final_output_gate_passed",),
            evidenceRefs=evidence_refs,
            authorityFlags=FinalOutputGateAuthorityFlags(
                finalAnswerAllowed=False,
                userVisibleOutputAllowed=False,
                productionWriteAllowed=False,
            ),
            evidenceFirstProgress=evidence_first,
        )


def _decision(
    status: FinalGateStatus,
    reason_codes: tuple[str, ...] | list[str],
    evidence_refs: tuple[str, ...],
    evidence_first: Mapping[str, object],
) -> FinalOutputGateDecision:
    return FinalOutputGateDecision(
        status=status,
        reasonCodes=tuple(sorted(dict.fromkeys(reason_codes))),
        evidenceRefs=evidence_refs,
        authorityFlags=FinalOutputGateAuthorityFlags(),
        evidenceFirstProgress=evidence_first,
    )


def _source_refs(records: tuple[Mapping[str, object], ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            dict.fromkeys(
                source_ref
                for record in records
                if isinstance(source_ref := record.get("sourceRef"), str)
                if str(record.get("type")) in _SOURCE_EVIDENCE_TYPES
                and _is_public_ref(source_ref)
            )
        )
    )


def _evidence_refs(records) -> tuple[str, ...]:
    return tuple(
        sorted(
            dict.fromkeys(
                evidence_ref
                for record in records
                if isinstance(evidence_ref := record.get("evidenceRef"), str)
                and _is_public_ref(evidence_ref)
            )
        )
    )


def _status_for_uncertainty_action(action: str) -> FinalGateStatus:
    if action == "block":
        return "blocked"
    if action == "repair":
        return "repair_required"
    return "insufficient_evidence"


def _required_present(
    required: tuple[str, ...],
    records: tuple[Mapping[str, object], ...],
) -> set[str]:
    present: set[str] = set()
    if "source_ledger" in required and _source_refs(records):
        present.add("source_ledger")
    if "calculation_evidence" in required and any(
        record.get("type") in {"Calculation", "SpreadsheetValidation", "SpreadsheetDiff", "SQLQueryResult"}
        for record in records
    ):
        present.add("calculation_evidence")
    return present


def _is_public_ref(value: str) -> bool:
    return _PRIVATE_REF_RE.search(value) is None and _PUBLIC_REF_RE.fullmatch(value) is not None


__all__ = [
    "FinalOutputGate",
    "FinalOutputGateAuthorityFlags",
    "FinalOutputGateConfig",
    "FinalOutputGateDecision",
    "FinalOutputGateRequest",
    "FinalGateStatus",
]
