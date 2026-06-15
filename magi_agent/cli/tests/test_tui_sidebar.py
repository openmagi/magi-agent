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
        # Honest bare token count — NOT a ``usage / limit`` ratio against a
        # hardcoded 200k default (misleading on a 128k/1M-window model).
        assert "1,280 tokens" in text
        assert "/ 8,000" not in text
        # Files display shortened (basename) form, not the full stored path.
        assert "app.py" in text
        assert "foo.py" in text

    asyncio.run(_run())


def test_sidebar_recent_files_display_shortened_but_keys_full_path() -> None:
    """A long stored path renders a shortened display, while dedup still keys on
    the FULL path (re-touching the same full path moves it to top, not dup)."""

    async def _run() -> None:
        sidebar = Sidebar(id="sidebar")
        app = _Harness(sidebar)
        long_path = "lib/services/handlers/foo.py"
        async with app.run_test() as pilot:
            await pilot.pause()
            sidebar.add_file(long_path)
            sidebar.add_file("other.py")
            sidebar.add_file(long_path)  # re-touch the SAME full path
            await pilot.pause()
            text = sidebar.panes_text()
            files = sidebar.recent_files()

        # Display is shortened: the basename shows, the full dir prefix does not.
        assert "foo.py" in text
        assert "lib/services/handlers/foo.py" not in text
        # Dedup keyed on the FULL path: stored list keeps the full path, exactly
        # once, and the re-touch moved it back to most-recent (top).
        assert files == [long_path, "other.py"]
        assert files.count(long_path) == 1


    asyncio.run(_run())


def test_shorten_file_cjk_is_cell_bounded() -> None:
    """A long Hangul basename shortens to ``MAX_FILE_DISPLAY_WIDTH`` *cells*, not
    codepoints (which would be ~2x and overflow the 32-wide dock), leads with
    ``…``, and the stored dedup key remains the FULL path."""

    from magi_agent.cli.render.width import display_width
    from magi_agent.cli.tui import sidebar as sidebar_mod

    async def _run() -> None:
        sidebar = Sidebar(id="sidebar")
        app = _Harness(sidebar)
        full_path = "lib/services/" + "파일이름" * 8 + ".py"
        async with app.run_test() as pilot:
            await pilot.pause()
            sidebar.add_file(full_path)
            await pilot.pause()
            text = sidebar.panes_text()
            files = sidebar.recent_files()

        # The shortened DISPLAY line is cell-bounded and leads with ``…``.
        shortened = sidebar_mod._shorten_file(full_path)
        assert display_width(shortened) <= sidebar_mod.MAX_FILE_DISPLAY_WIDTH
        assert shortened.startswith("…")
        assert shortened in text
        # Dedup key keeps the FULL path exactly once (display never corrupts it).
        assert files == [full_path]

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
