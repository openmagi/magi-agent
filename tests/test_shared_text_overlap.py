from __future__ import annotations

import time
from itertools import product

from magi_agent.shared.text_overlap import unstreamed_suffix


def _legacy_unstreamed_suffix(aggregate_text: str, emitted_text: str) -> str:
    """Verbatim copy of the descending-scan implementation that previously lived
    in cli.engine._unstreamed_text_delta and
    adk_bridge.event_adapter._unstreamed_final_text. Kept here as the behavior
    oracle for the linear replacement (PR-D4 / N-38)."""
    if not emitted_text:
        return aggregate_text
    if aggregate_text.startswith(emitted_text):
        return aggregate_text[len(emitted_text):]
    if emitted_text.endswith(aggregate_text):
        return ""
    max_overlap = min(len(aggregate_text), len(emitted_text))
    for size in range(max_overlap, 0, -1):
        if emitted_text.endswith(aggregate_text[:size]):
            return aggregate_text[size:]
    return aggregate_text


def test_matches_legacy_descending_scan_exhaustive():
    alphabet = "ab"
    strings = [""]
    for length in range(1, 9):
        strings.extend("".join(combo) for combo in product(alphabet, repeat=length))
    for aggregate in strings:
        for emitted in strings:
            assert unstreamed_suffix(aggregate, emitted) == _legacy_unstreamed_suffix(
                aggregate, emitted
            ), f"mismatch for aggregate={aggregate!r} emitted={emitted!r}"


def test_matches_legacy_on_unicode_and_redaction_markers():
    cases = [
        ("[redacted] hello world", "[redacted] hello"),
        ("한국어 스트리밍 텍스트", "한국어 스트리밍"),
        ("emoji stream 🚀🌊 tail", "emoji stream 🚀"),
        ("<<REDACTED:secret>> after", "<<REDACTED:secret>>"),
        ("abcabcabc", "xxabcabc"),
        ("mississippi", "issip"),
    ]
    for aggregate, emitted in cases:
        assert unstreamed_suffix(aggregate, emitted) == _legacy_unstreamed_suffix(
            aggregate, emitted
        )


def test_boundary_conditions():
    assert unstreamed_suffix("", "") == ""
    assert unstreamed_suffix("abc", "") == "abc"
    assert unstreamed_suffix("", "abc") == ""
    # startswith short-circuit
    assert unstreamed_suffix("abcdef", "abc") == "def"
    # emitted fully contains aggregate as a suffix
    assert unstreamed_suffix("def", "abcdef") == ""
    # complete mismatch: nothing overlaps, return aggregate whole
    assert unstreamed_suffix("xyz", "abc") == "xyz"


def test_linear_performance_bound():
    aggregate = "a" * 50_000 + "b"
    emitted = "a" * 50_000
    start = time.monotonic()
    result = unstreamed_suffix(aggregate, emitted)
    elapsed = time.monotonic() - start
    # Behavior: emitted is a prefix of aggregate, so the whole tail after the
    # prefix is returned (the startswith short-circuit handles this in O(n)).
    assert result == "b"
    assert elapsed < 0.5, f"took {elapsed:.3f}s, expected linear time"


def test_linear_performance_bound_no_startswith_shortcut():
    # Force the automaton path (no startswith/endswith short-circuit) with a
    # highly repetitive worst case for the old quadratic scan.
    aggregate = "a" * 50_000 + "c"
    emitted = "b" + "a" * 50_000
    start = time.monotonic()
    result = unstreamed_suffix(aggregate, emitted)
    elapsed = time.monotonic() - start
    assert result == _legacy_unstreamed_suffix(aggregate, emitted)
    assert elapsed < 0.5, f"took {elapsed:.3f}s, expected linear time"
