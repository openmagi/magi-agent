"""Tests for the PR2.4 session list dialog.

Style mirrors ``test_tui_dialog_model.py``: SYNC test functions driving the
coroutine via ``asyncio.run`` with a nested ``async def _run`` over Textual's
``App.run_test()`` Pilot harness. No model is ever hit; the dialog is pure UI
over an injected list of :class:`SessionEntry`.
"""

from __future__ import annotations

import asyncio

from textual.app import App
from textual.widgets import OptionList, Static

from magi_agent.cli.tui.dialogs.session import (
    SessionEntry,
    SessionListDialog,
    session_entries,
)

SESSIONS = [
    SessionEntry(ref="s-1", label="fix the build", updated="2026-06-06"),
    SessionEntry(ref="s-2", label="add tests", updated="2026-06-07"),
]


def test_session_dialog_lists_sessions() -> None:
    async def _run() -> None:
        app = App()
        async with app.run_test() as pilot:
            dialog = SessionListDialog(sessions=SESSIONS)
            await app.push_screen(dialog)
            await pilot.pause()
            options = dialog.query_one(OptionList)
            ids = [
                options.get_option_at_index(i).id
                for i in range(options.option_count)
            ]
        assert ids == ["s-1", "s-2"]

    asyncio.run(_run())


def test_session_dialog_row_shows_label_and_timestamp() -> None:
    async def _run() -> None:
        app = App()
        async with app.run_test() as pilot:
            dialog = SessionListDialog(sessions=SESSIONS)
            await app.push_screen(dialog)
            await pilot.pause()
            options = dialog.query_one(OptionList)
            first = options.get_option_at_index(0).prompt
        rendered = str(first)
        assert "fix the build" in rendered
        assert "2026-06-06" in rendered

    asyncio.run(_run())


def test_session_dialog_select_dismisses_with_ref() -> None:
    async def _run() -> None:
        app = App()
        picked: dict[str, str | None] = {}

        async def _open() -> None:
            picked["ref"] = await app.push_screen_wait(
                SessionListDialog(sessions=SESSIONS)
            )

        async with app.run_test() as pilot:
            worker = app.run_worker(_open(), exclusive=False)
            await pilot.pause()
            dialog = app.screen
            assert isinstance(dialog, SessionListDialog)
            options = dialog.query_one(OptionList)
            options.highlighted = 1
            await pilot.pause()
            options.action_select()
            await worker.wait()
            await pilot.pause()
        assert picked["ref"] == "s-2"

    asyncio.run(_run())


def test_session_dialog_escape_dismisses_none() -> None:
    async def _run() -> None:
        app = App()
        picked: dict[str, str | None] = {"ref": "sentinel"}

        async def _open() -> None:
            picked["ref"] = await app.push_screen_wait(
                SessionListDialog(sessions=SESSIONS)
            )

        async with app.run_test() as pilot:
            worker = app.run_worker(_open(), exclusive=False)
            await pilot.pause()
            await pilot.press("escape")
            await worker.wait()
            await pilot.pause()
        assert picked["ref"] is None

    asyncio.run(_run())


def test_session_dialog_empty_shows_placeholder() -> None:
    async def _run() -> None:
        app = App()
        async with app.run_test() as pilot:
            dialog = SessionListDialog(sessions=[])
            await app.push_screen(dialog)
            await pilot.pause()
            options = dialog.query_one(OptionList)
            assert options.option_count == 0
            placeholder = dialog.query_one("#session-empty", Static)
            assert "No prior sessions." in str(placeholder.render())

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# session_entries() — best-effort prior-session list off a wired controller
# ---------------------------------------------------------------------------
def test_session_entries_none_runtime_is_empty() -> None:
    assert session_entries(None) == []


def test_session_entries_without_lister_is_empty() -> None:
    class Runtime:
        pass

    assert session_entries(Runtime()) == []


def test_session_entries_reads_wired_lister() -> None:
    class Row:
        def __init__(self, ref: str, label: str, updated: str) -> None:
            self.ref = ref
            self.label = label
            self.updated = updated

    class Lister:
        def recent(self) -> list[Row]:
            return [Row("s-9", "earlier work", "2026-06-06")]

    class Runtime:
        session_lister = Lister()

    entries = session_entries(Runtime())
    assert entries == [
        SessionEntry(ref="s-9", label="earlier work", updated="2026-06-06")
    ]


def test_session_entries_skips_rows_without_ref() -> None:
    class Row:
        def __init__(self, ref: object) -> None:
            self.ref = ref

    class Lister:
        def recent(self) -> list[Row]:
            return [Row(None), Row(""), Row("s-ok")]

    class Runtime:
        session_lister = Lister()

    entries = session_entries(Runtime())
    assert [e.ref for e in entries] == ["s-ok"]


def test_session_entries_swallows_lister_errors() -> None:
    class Lister:
        def recent(self) -> list[object]:
            raise RuntimeError("boom")

    class Runtime:
        session_lister = Lister()

    assert session_entries(Runtime()) == []
