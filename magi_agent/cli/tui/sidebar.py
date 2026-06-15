"""Toggleable left sidebar for the Magi TUI (PR3.2).

``Sidebar`` is a ``VerticalScroll`` holding three labelled panes: a Todo list
(fed from ``TodoWrite`` tool state), a Context-usage line (token budget), and a
Recent-files list (fed from ``Read``/``Edit`` tool events). It is a sibling
widget of the transcript and is shown/hidden via ``display`` (``ctrl+b`` toggle
lives in the App). It does NOT depend on the PR0.3 ``TranscriptView`` migration —
it stands alone as its own scroll container.

State setters are imperative (``set_todos`` / ``set_context`` / ``add_file``); the
App folds engine tool events into them. Recent-files is an MRU list (most-recent
first), deduped, capped at ``MAX_RECENT_FILES``.
"""

from __future__ import annotations

import os

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from magi_agent.cli.render.width import truncate_cells

__all__ = ["Sidebar", "MAX_RECENT_FILES", "MAX_FILE_DISPLAY_WIDTH"]

MAX_RECENT_FILES = 10

# Recent-file display width budget, measured in terminal CELLS (East-Asian Wide
# chars count 2) via ``truncate_cells`` — so a CJK basename doesn't ~2x-overflow.
# The sidebar dock is ``width: 32`` (see the App CSS); recent files store FULL
# paths (for dedup integrity) but DISPLAY a shortened basename so a long path
# like ``lib/services/handlers/foo.py`` does not overflow/wrap the column. Kept
# under the dock width minus the 2-space indent and a little slack.
MAX_FILE_DISPLAY_WIDTH = 28


def _shorten_file(path: str) -> str:
    """Shorten a stored full path to a column-friendly display string.

    Renders the basename (most readable for a recent-files list); if even the
    basename is wider than ``MAX_FILE_DISPLAY_WIDTH`` it is tail-truncated with a
    leading ``…`` so the file extension stays visible.
    """

    name = os.path.basename(path.rstrip("/")) or path
    return truncate_cells(name, MAX_FILE_DISPLAY_WIDTH, lead=True)


class Sidebar(VerticalScroll):
    """Left dock: todo · context-usage · recent-files panes."""

    def __init__(self, *, id: str | None = None) -> None:  # noqa: A002
        super().__init__(id=id)
        self._todos: list[str] = []
        self._context_usage: int = 0
        self._context_limit: int = 0
        self._recent_files: list[str] = []
        self._todo_pane: Static | None = None
        self._context_pane: Static | None = None
        self._files_pane: Static | None = None

    def compose(self) -> ComposeResult:
        self._todo_pane = Static("", id="sidebar-todo", classes="sidebar-pane")
        self._context_pane = Static("", id="sidebar-context", classes="sidebar-pane")
        self._files_pane = Static("", id="sidebar-files", classes="sidebar-pane")
        yield self._todo_pane
        yield self._context_pane
        yield self._files_pane

    def on_mount(self) -> None:
        self._repaint()

    # -- imperative setters (folded from tool events) -----------------------
    def set_todos(self, todos: list[str]) -> None:
        self._todos = [str(t) for t in todos]
        self._repaint()

    def set_context(self, *, usage: int, limit: int) -> None:
        self._context_usage = max(0, int(usage))
        self._context_limit = max(0, int(limit))
        self._repaint()

    def add_file(self, path: str) -> None:
        path = str(path)
        if path in self._recent_files:
            self._recent_files.remove(path)
        self._recent_files.insert(0, path)
        del self._recent_files[MAX_RECENT_FILES:]
        self._repaint()

    def recent_files(self) -> list[str]:
        return list(self._recent_files)

    # -- rendering -----------------------------------------------------------
    def _todo_text(self) -> str:
        if not self._todos:
            return "Todo\n  (none)"
        lines = "\n".join(f"  • {t}" for t in self._todos)
        return f"Todo\n{lines}"

    def _context_text(self) -> str:
        # Render a HONEST bare token count, NOT ``usage / limit``. A hardcoded
        # default limit (200k) is actively misleading on a 128k/1M-window model
        # — the ratio would be wrong for every non-Claude model. ``set_context``
        # still accepts a ``limit`` (other code passes one, and it is kept for a
        # future per-model context-window table) but it is no longer rendered as
        # a hard ratio against the hardcoded default.
        return f"Context\n  {self._context_usage:,} tokens"

    def _files_text(self) -> str:
        if not self._recent_files:
            return "Files\n  (none)"
        # Store FULL paths (dedup keys on them) but DISPLAY shortened basenames
        # so long paths don't overflow the 32-wide sidebar dock.
        lines = "\n".join(f"  {_shorten_file(p)}" for p in self._recent_files)
        return f"Files\n{lines}"

    def panes_text(self) -> str:
        """Concatenated pane text (asserted by tests)."""

        return "\n".join(
            (self._todo_text(), self._context_text(), self._files_text())
        )

    def _repaint(self) -> None:
        if self._todo_pane is not None:
            self._todo_pane.update(self._todo_text())
        if self._context_pane is not None:
            self._context_pane.update(self._context_text())
        if self._files_pane is not None:
            self._files_pane.update(self._files_text())
