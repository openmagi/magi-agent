"""PR1 — OKF lexical matcher (local 6-line copy, no memory import)."""
from __future__ import annotations

from magi_agent.knowledge.okf.matcher import match_score


def test_counts_term_hits_across_fields() -> None:
    score = match_score("orders table", "Orders index", "table of orders")
    assert score >= 2


def test_no_match_returns_zero() -> None:
    assert match_score("nonexistent", "orders index", "customers") == 0


def test_empty_query_returns_zero() -> None:
    assert match_score("   ", "anything") == 0


def test_case_insensitive() -> None:
    assert match_score("ORDERS", "the orders doc") >= 1


def test_substring_hit_counts() -> None:
    # "ord" is a substring of "orders".
    assert match_score("ord", "orders") >= 1
