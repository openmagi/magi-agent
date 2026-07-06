"""Wave 4a source-citation gate: detector, gate evaluation, and audit wiring.

TDD RED-first. The canonical RED fixture is the Tesla report text (hard numbers,
zero sources) which MUST produce ``uncited_high_risk_zero_source``. No em-dashes
anywhere in this file per the citation feature style rule.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from magi_agent.engine.driver import MagiEngineDriver
from magi_agent.evidence.citation_gate import (
    CitationGateResult,
    corpus_texts_from_snapshot,
    detect_high_risk_claims,
    evaluate_citation_gate,
)
from magi_agent.evidence.citation_registry import SessionSourceRegistry
from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
from magi_agent.evidence.types import EvidenceRecord

# The canonical RED fixture (design 16.4): numbers, zero sources.
TESLA_REPORT = (
    "Tesla posted revenue of $12.77B in the quarter. "
    "Cash reserves reached $44.1B while total debt stood at 8,400 million dollars. "
    "The stock remains a strong buy for long-term holders."
)


def _classes(text: str, **kwargs: object) -> list[tuple[str, bool]]:
    return [
        (claim.claim_class, claim.has_marker)
        for claim in detect_high_risk_claims(text, **kwargs)
    ]


# --- Piece A: high-risk claim detector golden table --------------------------


def test_numeric_class_detected() -> None:
    classes = _classes("Cash reserves reached $44.1B this year.")
    assert ("numeric", False) in classes


def test_currency_or_magnitude_figure_below_three_digits_is_numeric() -> None:
    # "$5B" has only one significant digit but is a currency/magnitude figure.
    assert _classes("The deal was worth $5B.") == [("numeric", False)]
    assert _classes("Margins came in at 3%.") == [("numeric", False)]


def test_date_class_detected() -> None:
    assert _classes("The merger was announced on March 3, 2026.") == [
        ("date", False)
    ]


def test_quote_class_detected() -> None:
    # A quoted span of 5+ words attributed to a named entity.
    text = 'Musk said "we will change the entire automotive industry forever" today.'
    assert _classes(text) == [("quote", False)]


def test_short_quote_is_not_high_risk() -> None:
    # Under 5 words inside the quotes is not the high-risk quoted-string class.
    assert _classes('She said "hello there friend" warmly.') == []


def test_superlative_class_detected() -> None:
    assert _classes("Tesla is the largest automaker in California.") == [
        ("superlative", False)
    ]


def test_opinions_and_plans_are_not_high_risk() -> None:
    assert _classes("I think we should ship this next week.") == []
    assert _classes("This is a great approach and worth trying.") == []


def test_fenced_code_is_excluded_entirely() -> None:
    text = (
        "Here is the snippet:\n"
        "```\n"
        "revenue = 12345678\n"
        "price = 999.99\n"
        "```\n"
        "That prints the totals."
    )
    # No claim from inside the fence; the prose line has no figures.
    assert detect_high_risk_claims(text) == ()


def test_user_supplied_figures_are_suppressed() -> None:
    # Every figure came from the user's own input: not high-risk.
    text = "You reported revenue of 12,345,678 dollars last month."
    assert detect_high_risk_claims(text, user_input="revenue of 12,345,678") == ()


def test_partial_user_figures_are_not_suppressed() -> None:
    # One figure is new (44.1), so the sentence stays high-risk.
    text = "Revenue was 12,345,678 and margin was 44.1 percent."
    claims = detect_high_risk_claims(text, user_input="revenue of 12,345,678")
    assert [claim.claim_class for claim in claims] == ["numeric"]


def test_corpus_supported_figures_still_want_a_marker() -> None:
    registry = SessionSourceRegistry(session_id="s1")
    registry.register(
        "web_fetch",
        "https://sec.gov/tsla",
        turn_id="t1",
        tool_name="web_fetch",
        title="Tesla 10-Q",
        snippets=("revenue was 12,770,000,000 dollars",),
    )
    corpus = corpus_texts_from_snapshot(registry.snapshot())
    claims = detect_high_risk_claims(
        "Revenue was 12,770,000,000 dollars.", corpus_texts=corpus
    )
    assert len(claims) == 1
    assert claims[0].corpus_supported is True
    # Corpus support is informational: the strict marker check still applies.
    assert claims[0].has_marker is False


def test_arithmetic_over_cited_figure_with_marker_is_marked_cited() -> None:
    claims = detect_high_risk_claims("Revenue rose to $12.77B [src_3] last quarter.")
    assert len(claims) == 1
    assert claims[0].has_marker is True


def test_marker_strictness_is_per_sentence() -> None:
    # A marker on sentence one does not cover sentence two (design OQ2).
    text = "Revenue was $12.77B [src_3]. Cash was $44.1B."
    claims = detect_high_risk_claims(text)
    by_marker = {claim.text.strip(): claim.has_marker for claim in claims}
    assert by_marker["Revenue was $12.77B [src_3]."] is True
    assert by_marker["Cash was $44.1B."] is False


def test_decimal_and_grouped_numbers_do_not_oversplit_sentences() -> None:
    # "44.1%" and "8,400" must not split the sentence at their punctuation.
    claims = detect_high_risk_claims("Margin was 44.1% on 8,400 units shipped.")
    assert len(claims) == 1


def test_char_spans_index_into_original_text() -> None:
    text = "Intro line. Cash was $44.1B here."
    claims = detect_high_risk_claims(text)
    assert len(claims) == 1
    claim = claims[0]
    assert text[claim.start : claim.end].strip() == "Cash was $44.1B here."


# --- Piece B: gate evaluation ------------------------------------------------


def test_tesla_report_produces_uncited_high_risk_zero_source() -> None:
    result = evaluate_citation_gate(
        TESLA_REPORT, registry_snapshot=(), per_turn_source_ids=(), user_input=""
    )
    assert result.verdict == "uncited"
    assert result.zero_source_turn is True
    kinds = [violation.kind for violation in result.violations]
    assert "uncited_high_risk_zero_source" in kinds


def _registry_with_one_source() -> SessionSourceRegistry:
    registry = SessionSourceRegistry(session_id="s1")
    registry.register(
        "web_fetch",
        "https://sec.gov/tsla",
        turn_id="t1",
        tool_name="web_fetch",
        title="Tesla 10-Q",
    )
    return registry


def test_dangling_ref_violation() -> None:
    registry = _registry_with_one_source()
    result = evaluate_citation_gate(
        "Revenue was $12.77B [src_9].",
        registry_snapshot=registry.snapshot(),
        per_turn_source_ids=("src_1",),
    )
    assert result.dangling_refs == ("src_9",)
    assert result.violations[0].kind == "dangling_ref"


def test_uncited_high_risk_when_sources_exist() -> None:
    registry = _registry_with_one_source()
    result = evaluate_citation_gate(
        "Revenue was $12.77B.",
        registry_snapshot=registry.snapshot(),
        per_turn_source_ids=("src_1",),
    )
    assert result.verdict == "uncited"
    assert result.zero_source_turn is False
    assert [violation.kind for violation in result.violations] == [
        "uncited_high_risk"
    ]


def test_clean_cited_turn_has_no_violations() -> None:
    registry = _registry_with_one_source()
    result = evaluate_citation_gate(
        "Revenue was $12.77B [src_1].",
        registry_snapshot=registry.snapshot(),
        per_turn_source_ids=("src_1",),
    )
    assert result.verdict == "cited"
    assert result.violations == ()
    assert result.cited_claims == 1


def test_partial_verdict_when_some_high_risk_uncited() -> None:
    registry = _registry_with_one_source()
    text = "Revenue was $12.77B [src_1]. Cash was $44.1B."
    result = evaluate_citation_gate(
        text,
        registry_snapshot=registry.snapshot(),
        per_turn_source_ids=("src_1",),
    )
    assert result.verdict == "partial"
    assert [violation.kind for violation in result.violations] == [
        "uncited_high_risk"
    ]


def test_advisory_only_content_is_not_applicable() -> None:
    result = evaluate_citation_gate(
        "I think we should proceed with the plan.",
        registry_snapshot=(),
        per_turn_source_ids=(),
    )
    assert result.verdict == "not_applicable"
    assert result.violations == ()


def test_dangling_severity_ordered_before_uncited() -> None:
    registry = _registry_with_one_source()
    # One sentence cites a dangling ref, another high-risk sentence is uncited.
    text = "Debt was $8.4B [src_9]. Cash was $44.1B."
    result = evaluate_citation_gate(
        text,
        registry_snapshot=registry.snapshot(),
        per_turn_source_ids=("src_1",),
    )
    kinds = [violation.kind for violation in result.violations]
    assert kinds[0] == "dangling_ref"
    assert "uncited_high_risk" in kinds


# --- Piece C: verdict record + audit-mode wiring -----------------------------


class _FakeDriver:
    """Minimal binding of the real driver citation-gate methods for testing.

    Binds the actual EngineDriver methods and the collector property onto a
    lightweight object with just a ``_runner`` exposing the collector, so the
    audit wiring can be exercised without standing up a full engine.
    """

    local_tool_evidence_collector = MagiEngineDriver.local_tool_evidence_collector
    _maybe_citation_gate_audit = MagiEngineDriver._maybe_citation_gate_audit
    _emit_citation_verdict_record = MagiEngineDriver._emit_citation_verdict_record

    def __init__(self, collector: object) -> None:
        self._runner = SimpleNamespace(local_tool_evidence_collector=collector)


def _citation_records(collector: LocalToolEvidenceCollector, turn_id: str) -> list[object]:
    return [
        record
        for record in collector.collect_for_turn(turn_id)
        if isinstance(record, EvidenceRecord)
        and record.type == "custom:CitationVerdict"
    ]


def _register_zero_source_turn(collector: LocalToolEvidenceCollector) -> None:
    # Force the registry to exist (audit only reads it), leaving it empty so the
    # Tesla case is a zero-source turn.
    collector.source_registry_for("sess")


def test_audit_mode_emits_verdict_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_GATE_MODE", "audit")
    collector = LocalToolEvidenceCollector()
    _register_zero_source_turn(collector)
    driver = _FakeDriver(collector)

    returned = driver._maybe_citation_gate_audit(
        session_id="sess", turn_id="turn", prompt="", final_text=TESLA_REPORT
    )

    # AUDIT mode never alters the turn: the call returns None (no decision).
    assert returned is None
    records = _citation_records(collector, "turn")
    assert len(records) == 1
    fields = dict(records[0].fields)
    assert fields["verdict"] == "uncited"
    assert fields["highRiskClaims"] >= 1
    assert fields["repairAttempts"] == 0
    assert fields["inducedSearch"] is False
    assert fields["failOpen"] is False
    # Producer provenance carries the first-party gate rule id (design 8).
    assert records[0].origin == "producer_control"
    assert records[0].producing_rule_id == "source_citation.gate"


def test_repair_mode_behaves_as_audit_in_wave_4a(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_GATE_MODE", "repair")
    collector = LocalToolEvidenceCollector()
    _register_zero_source_turn(collector)
    driver = _FakeDriver(collector)

    returned = driver._maybe_citation_gate_audit(
        session_id="sess", turn_id="turn", prompt="", final_text=TESLA_REPORT
    )
    # repair is accepted but does not block or repair in Wave 4a: a record is
    # emitted and the turn is not altered.
    assert returned is None
    assert len(_citation_records(collector, "turn")) == 1


def test_flag_off_emits_no_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "0")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_GATE_MODE", "audit")
    collector = LocalToolEvidenceCollector()
    driver = _FakeDriver(collector)

    driver._maybe_citation_gate_audit(
        session_id="sess", turn_id="turn", prompt="", final_text=TESLA_REPORT
    )
    assert _citation_records(collector, "turn") == []


def test_gate_mode_off_emits_no_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_GATE_MODE", "off")
    collector = LocalToolEvidenceCollector()
    _register_zero_source_turn(collector)
    driver = _FakeDriver(collector)

    driver._maybe_citation_gate_audit(
        session_id="sess", turn_id="turn", prompt="", final_text=TESLA_REPORT
    )
    assert _citation_records(collector, "turn") == []


def test_emit_record_ignores_non_result_input() -> None:
    # Defensive: a non-CitationGateResult never produces a record.
    collector = LocalToolEvidenceCollector()
    driver = _FakeDriver(collector)
    driver._emit_citation_verdict_record(
        session_id="sess", turn_id="turn", result=object()
    )
    assert _citation_records(collector, "turn") == []


def test_verdict_record_carries_violation_detail_in_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_GATE_MODE", "audit")
    collector = LocalToolEvidenceCollector()
    _register_zero_source_turn(collector)
    driver = _FakeDriver(collector)
    driver._maybe_citation_gate_audit(
        session_id="sess", turn_id="turn", prompt="", final_text=TESLA_REPORT
    )
    record = _citation_records(collector, "turn")[0]
    metadata = dict(record.metadata)
    kinds = [entry["kind"] for entry in metadata["violations"]]
    assert "uncited_high_risk_zero_source" in kinds
    assert metadata["zeroSourceTurn"] is True


def test_result_type_shape() -> None:
    result = evaluate_citation_gate(TESLA_REPORT, registry_snapshot=())
    assert isinstance(result, CitationGateResult)
    assert result.verdict in {"cited", "partial", "uncited", "not_applicable"}
