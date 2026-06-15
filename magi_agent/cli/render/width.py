"""Display-width-aware truncation for the Magi CLI TUI.

Every one-line budget in the TUI must be measured in terminal *cells*, not
Python ``len()`` codepoints: East-Asian Wide characters (Hangul / CJK ideographs
/ kana / fullwidth forms) occupy 2 cells each, so a string that passes a
``len() <= N`` gate can render up to ``2N`` cells wide and overflow its budget.

This module is the single shared truncation primitive. It is backed by
``rich.cells`` — already a hard dependency of the ``cli`` surface (no new
install) — and, crucially, the SAME width tables Textual uses for its own column
layout (``textual/_cells.py`` does ``from rich.cells import cached_cell_len``).
Measuring here therefore stays in lockstep with how Textual actually renders.

``rich`` may be imported here: ``cli/render/__init__.py`` sanctions this package
(with ``cli/tui/``) as a ``rich``-importing home.
"""

from __future__ import annotations

from rich.cells import cell_len, set_cell_size

__all__ = ["display_width", "truncate_cells"]


def display_width(text: str) -> int:
    """Terminal cell width of ``text`` (East-Asian Wide chars count 2).

    Thin alias over ``rich.cells.cell_len`` — the SAME measure Textual uses for
    its own column layout (``textual/_cells.py`` imports ``cached_cell_len`` from
    ``rich.cells``), so a budget enforced here stays in lockstep with how Textual
    actually renders. Combining marks / zero-width joiners count 0.
    """

    return cell_len(text)


def truncate_cells(
    text: str, max_cells: int, *, ellipsis: str = "…", lead: bool = False
) -> str:
    """Truncate ``text`` to at most ``max_cells`` terminal cells, appending
    ``ellipsis`` (default ``…``). ``lead=True`` keeps the TAIL (for cwd /
    filenames) and puts the ellipsis in FRONT.

    The ellipsis's own cell width is reserved out of the budget, so the returned
    string never exceeds ``max_cells`` cells. ``set_cell_size`` pads to land
    exactly on the cell boundary when a cut would split a wide char; the kept
    segment is ``rstrip``'d (head-keep) / ``lstrip``'d (lead) so that pad space is
    invisible in normal output.

    ASCII parity: callers that previously did ``x[:N-1].rstrip() + "…"`` get
    byte-identical output (the built-in ``rstrip``); callers that did the plain
    ``x[:N-1] + "…"`` also match, since ``rstrip`` is a no-op when the cut does
    not land on whitespace. Pinned by ``test_tui_width`` parity cases.

    Tail truncation (``lead=True``) reverses the string, sizes the head, then
    reverses back — best-effort for exotic grapheme clusters (combining marks /
    flag emoji), matching today's mid-codepoint ``name[-(N-1):]`` slicing.
    """

    if cell_len(text) <= max_cells:
        return text
    budget = max(0, max_cells - cell_len(ellipsis))
    if lead:  # cwd / sidebar keep the TAIL, ellipsis in front
        kept = set_cell_size(text[::-1], budget)[::-1]
        return ellipsis + kept.lstrip()
    kept = set_cell_size(text, budget).rstrip()
    return kept + ellipsis
