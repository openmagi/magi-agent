"""Task 6.1 — Deterministic memory compactor (pure, no IO).

TDD tests written before implementation. The compactor consolidates an
append-only memory file's text by:
  1. Removing exact-duplicate entries (preserve first occurrence + order).
  2. If still over ``max_bytes``, dropping the OLDEST entries (front of file)
     until the result fits — reporting how many were dropped (never silent).

Drop-safety philosophy (mirrors regen-types): report what was removed; a
unique fact is only dropped when the file genuinely cannot fit, and that count
is always surfaced via ``dropped_count``.
"""
from __future__ import annotations

from magi_agent.memory.compactor import CompactionResult, consolidate


def _entries(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# (a) under-cap input → unchanged
# ---------------------------------------------------------------------------


def test_under_cap_input_unchanged() -> None:
    text = "- [note] alpha\n- [note] beta\n- [note] gamma\n"
    result = consolidate(text, max_bytes=10_000)
    assert isinstance(result, CompactionResult)
    assert result.was_compacted is False
    assert result.dropped_count == 0
    assert result.text == text
    assert result.kept_count == 3


def test_empty_input_unchanged() -> None:
    result = consolidate("", max_bytes=10_000)
    assert result.was_compacted is False
    assert result.dropped_count == 0
    assert result.kept_count == 0
    assert result.text == ""


# ---------------------------------------------------------------------------
# (b) exact-duplicate entries removed, all distinct facts preserved
# ---------------------------------------------------------------------------


def test_exact_duplicates_removed_distinct_preserved() -> None:
    text = (
        "- [note] alpha\n"
        "- [note] beta\n"
        "- [note] alpha\n"  # exact dup of first
        "- [note] gamma\n"
        "- [note] beta\n"  # exact dup of second
    )
    result = consolidate(text, max_bytes=10_000)
    assert result.was_compacted is True
    assert result.dropped_count == 2
    kept = _entries(result.text)
    # First-occurrence order preserved, no distinct fact lost.
    assert kept == ["- [note] alpha", "- [note] beta", "- [note] gamma"]
    assert result.kept_count == 3
    # All distinct input facts still present.
    for fact in {"alpha", "beta", "gamma"}:
        assert fact in result.text


def test_duplicate_removal_keeps_first_occurrence_order() -> None:
    text = "- [note] z\n- [note] a\n- [note] z\n- [note] a\n- [note] m\n"
    result = consolidate(text, max_bytes=10_000)
    assert _entries(result.text) == ["- [note] z", "- [note] a", "- [note] m"]
    assert result.dropped_count == 2


# ---------------------------------------------------------------------------
# (c) over-cap after dedup → oldest dropped, result <= cap, newest retained
# ---------------------------------------------------------------------------


def test_over_cap_drops_oldest_reports_count() -> None:
    # Each entry ~ 16 bytes; 10 unique entries.
    entries = [f"- [note] fact-{i:02d}" for i in range(10)]
    text = "\n".join(entries) + "\n"
    # Cap that only fits a few entries.
    cap = 60
    result = consolidate(text, max_bytes=cap)
    assert result.was_compacted is True
    # Result must fit the cap (UTF-8 byte length).
    assert len(result.text.encode("utf-8")) <= cap
    # Some entries were dropped and reported.
    assert result.dropped_count > 0
    kept = _entries(result.text)
    assert result.kept_count == len(kept)
    # Newest facts retained: the LAST input entry must survive.
    assert "fact-09" in result.text
    # Oldest dropped: the FIRST input entry must be gone.
    assert "fact-00" not in result.text


def test_dedup_then_oldest_drop_combined() -> None:
    # Build input with duplicates AND over-cap so both passes engage.
    base = [f"- [note] u{i:02d}" for i in range(8)]
    text = "\n".join(base + base[:4]) + "\n"  # 4 dups appended
    cap = 50
    result = consolidate(text, max_bytes=cap)
    assert len(result.text.encode("utf-8")) <= cap
    assert result.was_compacted is True
    # Newest unique fact retained.
    assert "u07" in result.text
    # dropped_count accounts for both dedup + oldest-drop removals.
    total_input = len(_entries(text))
    assert result.dropped_count == total_input - result.kept_count


def test_result_never_exceeds_cap_even_single_large_entry() -> None:
    # A single entry larger than cap cannot fit; result must still be <= cap
    # (becomes empty) and dropped_count reports the loss.
    text = "- [note] " + ("x" * 200) + "\n"
    cap = 20
    result = consolidate(text, max_bytes=cap)
    assert len(result.text.encode("utf-8")) <= cap
    assert result.dropped_count == 1
    assert result.kept_count == 0


# ---------------------------------------------------------------------------
# (d) UTF-8 boundary safety (multibyte entries near the cap)
# ---------------------------------------------------------------------------


def test_utf8_multibyte_boundary_safety() -> None:
    # Korean + emoji entries (multi-byte). Ensure we never split mid-codepoint
    # and the byte length stays <= cap.
    entries = [
        "- [note] 한국어 정보 하나",  # multibyte
        "- [note] 두번째 기억 항목",
        "- [note] 세번째 데이터 🚀",  # emoji
        "- [note] 네번째 최신 사실",
    ]
    text = "\n".join(entries) + "\n"
    # Cap that forces dropping some entries but lands near a multibyte boundary.
    cap = 60
    result = consolidate(text, max_bytes=cap)
    assert len(result.text.encode("utf-8")) <= cap
    # Result must be valid UTF-8 (round-trips) and not split an entry.
    assert result.text == result.text.encode("utf-8").decode("utf-8")
    for line in _entries(result.text):
        # Each surviving line must be a verbatim, whole input entry.
        assert line in entries
    # Newest fact retained.
    assert "네번째 최신 사실" in result.text


def test_no_partial_entry_in_output() -> None:
    entries = [f"- [note] entry number {i} with some padding text" for i in range(6)]
    text = "\n".join(entries) + "\n"
    cap = 120
    result = consolidate(text, max_bytes=cap)
    assert len(result.text.encode("utf-8")) <= cap
    for line in _entries(result.text):
        assert line in entries  # whole entries only, never mid-entry slices
