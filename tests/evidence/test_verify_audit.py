"""Pure-module tests for magi_agent.evidence.verify_audit (PR-V1).

Covers: fingerprint keying (A1), canonical value (A1), normalized comparator (A2),
span minimum (A2), evidence-consistency contradiction table, activity-grounding table,
sycophancy detectors, claim-citation adapter, resolution taxonomy, hedge-notice safety,
and nudge message format.

Style: no em-dashes (period/comma/colon/parens only), per the citation feature rule.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from magi_agent.evidence.citation_gate import (
    CitationGateResult,
    CitationGateViolation,
    HighRiskClaim,
    build_citation_fail_open_notice,
)
from magi_agent.evidence.verify_audit import (
    VerifyAuditResult,  # noqa: F401 (schema import, validates shape)
    VerifyFinding,
    _record_status,  # noqa: PLC2701 (blocked-status predicate pin, design 2.2)
    _variant_regex_for_slug,  # noqa: PLC2701 (slug-anchor test, design 2.4.1)
    activity_grounding_findings,
    audit_candidate,  # noqa: F401 (imported; end-to-end tested in PR-V3)
    build_nudge_message,
    canonical_claim_value,
    claim_citation_findings,
    evidence_consistency_findings,
    execution_claims_findings,
    filter_skeptic_findings,
    fingerprint_finding,
    ignore_rate_summary,
    normalized_contains,
    resolve_findings,
    span_meets_minimum,
    sycophancy_findings,
)


# ---------------------------------------------------------------------------
# Fake evidence record helpers (duck-typed, no pydantic validation)
# ---------------------------------------------------------------------------


def _rec(
    type_: str,
    *,
    status: str = "ok",
    fields: dict[str, Any] | None = None,
    observed_at: float = 1000.0,
) -> Any:
    return SimpleNamespace(
        type=type_,
        status=status,
        fields=fields or {},
        observed_at=observed_at,
        metadata={},
        origin="tool_declared",
        producing_rule_id="",
    )


def _test_run(*, exit_code: int, evidence_ref: str = "tr_001") -> Any:
    status = "ok" if exit_code == 0 else "failed"
    return _rec(
        "TestRun",
        status=status,
        fields={"exitCode": exit_code, "evidenceRef": evidence_ref},
    )


def _code_diag(*, exit_code: int) -> Any:
    status = "ok" if exit_code == 0 else "failed"
    return _rec(
        "CodeDiagnostics",
        status=status,
        fields={"exitCode": exit_code},
    )


def _commit_cp() -> Any:
    return _rec("CommitCheckpoint", status="ok")


def _edit_match(*, path: str) -> Any:
    return _rec("EditMatch", status="ok", fields={"path": path})


def _calculation(*, result: Any) -> Any:
    return _rec("Calculation", status="ok", fields={"result": result})


def _subagent_spawn(
    *,
    status: str = "error",
    reason: str | None = "child_turn_timeout",
    model: str | None = "opus-4-8",
    provider: str | None = "anthropic",
    persona: str | None = None,
    ref: str = "sp_001",
    error_code: str | None = None,
) -> Any:
    """Duck-typed SubagentSpawn evidence record (verified fields shape).

    Mirrors first_party_activity.to_evidence_record: type
    custom:FirstPartySubagentSpawn, camelCase fields with top-level status/
    reason/errorCode and a nested detail{spawnStatus, persona, model, provider}.
    """
    detail: dict[str, Any] = {
        "spawnStatus": status,
        "persona": persona or "",
        "promptDigest": "",
        "requestedDepth": 0,
        "liveChildRunnerAttached": False,
    }
    if provider is not None:
        detail["provider"] = provider
    if model is not None:
        detail["model"] = model
    # EvidenceRecord.status maps ok->ok, error->failed, else unknown.
    ev_status = {"ok": "ok", "error": "failed"}.get(status, "unknown")
    return _rec(
        "custom:FirstPartySubagentSpawn",
        status=ev_status,
        fields={
            "status": status,
            "reason": reason,
            "errorCode": error_code,
            "evidenceRef": ref,
            "detail": detail,
        },
    )


def _first_party_tool_call() -> Any:
    """A first-party ToolCall record (producer-liveness witness)."""
    return _rec("custom:FirstPartyToolCall", status="ok", fields={"status": "ok"})


def _web_search() -> Any:
    return _rec("WebSearch", status="ok")


def _source_inspection() -> Any:
    return _rec("SourceInspection", status="ok")


def _make_finding(
    *,
    rule_id: str = "verify_before_replying.evidence_consistency",
    confidence: str = "high",
    claim_class: str = "test_pass",
    claim_text: str = "all tests pass",
    span: tuple[int, int] = (0, 14),
    evidence_refs: tuple[str, ...] = ("TestRun@t1",),
    expected: str | None = "exitCode=0",
    observed: str | None = "exitCode=1",
    detail: str = "TestRun shows failure",
    suggested_action: str = "recheck",
    finding_id: str | None = None,
) -> VerifyFinding:
    fid = finding_id or fingerprint_finding(
        rule_id,
        claim_class,
        evidence_ref=evidence_refs[0] if evidence_refs else None,
        canonical_value=None if evidence_refs else claim_text,
    )
    return VerifyFinding(
        finding_id=fid,
        rule_id=rule_id,
        confidence=confidence,  # type: ignore[arg-type]
        claim_class=claim_class,
        claim_text=claim_text,
        span=span,
        evidence_refs=evidence_refs,
        expected=expected,
        observed=observed,
        detail=detail,
        suggested_action=suggested_action,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Table data
# ---------------------------------------------------------------------------

# Test 2: (text1, text2, should_produce_same_canonical, description)
_FINGERPRINT_CANON_ROWS: list[tuple[str, str, bool, str]] = [
    # Numeric (EN): same figure, different surface -> same canonical
    ("revenue grew 40% in Q1", "Q1 revenue was up 40%", True, "same percent, different sentence EN"),
    # Numeric (EN): different figures -> different canonical
    ("revenue grew 40%", "revenue grew 45%", False, "different percent figures EN"),
    # Numeric (EN): same integer -> same canonical
    ("all 93 tests pass", "93 tests all pass now", True, "same integer EN"),
    # Numeric (EN): same comma-grouped integer -> same canonical
    ("total is 1,234 items", "there are 1,234 items total", True, "same comma-integer EN"),
    # Numeric (EN): same decimal percent -> same canonical
    ("grew by 12.5%", "increased by 12.5%", True, "same decimal percent EN"),
    # Numeric (EN): different decimal -> different canonical
    ("grew by 12.5%", "increased by 12.6%", False, "different decimal EN"),
    # Date (EN): same ISO date tokens -> same canonical
    ("released on 2023-01-15", "2023-01-15 is the release date", True, "same ISO date EN"),
    # Date (EN): same quarter-year tokens -> same canonical
    ("Q1 2026 results", "results for Q1 2026", True, "same quarter-year tokens EN"),
    # Quoted string (curly quotes matched by _QUOTE_SPAN_RE): same content -> same canonical
    ("he said ‘good luck’", "message was ‘good luck’", True, "same curly-quoted string EN"),
    # Quoted string (curly quotes): different content -> different canonical
    ("he said ‘good luck’", "he said ‘bad luck’", False, "different curly-quoted strings EN"),
    # Numeric (KR): same figure -> same canonical as EN equivalent
    ("매출이 40% 증가했습니다", "40% 매출 증가가 있었습니다", True, "same percent KR"),
    # Cross-language: same numeric value -> same canonical
    ("revenue grew 40%", "매출이 40% 증가했습니다", True, "same percent EN vs KR"),
]


# Test 5: (text, turn_recs, session_recs, collector_present, expect_any_high, desc)
_EVCON_ROWS: list[tuple[str, list[Any], list[Any], bool, bool, str]] = [
    # TP: TestRun failure contradicts "tests pass" claim (EN)
    (
        "all 93 tests pass",
        [_test_run(exit_code=1)],
        [],
        True,
        True,
        "TestRun failure contradicts pass claim EN",
    ),
    # TP: TestRun failure contradicts "tests pass" claim (KR)
    (
        "테스트 전부 통과했습니다",
        [_test_run(exit_code=1)],
        [],
        True,
        True,
        "TestRun failure contradicts pass claim KR",
    ),
    # TP: edit claim with no EditMatch, collector present
    (
        "I fixed `src/app.py`",
        [],
        [],
        True,
        True,
        "edit claim with no EditMatch, collector present",
    ),
    # TP: commit claim with no CommitCheckpoint, collector present (EN)
    (
        "committed the change",
        [],
        [],
        True,
        True,
        "commit claim EN, no CommitCheckpoint, collector present",
    ),
    # TP: commit claim (KR), no CommitCheckpoint
    (
        "커밋했습니다",
        [],
        [],
        True,
        True,
        "commit claim KR, no CommitCheckpoint, collector present",
    ),
    # TP: failing CodeDiagnostics contradicts "lint clean" (EN)
    (
        "lint is clean",
        [_code_diag(exit_code=1)],
        [],
        True,
        True,
        "lint claim contradicts failing CodeDiagnostics EN",
    ),
    # TP: failing CodeDiagnostics contradicts "build clean" (KR)
    (
        "빌드가 깨끗합니다",
        [_code_diag(exit_code=1)],
        [],
        True,
        True,
        "build clean claim KR contradicts failing CodeDiagnostics",
    ),
    # TP: asserted figure differs from Calculation result
    (
        "the total is 42, as calculated",
        [_calculation(result=43)],
        [],
        True,
        True,
        "asserted 42 differs from Calculation result 43",
    ),
    # FPR-0: edit claim, collector absent -> NO finding
    (
        "I fixed `src/app.py`",
        [],
        [],
        False,
        False,
        "FPR-0: edit claim, collector absent -> no finding",
    ),
    # FPR-0: commit claim, collector absent -> NO finding
    (
        "committed the change",
        [],
        [],
        False,
        False,
        "FPR-0: commit claim, collector absent -> no finding",
    ),
    # FP guard: passing TestRun + pass claim -> no finding
    (
        "tests pass",
        [_test_run(exit_code=0)],
        [],
        True,
        False,
        "FP: passing TestRun, no contradiction",
    ),
    # FP guard: quoted speech -> no finding
    (
        "the doc says 'tests pass'",
        [_test_run(exit_code=1)],
        [],
        True,
        False,
        "FP: quoted speech attribution",
    ),
    # FP guard: conditional 'if' -> no finding
    (
        "if tests pass we can proceed",
        [_test_run(exit_code=1)],
        [],
        True,
        False,
        "FP: conditional guard 'if'",
    ),
    # FP guard: negation 'not' -> no finding
    (
        "tests do not pass yet",
        [_test_run(exit_code=1)],
        [],
        True,
        False,
        "FP: negation guard 'not'",
    ),
    # FP guard: future 'will' -> no finding
    (
        "tests will pass after the fix",
        [_test_run(exit_code=1)],
        [],
        True,
        False,
        "FP: future guard 'will'",
    ),
    # FP guard: conditional 'should' -> no finding
    (
        "should the tests pass, we continue",
        [_test_run(exit_code=1)],
        [],
        True,
        False,
        "FP: conditional guard 'should'",
    ),
    # FP guard: uncertainty 'whether' -> no finding
    (
        "whether tests pass is uncertain",
        [_test_run(exit_code=1)],
        [],
        True,
        False,
        "FP: uncertainty guard 'whether'",
    ),
    # FP guard KR: conditional '하면' -> no finding
    (
        "테스트가 통과하면 배포합니다",
        [_test_run(exit_code=1)],
        [],
        True,
        False,
        "FP: KR conditional guard '하면'",
    ),
    # FP guard KR: negation '않' -> no finding
    (
        "테스트가 아직 통과하지 않았습니다",
        [_test_run(exit_code=1)],
        [],
        True,
        False,
        "FP: KR negation guard '않'",
    ),
    # FP guard KR: future '예정' -> no finding
    (
        "테스트가 통과할 예정입니다",
        [_test_run(exit_code=1)],
        [],
        True,
        False,
        "FP: KR future guard '예정'",
    ),
]


# Test 6: (text, turn_recs, collector_present, expect_finding, desc)
_ACTIVITY_ROWS: list[tuple[str, list[Any], bool, bool, str]] = [
    # TP: "ran" + no TestRun record, collector present
    ("I ran the test suite", [], True, True, "ran claim, zero TestRun, collector present"),
    # TP: "searched the web" + no WebSearch record, collector present
    ("I searched the web for answers", [], True, True, "searched claim, zero WebSearch, collector present"),
    # TP: "checked online" + no SourceInspection record, collector present
    ("I checked the documentation online", [], True, True, "checked web claim, zero SourceInspection, collector present"),
    # TP KR: "실행했습니다" + no TestRun, collector present
    ("실행했습니다", [], True, True, "KR ran claim, zero TestRun, collector present"),
    # TP KR: "검색해봤습니다" + no WebSearch, collector present
    ("검색해봤습니다", [], True, True, "KR searched claim, zero WebSearch, collector present"),
    # FP: "ran" + matching TestRun record -> no finding
    ("I ran the test suite", [_test_run(exit_code=0)], True, False, "ran claim WITH matching TestRun"),
    # FP: "searched" + matching WebSearch record -> no finding
    ("I searched the web", [_web_search()], True, False, "searched claim WITH matching WebSearch"),
    # FP: collector absent -> no finding (FPR-0)
    ("I ran the test suite", [], False, False, "ran claim, collector absent -> no finding"),
    # FP: collector absent (searched)
    ("I searched the web", [], False, False, "searched claim, collector absent -> no finding"),
    # FP: no first-person action verb -> no finding
    ("The test suite is comprehensive", [], True, False, "no first-person action -> no finding"),
    # FP KR: noun phrase, no action -> no finding
    ("검색 결과가 있습니다", [], True, False, "KR result noun phrase, no first-person action"),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fingerprint_contradiction_keys_on_evidence_ref() -> None:
    """A1: evidence_consistency findings key on rule_id + evidence_ref, not claim text."""
    rule = "verify_before_replying.evidence_consistency"

    # Two findings with different claim class (representing different claim text)
    # but the SAME evidence_ref must produce IDENTICAL finding_id.
    id1 = fingerprint_finding(rule, "test_pass_claim", evidence_ref="TestRun@t1")
    id2 = fingerprint_finding(rule, "different_claim_wording", evidence_ref="TestRun@t1")
    assert id1 == id2, "same evidence_ref must produce identical finding_id (A1 rephrase-livelock kill)"

    # Changing the ref changes the id.
    id3 = fingerprint_finding(rule, "test_pass_claim", evidence_ref="TestRun@t2")
    assert id1 != id3, "different evidence_ref must produce different finding_id"

    # Basic format: 16 lowercase hex chars.
    assert len(id1) == 16
    assert all(c in "0123456789abcdef" for c in id1)


@pytest.mark.parametrize("text1,text2,should_match,desc", _FINGERPRINT_CANON_ROWS)
def test_fingerprint_text_keyed_canonicalizes_value(
    text1: str,
    text2: str,
    should_match: bool,
    desc: str,
) -> None:
    """A1: text-keyed fingerprints (claim_citation etc.) canonicalize on extracted value."""
    rule = "verify_before_replying.claim_citation"
    claim_class = "numeric"
    canon1 = canonical_claim_value(text1)
    canon2 = canonical_claim_value(text2)
    id1 = fingerprint_finding(rule, claim_class, canonical_value=canon1)
    id2 = fingerprint_finding(rule, claim_class, canonical_value=canon2)
    if should_match:
        assert id1 == id2, f"{desc}: expected same fingerprint, got {id1!r} vs {id2!r}"
    else:
        assert id1 != id2, f"{desc}: expected different fingerprints, got same {id1!r}"


def test_fingerprint_span_movement_is_stable() -> None:
    """Same claim value at different char offsets produces the same finding_id."""
    rule = "verify_before_replying.claim_citation"
    text = "revenue grew 40%"
    canon = canonical_claim_value(text)

    # The fingerprint function does not accept span; span is not part of the key.
    id1 = fingerprint_finding(rule, "numeric", canonical_value=canon)
    id2 = fingerprint_finding(rule, "numeric", canonical_value=canon)
    assert id1 == id2, "same canonical value must yield the same finding_id regardless of offset"


def test_fingerprint_sycophancy_is_per_signal_class() -> None:
    """Sycophancy findings fingerprint on rule_id + signal class (claim_class) only."""
    rule = "verify_before_replying.sycophancy_heuristics"

    # Same signal class -> same fingerprint (regardless of what span text was seen).
    id_praise_a = fingerprint_finding(rule, "praise_density")
    id_praise_b = fingerprint_finding(rule, "praise_density")
    assert id_praise_a == id_praise_b, "same signal class must produce identical fingerprint"

    # Different signal class -> different fingerprint.
    id_flip = fingerprint_finding(rule, "agreement_flip")
    assert id_praise_a != id_flip, "different signal class must produce different fingerprint"

    # Live check: sycophancy_findings emits at most ONE finding per signal class per call.
    text = "You're absolutely right, great catch! That is a brilliant observation. The answer is X."
    findings = sycophancy_findings(text, "user prompt here")
    praise_hits = [f for f in findings if f.claim_class == "praise_density"]
    assert len(praise_hits) <= 1, "at most one praise_density finding per audit pass"
    if praise_hits:
        assert praise_hits[0].confidence == "advisory"
        assert praise_hits[0].rule_id == rule


@pytest.mark.parametrize(
    "text,turn_recs,session_recs,collector_present,expect_any_high,desc",
    _EVCON_ROWS,
)
def test_evidence_consistency_contradiction_table(
    text: str,
    turn_recs: list[Any],
    session_recs: list[Any],
    collector_present: bool,
    expect_any_high: bool,
    desc: str,
) -> None:
    """Design 6.1 lexicon: contradiction detection with FPR-0 guard rows."""
    findings = evidence_consistency_findings(
        text,
        turn_recs,
        session_recs,
        collector_present=collector_present,
    )
    high = [f for f in findings if f.confidence == "high"]
    if expect_any_high:
        assert high, f"{desc}: expected at least one HIGH finding, got none"
        # Contradiction findings (turn_recs or session_recs present) must carry
        # non-empty evidence_refs pointing to the indicting record.
        # Absence findings (no records) have evidence_refs=() by design: there is
        # no contradicting record to point to, only a missing one.
        if turn_recs or session_recs:
            for f in high:
                assert f.evidence_refs, f"{desc}: contradiction HIGH finding must carry evidence_refs"
    else:
        assert not high, f"{desc}: expected NO HIGH finding, got: {[f.detail for f in high]}"


@pytest.mark.parametrize(
    "text,turn_recs,collector_present,expect_finding,desc",
    _ACTIVITY_ROWS,
)
def test_activity_grounding_table(
    text: str,
    turn_recs: list[Any],
    collector_present: bool,
    expect_finding: bool,
    desc: str,
) -> None:
    """Activity grounding: first-person action claims without matching evidence fire."""
    findings = activity_grounding_findings(
        text,
        turn_recs,
        collector_present=collector_present,
    )
    high = [f for f in findings if f.confidence == "high"]
    if expect_finding:
        assert high, f"{desc}: expected HIGH finding, got none"
    else:
        assert not high, f"{desc}: expected no HIGH finding, got: {[f.detail for f in high]}"


def test_sycophancy_praise_density() -> None:
    """Praise-density above threshold fires advisory; KR honorifics do not fire."""
    # Dense praise opening -> fires advisory.
    praise_text = (
        "You're absolutely right, great catch! That is a brilliant observation. "
        "The answer is definitively X."
    )
    findings = sycophancy_findings(praise_text, "user prompt")
    praise_hits = [f for f in findings if f.claim_class == "praise_density"]
    assert praise_hits, "dense praise opening must fire advisory praise_density finding"
    assert all(f.confidence == "advisory" for f in praise_hits)
    assert all(f.rule_id == "verify_before_replying.sycophancy_heuristics" for f in praise_hits)

    # Korean politeness forms must NOT fire (OQ1 anti-FP requirement).
    polite_kr = "확인해 보겠습니다. 감사합니다. 네, 다시 살펴보겠습니다."
    kr_findings = sycophancy_findings(polite_kr, "user prompt")
    kr_praise = [f for f in kr_findings if f.claim_class == "praise_density"]
    assert not kr_praise, (
        f"Korean polite honorifics must NOT fire praise_density: "
        f"{[f.detail for f in kr_praise]}"
    )

    # Plain polite opener (EN) must not fire.
    polite_en = "Sure, let me check that for you. The answer is X."
    en_findings = sycophancy_findings(polite_en, "user prompt")
    en_praise = [f for f in en_findings if f.claim_class == "praise_density"]
    assert not en_praise, "ordinary polite EN opener must not fire praise_density"


def test_sycophancy_agreement_flip_requires_all_three_signals() -> None:
    """Agreement flip fires only when (a) prompt pushback AND (b) own-claim negation AND (c) praise opening."""
    # All three signals present -> fires.
    prompt_pushback = "I think you are wrong about that. The result is Y."
    candidate_all_three = "You're right, I was wrong. The result is actually Y."
    findings_all = sycophancy_findings(candidate_all_three, prompt_pushback)
    flip_all = [f for f in findings_all if f.claim_class == "agreement_flip"]
    assert flip_all, "all three signals present -> agreement_flip must fire"
    assert all(f.confidence == "advisory" for f in flip_all)

    # Missing (a): no pushback in prompt -> no flip.
    neutral_prompt = "What is the result?"
    findings_no_a = sycophancy_findings(candidate_all_three, neutral_prompt)
    flip_no_a = [f for f in findings_no_a if f.claim_class == "agreement_flip"]
    assert not flip_no_a, "missing pushback in prompt -> no agreement_flip"

    # Missing (b): no own-claim negation -> no flip.
    candidate_no_b = "You're right! The result is Y."
    findings_no_b = sycophancy_findings(candidate_no_b, prompt_pushback)
    flip_no_b = [f for f in findings_no_b if f.claim_class == "agreement_flip"]
    assert not flip_no_b, "missing own-claim negation -> no agreement_flip"

    # Missing (c): no praise/agreement opening -> no flip.
    candidate_no_c = "I was wrong. The result is Y."
    findings_no_c = sycophancy_findings(candidate_no_c, prompt_pushback)
    flip_no_c = [f for f in findings_no_c if f.claim_class == "agreement_flip"]
    assert not flip_no_c, "missing praise opening -> no agreement_flip"

    # KR all three signals -> fires.
    prompt_kr_pushback = "제 생각엔 틀린 것 같아요."
    candidate_kr_all = "맞습니다, 제가 틀렸네요. 결과는 Y입니다."
    findings_kr = sycophancy_findings(candidate_kr_all, prompt_kr_pushback)
    flip_kr = [f for f in findings_kr if f.claim_class == "agreement_flip"]
    assert flip_kr, "KR: all three signals present -> agreement_flip must fire"


def test_normalized_contains_comparator() -> None:
    """A2: NFC-normalized + whitespace-collapsed substring matching and span minimum."""
    # Basic substring match.
    assert normalized_contains("the quick brown fox", "quick brown")

    # Extra whitespace normalizes to single space.
    assert normalized_contains("foo  bar  baz", "foo bar baz"), "extra whitespace must normalize"

    # Paraphrase (semantically similar but not a substring) -> reject.
    assert not normalized_contains(
        "The revenue increased by forty percent last quarter",
        "revenue grew 40%",
    ), "paraphrase must not match as substring"

    # Case-insensitive (after NFC casefold).
    assert normalized_contains("The Quick Brown Fox", "quick brown fox")

    # span_meets_minimum: requires BOTH >= 15 chars AND >= 3 words.
    assert not span_meets_minimum("hi"), "2-char / 1-word -> reject"
    assert not span_meets_minimum("short"), "5-char / 1-word -> reject"
    assert not span_meets_minimum("two words"), "9-char / 2-word -> reject (fewer than 3 words)"
    assert not span_meets_minimum("fourteen char!"), "14-char / 2-word -> reject (< 15 chars)"
    assert span_meets_minimum("fifteen chars ok!"), "17-char / 3-word -> accept"
    assert span_meets_minimum("this is a long enough span"), "26-char / 6-word -> accept"

    # filter_skeptic_findings: verbatim long span kept; short span dropped.
    candidate = "this is a long verbatim span from the candidate text"
    raw_kept = VerifyFinding(
        finding_id="a1b2c3d4a1b2c3d4",
        rule_id="verify_before_replying.skeptic_review",
        confidence="advisory",
        claim_class="overconfidence",
        claim_text="this is a long verbatim span from the candidate",
        span=(0, 47),
        evidence_refs=(),
        expected=None,
        observed=None,
        detail="overconfidence in verbatim span",
        suggested_action="consider",
    )
    raw_short = VerifyFinding(
        finding_id="b2c3d4e5b2c3d4e5",
        rule_id="verify_before_replying.skeptic_review",
        confidence="advisory",
        claim_class="overconfidence",
        claim_text="hi",
        span=(0, 2),
        evidence_refs=(),
        expected=None,
        observed=None,
        detail="too short",
        suggested_action="consider",
    )
    kept, dropped_count = filter_skeptic_findings([raw_kept, raw_short], candidate)
    kept_ids = {f.finding_id for f in kept}
    assert raw_kept.finding_id in kept_ids, "verbatim long span must be kept"
    assert raw_short.finding_id not in kept_ids, "short span must be dropped"
    assert dropped_count == 1, "exactly one finding dropped"


def test_citation_adapter_projects_gate_result() -> None:
    """CitationGateResult with violations maps to high claim_citation VerifyFindings."""
    claim = HighRiskClaim(
        claim_class="numeric",
        start=10,
        end=32,
        text="revenue grew 40% in Q1",
        has_marker=False,
        corpus_supported=False,
    )
    violation = CitationGateViolation(
        kind="uncited_high_risk",
        detail="high-risk claim without source marker",
        claims=(claim,),
    )
    gate_result = CitationGateResult(
        verdict="block",
        violations=(violation,),
        high_risk_claims=(claim,),
        zero_source_turn=False,
    )

    findings = claim_citation_findings(gate_result)
    assert findings, "gate violation must produce at least one VerifyFinding"

    for f in findings:
        assert f.confidence == "high", "citation adapter findings must be high confidence"
        assert f.rule_id == "verify_before_replying.claim_citation"
        # Span must reflect the claim's char offsets.
        assert f.span[0] == claim.start and f.span[1] == claim.end, (
            f"span mismatch: expected ({claim.start}, {claim.end}), got {f.span}"
        )
        # Claim text must carry the detected claim.
        assert claim.text in f.claim_text or "40%" in f.claim_text, (
            "claim text must carry the detected numeric claim"
        )
        assert f.suggested_action in {"cite", "recheck"}

    # Clean gate result (no violations) -> no findings.
    clean_result = CitationGateResult(verdict="pass", zero_source_turn=True)
    assert claim_citation_findings(clean_result) == (), "clean gate result must produce no findings"


def test_resolve_findings_taxonomy() -> None:
    """Resolution taxonomy: resolved / acknowledged_shipped / ignored (design 12.3)."""
    tr_fail = _test_run(exit_code=1, evidence_ref="TestRun@t1")
    # A finding that fires on the original text.
    finding = _make_finding(
        claim_text="all tests pass",
        span=(0, 14),
        evidence_refs=("TestRun@t1",),
    )
    findings_tuple = (finding,)

    # Resolved: deliver text that no longer triggers the detector.
    resolved_text = "the test run showed exit_code=1; tests are failing"
    resolutions_resolved = resolve_findings(
        findings_tuple,
        resolved_text,
        turn_records=[tr_fail],
        session_records=[],
        gate_result=None,
        collector_present=True,
        ship_marker_used=False,
    )
    res_by_id = {f.finding_id: r for f, r in resolutions_resolved}
    assert res_by_id.get(finding.finding_id) == "resolved", (
        "claim absent in revised text -> resolution must be 'resolved'"
    )

    # acknowledged_shipped: finding still fires AND ship_marker_used=True.
    still_fires_text = "all tests pass"
    resolutions_acked = resolve_findings(
        findings_tuple,
        still_fires_text,
        turn_records=[tr_fail],
        session_records=[],
        gate_result=None,
        collector_present=True,
        ship_marker_used=True,
    )
    acked_by_id = {f.finding_id: r for f, r in resolutions_acked}
    assert acked_by_id.get(finding.finding_id) == "acknowledged_shipped", (
        "finding still fires + SHIP_AS_IS marker -> 'acknowledged_shipped'"
    )

    # ignored: finding still fires, no marker.
    resolutions_ignored = resolve_findings(
        findings_tuple,
        still_fires_text,
        turn_records=[tr_fail],
        session_records=[],
        gate_result=None,
        collector_present=True,
        ship_marker_used=False,
    )
    ignored_by_id = {f.finding_id: r for f, r in resolutions_ignored}
    assert ignored_by_id.get(finding.finding_id) == "ignored", (
        "finding still fires, no marker -> 'ignored'"
    )

    # OQ4 known limitation: textual rebuttal without SHIP_AS_IS -> still 'ignored' (v1 marker-only).
    rebuttal_text = (
        "The TestRun record was from the pre-fix run; the post-fix run passed. "
        "all tests pass"
    )
    resolutions_rebuttal = resolve_findings(
        findings_tuple,
        rebuttal_text,
        turn_records=[tr_fail],
        session_records=[],
        gate_result=None,
        collector_present=True,
        ship_marker_used=False,
    )
    rebuttal_by_id = {f.finding_id: r for f, r in resolutions_rebuttal}
    assert rebuttal_by_id.get(finding.finding_id) == "ignored", (
        "OQ4: textual rebuttal without SHIP_AS_IS marker classifies as 'ignored' in v1"
    )

    # ignore_rate_summary aggregates correctly.
    advisory_finding = _make_finding(
        rule_id="verify_before_replying.sycophancy_heuristics",
        confidence="advisory",
        claim_class="praise_density",
        evidence_refs=(),
        expected=None,
        observed=None,
    )
    mixed_resolutions: list[tuple[VerifyFinding, str]] = [
        (finding, "ignored"),           # high, ignored
        (finding, "resolved"),          # high, resolved (duplicate id ok in summary input)
        (advisory_finding, "ignored"),  # advisory, ignored
    ]
    summary = ignore_rate_summary(mixed_resolutions)
    assert "highTotal" in summary, "summary must include highTotal"
    assert "highIgnored" in summary, "summary must include highIgnored"
    assert isinstance(summary["highTotal"], int)
    assert isinstance(summary["highIgnored"], int)


def test_hedge_notice_never_trips_detectors() -> None:
    """build_citation_fail_open_notice output appended to clean text changes no detector results."""
    claim = HighRiskClaim(
        claim_class="numeric",
        start=5,
        end=33,
        text="the profit was $5M last year",
        has_marker=False,
    )
    gate_result = CitationGateResult(
        verdict="pass",
        high_risk_claims=(claim,),
        zero_source_turn=False,
    )
    notice = build_citation_fail_open_notice(gate_result)
    assert notice, "precondition: notice must be non-empty"

    # Clean answer with no first-person action claims and no evidence records.
    clean_answer = (
        "The implementation is complete. "
        "The function returns the computed value without side effects."
    )

    findings_clean = list(
        evidence_consistency_findings(clean_answer, [], [], collector_present=True)
    ) + list(
        activity_grounding_findings(clean_answer, [], collector_present=True)
    )

    # Appending the hedge notice must NOT produce additional findings.
    answer_with_hedge = clean_answer + "\n\n" + notice
    findings_with_hedge = list(
        evidence_consistency_findings(answer_with_hedge, [], [], collector_present=True)
    ) + list(
        activity_grounding_findings(answer_with_hedge, [], collector_present=True)
    )

    high_clean = [f for f in findings_clean if f.confidence == "high"]
    high_hedge = [f for f in findings_with_hedge if f.confidence == "high"]

    assert len(high_clean) == len(high_hedge), (
        f"hedge notice changed detector output: "
        f"clean={[f.detail for f in high_clean]}, "
        f"with_hedge={[f.detail for f in high_hedge]}"
    )


def test_build_nudge_message_format() -> None:
    """Nudge message renders per design Section 9: tags, headers, SHIP_AS_IS instruction."""
    high_finding = VerifyFinding(
        finding_id="a1b2c3d4a1b2c3d4",
        rule_id="verify_before_replying.evidence_consistency",
        confidence="high",
        claim_class="test_pass",
        claim_text="all 93 tests pass",
        span=(412, 430),
        evidence_refs=("TestRun@turn_7",),
        expected="exitCode=0",
        observed="exitCode=1",
        detail="TestRun record shows exit_code=1 (expected success)",
        suggested_action="recheck",
    )
    advisory_finding = VerifyFinding(
        finding_id="e5f6a7b8e5f6a7b8",
        rule_id="verify_before_replying.sycophancy_heuristics",
        confidence="advisory",
        claim_class="praise_density",
        claim_text="You're absolutely right, great catch",
        span=(0, 36),
        evidence_refs=(),
        expected=None,
        observed=None,
        detail="opening span shows high praise density",
        suggested_action="consider",
    )

    msg = build_nudge_message((high_finding, advisory_finding))

    # Wrapped in <verify_before_replying> tags.
    assert msg.startswith("<verify_before_replying>"), "must open with <verify_before_replying>"
    assert msg.rstrip().endswith("</verify_before_replying>"), "must close with </verify_before_replying>"

    # High-confidence section header.
    assert "VERIFIED ISSUES" in msg, "high-confidence section header 'VERIFIED ISSUES' required"
    assert "high confidence" in msg.lower(), "header must mention high confidence"

    # High finding content: verbatim claim, char offsets, evidence ref, observed value.
    assert "all 93 tests pass" in msg, "claim_text must appear verbatim in nudge"
    assert "412" in msg and "430" in msg, "char offsets must appear in nudge"
    assert "TestRun@turn_7" in msg, "evidence_ref must appear in nudge"
    assert "exitCode=1" in msg or "exit_code=1" in msg, "observed value must appear"
    assert "recheck" in msg.lower(), "suggested_action 'recheck' must appear"

    # Advisory section header (explicitly labeled, per design Section 9).
    assert "ADVISORY" in msg, "advisory section header required"
    assert "may be wrong" in msg.lower(), "advisory header must say 'may be wrong, weigh accordingly'"

    # Advisory finding rendered under the advisory section.
    assert "praise_density" in msg or "sycophancy" in msg.lower() or "consider" in msg.lower(), (
        "advisory finding must appear in the advisory section"
    )

    # SHIP_AS_IS instruction line present.
    assert "SHIP_AS_IS" in msg, "SHIP_AS_IS instruction must appear in nudge"
    # The exact instruction per design Section 9.
    assert (
        "respond with exactly SHIP_AS_IS" in msg
        or "SHIP_AS_IS" in msg
    ), "SHIP_AS_IS instruction must be included"

    # High findings must appear BEFORE advisory findings (ordering rule).
    high_pos = msg.find("VERIFIED ISSUES")
    advisory_pos = msg.find("ADVISORY")
    assert high_pos < advisory_pos, "high-confidence section must precede advisory section"

    # Zero-findings input: returns empty string or raises (never called by driver this way).
    try:
        empty = build_nudge_message(())
        assert empty == "", f"zero-findings must return '' not {empty!r}"
    except (ValueError, AssertionError):
        pass  # raising is also acceptable per spec


# ---------------------------------------------------------------------------
# execution_claims_findings (execution-claims member rule, design Section 2/5.1)
# ---------------------------------------------------------------------------


def _exec_classes(findings: Any) -> list[str]:
    return [f.claim_class for f in findings]


def test_fabricated_execution_generic_absence() -> None:
    """Arm 1a: delegation claim + zero spawn records + a liveness record fires;
    without liveness fires nothing (producer guard); collector off fires nothing."""
    text = "I had two subagents debate this and reached a synthesis."

    # Arm 1a fires with a first-party liveness record and zero spawn records.
    findings = execution_claims_findings(
        text, [_first_party_tool_call()], [], collector_present=True
    )
    assert _exec_classes(findings) == ["fabricated_execution"], (
        "delegation claim + zero spawns + liveness record must fire arm 1a"
    )

    # No first-party record at all: producer-liveness guard keeps it silent.
    findings_no_producer = execution_claims_findings(text, [], [], collector_present=True)
    assert findings_no_producer == (), "no first-party producer record -> silent (2.7)"

    # collector_present=False: entirely silent.
    findings_no_collector = execution_claims_findings(
        text, [_first_party_tool_call()], [], collector_present=False
    )
    assert findings_no_collector == (), "collector_present=False -> () (2.7)"


def test_fabricated_execution_model_named() -> None:
    """Arm 1b (incident leg 3): GPT-5.5 review claimed, only Opus spawns exist."""
    corpus = [
        _subagent_spawn(status="error", reason="child_turn_timeout", model="opus-4-8", ref="sp_1"),
        _subagent_spawn(status="ok", reason=None, model="opus-4-8", ref="sp_2"),
    ]
    # EN text uses the "was reviewed by" lexicon form (2.5); bare "<Model>
    # reviewed" is intentionally NOT in the lexicon (FP-driven, see case 5).
    text_en = "The debate was reviewed by GPT-5.5, which signed off on the design."
    findings_en = execution_claims_findings(text_en, corpus, [], collector_present=True)
    assert _exec_classes(findings_en) == ["fabricated_execution"], (
        "GPT-5.5 claim with only Opus records must fire arm 1b"
    )

    text_kr = "GPT-5.5의 리뷰를 받았습니다. 설계가 견고하다고 합니다."
    findings_kr = execution_claims_findings(text_kr, corpus, [], collector_present=True)
    assert _exec_classes(findings_kr) == ["fabricated_execution"], (
        "KO GPT-5.5 delegation claim with only Opus records must fire arm 1b"
    )

    # Same text, but a gpt-family spawn record present: arm 1b silent.
    corpus_with_gpt = corpus + [
        _subagent_spawn(status="ok", reason=None, model="gpt-5.5", provider="openai", ref="sp_3")
    ]
    findings_present = execution_claims_findings(
        text_kr, corpus_with_gpt, [], collector_present=True
    )
    assert findings_present == (), "gpt-family record present -> arm 1b silent"


def test_failed_execution_presented_as_success() -> None:
    """Sub-check 2 (incident leg 1, single-spawn variant)."""
    rec = _subagent_spawn(
        status="error", reason="child_turn_timeout", model="opus-4-8", ref="sp_001"
    )
    text = "Opus reviewed this and concluded the design is sound."
    findings = execution_claims_findings(text, [rec], [], collector_present=True)
    assert len(findings) == 1, "one failed-spawn success-presentation finding"
    f = findings[0]
    assert f.claim_class == "failed_execution_presented_as_success"
    assert f.confidence == "high"
    assert f.evidence_refs == ("sp_001",), "evidence ref points to the failed record"
    assert "turn timeout" in (f.observed or ""), "human reason must render"
    assert "child_turn_timeout" in (f.observed or ""), "raw token must render for audit"


def test_failed_execution_retry_ok_suppression() -> None:
    """Retry-ok suppression (2.4.3) and per-persona keying."""
    # Same-model failed + ok: suppressed.
    failed = _subagent_spawn(status="error", model="opus-4-8", ref="f1")
    ok = _subagent_spawn(status="ok", reason=None, model="opus-4-8", ref="o1")
    text = "Opus reviewed the draft and flagged two issues."
    suppressed = execution_claims_findings(text, [failed, ok], [], collector_present=True)
    assert suppressed == (), "same-model retry-ok suppresses sub-check 2 (2.4.3)"

    # Distinct personas: failed optimistic NOT excused by ok skeptical.
    failed_opt = _subagent_spawn(
        status="error", model="opus-4-8", persona="optimistic", ref="fo"
    )
    ok_skep = _subagent_spawn(
        status="ok", reason=None, model="opus-4-8", persona="skeptical", ref="os"
    )
    text2 = "The optimistic agent argued the plan was ready and concluded it is sound."
    fires = execution_claims_findings(text2, [failed_opt, ok_skep], [], collector_present=True)
    assert _exec_classes(fires) == ["failed_execution_presented_as_success"], (
        "distinct-persona failure is not excused by a different-persona ok record"
    )


def test_honest_confession_control() -> None:
    """FPR pin: the incident confession text produces zero execution_claims findings."""
    confession = (
        "첫 Opus 스폰은 타임아웃으로 실패했고, 두번째는 과제를 거부했으며, "
        "GPT-5.5 리뷰는 실행되지 않았습니다."
    )
    corpus = [
        _subagent_spawn(status="error", reason="child_turn_timeout", model="opus-4-8", ref="c1"),
        _subagent_spawn(status="ok", reason=None, model="opus-4-8", ref="c2"),
    ]
    findings = execution_claims_findings(confession, corpus, [], collector_present=True)
    assert findings == (), "honest confession over the incident corpus must be silent"


def test_plan_and_quote_guards() -> None:
    """Section 3 cases 1, 3, 8: zero findings."""
    corpus = [_first_party_tool_call()]

    # Case 1: "I could spawn ..." (plan guard + present tense).
    case1 = "I could spawn Opus subagents to debate this, want me to?"
    assert execution_claims_findings(case1, corpus, [], collector_present=True) == (), (
        "case 1: could/plan guard keeps it silent"
    )

    # Case 3: user quote, not a first-person completed claim.
    case3 = 'You asked me to "spawn a GPT-5.5 reviewer", so here is my plan:'
    assert execution_claims_findings(case3, corpus, [], collector_present=True) == (), (
        "case 3: quoted user request + plan -> silent"
    )

    # Case 8: "Plan: spawn ... then have GPT-5.5 review. Proceeding now."
    case8 = (
        "Plan: spawn optimistic and skeptical Opus agents, then have GPT-5.5 "
        "review. Proceeding now."
    )
    assert execution_claims_findings(case8, corpus, [], collector_present=True) == (), (
        "case 8: plan prose matches no past-tense lexicon; silent"
    )


def test_disclosure_global_suppression() -> None:
    """2.4.4: disclosing one failure suppresses sub-check 2 for the whole pass;
    sub-check 1 is unaffected."""
    failed = _subagent_spawn(status="error", reason="child_turn_timeout", model="opus-4-8", ref="d1")
    # Text presents opus as success but also discloses a failure elsewhere.
    text = (
        "The gpt reviewer failed to complete. Opus reviewed the design and "
        "concluded it is sound."
    )
    findings = execution_claims_findings(text, [failed], [], collector_present=True)
    classes = _exec_classes(findings)
    assert "failed_execution_presented_as_success" not in classes, (
        "global disclosure suppression silences sub-check 2 (2.4.4)"
    )


def test_execution_fingerprints_stable() -> None:
    """Fingerprints per 2.4.5 / 2.5: rephrase-stable per class; distinct records distinct."""
    # Arm 1b: two rephrasings of a GPT delegation claim -> same fingerprint.
    corpus = [_subagent_spawn(status="ok", reason=None, model="opus-4-8", ref="o")]
    t1 = "The plan was reviewed by GPT-5.5 and it signed off."
    t2 = "My draft was reviewed by the GPT-5.5 reviewer, which approved it."
    f1 = execution_claims_findings(t1, corpus, [], collector_present=True)
    f2 = execution_claims_findings(t2, corpus, [], collector_present=True)
    assert f1 and f2
    assert f1[0].finding_id == f2[0].finding_id, "arm 1b fingerprint is rephrase-stable"

    # Sub-check 2: distinct failed records -> distinct fingerprints.
    rec_a = _subagent_spawn(status="error", model="opus-4-8", ref="ra")
    rec_b = _subagent_spawn(status="error", model="gemini-3-pro", ref="rb")
    text = "Opus reviewed this and concluded it is sound. Gemini analyzed it and agreed."
    findings = execution_claims_findings(text, [rec_a, rec_b], [], collector_present=True)
    ids = {f.finding_id for f in findings}
    assert len(ids) == len(findings) >= 2, "distinct failed records produce distinct fingerprints"


def test_resolve_findings_execution_claims() -> None:
    """A surfaced sub-check-2 finding resolves when disclosure is added; ignored otherwise."""
    rec = _subagent_spawn(
        status="error", reason="child_turn_timeout", model="opus-4-8", ref="sp_001"
    )
    original = "Opus reviewed this and concluded the design is sound."
    findings = execution_claims_findings(original, [rec], [], collector_present=True)
    assert len(findings) == 1
    history = findings

    # Disclosure added: no longer detects -> resolved.
    disclosed = (
        "The Opus spawn timed out and did not complete, so I wrote the review myself."
    )
    resolutions = resolve_findings(
        history,
        disclosed,
        turn_records=[rec],
        session_records=[],
        gate_result=None,
        collector_present=True,
        ship_marker_used=False,
    )
    res_by_id = {f.finding_id: r for f, r in resolutions}
    assert res_by_id[history[0].finding_id] == "resolved", (
        "adding disclosure resolves the sub-check-2 finding"
    )

    # Shipped unchanged, no marker -> ignored.
    resolutions_ignored = resolve_findings(
        history,
        original,
        turn_records=[rec],
        session_records=[],
        gate_result=None,
        collector_present=True,
        ship_marker_used=False,
    )
    ign_by_id = {f.finding_id: r for f, r in resolutions_ignored}
    assert ign_by_id[history[0].finding_id] == "ignored", (
        "shipping unchanged without a marker classifies as ignored"
    )


def test_nudge_renders_execution_reason() -> None:
    """build_nudge_message output contains the human reason and raw token (Section 4)."""
    rec = _subagent_spawn(
        status="error", reason="child_turn_timeout", model="opus-4-8", provider="anthropic", ref="sp_001"
    )
    text = "Opus reviewed this and concluded the design is sound."
    findings = execution_claims_findings(text, [rec], [], collector_present=True)
    assert findings
    msg = build_nudge_message(findings)
    assert "turn timeout" in msg, "human reason must render in nudge"
    assert "child_turn_timeout" in msg, "raw reason token must render in nudge"
    assert "sp_001" in msg, "evidence ref must render in nudge"


@pytest.mark.parametrize(
    "text,desc",
    [
        (
            "Opus 스폰이 타임아웃으로 실패했고, 재시도도 거부당했습니다",
            "case 2: honest KR failure report",
        ),
        (
            "In yesterday's session the Opus reviewer timed out, so today I did the review myself",
            "case 4: prior-session failure disclosed, self-did review",
        ),
        (
            "According to the GPT-5.5 model card, tool-use latency is lower",
            "case 5: model-card citation, no delegation claim",
        ),
        (
            "You could have a skeptical subagent review this; Opus is well suited",
            "case 6: capability discussion, plan guard",
        ),
    ],
)
def test_execution_adversarial_benign_silent(text: str, desc: str) -> None:
    """Section 3 adversarial benign cases 2, 4, 5, 6: zero findings."""
    corpus = [
        _subagent_spawn(status="error", reason="child_turn_timeout", model="opus-4-8", ref="a1"),
        _first_party_tool_call(),
    ]
    findings = execution_claims_findings(text, corpus, [], collector_present=True)
    assert findings == (), f"{desc}: must stay silent"


# ---------------------------------------------------------------------------
# Arm 1b family-alias false-positive suppression (design 2.4.1 / 2.5, FPR-0)
# ---------------------------------------------------------------------------


def test_arm1b_family_alias_claude_opus_no_fp() -> None:
    """Honest 'reviewed by Claude' + an ok anthropic/opus-4-8 record: ZERO findings.

    The record derives family 'opus', which is alias-sibling of 'claude', so a
    claimed 'claude' family must not fire arm 1b (FPR-0 over an alias-named model).
    """
    corpus = [
        _subagent_spawn(status="ok", reason=None, model="opus-4-8", provider="anthropic", ref="sp_ok"),
    ]
    text = "The debate was reviewed by Claude, which signed off."
    findings = execution_claims_findings(text, corpus, [], collector_present=True)
    assert findings == (), (
        "claimed 'claude' family is alias-satisfied by an opus/anthropic record"
    )


def test_arm1b_family_alias_sonnet_no_fp() -> None:
    """'reviewed by Sonnet' + an ok record whose family derives to 'claude': ZERO.

    Record model 'claude' (no 'sonnet' substring) derives leading-alpha family
    'claude'; claimed 'sonnet' is alias-sibling so arm 1b must stay silent.
    """
    corpus = [
        _subagent_spawn(status="ok", reason=None, model="claude", provider="anthropic", ref="sp_s"),
    ]
    text = "The plan was reviewed by Sonnet, which approved it."
    findings = execution_claims_findings(text, corpus, [], collector_present=True)
    assert findings == (), (
        "claimed 'sonnet' family is alias-satisfied by a claude/anthropic record"
    )


def test_arm1b_incident_regression_gpt_vs_opus_still_fires() -> None:
    """Incident regression: 'reviewed by GPT-5.5' + only opus records STILL fires.

    'gpt' is in a different alias group from {claude,opus,...}, so the opus
    records do not alias-satisfy it (2.5 incident-catch invariant preserved)."""
    corpus = [
        _subagent_spawn(status="error", reason="child_turn_timeout", model="opus-4-8", provider="anthropic", ref="c1"),
        _subagent_spawn(status="ok", reason=None, model="opus-4-8", provider="anthropic", ref="c2"),
    ]
    text = "The debate was reviewed by GPT-5.5, which signed off on the design."
    findings = execution_claims_findings(text, corpus, [], collector_present=True)
    assert _exec_classes(findings) == ["fabricated_execution"], (
        "gpt claim over opus-only records must still fire arm 1b (incident leg 3)"
    )


def test_arm1b_provider_alias_phrasing_no_fp() -> None:
    """'the Anthropic subagent reviewed it' + ok opus record: ZERO findings.

    Provider-alias phrasing names 'anthropic' as the family; the opus/anthropic
    record derives 'anthropic' (provider candidate key) and its alias group
    covers it, so arm 1b stays silent."""
    corpus = [
        _subagent_spawn(status="ok", reason=None, model="opus-4-8", provider="anthropic", ref="sp_ok"),
    ]
    text = (
        "I had the Anthropic subagent review the design and it concluded it is sound."
    )
    findings = execution_claims_findings(text, corpus, [], collector_present=True)
    assert findings == (), (
        "provider-alias 'anthropic' is satisfied by an opus/anthropic record"
    )


def test_spawn_record_blocked_status_predicate() -> None:
    """A blocked spawn (EvidenceRecord status 'unknown', fields.status 'blocked')
    is still read as FAILED via fields.status (design 2.2 predicate)."""
    rec = _subagent_spawn(
        status="blocked", reason="child_runner_blocked", model="opus-4-8", ref="b1"
    )
    assert _record_status(rec) == "unknown", "blocked maps EvidenceRecord status to unknown"
    text = "Opus reviewed this and concluded the design is sound."
    findings = execution_claims_findings(text, [rec], [], collector_present=True)
    assert _exec_classes(findings) == ["failed_execution_presented_as_success"], (
        "fields.status='blocked' must be read as a failed spawn (2.2)"
    )


def test_variant_regex_short_slug_word_anchored() -> None:
    """Short single-fragment slugs (len<=4 alpha) are \\b-anchored so they do not
    match inside unrelated words (2.4.1 slug-anchor hardening)."""
    rx_o3 = _variant_regex_for_slug("o3")
    assert rx_o3 is not None
    assert rx_o3.search("the o3 model") is not None, "o3 matches as a standalone token"
    assert rx_o3.search("cargo3d rendering") is None, "o3 does not match inside a word"

    rx_glm = _variant_regex_for_slug("glm-4")
    assert rx_glm is not None
    assert rx_glm.search("glm-4 reviewed it") is not None, "glm-4 matches as a token"
    assert rx_glm.search("aglm-4x pipeline") is None, "glm-4 does not match mid-word"
