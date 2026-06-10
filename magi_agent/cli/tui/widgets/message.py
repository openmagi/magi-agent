"""Message widgets for the mounted-widget transcript (01-architecture §2.3).

* ``UserMessage(Static)``      — the echoed prompt ("› <text>").
* ``AssistantMessage(Markdown)`` — a finalized assistant turn (markdown + syntax).
* ``StatusLine(Static)``       — terminal/control/error summaries.
* ``ThinkingBlock(Collapsible)`` — defined-but-unused per the 2026-06-09
  revision (thinking renders as a dim one-line via ``commit_coalesced``, not a
  Collapsible); kept here so the widget taxonomy lives in one module.

These are thin subclasses so a query (``query_one(AssistantMessage)``) and CSS
targeting are by type. ``Markdown`` parses its content on construction/update;
``Static`` holds any Rich renderable or plain string.
"""

from __future__ import annotations

from textual.widgets import Collapsible, Markdown, Static

__all__ = ["UserMessage", "AssistantMessage", "StatusLine", "ThinkingBlock"]


class UserMessage(Static):
    """The echoed user prompt line ("› <text>")."""


class AssistantMessage(Markdown):
    """A finalized assistant turn rendered as markdown (headings/lists/code)."""


class StatusLine(Static):
    """A one-line status/terminal/control/error summary."""


class ThinkingBlock(Collapsible):
    """Collapsed, dimmed reasoning block — defined-but-unused per the 2026-06-09
    revision (thinking renders as a dim one-line via ``commit_coalesced``, not a
    Collapsible)."""
