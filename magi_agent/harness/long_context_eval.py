from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.evidence.final_output_gate import (
    FinalOutputGate,
    FinalOutputGateConfig,
    FinalOutputGateDecision,
    FinalOutputGateRequest,
)
from magi_agent.runtime.context_budget import (
    ContextBudgetPlan,
    ContextBudgetPlanner,
    ContextBudgetRequest,
)
from magi_agent.runtime.model_tiers import ModelTier, ModelUsagePhase
from magi_agent.runtime.request_shape import RequestShapeLedger, RequestShapeRecord


ValidatorOutcome = Literal["passed", "repair_required", "blocked"]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class LongContextReliabilityCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    recipe_ids: tuple[str, ...] = Field(alias="recipeIds")
    model_tier: ModelTier = Field(alias="modelTier")
    phase: ModelUsagePhase
    source_refs: tuple[str, ...] = Field(default=(), alias="sourceRefs")
    summary_refs: tuple[str, ...] = Field(default=(), alias="summaryRefs")
    raw_input_bytes: int = Field(default=0, alias="rawInputBytes")
    evidence_records: tuple[Mapping[str, object], ...] = Field(
        default=(),
        alias="evidenceRecords",
    )
    citations: tuple[str, ...] = ()
    output_text: str = Field(default="", alias="outputText")
    expected_validator_outcome: ValidatorOutcome = Field(alias="expectedValidatorOutcome")
    expected_final_gate_action: str = Field(alias="expectedFinalGateAction")


class LongContextReliabilityResult(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    context_budget_plan: ContextBudgetPlan = Field(alias="contextBudgetPlan")
    request_shape_record: RequestShapeRecord = Field(alias="requestShapeRecord")
    validator_outcome: ValidatorOutcome = Field(alias="validatorOutcome")
    final_gate_action: str = Field(alias="finalGateAction")
    final_gate_decision: FinalOutputGateDecision = Field(alias="finalGateDecision")


def evaluate_long_context_case(
    case: LongContextReliabilityCase,
) -> LongContextReliabilityResult:
    context_plan = ContextBudgetPlanner.with_defaults().plan(
        ContextBudgetRequest(
            recipeIds=case.recipe_ids,
            modelTier=case.model_tier,
            phase=case.phase,
            sourceRefs=case.source_refs,
            summaryRefs=case.summary_refs,
            rawInputBytes=case.raw_input_bytes,
        )
    )
    provider, model, registry_tier = _model_for_case(case.model_tier)
    request_record = RequestShapeLedger().record_model_phase(
        turnId=f"turn:{case.case_id}",
        phase=case.phase,
        provider=provider,
        model=model,
        modelTier=registry_tier,
        inputRefs=context_plan.included_refs,
        evidenceRefs=tuple(
            str(record.get("evidenceRef"))
            for record in case.evidence_records
            if isinstance(record.get("evidenceRef"), str)
        ),
        contextPlanDigest="sha256:" + "1" * 64,
        rawInput={"caseId": case.case_id, "refs": context_plan.included_refs},
    )
    final_gate = FinalOutputGate(
        FinalOutputGateConfig(enabled=True, localEvaluationEnabled=True)
    ).evaluate(
        FinalOutputGateRequest(
            domain="research" if "openmagi.research" in case.recipe_ids else "coding",
            outputText=case.output_text,
            citations=case.citations,
            evidenceRecords=case.evidence_records,
            modelTier="cheap" if case.model_tier == "long_context" else case.model_tier,
            uncertainty="low",
        )
    )
    validator_outcome: ValidatorOutcome = (
        "passed" if final_gate.status == "passed" else "repair_required"
    )
    return LongContextReliabilityResult(
        caseId=case.case_id,
        contextBudgetPlan=context_plan,
        requestShapeRecord=request_record,
        validatorOutcome=validator_outcome,
        finalGateAction=final_gate.status,
        finalGateDecision=final_gate,
    )


def _model_for_case(tier: ModelTier) -> tuple[str, str, ModelTier]:
    if tier == "cheap":
        return ("google", "gemini-3.5-flash", "cheap")
    if tier == "sota":
        return ("openai", "gpt-5.5", "sota")
    if tier == "long_context":
        return ("fireworks", "kimi-k2p6", "cheap")
    return ("example", "standard-model", "standard")


__all__ = [
    "LongContextReliabilityCase",
    "LongContextReliabilityResult",
    "ValidatorOutcome",
    "evaluate_long_context_case",
]
