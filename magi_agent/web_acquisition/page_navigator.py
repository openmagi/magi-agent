"""Page content navigator for deep web research.

Extracts the most-relevant section, table, or list from a markdown page
given a research question.  All processing is text-only — no network
calls and no LLM calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|.+\|$")
_ORDERED_LIST_RE = re.compile(r"^\s*\d+\.\s+.+$")
_UNORDERED_LIST_RE = re.compile(r"^\s*[-*+]\s+.+$")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_NUMERIC_UNIT_RE = re.compile(r"[\$€£¥%#]|\b\d[\d,]*\.?\d*\b")
_WHITESPACE_RE = re.compile(r"\s+")


def _tokenize(text: str) -> frozenset[str]:
    """Lowercased word tokens (length ≥ 2) for overlap scoring."""
    return frozenset(
        w for w in _WHITESPACE_RE.split(text.lower()) if len(w) >= 2
    )


def _overlap(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractedSection:
    """A section/table/list extracted from a markdown page."""

    content: str
    section_kind: str  # "table", "list", "section", "year_context", "full"
    header: str | None = None
    confidence: float = 0.5


@dataclass
class ExtractedFact:
    """A concrete numeric or textual fact extracted from an ``ExtractedSection``."""

    value: str
    source_url_ref: str
    span_ref: str
    context_snippet: str
    confidence: float = 0.5

    def __hash__(self) -> int:
        return hash((self.value, self.source_url_ref, self.span_ref))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ExtractedFact):
            return NotImplemented
        return (
            self.value == other.value
            and self.source_url_ref == other.source_url_ref
            and self.span_ref == other.span_ref
        )


# ---------------------------------------------------------------------------
# PageNavigator
# ---------------------------------------------------------------------------


class PageNavigator:
    """Extracts the most relevant content block from a markdown page.

    Strategy (priority order):
    1. Markdown table containing question keywords
    2. Ordered/unordered list containing question keywords
    3. Section (under a header) with highest keyword overlap
    4. Year/date context lines (±3 lines around matching year)
    5. Numeric unit context (lines near ``$``, ``%``, ``#``)
    6. Full trimmed page (fallback)
    """

    def extract_target(
        self,
        markdown: str,
        question: str,
        *,
        max_chars: int = 4096,
    ) -> ExtractedSection:
        """Return the best matching ``ExtractedSection`` for *question*."""
        if not markdown or not markdown.strip():
            return ExtractedSection(
                content="",
                section_kind="empty",
                confidence=0.0,
            )

        q_tokens = _tokenize(question)
        lines = markdown.splitlines()

        # 1. Table extraction
        table_result = self._extract_table(lines, q_tokens, max_chars)
        if table_result is not None:
            return table_result

        # 2. List extraction
        list_result = self._extract_list(lines, q_tokens, max_chars)
        if list_result is not None:
            return list_result

        # 3. Section extraction
        section_result = self._extract_section(lines, q_tokens, max_chars)
        if section_result is not None:
            return section_result

        # 4. Year context
        year_match = _YEAR_RE.search(question)
        if year_match:
            year = year_match.group(0)
            year_result = self._extract_year_context(lines, year, max_chars)
            if year_result is not None:
                return year_result

        # 5. Numeric unit context
        if _NUMERIC_UNIT_RE.search(question):
            num_result = self._extract_numeric_context(lines, q_tokens, max_chars)
            if num_result is not None:
                return num_result

        # 6. Fallback: full page
        full = markdown[:max_chars].strip()
        return ExtractedSection(content=full, section_kind="full", confidence=0.1)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_table(
        self,
        lines: list[str],
        q_tokens: frozenset[str],
        max_chars: int,
    ) -> ExtractedSection | None:
        """Find and return all contiguous table rows with keyword overlap.

        Scoring uses both the table cell content and the nearest header above
        the table, so a question like "box office 2020" can match a table whose
        header says "Worldwide Box Office 2020" even though the cell content
        only contains film titles and grosses.
        """
        # Find runs of table lines (lines starting and ending with |)
        best: list[str] | None = None
        best_score = 0.0
        current_run: list[str] = []
        header_above: str | None = None
        last_header: str | None = None
        # Lines between the last header and the current table run
        context_lines: list[str] = []

        def _score_run(run: list[str], header: str | None) -> float:
            combined = "\n".join(run)
            row_tokens = _tokenize(combined)
            # Include header tokens in overlap scoring
            if header:
                row_tokens = row_tokens | _tokenize(header)
            return _overlap(q_tokens, row_tokens)

        for line in lines:
            if line.startswith("#"):
                last_header = line.lstrip("#").strip()
                context_lines = []
            if _TABLE_ROW_RE.match(line.strip()):
                if not current_run:
                    header_above = last_header
                current_run.append(line)
            else:
                if current_run:
                    score = _score_run(current_run, header_above)
                    if score > best_score:
                        best_score = score
                        best = list(current_run)
                    current_run = []
                    header_above = None
                context_lines.append(line)

        # handle trailing table
        if current_run:
            score = _score_run(current_run, header_above or last_header)
            if score > best_score:
                best_score = score
                best = list(current_run)
                header_above = header_above or last_header

        if best is None:
            return None

        content = "\n".join(best)[:max_chars]
        return ExtractedSection(
            content=content,
            section_kind="table",
            header=header_above,
            confidence=min(0.9, 0.5 + best_score),
        )

    def _extract_list(
        self,
        lines: list[str],
        q_tokens: frozenset[str],
        max_chars: int,
    ) -> ExtractedSection | None:
        best: list[str] | None = None
        best_score = 0.0
        current_run: list[str] = []
        last_header: str | None = None
        run_header: str | None = None

        def _is_list_line(line: str) -> bool:
            return bool(_ORDERED_LIST_RE.match(line)) or bool(_UNORDERED_LIST_RE.match(line))

        def _score_run(run: list[str], header: str | None) -> float:
            combined = "\n".join(run)
            tokens = _tokenize(combined)
            if header:
                tokens = tokens | _tokenize(header)
            return _overlap(q_tokens, tokens)

        for line in lines:
            if line.startswith("#"):
                last_header = line.lstrip("#").strip()
            if _is_list_line(line):
                if not current_run:
                    run_header = last_header
                current_run.append(line)
            else:
                if current_run:
                    score = _score_run(current_run, run_header)
                    if score > best_score:
                        best_score = score
                        best = list(current_run)
                    current_run = []
                    run_header = None

        if current_run:
            score = _score_run(current_run, run_header or last_header)
            if score > best_score:
                best_score = score
                best = list(current_run)

        if best is None or best_score == 0.0:
            return None

        content = "\n".join(best)[:max_chars]
        return ExtractedSection(
            content=content,
            section_kind="list",
            header=last_header,
            confidence=min(0.85, 0.45 + best_score),
        )

    def _extract_section(
        self,
        lines: list[str],
        q_tokens: frozenset[str],
        max_chars: int,
    ) -> ExtractedSection | None:
        """Return the section (bounded by headers) with highest keyword overlap."""
        # Find all header positions
        header_positions: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            m = _HEADER_RE.match(line)
            if m:
                header_positions.append((i, m.group(2).strip()))

        if not header_positions:
            return None

        best_section: list[str] | None = None
        best_score = 0.0
        best_header: str | None = None

        # Score each section body
        for idx, (start, header_text) in enumerate(header_positions):
            end = header_positions[idx + 1][0] if idx + 1 < len(header_positions) else len(lines)
            body_lines = lines[start + 1 : end]
            body = "\n".join(body_lines).strip()
            if not body:
                continue
            # Use header + body tokens for matching
            combined_tokens = _tokenize(header_text + " " + body)
            score = _overlap(q_tokens, combined_tokens)
            if score > best_score:
                best_score = score
                best_section = body_lines
                best_header = header_text

        if best_section is None or best_score == 0.0:
            return None

        content = "\n".join(best_section)[:max_chars]
        return ExtractedSection(
            content=content,
            section_kind="section",
            header=best_header,
            confidence=min(0.8, 0.4 + best_score),
        )

    def _extract_year_context(
        self,
        lines: list[str],
        year: str,
        max_chars: int,
    ) -> ExtractedSection | None:
        """Return ±3 lines around any line containing *year*."""
        context_lines: list[str] = []
        for i, line in enumerate(lines):
            if year in line:
                start = max(0, i - 3)
                end = min(len(lines), i + 4)
                context_lines.extend(lines[start:end])

        if not context_lines:
            return None

        content = "\n".join(context_lines)[:max_chars]
        return ExtractedSection(
            content=content,
            section_kind="year_context",
            confidence=0.6,
        )

    def _extract_numeric_context(
        self,
        lines: list[str],
        q_tokens: frozenset[str],
        max_chars: int,
    ) -> ExtractedSection | None:
        """Return lines with numeric units that have keyword overlap."""
        matching: list[str] = []
        for line in lines:
            if _NUMERIC_UNIT_RE.search(line):
                line_tokens = _tokenize(line)
                if q_tokens & line_tokens:
                    matching.append(line)

        if not matching:
            return None

        content = "\n".join(matching)[:max_chars]
        return ExtractedSection(
            content=content,
            section_kind="numeric_context",
            confidence=0.5,
        )


# ---------------------------------------------------------------------------
# FactExtractor
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(
    r"\b(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+\.\d+|\d+)\s*"
    r"(?:%|million|billion|trillion|thousand|M\b|B\b|T\b|K\b)?",
    re.IGNORECASE,
)
_YEAR_ONLY_RE = re.compile(r"\b(19|20)\d{2}\b")


class FactExtractor:
    """Extracts candidate numeric/temporal facts from an ``ExtractedSection``.

    Rule-based, LLM-free.  Confidence is approximate.
    """

    def extract(
        self,
        section: ExtractedSection,
        *,
        source_url_ref: str,
        span_ref_prefix: str = "span",
    ) -> list[ExtractedFact]:
        """Return candidate facts from *section* sorted by confidence desc."""
        facts: list[ExtractedFact] = []
        seen_values: set[str] = set()

        for i, line in enumerate(section.content.splitlines()):
            line = line.strip()
            if not line:
                continue

            # Year facts
            for m in _YEAR_ONLY_RE.finditer(line):
                val = m.group(0)
                if val not in seen_values:
                    seen_values.add(val)
                    start = max(0, m.start() - 25)
                    end = min(len(line), m.end() + 25)
                    facts.append(
                        ExtractedFact(
                            value=val,
                            source_url_ref=source_url_ref,
                            span_ref=f"{span_ref_prefix}.{i}.year",
                            context_snippet=line[start:end],
                            confidence=0.6,
                        )
                    )

            # Numeric facts
            for m in _NUMBER_RE.finditer(line):
                raw = m.group(0).strip()
                # Normalise: strip commas, keep decimal
                normalised = re.sub(r",", "", m.group(1))
                if normalised not in seen_values and normalised:
                    seen_values.add(normalised)
                    start = max(0, m.start() - 25)
                    end = min(len(line), m.end() + 25)
                    facts.append(
                        ExtractedFact(
                            value=normalised,
                            source_url_ref=source_url_ref,
                            span_ref=f"{span_ref_prefix}.{i}.num",
                            context_snippet=line[start:end],
                            confidence=0.5,
                        )
                    )

        facts.sort(key=lambda f: f.confidence, reverse=True)
        return facts


__all__ = [
    "ExtractedFact",
    "ExtractedSection",
    "FactExtractor",
    "PageNavigator",
]
