"""Tests for the PR3.2 toggleable sidebar widget.

Style mirrors the rest of the TUI test suite: no ``pytest-asyncio``; async tests
are SYNC functions driving the coroutine via ``asyncio.run`` with a nested
``async def _run`` using Textual's ``App.run_test()`` harness.
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from magi_agent.cli.tui.sidebar import Sidebar


class _Harness(App[None]):
    def __init__(self, sidebar: Sidebar) -> None:
        super().__init__()
        self._sidebar = sidebar

    def compose(self) -> ComposeResult:
        yield self._sidebar


def test_sidebar_renders_three_panes_empty() -> None:
    async def _run() -> None:
        sidebar = Sidebar(id="sidebar")
        app = _Harness(sidebar)
        async with app.run_test() as pilot:
            await pilot.pause()
            text = sidebar.panes_text()
        assert "Todo" in text
        assert "Context" in text
        assert "Files" in text

    asyncio.run(_run())


def test_sidebar_updates_panes_from_state() -> None:
    async def _run() -> None:
        sidebar = Sidebar(id="sidebar")
        app = _Harness(sidebar)
        async with app.run_test() as pilot:
            await pilot.pause()
            sidebar.set_todos(["write tests", "ship it"])
            sidebar.set_context(usage=1280, limit=8000)
            sidebar.add_file("src/app.py")
            sidebar.add_file("src/foo.py")
            await pilot.pause()
            text = sidebar.panes_text()
        assert "write tests" in text
        assert "ship it" in text
        assert "1,280 / 8,000" in text
        assert "src/app.py" in text
        assert "src/foo.py" in text

    asyncio.run(_run())


def test_sidebar_recent_files_dedupe_and_cap() -> None:
    async def _run() -> None:
        sidebar = Sidebar(id="sidebar")
        app = _Harness(sidebar)
        async with app.run_test() as pilot:
            await pilot.pause()
            for i in range(12):
                sidebar.add_file(f"f{i}.py")
            sidebar.add_file("f0.py")  # re-touch moves to most-recent, no dup
            await pilot.pause()
            files = sidebar.recent_files()
        assert len(files) <= 10
        assert files[0] == "f0.py"  # most-recent first
        assert files.count("f0.py") == 1

    asyncio.run(_run())
