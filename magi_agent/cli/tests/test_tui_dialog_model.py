"""Tests for the PR2.3 model picker dialog.

Style mirrors ``test_tui_app.py``: this package has no ``pytest-asyncio``; async
tests are SYNC functions driving a nested ``async def _run`` via ``asyncio.run``
that uses Textual's ``App.run_test()`` Pilot harness.
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.widgets import OptionList

from magi_agent.cli.tui.dialogs.model import ModelPickerDialog, model_choices


class Host(App[None]):
    def __init__(self, models: list[str]) -> None:
        super().__init__()
        self._models = models
        self.picked: str | None = None

    def compose(self) -> ComposeResult:
        yield from ()

    async def open_picker(self) -> None:
        self.picked = await self.push_screen_wait(
            ModelPickerDialog(models=self._models, current="b")
        )


def test_model_picker_lists_models() -> None:
    async def _run() -> None:
        app = Host(["a", "b", "c"])
        async with app.run_test() as pilot:
            dialog = ModelPickerDialog(models=["a", "b", "c"], current="b")
            await app.push_screen(dialog)
            await pilot.pause()
            options = dialog.query_one(OptionList)
            ids = [
                options.get_option_at_index(i).id
                for i in range(options.option_count)
            ]
        assert ids == ["a", "b", "c"]

    asyncio.run(_run())


def test_model_picker_select_dismisses_with_model_id() -> None:
    async def _run() -> None:
        app = Host(["a", "b", "c"])
        async with app.run_test() as pilot:
            worker = app.run_worker(app.open_picker(), exclusive=False)
            await pilot.pause()
            dialog = app.screen
            assert isinstance(dialog, ModelPickerDialog)
            options = dialog.query_one(OptionList)
            options.highlighted = 2  # "c"
            await pilot.pause()
            options.action_select()
            await worker.wait()
            await pilot.pause()
        assert app.picked == "c"

    asyncio.run(_run())


def test_model_picker_escape_dismisses_none() -> None:
    async def _run() -> None:
        app = Host(["a", "b"])
        async with app.run_test() as pilot:
            worker = app.run_worker(app.open_picker(), exclusive=False)
            await pilot.pause()
            await pilot.press("escape")
            await worker.wait()
            await pilot.pause()
        assert app.picked is None

    asyncio.run(_run())


def test_model_picker_current_is_preselected() -> None:
    async def _run() -> None:
        app = Host(["a", "b", "c"])
        async with app.run_test() as pilot:
            dialog = ModelPickerDialog(models=["a", "b", "c"], current="b")
            await app.push_screen(dialog)
            await pilot.pause()
            options = dialog.query_one(OptionList)
            highlighted = options.highlighted
        assert highlighted == 1  # "b"

    asyncio.run(_run())


def test_model_picker_empty_models_handled() -> None:
    async def _run() -> None:
        app = Host([])
        async with app.run_test() as pilot:
            dialog = ModelPickerDialog(models=[], current=None)
            await app.push_screen(dialog)
            await pilot.pause()
            options = dialog.query_one(OptionList)
            count = options.option_count
        assert count == 0

    asyncio.run(_run())


def test_model_choices_lists_provider_defaults_current_first() -> None:
    from magi_agent.cli.providers import _DEFAULT_MODEL, SUPPORTED_PROVIDERS

    choices = model_choices("custom-model")
    assert choices[0] == "custom-model"
    # Every provider default is offered exactly once.
    for provider in SUPPORTED_PROVIDERS:
        assert _DEFAULT_MODEL[provider] in choices
    assert len(choices) == len(set(choices))


def test_model_choices_no_current_starts_with_defaults() -> None:
    from magi_agent.cli.providers import _DEFAULT_MODEL, SUPPORTED_PROVIDERS

    choices = model_choices(None)
    assert choices[0] == _DEFAULT_MODEL[SUPPORTED_PROVIDERS[0]]
