from __future__ import annotations

import json
from pathlib import Path

from openmagi_core_agent.harness.long_context_eval import (
    LongContextReliabilityCase,
    evaluate_long_context_case,
)


FIXTURE = Path(__file__).parent / "fixtures" / "runtime_reliability" / "long_context_matrix.json"


def _cases() -> list[LongContextReliabilityCase]:
    return [
        LongContextReliabilityCase.model_validate(item)
        for item in json.loads(FIXTURE.read_text(encoding="utf-8"))
    ]


def test_long_context_matrix_contains_required_cases() -> None:
    case_ids = {case.case_id for case in _cases()}

    assert case_ids == {
        "middle_fact_retrieval",
        "scattered_multi_doc_evidence",
        "unsupported_source_claim",
        "distant_code_dependency",
        "chunk_synthesis_coverage",
        "cheap_model_context_budget",
        "long_context_model_still_requires_refs",
    }


def test_each_fixture_maps_to_context_request_shape_validator_and_final_gate() -> None:
    for case in _cases():
        result = evaluate_long_context_case(case)

        assert result.case_id == case.case_id
        assert result.context_budget_plan.included_refs
        assert result.request_shape_record.input_refs
        assert result.validator_outcome == case.expected_validator_outcome
        assert result.final_gate_action == case.expected_final_gate_action
        assert result.request_shape_record.public_projection()["inputRefs"]


def test_cheap_model_case_forces_ref_chunk_mode_and_bounds_refs() -> None:
    case = next(case for case in _cases() if case.case_id == "cheap_model_context_budget")
    result = evaluate_long_context_case(case)

    assert result.context_budget_plan.strategy == "refs_only_with_chunk_summaries"
    assert result.context_budget_plan.raw_context_included is False
    assert len(result.context_budget_plan.included_refs) <= 6
    assert "raw_context_too_large" in result.context_budget_plan.reason_codes


def test_long_context_model_allows_more_refs_but_still_avoids_raw_stuffing() -> None:
    case = next(case for case in _cases() if case.case_id == "long_context_model_still_requires_refs")
    result = evaluate_long_context_case(case)

    assert result.context_budget_plan.max_refs > 20
    assert result.context_budget_plan.raw_context_included is False
    assert "refs_recorded" in result.context_budget_plan.reason_codes


def test_unsupported_source_claim_repairs_in_final_gate() -> None:
    case = next(case for case in _cases() if case.case_id == "unsupported_source_claim")
    result = evaluate_long_context_case(case)

    assert result.validator_outcome == "repair_required"
    assert result.final_gate_action == "repair_required"
    assert "unsupported_citation" in result.final_gate_decision.reason_codes
