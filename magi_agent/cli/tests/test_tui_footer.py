"""Tests for the PR3.1 StatusFooter dynamic status widget.

Style: mirrors the rest of the TUI test suite — no ``pytest-asyncio``; async
tests are SYNC functions driving a coroutine via ``asyncio.run`` with a nested
``async def _run`` that uses Textual's ``App.run_test()`` Pilot harness.
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from magi_agent.cli.tui.footer import StatusFooter


class _Harness(App[None]):
    def __init__(self, footer: StatusFooter) -> None:
        super().__init__()
        self._footer = footer

    def compose(self) -> ComposeResult:
        yield self._footer


def test_footer_renders_all_fields_idle() -> None:
    async def _run() -> None:
        footer = StatusFooter(model="claude-x", cwd="~/proj", id="footer")
        app = _Harness(footer)
        async with app.run_test() as pilot:
            await pilot.pause()
            text = footer.status_text()
        assert "claude-x" in text
        assert "~/proj" in text
        assert "idle" in text
        assert "0 tok" in text
        assert "0s" in text

    asyncio.run(_run())


def test_footer_update_reflects_running_state_tokens_elapsed() -> None:
    async def _run() -> None:
        footer = StatusFooter(model="claude-x", cwd="~/proj", id="footer")
        app = _Harness(footer)
        async with app.run_test() as pilot:
            await pilot.pause()
            footer.set_state("running")
            footer.set_tokens(1234)
            footer.set_elapsed(7.4)
            await pilot.pause()
            text = footer.status_text()
        assert "running" in text
        assert "1,234 tok" in text
        assert "7s" in text

    asyncio.run(_run())


def test_footer_set_activity_assigns_reactive() -> None:
    async def _run() -> None:
        footer = StatusFooter(model="claude-x", cwd="~/proj", id="footer")
        app = _Harness(footer)
        async with app.run_test() as pilot:
            await pilot.pause()
            footer.set_activity("Bash")
            await pilot.pause()
            value = footer.activity
        assert value == "Bash"

    asyncio.run(_run())


def test_footer_shows_activity_word_when_running() -> None:
    async def _run() -> None:
        footer = StatusFooter(model="claude-x", cwd="~/proj", id="footer")
        app = _Harness(footer)
        async with app.run_test() as pilot:
            await pilot.pause()
            footer.set_state("running")
            footer.set_activity("Bash")
            await pilot.pause()
            text = footer.status_text()
        assert "running · Bash" in text

    asyncio.run(_run())


def test_footer_idle_text_is_exact() -> None:
    # Strict EQUALITY guard the repo previously lacked: no activity segment may
    # leak in while idle (the existing all-fields test only uses substring `in`).
    async def _run() -> None:
        footer = StatusFooter(model="claude-x", cwd="~/proj", id="footer")
        app = _Harness(footer)
        async with app.run_test() as pilot:
            await pilot.pause()
            text = footer.status_text()
        assert text == "claude-x   ~/proj   idle   0 tok   0s"

    asyncio.run(_run())


def test_footer_running_with_empty_activity_is_exact() -> None:
    # Running but no open tool -> bare ``running`` with NO trailing ` · `.
    async def _run() -> None:
        footer = StatusFooter(model="claude-x", cwd="~/proj", id="footer")
        app = _Harness(footer)
        async with app.run_test() as pilot:
            await pilot.pause()
            footer.set_state("running")
            await pilot.pause()
            text = footer.status_text()
        assert text == "claude-x   ~/proj   running   0 tok   0s"

    asyncio.run(_run())
