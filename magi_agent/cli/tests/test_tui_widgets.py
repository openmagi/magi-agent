"""Tests for the PR0.3 transcript widget primitives.

Widgets are mounted under a tiny host App via Textual's run_test() harness,
matching the test_tui_transcript.py / test_tui_app.py async-via-asyncio.run
convention.
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from magi_agent.cli.tui.widgets.message import (
    AssistantMessage,
    StatusLine,
    UserMessage,
)
from magi_agent.cli.tui.widgets.transcript_view import TranscriptView


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        yield TranscriptView(id="view")


def test_transcript_view_mounts_message_widgets() -> None:
    async def _run() -> None:
        app = _Host()
        async with app.run_test() as pilot:
            view = app.query_one(TranscriptView)
            await view.add_block(UserMessage("› hi"))
            await view.add_block(AssistantMessage("# Heading\n\nbody"))
            await view.add_block(StatusLine("[turn aborted]"))
            await pilot.pause()
            assert len(view.query(UserMessage)) == 1
            assert len(view.query(AssistantMessage)) == 1
            assert len(view.query(StatusLine)) == 1

    asyncio.run(_run())


def test_add_block_scrolls_to_bottom() -> None:
    """Auto-scroll parity: after committing enough tall blocks to exceed the
    viewport, the view must be scrolled to the very bottom (matching the old
    ``RichLog(auto_scroll=True)``). Asserts the real scroll position, not just
    mount count.

    HONESTY NOTE (Phase 0 review): this asserts the END-STATE bottom position,
    not specifically that the POST-REFRESH ``call_after_refresh(scroll_end)``
    re-scroll fired. Empirically, in this ``run_test`` harness the layout/height
    is measured synchronously enough that EITHER scroll lands the bottom on its
    own — commenting out ``call_after_refresh`` alone still passes, and commenting
    out the synchronous ``scroll_end`` alone still passes; only removing BOTH
    fails (``scroll_offset.y`` stays 0). So this test cannot single out the
    post-refresh line. The post-refresh re-scroll is belt-and-suspenders for REAL
    terminals, where a tall block's height isn't known until layout flushes and
    the synchronous scroll fires before the new bottom exists. This test's job is
    the regression guard that SOME scroll-to-bottom happens at all (removing both
    fails); the ``await pilot.pause()`` flushes layout before we read the offset.
    """

    async def _run() -> None:
        app = _Host()
        async with app.run_test() as pilot:
            view = app.query_one(TranscriptView)
            # Commit several multi-line finalized blocks — enough to overflow
            # the default test viewport so there is something to scroll.
            for i in range(12):
                body = f"block {i}\n" + "\n".join(f"line {i}.{j}" for j in range(6))
                await view.add_block(StatusLine(body))
            await pilot.pause()
            # There IS scrollable content (guards against a no-op assertion).
            assert view.max_scroll_y > 0
            # ...and we are pinned to the bottom of it.
            assert view.scroll_offset.y == view.max_scroll_y

    asyncio.run(_run())


def test_assistant_message_is_markdown_widget() -> None:
    from textual.widgets import Markdown

    assert issubclass(AssistantMessage, Markdown)


def test_user_message_and_status_line_are_static() -> None:
    from textual.widgets import Static

    assert issubclass(UserMessage, Static)
    assert issubclass(StatusLine, Static)


def test_tool_card_from_render_node_collapsed_by_default() -> None:
    async def _run() -> None:
        from textual.app import App, ComposeResult
        from textual.widgets import Collapsible

        from magi_agent.cli.contracts import RenderNode
        from magi_agent.cli.tui.widgets.tool_card import ToolCard

        node = RenderNode(
            rich=__import__("rich.text", fromlist=["Text"]).Text("body"),
            text="Bash($ ls)",
        )

        class _ToolHost(App[None]):
            def compose(self) -> ComposeResult:
                yield ToolCard.from_render_node(node)

        app = _ToolHost()
        async with app.run_test() as pilot:
            card = app.query_one(ToolCard)
            assert isinstance(card, Collapsible)
            # Header is the RenderNode.text; collapsed by default.
            assert card.title == "Bash($ ls)"
            assert card.collapsed is True
            await pilot.pause()

    asyncio.run(_run())


def test_tool_card_title_is_first_line_only_for_multiline_edit() -> None:
    """An Edit's ``RenderNode.text`` is multi-line (``"Edit(file)\\n<diff>"``).

    The single-line ``CollapsibleTitle`` must show ONLY the first line so the
    whole unified diff is not jammed into the header (it clips/overflows). The
    body (``node.rich``) still carries the styled header + diff, so the diff is
    not lost — only the TITLE is trimmed.
    """

    from rich.console import Group
    from rich.text import Text

    from magi_agent.cli.contracts import RenderNode
    from magi_agent.cli.tui.widgets.tool_card import ToolCard

    node = RenderNode(
        rich=Group(Text("Edit(foo.py)"), Text("- old\n+ new")),
        text="Edit(foo.py)\n- old\n+ new",
    )
    card = ToolCard.from_render_node(node)
    assert card.title == "Edit(foo.py)"
    assert "\n" not in card.title


def test_tool_card_toggles_open() -> None:
    async def _run() -> None:
        from textual.app import App, ComposeResult

        from magi_agent.cli.contracts import RenderNode
        from magi_agent.cli.tui.widgets.tool_card import ToolCard

        node = RenderNode(rich=None, text="Read(/tmp/x)")

        class _ToolHost(App[None]):
            def compose(self) -> ComposeResult:
                yield ToolCard.from_render_node(node)

        app = _ToolHost()
        async with app.run_test() as pilot:
            card = app.query_one(ToolCard)
            assert card.collapsed is True
            card.collapsed = False
            await pilot.pause()
            assert card.collapsed is False

    asyncio.run(_run())
