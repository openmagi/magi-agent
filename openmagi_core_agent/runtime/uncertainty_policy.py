from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


UncertaintyLevel = Literal["low", "medium", "high", "unknown"]
UncertaintyAction = Literal[
    "pass",
    "repair",
    "gather_evidence",
    "ask_user",
    "escalate_model",
    "block",
    "insufficient_evidence",
    "fallback_to_typescript",
]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_HIGH_RISK_DOMAINS = frozenset({"finance", "accounting", "tax", "legal", "medical"})


class UncertaintyDecisionRequest(BaseModel):
    model_config = _MODEL_CONFIG

    domain: str
    uncertainty: UncertaintyLevel
    missing_evidence: tuple[str, ...] = Field(default=(), alias="missingEvidence")
    repair_allowed: bool = Field(default=False, alias="repairAllowed")
    source_acquisition_available: bool = Field(
        default=False,
        alias="sourceAcquisitionAvailable",
    )
    ambiguity_user_resolvable: bool = Field(default=False, alias="ambiguityUserResolvable")
    escalation_allowed: bool = Field(default=False, alias="escalationAllowed")
    budget_remaining_usd: float = Field(default=0.0, ge=0, alias="budgetRemainingUsd")
    python_decision_available: bool = Field(default=True, alias="pythonDecisionAvailable")
    user_question: str | None = Field(default=None, alias="userQuestion")


class UncertaintyDecision(BaseModel):
    model_config = _MODEL_CONFIG

    action: UncertaintyAction
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    missing_evidence: tuple[str, ...] = Field(default=(), alias="missingEvidence")
    final_answer_allowed: bool = Field(default=False, alias="finalAnswerAllowed")
    escalation_allowed: bool = Field(default=False, alias="escalationAllowed")
    fallback_to_typescript: bool = Field(default=False, alias="fallbackToTypeScript")

    def public_projection(self) -> dict[str, object]:
        return {
            "action": self.action,
            "reasonCodes": list(self.reason_codes),
            "missingEvidence": list(self.missing_evidence),
            "finalAnswerAllowed": self.final_answer_allowed,
            "escalationAllowed": self.escalation_allowed,
            "fallbackToTypeScript": self.fallback_to_typescript,
        }


class UncertaintyDecisionEngine:
    @classmethod
    def with_defaults(cls) -> "UncertaintyDecisionEngine":
        return cls()

    def decide(self, request: UncertaintyDecisionRequest) -> UncertaintyDecision:
        missing = tuple(sorted(dict.fromkeys(request.missing_evidence)))
        reasons: list[str] = []
        if not request.python_decision_available:
            return _decision(
                "fallback_to_typescript",
                ("python_uncertainty_decision_unavailable",),
                missing,
                fallback=True,
            )
        if not missing and request.uncertainty == "low":
            return UncertaintyDecision(
                action="pass",
                reasonCodes=("evidence_sufficient",),
                missingEvidence=(),
                finalAnswerAllowed=False,
            )
        if request.budget_remaining_usd <= 0 and request.escalation_allowed:
            reasons.append("budget_exceeded")
        if request.domain.casefold() in _HIGH_RISK_DOMAINS and missing:
            return _decision("block", reasons + ["high_risk_evidence_missing"], missing)
        if request.ambiguity_user_resolvable:
            return _decision("ask_user", reasons + ["ambiguity_user_resolvable"], missing)
        if request.source_acquisition_available and "source_ledger" in missing:
            return _decision("gather_evidence", reasons + ["source_acquisition_available"], missing)
        if request.repair_allowed:
            return _decision("repair", reasons + ["repair_allowed"], missing)
        if request.escalation_allowed and request.budget_remaining_usd > 0:
            return UncertaintyDecision(
                action="escalate_model",
                reasonCodes=tuple(sorted(dict.fromkeys(reasons + ["bounded_escalation_allowed"]))),
                missingEvidence=missing,
                finalAnswerAllowed=False,
                escalationAllowed=False,
            )
        if reasons:
            return _decision("ask_user", reasons, missing)
        if missing:
            return _decision("insufficient_evidence", ("missing_required_evidence",), missing)
        return _decision("insufficient_evidence", ("uncertainty_not_resolved",), missing)


def _decision(
    action: UncertaintyAction,
    reason_codes: tuple[str, ...] | list[str],
    missing: tuple[str, ...],
    *,
    fallback: bool = False,
) -> UncertaintyDecision:
    return UncertaintyDecision(
        action=action,
        reasonCodes=tuple(sorted(dict.fromkeys(reason_codes))),
        missingEvidence=missing,
        finalAnswerAllowed=False,
        escalationAllowed=False,
        fallbackToTypeScript=fallback,
    )


__all__ = [
    "UncertaintyAction",
    "UncertaintyDecision",
    "UncertaintyDecisionEngine",
    "UncertaintyDecisionRequest",
    "UncertaintyLevel",
]
