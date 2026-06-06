from __future__ import annotations

from magi_agent.benchmarks.gaia.best_of_n import majority_vote


def test_majority_picks_most_common() -> None:
    assert majority_vote(["Paris", "paris.", "London"]) == "Paris"


def test_tie_breaks_by_first_occurrence() -> None:
    assert majority_vote(["b", "a", "a", "b"]) == "b"


def test_empty_returns_empty() -> None:
    assert majority_vote([]) == ""
