"""Rule-based query planner for deep web research.

Generates multiple query variants from a single question without any LLM call,
keeping latency and cost additions at zero.  The planner is intentionally
conservative: it produces a small set of high-signal variants rather than a
noisy long list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from magi_agent.web_acquisition.deep_research_config import DeepResearchConfig

# Patterns that identify numeric / count questions
_NUMERIC_PATTERNS = re.compile(
    r"\b(how\s+many|count|number\s+of|total|how\s+much|average|mean|"
    r"rank(?:ing)?|top\s+\d+|list\s+of|table\s+of)\b",
    re.IGNORECASE,
)
_YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")
_DATE_WORDS = re.compile(r"\b(first|last|when|year|date|since|before|after|in\s+\d{4})\b", re.IGNORECASE)

# Authority-site hints: domain keywords that, when found in the question, help
# generate a site:-qualified variant without introducing a new LLM call.
_AUTHORITY_HINTS: dict[str, str] = {
    "box office mojo": "site:boxofficemojo.com",
    "orcid": "site:orcid.org",
    "wikipedia": "site:wikipedia.org",
    "google finance": "site:google.com/finance",
    "imdb": "site:imdb.com",
    "statista": "site:statista.com",
    "worldometer": "site:worldometers.info",
}


@dataclass(frozen=True)
class SearchQuery:
    """A single planned search query."""

    text: str
    variant_kind: str  # e.g. "original", "numeric_list", "site_qualified", "rephrased"


class QueryPlanner:
    """Generates ``SearchQuery`` variants from a research question.

    All variants are derived rule-based — no network calls, no LLM calls.
    The planner caps the output at ``config.max_queries`` items.
    """

    def plan(self, question: str, config: DeepResearchConfig) -> list[SearchQuery]:
        """Return up to ``config.max_queries`` search query variants."""
        queries: list[SearchQuery] = []
        seen: set[str] = set()

        def _add(text: str, kind: str) -> None:
            clean = " ".join(text.strip().split())
            if clean and clean not in seen and len(queries) < config.max_queries:
                queries.append(SearchQuery(text=clean, variant_kind=kind))
                seen.add(clean)

        # 1. Original question verbatim
        _add(question, "original")

        q_lower = question.lower()

        # 2. Authority-site qualified variant
        for hint_phrase, site_qualifier in _AUTHORITY_HINTS.items():
            if hint_phrase in q_lower and len(queries) < config.max_queries:
                _add(f"{question} {site_qualifier}", "site_qualified")
                break

        # 3. Numeric/list variant — "list of X" / "table of X" / "ranking X"
        if _NUMERIC_PATTERNS.search(question):
            # Strip leading "how many" / "how much" and prepend "list of"
            stripped = re.sub(
                r"^\s*(how\s+many|how\s+much|count\s+of|number\s+of)\s*",
                "",
                question,
                flags=re.IGNORECASE,
            ).strip()
            if stripped and stripped.lower() != question.lower():
                _add(f"list of {stripped}", "numeric_list")
            # Also add a "table of X" variant
            _add(f"table {question}", "numeric_table")
        else:
            # For non-numeric questions, add a "ranking" or count variant
            _add(f"ranking list {question}", "numeric_list")

        # 4. Date/year context variant — add "year history" or direct rephrasing
        if _DATE_WORDS.search(question) or _YEAR_PATTERN.search(question):
            year_match = _YEAR_PATTERN.search(question)
            if year_match:
                year = year_match.group(0)
                base = question.replace(year, "").strip()
                _add(f"{base} {year} official statistics", "temporal_stats")
            else:
                _add(f"{question} statistics history", "temporal_stats")

        # 5. Generic numeric rephrasing (covers remaining budget)
        if len(queries) < config.max_queries:
            _add(f"{question} count total number", "count_rephrase")

        return queries[: config.max_queries]


__all__ = [
    "QueryPlanner",
    "SearchQuery",
]
