"""Collapsible tool-output card (01-architecture §2.3, PR0.4).

A ``ToolCard`` is a ``textual.widgets.Collapsible`` whose header is the tool call
line (``RenderNode.text``) and whose body is the tool's Rich renderable
(``RenderNode.rich``) wrapped in a ``Static``. It is collapsed by default;
Textual's ``CollapsibleTitle`` already binds ``enter`` to toggle and the focused
title toggles on activation (``space``), so no custom bindings are needed.

Renderers stay pure (no widget imports): ``app._commit_render_node`` calls
``ToolCard.from_render_node(node)`` to lift a ``RenderNode`` into a widget at the
mount boundary.
"""

from __future__ import annotations

from textual.widgets import Collapsible, Static

from magi_agent.cli.contracts import RenderNode

__all__ = ["ToolCard"]


class ToolCard(Collapsible):
    """Collapsed-by-default card hosting a tool's call header + result body."""

    @classmethod
    def from_render_node(cls, node: RenderNode, *, collapsed: bool = True) -> "ToolCard":
        """Build a ``ToolCard`` from a ``RenderNode``.

        Header = ``node.text`` (the call line / search snapshot). Body = a
        ``Static`` wrapping ``node.rich`` when present, else ``node.text``. An
        empty header falls back to ``"tool"`` so the title is never blank.
        """

        title = node.text or "tool"
        body = Static(node.rich if node.rich is not None else node.text)
        return cls(body, title=title, collapsed=collapsed)
