"""Tests for OutputContractGate (PR 1 — Stage A deterministic + PR 2 — Stage B LLM repair).

Anti-overfitting firewall: this file MUST NOT import anything from
benchmarks.gaia.  A dedicated test enforces this at the module level.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
import types
from collections.abc import Callable

import pytest

from magi_agent.research.output_contract_gate import (
    OutputContract,
    OutputContractGateRequest,
    OutputContractGateResult,
    OutputContractModelCallReceipt,
    evaluate_output_contract_gate,
)


# ---------------------------------------------------------------------------
# Anti-overfitting firewall test
# ---------------------------------------------------------------------------


def test_gate_module_does_not_import_gaia_scorer() -> None:
    """The gate must not import benchmarks.gaia.scorer — structural firewall."""
    gate_mod = importlib.import_module("magi_agent.research.output_contract_gate")
    # Collect all module names reachable from the gate's own imports (direct only).
    imported_names: set[str] = set()
    for attr_name in dir(gate_mod):
        attr = getattr(gate_mod, attr_name, None)
        if isinstance(attr, types.ModuleType):
            imported_names.add(attr.__name__)
    # Also check sys.modules for anything the gate may have pulled in transitively
    # by inspecting only those that share the gaia prefix.
    gate_source = gate_mod.__file__ or ""
    gaia_scorer_name = "benchmarks.gaia.scorer"
    gaia_scorer_mod = sys.modules.get(gaia_scorer_name)
    # Even if the scorer is already loaded (e.g. by another test), the gate must not
    # hold a reference to it.
    assert gaia_scorer_name not in imported_names, (
        "output_contract_gate must not import benchmarks.gaia.scorer"
    )
    # Verify via the module's __dict__ that no attribute points to the scorer module.
    gate_dict_values = gate_mod.__dict__.values()
    if gaia_scorer_mod is not None:
        assert gaia_scorer_mod not in gate_dict_values, (
            "output_contract_gate must not hold a reference to the gaia scorer"
        )
    # Also verify the file path doesn't import from benchmarks.gaia
    if gate_source:
        with open(gate_source) as fh:
            source_text = fh.read()
        assert "benchmarks.gaia" not in source_text, (
            "output_contract_gate source must not reference benchmarks.gaia"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _req(
    candidate: str,
    *,
    mode: str = "enforce",
    contract_type: str = "string",
    concise: bool = False,
    max_items: int | None = None,
    min_items: int | None = None,
    forbid_articles: bool = False,
    forbid_abbreviations: bool = False,
    max_chars: int | None = None,
    min_chars: int | None = None,
    allow_punctuation: bool = True,
    forbid_units: bool = False,
    gate_id: str = "test-gate",
    contract_id: str = "test-contract.v1",
    model_provider: object | None = None,
) -> OutputContractGateRequest:
    contract = OutputContract(
        contract_id=contract_id,
        type=contract_type,
        concise=concise,
        max_items=max_items,
        min_items=min_items,
        forbid_articles=forbid_articles,
        forbid_abbreviations=forbid_abbreviations,
        max_chars=max_chars,
        min_chars=min_chars,
        allow_punctuation=allow_punctuation,
        forbid_units=forbid_units,
    )
    return OutputContractGateRequest(
        gate_id=gate_id,
        mode=mode,
        candidate_final_answer=candidate,
        contract=contract,
        model_provider=model_provider,
    )


# ---------------------------------------------------------------------------
# Mode: off
# ---------------------------------------------------------------------------


class TestModeOff:
    def test_off_always_skips(self) -> None:
        result = evaluate_output_contract_gate(_req("anything", mode="off"))
        assert result.status == "skipped"
        assert result.ok is True
        assert "output_contract_gate_off" in result.reason_codes

    def test_off_preserves_original_answer(self) -> None:
        result = evaluate_output_contract_gate(_req("anything goes here", mode="off"))
        assert result.conformed_answer is None

    def test_off_with_number_contract_still_skips(self) -> None:
        result = evaluate_output_contract_gate(
            _req("not a number at all", mode="off", contract_type="number")
        )
        assert result.status == "skipped"
        assert result.ok is True


# ---------------------------------------------------------------------------
# Mode: audit
# ---------------------------------------------------------------------------


class TestModeAudit:
    def test_audit_returns_ok_true_even_with_violations(self) -> None:
        result = evaluate_output_contract_gate(
            _req("This is a sentence answer", mode="audit", contract_type="number")
        )
        assert result.ok is True
        assert result.status == "audit"
        assert "type_mismatch" in result.reason_codes

    def test_audit_does_not_change_answer(self) -> None:
        result = evaluate_output_contract_gate(
            _req("The castle", mode="audit", contract_type="string", forbid_articles=True)
        )
        assert result.conformed_answer is None
        assert result.ok is True

    def test_audit_clean_answer_has_no_violations(self) -> None:
        result = evaluate_output_contract_gate(
            _req("42", mode="audit", contract_type="number")
        )
        assert result.ok is True
        assert result.reason_codes == ("output_contract_passed",)


# ---------------------------------------------------------------------------
# Stage A — deterministic checks
# ---------------------------------------------------------------------------


class TestTypeConformance:
    def test_number_contract_passes_integer_string(self) -> None:
        result = evaluate_output_contract_gate(_req("42", contract_type="number"))
        assert result.ok is True
        assert "type_mismatch" not in result.reason_codes

    def test_number_contract_passes_float_string(self) -> None:
        result = evaluate_output_contract_gate(_req("3.14", contract_type="number"))
        assert result.ok is True

    def test_number_contract_fails_prose(self) -> None:
        result = evaluate_output_contract_gate(
            _req("The answer is forty-two", contract_type="number")
        )
        assert result.ok is False
        assert "type_mismatch" in result.reason_codes

    def test_integer_contract_passes_integer(self) -> None:
        result = evaluate_output_contract_gate(_req("7", contract_type="integer"))
        assert result.ok is True

    def test_integer_contract_fails_float(self) -> None:
        result = evaluate_output_contract_gate(_req("3.14", contract_type="integer"))
        assert result.ok is False
        assert "type_mismatch" in result.reason_codes

    def test_boolean_contract_passes_yes(self) -> None:
        result = evaluate_output_contract_gate(_req("yes", contract_type="boolean"))
        assert result.ok is True

    def test_boolean_contract_passes_no(self) -> None:
        result = evaluate_output_contract_gate(_req("No", contract_type="boolean"))
        assert result.ok is True

    def test_boolean_contract_passes_true(self) -> None:
        result = evaluate_output_contract_gate(_req("True", contract_type="boolean"))
        assert result.ok is True

    def test_boolean_contract_fails_prose(self) -> None:
        result = evaluate_output_contract_gate(
            _req("I think the answer is affirmative", contract_type="boolean")
        )
        assert result.ok is False
        assert "type_mismatch" in result.reason_codes

    def test_unspecified_contract_always_passes(self) -> None:
        result = evaluate_output_contract_gate(
            _req("anything at all", contract_type="unspecified")
        )
        assert result.ok is True

    def test_list_contract_passes_comma_separated(self) -> None:
        result = evaluate_output_contract_gate(_req("apple, banana, cherry", contract_type="list"))
        assert result.ok is True

    def test_list_of_numbers_passes(self) -> None:
        result = evaluate_output_contract_gate(
            _req("1, 2, 3", contract_type="list_of_numbers")
        )
        assert result.ok is True

    def test_list_of_numbers_fails_non_numeric(self) -> None:
        result = evaluate_output_contract_gate(
            _req("apple, banana, cherry", contract_type="list_of_numbers")
        )
        assert result.ok is False
        assert "type_mismatch" in result.reason_codes


class TestConciseness:
    def test_concise_flag_flags_scene_heading(self) -> None:
        """INT. THE CASTLE - DAY 1 should be flagged as concise_violation."""
        result = evaluate_output_contract_gate(
            _req("INT. THE CASTLE - DAY 1", concise=True, contract_type="string")
        )
        assert result.ok is False
        assert "concise_violation" in result.reason_codes

    def test_concise_flag_flags_final_answer_prefix(self) -> None:
        result = evaluate_output_contract_gate(
            _req("FINAL ANSWER: The castle", concise=True, contract_type="string")
        )
        assert result.ok is False
        assert "concise_violation" in result.reason_codes

    def test_concise_flag_flags_the_answer_is_prefix(self) -> None:
        result = evaluate_output_contract_gate(
            _req("The answer is: 42", concise=True, contract_type="number")
        )
        assert result.ok is False
        assert "concise_violation" in result.reason_codes

    def test_concise_clean_short_answer_passes(self) -> None:
        result = evaluate_output_contract_gate(
            _req("THE CASTLE", concise=True, contract_type="string")
        )
        assert result.ok is True

    def test_concise_number_passes(self) -> None:
        result = evaluate_output_contract_gate(
            _req("42", concise=True, contract_type="number")
        )
        assert result.ok is True

    def test_concise_false_does_not_flag_long_answer(self) -> None:
        result = evaluate_output_contract_gate(
            _req(
                "INT. THE CASTLE - DAY 1 with lots of extra context",
                concise=False,
                contract_type="string",
            )
        )
        # Without concise=True, no concise_violation
        assert "concise_violation" not in result.reason_codes


class TestArticleDetection:
    def test_forbid_articles_flags_the(self) -> None:
        result = evaluate_output_contract_gate(
            _req("The castle", contract_type="string", forbid_articles=True)
        )
        assert result.ok is False
        assert "article_present" in result.reason_codes

    def test_forbid_articles_flags_a(self) -> None:
        result = evaluate_output_contract_gate(
            _req("A dog", contract_type="string", forbid_articles=True)
        )
        assert result.ok is False
        assert "article_present" in result.reason_codes

    def test_forbid_articles_flags_an(self) -> None:
        result = evaluate_output_contract_gate(
            _req("An apple", contract_type="string", forbid_articles=True)
        )
        assert result.ok is False
        assert "article_present" in result.reason_codes

    def test_forbid_articles_passes_no_article(self) -> None:
        result = evaluate_output_contract_gate(
            _req("Castle", contract_type="string", forbid_articles=True)
        )
        assert result.ok is True

    def test_allow_articles_passes_the(self) -> None:
        result = evaluate_output_contract_gate(
            _req("The castle", contract_type="string", forbid_articles=False)
        )
        assert "article_present" not in result.reason_codes


class TestTrailingPeriod:
    def test_trailing_period_on_number_flagged(self) -> None:
        result = evaluate_output_contract_gate(_req("42.", contract_type="number"))
        assert result.ok is False
        assert "trailing_period_on_number" in result.reason_codes

    def test_no_trailing_period_on_number_passes(self) -> None:
        result = evaluate_output_contract_gate(_req("42", contract_type="number"))
        assert result.ok is True

    def test_trailing_period_on_string_not_flagged(self) -> None:
        result = evaluate_output_contract_gate(
            _req("Castle.", contract_type="string")
        )
        assert "trailing_period_on_number" not in result.reason_codes


class TestLengthConstraints:
    def test_max_chars_violation(self) -> None:
        result = evaluate_output_contract_gate(
            _req("A" * 101, contract_type="string", max_chars=100)
        )
        assert result.ok is False
        assert "length_violation" in result.reason_codes

    def test_max_chars_passes(self) -> None:
        result = evaluate_output_contract_gate(
            _req("A" * 100, contract_type="string", max_chars=100)
        )
        assert "length_violation" not in result.reason_codes

    def test_min_chars_violation(self) -> None:
        result = evaluate_output_contract_gate(
            _req("Hi", contract_type="string", min_chars=5)
        )
        assert result.ok is False
        assert "length_violation" in result.reason_codes

    def test_min_chars_passes(self) -> None:
        result = evaluate_output_contract_gate(
            _req("Hello", contract_type="string", min_chars=5)
        )
        assert "length_violation" not in result.reason_codes


class TestListItemCount:
    def test_max_items_violation(self) -> None:
        result = evaluate_output_contract_gate(
            _req("apple, banana, cherry", contract_type="list", max_items=2)
        )
        assert result.ok is False
        assert "list_count_violation" in result.reason_codes

    def test_max_items_passes(self) -> None:
        result = evaluate_output_contract_gate(
            _req("apple, banana", contract_type="list", max_items=2)
        )
        assert "list_count_violation" not in result.reason_codes

    def test_min_items_violation(self) -> None:
        result = evaluate_output_contract_gate(
            _req("apple", contract_type="list", min_items=2)
        )
        assert result.ok is False
        assert "list_count_violation" in result.reason_codes


class TestEnforceMode:
    def test_enforce_returns_ok_false_on_violation(self) -> None:
        result = evaluate_output_contract_gate(
            _req("Not a number", mode="enforce", contract_type="number")
        )
        assert result.ok is False
        assert result.status == "format_violation"

    def test_enforce_returns_ok_true_when_clean(self) -> None:
        result = evaluate_output_contract_gate(
            _req("42", mode="enforce", contract_type="number")
        )
        assert result.ok is True
        assert result.status == "passed"


# ---------------------------------------------------------------------------
# Stage B — LLM repair (fake model provider)
# ---------------------------------------------------------------------------


class _FakeModelProvider:
    """Fake model provider for hermetic llm_repair tests.

    Returns canned responses via a mapping of (candidate, contract_type) → repaired.
    No real API calls are made.
    """

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        for key, val in self._responses.items():
            if key in prompt:
                return val
        return prompt  # echo back if no match


class TestLLMRepairMode:
    def test_llm_repair_skipped_when_no_violation(self) -> None:
        """When Stage A passes, Stage B must not run."""
        provider = _FakeModelProvider({"42": "repaired but should not be called"})
        result = evaluate_output_contract_gate(
            _req("42", mode="llm_repair", contract_type="number", model_provider=provider)
        )
        assert result.ok is True
        assert result.status == "passed"
        assert not provider.calls, "model provider must not be called when no violation"

    def test_llm_repair_succeeds_scene_heading(self) -> None:
        """Scene heading 'INT. THE CASTLE - DAY 1' → repaired to 'THE CASTLE'."""
        provider = _FakeModelProvider({"INT. THE CASTLE - DAY 1": "THE CASTLE"})
        result = evaluate_output_contract_gate(
            _req(
                "INT. THE CASTLE - DAY 1",
                mode="llm_repair",
                contract_type="string",
                concise=True,
                model_provider=provider,
            )
        )
        assert result.status == "repaired"
        assert result.conformed_answer == "THE CASTLE"
        assert result.repair_applied is True

    def test_llm_repair_records_receipt(self) -> None:
        """A repair must produce a ModelCallReceipt."""
        provider = _FakeModelProvider({"INT. THE CASTLE - DAY 1": "THE CASTLE"})
        result = evaluate_output_contract_gate(
            _req(
                "INT. THE CASTLE - DAY 1",
                mode="llm_repair",
                contract_type="string",
                concise=True,
                model_provider=provider,
            )
        )
        assert result.model_call_receipt is not None
        receipt = result.model_call_receipt
        assert isinstance(receipt, OutputContractModelCallReceipt)
        assert receipt.gate_id == "test-gate"
        assert receipt.contract_id == "test-contract.v1"
        assert receipt.similarity_score is not None
        assert 0.0 <= receipt.similarity_score <= 1.0

    def test_llm_repair_refused_when_numeric_value_changes(self) -> None:
        """For type=number, if the repaired numeric value differs, refuse repair."""
        # Candidate is "42." (trailing period flagged), fake model returns "43"
        provider = _FakeModelProvider({"42.": "43"})
        result = evaluate_output_contract_gate(
            _req("42.", mode="llm_repair", contract_type="number", model_provider=provider)
        )
        assert result.status == "repair_refused"
        assert result.conformed_answer is None
        assert result.repair_applied is False

    def test_llm_repair_refused_when_jaccard_too_low(self) -> None:
        """If the repair changes more tokens than allowed (Jaccard < threshold), refuse."""
        # Candidate: "Castle" (with article violation — "The Castle")
        # Fake model returns completely different text
        provider = _FakeModelProvider({"The Castle": "Completely different unrelated text here"})
        result = evaluate_output_contract_gate(
            _req(
                "The Castle",
                mode="llm_repair",
                contract_type="string",
                forbid_articles=True,
                model_provider=provider,
            )
        )
        assert result.status == "repair_refused"
        assert result.repair_applied is False

    def test_llm_repair_refused_when_stage_a_still_fails_after_repair(self) -> None:
        """If repaired text still fails Stage A, refuse."""
        # Candidate has article violation; fake model returns text that still has article
        provider = _FakeModelProvider({"The Castle": "A Castle"})
        result = evaluate_output_contract_gate(
            _req(
                "The Castle",
                mode="llm_repair",
                contract_type="string",
                forbid_articles=True,
                model_provider=provider,
            )
        )
        assert result.status == "repair_refused"

    def test_llm_repair_succeeds_removes_article(self) -> None:
        """Repair that removes leading article and preserves content succeeds."""
        provider = _FakeModelProvider({"The Castle": "Castle"})
        result = evaluate_output_contract_gate(
            _req(
                "The Castle",
                mode="llm_repair",
                contract_type="string",
                forbid_articles=True,
                model_provider=provider,
            )
        )
        assert result.status == "repaired"
        assert result.conformed_answer == "Castle"

    def test_llm_repair_falls_through_without_provider(self) -> None:
        """If llm_repair mode but no model_provider given, treat like enforce."""
        result = evaluate_output_contract_gate(
            _req(
                "The Castle",
                mode="llm_repair",
                contract_type="string",
                forbid_articles=True,
                model_provider=None,
            )
        )
        # Without a provider, repair is not attempted; falls back to format_violation
        assert result.status in {"format_violation", "repair_refused"}
        assert result.ok is False

    def test_llm_repair_no_repair_applied_when_skipped(self) -> None:
        result = evaluate_output_contract_gate(
            _req("42", mode="llm_repair", contract_type="number", model_provider=None)
        )
        assert result.repair_applied is False
        assert result.ok is True


# ---------------------------------------------------------------------------
# Semantic preservation guard — numeric value identity
# ---------------------------------------------------------------------------


class TestSemanticPreservation:
    def test_number_repair_same_value_passes(self) -> None:
        """42. → 42 (same numeric value) should succeed."""
        provider = _FakeModelProvider({"42.": "42"})
        result = evaluate_output_contract_gate(
            _req("42.", mode="llm_repair", contract_type="number", model_provider=provider)
        )
        assert result.status == "repaired"
        assert result.conformed_answer == "42"

    def test_number_repair_different_value_refused(self) -> None:
        """42. → 43 (different numeric value) must always be refused."""
        provider = _FakeModelProvider({"42.": "43"})
        result = evaluate_output_contract_gate(
            _req("42.", mode="llm_repair", contract_type="number", model_provider=provider)
        )
        assert result.status == "repair_refused"

    def test_integer_repair_same_value_passes(self) -> None:
        provider = _FakeModelProvider({"7.": "7"})
        result = evaluate_output_contract_gate(
            _req("7.", mode="llm_repair", contract_type="integer", model_provider=provider)
        )
        assert result.status == "repaired"

    def test_integer_repair_different_value_refused(self) -> None:
        provider = _FakeModelProvider({"7.": "8"})
        result = evaluate_output_contract_gate(
            _req("7.", mode="llm_repair", contract_type="integer", model_provider=provider)
        )
        assert result.status == "repair_refused"


# ---------------------------------------------------------------------------
# Digest fields and audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail:
    def test_candidate_digest_present(self) -> None:
        result = evaluate_output_contract_gate(_req("42", contract_type="number"))
        assert result.candidate_digest.startswith("sha256:")

    def test_conformed_digest_equals_candidate_when_not_repaired(self) -> None:
        result = evaluate_output_contract_gate(_req("42", contract_type="number"))
        assert result.conformed_digest == result.candidate_digest

    def test_conformed_digest_differs_after_repair(self) -> None:
        provider = _FakeModelProvider({"42.": "42"})
        result = evaluate_output_contract_gate(
            _req("42.", mode="llm_repair", contract_type="number", model_provider=provider)
        )
        assert result.conformed_digest != result.candidate_digest

    def test_repair_applied_false_when_no_repair(self) -> None:
        result = evaluate_output_contract_gate(_req("42", contract_type="number"))
        assert result.repair_applied is False


# ---------------------------------------------------------------------------
# Execution posture
# ---------------------------------------------------------------------------


class TestExecutionPosture:
    def test_execution_posture_all_false(self) -> None:
        result = evaluate_output_contract_gate(_req("42", contract_type="number"))
        posture = result.execution_posture
        assert posture.default_off is True
        assert posture.live_execution_allowed is False
        assert posture.model_calls_allowed is False
        assert posture.channel_delivery_allowed is False
        assert posture.adk_runner_attached is False


# ---------------------------------------------------------------------------
# OutputContract model validation
# ---------------------------------------------------------------------------


class TestOutputContractModel:
    def test_frozen_contract_raises_on_mutation(self) -> None:
        contract = OutputContract(contract_id="test.v1", type="string")
        with pytest.raises(Exception):
            contract.type = "number"  # type: ignore[misc]

    def test_contract_requires_contract_id(self) -> None:
        with pytest.raises(Exception):
            OutputContract(type="string")  # type: ignore[call-arg]

    def test_contract_accepts_all_types(self) -> None:
        for t in (
            "unspecified",
            "number",
            "integer",
            "string",
            "text",
            "list",
            "list_of_numbers",
            "filename",
            "code",
            "boolean",
        ):
            contract = OutputContract(contract_id=f"test-{t}.v1", type=t)
            assert contract.type == t


# ---------------------------------------------------------------------------
# Gate request validation
# ---------------------------------------------------------------------------


class TestGateRequestValidation:
    def test_empty_candidate_rejected(self) -> None:
        with pytest.raises(Exception):
            _req("")

    def test_valid_request_passes_validation(self) -> None:
        req = _req("valid answer", contract_type="string")
        assert req.candidate_final_answer == "valid answer"

    def test_gate_id_stored(self) -> None:
        req = _req("answer", gate_id="my-gate-v1")
        assert req.gate_id == "my-gate-v1"
