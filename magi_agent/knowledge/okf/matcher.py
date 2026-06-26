"""Local lexical matcher for OKF search (v1).

A deliberately tiny substring scorer.  Copied (not imported) to keep
``knowledge/okf`` decoupled from ``magi_agent.memory.adapters`` per the design's
zero-coupling invariant (S2).  Vector/HyDE ranking is a follow-up.
"""
from __future__ import annotations


def match_score(query: str, *fields: str) -> int:
    """Count query-term substring hits across the joined ``fields``.

    Lowercases everything, splits ``query`` on whitespace, and sums one point
    per term found as a substring of the joined field text.  Returns 0 when no
    term matches (or the query is blank).
    """
    haystack = " ".join(fields).lower()
    terms = query.lower().split()
    return sum(1 for term in terms if term in haystack)
