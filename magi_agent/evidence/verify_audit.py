"""Verify-before-replying: pure detector module (PR-V1).

Deterministic, evidence-bound quality audit of candidate final answers at the
pre-final boundary. No I/O, no driver import, no flags import. Returns
VerifyAuditResult to the caller (engine driver, PR-V3), which decides whether
to inject a nudge continuation.

Imports restricted to: stdlib, pydantic, evidence/citation_gate.py (private
symbols _sentence_numbers :227 and _QUOTE_SPAN_RE :111 with comment below),
evidence/types.py, and research/grounded_answer_guard.py (_NUMBER_RE).
Private cross-module imports follow the precedent in driver.py importing
_recipe_routing._DEV_CODING_EVIDENCE_VALIDATOR.

Style: no em-dashes anywhere in this module, per the citation feature rule.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

# Private cross-import: _sentence_numbers (citation_gate.py:227) and
# _QUOTE_SPAN_RE (citation_gate.py:111). These are the canonical number
# and quote-span extractors for this codebase; importing them here avoids
# forking the logic. Precedent: driver.py imports
# _recipe_routing._DEV_CODING_EVIDENCE_VALIDATOR.
from magi_agent.evidence.citation_gate import (  # type: ignore[attr-defined]
    CitationGateResult,
    _QUOTE_SPAN_RE,  # noqa: PLC2701
    _sentence_numbers,  # noqa: PLC2701
)
from magi_agent.research.grounded_answer_guard import _NUMBER_RE  # noqa: PLC2701


# ---------------------------------------------------------------------------
# Schema (design Section 8, verbatim fields)
# ---------------------------------------------------------------------------


class VerifyFinding(BaseModel):
    """One finding from the evidence-bound or skeptic auditor."""

    model_config = ConfigDict(frozen=True)

    finding_id: str
    rule_id: str
    confidence: Literal["high", "advisory"]
    claim_class: str
    claim_text: str
    span: tuple[int, int]
    evidence_refs: tuple[str, ...]
    expected: str | None
    observed: str | None
    detail: str
    suggested_action: Literal["cite", "recheck", "revise", "consider"]


class VerifyAuditResult(BaseModel):
    """Result of one audit pass (design Section 8)."""

    model_config = ConfigDict(frozen=True)

    findings: tuple[VerifyFinding, ...]
    new_findings: tuple[VerifyFinding, ...]
    high_count: int
    advisory_count: int
    corpus_record_count: int
    skeptic_ran: bool
    skeptic_findings_dropped: int


# ---------------------------------------------------------------------------
# Fingerprint (A1)
# ---------------------------------------------------------------------------


def fingerprint_finding(
    rule_id: str,
    claim_class: str,
    *,
    evidence_ref: str | None = None,
    canonical_value: str | None = None,
) -> str:
    """Return a 16-char lowercase hex fingerprint for finding deduplication.

    A1 keying rules (design Section 18, binding):
    - evidence_consistency findings: key on rule_id + evidence_ref (prevents
      rephrase-livelock -- changing only the claim wording never re-nudges).
    - Text-keyed findings (claim_citation, activity_grounding, absence):
      key on rule_id + canonical_value (the extracted numeric/date/quote VALUE).
    - sycophancy_heuristics / default: key on rule_id + claim_class (the
      signal class), so at most one finding per signal class per turn.
    """
    if evidence_ref is not None:
        key = f"{rule_id}|{evidence_ref}"
    elif canonical_value is not None:
        key = f"{rule_id}|{canonical_value}"
    else:
        key = f"{rule_id}|{claim_class}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def canonical_claim_value(text: str) -> str:
    """Extract the canonical value from a claim text for text-keyed fingerprinting.

    NFC-normalizes and casefolds the text, then extracts and sorts numeric
    tokens (via _NUMBER_RE, citation_gate.py:227 reuse) and curly/smart quoted
    spans (via _QUOTE_SPAN_RE, citation_gate.py:111 reuse). Returns a
    pipe-separated sorted string so paraphrases with the same numeric or
    quoted content produce the same fingerprint.
    """
    normalized = unicodedata.normalize("NFC", text).casefold()
    nums = sorted(m.group(0).strip() for m in _NUMBER_RE.finditer(normalized))
    quotes = sorted(m.group(1).strip() for m in _QUOTE_SPAN_RE.finditer(text))
    parts = nums + quotes
    return "|".join(parts)


# ---------------------------------------------------------------------------
# A2 comparator
# ---------------------------------------------------------------------------


def normalized_contains(haystack: str, needle: str) -> bool:
    """True when needle is a verbatim NFC-normalized, whitespace-collapsed
    substring of haystack. Case-insensitive (via casefold). Design A2."""
    h = " ".join(unicodedata.normalize("NFC", haystack).casefold().split())
    n = " ".join(unicodedata.normalize("NFC", needle).casefold().split())
    return n in h


def span_meets_minimum(span_text: str) -> bool:
    """True when span_text has >= 15 chars AND >= 3 whitespace-separated words.
    Design A2 post-filter minimum for skeptic spans."""
    return len(span_text) >= 15 and len(span_text.split()) >= 3


# ---------------------------------------------------------------------------
# Internal record-access helpers (duck-typed; no pydantic validation here)
# ---------------------------------------------------------------------------


def _record_type(record: Any) -> str:
    return str(getattr(record, "type", ""))


def _record_fields(record: Any) -> Mapping[str, Any]:
    f = getattr(record, "fields", {})
    return f if isinstance(f, Mapping) else {}


def _record_status(record: Any) -> str:
    return str(getattr(record, "status", ""))


def _record_ref(record: Any) -> str:
    """Best-effort evidence ref (mirrors criterion_engine._record_ref :169)."""
    fields = _record_fields(record)
    ref = fields.get("evidenceRef")
    if isinstance(ref, str) and ref:
        return ref
    r = getattr(record, "ref", "")
    if isinstance(r, str) and r:
        return r
    rtype = _record_type(record)
    obs = getattr(record, "observed_at", 0)
    return f"{rtype}@{obs}"


def _is_failing(record: Any) -> bool:
    """True when a record's status or exitCode indicates failure."""
    if _record_status(record) in ("failed", "error"):
        return True
    fields = _record_fields(record)
    exit_code = fields.get("exitCode")
    if exit_code is not None:
        try:
            return int(exit_code) != 0
        except (TypeError, ValueError):
            pass
    return False


# ---------------------------------------------------------------------------
# FP guard tokens (design D2 lexicon)
# ---------------------------------------------------------------------------

_EN_GUARD_RE = re.compile(
    r"\b(if|not|n't|will|shall|would|should|whether|"
    r"fail(?:ed|s|ing)?|"
    r"haven't|haven.t|didn't|didn.t|doesn't|doesn.t|"
    r"don't|don.t|isn't|isn.t|aren't|aren.t|wasn't|wasn.t|"
    r"won't|won.t|cannot|can't|can.t)\b",
    re.IGNORECASE,
)
# Korean FP guards: conditional/negation/future markers.
_KR_GUARD_RE = re.compile(r"않|하면|예정|못\s|아직|아닌|없는|실패")
# Attribution cue: "the doc says", "she said", "according to".
_ATTRIBUTION_CUE_RE = re.compile(
    r"\b(?:said|says|stated|states|wrote|writes|told|tells|according\s+to|"
    r"remarked|noted|declared|announced|the\s+doc(?:s)?\s+say(?:s)?)\b",
    re.IGNORECASE,
)
# Quote presence: ASCII apostrophe, ASCII double, or curly variants.
_HAS_QUOTE_RE = re.compile(r"""['"‘’“”]""")


def _has_fp_guard(sentence: str) -> bool:
    """Return True when the sentence contains a known FP guard token."""
    if _EN_GUARD_RE.search(sentence):
        return True
    if _KR_GUARD_RE.search(sentence):
        return True
    # Attribution cue + any quote marks -> quoted speech, skip.
    if _ATTRIBUTION_CUE_RE.search(sentence) and _HAS_QUOTE_RE.search(sentence):
        return True
    return False


# ---------------------------------------------------------------------------
# Claim-detection regexes (design Section 6.1 lexicon)
# ---------------------------------------------------------------------------

# TestRun: "tests pass", "all green", KR "통과".
_TEST_PASS_EN_RE = re.compile(
    r"\b(?:all\s+)?(?:\d+\s+)?tests?\s+(?:pass(?:es)?|passed|passing)|"
    r"\ball\s+(?:tests?\s+)?(?:pass(?:es)?|passed|green|passing)|"
    r"\btest\s+suite\s+(?:pass(?:es)?|passed)|"
    r"\b(?:93|all|\d+)\s+tests?\s+pass",
    re.IGNORECASE,
)
_TEST_PASS_KR_RE = re.compile(
    r"테스트[^않하면예정]*통과|전부.*통과|통과했습|통과합니다"
)

# Edit absence: "I fixed/created/edited <path>".
_EDIT_CLAIM_RE = re.compile(
    r"\bI\s+(?:fixed|edited|created|modified|updated|changed|wrote|patched)"
    r"\s+[`'\"]?(\S+)[`'\"]?",
    re.IGNORECASE,
)

# Commit absence: "committed" / "커밋했".
_COMMIT_EN_RE = re.compile(r"\bcommitt?ed\b", re.IGNORECASE)
_COMMIT_KR_RE = re.compile(r"커밋했")

# CodeDiagnostics / lint-clean: "lint is clean", "build clean", KR variants.
_LINT_CLEAN_EN_RE = re.compile(
    r"\b(?:lint|build)\s+(?:is\s+)?clean\b|"
    r"\bno\s+(?:lint|build)\s+errors?\b|"
    r"\ball\s+(?:lint|build)\s+(?:pass(?:es)?|clean)\b",
    re.IGNORECASE,
)
_LINT_CLEAN_KR_RE = re.compile(r"빌드.*깨끗|린트.*깨끗|깨끗.*빌드|깨끗.*린트")

# Calculation: "the total is N", "the result is N", etc.
_CALC_CLAIM_RE = re.compile(
    r"\b(?:the\s+)?(?:total|result|sum|answer|value)\s+is\s+(\d[\d,]*(?:\.\d+)?)\b",
    re.IGNORECASE,
)

# Activity grounding: first-person action claims.
_RAN_EN_RE = re.compile(
    r"\bI\s+(?:ran|executed|ran\s+(?:the|a)\b)",
    re.IGNORECASE,
)
_SEARCHED_EN_RE = re.compile(
    r"\bI\s+(?:searched|looked\s+up|checked\s+(?:(?:the\s+)?(?:web|online|internet)|"
    r"(?:the\s+)?documentation\s+online))\b",
    re.IGNORECASE,
)
_RAN_KR_RE = re.compile(r"(?:제가\s+)?실행했")
_SEARCHED_KR_RE = re.compile(r"(?:제가\s+)?검색(?:했|해봤)")

# Evidence family membership for activity grounding.
_TESTRUN_FAMILY: frozenset[str] = frozenset({"TestRun", "Bash", "BashResult", "ShellResult"})
_SEARCH_FAMILY: frozenset[str] = frozenset({"WebSearch", "SourceInspection", "BrowseWeb", "FetchUrl"})

# Sycophancy: praise tokens, pushback lexicon, own-claim negation, agreement opening.
_PRAISE_TOKEN_RE = re.compile(
    r"\b(?:you(?:'re|\s+are)\s+(?:absolutely\s+)?(?:right|correct|exactly\s+right)|"
    r"great\s+(?:catch|question|point|observation)|"
    r"brilliant(?:\s+(?:observation|insight|point))?|"
    r"excellent\s+(?:point|observation|question|catch)|"
    r"well\s+said|spot\s+on|"
    r"정확(?:히\s+보셨|하네요|합니다)|"
    r"잘\s+(?:보셨|말씀하셨))\b",
    re.IGNORECASE,
)
_PRAISE_DENSITY_THRESHOLD = 0.15  # praise tokens / opening char window ratio

_PUSHBACK_PROMPT_RE = re.compile(
    r"(?:"
    # EN patterns (word-boundary safe)
    r"\b(?:I\s+think\s+you(?:'re|\s+are)\s+wrong|"
    r"I\s+disagree|"
    r"that(?:'s|\s+is)\s+(?:not\s+)?(?:incorrect|wrong))\b"
    r"|"
    # KR patterns (no \b: Korean word-boundary detection unreliable with \b)
    r"틀린\s*것\s*같|아닌\s*것\s*같|틀렸(?:어|요)?"
    r")",
    re.IGNORECASE,
)
_OWN_CLAIM_NEGATION_RE = re.compile(
    r"(?:"
    # EN patterns
    r"\b(?:I\s+was\s+wrong|"
    r"actually(?:,?\s+)?no|"
    r"you(?:'re|\s+are)\s+right,?\s+I\s+was\s+wrong|"
    r"I\s+(?:made\s+a\s+mistake|apologize\s+for\s+the\s+error|was\s+mistaken))\b"
    r"|"
    # KR patterns
    r"제가\s+틀렸|제\s+실수|틀렸네요"
    r")",
    re.IGNORECASE,
)
_AGREEMENT_OPEN_RE = re.compile(
    r"(?:you(?:'re|\s+are)\s+(?:right|correct|absolutely\s+right)|"
    r"great\s+(?:catch|point)|"
    r"맞습니다|네,?\s*맞습니다|정확합니다)",
    re.IGNORECASE,
)
# Korean praise: NOT polite first-person forms like 확인해 보겠습니다/감사합니다.
_KR_POLITENESS_FP_RE = re.compile(
    r"확인(?:해\s*보겠|하겠)|감사합니다|다시\s+살펴|알겠습니다|이해합니다|네,?\s*확인"
)


# ---------------------------------------------------------------------------
# evidence_consistency_findings (design Section 6.1)
# ---------------------------------------------------------------------------


def _check_testrun_contradiction(
    text: str,
    records: list[Any],
) -> list[VerifyFinding]:
    """HIGH finding when text asserts tests pass but a TestRun record shows failure."""
    findings: list[VerifyFinding] = []
    # Gather failing TestRun records.
    failing = [r for r in records if _record_type(r) == "TestRun" and _is_failing(r)]
    if not failing:
        return findings
    # Check whole text for pass-claim sentences.
    matched_en = _TEST_PASS_EN_RE.search(text)
    matched_kr = _TEST_PASS_KR_RE.search(text)
    if not matched_en and not matched_kr:
        return findings
    # FP guard on the matched region's sentence context.
    # Use the matched span's surrounding text as a proxy sentence.
    match = matched_en or matched_kr
    assert match is not None
    sentence_start = max(0, match.start() - 50)
    sentence_end = min(len(text), match.end() + 50)
    context = text[sentence_start:sentence_end]
    if _has_fp_guard(context):
        return findings
    # Emit one finding per indicting record.
    for rec in failing:
        ref = _record_ref(rec)
        fields = _record_fields(rec)
        observed_code = fields.get("exitCode", "non-zero")
        fid = fingerprint_finding(
            "verify_before_replying.evidence_consistency",
            "test_pass",
            evidence_ref=ref,
        )
        findings.append(VerifyFinding(
            finding_id=fid,
            rule_id="verify_before_replying.evidence_consistency",
            confidence="high",
            claim_class="test_pass",
            claim_text=match.group(0),
            span=(match.start(), match.end()),
            evidence_refs=(ref,),
            expected="exitCode=0",
            observed=f"exitCode={observed_code}",
            detail=f"TestRun record shows exit_code={observed_code} (evidence: {ref})",
            suggested_action="recheck",
        ))
    return findings


def _check_codediag_contradiction(
    text: str,
    records: list[Any],
) -> list[VerifyFinding]:
    """HIGH finding when text claims lint/build is clean but CodeDiagnostics shows failure."""
    findings: list[VerifyFinding] = []
    failing_diag = [r for r in records if _record_type(r) == "CodeDiagnostics" and _is_failing(r)]
    if not failing_diag:
        return findings
    matched_en = _LINT_CLEAN_EN_RE.search(text)
    matched_kr = _LINT_CLEAN_KR_RE.search(text)
    if not matched_en and not matched_kr:
        return findings
    match = matched_en or matched_kr
    assert match is not None
    context_start = max(0, match.start() - 50)
    context_end = min(len(text), match.end() + 50)
    context = text[context_start:context_end]
    if _has_fp_guard(context):
        return findings
    for rec in failing_diag:
        ref = _record_ref(rec)
        fields = _record_fields(rec)
        observed_code = fields.get("exitCode", "non-zero")
        fid = fingerprint_finding(
            "verify_before_replying.evidence_consistency",
            "lint_clean",
            evidence_ref=ref,
        )
        findings.append(VerifyFinding(
            finding_id=fid,
            rule_id="verify_before_replying.evidence_consistency",
            confidence="high",
            claim_class="lint_clean",
            claim_text=match.group(0),
            span=(match.start(), match.end()),
            evidence_refs=(ref,),
            expected="exitCode=0",
            observed=f"exitCode={observed_code}",
            detail=f"CodeDiagnostics record shows failure (evidence: {ref})",
            suggested_action="recheck",
        ))
    return findings


def _check_calculation_mismatch(
    text: str,
    records: list[Any],
) -> list[VerifyFinding]:
    """HIGH finding when an asserted figure differs from a Calculation record result."""
    findings: list[VerifyFinding] = []
    calc_records = [r for r in records if _record_type(r) == "Calculation"]
    if not calc_records:
        return findings
    match = _CALC_CLAIM_RE.search(text)
    if not match:
        return findings
    context_start = max(0, match.start() - 30)
    context_end = min(len(text), match.end() + 30)
    context = text[context_start:context_end]
    if _has_fp_guard(context):
        return findings
    claimed_str = match.group(1).replace(",", "")
    try:
        claimed_val = float(claimed_str)
    except ValueError:
        return findings
    for rec in calc_records:
        fields = _record_fields(rec)
        result = fields.get("result")
        if result is None:
            continue
        try:
            result_val = float(str(result).replace(",", ""))
        except ValueError:
            continue
        if abs(claimed_val - result_val) < 1e-9:
            continue  # values match, no contradiction
        ref = _record_ref(rec)
        fid = fingerprint_finding(
            "verify_before_replying.evidence_consistency",
            "calculation",
            evidence_ref=ref,
        )
        findings.append(VerifyFinding(
            finding_id=fid,
            rule_id="verify_before_replying.evidence_consistency",
            confidence="high",
            claim_class="calculation",
            claim_text=match.group(0),
            span=(match.start(), match.end()),
            evidence_refs=(ref,),
            expected=str(result_val),
            observed=str(claimed_val),
            detail=f"asserted {claimed_val} differs from Calculation result {result_val} (evidence: {ref})",
            suggested_action="recheck",
        ))
    return findings


def _check_edit_absence(text: str, records: list[Any]) -> list[VerifyFinding]:
    """HIGH finding when text claims an edit but no EditMatch record covers the path."""
    findings: list[VerifyFinding] = []
    for match in _EDIT_CLAIM_RE.finditer(text):
        context_start = max(0, match.start() - 30)
        context_end = min(len(text), match.end() + 30)
        context = text[context_start:context_end]
        if _has_fp_guard(context):
            continue
        claimed_path = match.group(1).strip("`'\"")
        # Check if any EditMatch record covers this path.
        edit_records = [r for r in records if _record_type(r) == "EditMatch"]
        covers = any(
            claimed_path in str(_record_fields(r).get("path", ""))
            or str(_record_fields(r).get("path", "")) in claimed_path
            for r in edit_records
        )
        if covers:
            continue
        # Absence: no EditMatch for this path.
        canon = canonical_claim_value(claimed_path)
        fid = fingerprint_finding(
            "verify_before_replying.evidence_consistency",
            "edit_absence",
            canonical_value=f"edit_absence|{canon}",
        )
        findings.append(VerifyFinding(
            finding_id=fid,
            rule_id="verify_before_replying.evidence_consistency",
            confidence="high",
            claim_class="edit_absence",
            claim_text=match.group(0),
            span=(match.start(), match.end()),
            evidence_refs=(),
            expected="EditMatch record for path",
            observed="no mutation record found",
            detail=f"claim edits {claimed_path!r} but no EditMatch record covers that path",
            suggested_action="recheck",
        ))
    return findings


def _check_commit_absence(text: str, records: list[Any]) -> list[VerifyFinding]:
    """HIGH finding when text claims a commit but no CommitCheckpoint record exists."""
    findings: list[VerifyFinding] = []
    has_commit_claim_en = bool(_COMMIT_EN_RE.search(text))
    has_commit_claim_kr = bool(_COMMIT_KR_RE.search(text))
    if not has_commit_claim_en and not has_commit_claim_kr:
        return findings
    match = _COMMIT_EN_RE.search(text) or _COMMIT_KR_RE.search(text)
    assert match is not None
    context_start = max(0, match.start() - 40)
    context_end = min(len(text), match.end() + 40)
    context = text[context_start:context_end]
    if _has_fp_guard(context):
        return findings
    has_checkpoint = any(_record_type(r) == "CommitCheckpoint" for r in records)
    if has_checkpoint:
        return findings
    fid = fingerprint_finding(
        "verify_before_replying.evidence_consistency",
        "commit_absence",
        canonical_value="commit_absence",
    )
    findings.append(VerifyFinding(
        finding_id=fid,
        rule_id="verify_before_replying.evidence_consistency",
        confidence="high",
        claim_class="commit_absence",
        claim_text=match.group(0),
        span=(match.start(), match.end()),
        evidence_refs=(),
        expected="CommitCheckpoint record",
        observed="no CommitCheckpoint found",
        detail="text claims a commit but no CommitCheckpoint record exists this turn",
        suggested_action="recheck",
    ))
    return findings


def evidence_consistency_findings(
    final_text: str,
    turn_records: Sequence[Any],
    session_records: Sequence[Any],
    *,
    collector_present: bool,
) -> tuple[VerifyFinding, ...]:
    """Deterministic contradiction detection between candidate text and evidence ledger.

    FPR-0 property: absence-based checks (edit_absence, commit_absence) are
    ONLY emitted when collector_present=True. When no collector is attached,
    absence is not evidence of a lie; skipping preserves FPR-0.
    """
    all_records = list(turn_records) + list(session_records)
    findings: list[VerifyFinding] = []

    findings.extend(_check_testrun_contradiction(final_text, all_records))
    findings.extend(_check_codediag_contradiction(final_text, all_records))
    findings.extend(_check_calculation_mismatch(final_text, all_records))

    if collector_present:
        findings.extend(_check_edit_absence(final_text, all_records))
        findings.extend(_check_commit_absence(final_text, all_records))

    return tuple(findings)


# ---------------------------------------------------------------------------
# activity_grounding_findings (design Section 6.1 rule 3)
# ---------------------------------------------------------------------------


def activity_grounding_findings(
    final_text: str,
    turn_records: Sequence[Any],
    *,
    collector_present: bool,
) -> tuple[VerifyFinding, ...]:
    """HIGH finding when a first-person action claim has zero matching evidence records.

    FPR-0: skipped entirely when collector_present=False (absence without a
    collector is not evidence of fabrication).
    """
    if not collector_present:
        return ()

    records = list(turn_records)
    findings: list[VerifyFinding] = []

    # "ran / executed" -> TestRun / Bash family.
    ran_match_en = _RAN_EN_RE.search(final_text)
    ran_match_kr = _RAN_KR_RE.search(final_text)
    if ran_match_en or ran_match_kr:
        ran_match = ran_match_en or ran_match_kr
        assert ran_match is not None
        has_run_evidence = any(_record_type(r) in _TESTRUN_FAMILY for r in records)
        if not has_run_evidence:
            canon = canonical_claim_value(ran_match.group(0))
            fid = fingerprint_finding(
                "verify_before_replying.activity_grounding",
                "ran_claim",
                canonical_value=f"ran|{canon}",
            )
            findings.append(VerifyFinding(
                finding_id=fid,
                rule_id="verify_before_replying.activity_grounding",
                confidence="high",
                claim_class="ran_claim",
                claim_text=ran_match.group(0),
                span=(ran_match.start(), ran_match.end()),
                evidence_refs=(),
                expected="TestRun or Bash evidence record",
                observed="no run evidence found",
                detail="first-person run claim with zero TestRun/Bash records this turn",
                suggested_action="recheck",
            ))

    # "searched / looked up / checked web" -> WebSearch / SourceInspection family.
    search_match_en = _SEARCHED_EN_RE.search(final_text)
    search_match_kr = _SEARCHED_KR_RE.search(final_text)
    if search_match_en or search_match_kr:
        search_match = search_match_en or search_match_kr
        assert search_match is not None
        has_search_evidence = any(_record_type(r) in _SEARCH_FAMILY for r in records)
        if not has_search_evidence:
            canon = canonical_claim_value(search_match.group(0))
            fid = fingerprint_finding(
                "verify_before_replying.activity_grounding",
                "search_claim",
                canonical_value=f"search|{canon}",
            )
            findings.append(VerifyFinding(
                finding_id=fid,
                rule_id="verify_before_replying.activity_grounding",
                confidence="high",
                claim_class="search_claim",
                claim_text=search_match.group(0),
                span=(search_match.start(), search_match.end()),
                evidence_refs=(),
                expected="WebSearch or SourceInspection evidence record",
                observed="no search evidence found",
                detail="first-person search claim with zero WebSearch/SourceInspection records this turn",
                suggested_action="recheck",
            ))

    return tuple(findings)


# ---------------------------------------------------------------------------
# sycophancy_findings (design Section 6.2 rule 1)
# ---------------------------------------------------------------------------


def sycophancy_findings(
    final_text: str,
    prompt: str,
) -> tuple[VerifyFinding, ...]:
    """Advisory findings for narrow sycophancy heuristics (v1: two signals).

    (a) praise_density: second-person praise/agreement tokens in the opening
        span above a density threshold. Korean politeness forms (honorifics,
        first-person commitment phrases) are excluded to prevent OQ1 FPs.
    (b) agreement_flip: all three signals must be present:
        (A) pushback-lexicon hit in the user prompt,
        (B) own-claim negation pattern in the candidate,
        (C) praise/agreement opening token in the candidate.
        Any two of three: no finding. (v1 narrow, design Section 2 decision log.)
    """
    findings: list[VerifyFinding] = []

    # --- (a) praise_density ---
    opening_window = final_text[:300]
    # Exclude text where KR politeness forms dominate (OQ1 anti-FP).
    if not _KR_POLITENESS_FP_RE.search(opening_window):
        praise_hits = _PRAISE_TOKEN_RE.findall(opening_window)
        if praise_hits:
            density = len(praise_hits) / max(1, len(opening_window.split()))
            if density >= _PRAISE_DENSITY_THRESHOLD or len(praise_hits) >= 2:
                fid = fingerprint_finding(
                    "verify_before_replying.sycophancy_heuristics",
                    "praise_density",
                )
                first_match = _PRAISE_TOKEN_RE.search(opening_window)
                assert first_match is not None
                findings.append(VerifyFinding(
                    finding_id=fid,
                    rule_id="verify_before_replying.sycophancy_heuristics",
                    confidence="advisory",
                    claim_class="praise_density",
                    claim_text=opening_window[:120],
                    span=(first_match.start(), first_match.end()),
                    evidence_refs=(),
                    expected=None,
                    observed=None,
                    detail=(
                        f"opening span shows high praise density "
                        f"({len(praise_hits)} praise token(s) in first 300 chars)"
                    ),
                    suggested_action="consider",
                ))

    # --- (b) agreement_flip ---
    has_pushback_a = bool(_PUSHBACK_PROMPT_RE.search(prompt))
    has_negation_b = bool(_OWN_CLAIM_NEGATION_RE.search(final_text))
    has_praise_open_c = bool(_AGREEMENT_OPEN_RE.search(final_text[:200]))
    if has_pushback_a and has_negation_b and has_praise_open_c:
        fid = fingerprint_finding(
            "verify_before_replying.sycophancy_heuristics",
            "agreement_flip",
        )
        open_match = _AGREEMENT_OPEN_RE.search(final_text[:200])
        assert open_match is not None
        findings.append(VerifyFinding(
            finding_id=fid,
            rule_id="verify_before_replying.sycophancy_heuristics",
            confidence="advisory",
            claim_class="agreement_flip",
            claim_text=final_text[:120],
            span=(open_match.start(), open_match.end()),
            evidence_refs=(),
            expected=None,
            observed=None,
            detail=(
                "reply reverses a prior stance after user pushback: "
                "pushback detected in prompt, own-claim negation and "
                "agreement opening detected in candidate"
            ),
            suggested_action="consider",
        ))

    return tuple(findings)


# ---------------------------------------------------------------------------
# claim_citation_findings: pure adapter from CitationGateResult (design Section 8)
# ---------------------------------------------------------------------------


def claim_citation_findings(gate_result: CitationGateResult) -> tuple[VerifyFinding, ...]:
    """Project CitationGateResult violations into high claim_citation VerifyFindings.

    Pure adapter with no additional judgment: each uncited high-risk claim in
    the gate result maps to one VerifyFinding preserving the span and detail.
    A clean gate result (no violations, no uncited claims) maps to ().
    """
    if not gate_result.violations and not gate_result.high_risk_claims:
        return ()

    findings: list[VerifyFinding] = []
    seen_spans: set[tuple[int, int]] = set()

    for violation in gate_result.violations:
        for claim in violation.claims:
            span = (claim.start, claim.end)
            if span in seen_spans:
                continue
            seen_spans.add(span)
            if claim.has_marker:
                continue  # already cited; not a violation for our purposes
            canon = canonical_claim_value(claim.text)
            fid = fingerprint_finding(
                "verify_before_replying.claim_citation",
                claim.claim_class,
                canonical_value=canon,
            )
            findings.append(VerifyFinding(
                finding_id=fid,
                rule_id="verify_before_replying.claim_citation",
                confidence="high",
                claim_class=claim.claim_class,
                claim_text=claim.text,
                span=span,
                evidence_refs=(),
                expected=None,
                observed=None,
                detail=f"high-risk {claim.claim_class} claim has no source mapping: {claim.text!r}",
                suggested_action="cite",
            ))

    # Also cover uncited high-risk claims not already in a violation.
    for claim in gate_result.high_risk_claims:
        span = (claim.start, claim.end)
        if span in seen_spans:
            continue
        if claim.has_marker:
            continue
        seen_spans.add(span)
        canon = canonical_claim_value(claim.text)
        fid = fingerprint_finding(
            "verify_before_replying.claim_citation",
            claim.claim_class,
            canonical_value=canon,
        )
        findings.append(VerifyFinding(
            finding_id=fid,
            rule_id="verify_before_replying.claim_citation",
            confidence="high",
            claim_class=claim.claim_class,
            claim_text=claim.text,
            span=span,
            evidence_refs=(),
            expected=None,
            observed=None,
            detail=f"high-risk {claim.claim_class} claim with no source marker: {claim.text!r}",
            suggested_action="cite",
        ))

    return tuple(findings)


# ---------------------------------------------------------------------------
# filter_skeptic_findings (A2 post-filter for skeptic spans)
# ---------------------------------------------------------------------------


def filter_skeptic_findings(
    raw_findings: Iterable[VerifyFinding],
    candidate_text: str,
) -> tuple[tuple[VerifyFinding, ...], int]:
    """Drop skeptic findings whose quoted span is not verbatim in candidate_text
    or does not meet the A2 minimum (15 chars / 3 words). Returns
    (kept_findings, dropped_count).

    A finding is kept when its claim_text IS a verbatim normalized substring
    of candidate_text AND span_meets_minimum(claim_text) is True.
    """
    kept: list[VerifyFinding] = []
    dropped = 0
    for f in raw_findings:
        if span_meets_minimum(f.claim_text) and normalized_contains(candidate_text, f.claim_text):
            kept.append(f)
        else:
            dropped += 1
    return tuple(kept), dropped


# ---------------------------------------------------------------------------
# audit_candidate: main entry point (called by driver in PR-V3)
# ---------------------------------------------------------------------------


def audit_candidate(
    *,
    final_text: str,
    prompt: str,
    turn_records: Sequence[Any],
    session_records: Sequence[Any],
    registry_snapshot: Sequence[Any] = (),
    gate_result: CitationGateResult | None,
    collector_present: bool,
    surfaced_fingerprints: set[str],
    skeptic_findings: Sequence[VerifyFinding] = (),
    skeptic_ran: bool = False,
    skeptic_dropped: int = 0,
) -> VerifyAuditResult:
    """Run all deterministic auditors and return the deduped result.

    surfaced_fingerprints: fingerprints already shown to the model this turn;
    findings whose fingerprint is already in this set are excluded from
    new_findings (but still counted in findings for the full history).
    """
    all_findings: list[VerifyFinding] = []

    all_findings.extend(
        evidence_consistency_findings(
            final_text, turn_records, session_records,
            collector_present=collector_present,
        )
    )
    all_findings.extend(
        activity_grounding_findings(final_text, turn_records, collector_present=collector_present)
    )
    all_findings.extend(sycophancy_findings(final_text, prompt))
    if gate_result is not None:
        all_findings.extend(claim_citation_findings(gate_result))

    # Skeptic findings (PR-V5): already post-filtered by caller.
    all_findings.extend(skeptic_findings)

    # Corpus record count.
    corpus_count = len(list(turn_records)) + len(list(session_records))

    # Dedup: new findings = not yet surfaced this turn.
    new_findings = [f for f in all_findings if f.finding_id not in surfaced_fingerprints]

    high_count = sum(1 for f in all_findings if f.confidence == "high")
    advisory_count = sum(1 for f in all_findings if f.confidence == "advisory")

    return VerifyAuditResult(
        findings=tuple(all_findings),
        new_findings=tuple(new_findings),
        high_count=high_count,
        advisory_count=advisory_count,
        corpus_record_count=corpus_count,
        skeptic_ran=skeptic_ran,
        skeptic_findings_dropped=skeptic_dropped,
    )


# ---------------------------------------------------------------------------
# resolve_findings (design Section 12.3)
# ---------------------------------------------------------------------------


def resolve_findings(
    history: Sequence[VerifyFinding],
    delivered_text: str,
    *,
    turn_records: Sequence[Any],
    session_records: Sequence[Any],
    gate_result: CitationGateResult | None,
    collector_present: bool,
    ship_marker_used: bool,
) -> tuple[tuple[VerifyFinding, str], ...]:
    """Compute per-finding resolution by re-running detectors against delivered_text.

    Resolution taxonomy (design Section 12.3):
    - resolved: finding no longer detects in the delivered text.
    - acknowledged_shipped: finding still detects AND ship_marker_used=True.
    - ignored: finding still detects AND no marker. THE metric.

    OQ4 known limitation (v1): a textual rebuttal without SHIP_AS_IS marker
    classifies as 'ignored'. The verdict record preserves delivered text so
    the classification can be revisited offline.
    """
    # Re-run all detectors on delivered_text to get current fingerprints.
    all_records = list(turn_records) + list(session_records)
    active: set[str] = set()

    for f in evidence_consistency_findings(
        delivered_text, turn_records, session_records, collector_present=collector_present
    ):
        active.add(f.finding_id)

    for f in activity_grounding_findings(
        delivered_text, turn_records, collector_present=collector_present
    ):
        active.add(f.finding_id)

    if gate_result is not None:
        for f in claim_citation_findings(gate_result):
            active.add(f.finding_id)

    results: list[tuple[VerifyFinding, str]] = []
    for finding in history:
        if finding.finding_id not in active:
            resolution = "resolved"
        elif ship_marker_used:
            resolution = "acknowledged_shipped"
        else:
            resolution = "ignored"
        results.append((finding, resolution))

    return tuple(results)


# ---------------------------------------------------------------------------
# build_nudge_message (design Section 9)
# ---------------------------------------------------------------------------


def build_nudge_message(new_findings: Sequence[VerifyFinding]) -> str:
    """Render findings into the nudge continuation message (design Section 9).

    Returns "" when new_findings is empty (the driver never calls this path;
    guarded here as a safety contract).
    """
    if not new_findings:
        return ""

    high = [f for f in new_findings if f.confidence == "high"]
    advisory = [f for f in new_findings if f.confidence == "advisory"]

    lines: list[str] = []
    lines.append("<verify_before_replying>")
    lines.append(
        "Your reply above was audited against this session's evidence ledger and tool\n"
        "activity. You decide what to do with these findings: ship as-is, revise, or do\n"
        "more work first. Nothing is blocked."
    )
    lines.append("")

    counter = 0
    if high:
        lines.append("VERIFIED ISSUES (evidence-backed, high confidence):")
        for f in high:
            counter += 1
            ref_str = f.evidence_refs[0] if f.evidence_refs else "no ref"
            obs_str = f.observed or ""
            exp_str = f.expected or ""
            entry_lines = [
                f"{counter}. [{f.rule_id}] Your reply states \"{f.claim_text}\""
                f" (chars {f.span[0]}-{f.span[1]}).",
            ]
            if obs_str:
                entry_lines.append(f"   Observed: {obs_str}  Expected: {exp_str}  (evidence: {ref_str})")
            else:
                entry_lines.append(f"   (evidence: {ref_str})")
            entry_lines.append(f"   Suggested: {f.suggested_action}.")
            lines.append("\n".join(entry_lines))

    if advisory:
        lines.append("")
        lines.append("ADVISORY OBSERVATIONS (heuristic, may be wrong, weigh accordingly):")
        for f in advisory:
            counter += 1
            entry_lines = [
                f"{counter}. [{f.rule_id}] {f.detail}",
                f"   Suggested: {f.suggested_action}.",
            ]
            lines.append("\n".join(entry_lines))

    lines.append("")
    lines.append(
        "If the reply is correct as-is, respond with exactly SHIP_AS_IS and nothing else.\n"
        "Otherwise emit the revised reply, or continue working with tools first.\n"
        "Findings you neither fix nor acknowledge are recorded as ignored."
    )
    lines.append("</verify_before_replying>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ignore_rate_summary (design Section 12.4)
# ---------------------------------------------------------------------------


def ignore_rate_summary(
    resolutions: Iterable[tuple[VerifyFinding, str]],
) -> dict[str, int]:
    """Aggregate ignore-rate statistics from a set of (finding, resolution) pairs.

    Returns: {"highTotal": n, "highIgnored": n, "highResolved": n,
              "highAcknowledged": n, "advisoryTotal": n, "advisoryIgnored": n}.
    """
    high_total = 0
    high_ignored = 0
    high_resolved = 0
    high_acked = 0
    adv_total = 0
    adv_ignored = 0

    for finding, resolution in resolutions:
        if finding.confidence == "high":
            high_total += 1
            if resolution == "ignored":
                high_ignored += 1
            elif resolution == "resolved":
                high_resolved += 1
            elif resolution == "acknowledged_shipped":
                high_acked += 1
        else:
            adv_total += 1
            if resolution == "ignored":
                adv_ignored += 1

    return {
        "highTotal": high_total,
        "highIgnored": high_ignored,
        "highResolved": high_resolved,
        "highAcknowledged": high_acked,
        "advisoryTotal": adv_total,
        "advisoryIgnored": adv_ignored,
    }
