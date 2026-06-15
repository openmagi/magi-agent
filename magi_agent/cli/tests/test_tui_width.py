"""Unit tests for the display-width truncation helper (``cli/render/width.py``).

The TUI's one-line budgets must count terminal *cells* (East-Asian Wide chars
are 2 cells), not Python ``len()`` codepoints, or CJK text overflows its visual
budget by ~2x. ``truncate_cells`` is the shared primitive every call-site routes
through; ``display_width`` is the thin ``rich.cells.cell_len`` alias the tests
assert against.

These are pure-function tests (no ``App`` harness needed). The ASCII-parity
cases are LOAD-BEARING: they pin that English output is byte-identical to the old
``x[:N-1](.rstrip())+"…"`` expressions, which is the license for the no-flag
rollout (see the design doc §7).
"""

from __future__ import annotations

from magi_agent.cli.render.width import display_width, truncate_cells


def test_display_width_counts_cjk_as_two() -> None:
    assert display_width("가나") == 4
    assert display_width("abc") == 3


def test_truncate_cells_cjk_is_cell_bounded() -> None:
    # The helper reserves the ellipsis's own cell (1), so the 6-cell budget keeps
    # 5 cells of Hangul -> "가나" (width 4) + "…" (width 1). Assert the ROBUST
    # bound + endswith; pin the empirical exact value, NOT the design §5 prose
    # "가나다…" (which ignored the reserved ellipsis cell).
    r = truncate_cells("가나다라마", 6)
    assert display_width(r) <= 6
    assert r.endswith("…")
    assert r == "가나…"


def test_truncate_cells_ascii_under_budget_unchanged() -> None:
    assert truncate_cells("hello", 80) == "hello"


def test_truncate_cells_lead_keeps_tail() -> None:
    r = truncate_cells("/a/very/long/path.py", 8, lead=True)
    assert r.startswith("…")
    assert r.endswith("path.py")
    assert display_width(r) <= 8


def test_truncate_cells_ascii_parity_rstrip_shape() -> None:
    # The rstrip-shape sites (_thinking_preview / _child_task_label / _preview)
    # did ``x[:N-1].rstrip() + "…"``. A cut landing on whitespace must collapse
    # the trailing spaces -> "aaaaa…", byte-identical to the old expression.
    s = "aaaaa     zzz"
    assert truncate_cells(s, 7) == s[:6].rstrip() + "…"
    assert truncate_cells(s, 7) == "aaaaa…"


def test_truncate_cells_ascii_parity_plain_shape() -> None:
    # The plain-cut sites (_clip / _topbar_cwd / _shorten_file) did
    # ``x[:N-1] + "…"`` with no rstrip; a non-whitespace cut must match exactly.
    s = "abcdefghij"
    assert truncate_cells(s, 7) == s[:6] + "…"


def test_display_width_zero_for_combining_marks() -> None:
    # Characterization (not a correctness claim about exotic clusters): a base
    # char + a combining mark is width 1, so cell_len < len(). This documents
    # that the HEAD-keep path may retain more codepoints than naive ``len()``.
    combining = "a" + "́"  # "a" + combining acute accent -> "á"
    assert len(combining) == 2
    assert display_width(combining) == 1
