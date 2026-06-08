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


def test_assistant_message_is_markdown_widget() -> None:
    from textual.widgets import Markdown

    assert issubclass(AssistantMessage, Markdown)


def test_user_message_and_status_line_are_static() -> None:
    from textual.widgets import Static

    assert issubclass(UserMessage, Static)
    assert issubclass(StatusLine, Static)
