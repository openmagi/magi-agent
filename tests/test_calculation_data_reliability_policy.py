from __future__ import annotations

from magi_agent.evidence.calculation_policy import (
    CalculationEvidencePolicy,
    NumericClaimRequest,
)


def test_numeric_claim_without_tool_evidence_requires_repair() -> None:
    policy = CalculationEvidencePolicy(enabled=True)

    decision = policy.evaluate(
        NumericClaimRequest(
            domain="spreadsheet",
            outputText="The invoices total $12,431.",
            evidenceRecords=(),
        )
    )

    assert decision.status == "repair_required"
    assert decision.reason_codes == ("numeric_claim_missing_calculation_evidence",)
    assert decision.authority_flags.model_dump(by_alias=True)["finalAnswerAllowed"] is False


def test_calculation_evidence_passes_with_result_digest() -> None:
    decision = CalculationEvidencePolicy(enabled=True).evaluate(
        NumericClaimRequest(
            domain="general",
            outputText="The answer is 42.",
            evidenceRecords=(
                {
                    "type": "Calculation",
                    "evidenceRef": "evidence:calc:1",
                    "resultDigest": "sha256:" + "a" * 64,
                    "observedNumbers": ("42",),
                },
            ),
        )
    )

    assert decision.status == "passed"
    assert decision.final_answer_allowed is False
    assert decision.authority_flags.user_visible_output_allowed is False
    assert decision.evidence_refs == ("evidence:calc:1",)


def test_sql_evidence_passes_only_when_query_and_result_digests_exist() -> None:
    passed = CalculationEvidencePolicy(enabled=True).evaluate(
        NumericClaimRequest(
            domain="accounting",
            outputText="There are 7 invoices.",
            evidenceRecords=(
                {
                    "type": "SQLQueryResult",
                    "evidenceRef": "evidence:sql:1",
                    "queryDigest": "sha256:" + "b" * 64,
                    "resultDigest": "sha256:" + "c" * 64,
                    "observedNumbers": ("7",),
                },
            ),
        )
    )
    failed = CalculationEvidencePolicy(enabled=True).evaluate(
        NumericClaimRequest(
            domain="accounting",
            outputText="There are 7 invoices.",
            evidenceRecords=({"type": "SQLQueryResult", "evidenceRef": "evidence:sql:2"},),
        )
    )

    assert passed.status == "passed"
    assert failed.status == "repair_required"


def test_spreadsheet_formula_and_recalc_evidence_passes() -> None:
    decision = CalculationEvidencePolicy(enabled=True).evaluate(
        NumericClaimRequest(
            domain="spreadsheet",
            outputText="The total is 100.",
            evidenceRecords=(
                {
                    "type": "SpreadsheetValidation",
                    "evidenceRef": "evidence:sheet:1",
                    "formulaDigest": "sha256:" + "d" * 64,
                    "recalcDigest": "sha256:" + "e" * 64,
                    "observedNumbers": ("100",),
                },
            ),
        )
    )

    assert decision.status == "passed"


def test_llm_arithmetic_explanation_alone_fails() -> None:
    decision = CalculationEvidencePolicy(enabled=True).evaluate(
        NumericClaimRequest(
            domain="general",
            outputText="The total is 15 because 7 plus 8 is 15.",
            evidenceRecords=(
                {
                    "type": "ModelReasoning",
                    "evidenceRef": "evidence:model:1",
                    "summary": "I added 7 and 8.",
                },
            ),
        )
    )

    assert decision.status == "repair_required"
    assert "model_explanation_not_calculation_evidence" in decision.reason_codes


def test_high_risk_accounting_total_requires_deterministic_tool_result() -> None:
    decision = CalculationEvidencePolicy(enabled=True).evaluate(
        NumericClaimRequest(
            domain="accounting",
            outputText="Revenue totals $91,204.",
            evidenceRecords=({"type": "SourceInspection", "evidenceRef": "evidence:src:1"},),
        )
    )

    assert decision.status == "repair_required"
    assert "high_risk_numeric_claim_requires_deterministic_evidence" in decision.reason_codes
    assert decision.final_answer_allowed is False


def test_mismatched_numeric_result_blocks_or_repairs() -> None:
    decision = CalculationEvidencePolicy(enabled=True).evaluate(
        NumericClaimRequest(
            domain="finance",
            outputText="The total is 18.",
            evidenceRecords=(
                {
                    "type": "Calculation",
                    "evidenceRef": "evidence:calc:1",
                    "resultDigest": "sha256:" + "f" * 64,
                    "observedNumbers": ("17",),
                },
            ),
        )
    )

    assert decision.status == "blocked"
    assert decision.final_answer_allowed is False
    assert "numeric_claim_mismatch" in decision.reason_codes


def test_digest_only_calculation_evidence_requires_observed_number_binding() -> None:
    decision = CalculationEvidencePolicy(enabled=True).evaluate(
        NumericClaimRequest(
            domain="finance",
            outputText="Revenue was $999,999.",
            evidenceRecords=(
                {
                    "type": "Calculation",
                    "evidenceRef": "evidence:calc:1",
                    "resultDigest": "sha256:" + "a" * 64,
                },
            ),
        )
    )

    assert decision.status == "repair_required"
    assert decision.final_answer_allowed is False
    assert "numeric_claim_missing_observed_result_binding" in decision.reason_codes


def test_token_shaped_evidence_refs_are_not_publicly_projected() -> None:
    decision = CalculationEvidencePolicy(enabled=True).evaluate(
        NumericClaimRequest(
            domain="general",
            outputText="The total is 42.",
            evidenceRecords=(
                {
                    "type": "Calculation",
                    "evidenceRef": "evidence:github_pat_unsafeToken12345",
                    "resultDigest": "sha256:" + "a" * 64,
                    "observedNumbers": ("42",),
                },
            ),
        )
    )

    assert decision.status == "passed"
    assert decision.evidence_refs == ()
    assert "github_pat_" not in str(decision.public_projection())


def test_test_run_numeric_evidence_requires_observed_number_binding() -> None:
    decision = CalculationEvidencePolicy(enabled=True).evaluate(
        NumericClaimRequest(
            domain="coding",
            outputText="3 tests passed.",
            evidenceRecords=(
                {
                    "type": "TestRun",
                    "evidenceRef": "evidence:test:1",
                    "resultDigest": "sha256:" + "c" * 64,
                    "observedNumbers": ("3",),
                },
            ),
        )
    )

    assert decision.status == "passed"
    assert decision.evidence_refs == ("evidence:test:1",)
    assert decision.final_answer_allowed is False
