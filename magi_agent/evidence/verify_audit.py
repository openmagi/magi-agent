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
# display_span: redaction-safe claim span transform (PR-2, design 3.4)
# ---------------------------------------------------------------------------

# Local regex mirrors of reports.py private patterns. The public function
# public_projection_safe_text is the postcondition source of truth (step 9);
# these local regexes are used for targeted substring replacement so we never
# fork the redaction grammar. Mirror, not import, to avoid coupling to private
# reports.py internals.
_DS_URL_RE = re.compile(
    r"(?:https?|s3|gs|file|ssh|git)://([A-Za-z0-9._-]+)(?:[/?#][^\s\"'{}\]\)]*)?",
    re.IGNORECASE,
)
_DS_GIT_SSH_RE = re.compile(
    r"git@([A-Za-z0-9._-]+):[^\s\"'{}\]\)]+",
    re.IGNORECASE,
)
_DS_REF_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:search|source|ref):[^\s\"'{}\]\)]+",
    re.IGNORECASE,
)
_DS_PVC_RE = re.compile(
    r"pvc-[A-Za-z0-9-]+",
    re.IGNORECASE,
)
_DS_PATH_RE = re.compile(
    r"(?:"
    r"~[\\/][^,\s\"'{}\]\)]*|"
    r"[A-Za-z]:[\\/][^,\s\"'{}\]\)]+|"
    r"\\\\[^,\s\"'{}\]\)]+|"
    r"(?<![A-Za-z0-9:/])/(?:[^/,\s\"'{}\]\)]+)(?:/[^/,\s\"'{}\]\)]+)*"
    r")",
)


def _ds_basename(m: re.Match) -> str:  # type: ignore[type-arg]
    """Extract basename from a path-match for use as replacement."""
    raw = m.group(0)
    # Normalize separators and split
    for sep in ("\\", "/"):
        parts = raw.replace("\\", "/").rstrip("/").split("/")
        if len(parts) >= 1 and parts[-1]:
            return parts[-1]
    return raw


def display_span(text: str, *, limit: int = 240) -> str:
    """Collapse redaction-triggering substrings so a verbatim claim span
    survives public_projection_safe_text whole-string redaction (design 3.4).

    Pure and deterministic. Returns "" when the input is empty or when the
    sanitized result would STILL be redacted (the caller/FE falls back to
    claimClass + member-rule copy).
    """
    if not text:
        return ""

    # Step 1: Unicode NFC normalize.
    s = unicodedata.normalize("NFC", text)

    # Step 2: URLs -> host only.
    s = _DS_URL_RE.sub(lambda m: m.group(1), s)

    # Step 3: git@host:path -> host.
    s = _DS_GIT_SSH_RE.sub(lambda m: m.group(1), s)

    # Step 4: ref:/source:/search: tokens -> [ref].
    s = _DS_REF_TOKEN_RE.sub("[ref]", s)

    # Step 5: pvc-... -> [volume].
    s = _DS_PVC_RE.sub("[volume]", s)

    # Step 6: filesystem paths -> basename.
    s = _DS_PATH_RE.sub(_ds_basename, s)

    # Step 7: collapse whitespace runs, strip.
    s = " ".join(s.split())

    # Step 8: truncate to limit chars (budget-inclusive ellipsis).
    if len(s) > limit:
        s = s[: limit - 3] + "..."

    # Step 9: postcondition backstop.
    from magi_agent.evidence.reports import (  # noqa: PLC0415
        public_projection_safe_text,
    )

    if public_projection_safe_text(s) == "[redacted]":
        return ""

    return s


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
# execution_claims_findings (execution-claims member rule, design Section 2)
# ---------------------------------------------------------------------------

# Spawn-record predicates (2.2). The EvidenceRecord for a SubagentSpawn projects
# the full FirstPartyActivity dump (camelCase) onto fields, so fields["status"]
# is the raw ToolResult status (ok|error|blocked), authoritative over the
# EvidenceRecord status which maps blocked -> "unknown"
# (first_party_activity.py:44).
_SPAWN_RECORD_TYPE = "custom:FirstPartySubagentSpawn"
_SPAWN_FAILED_STATUSES = frozenset({"error", "blocked"})
_FIRST_PARTY_TYPE_PREFIX = "custom:FirstParty"

# Static model-family lexicon (2.5 arm 1b). Matching AID only, consulted INSIDE
# an already-matched delegation claim. Staleness degrades recall, never
# precision. Includes provider aliases (anthropic/openai/google/...) so
# provider-alias phrasing ("the Anthropic subagent", "the OpenAI reviewer") is
# both catchable and alias-suppressible against a matching spawn record.
_MODEL_FAMILY_TOKENS: tuple[str, ...] = (
    "gpt", "o3", "o4", "opus", "claude", "sonnet", "haiku", "fable", "gemini",
    "kimi", "glm", "grok", "llama", "mistral", "deepseek", "qwen", "minimax",
    "anthropic", "openai", "chatgpt", "google", "zhipu", "moonshot", "alibaba",
    "meta", "xai",
)

# Bidirectional alias groups (design 2.4.1 / 2.5). A spawn record whose derived
# family keys land in group G alias-satisfies ANY claimed family also in G, so a
# real "opus-4-8 anthropic" record suppresses a claimed "claude"/"sonnet"/
# "anthropic" family and vice versa. Groups are disjoint, so the incident-catch
# invariant holds: an anthropic-group record never satisfies a claimed "gpt".
_MODEL_FAMILY_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"claude", "opus", "sonnet", "haiku", "fable", "anthropic"}),
    frozenset({"gpt", "o3", "o4", "openai", "chatgpt"}),
    frozenset({"gemini", "google"}),
    frozenset({"glm", "zhipu"}),
    frozenset({"kimi", "moonshot"}),
    frozenset({"minimax"}),
    frozenset({"qwen", "alibaba"}),
    frozenset({"deepseek"}),
    frozenset({"llama", "meta"}),
    frozenset({"mistral"}),
    frozenset({"grok", "xai"}),
)


def _alias_expand(token: str) -> frozenset[str]:
    """Return token plus its bidirectional alias siblings (design 2.4.1).

    A token with no alias group expands to just itself, so novel/ungrouped
    family tokens keep exact-match semantics.
    """
    token = token.casefold()
    for group in _MODEL_FAMILY_ALIAS_GROUPS:
        if token in group:
            return group
    return frozenset({token})

# Sub-check 1 claim lexicon (2.5): first-person completed delegation only.
_SPAWN_CLAIM_EN_RE = re.compile(
    r"\bI\s+(?:spawned|launched|dispatched|delegated(?:\s+\S+){0,3}\s+to)\b"
    r"|\bI\s+(?:had|asked|got|instructed)\s+(?:\w+\s+){0,3}"
    r"(?:sub[- ]?agents?|agents?|models?)\s+(?:\w+\s+){0,2}"
    r"(?:debate|review|discuss|analy[sz]e|verify|evaluate)"
    r"|\bsub[- ]?agents?\s+(?:debated|reviewed|discussed|analy[sz]ed|verified|evaluated)\b"
    r"|\bwas\s+reviewed\s+by\b",
    re.IGNORECASE,
)
_SPAWN_CLAIM_KR_RE = re.compile(
    r"스폰했|(?:서브\s*)?에이전트(?:를|가|들이)?[^.\n]{0,30}?"
    r"(?:토론(?:시켰|했)|검토(?:시켰|했)|리뷰(?:시켰|했)|분석(?:시켰|했)|돌렸|실행했|위임했)"
    r"|리뷰를\s*받(?:았|아)|검토를\s*받(?:았|아)|토론시켰|위임했"
)

# Sub-check 2 success/completion predicates (2.4.2).
_EXEC_SUCCESS_EN_RE = re.compile(
    r"\b(?:reviewed|verified|confirmed|analy[sz]ed|debated|discussed|concluded|"
    r"reported|completed|finished|delivered|evaluated|assessed|agreed|"
    r"found|said|noted|argued|responded)\b",
    re.IGNORECASE,
)
_EXEC_SUCCESS_KR_RE = re.compile(
    r"검토했|검토를\s*마쳤|리뷰했|리뷰를\s*(?:완료|마쳤)|분석했|토론했|토론을\s*마쳤|"
    r"결론(?:냈|을\s*내렸)|평가했|완료했|응답했|말했|밝혔|동의했|검증했"
)

# Global execution-failure disclosure suppression (2.4.4).
_EXEC_DISCLOSURE_RE = re.compile(
    r"\b(?:fail(?:ed|ure)?|timed?\s*[- ]?out|timeout|refused|declined|"
    r"blocked|errored|did\s+not\s+(?:complete|run|respond)|"
    r"couldn'?t\s+(?:be\s+)?(?:spawn|complete|reach)|never\s+ran|was\s+not\s+spawned)\b"
    r"|실패|타임\s*아웃|시간\s*초과|거부|거절|차단|오류|에러|못\s*했|않았|불발|미완료|중단",
    re.IGNORECASE,
)

# Execution-specific plan/hypothetical guard (2.6), in addition to _has_fp_guard.
_EXEC_PLAN_GUARD_RE = re.compile(
    r"\b(?:can|could|may|might|plan(?:s|ning)?\s+to|going\s+to|about\s+to|"
    r"let\s+me|I(?:'ll| will| can| could)|option(?:s|ally)?|propose|would\s+like)\b"
    r"|할게|하겠|해볼|해\s*드릴|예정|계획|가능|옵션|제안|요청|부탁|해\s*달|하라고|하시면",
    re.IGNORECASE,
)

# Reason-token to human-readable rendering (Section 4). Unknown tokens fall
# through to the raw token verbatim.
_EXEC_REASON_HUMAN: Mapping[str, str] = {
    "child_turn_timeout": "the child agent hit its turn timeout",
    "child_turn_error": "the child agent's turn failed with an internal error",
    "child_provider_key_missing": "no API key was available for the child's provider",
    "child_model_route_unknown": "the requested child model route is not registered",
    "live_child_runner_attach_failed": "the child runner could not be attached",
    "child_runner_blocked": "the child run was blocked before starting",
}


def _spawn_records(records: Sequence[Any]) -> list[Any]:
    """All SubagentSpawn evidence records in the corpus (2.2)."""
    return [r for r in records if _record_type(r) == _SPAWN_RECORD_TYPE]


def _spawn_record_status(record: Any) -> str:
    """Raw ToolResult status (ok|error|blocked) from fields (2.2)."""
    return str(_record_fields(record).get("status") or "")


def _spawn_record_detail(record: Any) -> Mapping[str, Any]:
    detail = _record_fields(record).get("detail")
    return detail if isinstance(detail, Mapping) else {}


def _spawn_record_failed(record: Any) -> bool:
    """A spawn record is FAILED when its raw status is error/blocked (2.2).

    fields["status"] is authoritative because the EvidenceRecord status maps
    blocked -> "unknown" (first_party_activity.py:44).
    """
    if _spawn_record_status(record) in _SPAWN_FAILED_STATUSES:
        return True
    return _record_status(record) == "failed"


def _spawn_record_ok(record: Any) -> bool:
    """A spawn record is OK when its raw status is exactly "ok" (2.2)."""
    return _spawn_record_status(record) == "ok"


def _spawn_reason_token(record: Any) -> str:
    """reason -> errorCode -> spawnStatus, in that order (2.2)."""
    fields = _record_fields(record)
    reason = fields.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    error_code = fields.get("errorCode")
    if isinstance(error_code, str) and error_code:
        return error_code
    return str(_spawn_record_detail(record).get("spawnStatus") or "")


def _human_reason(token: str) -> str:
    """Render a reason token human-readably (Section 4); child_llm_ prefix and
    unknown tokens fall through per the table."""
    if not token:
        return "the child run did not complete"
    mapped = _EXEC_REASON_HUMAN.get(token)
    if mapped is not None:
        return mapped
    if token.startswith("child_llm_"):
        slug = token[len("child_llm_"):]
        return f"the child's model provider returned an error ({slug})"
    return token


def _variant_regex_for_slug(slug: str) -> re.Pattern[str] | None:
    """Build a separator-tolerant regex for a model slug (2.4.1).

    Separators [-_./: ] in the slug each match [-_./: ]? so "gpt-5.5",
    "gpt 5.5", "gpt5.5", "GPT-5.5" all match. Returns None for an empty slug.
    """
    slug = slug.strip()
    if not slug:
        return None
    fragments = re.split(r"[-_./: ]+", slug)
    fragments = [f for f in fragments if f]
    if not fragments:
        return None
    joined = r"[-_./: ]?".join(re.escape(f) for f in fragments)
    # Word-anchor short alpha-leading slug variants (whole slug len <= 5, e.g.
    # "o3", "glm-4") so a short token does not match mid-word ("o3" inside
    # "cargo3d", "glm-4" inside "aglm-4x"). Longer variants (e.g. "gpt-5.5") are
    # specific enough that a mid-word coincidence is negligible.
    if len(slug) <= 5 and slug[:1].isalpha():
        joined = r"\b" + joined + r"\b"
    return re.compile(joined, re.IGNORECASE)


def _spawn_mention_regexes(record: Any) -> list[re.Pattern[str]]:
    """Mention-token regexes derived FROM the record, never a global list (2.4.1).

    Returns model variant regex(es), the family token (leading alpha run of the
    model, len >= 3, word-bounded), and the persona token (exact word, len >= 3).
    Provider alone is never a mention token.
    """
    detail = _spawn_record_detail(record)
    regexes: list[re.Pattern[str]] = []

    model = detail.get("model")
    if isinstance(model, str) and model.strip():
        m = model.casefold().strip()
        halves = [h for h in m.split(":") if h] if ":" in m else [m]
        for half in halves:
            variant = _variant_regex_for_slug(half)
            if variant is not None:
                regexes.append(variant)
            family_match = re.match(r"[a-z]+", half)
            if family_match and len(family_match.group(0)) >= 3:
                regexes.append(
                    re.compile(r"\b" + re.escape(family_match.group(0)) + r"\b", re.IGNORECASE)
                )

    persona = detail.get("persona")
    if isinstance(persona, str) and len(persona.strip()) >= 3:
        p = persona.strip()
        # Word-boundary match for ASCII personas; bare token for non-ASCII (KO).
        if re.fullmatch(r"[A-Za-z0-9_]+", p):
            regexes.append(re.compile(r"\b" + re.escape(p) + r"\b", re.IGNORECASE))
        else:
            regexes.append(re.compile(re.escape(p), re.IGNORECASE))

    return regexes


def _spawn_record_label(record: Any) -> str:
    """provider:model when both present, else model, else persona (2.4.5)."""
    detail = _spawn_record_detail(record)
    model = detail.get("model")
    provider = detail.get("provider")
    persona = detail.get("persona")
    model_s = str(model).strip() if isinstance(model, str) else ""
    provider_s = str(provider).strip() if isinstance(provider, str) else ""
    persona_s = str(persona).strip() if isinstance(persona, str) else ""
    if provider_s and model_s:
        return f"{provider_s}:{model_s}"
    if model_s:
        return model_s
    return persona_s


def _exec_window_guarded(text: str, match: re.Match[str], *, radius: int = 120) -> bool:
    """True when the +-radius window around match is FP-guarded (2.6 + _has_fp_guard)."""
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    window = text[start:end]
    if _has_fp_guard(window):
        return True
    if _EXEC_PLAN_GUARD_RE.search(window):
        return True
    return False


def _record_family_candidate_keys(record: Any) -> set[str]:
    """Candidate family keys derived FROM a spawn record (design 2.4.1 / 2.5).

    Keys = leading-alpha run of the model slug, plus the provider token, plus
    every _MODEL_FAMILY_TOKENS entry that word-matches the record's own haystack
    (model + provider + persona). These are the pre-alias-expansion families the
    record can vouch for.
    """
    detail = _spawn_record_detail(record)
    keys: set[str] = set()

    model = detail.get("model")
    if isinstance(model, str) and model.strip():
        m = model.casefold().strip()
        for half in (h for h in m.split(":") if h) if ":" in m else (m,):
            family_match = re.match(r"[a-z]+", half)
            if family_match and len(family_match.group(0)) >= 3:
                keys.add(family_match.group(0))

    provider = detail.get("provider")
    if isinstance(provider, str) and provider.strip():
        keys.add(provider.casefold().strip())

    haystack_parts = [
        str(detail.get("model") or ""),
        str(detail.get("provider") or ""),
        str(detail.get("persona") or ""),
    ]
    haystack = " ".join(p for p in haystack_parts if p)
    if haystack:
        for token in _MODEL_FAMILY_TOKENS:
            if re.search(r"\b" + re.escape(token) + r"\b", haystack, re.IGNORECASE):
                keys.add(token)

    return keys


def _expanded_families_for_records(records: Sequence[Any]) -> set[str]:
    """Union of alias-expanded family keys across every spawn record (2.5).

    A claimed family present in this set is vouched for by some spawn record
    (directly or via a bidirectional alias sibling), so arm 1b must not fire on
    it.
    """
    expanded: set[str] = set()
    for record in records:
        for key in _record_family_candidate_keys(record):
            expanded |= _alias_expand(key)
    return expanded


def _check_failed_execution_success(
    text: str,
    records: Sequence[Any],
) -> list[VerifyFinding]:
    """Sub-check 2: a failed spawn presented as a success (2.4).

    Global disclosure suppression (2.4.4): if the candidate discloses ANY
    execution failure, emit nothing for the whole pass.
    """
    findings: list[VerifyFinding] = []
    if _EXEC_DISCLOSURE_RE.search(text):
        return findings

    all_spawns = _spawn_records(records)
    failed = [r for r in all_spawns if _spawn_record_failed(r)]
    if not failed:
        return findings
    ok_records = [r for r in all_spawns if _spawn_record_ok(r)]

    for rec in failed:
        detail = _spawn_record_detail(rec)
        model = detail.get("model")
        persona = detail.get("persona")
        model_s = str(model).strip().casefold() if isinstance(model, str) else ""
        persona_s = str(persona).strip().casefold() if isinstance(persona, str) else ""

        # Nothing safe to match on (2.4.1).
        if not model_s and not persona_s:
            continue

        # Retry-ok suppression (2.4.3): a matching ok record excuses this failure.
        if _has_retry_ok(ok_records, model_s, persona_s):
            continue

        mention_regexes = _spawn_mention_regexes(rec)
        if not mention_regexes:
            continue

        indicting = _first_success_mention(text, mention_regexes)
        if indicting is None:
            continue

        match = indicting
        ref = _record_ref(rec)
        status = _spawn_record_status(rec) or "error"
        reason_token = _spawn_reason_token(rec)
        label = _spawn_record_label(rec) or "the child agent"
        human = _human_reason(reason_token)
        token_suffix = f" ({reason_token})" if reason_token else ""
        fid = fingerprint_finding(
            "verify_before_replying.execution_claims",
            "failed_execution_presented_as_success",
            evidence_ref=ref,
        )
        findings.append(VerifyFinding(
            finding_id=fid,
            rule_id="verify_before_replying.execution_claims",
            confidence="high",
            claim_class="failed_execution_presented_as_success",
            claim_text=match.group(0),
            span=(match.start(), match.end()),
            evidence_refs=(ref,),
            expected=f"SpawnAgent completed for {label}",
            observed=f"SpawnAgent {status}: {human}{token_suffix}",
            detail=(
                f"spawn of {label} ended {status} reason={reason_token or 'unknown'} "
                f"but the reply presents its output as delivered (evidence: {ref})"
            ),
            suggested_action="recheck",
        ))

    return findings


def _has_retry_ok(ok_records: Sequence[Any], model_s: str, persona_s: str) -> bool:
    """Retry-ok suppression predicate (2.4.3).

    Match on model slug equality (casefolded) when both records carry model;
    when the failed record carries a persona, the ok record must ALSO match the
    persona (a failed "optimistic" is not excused by a successful "skeptical").
    """
    for ok in ok_records:
        detail = _spawn_record_detail(ok)
        ok_model = str(detail.get("model") or "").strip().casefold()
        ok_persona = str(detail.get("persona") or "").strip().casefold()
        if model_s and ok_model != model_s:
            continue
        if persona_s and ok_persona != persona_s:
            continue
        if not model_s and not persona_s:
            continue
        return True
    return False


def _first_success_mention(
    text: str,
    mention_regexes: Sequence[re.Pattern[str]],
) -> re.Match[str] | None:
    """First mention token whose +-120 window carries a success predicate and
    passes the FP guards (2.4.2)."""
    for rx in mention_regexes:
        for match in rx.finditer(text):
            start = max(0, match.start() - 120)
            end = min(len(text), match.end() + 120)
            window = text[start:end]
            if not (_EXEC_SUCCESS_EN_RE.search(window) or _EXEC_SUCCESS_KR_RE.search(window)):
                continue
            if _exec_window_guarded(text, match):
                continue
            return match
    return None


def _check_fabricated_execution(
    text: str,
    records: Sequence[Any],
) -> list[VerifyFinding]:
    """Sub-check 1: a delegation claim with no supporting spawn evidence (2.5).

    Arm 1a: any spawn record set is empty -> generic absence.
    Arm 1b: a model family is named in the claim window but no spawn record
    matches that family -> model-named absence (the leg-3 catch).
    """
    findings: list[VerifyFinding] = []

    claim_match = _SPAWN_CLAIM_EN_RE.search(text) or _SPAWN_CLAIM_KR_RE.search(text)
    if claim_match is None:
        return findings
    if _exec_window_guarded(text, claim_match):
        return findings

    all_spawns = _spawn_records(records)

    # Arm 1a: generic absence.
    if not all_spawns:
        fid = fingerprint_finding(
            "verify_before_replying.execution_claims",
            "fabricated_execution",
            canonical_value="fabricated_execution|generic",
        )
        findings.append(VerifyFinding(
            finding_id=fid,
            rule_id="verify_before_replying.execution_claims",
            confidence="high",
            claim_class="fabricated_execution",
            claim_text=claim_match.group(0),
            span=(claim_match.start(), claim_match.end()),
            evidence_refs=(),
            expected="custom:FirstPartySubagentSpawn record",
            observed="no SpawnAgent evidence found this session",
            detail=(
                "reply claims a subagent delegation but no SpawnAgent evidence "
                "record exists this session"
            ),
            suggested_action="recheck",
        ))
        return findings

    # Arm 1b: model-named absence. Scan the claim window for a family token, then
    # suppress any family that a spawn record vouches for directly or through a
    # bidirectional alias sibling (design 2.4.1 / 2.5). "opus-4-8 anthropic" thus
    # suppresses a claimed "claude"/"sonnet"/"anthropic"; a claimed "gpt" (a
    # different alias group) is never suppressed by an anthropic-group record, so
    # the incident-catch invariant holds.
    win_start = max(0, claim_match.start() - 120)
    win_end = min(len(text), claim_match.end() + 120)
    window = text[win_start:win_end]
    covered_families = _expanded_families_for_records(all_spawns)
    matched_families: list[str] = []
    for family in _MODEL_FAMILY_TOKENS:
        if re.search(r"\b" + re.escape(family) + r"\b", window, re.IGNORECASE):
            if family.casefold() not in covered_families:
                matched_families.append(family)
    if not matched_families:
        return findings

    sorted_families = sorted(set(matched_families))
    fid = fingerprint_finding(
        "verify_before_replying.execution_claims",
        "fabricated_execution",
        canonical_value="fabricated_execution|" + "|".join(sorted_families),
    )
    family_label = ", ".join(sorted_families)
    findings.append(VerifyFinding(
        finding_id=fid,
        rule_id="verify_before_replying.execution_claims",
        confidence="high",
        claim_class="fabricated_execution",
        claim_text=claim_match.group(0),
        span=(claim_match.start(), claim_match.end()),
        evidence_refs=(),
        expected=f"SpawnAgent record matching '{family_label}'",
        observed="no SpawnAgent evidence for that model this session",
        detail=(
            f"reply claims a '{family_label}' subagent delegation but no "
            f"SpawnAgent record matches that model this session"
        ),
        suggested_action="recheck",
    ))
    return findings


def execution_claims_findings(
    final_text: str,
    turn_records: Sequence[Any],
    session_records: Sequence[Any],
    *,
    collector_present: bool,
) -> tuple[VerifyFinding, ...]:
    """Deterministic execution-claims audit against the spawn ledger (design 2).

    Two HIGH sub-checks over the turn+session record union (2.3):
    - fabricated_execution (absence-based, arms 1a generic + 1b model-named).
    - failed_execution_presented_as_success (contradiction-based, conservative).

    FPR-0 guards (2.7): collector_present=False returns () entirely; sub-check 1
    additionally requires at least one first-party record (producer-liveness),
    so a producer-off gap is not read as a lie. Sub-check 2 needs no such guard
    because it only ever fires ON an existing spawn record.
    """
    if not collector_present:
        return ()

    all_records = list(turn_records) + list(session_records)
    findings: list[VerifyFinding] = []

    # Producer-liveness guard for sub-check 1 (2.7): a collector can be present
    # while the first-party producer is off. Zero first-party records then means
    # a producer gap, not a fabrication.
    producer_live = any(
        _record_type(r).startswith(_FIRST_PARTY_TYPE_PREFIX) for r in all_records
    )
    if producer_live:
        findings.extend(_check_fabricated_execution(final_text, all_records))

    # Sub-check 2 only fires on an existing spawn record; no liveness guard.
    findings.extend(_check_failed_execution_success(final_text, all_records))

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
    all_findings.extend(
        execution_claims_findings(
            final_text, turn_records, session_records,
            collector_present=collector_present,
        )
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

    for f in execution_claims_findings(
        delivered_text, turn_records, session_records, collector_present=collector_present
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


def build_backstop_repair_message(
    high_findings: Sequence[VerifyFinding],
    *,
    attempt: int,
    max_attempts: int,
) -> str:
    """Render the DIRECTIVE repair message for the repair_high/block_high backstop.

    Unlike ``build_nudge_message`` (advisory, offers a SHIP_AS_IS escape), this
    is a hard directive: the pass produced crisp high-confidence, evidence-backed
    findings, so the model MUST revise or ground them before the answer is
    accepted. There is no SHIP_AS_IS escape while the bounded attempt budget
    remains. Only high-confidence findings are passed in (the caller filters);
    advisory findings never reach the backstop.
    """
    lines: list[str] = []
    lines.append('<verify_before_replying backstop="repair_high">')
    lines.append(
        f"Your reply above failed the evidence backstop (attempt {attempt} of "
        f"{max_attempts}). The following high-confidence findings are backed by "
        "this session's evidence ledger -- they are not heuristics. You must "
        "revise the reply to fix or explicitly ground each one before it can be "
        "delivered."
    )
    lines.append("")
    lines.append("VERIFIED ISSUES YOU MUST ADDRESS (evidence-backed, high confidence):")
    counter = 0
    for f in high_findings:
        counter += 1
        ref_str = f.evidence_refs[0] if f.evidence_refs else "no ref"
        obs_str = f.observed or ""
        exp_str = f.expected or ""
        entry_lines = [
            f'{counter}. [{f.rule_id}] Your reply states "{f.claim_text}"'
            f" (chars {f.span[0]}-{f.span[1]}).",
        ]
        if obs_str:
            entry_lines.append(
                f"   Observed: {obs_str}  Expected: {exp_str}  (evidence: {ref_str})"
            )
        else:
            entry_lines.append(f"   (evidence: {ref_str})")
        entry_lines.append(f"   Required: {f.suggested_action}.")
        lines.append("\n".join(entry_lines))
    lines.append("")
    lines.append(
        "Emit the corrected reply now, or use tools to gather the missing "
        "evidence first. Do NOT respond with SHIP_AS_IS: these findings are "
        "evidence-backed and must be resolved, not acknowledged."
    )
    lines.append("</verify_before_replying>")
    return "\n".join(lines)


def build_backstop_block_notice(high_findings: Sequence[VerifyFinding]) -> str:
    """Render the exhaustion notice for the ``block_high`` backstop.

    Reached only when the ``block_high`` mode has spent its bounded repair
    budget and the pass STILL produces crisp high-confidence findings. Rather
    than silently shipping an unresolved evidence-backed contradiction, the model
    is directed to deliver an HONEST answer that states the unresolved issue
    (never to fabricate). This is a directive continuation, not a hard runtime
    block (the driver contract returns a message, not a terminal); it keeps the
    turn honest at the end of the budget without a dead-end.
    """
    lines: list[str] = []
    lines.append('<verify_before_replying backstop="block_high" exhausted="true">')
    lines.append(
        "The evidence backstop budget is exhausted and these high-confidence, "
        "evidence-backed findings are still unresolved. Do NOT ship the reply as "
        "if they were resolved. Produce a final answer that states plainly what "
        "could not be verified or is contradicted by the evidence, and give only "
        "the parts you can support. Never fabricate to fill the gap."
    )
    lines.append("")
    lines.append("UNRESOLVED VERIFIED ISSUES:")
    counter = 0
    for f in high_findings:
        counter += 1
        ref_str = f.evidence_refs[0] if f.evidence_refs else "no ref"
        lines.append(
            f'{counter}. [{f.rule_id}] "{f.claim_text}" '
            f"(chars {f.span[0]}-{f.span[1]}; evidence: {ref_str})."
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
