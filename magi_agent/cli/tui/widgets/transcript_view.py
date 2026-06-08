"""The mounted-widget finalized region (01-architecture §2.3, PR0.3).

``TranscriptView`` is a ``VerticalScroll`` that owns the list of finalized
message/tool widgets. It replaces ``RichLog`` as the controller's finalized-block
backing: instead of ``RichLog.write(renderable)`` the controller calls
``view.add_block(widget)``. Textual re-renders only changed widgets, so a list of
N message widgets does NOT reintroduce the whole-transcript reparse trap (that
trap was a single Markdown widget holding the entire transcript; the live block
stays a separate small widget).

``add_block`` mounts the widget and scrolls to the end (matching ``RichLog``'s
``auto_scroll``). It returns the ``AwaitMount`` so callers/tests can ``await`` the
mount completing before querying.
"""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widget import Widget

__all__ = ["TranscriptView"]


class TranscriptView(VerticalScroll):
    """Scrollable, focusable host for the finalized transcript widgets."""

    def add_block(self, widget: Widget) -> object:
        """Mount ``widget`` at the end and scroll to it (auto-scroll parity).

        Returns the awaitable mount handle so callers can ``await`` it.
        """

        await_mount = self.mount(widget)
        self.scroll_end(animate=False)
        return await_mount
