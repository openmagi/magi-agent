"""Linear suffix/prefix overlap for streamed-vs-final text reconciliation.

Single home for the helper previously duplicated as
cli.engine._unstreamed_text_delta and
adk_bridge.event_adapter._unstreamed_final_text (PR-D4 / N-38).
"""
from __future__ import annotations


def unstreamed_suffix(aggregate_text: str, emitted_text: str) -> str:
    """Return the suffix of ``aggregate_text`` not already covered by the tail
    of ``emitted_text``.

    Behavior-identical to the previous descending scan (the exhaustive
    property test in tests/test_shared_text_overlap.py pins this): find the
    largest ``size`` such that ``emitted_text`` ends with
    ``aggregate_text[:size]`` and return ``aggregate_text[size:]``.

    The overlap search is O(n + m) via the standard prefix-function automaton
    (longest prefix of the pattern that is a suffix of the window) instead of
    the worst-case O(n * m) descending scan.
    """
    if not emitted_text:
        return aggregate_text
    if aggregate_text.startswith(emitted_text):
        return aggregate_text[len(emitted_text):]
    if emitted_text.endswith(aggregate_text):
        return ""
    max_overlap = min(len(aggregate_text), len(emitted_text))
    pattern = aggregate_text[:max_overlap]
    window = emitted_text[len(emitted_text) - max_overlap:]

    # Prefix (failure) function of ``pattern`` in O(len(pattern)).
    failure = [0] * len(pattern)
    k = 0
    for i in range(1, len(pattern)):
        while k and pattern[i] != pattern[k]:
            k = failure[k - 1]
        if pattern[i] == pattern[k]:
            k += 1
        failure[i] = k

    # Feed ``window`` through the automaton. After the full pass, ``state`` is
    # the length of the longest prefix of ``pattern`` that is a suffix of
    # ``window`` - i.e. the largest overlap ``size``. A full-pattern match can
    # only occur when window == pattern (both have length max_overlap), which
    # the startswith/endswith short-circuits above already excluded; the reset
    # branch is a defensive guard that never affects the returned answer.
    state = 0
    for ch in window:
        while state and ch != pattern[state]:
            state = failure[state - 1]
        if ch == pattern[state]:
            state += 1
        if state == len(pattern):
            state = failure[state - 1]
    return aggregate_text[state:]


__all__ = ["unstreamed_suffix"]
