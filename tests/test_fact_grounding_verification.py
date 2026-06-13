"""Semantic grounding verification wired into the live evidence gate.

The deterministic ``evaluate_answer_grounding`` detector
(``magi_agent.research.grounded_answer_guard``) had ZERO live callers: a
research answer that asserted a specific numeric/identifier value NOT present in
the opened-source corpus was never blocked. This suite covers the wiring that
makes it block, behind the strict default-OFF
``MAGI_FACT_GROUNDING_VERIFICATION_ENABLED`` flag:

* Flag default-OFF -> the satisfier is inert; the engine gate is byte-identical
  to main (the bare ``fact_grounding`` required-validator behaves exactly as it
  does today).
* Flag ON + a fabricated specific value (not in the corpus) -> ``guess`` ->
  the ``fact_grounding`` requirement stays missing -> gate ``block`` ->
  ``pre_final_evidence_gate_blocked``.
* Flag ON + a grounded specific value (present in the corpus) -> ``grounded``
  -> the ``fact_grounding`` requirement is satisfied -> gate passes.
* Flag ON + a semantic-only answer (no specific value to ground) -> ``grounded``
  (G4 boundary) -> no false block.

The producer is a PURE function over the turn's collected evidence corpus + the
final answer text; activation/gating lives in the engine caller.
"""
from __future__ import annotations

from magi_agent.config.env import parse_fact_grounding_verification_enabled
from magi_agent.evidence.claim_grounding import (
    FACT_GROUNDING_REQUIREMENT_LABEL,
    FactGroundingEvidenceProducer,
)
from magi_agent.evidence.types import EvidenceRecord


# ---------------------------------------------------------------------------
# Flag — strict default OFF
# ---------------------------------------------------------------------------


def test_flag_default_off() -> None:
    assert parse_fact_grounding_verification_enabled({}) is False
    assert (
        parse_fact_grounding_verification_enabled(
            {"MAGI_FACT_GROUNDING_VERIFICATION_ENABLED": ""}
        )
        is False
    )
    assert (
        parse_fact_grounding_verification_enabled(
            {"MAGI_FACT_GROUNDING_VERIFICATION_ENABLED": "0"}
        )
        is False
    )


def test_flag_explicit_on() -> None:
    assert (
        parse_fact_grounding_verification_enabled(
            {"MAGI_FACT_GROUNDING_VERIFICATION_ENABLED": "1"}
        )
        is True
    )


def test_flag_never_defaults_on_in_full_profile() -> None:
    # Strict default-OFF: the full runtime profile must NOT flip it on.
    assert (
        parse_fact_grounding_verification_enabled({"MAGI_RUNTIME_PROFILE": "full"})
        is False
    )


# ---------------------------------------------------------------------------
# Producer — pure grounding decision over the collected evidence corpus
# ---------------------------------------------------------------------------


def _source_record(preview: str) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "type": "SourceInspection",
            "status": "ok",
            "observedAt": 1000.0,
            "source": {"kind": "tool_trace", "toolName": "WebFetch"},
            "preview": preview,
        }
    )


def test_producer_grounded_when_value_in_corpus() -> None:
    producer = FactGroundingEvidenceProducer()
    records = (_source_record("The channel reported 776,665 subscribers in May."),)
    verdict = producer.evaluate(final_text="It has 776665 subscribers.", evidence_records=records)
    assert verdict.grounded is True
    assert verdict.satisfied_label == FACT_GROUNDING_REQUIREMENT_LABEL
    assert verdict.status == "grounded"


def test_producer_guess_when_value_absent_from_corpus() -> None:
    producer = FactGroundingEvidenceProducer()
    records = (_source_record("The channel is popular but the page would not load."),)
    verdict = producer.evaluate(
        final_text="It has exactly 776665 subscribers.", evidence_records=records
    )
    assert verdict.grounded is False
    assert verdict.satisfied_label is None
    assert verdict.status == "guess"


def test_producer_grounded_when_no_specific_value_to_ground() -> None:
    # G4 boundary: a general natural-language answer asserts no specific
    # numeric/identifier value, so there is nothing to ground -> grounded/pass.
    producer = FactGroundingEvidenceProducer()
    records = (_source_record("Background notes about the topic."),)
    verdict = producer.evaluate(
        final_text="The thesis is consistent with the cited reporting.",
        evidence_records=records,
    )
    assert verdict.grounded is True
    assert verdict.satisfied_label == FACT_GROUNDING_REQUIREMENT_LABEL


def test_producer_corpus_reads_fields_and_metadata_strings() -> None:
    # The corpus is built from preview + fields + metadata string values so a
    # value surfaced in a structured field still counts as grounded.
    producer = FactGroundingEvidenceProducer()
    record = EvidenceRecord.model_validate(
        {
            "type": "WebSearch",
            "status": "ok",
            "observedAt": 1000.0,
            "source": {"kind": "tool_trace", "toolName": "WebSearch"},
            "fields": {"viewCount": "1,234,567 views"},
        }
    )
    verdict = producer.evaluate(final_text="It has 1234567 views.", evidence_records=(record,))
    assert verdict.grounded is True


def test_producer_empty_corpus_and_specific_value_is_guess() -> None:
    producer = FactGroundingEvidenceProducer()
    verdict = producer.evaluate(
        final_text="The exact count is 9876543.", evidence_records=()
    )
    assert verdict.grounded is False
    assert verdict.status == "guess"


def test_producer_matched_label_is_the_research_requirement_label() -> None:
    # The label the producer satisfies on grounded MUST be exactly the bare
    # required-validator the research recipe carries, or the engine gate could
    # never count it.
    from magi_agent.recipes.reliability_policy import RecipeReliabilityPolicyRegistry

    policy = RecipeReliabilityPolicyRegistry.with_defaults().for_recipe(
        "openmagi.research", modelTier="standard"
    )
    assert FACT_GROUNDING_REQUIREMENT_LABEL in policy.required_validators
