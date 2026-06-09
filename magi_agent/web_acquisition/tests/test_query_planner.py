"""Tests for QueryPlanner — PR1 (TDD: these were written first)."""

from __future__ import annotations

from magi_agent.web_acquisition.deep_research_config import DeepResearchConfig
from magi_agent.web_acquisition.query_planner import QueryPlanner, SearchQuery


_CONFIG = DeepResearchConfig(enabled=True)
_CONFIG_MAX2 = DeepResearchConfig(enabled=True, max_queries=2)


def test_query_planner_produces_at_least_one() -> None:
    planner = QueryPlanner()
    queries = planner.plan("Apple stock first year above 100", _CONFIG)
    assert len(queries) >= 1


def test_query_planner_generates_variants() -> None:
    planner = QueryPlanner()
    queries = planner.plan("Apple stock first year above 100", _CONFIG)
    assert len(queries) >= 2
    assert any("Apple" in q.text for q in queries)


def test_query_planner_original_always_first() -> None:
    planner = QueryPlanner()
    queries = planner.plan("some question", _CONFIG)
    assert queries[0].variant_kind == "original"
    assert "some question" in queries[0].text


def test_query_planner_respects_max_queries() -> None:
    planner = QueryPlanner()
    queries = planner.plan("Apple stock first year above 100", _CONFIG_MAX2)
    assert len(queries) <= 2


def test_query_planner_adds_numeric_variant_for_count_question() -> None:
    planner = QueryPlanner()
    queries = planner.plan("how many top 10 films 2020 non-English", _CONFIG)
    texts = [q.text.lower() for q in queries]
    assert any(kw in t for t in texts for kw in ["list", "table", "ranking", "top"])


def test_query_planner_adds_source_hint_for_numeric() -> None:
    """Numeric questions should include list/table/ranking variants."""
    planner = QueryPlanner()
    queries = planner.plan("how many top 10 films 2020 non-English", _CONFIG)
    texts = [q.text.lower() for q in queries]
    assert any(kw in t for t in texts for kw in ["list", "table", "ranking"])


def test_query_planner_authority_hint_box_office() -> None:
    planner = QueryPlanner()
    queries = planner.plan("Box Office Mojo 2020 top films worldwide gross", _CONFIG)
    texts = [q.text for q in queries]
    assert any("boxofficemojo.com" in t for t in texts)


def test_query_planner_authority_hint_orcid() -> None:
    planner = QueryPlanner()
    queries = planner.plan("ORCID average works per researcher 2019", _CONFIG)
    texts = [q.text for q in queries]
    assert any("orcid.org" in t for t in texts)


def test_query_planner_no_duplicate_queries() -> None:
    planner = QueryPlanner()
    queries = planner.plan("Apple stock 2018", _CONFIG)
    texts = [q.text for q in queries]
    assert len(texts) == len(set(texts))


def test_query_planner_returns_list_of_search_query() -> None:
    planner = QueryPlanner()
    queries = planner.plan("some question", _CONFIG)
    assert all(isinstance(q, SearchQuery) for q in queries)


def test_query_planner_variant_kinds_are_strings() -> None:
    planner = QueryPlanner()
    queries = planner.plan("how many films 2020", _CONFIG)
    assert all(isinstance(q.variant_kind, str) and q.variant_kind for q in queries)


def test_query_planner_date_question_includes_temporal_variant() -> None:
    planner = QueryPlanner()
    queries = planner.plan("Apple stock first year above 200 in 2018", _CONFIG)
    kinds = {q.variant_kind for q in queries}
    assert "temporal_stats" in kinds or any("2018" in q.text for q in queries)
