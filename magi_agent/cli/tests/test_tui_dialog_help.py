"""Tests for the PR2.5 help dialog.

Style mirrors ``test_tui_dialog_model.py`` / ``test_tui_dialog_session.py``:
SYNC test functions driving the coroutine via ``asyncio.run`` with a nested
``async def _run`` over Textual's ``App.run_test()`` Pilot harness. The dialog
is a read-only reference over already-resolved inputs (no model is ever hit).

NOTE: Textual 8.2.7's ``Static`` has NO ``.renderable`` attribute — read the
rendered content via ``str(widget.render())`` everywhere.
"""

from __future__ import annotations

import asyncio

from textual.app import App
from textual.widgets import Static

from magi_agent.cli.tui.dialogs.help import HelpDialog, build_help_sections


def test_build_help_sections_includes_keys_and_commands() -> None:
    sections = build_help_sections(
        bindings=[("ctrl+c", "Cancel"), ("ctrl+p", "Command palette")],
        commands=["compact", "status", "help"],
    )
    flat = "\n".join(line for _title, lines in sections for line in lines)
    assert "ctrl+c" in flat and "Cancel" in flat
    assert "ctrl+p" in flat
    assert "/compact" in flat and "/status" in flat


def test_build_help_sections_drops_empty_sections() -> None:
    # No commands -> only the Keybindings section is present.
    sections = build_help_sections(
        bindings=[("ctrl+c", "Cancel")],
        commands=[],
    )
    titles = [title for title, _lines in sections]
    assert titles == ["Keybindings"]


def test_help_dialog_renders_and_escape_dismisses() -> None:
    async def _run() -> None:
        app = App()
        done: dict[str, bool] = {"closed": False}

        async def _open() -> None:
            await app.push_screen_wait(
                HelpDialog(
                    bindings=[("ctrl+c", "Cancel")],
                    commands=["compact"],
                )
            )
            done["closed"] = True

        async with app.run_test() as pilot:
            worker = app.run_worker(_open(), exclusive=False)
            await pilot.pause()
            dialog = app.screen
            assert isinstance(dialog, HelpDialog)
            body = dialog.query_one("#help-body", Static)
            rendered = str(body.render())
            assert "ctrl+c" in rendered
            assert "Cancel" in rendered
            assert "/compact" in rendered
            await pilot.press("escape")
            await worker.wait()
            await pilot.pause()
        assert done["closed"] is True

    asyncio.run(_run())


def test_help_dialog_enter_also_dismisses() -> None:
    async def _run() -> None:
        app = App()
        done: dict[str, bool] = {"closed": False}

        async def _open() -> None:
            await app.push_screen_wait(
                HelpDialog(bindings=[("ctrl+c", "Cancel")], commands=[])
            )
            done["closed"] = True

        async with app.run_test() as pilot:
            worker = app.run_worker(_open(), exclusive=False)
            await pilot.pause()
            assert isinstance(app.screen, HelpDialog)
            await pilot.press("enter")
            await worker.wait()
            await pilot.pause()
        assert done["closed"] is True

    asyncio.run(_run())
