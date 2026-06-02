from __future__ import annotations

from magi_agent.evidence.final_output_gate import (
    FinalOutputGate,
    FinalOutputGateConfig,
    FinalOutputGateRequest,
)


def test_final_gate_combines_citation_calculation_and_uncertainty_policy() -> None:
    gate = FinalOutputGate(FinalOutputGateConfig(enabled=True, localEvaluationEnabled=True))

    decision = gate.evaluate(
        FinalOutputGateRequest(
            domain="research",
            outputText="The total changed by 18% according to Source A.",
            citations=("source:web:src_1",),
            evidenceRecords=(),
            modelTier="cheap",
            uncertainty="high",
        )
    )

    assert decision.status in {"repair_required", "insufficient_evidence"}
    assert decision.authority_flags.model_dump(by_alias=True)["userVisibleOutputAllowed"] is False


def test_valid_source_and_calculation_evidence_passes_locally_without_user_visible_authority() -> None:
    gate = FinalOutputGate(FinalOutputGateConfig(enabled=True, localEvaluationEnabled=True))

    decision = gate.evaluate(
        FinalOutputGateRequest(
            domain="research",
            outputText="The total changed by 18% according to Source A.",
            citations=("source:web:src_1",),
            evidenceRecords=(
                {
                    "type": "SourceInspection",
                    "evidenceRef": "evidence:web:src_1",
                    "sourceRef": "source:web:src_1",
                },
                {
                    "type": "Calculation",
                    "evidenceRef": "evidence:calc:1",
                    "resultDigest": "sha256:" + "a" * 64,
                    "observedNumbers": ("18",),
                },
            ),
            modelTier="cheap",
            uncertainty="low",
        )
    )

    assert decision.status == "passed"
    assert decision.authority_flags.final_answer_allowed is False
    assert decision.authority_flags.user_visible_output_allowed is False
    assert decision.authority_flags.production_write_allowed is False


def test_unsupported_citation_repairs() -> None:
    decision = FinalOutputGate(
        FinalOutputGateConfig(enabled=True, localEvaluationEnabled=True)
    ).evaluate(
        FinalOutputGateRequest(
            domain="research",
            outputText="Source A supports it.",
            citations=("source:web:missing",),
            evidenceRecords=(
                {
                    "type": "SourceInspection",
                    "evidenceRef": "evidence:web:src_1",
                    "sourceRef": "source:web:src_1",
                },
            ),
            modelTier="standard",
            uncertainty="low",
        )
    )

    assert decision.status == "repair_required"
    assert "unsupported_citation" in decision.reason_codes


def test_numeric_claim_without_calculation_evidence_repairs_even_on_standard_model() -> None:
    decision = FinalOutputGate(
        FinalOutputGateConfig(enabled=True, localEvaluationEnabled=True)
    ).evaluate(
        FinalOutputGateRequest(
            domain="research",
            outputText="The total changed by 18%.",
            evidenceRecords=(
                {
                    "type": "SourceInspection",
                    "evidenceRef": "evidence:web:src_1",
                    "sourceRef": "source:web:src_1",
                },
            ),
            modelTier="standard",
            uncertainty="low",
        )
    )

    assert decision.status == "repair_required"
    assert "numeric_claim_missing_calculation_evidence" in decision.reason_codes


def test_cheap_model_with_missing_evidence_abstains() -> None:
    decision = FinalOutputGate(
        FinalOutputGateConfig(enabled=True, localEvaluationEnabled=True)
    ).evaluate(
        FinalOutputGateRequest(
            domain="research",
            outputText="Unsupported answer.",
            requiredEvidence=("source_ledger",),
            evidenceRecords=(),
            modelTier="cheap",
            uncertainty="high",
        )
    )

    assert decision.status == "insufficient_evidence"
    assert decision.authority_flags.final_answer_allowed is False


def test_public_projection_is_evidence_first_and_redacted() -> None:
    decision = FinalOutputGate(
        FinalOutputGateConfig(enabled=True, localEvaluationEnabled=True)
    ).evaluate(
        FinalOutputGateRequest(
            domain="research",
            outputText="The total is 42 with sk-live-secret.",
            citations=("source:web:src_1",),
            evidenceRecords=(
                {
                    "type": "SourceInspection",
                    "evidenceRef": "evidence:web:src_1",
                    "sourceRef": "source:web:src_1",
                },
                {
                    "type": "Calculation",
                    "evidenceRef": "evidence:calc:1",
                    "resultDigest": "sha256:" + "b" * 64,
                    "observedNumbers": ("42",),
                },
            ),
            modelTier="cheap",
            uncertainty="low",
            hiddenReasoning="I guessed with chain of thought",
        )
    )
    public = decision.public_projection()

    assert public["evidenceFirstProgress"]["openedSourceRefs"] == ["source:web:src_1"]
    assert public["evidenceFirstProgress"]["calculationEvidenceRefs"] == ["evidence:calc:1"]
    assert "sk-live-secret" not in str(public)
    assert "chain of thought" not in str(public)


def test_private_or_non_source_evidence_cannot_support_citation_or_leak_refs() -> None:
    decision = FinalOutputGate(
        FinalOutputGateConfig(enabled=True, localEvaluationEnabled=True)
    ).evaluate(
        FinalOutputGateRequest(
            domain="research",
            outputText="Claim supported by source.",
            citations=("source:web:src_1",),
            evidenceRecords=(
                {
                    "type": "ModelReasoning",
                    "evidenceRef": "/Users/kevin/private/token=sk-live-secret",
                    "sourceRef": "source:web:src_1",
                    "summary": "model guessed",
                },
            ),
            modelTier="standard",
            uncertainty="low",
        )
    )
    public = decision.public_projection()
    encoded = str(public)

    assert decision.status == "repair_required"
    assert "unsupported_citation" in decision.reason_codes
    assert "source:web:src_1" not in public["evidenceFirstProgress"].get("openedSourceRefs", [])
    assert "/Users/kevin" not in encoded
    assert "sk-live-secret" not in encoded


def test_top_level_evidence_refs_are_sanitized_before_public_projection() -> None:
    decision = FinalOutputGate(
        FinalOutputGateConfig(enabled=True, localEvaluationEnabled=True)
    ).evaluate(
        FinalOutputGateRequest(
            domain="research",
            outputText="The total is 42.",
            evidenceRecords=(
                {
                    "type": "Calculation",
                    "evidenceRef": "evidence:github_pat_unsafeToken12345",
                    "resultDigest": "sha256:" + "a" * 64,
                    "observedNumbers": ("42",),
                },
            ),
            modelTier="standard",
            uncertainty="low",
        )
    )
    public = decision.public_projection()

    assert "github_pat_" not in str(public)
    assert public["evidenceRefs"] == []


def test_uncertainty_repair_action_cannot_fall_through_to_passed_final_answer() -> None:
    decision = FinalOutputGate(
        FinalOutputGateConfig(enabled=True, localEvaluationEnabled=True)
    ).evaluate(
        FinalOutputGateRequest(
            domain="research",
            outputText="Source A supports it.",
            citations=("source:web:src_1",),
            evidenceRecords=(
                {
                    "type": "SourceInspection",
                    "evidenceRef": "evidence:web:src_1",
                    "sourceRef": "source:web:src_1",
                },
            ),
            modelTier="standard",
            uncertainty="high",
        )
    )

    assert decision.status == "repair_required"
    assert decision.authority_flags.final_answer_allowed is False
    assert "repair_allowed" in decision.reason_codes


def test_malformed_non_string_source_refs_cannot_support_citations() -> None:
    decision = FinalOutputGate(
        FinalOutputGateConfig(enabled=True, localEvaluationEnabled=True)
    ).evaluate(
        FinalOutputGateRequest(
            domain="research",
            outputText="Claim supported by malformed source.",
            citations=("True",),
            evidenceRecords=(
                {
                    "type": "SourceInspection",
                    "evidenceRef": True,
                    "sourceRef": True,
                },
            ),
            modelTier="standard",
            uncertainty="low",
        )
    )

    assert decision.status == "repair_required"
    assert "unsupported_citation" in decision.reason_codes
    assert decision.evidence_refs == ()
