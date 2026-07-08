"""Deterministic pre-final citation gate: high-risk claim detector plus gate
evaluation (Wave 4a, design Sections 11.1 and 11.2).

Pure and deterministic: no env reads, no I/O, no model call. The caller
(engine driver) gates on the flags and decides what to do with the verdict; in
Wave 4a the gate runs in AUDIT mode only (it emits a verdict record and never
alters the turn). Repair and induce-search are Wave 4b.

Two pieces live here:

* Piece A, ``detect_high_risk_claims``: a multi-claim sentence scanner that
  extends the ``grounded_answer_guard`` number machinery and the
  ``claim_grounding.ClaimType`` semantics. It segments the answer into
  sentences (fenced code excluded entirely), classifies each into a high-risk
  class or none, and records whether the sentence carries its own in-sentence
  canonical ``[src_N]`` marker (strictly per-sentence per OQ2).
* Piece B, ``evaluate_citation_gate``: takes the candidate final text, a
  registry snapshot, the per-turn source ids, and the user input, and produces
  ordered violations plus a deterministic verdict aligned with the Wave 3a
  render verdict value set (``cited`` / ``partial`` / ``uncited`` /
  ``not_applicable``).

No em-dashes anywhere in this module per the citation feature style rule.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

# Reuse (do not fork) the canonical number regex from the fabrication detector
# and the canonical src_N citation regex from the research final gate, so the
# gate never diverges from those governance surfaces.
from magi_agent.evidence.research_final_gate import _FINAL_ANSWER_SOURCE_REF_RE
from magi_agent.research.grounded_answer_guard import _NUMBER_RE, _normalize_digits

HighRiskClass = Literal["numeric", "date", "quote", "superlative"]
CitationGateViolationKind = Literal[
    "dangling_ref",
    "uncited_high_risk_zero_source",
    "uncited_high_risk",
]
CitationGateVerdict = Literal["cited", "partial", "uncited", "not_applicable"]
CitationRepairKind = Literal["attribution", "induce_search"]

__all__ = [
    "HighRiskClass",
    "HighRiskClaim",
    "CitationGateViolationKind",
    "CitationGateViolation",
    "CitationGateVerdict",
    "CitationGateResult",
    "CitationRepairKind",
    "CitationRepairPlan",
    "detect_high_risk_claims",
    "evaluate_citation_gate",
    "corpus_texts_from_snapshot",
    "plan_citation_repair",
    "build_attribution_repair_message",
    "build_induce_search_repair_message",
    "build_citation_fail_open_notice",
    "build_citation_fail_open_notice_block",
    "CITATION_HEDGE_SENTINEL",
]

# --- detector regexes (built on the reused number machinery) -----------------

# A currency / magnitude / percent figure is high-risk even below the 3
# significant-digit bar (e.g. "$5B", "3%"). Digits optionally grouped, optional
# decimal, optional magnitude suffix or percent, optional leading currency mark.
_CURRENCY_MAG_RE = re.compile(
    r"""
    (?:[$£€¥]\s?\d[\d,]*(?:\.\d+)?         # $12.77 , $5
       (?:\s?(?:[KMBT]\b|bn|mn|billion|million|trillion|thousand))?)
    |
    (?:\b\d[\d,]*(?:\.\d+)?\s?%)                          # 44.1% , 3%
    |
    (?:\b\d[\d,]*(?:\.\d+)?\s?                            # 8 billion , 5K
       (?:percent|[KMBT]|bn|mn|billion|million|trillion|thousand)\b)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# An explicit date token: bare 4-digit year, "Q<n> <year>", ISO-ish dates, and
# month names optionally followed by a day/year. Deliberately conservative.
_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|"
    "november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
_DATE_RE = re.compile(
    rf"""
    (?:\bQ[1-4]\s?(?:19|20)\d{{2}}\b)                    # Q1 2026
    | (?:\b(?:19|20)\d{{2}}-\d{{1,2}}-\d{{1,2}}\b)       # 2026-01-05
    | (?:\b(?:{_MONTHS})\b\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+(?:19|20)\d{{2}})?)
    | (?:\b\d{{1,2}}\s+(?:{_MONTHS})\b\.?(?:,?\s+(?:19|20)\d{{2}})?)
    | (?:\b(?:19|20)\d{{2}}\b)                            # bare year
    """,
    re.IGNORECASE | re.VERBOSE,
)
_ASSERTIVE_VERB_RE = re.compile(
    r"\b(?:is|are|was|were|has|have|had|reported|announced|posted|reached|"
    r"rose|fell|grew|declined|increased|decreased|recorded|hit|earned|"
    r"generated|reports|said|stated)\b",
    re.IGNORECASE,
)

_ATTRIBUTION_CUE_RE = re.compile(
    r"\b(?:said|says|stated|states|wrote|writes|told|tells|according to|"
    r"remarked|noted|declared|announced|added|explained)\b",
    re.IGNORECASE,
)
_QUOTE_SPAN_RE = re.compile(r"[\"“‘]([^\"“”‘’]+)[\"”’]")
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][A-Za-z0-9.&'-]+\b")
_SUPERLATIVE_RE = re.compile(
    r"\bthe\s+(?:largest|biggest|smallest|highest|lowest|first|only|last|"
    r"best|worst|fastest|slowest|most|least|leading|top|greatest|"
    r"number\s+one|no\.?\s*1)\b",
    re.IGNORECASE,
)

# Common single-word sentence starters that are capitalized but not entities.
_SENTENCE_STARTER_STOPWORDS = frozenset(
    {
        "the", "this", "that", "these", "those", "it", "its", "their", "our",
        "a", "an", "in", "on", "at", "for", "with", "and", "but", "or", "as",
        "we", "they", "he", "she", "you", "i", "to", "of", "by", "from",
        "here", "there", "when", "while", "if", "then", "so", "because",
    }
)


@dataclass(frozen=True)
class HighRiskClaim:
    """One high-risk claim sentence detected in the final answer.

    ``claim_class`` is the detected high-risk class. ``start``/``end`` are the
    character span into the ORIGINAL ``final_text``. ``has_marker`` is True when
    an in-sentence canonical ``src_N`` marker is present (strictly per-sentence:
    a marker on a neighboring sentence does NOT set this). ``corpus_supported``
    is True when every numeric figure in the sentence appears verbatim in the
    registered source corpus; it is informational (the strict marker check still
    wants a marker) and never suppresses.
    """

    claim_class: HighRiskClass
    start: int
    end: int
    text: str
    has_marker: bool
    corpus_supported: bool = False


@dataclass(frozen=True)
class CitationGateViolation:
    """One gate violation. ``kind`` orders severity (see design 11.2)."""

    kind: CitationGateViolationKind
    detail: str
    refs: tuple[str, ...] = ()
    claims: tuple[HighRiskClaim, ...] = ()


@dataclass(frozen=True)
class CitationGateResult:
    """Deterministic gate evaluation result for one final answer."""

    verdict: CitationGateVerdict
    violations: tuple[CitationGateViolation, ...] = ()
    high_risk_claims: tuple[HighRiskClaim, ...] = ()
    cited_claims: int = 0
    dangling_refs: tuple[str, ...] = ()
    zero_source_turn: bool = False


# --- Piece A: high-risk claim detector ---------------------------------------


def _fenced_code_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of fenced code blocks (```...```), to exclude entirely."""
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"```.*?```", text, re.DOTALL):
        spans.append((match.start(), match.end()))
    return spans


def _overlaps(start: int, end: int, spans: Sequence[tuple[int, int]]) -> bool:
    for span_start, span_end in spans:
        if start < span_end and end > span_start:
            return True
    return False


def _segment_sentences(text: str) -> list[tuple[int, int, str]]:
    """Segment ``text`` into (start, end, sentence) triples with char offsets.

    Conservative splitter: a run of ``.``/``!``/``?`` ends a sentence ONLY when
    followed by whitespace or end of string (so "44.1%" and "$12.77B" are not
    split at the decimal point). Newlines always end a segment, so a table row
    or a list item is its own unit. Blank segments are dropped.
    """
    segments: list[tuple[int, int, str]] = []
    length = len(text)
    start = 0
    i = 0
    while i < length:
        char = text[i]
        if char == "\n":
            if i > start:
                segments.append((start, i, text[start:i]))
            start = i + 1
            i += 1
            continue
        if char in ".!?":
            j = i + 1
            while j < length and text[j] in ".!?":
                j += 1
            if j >= length or text[j] in " \t\n":
                segments.append((start, j, text[start:j]))
                start = j
                i = j
                continue
        i += 1
    if start < length:
        segments.append((start, length, text[start:]))
    return [(s, e, seg) for (s, e, seg) in segments if seg.strip()]


def _sentence_numbers(sentence: str) -> list[str]:
    """Raw number tokens in a sentence (via the reused number regex)."""
    return [match.group(0).strip() for match in _NUMBER_RE.finditer(sentence)]


def _has_specific_number(sentence: str) -> bool:
    """True when a number with >=3 significant digits is present."""
    for match in _NUMBER_RE.finditer(sentence):
        digits = _normalize_digits(match.group(0))
        if len(digits.lstrip("0").replace(".", "")) >= 3:
            return True
    return False


def _has_named_entity(sentence: str) -> bool:
    """True when the sentence carries a capitalized proper-noun token that is
    not merely the (capitalized) first word acting as a stopword."""
    for index, match in enumerate(_PROPER_NOUN_RE.finditer(sentence)):
        token = match.group(0)
        is_first = match.start() == len(sentence) - len(sentence.lstrip())
        if is_first and token.lower() in _SENTENCE_STARTER_STOPWORDS:
            continue
        if index == 0 and is_first and token.lower() in _SENTENCE_STARTER_STOPWORDS:
            continue
        return True
    return False


def _classify_sentence(sentence: str) -> HighRiskClass | None:
    """Classify a sentence into a high-risk class, or None (advisory only).

    Priority: quote > superlative > date > numeric, so a quoted or superlative
    sentence keeps its more specific class even when it also carries figures.
    """
    # quote: a quoted span of 5+ words attributed to a named entity.
    for quote_match in _QUOTE_SPAN_RE.finditer(sentence):
        inner = quote_match.group(1).strip()
        if len(inner.split()) >= 5 and (
            _ATTRIBUTION_CUE_RE.search(sentence) or _has_named_entity(sentence)
        ):
            return "quote"
    # superlative: "the largest/first/only ..." plus a named entity.
    if _SUPERLATIVE_RE.search(sentence) and _has_named_entity(sentence):
        return "superlative"
    # dated fact: an explicit date plus an assertive verb.
    if _DATE_RE.search(sentence) and _ASSERTIVE_VERB_RE.search(sentence):
        return "date"
    # numeric: a specific figure, or any currency / magnitude / percent figure.
    if _has_specific_number(sentence) or _CURRENCY_MAG_RE.search(sentence):
        return "numeric"
    return None


def corpus_texts_from_snapshot(snapshot: Sequence[object]) -> tuple[str, ...]:
    """Collect corpus strings (title, uri, snippets) from a registry snapshot.

    Used for the informational ``corpus_supported`` flag: figures appearing
    verbatim here are corpus-supported. Never raises; unreadable records
    contribute nothing.
    """
    texts: list[str] = []
    for record in snapshot:
        for attr in ("title", "uri"):
            value = getattr(record, attr, None)
            if isinstance(value, str) and value:
                texts.append(value)
        snippets = getattr(record, "snippets", None)
        if isinstance(snippets, (list, tuple)):
            texts.extend(str(item) for item in snippets if item)
    return tuple(texts)


def _figures_all_present(figures: Sequence[str], corpus_text: str) -> bool:
    """True when every figure token appears (separator-agnostic) in the corpus.

    Empty ``figures`` returns False (no numeric basis to suppress on).
    """
    if not figures:
        return False
    corpus_numbers = {
        _normalize_digits(match.group(0)) for match in _NUMBER_RE.finditer(corpus_text)
    }
    return all(_normalize_digits(figure) in corpus_numbers for figure in figures)


def detect_high_risk_claims(
    final_text: str | None,
    *,
    user_input: str = "",
    corpus_texts: Sequence[str] = (),
) -> tuple[HighRiskClaim, ...]:
    """Scan ``final_text`` for high-risk claim sentences (design 11.1).

    ``user_input`` supplies the suppression corpus: a NUMERIC sentence whose
    figures all appear verbatim in the user's own turn input is NOT high-risk
    (the user gave the numbers). ``corpus_texts`` (registered source snippets /
    titles) drives the informational ``corpus_supported`` flag only; it never
    suppresses. Fenced code is excluded entirely.
    """
    text = final_text or ""
    if not text.strip():
        return ()
    code_spans = _fenced_code_spans(text)
    user_text = user_input or ""
    corpus_joined = "\n".join(str(item) for item in corpus_texts)

    claims: list[HighRiskClaim] = []
    for start, end, sentence in _segment_sentences(text):
        if _overlaps(start, end, code_spans):
            continue
        claim_class = _classify_sentence(sentence)
        if claim_class is None:
            continue
        figures = _sentence_numbers(sentence)
        # Suppression: numeric sentence whose figures all came from the user.
        if claim_class == "numeric" and _figures_all_present(figures, user_text):
            continue
        corpus_supported = (
            claim_class == "numeric" and _figures_all_present(figures, corpus_joined)
        )
        has_marker = _FINAL_ANSWER_SOURCE_REF_RE.search(sentence) is not None
        claims.append(
            HighRiskClaim(
                claim_class=claim_class,
                start=start,
                end=end,
                text=sentence,
                has_marker=has_marker,
                corpus_supported=corpus_supported,
            )
        )
    return tuple(claims)


# --- Piece B: gate evaluation ------------------------------------------------


def _cited_refs_in_order(final_text: str) -> list[str]:
    """Canonical src_N refs in first-appearance order (deduped)."""
    seen: dict[str, None] = {}
    for ref in _FINAL_ANSWER_SOURCE_REF_RE.findall(final_text or ""):
        seen.setdefault(ref, None)
    return list(seen)


def _compute_verdict(
    *,
    high_risk: Sequence[HighRiskClaim],
    resolvable_cited: int,
    dangling: int,
) -> CitationGateVerdict:
    """Deterministic verdict in the Wave 3a value set (design 11.2).

    * ``not_applicable``: nothing to govern (no high-risk claims, no citations).
    * ``cited``: every high-risk claim carries a marker and no dangling refs.
    * ``partial``: some attribution present but some high-risk uncited or a
      dangling ref alongside a resolvable one.
    * ``uncited``: high-risk claims (or a dangling ref) with no real attribution.
    """
    uncited_hr = sum(1 for claim in high_risk if not claim.has_marker)
    has_attribution = resolvable_cited > 0
    if not high_risk and dangling == 0 and resolvable_cited == 0:
        return "not_applicable"
    if uncited_hr == 0 and dangling == 0:
        return "cited"
    if has_attribution and (uncited_hr > 0 or dangling > 0):
        return "partial"
    return "uncited"


def evaluate_citation_gate(
    final_text: str | None,
    *,
    registry_snapshot: Sequence[object] = (),
    per_turn_source_ids: Sequence[str] = (),
    user_input: str = "",
) -> CitationGateResult:
    """Evaluate the deterministic pre-final citation gate (design 11.2).

    Produces violations in severity order:

    1. ``dangling_ref``: a cited id absent from the registry snapshot.
    2. ``uncited_high_risk_zero_source``: at least one high-risk claim while the
       turn registered ZERO in-scope external-read sources (the Tesla case).
    3. ``uncited_high_risk``: a high-risk sentence with no in-sentence marker
       while sources exist.

    The verdict is authoritative (Wave 4 supersedes the render verdict) and uses
    the same 4-value string set so the UI label mapping does not break.
    """
    text = final_text or ""
    known_ids = {
        str(getattr(record, "source_id", "")) for record in registry_snapshot
    }
    known_ids.discard("")

    cited_refs = _cited_refs_in_order(text)
    dangling = tuple(ref for ref in cited_refs if ref not in known_ids)
    resolvable_cited = sum(1 for ref in cited_refs if ref in known_ids)

    corpus_texts = corpus_texts_from_snapshot(registry_snapshot)
    high_risk = detect_high_risk_claims(
        text, user_input=user_input, corpus_texts=corpus_texts
    )
    cited_claims = sum(1 for claim in high_risk if claim.has_marker)
    uncited_claims = tuple(claim for claim in high_risk if not claim.has_marker)

    # A turn is zero-source when it registered no in-scope external reads. The
    # per-turn set is the ground truth for the Tesla case; fall back to the
    # session snapshot being empty so a session with sources but none this turn
    # is not spuriously flagged zero-source when the snapshot is otherwise empty.
    zero_source_turn = len(per_turn_source_ids) == 0 and len(known_ids) == 0

    violations: list[CitationGateViolation] = []
    if dangling:
        violations.append(
            CitationGateViolation(
                kind="dangling_ref",
                detail=(
                    "answer cites source id(s) not in the registry: "
                    + ", ".join(dangling)
                ),
                refs=dangling,
            )
        )
    if uncited_claims:
        if zero_source_turn:
            violations.append(
                CitationGateViolation(
                    kind="uncited_high_risk_zero_source",
                    detail=(
                        f"{len(uncited_claims)} high-risk claim(s) with no source: "
                        "the turn registered zero external-read sources"
                    ),
                    claims=uncited_claims,
                )
            )
        else:
            violations.append(
                CitationGateViolation(
                    kind="uncited_high_risk",
                    detail=(
                        f"{len(uncited_claims)} high-risk claim(s) lack an "
                        "in-sentence source marker while sources exist"
                    ),
                    claims=uncited_claims,
                )
            )

    verdict = _compute_verdict(
        high_risk=high_risk,
        resolvable_cited=resolvable_cited,
        dangling=len(dangling),
    )

    return CitationGateResult(
        verdict=verdict,
        violations=tuple(violations),
        high_risk_claims=high_risk,
        cited_claims=cited_claims,
        dangling_refs=dangling,
        zero_source_turn=zero_source_turn,
    )


# --- Wave 4b: repair planning + repair-message builders ----------------------
#
# All pure and deterministic (no env reads, no I/O, no model call). The engine
# driver reads the flags, resolves tool availability from the runtime, calls
# ``plan_citation_repair`` to pick a repair kind (or degrade to advisory), and
# builds the repair instruction / fail-open notice from these builders. The
# builders emit stable bytes for a given input so within-turn retry paths do not
# thrash the prompt cache.


@dataclass(frozen=True)
class CitationRepairPlan:
    """What the pre-final citation gate should do about a violated turn.

    * ``kind`` is the repair family to drive through the existing driver repair
      loop: ``"attribution"`` (a marker is missing but a valid source exists, so
      re-emit with the marker attached, no new tool call) or ``"induce_search"``
      (high-risk claims on a zero-external-read turn, so search then re-answer).
    * ``degrade_to_advisory`` is True when a repair is warranted in principle but
      is impossible or disabled (induce-search off, or no web/KB tool bound on a
      keyless install): the gate must never demand an impossible action, so the
      turn completes with the advisory ``advisory_verdict`` and no forced work.
      When ``degrade_to_advisory`` is True, ``kind`` is None.
    """

    kind: CitationRepairKind | None
    degrade_to_advisory: bool = False
    advisory_verdict: CitationGateVerdict = "uncited"
    induced_search: bool = False


def plan_citation_repair(
    result: CitationGateResult,
    *,
    web_available: bool,
    kb_available: bool,
    induce_search_enabled: bool,
) -> CitationRepairPlan | None:
    """Pick the repair action for a gate result (design 11.3).

    Returns None when nothing warrants repair (no violations). Otherwise:

    * A ``uncited_high_risk_zero_source`` violation (the Tesla case) drives an
      INDUCE-SEARCH repair, UNLESS induce-search is disabled OR no search tool is
      bound (``web_available`` and ``kb_available`` both False), in which case it
      DEGRADES to the advisory ``uncited`` verdict with no forced search. On a
      zero-source turn there is no valid id to attribute to, so attribution is
      never the remedy there.
    * A ``dangling_ref`` or ``uncited_high_risk`` violation (sources exist)
      drives an ATTRIBUTION repair: re-emit with the marker attached.

    Determinism: pure function of the result plus the three runtime booleans.
    """
    kinds = {violation.kind for violation in result.violations}
    if not kinds:
        return None
    if "uncited_high_risk_zero_source" in kinds:
        can_search = induce_search_enabled and (web_available or kb_available)
        if can_search:
            return CitationRepairPlan(kind="induce_search", induced_search=True)
        return CitationRepairPlan(
            kind=None, degrade_to_advisory=True, advisory_verdict="uncited"
        )
    if "dangling_ref" in kinds or "uncited_high_risk" in kinds:
        return CitationRepairPlan(kind="attribution")
    return None


def _valid_source_lines(
    registry_snapshot: Sequence[object], *, limit: int = 20
) -> list[str]:
    """Deterministic ``[src_N] <title>`` id list for the attribution message."""
    lines: list[str] = []
    for record in registry_snapshot:
        source_id = str(getattr(record, "source_id", "")).strip()
        if not source_id:
            continue
        title = getattr(record, "title", None)
        uri = getattr(record, "uri", None)
        label = ""
        if isinstance(title, str) and title.strip():
            label = title.strip()
        elif isinstance(uri, str) and uri.strip():
            label = uri.strip()
        lines.append(f"[{source_id}] {label}".rstrip())
        if len(lines) >= limit:
            break
    return lines


def _offending_sentences(result: CitationGateResult, *, limit: int = 12) -> list[str]:
    """The uncited high-risk sentences (deterministic order, trimmed)."""
    sentences: list[str] = []
    for claim in result.high_risk_claims:
        if claim.has_marker:
            continue
        text = " ".join(claim.text.split())
        if text:
            sentences.append(text)
        if len(sentences) >= limit:
            break
    return sentences


def build_attribution_repair_message(
    result: CitationGateResult, registry_snapshot: Sequence[object]
) -> str:
    """ATTRIBUTION repair instruction: attach a marker, no new tool call.

    Lists the offending sentences and the valid id list with titles, and asks
    the model to re-emit the SAME answer with a ``[src_N]`` marker immediately
    after each listed claim. Dangling ids (cited but not registered) are named so
    the model drops or corrects them.
    """
    parts: list[str] = [
        "Your previous answer has high-risk claims (figures, dates, quotes, or "
        "named superlatives) that are not attributed to a registered source. "
        "Re-emit the SAME answer, unchanged in substance, but place a [src_N] "
        "marker immediately after each of these claims. Do NOT invent ids: use "
        "only ids from the valid list below."
    ]
    offending = _offending_sentences(result)
    if offending:
        parts.append("Claims that need a source marker:")
        parts.extend(f"  - {sentence}" for sentence in offending)
    if result.dangling_refs:
        parts.append(
            "These cited ids are not registered and must be removed or replaced "
            "with a valid id: " + ", ".join(result.dangling_refs)
        )
    valid = _valid_source_lines(registry_snapshot)
    if valid:
        parts.append("Valid source ids you may cite:")
        parts.extend(f"  {line}" for line in valid)
    else:
        parts.append("No registered sources are available to cite.")
    return "\n".join(parts)


def build_induce_search_repair_message(result: CitationGateResult) -> str:
    """INDUCE-SEARCH repair instruction: ground the claims before asserting.

    Directs the model to call ``research_fact`` / ``web_search`` /
    ``KnowledgeSearch`` to find a source for each unsupported figure FIRST, then
    re-answer citing the ``[src_N]`` ids those tools return. Used only for the
    zero-external-read high-risk case (the Tesla report).
    """
    parts: list[str] = [
        "Your previous answer asserts specific figures, dates, or named facts "
        "but this turn registered NO external sources, so none of them are "
        "grounded. Before re-answering, call a research tool (research_fact, "
        "web_search, or KnowledgeSearch) to find a source for each figure. Then "
        "re-answer and place a [src_N] marker (an id returned by those tools) "
        "immediately after each figure. Do not assert a specific figure you "
        "could not source."
    ]
    offending = _offending_sentences(result)
    if offending:
        parts.append("Unsupported claims to ground first:")
        parts.extend(f"  - {sentence}" for sentence in offending)
    return "\n".join(parts)


def build_citation_fail_open_notice(result: CitationGateResult) -> str:
    """Deterministic one-line fail-open hedge appended after the answer.

    Format: ``Contains unverified figures; no source was available for: ...``.
    Follows the soft-hedge append precedent (a single trailing sentence, never a
    mutation of already-streamed text). The list is a short deterministic
    excerpt of the still-unsupported claims.
    """
    fragments: list[str] = []
    for claim in result.high_risk_claims:
        if claim.has_marker:
            continue
        text = " ".join(claim.text.split())
        if not text:
            continue
        if len(text) > 80:
            text = text[:77].rstrip() + "..."
        fragments.append(text)
        if len(fragments) >= 3:
            break
    if not fragments and result.dangling_refs:
        fragments = [f"unresolved citation {ref}" for ref in result.dangling_refs[:3]]
    if not fragments:
        detail = "one or more claims"
    else:
        detail = "; ".join(fragments)
    return f"Contains unverified figures; no source was available for: {detail}"


#: Deterministic sentinel that tags the fail-open hedge so the frontend can
#: render it as a distinguished (muted / non-alarming) callout instead of plain
#: answer prose. GitHub-alert admonition syntax the answer body never emits on
#: its own, so the frontend match cannot false-positive on normal text.
CITATION_HEDGE_SENTINEL = "[!citation-hedge]"


def build_citation_fail_open_notice_block(result: CitationGateResult) -> str:
    """Fail-open hedge wrapped as a sentinel-tagged markdown blockquote.

    Same one-line hedge as ``build_citation_fail_open_notice``, but emitted as a
    blockquote whose first line carries ``CITATION_HEDGE_SENTINEL`` so the
    renderer detects it deterministically (design GAP #5). Still a pure trailing
    SUFFIX (never a mutation of already-streamed text), and the underlying text
    is preserved verbatim so a non-callout renderer degrades to a readable
    blockquote rather than losing the hedge."""
    notice = build_citation_fail_open_notice(result)
    return f"> {CITATION_HEDGE_SENTINEL}\n> {notice}"
