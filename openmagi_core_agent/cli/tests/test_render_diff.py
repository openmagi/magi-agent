"""Tests for the PR-E3 diff engine (``cli/render/diff.py``).

Covers the char-level word diff (changed-range fidelity), the ``CHANGE_THRESHOLD``
whole-line fallback, the ``dim`` word-diff skip, the headless unified-diff
projection, and the rendered-diff cache keyed by ``(file, width, theme, dim)``.

Plain pytest only — no ``App.run_test()`` needed: diff.py is a pure module.
"""

from __future__ import annotations

from openmagi_core_agent.cli.render import diff as diffmod


# ---------------------------------------------------------------------------
# Char-level word diff
# ---------------------------------------------------------------------------
def test_word_ranges_highlights_only_changed_word() -> None:
    # A small edit: one word changes. The changed char ranges must point at the
    # changed word, NOT the whole line.
    old = "the quick brown fox"
    new = "the quick red fox"
    del_ranges, add_ranges = diffmod.word_ranges(old, new)

    # "brown" occupies chars 10..15 in old; "red" occupies 10..13 in new.
    assert (10, 15) in del_ranges
    assert (10, 13) in add_ranges
    # NOT marked as the whole line.
    assert (0, len(old)) not in del_ranges
    assert (0, len(new)) not in add_ranges


def test_word_ranges_threshold_falls_back_to_whole_line() -> None:
    # A noisy edit where >40% of the line changed: intra-line highlight is noise
    # so the whole line is marked changed.
    old = "alpha beta gamma"
    new = "zzzzz qqqqq wwwww"
    del_ranges, add_ranges = diffmod.word_ranges(old, new)

    assert del_ranges == [(0, len(old))]
    assert add_ranges == [(0, len(new))]


def test_word_ranges_identical_lines_have_no_ranges() -> None:
    del_ranges, add_ranges = diffmod.word_ranges("same line", "same line")
    assert del_ranges == []
    assert add_ranges == []


# ---------------------------------------------------------------------------
# Line patch / hunks
# ---------------------------------------------------------------------------
def test_build_hunks_pairs_adjacent_del_add() -> None:
    old = "line1\nline2\nline3\n"
    new = "line1\nCHANGED\nline3\n"
    hunks = diffmod.build_hunks(old, new, context=3)
    assert hunks, "expected at least one hunk"
    # Adjacency pairing: the deleted 'line2' and added 'CHANGED' should be paired
    # so that only those two lines carry word ranges.
    flat = [ln for h in hunks for ln in h.lines]
    del_lines = [ln for ln in flat if ln.kind == "del"]
    add_lines = [ln for ln in flat if ln.kind == "add"]
    assert any("line2" in ln.text for ln in del_lines)
    assert any("CHANGED" in ln.text for ln in add_lines)
    # Paired lines get word ranges populated; context lines do not.
    assert any(ln.word_ranges for ln in del_lines)
    assert all(ln.word_ranges == [] for ln in flat if ln.kind == "context")


def test_build_hunks_leading_tabs_converted_to_spaces() -> None:
    old = "\tindented\n"
    new = "\tindented changed\n"
    hunks = diffmod.build_hunks(old, new, context=3)
    flat = [ln for h in hunks for ln in h.lines]
    # No raw tab characters should survive in displayed text.
    assert all("\t" not in ln.text for ln in flat)


# ---------------------------------------------------------------------------
# Headless unified-diff projection
# ---------------------------------------------------------------------------
def test_unified_diff_text_is_plain_and_correct() -> None:
    old = "a\nb\nc\n"
    new = "a\nB\nc\n"
    text = diffmod.unified_diff_text(old, new, file="x.txt")
    assert "-b" in text
    assert "+B" in text
    # plain text — no ANSI escapes.
    assert "\x1b[" not in text


def test_unified_diff_text_importable_without_rich() -> None:
    # The plain path must not require rich to be importable. We can't truly
    # un-import rich here, but we assert the function is callable and produces a
    # str with no rich objects.
    out = diffmod.unified_diff_text("x\n", "y\n", file="f")
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# Colorized render + cache
# ---------------------------------------------------------------------------
def test_render_diff_returns_rich_text() -> None:
    from rich.text import Text

    node = diffmod.render_diff("a\nb\n", "a\nB\n", file="x.py", width=80)
    assert isinstance(node, Text)


def test_render_diff_cache_same_key_returns_same_object() -> None:
    diffmod.clear_diff_cache()
    first = diffmod.render_diff("a\nb\n", "a\nB\n", file="x.py", width=80, dim=False)
    second = diffmod.render_diff("a\nb\n", "a\nB\n", file="x.py", width=80, dim=False)
    assert first is second


def test_render_diff_cache_rebuilds_on_key_change() -> None:
    diffmod.clear_diff_cache()
    first = diffmod.render_diff("a\nb\n", "a\nB\n", file="x.py", width=80, dim=False)
    # Different dim -> different cache key -> different object.
    other = diffmod.render_diff("a\nb\n", "a\nB\n", file="x.py", width=80, dim=True)
    assert first is not other
    # Different width -> rebuild.
    wide = diffmod.render_diff("a\nb\n", "a\nB\n", file="x.py", width=120, dim=False)
    assert wide is not first


def test_render_diff_dim_skips_word_diff() -> None:
    # When dim=True the word-diff is skipped: paired changed lines are marked
    # whole-line, never with intra-line char ranges. We assert via the structured
    # hunk builder honoring the dim flag.
    hunks = diffmod.build_hunks("alpha beta\n", "alpha gamma\n", context=3, dim=True)
    flat = [ln for h in hunks for ln in h.lines]
    changed = [ln for ln in flat if ln.kind in ("del", "add")]
    assert changed, "expected changed lines"
    for ln in changed:
        # dim -> whole-line marking only (range spans full line) or empty.
        for start, end in ln.word_ranges:
            assert (start, end) == (0, len(ln.text))
