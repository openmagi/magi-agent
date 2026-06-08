"""Tests for the PR-E3 diff engine (``cli/render/diff.py``).

Covers the char-level word diff (changed-range fidelity), the ``CHANGE_THRESHOLD``
whole-line fallback, the ``dim`` word-diff skip, the headless unified-diff
projection, and the rendered-diff cache keyed by ``(file, width, theme, dim)``.

Plain pytest only — no ``App.run_test()`` needed: diff.py is a pure module.
"""

from __future__ import annotations

import io

from magi_agent.cli.render import diff as diffmod


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


def test_resolve_lexer_from_extension() -> None:
    from magi_agent.cli.render import diff as diffmod

    assert diffmod.resolve_lexer("foo.py") == "python"
    assert diffmod.resolve_lexer("a/b/c.ts") in ("typescript", "ts")
    # Unknown / extensionless -> a safe default, never raises.
    assert diffmod.resolve_lexer("Makefile") is not None
    assert diffmod.resolve_lexer("") is not None


def test_render_diff_split_returns_table_renderable() -> None:
    from rich.table import Table

    from magi_agent.cli.render import diff as diffmod

    diffmod.clear_diff_cache()
    out = diffmod.render_diff(
        "alpha\nbeta\n", "alpha\ngamma\n", file="x.py", split=True
    )
    # Split mode returns a Rich Table (two columns: old | new).
    assert isinstance(out, Table)
    assert len(out.columns) == 2


def test_render_diff_unified_still_default() -> None:
    from rich.text import Text

    from magi_agent.cli.render import diff as diffmod

    diffmod.clear_diff_cache()
    out = diffmod.render_diff("a\n", "b\n", file="x.py")
    # Default (split=False) is still the single Text projection.
    assert isinstance(out, Text)


def test_render_diff_split_and_unified_cache_independently() -> None:
    from magi_agent.cli.render import diff as diffmod

    diffmod.clear_diff_cache()
    unified = diffmod.render_diff("a\n", "b\n", file="x.py", split=False)
    split = diffmod.render_diff("a\n", "b\n", file="x.py", split=True)
    assert unified is not split


def test_render_diff_split_row_alignment_unequal_del_add() -> None:
    # A hunk where a context line is followed by an unequal del/add run:
    #   old = "a\nb\nc\n"  new = "a\nX\n"
    # -> context "a" mirrored both sides; then 2 dels (b, c) vs 1 add (X).
    # The split table must mirror the context line and pad the second deleted
    # line ("c") with a BLANK new-side cell (no matching add).
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    from magi_agent.cli.render import diff as diffmod

    diffmod.clear_diff_cache()
    old = "a\nb\nc\n"
    new = "a\nX\n"
    table = diffmod.render_diff(old, new, file="f.py", split=True)
    assert isinstance(table, Table)
    assert len(table.columns) == 2

    old_cells = list(table.columns[0]._cells)
    new_cells = list(table.columns[1]._cells)
    # Same number of rows on each side (paired row-by-row).
    assert len(old_cells) == len(new_cells)
    # context "a" + max(2 dels, 1 add) = 3 rows.
    assert len(old_cells) == 3

    def cell_text(c: object) -> str:
        return c.plain if isinstance(c, Text) else str(c)

    old_text = [cell_text(c) for c in old_cells]
    new_text = [cell_text(c) for c in new_cells]

    # Row 0: context line "a" mirrored on both sides.
    assert "a" in old_text[0]
    assert "a" in new_text[0]
    assert old_text[0] == new_text[0]

    # The deleted lines b, c appear on the old side.
    assert any("b" in t for t in old_text)
    assert any("c" in t for t in old_text)
    # The single added line X appears on the new side.
    assert any("X" in t for t in new_text)

    # The unmatched second deleted line ("c") pairs with a BLANK new-side cell:
    # find the row whose old cell holds "c" and assert its new cell is empty.
    c_rows = [k for k, t in enumerate(old_text) if "c" in t]
    assert c_rows, "expected a row carrying the deleted 'c' line"
    for k in c_rows:
        assert new_text[k].strip() == "", (
            f"expected blank new-side cell opposite deleted 'c', got {new_text[k]!r}"
        )

    # Cross-check via a rendered string: both 'b' and 'X' land on the same row,
    # 'c' has no neighbour on the new side.
    console = Console(file=io.StringIO(), width=80, force_terminal=False)
    console.print(table)
    rendered = console.file.getvalue()
    assert "b" in rendered
    assert "c" in rendered
    assert "X" in rendered


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
