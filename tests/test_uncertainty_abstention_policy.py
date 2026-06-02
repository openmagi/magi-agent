from __future__ import annotations

from openmagi_core_agent.runtime.uncertainty_policy import (
    UncertaintyDecisionEngine,
    UncertaintyDecisionRequest,
)


def test_missing_research_evidence_returns_insufficient_evidence_not_confident_answer() -> None:
    engine = UncertaintyDecisionEngine.with_defaults()

    decision = engine.decide(
        UncertaintyDecisionRequest(
            domain="research",
            uncertainty="high",
            missingEvidence=("source_ledger", "citation_support"),
            repairAllowed=False,
            escalationAllowed=False,
            userQuestion="What changed in the regulation?",
        )
    )

    assert decision.action == "insufficient_evidence"
    assert decision.final_answer_allowed is False
    assert "regulation" not in str(decision.public_projection())


def test_repair_when_repair_is_allowed() -> None:
    decision = UncertaintyDecisionEngine.with_defaults().decide(
        UncertaintyDecisionRequest(
            domain="research",
            uncertainty="medium",
            missingEvidence=("citation_support",),
            repairAllowed=True,
        )
    )

    assert decision.action == "repair"
    assert decision.final_answer_allowed is False


def test_gather_more_evidence_when_source_acquisition_available() -> None:
    decision = UncertaintyDecisionEngine.with_defaults().decide(
        UncertaintyDecisionRequest(
            domain="research",
            uncertainty="medium",
            missingEvidence=("source_ledger",),
            sourceAcquisitionAvailable=True,
        )
    )

    assert decision.action == "gather_evidence"


def test_ask_user_when_ambiguity_is_user_resolvable() -> None:
    decision = UncertaintyDecisionEngine.with_defaults().decide(
        UncertaintyDecisionRequest(
            domain="general",
            uncertainty="high",
            missingEvidence=("user_target",),
            ambiguityUserResolvable=True,
        )
    )

    assert decision.action == "ask_user"


def test_escalate_to_stronger_model_when_budget_allows() -> None:
    decision = UncertaintyDecisionEngine.with_defaults().decide(
        UncertaintyDecisionRequest(
            domain="coding",
            uncertainty="high",
            missingEvidence=("fresh_review",),
            escalationAllowed=True,
            budgetRemainingUsd=0.10,
        )
    )

    assert decision.action == "escalate_model"
    assert decision.escalation_allowed is False
    assert decision.final_answer_allowed is False


def test_high_risk_missing_evidence_blocks() -> None:
    decision = UncertaintyDecisionEngine.with_defaults().decide(
        UncertaintyDecisionRequest(
            domain="finance",
            uncertainty="high",
            missingEvidence=("calculation_evidence",),
            repairAllowed=False,
            escalationAllowed=False,
        )
    )

    assert decision.action == "block"
    assert decision.final_answer_allowed is False


def test_python_cannot_decide_can_explicitly_fallback_to_typescript() -> None:
    decision = UncertaintyDecisionEngine.with_defaults().decide(
        UncertaintyDecisionRequest(
            domain="general",
            uncertainty="unknown",
            missingEvidence=(),
            pythonDecisionAvailable=False,
        )
    )

    assert decision.action == "fallback_to_typescript"
    assert decision.fallback_to_typescript is True


def test_budget_exceeded_returns_stop_or_ask_not_silent_degradation() -> None:
    decision = UncertaintyDecisionEngine.with_defaults().decide(
        UncertaintyDecisionRequest(
            domain="research",
            uncertainty="high",
            missingEvidence=("source_ledger",),
            escalationAllowed=True,
            budgetRemainingUsd=0,
        )
    )

    assert decision.action in {"ask_user", "insufficient_evidence"}
    assert decision.final_answer_allowed is False
    assert "budget_exceeded" in decision.reason_codes
