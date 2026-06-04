from __future__ import annotations

from datetime import datetime, timezone

import pytest

from magi_agent.web_acquisition.policy import normalize_query


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW_2026 = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
_NOW_2030 = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1b-i  flag off → identical output to today
# ---------------------------------------------------------------------------

def test_recency_flag_off_is_identical_to_baseline() -> None:
    """inject_recency_year=False (default) must produce byte-for-byte identical output."""
    query = "latest AI models"
    result_default = normalize_query(query)
    result_explicit = normalize_query(query, inject_recency_year=False)

    assert result_default == result_explicit


def test_recency_flag_off_no_year_appended_for_recency_queries() -> None:
    """With flag off, recency-intent queries must NOT get a year appended."""
    result = normalize_query("latest news", inject_recency_year=False)
    # Should just be the normalized, no trailing year
    assert result == "latest news"


# ---------------------------------------------------------------------------
# 1b-ii  flag on + recency intent + no year → year appended deterministically
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "latest AI models",
    "recent breakthroughs in quantum computing",
    "current best practices for Python",
    "today's stock prices",
    "news about climate change",
    "this year best smartphones",
    "newest JavaScript frameworks",
    "up to date kubernetes documentation",
])
def test_recency_year_appended_for_recency_intent_queries(query: str) -> None:
    """All recency-intent keywords trigger year append when flag is on."""
    result = normalize_query(query, inject_recency_year=True, now=_NOW_2026)
    assert result.endswith(" 2026"), f"Expected ' 2026' suffix, got: {result!r}"


def test_recency_year_injected_deterministically_with_injected_now() -> None:
    """Injected now= makes the result deterministic for testing."""
    result_a = normalize_query("latest news", inject_recency_year=True, now=_NOW_2026)
    result_b = normalize_query("latest news", inject_recency_year=True, now=_NOW_2026)
    assert result_a == result_b == "latest news 2026"


def test_recency_year_uses_injected_now_year() -> None:
    """The appended year comes from the injected now parameter."""
    result = normalize_query("recent discoveries", inject_recency_year=True, now=_NOW_2030)
    assert result.endswith(" 2030")


def test_recency_year_normalized_first_then_appended() -> None:
    """Collapsing of whitespace happens first, then year is appended."""
    result = normalize_query("  latest   models  ", inject_recency_year=True, now=_NOW_2026)
    assert result == "latest models 2026"


# ---------------------------------------------------------------------------
# 1b-iii  flag on + query already has a year → no double-append
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query, baseline", [
    ("latest AI in 2024", "latest AI in 2024"),
    ("best models 2025", "best models 2025"),
    ("news from 2026", "news from 2026"),
    ("recent events 1999", "recent events 1999"),
    ("current trends 2030", "current trends 2030"),
])
def test_no_year_appended_when_query_already_contains_year(query: str, baseline: str) -> None:
    """If the query already has a 4-digit year (19xx/20xx), do not append another."""
    result_flag_on = normalize_query(query, inject_recency_year=True, now=_NOW_2026)
    result_flag_off = normalize_query(query, inject_recency_year=False)
    # With flag on, result should equal the flag-off result (no extra year appended)
    assert result_flag_on == result_flag_off, (
        f"Year was appended despite existing year in query. Got: {result_flag_on!r}"
    )
    # The existing year should still be present (not removed)
    import re
    assert re.search(r"\b(?:19|20)\d{2}\b", result_flag_on), "Existing year should be preserved"


# ---------------------------------------------------------------------------
# 1b-iv  flag on + no recency intent → no append
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "Python tutorial",
    "how to sort a list",
    "machine learning overview",
    "compare numpy and pandas",
    "what is a transformer model",
])
def test_no_year_appended_when_no_recency_intent(query: str) -> None:
    """Without a recency-intent keyword, no year is appended."""
    result = normalize_query(query, inject_recency_year=True, now=_NOW_2026)
    assert result == normalize_query(query, inject_recency_year=False)
    import re
    assert not re.search(r"\b20\d{2}\b", result), (
        f"No year should be appended for non-recency query: {result!r}"
    )


# ---------------------------------------------------------------------------
# 1b-v  append would exceed max_chars → skipped
# ---------------------------------------------------------------------------

def test_year_not_appended_when_it_would_exceed_max_chars() -> None:
    """If appending ' YYYY' would push length over max_chars, skip the append."""
    # Use a recency-intent query whose normalized form exactly fills max_chars
    # so there's no room for ' 2026' (5 chars)
    max_chars = 20
    # "latest" has recency intent; pad to exactly max_chars after normalization
    base = "latest"
    padding = "x" * (max_chars - len(base) - 1)  # leave 1 space between
    query = f"{base} {padding}"
    normalized = normalize_query(query, inject_recency_year=False, max_chars=max_chars)
    assert len(normalized) == max_chars, (
        f"Baseline query should fill max_chars exactly: {normalized!r} ({len(normalized)})"
    )

    result = normalize_query(query, inject_recency_year=True, now=_NOW_2026, max_chars=max_chars)
    assert len(result) <= max_chars, f"Result exceeds max_chars: {result!r}"
    assert not result.endswith(" 2026"), f"Year should not be appended: {result!r}"


def test_year_appended_when_it_fits_exactly() -> None:
    """If after normalization there's exactly room for ' YYYY', append it."""
    # "latest" + space + year = "latest 2026" = 11 chars
    result = normalize_query("latest", inject_recency_year=True, now=_NOW_2026, max_chars=11)
    assert result == "latest 2026"
    assert len(result) == 11


def test_year_appended_when_max_chars_is_large() -> None:
    """With generous max_chars, recency year is always appended."""
    result = normalize_query("current events", inject_recency_year=True, now=_NOW_2026, max_chars=512)
    assert result == "current events 2026"


# ---------------------------------------------------------------------------
# Baseline compatibility: existing redact/truncate discipline is preserved
# ---------------------------------------------------------------------------

def test_recency_year_respects_redact_discipline() -> None:
    """Year-append happens on already-redacted/normalized string."""
    # A query with a sensitive path but recency intent — path should be redacted
    result = normalize_query(
        "latest news from /home/user/data",
        inject_recency_year=True,
        now=_NOW_2026,
    )
    assert "/home/user" not in result
    assert result.endswith(" 2026")


def test_normalize_query_still_raises_on_empty_with_flag_on() -> None:
    """normalize_query still raises ValueError for empty queries even with flag on."""
    with pytest.raises(ValueError, match="query is required"):
        normalize_query("", inject_recency_year=True, now=_NOW_2026)
